import logging
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from dandi.dandiapi import DandiAPIClient
from pydantic2linkml.cli.tools import LogLevel

from dandisets_linkml_status.cli.tools import compile_validation_report, output_reports

if TYPE_CHECKING:
    from dandisets_linkml_status.cli.models import DandisetValidationReport

logger = logging.getLogger(__name__)
app = typer.Typer()


@app.command()
def main(
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
    logging.basicConfig(level=getattr(logging, log_level))

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
                dandiset_latest = dandiset.for_version(most_recent_published_version)
                dandiset_draft = dandiset.for_version(dandiset.draft_version)

                report_on_latest = compile_validation_report(dandiset_latest)
                report_on_draft = compile_validation_report(dandiset_draft)

                validation_reports.append(report_on_latest)

                # Only attach the report on the draft version if it is different from
                # the latest version in modification time or status
                if (
                    report_on_draft.dandiset_version_modified
                    != report_on_latest.dandiset_version_modified
                    or report_on_draft.dandiset_version_status
                    is not report_on_latest.dandiset_version_status
                ):
                    validation_reports.append(report_on_draft)
            else:
                # === The dandiset has never been published ===
                # === Only a draft version is available ===
                dandiset_draft = dandiset
                report_on_draft = compile_validation_report(dandiset_draft)
                validation_reports.append(report_on_draft)

    # Print summary of validation reports
    print(
        "\n".join(
            f"dandiset: {r.dandiset_identifier}, "
            f"linkml: {len(r.linkml_validation_errs)}, "
            f"pydantic: {len(r.pydantic_validation_errs)}"
            for r in validation_reports
        )
    )

    output_reports(validation_reports, output_path)

    logger.info("Success!")
