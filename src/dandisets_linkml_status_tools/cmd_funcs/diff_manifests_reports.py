import logging
from collections.abc import Callable, Iterable
from itertools import chain
from pathlib import Path
from typing import Annotated, Any, TypeAlias, cast

from jsondiff import diff
from pydantic import Field

from dandisets_linkml_status_tools.cli import (
    ASSET_VALIDATION_REPORTS_FILE,
    DANDISET_VALIDATION_REPORTS_FILE,
)
from dandisets_linkml_status_tools.models import (
    ASSET_VALIDATION_REPORTS_ADAPTER,
    DANDISET_VALIDATION_REPORTS_ADAPTER,
    AssetValidationReport,
    AssetValidationReportsType,
    DandiBaseReport,
    DandisetValidationReport,
    DandisetValidationReportsType,
    JsonschemaValidationErrorModel,
    PydanticValidationErrsType,
    ValidationReportsType,
)
from dandisets_linkml_status_tools.tools import (
    create_or_replace_dir,
    get_validation_reports_entries,
    read_reports,
    write_data,
)
from dandisets_linkml_status_tools.tools.md import (
    jsonschema_validation_err_diff_detailed_table,
    pydantic_validation_err_diff_detailed_table,
    validation_err_diff_summary,
)
from dandisets_linkml_status_tools.tools.validation_err_counter import (
    ValidationErrCounter,
)

logger = logging.getLogger(__name__)

PydanticValidationErrRep: TypeAlias = tuple[str, str, tuple, Path]
JsonschemaValidationErrRep: TypeAlias = tuple[JsonschemaValidationErrorModel, Path]

PYDANTIC_ERRS_SUMMARY_FNAME = "pydantic_errs_summary.md"
JSONSCHEMA_ERRS_SUMMARY_FNAME = "jsonschema_errs_summary.md"


class _DandiValidationDiffReport(DandiBaseReport):
    """
    A base class for DANDI validation diff reports
    """

    # Pydantic validation errors and their diff
    pydantic_validation_errs1: Annotated[
        PydanticValidationErrsType, Field(default_factory=list)
    ]
    pydantic_validation_errs2: Annotated[
        PydanticValidationErrsType, Field(default_factory=list)
    ]
    pydantic_validation_errs_diff: dict | list

    # jsonschema validation errors and their diff
    jsonschema_validation_errs1: Annotated[
        list[JsonschemaValidationErrorModel], Field(default_factory=list)
    ]
    jsonschema_validation_errs2: Annotated[
        list[JsonschemaValidationErrorModel], Field(default_factory=list)
    ]
    jsonschema_validation_errs_diff: dict | list


class _DandisetValidationDiffReport(_DandiValidationDiffReport):
    """
    A class for Dandiset validation diff reports
    """


class _AssetValidationDiffReport(_DandiValidationDiffReport):
    """
    A class for Asset validation diff reports
    """

    asset_id: str | None
    asset_path: str | None

    # The index of the asset in the containing JSON array in `assets.jsonld`
    asset_idx: int


def diff_manifests_reports(
    reports_dir1: Path, reports_dir2: Path, output_dir: Path
) -> None:
    """
    Generate a report of differences between two sets of reports on the same manifests

    :param reports_dir1: Path of the directory containing the first set of reports
        for contrast
    :param reports_dir2: Path of the directory containing the second set of reports
        for contrast
    :param output_dir: Path of the directory to write the report of differences to
    """
    diff_reports_dir = output_dir / "diff_reports"

    reports_dirs = [reports_dir1, reports_dir2]

    dandiset_validation_reports_lst: list[DandisetValidationReportsType] = []
    asset_validation_reports_lst: list[AssetValidationReportsType] = []
    for dir_ in reports_dirs:
        dandiset_validation_reports_file: Path = dir_ / DANDISET_VALIDATION_REPORTS_FILE
        asset_validation_reports_file: Path = dir_ / ASSET_VALIDATION_REPORTS_FILE

        for f in [
            dandiset_validation_reports_file,
            asset_validation_reports_file,
        ]:
            if not f.is_file():
                raise RuntimeError(f"There is no file at {f}")

        # Load and store dandiset validation reports
        dandiset_validation_reports_lst.append(
            read_reports(
                dandiset_validation_reports_file,
                DANDISET_VALIDATION_REPORTS_ADAPTER,
            )
        )

        # Load and store asset validation reports
        asset_validation_reports_lst.append(
            read_reports(
                asset_validation_reports_file, ASSET_VALIDATION_REPORTS_ADAPTER
            )
        )

    _output_validation_diff_reports(
        _dandiset_validation_diff_reports(*dandiset_validation_reports_lst),
        _asset_validation_diff_reports(*asset_validation_reports_lst),
        diff_reports_dir,
    )

    logger.info("Success!")


def _dandiset_validation_diff_reports(
    reports1: DandisetValidationReportsType, reports2: DandisetValidationReportsType
) -> list[_DandisetValidationDiffReport]:
    """
    Get the list of the dandiset validation diff reports of two given collections of
    dandiset validation reports

    :param reports1: The first collection of dandiset validation reports
    :param reports2: The second collection of dandiset validation reports
    :return: The list of dandiset validation diff reports of the given two
        collections
    """

    # Get all entries involved in the two collections of dandiset validation reports
    entries = sorted(
        get_validation_reports_entries(reports1)
        | get_validation_reports_entries(reports2)
    )

    # The list of dandiset validation diff reports to be returned
    rs = []
    for id_, ver in entries:  # Each entry can be break down to dandiset ID and version
        # Get reports at the same entry from the two collections respectively
        r1 = reports1.get(id_, {}).get(ver, None)
        r2 = reports2.get(id_, {}).get(ver, None)

        if r1 is not None:
            pydantic_errs1 = r1.pydantic_validation_errs
            jsonschema_errs1 = r1.jsonschema_validation_errs
        else:
            pydantic_errs1 = []
            jsonschema_errs1 = []

        if r2 is not None:
            pydantic_errs2 = r2.pydantic_validation_errs
            jsonschema_errs2 = r2.jsonschema_validation_errs
        else:
            pydantic_errs2 = []
            jsonschema_errs2 = []

        # If all errs are empty, skip this entry
        if not any(
            (pydantic_errs1, pydantic_errs2, jsonschema_errs1, jsonschema_errs2)
        ):
            continue

        rs.append(
            _DandisetValidationDiffReport(
                dandiset_identifier=id_,
                dandiset_version=ver,
                pydantic_validation_errs1=pydantic_errs1,
                pydantic_validation_errs2=pydantic_errs2,
                pydantic_validation_errs_diff=diff(
                    pydantic_errs1, pydantic_errs2, marshal=True
                ),
                jsonschema_validation_errs1=jsonschema_errs1,
                jsonschema_validation_errs2=jsonschema_errs2,
                jsonschema_validation_errs_diff=diff(
                    [e.model_dump(mode="json") for e in jsonschema_errs1],
                    [e.model_dump(mode="json") for e in jsonschema_errs2],
                    marshal=True,
                ),
            )
        )

    return rs


def _asset_validation_diff_reports(
    reports1: AssetValidationReportsType, reports2: AssetValidationReportsType
) -> list[_AssetValidationDiffReport]:
    """
    Get the list of asset validation diff reports of two given collections of asset
    validation reports

    :param reports1: The first collection of asset validation reports
    :param reports2: The second collection of asset validation reports
    :return: The list of asset validation diff reports of the given two collections
    """
    rs1 = _key_reports(reports1)
    rs2 = _key_reports(reports2)

    # Get all entries involved in the two collections of validation reports
    entries = sorted(rs1.keys() | rs2.keys())

    # The list of asset validation diff reports to be returned
    rs = []
    for entry in entries:
        # Get reports at the same entry from the two collections respectively
        r1 = rs1.get(entry)
        r2 = rs2.get(entry)

        if r1 is not None:
            pydantic_errs1 = r1.pydantic_validation_errs
            jsonschema_errs1 = r1.jsonschema_validation_errs
        else:
            pydantic_errs1 = []
            jsonschema_errs1 = []

        if r2 is not None:
            pydantic_errs2 = r2.pydantic_validation_errs
            jsonschema_errs2 = r2.jsonschema_validation_errs
        else:
            pydantic_errs2 = []
            jsonschema_errs2 = []

        # If all errs are empty, skip this entry
        if not any(
            (pydantic_errs1, pydantic_errs2, jsonschema_errs1, jsonschema_errs2)
        ):
            continue

        asset_id = r1.asset_id if r1 is not None else r2.asset_id
        asset_path = r1.asset_path if r1 is not None else r2.asset_path

        dandiset_id, dandiset_ver, asset_idx_str = entry.parts
        rs.append(
            _AssetValidationDiffReport(
                dandiset_identifier=dandiset_id,
                dandiset_version=dandiset_ver,
                asset_id=asset_id,
                asset_path=asset_path,
                asset_idx=int(asset_idx_str),
                pydantic_validation_errs1=pydantic_errs1,
                pydantic_validation_errs2=pydantic_errs2,
                pydantic_validation_errs_diff=diff(
                    pydantic_errs1, pydantic_errs2, marshal=True
                ),
                jsonschema_validation_errs1=jsonschema_errs1,
                jsonschema_validation_errs2=jsonschema_errs2,
                jsonschema_validation_errs_diff=diff(
                    [e.model_dump(mode="json") for e in jsonschema_errs1],
                    [e.model_dump(mode="json") for e in jsonschema_errs2],
                    marshal=True,
                ),
            )
        )

    return rs


def _key_reports(
    reports: ValidationReportsType,
) -> dict[Path, DandisetValidationReport | AssetValidationReport]:
    """
    Key each validation report in a given collection by the path of the corresponding
    metadata instance consisting of the dandiset ID, version, and, in the case of a
    `AssetValidationReport`, the index of the corresponding asset in the containing JSON
    array in `assets.jsonld`

    :param reports: The given collection of validation reports to be keyed
    :return: The collection of validation reports keyed by the corresponding paths as
        a dictionary
    :raises ValueError: If the given collection of reports contains a report that is not
        an instance of `DandisetValidationReport` or `AssetValidationReport`
    """
    if reports:
        r0 = reports[0]
        if isinstance(r0, DandisetValidationReport):
            parts = ["dandiset_identifier", "dandiset_version"]
        elif isinstance(r0, AssetValidationReport):
            parts = ["dandiset_identifier", "dandiset_version", "asset_idx"]
        else:
            msg = f"Unsupported report type: {type(r0)}"
            raise ValueError(msg)

        return {Path(*(str(getattr(r, p)) for p in parts)): r for r in reports}

    return {}


def _output_validation_diff_reports(
    dandiset_validation_diff_reports: list[_DandisetValidationDiffReport],
    asset_validation_diff_reports: list[_AssetValidationDiffReport],
    output_dir: Path,
) -> None:
    """
    Output the validation diff reports

    :param dandiset_validation_diff_reports: The list of dandiset validation diff
        reports to be output
    :param asset_validation_diff_reports: The list of asset validation diff reports
        to be output
    :param output_dir: Path of the directory to write the validation diff reports to
    """
    dandiset_diff_reports_dir = output_dir / "dandiset"
    asset_diff_reports_dir = output_dir / "asset"

    logger.info("Creating validation diff report directory %s", output_dir)
    create_or_replace_dir(output_dir)

    # Output dandiset validation diff reports
    _output_dandiset_validation_diff_reports(
        dandiset_validation_diff_reports, dandiset_diff_reports_dir
    )

    # Output asset validation diff reports
    _output_asset_validation_diff_reports(
        asset_validation_diff_reports, asset_diff_reports_dir
    )


def _output_dandiset_validation_diff_reports(
    reports: list[_DandisetValidationDiffReport],
    output_dir: Path,
) -> None:
    """
    Output dandiset validation diff reports

    :param reports: The reports to be output
    :param output_dir: Path of the directory to write the reports to
    """
    logger.info("Creating dandiset validation diff report directory %s", output_dir)
    output_dir.mkdir(parents=True)

    (
        pydantic_err1_reps,
        pydantic_err2_reps,
        jsonschema_err1_reps,
        jsonschema_err2_reps,
    ) = err_reps(reports)

    pydantic_validation_errs1_ctr = count_pydantic_validation_errs(pydantic_err1_reps)
    pydantic_validation_errs2_ctr = count_pydantic_validation_errs(pydantic_err2_reps)
    jsonschema_validation_errs1_ctr = count_jsonschema_validation_errs(
        jsonschema_err1_reps
    )
    jsonschema_validation_errs2_ctr = count_jsonschema_validation_errs(
        jsonschema_err2_reps
    )

    with (output_dir / PYDANTIC_ERRS_SUMMARY_FNAME).open("w") as summary_f:
        # Write the summary of the Pydantic validation error differences
        # noinspection PyTypeChecker
        summary_f.write(
            validation_err_diff_summary(
                pydantic_validation_errs1_ctr,
                pydantic_validation_errs2_ctr,
                pydantic_validation_err_diff_detailed_table,
            )
        )

    with (output_dir / JSONSCHEMA_ERRS_SUMMARY_FNAME).open("w") as summary_f:
        # Write the summary of the JSON schema validation error differences
        # noinspection PyTypeChecker
        summary_f.write(
            validation_err_diff_summary(
                jsonschema_validation_errs1_ctr,
                jsonschema_validation_errs2_ctr,
                jsonschema_validation_err_diff_detailed_table,
            )
        )

    # Output individual dandiset validation diff reports by writing the supporting
    # files
    for r in reports:
        report_dir = output_dir / r.dandiset_identifier / r.dandiset_version
        _output_supporting_files(r, report_dir)

        logger.info(
            "Wrote dandiset %s validation diff report supporting files to %s",
            r.dandiset_identifier,
            report_dir,
        )

    logger.info("Output of dandiset validation diff reports is complete")


def _output_asset_validation_diff_reports(
    reports: list[_AssetValidationDiffReport],
    output_dir: Path,
) -> None:
    """
    Output asset validation diff reports

    :param reports: The reports to be output
    :param output_dir: Path of the directory to write the reports to
    """
    output_dir.mkdir(parents=True)
    logger.info("Created asset validation diff report directory %s", output_dir)

    (
        pydantic_err1_reps,
        pydantic_err2_reps,
        jsonschema_err1_reps,
        jsonschema_err2_reps,
    ) = err_reps(reports)

    pydantic_validation_errs1_ctr = count_pydantic_validation_errs(pydantic_err1_reps)
    pydantic_validation_errs2_ctr = count_pydantic_validation_errs(pydantic_err2_reps)
    jsonschema_validation_errs1_ctr = count_jsonschema_validation_errs(
        jsonschema_err1_reps
    )
    jsonschema_validation_errs2_ctr = count_jsonschema_validation_errs(
        jsonschema_err2_reps
    )

    with (output_dir / PYDANTIC_ERRS_SUMMARY_FNAME).open("w") as summary_f:
        # Write the summary of the Pydantic validation error differences
        # noinspection PyTypeChecker
        summary_f.write(
            validation_err_diff_summary(
                pydantic_validation_errs1_ctr,
                pydantic_validation_errs2_ctr,
                pydantic_validation_err_diff_detailed_table,
            )
        )

    with (output_dir / JSONSCHEMA_ERRS_SUMMARY_FNAME).open("w") as summary_f:
        # Write the summary of the JSON schema validation error differences
        # noinspection PyTypeChecker
        summary_f.write(
            validation_err_diff_summary(
                jsonschema_validation_errs1_ctr,
                jsonschema_validation_errs2_ctr,
                jsonschema_validation_err_diff_detailed_table,
            )
        )

    # Output individual asset validation diff reports by writing the constituting
    # files
    for r in reports:
        report_dir = (
            output_dir / r.dandiset_identifier / r.dandiset_version / str(r.asset_idx)
        )
        _output_supporting_files(r, report_dir)

        logger.info(
            "Dandiset %s:%s - asset %sat index %d: "
            "Wrote asset validation diff report constituting files to %s",
            r.dandiset_identifier,
            r.dandiset_version,
            f"{r.asset_id} " if r.asset_id else "",
            r.asset_idx,
            report_dir,
        )

    logger.info("Output of asset validation diff reports is complete")


PYDANTIC_ERRS1_BASE_FNAME = "pydantic_validation_errs1"
PYDANTIC_ERRS2_BASE_FNAME = "pydantic_validation_errs2"
PYDANTIC_ERRS_DIFF_BASE_FNAME = "pydantic_validation_errs_diff"
JSONSCHEMA_ERRS1_BASE_FNAME = "jsonschema_validation_errs1"
JSONSCHEMA_ERRS2_BASE_FNAME = "jsonschema_validation_errs2"
JSONSCHEMA_ERRS_DIFF_BASE_FNAME = "jsonschema_validation_errs_diff"


def _output_supporting_files(r: _DandiValidationDiffReport, report_dir: Path) -> None:
    """
    Output the supporting files of an individual validation diff report

    :param r: The individual validation diff report
    :param report_dir: The directory to write the supporting files to
    """
    report_dir.mkdir(parents=True)

    for data, base_fname in (
        (r.pydantic_validation_errs1, PYDANTIC_ERRS1_BASE_FNAME),
        (r.pydantic_validation_errs2, PYDANTIC_ERRS2_BASE_FNAME),
        (r.pydantic_validation_errs_diff, PYDANTIC_ERRS_DIFF_BASE_FNAME),
        (
            [e.model_dump(mode="json") for e in r.jsonschema_validation_errs1],
            JSONSCHEMA_ERRS1_BASE_FNAME,
        ),
        (
            [e.model_dump(mode="json") for e in r.jsonschema_validation_errs2],
            JSONSCHEMA_ERRS2_BASE_FNAME,
        ),
        (r.jsonschema_validation_errs_diff, JSONSCHEMA_ERRS_DIFF_BASE_FNAME),
    ):
        if data:
            write_data(data, report_dir, base_fname)


def pydantic_err_categorizer(
    err: PydanticValidationErrRep,
) -> tuple[str, str, tuple]:
    """
    Categorize a Pydantic validation error represented as a tuple using the same
    tuple without the path component to the dandiset at a particular version and
    with a generalized "loc" with all array indices replaced by "[*]"

    :param err: The tuple representing the Pydantic validation error
    :return: The tuple representing the category that the error belongs to
    """
    type_, msg = err[0], err[1]

    # Categorize the "loc" by replacing all array indices with "[*]"
    categorized_loc = tuple("[*]" if isinstance(v, int) else v for v in err[2])

    return type_, msg, categorized_loc


def jsonschema_err_categorizer(
    err: JsonschemaValidationErrRep,
) -> tuple[tuple, tuple]:
    """
    Categorize a JSON schema validation error represented as a tuple

    :param err: The tuple representing the JSON schema validation error
    :return: The tuple representing the category that the error belongs to
    """
    err_model = err[0]
    # Categorize the "absolute_path" by replacing all array indices with "[*]"
    categorized_absolute_path = tuple(
        "[*]" if isinstance(v, int) else v for v in err_model.absolute_path
    )

    return err_model.absolute_schema_path, categorized_absolute_path


def pydantic_err_rep(err: dict[str, Any], path: Path) -> PydanticValidationErrRep:
    """
    Get a representation of a Pydantic validation error as a tuple for counting

    :param err: The Pydantic validation error as a `dict`
    :param path: The path the data instance that the error pertained to
    :return: The representation of the Pydantic validation error as tuple consisting of
        the values for the `'type'`, `'msg'`, `'loc'` keys of the error and `path`.
        Note: The value of the `'loc'` key is converted to a tuple from a list
    """
    return err["type"], err["msg"], tuple(err["loc"]), path


def jsonschema_err_rep(
    err: JsonschemaValidationErrorModel, path: Path
) -> JsonschemaValidationErrRep:
    """
    Get a representation of a JSON schema validation error as a tuple for counting

    :param err: The JSON schema validation error
    :param path: The path the data instance that the error pertained to
    :return: The representation of the JSON schema validation error as tuple consisting
        of the error and `path`
    """
    return err, path


def err_reps(
    rs: list[_DandisetValidationDiffReport] | list[_AssetValidationDiffReport],
) -> tuple[
    Iterable[PydanticValidationErrRep],
    Iterable[PydanticValidationErrRep],
    Iterable[JsonschemaValidationErrRep],
    Iterable[JsonschemaValidationErrRep],
]:
    """
    Get all validation errors in given reports and return them in tuple presentations
    suitable for counting

    :param rs: The given reports
    :return: A tuple of four elements:
        1. An iterable of representations of all errors in `pydantic_validation_errs1`
            of all reports
        2. An iterable of representations of all errors in `pydantic_validation_errs2`
            of all reports
        3. An iterable of representations of all errors in `jsonschema_validation_errs1`
            of all reports
        4. An iterable of representations of all errors in `jsonschema_validation_errs2`
            of all reports
    """

    pydantic_err1_rep_lsts: list[list[PydanticValidationErrRep]] = []
    pydantic_err2_rep_lsts: list[list[PydanticValidationErrRep]] = []
    jsonschema_err1_rep_lsts: list[list[JsonschemaValidationErrRep]] = []
    jsonschema_err2_rep_lsts: list[list[JsonschemaValidationErrRep]] = []

    if rs:
        r0 = rs[0]
        if isinstance(r0, _DandisetValidationDiffReport):

            def instance_path():
                return Path(r.dandiset_identifier, r.dandiset_version)

        elif isinstance(r0, _AssetValidationDiffReport):

            def instance_path():
                nonlocal r
                r = cast(_AssetValidationDiffReport, r)
                return Path(r.dandiset_identifier, r.dandiset_version, str(r.asset_idx))

        else:
            msg = f"Unsupported report type: {type(r0)}"
            raise TypeError(msg)

        for r in rs:
            p = instance_path()

            # Tuple representation of the Pydantic validation errors
            pydantic_err1_rep_lsts.append(
                [pydantic_err_rep(e, p) for e in r.pydantic_validation_errs1]
            )
            pydantic_err2_rep_lsts.append(
                [pydantic_err_rep(e, p) for e in r.pydantic_validation_errs2]
            )
            jsonschema_err1_rep_lsts.append(
                [jsonschema_err_rep(e, p) for e in r.jsonschema_validation_errs1]
            )
            jsonschema_err2_rep_lsts.append(
                [jsonschema_err_rep(e, p) for e in r.jsonschema_validation_errs2]
            )

    return (
        chain.from_iterable(pydantic_err1_rep_lsts),
        chain.from_iterable(pydantic_err2_rep_lsts),
        chain.from_iterable(jsonschema_err1_rep_lsts),
        chain.from_iterable(jsonschema_err2_rep_lsts),
    )


def count_validation_errs(
    err_reps_: Iterable[tuple], err_categorizer: Callable[[Any], tuple]
) -> ValidationErrCounter:
    """
    Count validation errors represented by tuples

    :param err_reps_: The validation errors represented as tuples
    :param err_categorizer: A function that categorizes validation errors, represented
        by tuples, into categories, also represented by tuples
    :return: A `ValidationErrCounter` object representing the counts
    """
    ctr = ValidationErrCounter(err_categorizer)
    ctr.count(err_reps_)

    return ctr


def count_pydantic_validation_errs(
    err_reps_: Iterable[PydanticValidationErrRep],
) -> ValidationErrCounter:
    """
    Pydantic validation errors provided by an iterable

    :param err_reps_: The iterable of Pydantic validation errors represented as tuples
        defined by the output of `pydantic_err_rep`
    :return: A `ValidationErrCounter` object representing the counts
    """
    return count_validation_errs(err_reps_, pydantic_err_categorizer)


def count_jsonschema_validation_errs(
    err_reps_: Iterable[JsonschemaValidationErrRep],
) -> ValidationErrCounter:
    """
    Count JSON schema validation errors provided by an iterable

    :param err_reps_: The iterable of JSON schema validation errors represented as
        tuples defined by the output of `jsonschema_err_rep`
    :return: A `ValidationErrCounter` object representing the counts
    """
    return count_validation_errs(err_reps_, jsonschema_err_categorizer)
