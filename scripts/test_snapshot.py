#!/usr/bin/env python
"""Generate AzBrief reports from an immutable local azsnapshot export."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from scripts.resource_snapshot import SnapshotResourceGraphService, SnapshotRuntime

_AZD_ENV_ALIASES = {
    "FOUNDRY_PROJECT_ENDPOINT": (
        "FOUNDRY_PROJECT_ENDPOINT",
        "AZURE_AI_PROJECT_ENDPOINT",
        "AZURE_AIPROJECT_ENDPOINT",
    ),
    "FOUNDRY_COORDINATOR_AGENT_NAME": ("AZBRIEF_PROMPT_COORDINATOR_AGENT_NAME",),
    "FOUNDRY_RESOURCE_GRAPH_AGENT_NAME": ("AZBRIEF_PROMPT_RESOURCE_GRAPH_AGENT_NAME",),
    "FOUNDRY_AZURE_MCP_AGENT_NAME": ("AZBRIEF_PROMPT_AZURE_MCP_AGENT_NAME",),
    "FOUNDRY_AZURE_API_AGENT_NAME": ("AZBRIEF_PROMPT_AZURE_API_AGENT_NAME",),
    "FOUNDRY_REPORT_WRITER_AGENT_NAME": ("AZBRIEF_PROMPT_REPORT_WRITER_AGENT_NAME",),
    "FOUNDRY_QUALITY_REVIEWER_AGENT_NAME": ("AZBRIEF_PROMPT_QUALITY_REVIEWER_AGENT_NAME",),
    "AZURE_TENANT_ID": ("AZURE_TENANT_ID",),
}


def _decode_azd_value(raw_value: str) -> str:
    value = raw_value.strip()
    if value.startswith('"') and value.endswith('"'):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, str) else value
        except json.JSONDecodeError:
            return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def _load_azd_environment(environment: str = "") -> list[str]:
    child_env = dict(os.environ)
    child_env["AZURE_DEV_USER_AGENT"] = "microsoft_foundry_skill"
    command = ["azd", "env", "get-values"]
    if environment:
        command.extend(["--environment", environment])
    process = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parent.parent,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(
            "azd env get-values failed; authenticate or select an existing azd environment"
        )
    source = {}
    for line in process.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip():
            source[key.strip()] = _decode_azd_value(value)
    loaded = []
    for target, aliases in _AZD_ENV_ALIASES.items():
        value = next((source.get(alias, "") for alias in aliases if source.get(alias)), "")
        if value:
            os.environ[target] = value
            loaded.append(target)
    return sorted(loaded)


def _default_output() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("eval_runs") / f"snapshot-reports-{stamp}.jsonl"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replay an azsnapshot NDJSON export through the local AzBrief analysis harness. "
            "No live tenant evidence tools are used."
        )
    )
    parser.add_argument(
        "--snapshot-dir",
        required=True,
        type=Path,
        help="Directory containing manifest.json and exported *.ndjson tables",
    )
    parser.add_argument(
        "--max-result-rows",
        type=int,
        default=5000,
        help="Maximum detail rows returned by one local KQL query (default: 5000)",
    )
    parser.add_argument(
        "--use-azd-env",
        action="store_true",
        help="Load Foundry project and Prompt Agent names from azd env into this process only",
    )
    parser.add_argument(
        "--azd-environment",
        default="",
        help="azd environment name used with --use-azd-env (defaults to the selected env)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="Validate snapshot structure and counts")
    validate.add_argument(
        "--full",
        action="store_true",
        help="Parse every NDJSON row and compare logical row counts with manifest.json",
    )

    commands.add_parser("resources", help="Show resource type and region summaries")

    query = commands.add_parser("query", help="Run one supported KQL query against the snapshot")
    query.add_argument("--kql", required=True, help="Resource Graph KQL query")

    analyze = commands.add_parser("analyze", help="Generate report JSONL from snapshot evidence")
    source = analyze.add_mutually_exclusive_group(required=True)
    source.add_argument("--latest", action="store_true", help="Analyze the latest RSS update")
    source.add_argument("--url", help="Analyze one Azure Update URL")
    source.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD")
    analyze.add_argument("--to", dest="to_date", metavar="YYYY-MM-DD")
    analyze.add_argument(
        "--output",
        type=Path,
        help="Destination JSONL; defaults under eval_runs/ and never sends email",
    )
    return parser


def _print_metadata(service: SnapshotResourceGraphService) -> None:
    metadata = service.metadata
    print("AzBrief offline snapshot test mode")
    print(f"  azsnapshot version: {metadata.version}")
    print(f"  captured: {metadata.started_utc} -> {metadata.finished_utc}")
    print(f"  subscriptions: {metadata.subscription_count}")
    print(f"  tables: {metadata.table_count}")
    print(f"  manifest warnings/errors: {metadata.warning_count}/{metadata.error_count}")
    print("  live Azure tenant evidence: disabled")
    print("  deployment packaging: excluded (scripts/ and tests/)")


async def _show_resources(service: SnapshotResourceGraphService) -> None:
    type_result, region_result = await asyncio.gather(
        service.get_resource_types_summary(),
        service.query_resources("Resources | summarize count() by location | order by count_ desc"),
    )
    print("\nTop resource types")
    for row in type_result["data"][:30]:
        print(f"  {row.get('type', 'Unknown')}: {row.get('count_', 0)}")
    print("\nTop regions")
    for row in region_result["data"][:20]:
        print(f"  {row.get('location', 'Unknown')}: {row.get('count_', 0)}")


async def _run_analysis(
    service: SnapshotResourceGraphService,
    *,
    latest: bool,
    url: Optional[str],
    from_date: Optional[str],
    to_date: Optional[str],
    output: Path,
) -> None:
    with SnapshotRuntime(service) as runtime:
        from scripts import test_local

        original_analyzer = test_local.AzureUpdateAnalyzer
        original_get_settings = test_local.get_settings
        test_local.AzureUpdateAnalyzer = runtime.analyzer_class
        test_local.get_settings = lambda: runtime.settings
        try:
            if from_date:
                await test_local.analyze_date_range(
                    from_date=from_date,
                    to_date=to_date,
                    jsonl_path=str(output),
                )
            else:
                await test_local.analyze_update(
                    url=url,
                    latest=latest,
                    jsonl_path=str(output),
                )
        finally:
            test_local.AzureUpdateAnalyzer = original_analyzer
            test_local.get_settings = original_get_settings


def main() -> int:
    args = _build_parser().parse_args()
    if args.max_result_rows < 1 or args.max_result_rows > 50_000:
        print("--max-result-rows must be between 1 and 50000", file=sys.stderr)
        return 2
    if args.azd_environment and not args.use_azd_env:
        print("--azd-environment requires --use-azd-env", file=sys.stderr)
        return 2
    if args.use_azd_env:
        try:
            loaded = _load_azd_environment(args.azd_environment)
        except Exception as exc:
            print(f"Unable to load azd environment: {exc}", file=sys.stderr)
            return 1
        print("Loaded azd keys in memory: " + ", ".join(loaded))
    try:
        service = SnapshotResourceGraphService(
            args.snapshot_dir,
            max_result_rows=args.max_result_rows,
        )
    except Exception as exc:
        print(f"Snapshot initialization failed: {exc}", file=sys.stderr)
        return 1

    _print_metadata(service)
    if args.command == "validate":
        result = service.validate(full=args.full)
        print(f"  validation mode: {result['mode']}")
        print(f"  checked tables: {len(result['checked'])}")
        print(f"  missing tables: {len(result['missing_tables'])}")
        print(f"  malformed tables: {len(result['malformed_tables'])}")
        print(f"  count mismatches: {len(result['count_mismatches'])}")
        print(f"  status: {'PASS' if result['ok'] else 'FAIL'}")
        if result["missing_tables"]:
            print("  missing: " + ", ".join(result["missing_tables"]))
        if result["malformed_tables"]:
            print("  malformed: " + ", ".join(sorted(result["malformed_tables"])))
        if result["count_mismatches"]:
            print(
                "  mismatched counts: "
                + json.dumps(result["count_mismatches"], ensure_ascii=True, sort_keys=True)
            )
        return 0 if result["ok"] else 1
    if args.command == "resources":
        asyncio.run(_show_resources(service))
        return 0
    if args.command == "query":
        result = asyncio.run(service.query_resources(args.kql))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "analyze":
        output = args.output or _default_output()
        output.parent.mkdir(parents=True, exist_ok=True)
        print(f"  report output: {output}")
        print("  email delivery: disabled")
        asyncio.run(
            _run_analysis(
                service,
                latest=args.latest,
                url=args.url,
                from_date=args.from_date,
                to_date=args.to_date,
                output=output,
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
