"""Tests for the community insight service (Azure Weekly commentary)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.community_insights import (
    CommunityInsightService,
    _extract_constraints,
    _is_allowed,
    _is_caveat,
    _parse_board_feed,
    _tokenize,
)

ISSUE_HTML = """
<html><body>
  <h2>Containers</h2>
  <ul>
    <li><a href="https://luke.geek.nz/azure/aks-mgtgw-lab/">AKS managed Gateway API blocks the ALB controller</a>
        by Luke Murray &middot; 4 min read The article explains how AKS's managed Gateway API
        conflicts with the ALB controller.</li>
    <li><a href="https://azure.microsoft.com/updates?id=567787">Generally Available: Encryption in Transit for Azure Files NFS Shares in AKS</a>
        by The Azure Updates Team &middot; 1 min read Restates the announcement.</li>
  </ul>
  <h2>Analytics</h2>
  <ul>
    <li><a href="https://example.com/databricks">Azure Databricks delivers proven business value</a>
        by Someone &middot; 3 min read A Forrester study on Databricks ROI.</li>
  </ul>
</body></html>
"""


def _service_with_entries(tmp_path: Path, entries: list[dict]) -> CommunityInsightService:
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps({"fetched_at": 9e12, "latest_issue": 1, "entries": entries}),
        encoding="utf-8",
    )
    return CommunityInsightService(cache_path=cache)


def _entry(title: str, category: str = "", caveat: bool | None = None) -> dict:
    return {
        "title": title,
        "url": f"https://example.com/{abs(hash(title))}",
        "summary": "summary",
        "category": category,
        "is_caveat": _is_caveat(title) if caveat is None else caveat,
        "issue_url": "https://azureweekly.info/issue-1.html",
    }


def test_is_allowed_blocks_ssrf_targets() -> None:
    assert _is_allowed("https://azureweekly.info/issue-1.html")
    assert not _is_allowed("https://evil.example.com/issue-1.html")
    assert not _is_allowed("file:///etc/passwd")
    assert not _is_allowed("http://169.254.169.254/metadata")


def test_tokenize_uses_word_boundaries() -> None:
    """'bus' must not match 'business' — that mis-ranked Service Bus updates."""
    tokens = _tokenize("Azure Databricks delivers proven business value")
    assert "business" in tokens
    assert "bus" not in tokens


def test_is_caveat_detects_operational_warnings() -> None:
    assert _is_caveat("AKS managed Gateway API blocks the ALB controller")
    assert _is_caveat("Think Twice Before Enabling Inbound Network Protection")
    assert _is_caveat("T-SQL gotchas nobody warns you about")
    assert not _is_caveat("Generally Available: Azure Functions support for Python 3.14")


def test_parse_issue_skips_announcement_restatements() -> None:
    svc = CommunityInsightService()
    entries = svc._parse_issue(ISSUE_HTML, "https://azureweekly.info/issue-1.html")
    titles = [e["title"] for e in entries]

    # The "Azure Updates Team" byline means it just restates the announcement.
    assert not any("Encryption in Transit" in t for t in titles)
    assert any("blocks the ALB controller" in t for t in titles)

    caveat = next(e for e in entries if "ALB controller" in e["title"])
    assert caveat["is_caveat"] is True
    assert caveat["category"] == "Containers"


@pytest.mark.asyncio
async def test_find_related_matches_service(tmp_path: Path) -> None:
    svc = _service_with_entries(
        tmp_path,
        [
            _entry("Azure Kubernetes Service (AKS) on Bare Metal"),
            _entry("Azure Databricks delivers proven business value"),
        ],
    )
    hits = await svc.find_related(
        ["Azure Kubernetes Service"], "GA: Configure AKS backup", auto_refresh=False
    )
    assert [h["title"] for h in hits] == ["Azure Kubernetes Service (AKS) on Bare Metal"]


@pytest.mark.asyncio
async def test_find_related_rejects_single_generic_token(tmp_path: Path) -> None:
    """'gateway' alone matched an AKS post against a VPN Gateway update."""
    svc = _service_with_entries(
        tmp_path,
        [_entry("AKS managed Gateway API blocks the ALB controller", "Containers")],
    )
    hits = await svc.find_related(
        ["Azure VPN Gateway"], "GA: IPv6 support for Azure VPN Gateway", auto_refresh=False
    )
    assert hits == []


@pytest.mark.asyncio
async def test_find_related_ranks_caveats_above_neutral(tmp_path: Path) -> None:
    svc = _service_with_entries(
        tmp_path,
        [
            _entry("Azure Kubernetes Service adds a neutral capability"),
            _entry("Azure Kubernetes Service upgrade fails without this step"),
        ],
    )
    hits = await svc.find_related(["Azure Kubernetes Service"], "", auto_refresh=False)
    assert hits[0]["is_caveat"] is True


@pytest.mark.asyncio
async def test_find_related_returns_empty_without_services(tmp_path: Path) -> None:
    svc = _service_with_entries(tmp_path, [_entry("Anything at all about Kubernetes")])
    assert await svc.find_related([], "some title", auto_refresh=False) == []


@pytest.mark.asyncio
async def test_find_related_empty_cache_without_refresh(tmp_path: Path) -> None:
    svc = CommunityInsightService(cache_path=tmp_path / "missing.json")
    assert await svc.find_related(["Azure Storage"], "x", auto_refresh=False) == []


# --------------------------------------------------------------- full text

BOARD_FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Monitoring pg_repack</title>
    <link>https://techcommunity.microsoft.com/blog/ADforPostgreSQL/pg-repack/4512345</link>
    <guid>https://techcommunity.microsoft.com/blog/ADforPostgreSQL/pg-repack/4512345</guid>
    <description>&lt;p&gt;Unlike VACUUM FULL, pg_repack requires only a brief lock on
      the SQL table during the final swap.&lt;/p&gt;</description>
  </item>
  <item>
    <title>No identifier here</title>
    <link>https://techcommunity.microsoft.com/blog/x/slug-only</link>
    <description>&lt;p&gt;Body.&lt;/p&gt;</description>
  </item>
</channel></rss>
"""


def test_parse_board_feed_maps_post_id_to_body() -> None:
    bodies = _parse_board_feed(BOARD_FEED)
    assert set(bodies) == {"4512345"}
    assert "pg_repack requires only a brief lock" in bodies["4512345"]


def test_extract_constraints_drops_marketing_and_fragments() -> None:
    body = (
        "Effective enterprise AI must be capable of planning over long horizons. "
        "We can't create the future of this codec alone, so join us. "
        "265/HEVC) have served the industry well but require dedicated hardware. "
        "The scenario requires a Flexible Server with High Availability enabled on the "
        "General Purpose tier."
    )
    picked = _extract_constraints(body)

    assert any("Flexible Server with High Availability" in s for s in picked)
    assert not any("must be capable" in s for s in picked)
    assert not any("create the future" in s for s in picked)
    # A fragment starting mid-sentence (abbreviation period) is not a sentence.
    assert not any(s.startswith("265/") for s in picked)


def test_extract_constraints_ranks_concrete_first() -> None:
    body = (
        "This approach generally requires some additional thought from the team "
        "before it can be adopted more widely across the wider organisation. "
        "The Premium SKU requires TLS 1.2 and at least 4 vCPU per node."
    )
    assert "Premium SKU" in _extract_constraints(body)[0]


@pytest.mark.asyncio
async def test_fetch_post_body_blocks_non_whitelisted_hosts() -> None:
    """A look-alike host must not be fetched (subdomain-suffix spoofing)."""
    svc = CommunityInsightService()
    for url in (
        "https://techcommunity.microsoft.com.evil.com/blog/b/s/123",
        "https://evil.example.com/blog/b/s/123",
        "https://azureweekly.info/some/post/123",  # allowed host, wrong shape
    ):
        assert await svc._fetch_post_body(url) == ""


@pytest.mark.asyncio
async def test_find_related_without_body_makes_no_requests(tmp_path: Path) -> None:
    """with_body=0 must not touch the network."""
    svc = _service_with_entries(tmp_path, [_entry("Azure Kubernetes Service news")])

    async def _fail(board_id: str) -> dict[str, str]:
        raise AssertionError("board feed must not be fetched when with_body=0")

    svc._fetch_board = _fail  # type: ignore[assignment]
    hits = await svc.find_related(["Azure Kubernetes Service"], "", auto_refresh=False, with_body=0)
    assert hits and "highlights" not in hits[0]


@pytest.mark.asyncio
async def test_find_related_attaches_highlights(tmp_path: Path) -> None:
    entry = _entry("Azure Kubernetes Service upgrade notes")
    entry["url"] = "https://techcommunity.microsoft.com/blog/AKSBlog/upgrade/999111"
    svc = _service_with_entries(tmp_path, [entry])

    async def _feed(board_id: str) -> dict[str, str]:
        assert board_id == "AKSBlog"
        return {"999111": "The Standard SKU requires TLS 1.2 and at least 3 nodes per pool."}

    svc._fetch_board = _feed  # type: ignore[assignment]
    hits = await svc.find_related(["Azure Kubernetes Service"], "", auto_refresh=False, with_body=1)
    assert "Standard SKU requires TLS 1.2" in hits[0]["highlights"][0]


@pytest.mark.asyncio
async def test_body_failure_degrades_to_blurb(tmp_path: Path) -> None:
    """An unreachable board feed must not break the match."""
    entry = _entry("Azure Kubernetes Service upgrade notes")
    entry["url"] = "https://techcommunity.microsoft.com/blog/AKSBlog/upgrade/999111"
    svc = _service_with_entries(tmp_path, [entry])

    async def _empty(board_id: str) -> dict[str, str]:
        return {}

    svc._fetch_board = _empty  # type: ignore[assignment]
    hits = await svc.find_related(["Azure Kubernetes Service"], "", auto_refresh=False, with_body=1)
    assert hits and "highlights" not in hits[0]
    assert hits[0]["summary"] == "summary"
