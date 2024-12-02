from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any

from linkml.validator import JsonschemaValidationPlugin, Validator
from linkml.validator.plugins import ValidationPlugin
from linkml.validator.report import ValidationResult
from linkml_runtime.linkml_model import SchemaDefinition

from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic2linkml.gen_linkml import translate_defs

from .cli.tools import DANDI_MODULE_NAMES
from .models import ValidationReport


def iter_direct_subdirs(path: Path) -> Iterable[Path]:
    """
    Get an iterable of the direct subdirectories of a given path.

    :param path: The given path
    :return: The iterable of the direct subdirectories of the given path
    :raises: ValueError if the given path is not a directory
    """
    if not path.is_dir():
        raise ValueError(f"The given path is not a directory: {path}")
    return (p for p in path.iterdir() if p.is_dir())


def pydantic_validate(data: dict[str, Any] | str, model: type[BaseModel]) -> str:
    """
    Validate the given data against a Pydantic model

    :param data: The data, as a dict or JSON string, to be validated
    :param model: The Pydantic model to validate the data against
    :return: A JSON string that specifies an array of errors encountered in
        the validation (The JSON string returned in a case of any validation failure
        is one returned by the Pydantic `ValidationError.json()` method. In the case
        of no validation error, the empty array JSON expression, `"[]"`, is returned.)
    """
    if isinstance(data, str):
        validate_method = model.model_validate_json
    else:
        validate_method = model.model_validate

    try:
        validate_method(data)
    except ValidationError as e:
        return e.json()

    return "[]"


def write_reports(
    file_path: Path, reports: list[ValidationReport], type_adapter: TypeAdapter
) -> None:
    """
    Write a given list of validation reports to a specified file

    :param file_path: The path specifying the file to write the reports to
    :param reports: The list of validation reports to write
    :param type_adapter: The type adapter to use for serializing the list of reports
    """
    file_path.write_bytes(type_adapter.dump_json(reports, indent=2))


class DandiModelLinkmlValidator:
    """
    A class to validate DANDI metadata against the DANDI metadata models in
    the LinkML schema produced by the pydantic2linkml translator for DANDI models
    expressed in Pydantic
    """

    # The LinkML schema produced by the pydantic2linkml translator for DANDI models
    _dandi_linkml_schema: SchemaDefinition | None = None

    def __init__(self, validation_plugins: list[ValidationPlugin] | None = None):
        """
        Initialize a `DandiModelLinkmlValidator` instance that wraps a LinkML validator
        instance set up with schema produced by the pydantic2linkml translator,
        for DANDI models expressed in Pydantic, and given validation plugins.

        :param validation_plugins: The list of given validation plugins to set up
        the LinkML validator with. If no validation plugins are given, the default of a
        list containing a `JsonschemaValidationPlugin` instance with `closed=True`.
        """
        if validation_plugins is None:
            validation_plugins = [JsonschemaValidationPlugin(closed=True)]

        self._inner_validator = Validator(
            # TODO: The deep copying may not be needed if
            #  https://github.com/linkml/linkml/issues/2359 is resolved
            deepcopy(self.get_dandi_linkml_schema()),
            validation_plugins=validation_plugins,
        )

    @classmethod
    def get_dandi_linkml_schema(cls) -> SchemaDefinition:
        """
        Get the LinkML schema produced by the pydantic2linkml translator
        for DANDI models

        :return: The LinkML schema
        """
        if cls._dandi_linkml_schema is None:
            cls._dandi_linkml_schema = translate_defs(DANDI_MODULE_NAMES)

        return cls._dandi_linkml_schema

    def validate(
        self, dandi_metadata: dict[str, Any], dandi_metadata_class: str
    ) -> list[ValidationResult]:
        """
        Validate given DANDI metadata against a DANDI metadata model
        specified by its class name in the LinkML schema

        :param dandi_metadata: The DANDI metadata to validate
        :param dandi_metadata_class: The class name of the DANDI metadata model
        :return: A list of validation errors encountered
        """
        validation_report = self._inner_validator.validate(
            dandi_metadata, target_class=dandi_metadata_class
        )
        return validation_report.results
