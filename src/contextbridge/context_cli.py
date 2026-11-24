"""CLI entry points for offline context transfer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from contextbridge.context.files import invalid, load_document, output_path, write_artifact
from contextbridge.context.handoff import OutputFormat, render_artifact
from contextbridge.context.local_sessions import load_session
from contextbridge.context.transfer import (
    Category,
    Selection,
    category_counts,
    check_references,
    redact_context,
    safe_artifact_name,
    select_context,
)
from contextbridge.core.exceptions import ContextBridgeError
from contextbridge.models.agent import AgentId
from contextbridge.models.context import ContextDocument

context_app = typer.Typer(help="Inspect and transfer local context offline.", no_args_is_help=True)
JsonOption = Annotated[
    bool, typer.Option("--json", help="Emit one JSON result or operation error.")
]
WorkspaceOption = Annotated[
    Path | None,
    typer.Option("--workspace", help="Check file references for links within this root."),
]
IncludeOption = Annotated[
    list[Category] | None,
    typer.Option("--include", help="Enable a category. Repeat for each category."),
]
ExcludeOption = Annotated[
    list[Category] | None,
    typer.Option("--exclude", help="Omit a category. Repeat for each category."),
]
SnippetsOption = Annotated[
    bool, typer.Option("--include-snippets", help="Keep existing snippets from file references.")
]
ExtensionsOption = Annotated[
    bool,
    typer.Option("--include-extensions", help="Keep opaque vendor data. Requires all categories."),
]
DryRunOption = Annotated[bool, typer.Option("--dry-run", help="Preview without writing files.")]
OutputOption = Annotated[
    Path, typer.Option("--output", help="New artifact in an existing directory.")
]


@dataclass(frozen=True)
class Transfer:
    format: OutputFormat
    output: Path
    dry_run: bool
    workspace: Path | None
    selection: Selection

    def run(self, context: ContextDocument) -> dict[str, object]:
        check_references(context, self.workspace)
        target = output_path(self.output)
        suffix = ".cbcf.json" if self.format == OutputFormat.CBCF else ".md"
        if not target.name.casefold().endswith(suffix):
            raise invalid(f"Output filename must end in {suffix} for this format.")
        selected, redactions = redact_context(select_context(context, self.selection))
        if self.format != OutputFormat.CBCF:
            selected.compatibility.report_loss(
                "context",
                "MARKDOWN_HANDOFF_VIEW",
                "Markdown does not preserve the full CBCF schema.",
            )
        artifact = render_artifact(selected, self.format)
        if not self.dry_run:
            write_artifact(target, artifact)
        original, included = category_counts(context), category_counts(selected)
        return {
            "status": "preview" if self.dry_run else "written",
            "format": self.format.value,
            "artifact_name": safe_artifact_name(target.name),
            "artifact_bytes": len(artifact),
            "selected_categories": sorted(item.value for item in self.selection.enabled(context)),
            "included": included,
            "omitted": {key: count - included[key] for key, count in original.items()},
            "include_snippets": self.selection.snippets,
            "include_extensions": self.selection.extensions,
            "redaction_matches": redactions,
            "compatibility": selected.compatibility.model_dump(mode="json"),
        }


def _echo(data: dict[str, object], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(data, sort_keys=True))
        return
    typer.echo(f"Context {data['status']}.")
    for key, value in data.items():
        if key != "status":
            typer.echo(f"{key}: {json.dumps(value, sort_keys=True)}")


def _error(exc: ContextBridgeError, json_output: bool) -> None:
    if json_output:
        typer.echo(
            json.dumps({"status": "error", "message": exc.message, "exit_code": exc.exit_code})
        )
    else:
        typer.echo(f"Error: {exc.message}", err=True)
    raise typer.Exit(exc.exit_code)


@context_app.command("inspect")
def inspect_context(
    file: Annotated[Path, typer.Argument(help="CBCF JSON file to inspect.")],
    workspace: WorkspaceOption = None,
    json_output: JsonOption = False,
) -> None:
    """Validate a local CBCF document and report counts without printing content."""
    try:
        context = load_document(file)
        check_references(context, workspace)
        cleaned, redactions = redact_context(context)
        text_bytes = sum(
            len(part.text.encode("utf-8"))
            for message in context.messages
            for part in message.parts
            if part.type == "text"
        )
        _echo(
            {
                "status": "valid",
                "schema_version": context.schema_version,
                "provenance": cleaned.provenance.model_dump(
                    mode="json", exclude={"notes", "endpoint_hint"}
                ),
                "message_count": len(context.messages),
                "counts": category_counts(context),
                "estimated_text_tokens": (text_bytes + 3) // 4,
                "redaction_matches": redactions,
                "compatibility": cleaned.compatibility.model_dump(mode="json"),
            },
            json_output,
        )
    except ContextBridgeError as exc:
        _error(exc, json_output)


@context_app.command("export")
def export_context(
    source: Annotated[str, typer.Option("--from", help="CBCF file or agent ID.")],
    output: OutputOption,
    source_file: Annotated[
        Path | None,
        typer.Option("--source", help="Explicit local session/export file for an agent ID."),
    ] = None,
    session: Annotated[
        str | None, typer.Option(help="Require this session ID in the input.")
    ] = None,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", help="Portable output format.")
    ] = OutputFormat.CBCF,
    include: IncludeOption = None,
    exclude: ExcludeOption = None,
    include_snippets: SnippetsOption = False,
    include_extensions: ExtensionsOption = False,
    workspace: WorkspaceOption = None,
    dry_run: DryRunOption = False,
    json_output: JsonOption = False,
) -> None:
    """Export a read-only local transcript or CBCF file with secret redaction."""
    try:
        agents = {agent.value for agent in AgentId}
        if source in agents:
            if source_file is None:
                raise invalid("Exporting from an agent requires an explicit --source file.")
            context = load_session(AgentId(source), source_file)
        else:
            if source_file is not None:
                raise invalid("Use --source only with an agent ID in --from.")
            context = load_document(Path(source))
        if session is not None and (
            context.session is None or context.session.session_id != session
        ):
            raise invalid("The requested session ID is absent from the input.")
        transfer = Transfer(
            output_format,
            output,
            dry_run,
            workspace,
            Selection(
                frozenset(include or ()),
                frozenset(exclude or ()),
                include_snippets,
                include_extensions,
            ),
        )
        _echo(transfer.run(context), json_output)
    except ContextBridgeError as exc:
        _error(exc, json_output)


@context_app.command("import")
def import_context(
    file: Annotated[Path, typer.Argument(help="CBCF JSON file to import.")],
    target: Annotated[
        str, typer.Option("--to", help="Agent ID or existing portable output directory.")
    ],
    output: Annotated[
        Path | None, typer.Option("--output", help="Custom artifact path when --to is an agent ID.")
    ] = None,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", help="Portable output format.")
    ] = OutputFormat.HANDOFF,
    apply: Annotated[bool, typer.Option("--apply", help="Write the portable artifact.")] = False,
    dry_run: DryRunOption = False,
    include: IncludeOption = None,
    exclude: ExcludeOption = None,
    include_snippets: SnippetsOption = False,
    include_extensions: ExtensionsOption = False,
    workspace: WorkspaceOption = None,
    json_output: JsonOption = False,
) -> None:
    """Preview a portable handoff by default. Apply writes a local artifact."""
    try:
        if apply and dry_run:
            raise invalid("Use either --apply or --dry-run.")
        if target in {agent.value for agent in AgentId}:
            directory = Path.cwd()
        else:
            if output is not None:
                raise invalid("Use --output with an agent ID, or specify the directory with --to.")
            directory = Path(target)
        filename = (
            "contextbridge-handoff.cbcf.json"
            if output_format == OutputFormat.CBCF
            else "contextbridge-handoff.md"
        )
        transfer = Transfer(
            output_format,
            output or directory / filename,
            not apply,
            workspace,
            Selection(
                frozenset(include or ()),
                frozenset(exclude or ()),
                include_snippets,
                include_extensions,
            ),
        )
        result = transfer.run(load_document(file))
        result["destination_kind"] = "portable_handoff"
        _echo(result, json_output)
    except ContextBridgeError as exc:
        _error(exc, json_output)


@context_app.command("convert")
def convert_context(
    file: Annotated[Path, typer.Argument(help="CBCF JSON file to convert.")],
    output_format: Annotated[
        OutputFormat, typer.Option("--to-format", help="Portable output format.")
    ],
    output: OutputOption,
    include: IncludeOption = None,
    exclude: ExcludeOption = None,
    include_snippets: SnippetsOption = False,
    include_extensions: ExtensionsOption = False,
    workspace: WorkspaceOption = None,
    dry_run: DryRunOption = False,
    json_output: JsonOption = False,
) -> None:
    """Convert local CBCF to a deterministic scoped CBCF or Markdown handoff."""
    try:
        transfer = Transfer(
            output_format,
            output,
            dry_run,
            workspace,
            Selection(
                frozenset(include or ()),
                frozenset(exclude or ()),
                include_snippets,
                include_extensions,
            ),
        )
        _echo(transfer.run(load_document(file)), json_output)
    except ContextBridgeError as exc:
        _error(exc, json_output)
