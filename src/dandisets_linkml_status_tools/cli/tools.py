import json
import logging
import re
from collections import Counter
from collections.abc import Iterable
from copy import deepcopy
from functools import partial
from pathlib import Path
from shutil import rmtree
from typing import Any, Optional

from dandi.dandiapi import RemoteDandiset
from dandischema.models import Dandiset
from linkml.validator import Validator
from linkml.validator.plugins import JsonschemaValidationPlugin, ValidationPlugin
from linkml.validator.report import ValidationResult
from linkml_runtime.dumpers import yaml_dumper
from linkml_runtime.linkml_model import SchemaDefinition
from pydantic import TypeAdapter, ValidationError
from pydantic2linkml.gen_linkml import translate_defs
from yaml import dump as yaml_dump

try:
    # Import the C-based YAML dumper if available
    from yaml import CSafeDumper as SafeDumper
except ImportError:
    # Otherwise, import the Python-based YAML dumper
    from yaml import SafeDumper  # type: ignore

from dandisets_linkml_status_tools.cli.models import (
    DandisetValidationReport,
    LinkmlValidationErrsType,
    PydanticValidationErrsType,
    dandiset_metadata_adapter,
    linkml_validation_errs_adapter,
    pydantic_validation_errs_adapter,
)

logger = logging.getLogger(__name__)

# The names of the collection of modules in which the DANDI models are defined
DANDI_MODULE_NAMES = ["dandischema.models"]

# A callable that sorts a given iterable of strings in a case-insensitive manner
isorted = partial(sorted, key=str.casefold)


def pydantic_validate(dandiset_metadata: dict[str, Any]) -> str:
    """
    Validate the given dandiset metadata against the Pydantic dandiset metadata model

    :param dandiset_metadata: The dandiset metadata to validate.
    :return: A JSON string that is an array of errors encountered in the validation
        (The JSON string returned in a case of any validation failure is one returned by
        the Pydantic `ValidationError.json()` method. In the case of no validation
        error, the empty array JSON expression, `"[]"`, is returned.)
    """
    try:
        Dandiset.model_validate(dandiset_metadata)
    except ValidationError as e:
        return e.json()

    return "[]"


class DandisetLinkmlValidator:
    """
    A class to validate dandiset metadata against the dandiset metadata model in
    the LinkML schema produced by the pydantic2linkml translator for DANDI models
    expressed in Pydantic
    """

    # The LinkML schema produced by the pydantic2linkml translator for DANDI models
    _dandi_linkml_schema: Optional[SchemaDefinition] = None

    def __init__(self, validation_plugins: Optional[list[ValidationPlugin]] = None):
        """
        Initialize a `DandisetLinkmlValidator` instance that wraps a LinkML validator
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
        Get the LinkML schema produced by the pydantic2linkml translator for DANDI models

        :return: The LinkML schema
        """
        if cls._dandi_linkml_schema is None:
            cls._dandi_linkml_schema = translate_defs(DANDI_MODULE_NAMES)

        return cls._dandi_linkml_schema

    def validate(self, dandiset_metadata: dict[str, Any]) -> list[ValidationResult]:
        """
        Validate the given dandiset metadata against the dandiset metadata model in
        LinkML

        :param dandiset_metadata: The dandiset metadata to validate
        :return: A list of validation errors encountered
        """
        # The name of the class in the LinkML schema representing Dandiset metadata
        dandiset_metadata_class = "Dandiset"

        validation_report = self._inner_validator.validate(
            dandiset_metadata, target_class=dandiset_metadata_class
        )
        return validation_report.results


def compile_validation_report(dandiset: RemoteDandiset) -> DandisetValidationReport:
    """
    Compile a validation report of the metadata of a given dandiset

    :param dandiset: The given dandiset
    :return: The compiled validation report

    Note: This function should only be called in the context of a `DandiAPIClient`
        context manager associated with the given dandiset.
    """
    dandiset_linkml_validator = DandisetLinkmlValidator()

    dandiset_id = dandiset.identifier
    dandiset_version = dandiset.version_id

    raw_metadata = dandiset.get_raw_metadata()

    # === Fetch dandiset version info ===
    dandiset_version_info = dandiset.get_version(dandiset_version)
    # Get dandiset version status
    dandiset_version_status = dandiset_version_info.status
    # Get dandiset version modified datetime
    dandiset_version_modified = dandiset_version_info.modified

    # Validate the raw metadata using the Pydantic model
    pydantic_validation_errs = pydantic_validate(raw_metadata)
    if pydantic_validation_errs != "[]":
        logger.info(
            "Captured Pydantic validation errors for dandiset %s @ %s",
            dandiset_id,
            dandiset_version,
        )

    # Validate the raw metadata using the LinkML schema
    linkml_validation_errs = dandiset_linkml_validator.validate(raw_metadata)
    if linkml_validation_errs:
        logger.info(
            "Captured LinkML validation errors for dandiset %s @ %s",
            dandiset_id,
            dandiset_version,
        )

    # noinspection PyTypeChecker
    return DandisetValidationReport(
        dandiset_identifier=dandiset_id,
        dandiset_version=dandiset_version,
        dandiset_version_status=dandiset_version_status,
        dandiset_version_modified=dandiset_version_modified,
        dandiset_metadata=raw_metadata,
        pydantic_validation_errs=pydantic_validation_errs,
        linkml_validation_errs=linkml_validation_errs,
    )


def output_reports(reports: list[DandisetValidationReport], output_path: Path) -> None:
    """
    Output the given list of dandiset validation reports, a summary of the reports
    , as a `summary.md`, and the schema used in the LinkML validations,
    as a `dandi_linkml_schema.yml`, to a given file path

    Note: This function will replace the output directory if it already exists.

    :param reports: The given list of dandiset validation reports
    :param output_path: The given file path to output the reports to.
        Note: In the case of the given output path already points to an existing object,
        if the object is directory, it will be removed and replaced with a new
        directory; Otherwise, `NotADirectoryError` will be raised.

    raises NotADirectoryError: If the given output path points to a non-directory object
    """
    summary_file_name = "summary.md"
    dandi_linkml_schema_file_name = "dandi-linkml-schema.yml"
    summary_headers = [
        "dandiset",
        "version",
        "schema_version",
        "api_status",
        "modified",
        "pydantic",
        "linkml",
    ]

    # Remove the existing report output directory if it exists
    if output_path.exists():
        logger.info("Found existing report output directory: %s", output_path)
        rmtree(output_path)
        logger.info("Deleted existing report output directory: %s", output_path)

    # Recreate the report output directory
    output_path.mkdir()
    logger.info("Recreated report output directory: %s", output_path)

    output_dandi_linkml_schema(output_path / dandi_linkml_schema_file_name)

    with (output_path / summary_file_name).open("w") as summary_f:
        # === Provide a reference to the DANDI LinkML schema in the summary ===
        summary_f.write(
            f"[DANDI LinkML schema](./{dandi_linkml_schema_file_name}) "
            f"(LinkML schema used in the LinkML validations)\n"
        )

        # Write line break before the start of the summary table
        summary_f.write("\n")

        # === Write the headers of the summary table ===
        header_row = _gen_row(f" {h} " for h in summary_headers)
        alignment_row = _gen_row("-" * (len(h) + 2) for h in summary_headers)
        summary_f.write(header_row + alignment_row)

        # Output the individual dandiset validation reports
        for r in reports:
            report_dir = output_path / r.dandiset_identifier / r.dandiset_version
            report_dir.mkdir(parents=True)

            _write_data(
                r.dandiset_metadata, dandiset_metadata_adapter, "metadata", report_dir
            )
            _write_data(
                r.pydantic_validation_errs,
                pydantic_validation_errs_adapter,
                "pydantic_validation_errs",
                report_dir,
            )
            _write_data(
                r.linkml_validation_errs,
                linkml_validation_errs_adapter,
                "linkml_validation_errs",
                report_dir,
            )

            logger.info("Output dandiset %s validation report", r.dandiset_identifier)

            # === Write the summary table row for the dandiset validation report ===
            # Directory for storing all metadata validation results of the dandiset
            dandiset_dir = f"./{r.dandiset_identifier}"
            # Directory for storing all metadata validation results of the dandiset
            # at a particular version
            version_dir = f"{dandiset_dir}/{r.dandiset_version}"

            pydantic_err_counts = get_pydantic_err_counts(r.pydantic_validation_errs)
            linkml_err_counts = get_linkml_err_counts(r.linkml_validation_errs)

            row_cells = (
                f" {c} "  # Add spaces around the cell content for better readability
                for c in [
                    # For the dandiset column
                    f"[{r.dandiset_identifier}]({dandiset_dir}/)",
                    # For the version column
                    f"[{r.dandiset_version}]({version_dir}/metadata.yaml)",
                    # For schema_version column
                    r.dandiset_schema_version,
                    # For the api_status column
                    r.dandiset_version_status.value,
                    # For the modified column
                    r.dandiset_version_modified.isoformat(),
                    # For the pydantic column
                    (
                        f"[{len(r.pydantic_validation_errs)} "
                        f"({', '.join(f'{v} {k}' for k, v in pydantic_err_counts.items())})]"
                        f"({version_dir}/pydantic_validation_errs.yaml)"
                        if r.pydantic_validation_errs
                        else "0"
                    ),
                    # For the linkml column
                    (
                        f"[{len(r.linkml_validation_errs)} "
                        f"({' + '.join(str(v) for v in linkml_err_counts.values())})]"
                        f"({version_dir}/linkml_validation_errs.yaml)"
                        if r.linkml_validation_errs
                        else "0"
                    ),
                ]
            )
            summary_f.write(_gen_row(row_cells))

    logger.info("Output of dandiset validation reports completed")


def output_dandi_linkml_schema(output_path: Path) -> None:
    """
    Output the DANDI LinkML schema, in YAML, to a file

    :param output_path: The path specifying the location of the file
    """
    # Output the LinkML schema used in the validations
    dandi_linkml_schema_yml = yaml_dumper.dumps(
        DandisetLinkmlValidator.get_dandi_linkml_schema()
    )
    with output_path.open("w") as f:
        f.write(dandi_linkml_schema_yml)
    logger.info("Output the DANDI LinkML schema to %s", output_path)


def _write_data(
    data: Any, data_adapter: TypeAdapter, base_file_name: str, output_dir: Path
) -> None:
    """
    Output given data to a JSON file and a YAML file in a given output directory

    :param data: The data to be output
    :param data_adapter: The type adapter used to serialize the data
    :param base_file_name: The base file name for the output files
    :param output_dir: The output directory to write the files to
    """
    serializable_data = data_adapter.dump_python(data, mode="json")

    # Output data to a JSON file
    json_file_path = output_dir / (base_file_name + ".json")
    with json_file_path.open("w") as f:
        json.dump(serializable_data, f, indent=2)

    # Output data to a YAML file
    yaml_file_path = output_dir / (base_file_name + ".yaml")
    with yaml_file_path.open("w") as f:
        yaml_dump(serializable_data, f, Dumper=SafeDumper)


def _gen_row(cell_str_values: Iterable[str]) -> str:
    """
    Construct a row of a Markdown table with given cell string values
    :param cell_str_values: The given iterable of cell string values
    :return: The constructed row of a Markdown table

    Note: The given iterable of cell string values are `str` values
    """
    return f'|{"|".join(cell_str_values)}|\n'


def get_pydantic_err_counts(errs: PydanticValidationErrsType) -> Counter[str]:
    """
    Get a `Counter` object that counts the Pydantic validation errors by type

    :param errs: The list of Pydantic validation errors to be counted
    :return: The `Counter` object
    """
    return Counter(isorted(e["type"] for e in errs))


def get_linkml_err_counts(errs: LinkmlValidationErrsType) -> Counter[str]:
    """
    Get a `Counter` object that counts the LinkML validation errors by type
    :param errs: The list of LinkML validation errors to be counted
    :return: The `Counter` object

    Notes: The determination of the type of a LinkML validation error is rather
        rudimentary at this point.
    """
    linkml_err_types = [
        re.sub(r".*(is .*) in \S.*", r"\1", e.message, count=1) for e in errs
    ]
    return Counter(isorted(linkml_err_types))
