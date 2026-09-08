"""Public repository hygiene contracts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_temporary_root_artifacts_are_absent() -> None:
    forbidden = {
        "admin-controls-desktop.png",
        "admin-controls-mobile.png",
        "admin-redesign-desktop.png",
        "admin-redesign-mobile.png",
        "archive-controls-desktop.png",
        "archive-controls-mobile.png",
        "azd-ai-agents-2026-08-27.log",
        "get_stats.py",
        "parse_logs.py",
        "session_logs.txt",
        "verify-readonly.ps1",
    }

    present = {path.name for path in ROOT.iterdir() if path.is_file()}

    assert forbidden.isdisjoint(present)


def test_generated_artifacts_are_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    required_patterns = {
        "/session_logs.txt",
        "/azd-*.log",
        "/get_stats.py",
        "/parse_logs.py",
        "/verify-readonly.ps1",
        "/.agent_configs/",
        "/.playwright-mcp/",
        "/admin-*.png",
        "/archive-*.png",
        "/.github/prompts/banner-design/",
        "/.github/prompts/brand/",
        "/.github/prompts/design/",
        "/.github/prompts/design-system/",
        "/.github/prompts/slides/",
        "/.github/prompts/ui-styling/",
        "/.github/prompts/ui-ux-pro-max/",
        "/.github/prompts/ui-ux-pro-max.prompt.md",
    }

    assert required_patterns <= set(gitignore.splitlines())
