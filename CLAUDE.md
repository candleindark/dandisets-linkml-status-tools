# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

Tools for reporting the status of translating DANDI schemas from Pydantic (`dandischema`) to LinkML (via `pydantic2linkml`), and for validating real DANDI metadata (dandisets and assets) against both representations. Output is a set of human-readable reports written to a directory (default `reports/`).

## Commands

This project uses [Hatch](https://hatch.pypa.io/) with `uv` as installer. Python >= 3.10.

- Run the CLI: `hatch run dandisets-report-tools --help`
  - Entry point: `dandisets_linkml_status_tools.cli:app` (Typer app).
- Run all tests (matches CI): `hatch run test:python -m pytest --numprocesses=logical -s -v tests`
- Run a single test: `hatch run test:python -m pytest tests/test_tools/test_md.py::test_name -v`
- Type-check: `hatch run types:check` (runs `mypy` on `src/` and `tests/`).
- Lint config lives under `[tool.ruff]` in `pyproject.toml` (line length 88). There is no dedicated `hatch run lint` env.
- Codespell runs in CI (config in `pyproject.toml` under `[tool.codespell]`).

## CLI subcommands

The Typer app exposes these commands (see `src/dandisets_linkml_status_tools/cli/__init__.py`):

- `linkml-translation` — pulls dandisets from a live DANDI instance via `DandiAPIClient` and reports Pydantic→LinkML translation/validation status for draft and latest-published versions.
- `manifests <manifest_path>` — walks a local `dandiset/<id>/<version>/` tree containing `dandiset.jsonld` and `assets.jsonld` (e.g. `manifests-migrated-dandischema-0_11_0/`) and writes per-dandiset and per-asset validation reports. Uses `Dandiset`/`PublishedDandiset` and `Asset`/`PublishedAsset` depending on whether the version is `draft`.
- `diff-manifests-reports` — diffs two previously generated manifests report directories.
- `migrate-manifests-dandisets` — migrates `Dandiset` metadata in a manifests tree to the latest `Dandiset` model.

Global options (must come before the subcommand): `--output-dir-path/-o` (default `reports`) and `--log-level/-l`.

## Architecture

Three layers, kept intentionally thin:

1. **`cli/__init__.py`** — Typer wiring. Holds a module-global `config: Config` populated in the `@app.callback()`; subcommands read `config["output_dir_path"]`. Heavy subcommand logic lives in `cmd_funcs/` and is imported lazily inside the command functions to keep CLI startup fast.
2. **`models.py`** — Pydantic/TypedDict definitions plus module-level `TypeAdapter` instances (e.g. `DANDISET_VALIDATION_REPORTS_ADAPTER`, `ASSET_VALIDATION_REPORTS_ADAPTER`, `DANDI_METADATA_LIST_ADAPTER`). Reports are serialized/deserialized through these adapters — prefer reusing an existing adapter over calling `TypeAdapter(...)` inline. `JsonValidationErrorView` is a serialization-safe projection of `jsonschema.exceptions.ValidationError`; `PolishedValidationResult` is a `TypedDict` built dynamically from `linkml.validator.report.ValidationResult` fields.
3. **`tools/`** — stateless helpers:
   - `tools/__init__.py`: LinkML generation (`translate_defs` from `pydantic2linkml`, `ShaclGenerator`, `OwlSchemaGenerator`), report compilation (`compile_dandiset_linkml_translation_report`), directory/report I/O (`create_or_replace_dir`, `output_reports`, `write_reports`, `get_direct_subdirs`), and `pydantic_validate`.
   - `tools/jsonschema.py`: `jsonschema_validator(schema, check_format=True)` factory and `err_lst` to collect errors.
   - `tools/md.py`: Markdown table helpers used by the report writers.
   - `tools/validation_err_counter.py`: aggregates/counts validation errors for summary tables.
- `cmd_funcs/` — the actual implementations for the `diff-manifests-reports` and `migrate-manifests-dandisets` commands.

Validation of every metadata record is done **twice** — once through Pydantic (`pydantic_validate`) and once through a `jsonschema` validator built from `Model.model_json_schema()` with format checking on — and both error lists are stored on the report. When adding new validation commands, follow this dual-validation pattern and reuse the existing report models and `TypeAdapter`s.

## Data conventions

- Report output dir layout: `reports/<subdir>/...`. The `manifests` command writes under `reports/manifests/` (`dandiset_validation_reports.json`, `asset_validation_reports.json`); `linkml-translation` writes under `reports/linkml_translation/<dandi_instance>/`.
- `create_or_replace_dir` wipes the target directory before writing — assume report subdirectories are ephemeral.
- The top-level `manifests-migrated-dandischema-0_11_0/` directory is fixture-like input data for the `manifests` command, not source code.
- `dandi-linkml-schema.owl.ttl` and `dandi-linkml-schema.shacl.ttl` at the repo root are generated artifacts from the LinkML translation; they are gitignored-in-spirit (appear as untracked) and should not be hand-edited.

## PR checklist style

When writing a PR description with a test plan, use `- [x]` for steps already completed and `- [ ]` for steps still to do.

## Keeping documentation in sync

Whenever new information is discovered about the project that makes existing documentation (this `CLAUDE.md`, `README.md`, docstrings, etc.) inaccurate or incomplete, update the affected documentation as part of the same change. This is especially important when preparing a PR or a commit to the `main` branch — verify that the docs still match the code before opening the PR or committing.
