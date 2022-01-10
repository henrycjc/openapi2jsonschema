#!/usr/bin/env python

import json
import yaml
import urllib
import os
import sys
if sys.version_info < (3, 0):
    raise Exception("Python 3 only")
import pathlib

from jsonref import JsonRef  # type: ignore
import click

from openapi2jsonschema.log import info, debug, error
from openapi2jsonschema.util import (
    additional_properties,
    replace_int_or_string,
    allow_null_optional_fields,
    change_dict_values,
    append_no_duplicates,
    get_request_and_response_body_components_from_paths,
)
from openapi2jsonschema.errors import UnsupportedError


@click.command()
@click.option(
    "-o",
    "--output",
    default="schemas",
    metavar="PATH",
    help="Directory to store schema files",
)
@click.option(
    "-p",
    "--prefix",
    default="_definitions.json",
    help="Prefix for JSON references (only for OpenAPI versions before 3.0)",
)
@click.option(
    "--stand-alone", is_flag=True, help="Whether or not to de-reference JSON schemas"
)
@click.option(
    "--expanded", is_flag=True, help="Expand Kubernetes schemas by API version"
)
@click.option(
    "--kubernetes", is_flag=True, help="Enable Kubernetes specific processors"
)
@click.option(
    "--no-all", is_flag=True, help="Do not generate all.json file"
)
@click.option(
    "--strict",
    is_flag=True,
    help="Prohibits properties not in the schema (additionalProperties: false)",
)
@click.option(
    "--include-bodies",
    is_flag=True,
    help="Include request and response bodies as if they are components",
)
@click.argument("schema", metavar="SCHEMA_URL")
def default(
    output,
    schema,
    prefix,
    stand_alone,
    expanded,
    kubernetes,
    strict,
    no_all,
    include_bodies,
):
    """
    Converts a valid OpenAPI specification into a set of JSON Schema files
    """
    info("Downloading schema")
    data = pathlib.Path(os.path.realpath(schema)).read_bytes().decode("utf-8")

    info("Parsing schema")
    # Note that JSON is valid YAML, so we can use the YAML parser whether
    # the schema is stored in JSON or YAML
    data = yaml.load(data, Loader=yaml.SafeLoader)

    if "swagger" in data:
        version = data["swagger"]
    elif "openapi" in data:
        version = data["openapi"]

    if not os.path.exists(output):
        os.makedirs(output)

    types = []

    info("Generating individual schemas")
    components = data["components"]["schemas"]

    generated_files = []

    if include_bodies:
        components.update(
            get_request_and_response_body_components_from_paths(data["paths"]),
        )

    for title in components:
        kind = title.split(".")[-1]
        if kubernetes:
            group = title.split(".")[-3].lower()
            api_version = title.split(".")[-2].lower()
        specification = components[title]
        specification["$schema"] = "http://json-schema.org/schema#"
        specification.setdefault("type", "object")

        if strict:
            specification["additionalProperties"] = False

        if kubernetes and expanded:
            if group in ["core", "api"]:
                full_name = "%s-%s" % (kind, api_version)
            else:
                full_name = "%s-%s-%s" % (kind, group, api_version)
        else:
            full_name = kind

        types.append(title)

        try:
            debug("Processing %s" % full_name)

            # These APIs are all deprecated
            if kubernetes:
                if title.split(".")[3] == "pkg" and title.split(".")[2] == "kubernetes":
                    raise UnsupportedError(
                        "%s not currently supported, due to use of pkg namespace"
                        % title
                    )

            # This list of Kubernetes types carry around jsonschema for Kubernetes and don't
            # currently work with openapi2jsonschema
            if (
                kubernetes
                and stand_alone
                and kind.lower() in [
                    "jsonschemaprops",
                    "jsonschemapropsorarray",
                    "customresourcevalidation",
                    "customresourcedefinition",
                    "customresourcedefinitionspec",
                    "customresourcedefinitionlist",
                    "customresourcedefinitionspec",
                    "jsonschemapropsorstringarray",
                    "jsonschemapropsorbool",
                ]
            ):
                raise UnsupportedError("%s not currently supported" % kind)

            updated = change_dict_values(specification, prefix, version)
            specification = updated

            if stand_alone:
                # Put generated file on list for dereferencig $ref elements
                # after all files will be generated
                generated_files.append(full_name)

            if "additionalProperties" in specification:
                if specification["additionalProperties"]:
                    updated = change_dict_values(
                        specification["additionalProperties"], prefix, version
                    )
                    specification["additionalProperties"] = updated

            if strict and "properties" in specification:
                updated = additional_properties(specification["properties"])
                specification["properties"] = updated

            if kubernetes and "properties" in specification:
                updated = replace_int_or_string(specification["properties"])
                updated = allow_null_optional_fields(updated)
                specification["properties"] = updated

            with open("%s/%s.json" % (output, full_name), "w") as schema_file:
                debug("Generating %s.json" % full_name)
                schema_file.write(json.dumps(specification, indent=2))
        except Exception as e:
            error("An error occured processing %s: %s" % (kind, e))

    if stand_alone:
        base = (pathlib.Path.cwd() / output / output).as_uri()
        for file_name in generated_files:
            full_path = "%s/%s.json" % (output, file_name)
            specification = json.load(open(full_path))
            specification = JsonRef.replace_refs(
                specification, base_uri=base)
            with open(full_path, "w") as schema_file:
                schema_file.write(json.dumps(specification, indent=2))

    if not no_all:
        with open("%s/all.json" % output, "w") as all_file:
            info("Generating schema for all types")
            contents = {"oneOf": []}
            for title in types:
                if version < "3":
                    contents["oneOf"].append(
                        {"$ref": "%s#/definitions/%s" % (prefix, title)}
                    )
                else:
                    contents["oneOf"].append(
                        {"$ref": (title.replace("#/components/schemas/", "") + ".json")}
                    )
            all_file.write(json.dumps(contents, indent=2))


if __name__ == "__main__":
    default()
