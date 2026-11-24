"""Typer CLI application for ContextBridge."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.markup import escape as escape_markup
from rich.panel import Panel

from contextbridge import __app_name__, __slogan__, __version__
from contextbridge.adapters.registry import list_adapters
from contextbridge.backup.manager import BackupManager
from contextbridge.context_cli import context_app
from contextbridge.core.discovery import discover_skills, scan_agents
from contextbridge.core.doctor import run_doctor
from contextbridge.core.exceptions import ContextBridgeError
from contextbridge.core.paths import EnvironmentContext, default_environment
from contextbridge.models.agent import AgentScope
from contextbridge.skills.exporter import export_skill_zip
from contextbridge.skills.migrator import execute_migration, plan_migration
from contextbridge.skills.validator import validate_skill
from contextbridge.utils.console import (
    console,
    doctor_badge,
    err_console,
    make_table,
    print_error_panel,
    print_json,
    severity_badge,
    status_badge,
    validation_badge,
)

app = typer.Typer(
    name="contextbridge",
    help=f"{__app_name__} — {__slogan__}",
    no_args_is_help=True,
    add_completion=False,
)
skills_app = typer.Typer(
    name="skills",
    help="List, validate, export, and migrate Agent Skills across AI coding agents.",
    no_args_is_help=True,
)
backup_app = typer.Typer(
    name="backup",
    help="Inspect and restore verified local ContextBridge backups.",
    no_args_is_help=True,
)
app.add_typer(skills_app, name="skills")
app.add_typer(backup_app, name="backup")
app.add_typer(context_app, name="context")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"{__app_name__} {__version__}")
        raise typer.Exit(code=0)


@app.callback()
def root_callback(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show ContextBridge version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """ContextBridge — Universal local manager for AI coding agent skills and context."""


def _build_env(project_dir: Path | None) -> EnvironmentContext:
    return default_environment(project_dir=project_dir)


def _is_interactive() -> bool:
    """Return True when stdin is attached to an interactive terminal."""
    return sys.stdin.isatty()


def _authorized_restore_roots(env: EnvironmentContext) -> list[Path]:
    """Collect all known project and global agent skill directories for restore authorization."""
    roots: list[Path] = []
    for adapter in list_adapters():
        for spec in adapter.skill_directories(env):
            roots.append(spec.path)
    return roots


def _handle_domain_error(exc: ContextBridgeError) -> None:
    print_error_panel("ContextBridge Error", exc.message)
    raise typer.Exit(code=exc.exit_code)


@app.command("scan")
def scan_command(
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Project directory to inspect (defaults to current working directory).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON output.",
    ),
) -> None:
    """Detect supported AI coding agents, configuration paths, and skills (read-only)."""
    try:
        env = _build_env(project_dir)
        summary = scan_agents(env)
    except ContextBridgeError as exc:
        _handle_domain_error(exc)

    if json_output:
        payload = {
            "project_dir": str(env.project_dir),
            "unique_physical_skills": summary.unique_physical_skills,
            "shared_directories": [str(p) for p in summary.shared_directories],
            "agents": [a.model_dump(mode="json") for a in summary.agents],
        }
        print_json(payload)
        return

    table = make_table(
        f"Agent Detection Summary ({env.project_dir})",
        ["Agent", "Status", "Scopes", "Existing Directories", "Skills", "Limitations"],
    )
    for det in summary.agents:
        existing_dirs = [str(s.path) for s in det.skill_directories if s.exists]
        if not existing_dirs:
            existing_dirs = [str(p) for p in det.config_directories]
        dirs_display = "\n".join(existing_dirs) if existing_dirs else "—"
        scopes_display = ", ".join(s.value for s in det.supported_scopes)
        limits_display = det.limitations[0] if det.limitations else "—"
        table.add_row(
            det.display_name,
            status_badge(det.status),
            scopes_display,
            dirs_display,
            str(det.skills_found),
            limits_display,
        )

    console.print(table)
    console.print(
        f"[bold]Unique physical skills discovered:[/bold] {summary.unique_physical_skills}"
    )
    if summary.shared_directories:
        shared_str = ", ".join(str(p) for p in summary.shared_directories)
        console.print(
            f"[dim]Shared directories detected across multiple agents: {shared_str}[/dim]"
        )


@skills_app.command("list")
def skills_list_command(
    agent: str | None = typer.Option(
        None,
        "--agent",
        "-a",
        help="Filter skills discovered by a specific agent (e.g., claude, codex, cursor).",
    ),
    scope: AgentScope | None = typer.Option(
        None,
        "--scope",
        "-s",
        help="Filter by scope: project or global.",
    ),
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Project directory to inspect (defaults to current working directory).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON output.",
    ),
) -> None:
    """List discovered Agent Skills with physical-path deduplication (read-only)."""
    try:
        env = _build_env(project_dir)
        records = discover_skills(env, agent_filter=agent, scope_filter=scope)
    except ContextBridgeError as exc:
        _handle_domain_error(exc)

    if json_output:
        print_json([r.model_dump(mode="json") for r in records])
        return

    if not records:
        console.print("[dim]No Agent Skills found for the selected filters.[/dim]")
        return

    table = make_table(
        f"Discovered Skills ({len(records)} unique)",
        ["Name", "Description", "Agents", "Scope", "Status", "Path"],
    )
    for rec in records:
        agents_parts = [a.value for a in rec.associated_agents]
        agents_parts.extend(f"{a.value} (compat)" for a in rec.compatibility_agents)
        desc = rec.description if len(rec.description) <= 68 else rec.description[:65] + "..."
        table.add_row(
            rec.name,
            desc or "—",
            ", ".join(agents_parts) or "—",
            rec.scope.value,
            validation_badge(rec.validation_status),
            str(rec.canonical_path),
        )

    console.print(table)


@skills_app.command("validate")
def skills_validate_command(
    path: Path = typer.Argument(
        ...,
        help="Path to the skill directory containing SKILL.md.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON output.",
    ),
) -> None:
    """Validate a skill directory against the official Agent Skills specification (read-only)."""
    try:
        report = validate_skill(path)
    except ContextBridgeError as exc:
        _handle_domain_error(exc)

    if json_output:
        print_json(report.model_dump(mode="json"))
        raise typer.Exit(code=0 if report.is_valid else 2)

    header = (
        f"[bold]Skill:[/bold] {report.skill_name or path.name}\n"
        f"[bold]Path:[/bold] {report.skill_path}\n"
        f"[bold]Files checked:[/bold] {report.files_checked} | "
        f"[bold]Local references checked:[/bold] {report.local_references_checked}\n"
        f"[bold]Errors:[/bold] {len(report.errors)} | "
        f"[bold]Warnings:[/bold] {len(report.warnings)} | "
        f"[bold]Info:[/bold] {len(report.infos)}"
    )
    border = (
        "green"
        if report.is_valid and not report.warnings
        else ("yellow" if report.is_valid else "red")
    )
    console.print(
        Panel(header, title=f"Validation: {report.status.value.upper()}", border_style=border)
    )

    if report.issues:
        table = make_table("Findings", ["Severity", "Code", "Location", "Message"])
        for issue in report.issues:
            loc = issue.path or "—"
            if issue.line is not None:
                loc = f"{loc}:{issue.line}"
            table.add_row(severity_badge(issue.severity), issue.code, loc, issue.message)
        console.print(table)
    else:
        console.print("[green]No issues found.[/green]")

    console.print(f"[dim]{report.heuristic_notice}[/dim]")
    raise typer.Exit(code=0 if report.is_valid else 2)


@skills_app.command("export")
def skills_export_command(
    path: Path = typer.Argument(
        ...,
        help="Path to the skill directory to export.",
    ),
    export_format: str = typer.Option(
        "zip",
        "--format",
        "-f",
        help="Export archive format (currently supported: zip).",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output ZIP file path or destination directory.",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Allow overwriting an existing output ZIP file.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON output.",
    ),
) -> None:
    """Export a validated skill as a safe, portable Agent Skills ZIP archive."""
    if export_format.lower() != "zip":
        print_error_panel(
            "Unsupported Format",
            f"Unsupported export format {export_format!r}. Currently supported: 'zip'.",
        )
        raise typer.Exit(code=2)

    try:
        result = export_skill_zip(path, output_path=output, overwrite=overwrite)
    except ContextBridgeError as exc:
        _handle_domain_error(exc)

    if json_output:
        print_json(
            {
                "skill_name": result.skill_name,
                "source_path": str(result.source_path),
                "output_path": str(result.output_path),
                "format": result.archive_format,
                "files_archived": result.files_archived,
                "archive_size_bytes": result.archive_size_bytes,
                "archive_sha256": result.archive_sha256,
                "format_note": result.format_note,
            }
        )
        return

    body = (
        f"[bold]Skill:[/bold] {result.skill_name}\n"
        f"[bold]Output:[/bold] {result.output_path}\n"
        f"[bold]Files archived:[/bold] {result.files_archived}\n"
        f"[bold]Size:[/bold] {result.archive_size_bytes} bytes\n"
        f"[bold]SHA-256:[/bold] {result.archive_sha256}\n\n"
        f"[dim]{result.format_note}[/dim]"
    )
    console.print(Panel(body, title="Skill Exported (ZIP)", border_style="green"))


@skills_app.command("migrate")
def skills_migrate_command(
    skill_name: str = typer.Argument(..., help="Name of the skill directory to migrate."),
    from_agent: str = typer.Option(
        ...,
        "--from",
        help="Source agent ID (claude, codex, antigravity, opencode, gemini-cli, cursor).",
    ),
    to_agent: str = typer.Option(
        ...,
        "--to",
        help="Target agent ID (claude, codex, antigravity, opencode, gemini-cli, cursor).",
    ),
    scope: AgentScope | None = typer.Option(
        None,
        "--scope",
        help="Source skill scope (project or global). Auto-detected if omitted.",
    ),
    target_scope: AgentScope | None = typer.Option(
        None,
        "--target-scope",
        help="Target skill scope (project or global). Defaults to source scope.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview migration plan without modifying files (default when --apply is omitted).",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the migration plan to disk.",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Allow replacing an existing target skill (always creates a verified backup first).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm applying changes without interactive prompt.",
    ),
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Project directory (defaults to current working directory).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON output.",
    ),
) -> None:
    """Migrate a skill between agents with validation, dry-run preview, backup, and verification."""
    if dry_run and apply:
        print_error_panel(
            "Conflicting Flags",
            "Cannot combine --dry-run and --apply. Omit --apply for preview or pass --apply alone.",
        )
        raise typer.Exit(code=2)

    effective_dry_run = not apply

    try:
        env = _build_env(project_dir)
        plan = plan_migration(
            skill_name,
            source_agent=from_agent,
            target_agent=to_agent,
            env=env,
            source_scope=scope,
            target_scope=target_scope,
        )
    except ContextBridgeError as exc:
        _handle_domain_error(exc)

    if not json_output:
        plan_lines = [
            f"[bold]Skill:[/bold] {plan.skill_name}",
            (
                f"[bold]Source:[/bold] {plan.source_agent.value} "
                f"({plan.source_scope.value}) -> {plan.source_path}"
            ),
            (
                f"[bold]Target:[/bold] {plan.target_agent.value} "
                f"({plan.target_scope.value}) -> {plan.target_path}"
            ),
            f"[bold]Source validation:[/bold] {plan.source_validation.status.value}",
            f"[bold]Files planned:[/bold] {len(plan.actions)}",
            f"[bold]Backup required:[/bold] {'Yes' if plan.requires_backup else 'No'}",
        ]
        if plan.conflict:
            plan_lines.append(f"[bold yellow]Conflict:[/bold yellow] {plan.conflict.reason}")
        if plan.blocking_reasons:
            plan_lines.append(
                "[bold red]Blocking issues:[/bold red] " + "; ".join(plan.blocking_reasons)
            )
        console.print(Panel("\n".join(plan_lines), title="Migration Plan", border_style="cyan"))

        if plan.actions:
            act_table = make_table(
                "Planned File Actions", ["Action", "Relative Path", "Bytes", "SHA-256"]
            )
            for act in plan.actions:
                act_table.add_row(
                    act.action.value.upper(),
                    act.relative_path,
                    str(act.size_bytes),
                    act.source_sha256[:16] + "...",
                )
            console.print(act_table)

        for warning in plan.compatibility_warnings:
            console.print(f"[yellow]Compatibility note:[/yellow] {escape_markup(warning)}")
        console.print(f"[dim]{escape_markup(plan.portability_notice)}[/dim]")

    if not effective_dry_run and not plan.same_physical_target and plan.can_apply and not yes:
        if not _is_interactive():
            print_error_panel(
                "Confirmation Required",
                "Non-interactive execution with --apply requires explicit --yes confirmation.",
            )
            raise typer.Exit(code=2)
        confirmed = typer.confirm(
            f"Apply migration of '{plan.skill_name}' to {plan.target_path}?",
            default=False,
            err=json_output,
        )
        if not confirmed:
            console.print("[yellow]Migration cancelled by user.[/yellow]")
            raise typer.Exit(code=0)

    try:
        result = execute_migration(
            plan,
            env=env,
            dry_run=effective_dry_run,
            overwrite=overwrite,
        )
    except ContextBridgeError as exc:
        _handle_domain_error(exc)

    if json_output:
        print_json(result.model_dump(mode="json"))
        return

    style = "green" if result.applied else "cyan"
    footer = result.message
    if result.backup_id:
        footer += f"\nBackup ID: {result.backup_id}"
    console.print(Panel(footer, title="Migration Result", border_style=style))


@backup_app.command("list")
def backup_list_command(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON output.",
    ),
) -> None:
    """List local ContextBridge backups (read-only)."""
    env = default_environment()
    manager = BackupManager(env.backups_dir)
    manifests = manager.list_backups()

    if json_output:
        print_json([m.model_dump(mode="json") for m in manifests])
        return

    if not manifests:
        console.print(f"[dim]No local backups found in {env.backups_dir}.[/dim]")
        return

    table = make_table(
        f"Local Backups ({len(manifests)})",
        ["Backup ID", "Created (UTC)", "Operation", "Skill", "Files", "Source Path"],
    )
    for manifest in manifests:
        table.add_row(
            manifest.backup_id,
            manifest.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            manifest.operation,
            manifest.skill_name or "—",
            str(manifest.total_files),
            str(manifest.source_path),
        )
    console.print(table)


@backup_app.command("restore")
def backup_restore_command(
    backup_id: str = typer.Argument(..., help="Backup ID from `contextbridge backup list`."),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply restoration to disk (default is dry-run preview).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm restoration without interactive prompt.",
    ),
    target: Path | None = typer.Option(
        None,
        "--target",
        help="Optional custom directory path to restore into instead of original source_path.",
    ),
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Project directory used to validate authorized restore destinations.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON output.",
    ),
) -> None:
    """Preview or restore a SHA-256 verified local backup."""
    env = _build_env(project_dir)
    manager = BackupManager(
        env.backups_dir,
        allowed_restore_roots=_authorized_restore_roots(env),
        home_dir=env.home_dir,
    )

    try:
        plan = manager.plan_restore(backup_id, target_override=target)
    except ContextBridgeError as exc:
        _handle_domain_error(exc)

    if not json_output:
        integrity_label = "[green]VERIFIED[/green]" if plan.integrity.valid else "[red]FAILED[/red]"
        summary = (
            f"[bold]Backup ID:[/bold] {plan.backup_id}\n"
            f"[bold]Target:[/bold] {plan.restore_target}\n"
            f"[bold]Integrity (SHA-256):[/bold] {integrity_label} "
            f"({plan.integrity.checked_files}/{plan.files_to_restore} file(s))\n"
            f"[bold]Target exists:[/bold] {'Yes' if plan.target_exists else 'No'}"
        )
        console.print(Panel(summary, title="Restore Plan", border_style="cyan"))
        for warn in plan.warnings:
            console.print(f"[yellow]Warning:[/yellow] {warn}")

    if apply and plan.integrity.valid and not yes:
        if not _is_interactive():
            print_error_panel(
                "Confirmation Required",
                "Non-interactive execution with --apply requires explicit --yes confirmation.",
            )
            raise typer.Exit(code=2)
        if not typer.confirm(
            f"Restore backup {backup_id} into {plan.restore_target}?",
            default=False,
            err=json_output,
        ):
            console.print("[yellow]Restore cancelled by user.[/yellow]")
            raise typer.Exit(code=0)

    try:
        result = manager.restore_backup(
            backup_id,
            dry_run=not apply,
            target_override=target,
        )
    except ContextBridgeError as exc:
        _handle_domain_error(exc)

    if json_output:
        print_json(result.model_dump(mode="json"))
        return

    style = "green" if result.applied else "cyan"
    msg = result.message
    if result.pre_restore_backup_id:
        msg += f"\nPre-restore safety backup ID: {result.pre_restore_backup_id}"
    console.print(Panel(msg, title="Restore Result", border_style=style))


@app.command("doctor")
def doctor_command(
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Project directory to diagnose (defaults to current working directory).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON output.",
    ),
) -> None:
    """Run read-only diagnostics on agents, permissions, skills, conflicts, and backups."""
    try:
        env = _build_env(project_dir)
        report = run_doctor(env)
    except ContextBridgeError as exc:
        _handle_domain_error(exc)

    if json_output:
        print_json(report.model_dump(mode="json"))
        raise typer.Exit(code=1 if report.error_count > 0 else 0)

    table = make_table(
        f"ContextBridge Doctor ({report.platform} / Python {report.python_version})",
        ["Status", "Category", "Check", "Message"],
    )
    for check in report.checks:
        msg = check.message
        if check.remediation:
            msg += f"\n[dim]Hint: {check.remediation}[/dim]"
        table.add_row(doctor_badge(check.status), check.category, check.name, msg)

    console.print(table)
    console.print(
        f"[bold]Summary:[/bold] {report.ok_count} OK, {report.warn_count} WARN, "
        f"{report.error_count} ERROR, {report.info_count} INFO"
    )
    if report.error_count > 0:
        err_console.print("[red]Doctor found one or more errors.[/red]")
        raise typer.Exit(code=1)


def main() -> None:
    """Console script entry point."""
    app()
