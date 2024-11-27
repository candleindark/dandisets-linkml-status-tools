import logging
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from typing import TYPE_CHECKING, Annotated, Any

import typer
from dandischema.models import Asset, Dandiset, PublishedAsset, PublishedDandiset

from dandi.dandiapi import DandiAPIClient
from pydantic2linkml.cli.tools import LogLevel


from dandisets_linkml_status_tools.cli.tools import (
    compile_dandiset_validation_report,
    output_reports,
)

if TYPE_CHECKING:
    from dandisets_linkml_status_tools.cli.models import DandisetValidationReport

logger = logging.getLogger(__name__)
app = typer.Typer()


@app.command()
def linkml_translation(
    *,
    include_unpublished: Annotated[
        bool, typer.Option("--include-unpublished", "-u")
    ] = False,
    dandi_instance: Annotated[
        str,
        typer.Option(
            "--dandi-instance",
            "-i",
            help="The DANDI server instance from which the dandiset metadata are "
            "downloaded",
        ),
    ] = "dandi",
    log_level: Annotated[
        LogLevel, typer.Option("--log-level", "-l")
    ] = LogLevel.WARNING,
):
    # Set log level of the CLI
    logging.basicConfig(
        format="[%(asctime)s]%(levelname)s:%(name)s:%(message)s",
        level=getattr(logging, log_level),
    )

    output_path = Path(dandi_instance + "-reports")

    validation_reports: list[DandisetValidationReport] = []

    with DandiAPIClient.for_dandi_instance(dandi_instance) as client:
        # Generate validation reports for danidsets
        for dandiset in client.get_dandisets(draft=include_unpublished, order="id"):
            dandiset_id = dandiset.identifier
            logger.info("Processing dandiset %s", dandiset_id)

            most_recent_published_version = dandiset.most_recent_published_version

            if most_recent_published_version is not None:
                # === The dandiset has been published ===
                # Get the draft version
                dandiset_draft = dandiset.for_version(dandiset.draft_version)

                # Get the latest published version
                dandiset_latest = dandiset.for_version(most_recent_published_version)

                # Handle the latest published version
                report_on_latest = compile_dandiset_validation_report(
                    dandiset_latest, is_dandiset_published=True
                )
                validation_reports.append(report_on_latest)
            else:
                # === The dandiset has never been published ===
                # === Only a draft version is available ===
                dandiset_draft = dandiset

            # Handle the draft version
            report_on_draft = compile_dandiset_validation_report(
                dandiset_draft, is_dandiset_published=False
            )
            validation_reports.append(report_on_draft)

    # Print summary of validation reports
    print(  # noqa: T201
        "\n".join(
            f"dandiset: {r.dandiset_identifier}, "
            f"linkml: {len(r.linkml_validation_errs)}, "
            f"pydantic: {len(r.pydantic_validation_errs)}"
            for r in validation_reports
        )
    )

    output_reports(validation_reports, output_path)

    logger.info("Success!")


# === temporary setup ===
from dandisets_linkml_status_tools.models import (
    DandisetValidationReport as DandisetValidationReport_,
    AssetValidationReport,
)
from dandisets_linkml_status_tools.tools import (
    pydantic_validate,
    iter_direct_subdirs,
    write_reports,
)

# Directory containing dandiset manifests
MANIFEST_DIR = Path("/Users/isaac/Downloads/mnt/backup/dandi/dandiset-manifests-s3cmd")

# metadata file names
DANDISET_FILE_NAME = "dandiset.jsonld"  # File with dandiset metadata
ASSETS_FILE_NAME = "assets.jsonld"  # File with assets metadata

# Directory and file paths for reports
REPORTS_DIR_PATH = Path("reports/validation")
DANDISET_PYDANTIC_REPORTS_FILE_PATH = (
    REPORTS_DIR_PATH / "dandiset_pydantic_validation_reports.json"
)
ASSET_PYDANTIC_REPORTS_FILE_PATH = (
    REPORTS_DIR_PATH / "asset_pydantic_validation_reports.json"
)

# Pydantic type adapters
DANDISET_PYDANTIC_REPORT_LIST_ADAPTER = TypeAdapter(list[DandisetValidationReport_])
ASSET_PYDANTIC_REPORT_LIST_ADAPTER = TypeAdapter(list[AssetValidationReport])


@app.command()
def manifests(
    *,
    log_level: Annotated[
        LogLevel, typer.Option("--log-level", "-l")
    ] = LogLevel.WARNING,
):
    # Set log level of the CLI
    logging.basicConfig(
        format="[%(asctime)s]%(levelname)s:%(name)s:%(message)s",
        level=getattr(logging, log_level),
    )

    def append_dandiset_validation_report() -> None:
        """
        Append a `DandisetValidationReport_` object to `dandiset_validation_reports`
        if the current dandiset version directory contains a dandiset metadata file.
        """
        dandiset_metadata_file_path = version_dir / DANDISET_FILE_NAME

        # Return immediately if the dandiset metadata file does not exist in the current
        # dandiset version directory
        if not dandiset_metadata_file_path.is_file():
            return

        # Get the Pydantic model to validate against
        if dandiset_version == "draft":
            model = Dandiset
        else:
            model = PublishedDandiset

        dandiset_metadata = dandiset_metadata_file_path.read_text()
        pydantic_validation_errs = pydantic_validate(dandiset_metadata, model)
        # noinspection PyTypeChecker
        dandiset_validation_reports.append(
            DandisetValidationReport_(
                dandiset_identifier=dandiset_identifier,
                dandiset_version=dandiset_version,
                pydantic_validation_errs=pydantic_validation_errs,
            )
        )

    def extend_asset_validation_reports() -> None:
        """
        Extend `asset_validation_reports` with `AssetValidationReport` objects if the
        current dandiset version directory contains an assets metadata file.
        """
        assets_metadata_file_path = version_dir / ASSETS_FILE_NAME

        # Return immediately if the assets metadata file does not exist in the current
        # dandiset version directory
        if not assets_metadata_file_path.is_file():
            return

        # Get the Pydantic model to validate against
        if dandiset_version == "draft":
            model = Asset
        else:
            model = PublishedAsset

        # JSON string read from the assets metadata file
        assets_metadata_json = assets_metadata_file_path.read_text()

        assets_metadata_type_adapter = TypeAdapter(list[dict[str, Any]])
        try:
            # Assets metadata as a list of dictionaries
            assets_metadata_python: list[dict[str, Any]] = (
                assets_metadata_type_adapter.validate_json(assets_metadata_json)
            )
        except ValidationError as e:
            msg = (
                f"The assets metadata file for "
                f"{dandiset_identifier}:{dandiset_version} is of unexpected format."
            )
            raise RuntimeError(msg) from e

        for asset_metadata in assets_metadata_python:
            asset_id = asset_metadata.get("id")
            asset_path = asset_metadata.get("path")
            pydantic_validation_errs = pydantic_validate(asset_metadata, model)
            # noinspection PyTypeChecker
            asset_validation_reports.append(
                AssetValidationReport(
                    dandiset_identifier=dandiset_identifier,
                    dandiset_version=dandiset_version,
                    asset_id=asset_id,
                    asset_path=asset_path,
                    pydantic_validation_errs=pydantic_validation_errs,
                )
            )

    dandiset_validation_reports: list[DandisetValidationReport_] = []
    asset_validation_reports: list[AssetValidationReport] = []
    for n, dandiset_dir in enumerate(
        sorted(iter_direct_subdirs(MANIFEST_DIR), key=lambda p: p.name)
    ):
        # === In a dandiset directory ===
        dandiset_identifier = dandiset_dir.name
        print(f"{n}:{dandiset_identifier}: {dandiset_dir}")

        for version_dir in iter_direct_subdirs(dandiset_dir):
            # === In a dandiset version directory ===
            dandiset_version = version_dir.name
            print(f"\tdandiset_version: {dandiset_version}")

            append_dandiset_validation_report()
            extend_asset_validation_reports()

    # Ensure directory for reports exists
    REPORTS_DIR_PATH.mkdir(parents=True, exist_ok=True)

    # Write the dandiset Pydantic validation reports to a file
    write_reports(
        DANDISET_PYDANTIC_REPORTS_FILE_PATH,
        dandiset_validation_reports,
        DANDISET_PYDANTIC_REPORT_LIST_ADAPTER,
    )

    # Write the asset Pydantic validation reports to a file
    write_reports(
        ASSET_PYDANTIC_REPORTS_FILE_PATH,
        asset_validation_reports,
        ASSET_PYDANTIC_REPORT_LIST_ADAPTER,
    )
