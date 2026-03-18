"""CLI command for overnight activity report generation."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from hermit.surfaces.cli.main import app


@app.command("overnight")
def overnight_report(
    lookback: int = typer.Option(12, help="Lookback window in hours"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Generate overnight activity report."""
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.assembly.config import get_settings

    settings = get_settings()
    db_path = Path(settings.base_dir) / "kernel" / "state.db"
    if not db_path.exists():
        typer.echo("No kernel database found.")
        raise typer.Exit(1)
    store = KernelStore(db_path)
    try:
        from hermit.plugins.builtin.hooks.overnight.report import OvernightReportService

        service = OvernightReportService(store)
        summary = service.generate(lookback_hours=lookback)
        if json_output:
            typer.echo(json.dumps(service.format_dashboard_json(summary), indent=2))
        else:
            typer.echo(service.format_markdown(summary))
    finally:
        store.close()
