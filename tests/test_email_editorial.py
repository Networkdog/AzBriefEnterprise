"""Behavior contracts for the editorial email design, independent of old card styling."""

import re
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote

import pytest
from bs4 import BeautifulSoup

from scripts.preview_email import build_demo_items, render_previews
from src.agent.resource_evidence import ResourceQueryEvidence, resource_identity
from src.agent.scope import AnalysisScope
from src.email.service import EmailService
from src.email.templates import (
    _LEVEL_COLORS,
    _VERIFY_COLOR,
    EMAIL_COLORS,
    FONT_SIZE_PX,
    FONT_STACK_DISPLAY,
    FONT_STACK_SANS,
    SEMANTIC_ACCENT_WIDTH_PX,
    format_affected_resources_html,
    format_affected_resources_text,
    format_digest_intro_html,
    format_digest_update_card_html,
    format_email_section_html,
    get_labels,
)


@pytest.fixture
def report_markup():
    """Return the actual renderer output without consulting tenant planning history."""
    with patch("src.agent.history.get_retirement_countdown", return_value=[]):
        service = EmailService()

        def render(kind: str, language: str) -> str:
            items = build_demo_items(language)
            if kind == "single":
                return service.build_email_content(
                    items[0]["update"],
                    items[0]["result"],
                    language,
                    archive_url=items[0]["archive_url"],
                )["html_content"]
            return service.build_digest_content(items, "2026-09-07 ~ 2026-09-13", language)[
                "html_content"
            ]

        yield render


@pytest.mark.parametrize("kind", ["single", "digest"])
def test_report_uses_sans_display_without_embedded_reference_fonts(report_markup, kind):
    markup = report_markup(kind, "ko")
    soup = BeautifulSoup(markup, "html.parser")
    assert FONT_STACK_DISPLAY == FONT_STACK_SANS
    assert f"font-family: {FONT_STACK_SANS};" in soup.body["style"]
    assert f"font-family: {FONT_STACK_SANS};" in soup.select_one(".azb-wordmark")["style"]
    assert "@font-face" not in markup
    assert "Georgia" not in markup
    assert "Times New Roman" not in markup
    assert "VerizonNHG" not in markup
    assert "font-weight: 700" in soup.select_one(".azb-wordmark")["style"]
    for number in soup.select(".azb-chapter-number, .azb-action-number, .azb-toc-number"):
        assert "font-weight: 700" in number["style"]
        assert "font-variant-numeric: tabular-nums" in number["style"]
    for title in soup.select(".azb-action-title"):
        assert "font-size: 17px" in title["style"]
        assert "font-weight: 700" in title["style"]


@pytest.mark.parametrize("kind", ["single", "digest"])
@pytest.mark.parametrize("language", ["ko", "en", "ja"])
def test_new_document_hierarchy_is_shared_and_complete(report_markup, kind, language):
    markup = report_markup(kind, language)
    soup = BeautifulSoup(markup, "html.parser")
    assert len(soup.find_all("h1")) == 1
    assert len(soup.select(".azb-paper")) == 1
    assert len(soup.select(".azb-masthead")) == 1
    assert len(soup.select(".azb-footer")) == 1
    assert soup.select_one(".azb-preheader").get_text(strip=True)
    assert soup.select(".azb-takeaway .azb-summary")
    assert soup.select(".azb-assessment")
    for key in (
        "col_importance",
        "col_impact",
        "col_job_relevance",
        "relevance_evidence",
        "affected_resources",
    ):
        assert get_labels(language)[key] in soup.get_text()
    if language == "ko":
        assert get_labels(language)["affected_resources"] == "연관 리소스"
        assert get_labels(language)["no_affected_resources"] == "연관 리소스가 없습니다."
    assert "#0f1b2d" not in markup
    assert "#1e3a5f" not in markup
    assert "box-shadow" not in markup
    assert "border-radius: 8px" not in markup
    assert soup.find("script") is None
    assert soup.find("link") is None
    assert not re.search(r"\{[a-z_]+_html\}", markup)
    for row in soup.find_all("tr"):
        assert row.parent.name in {"table", "thead", "tbody", "tfoot"}
    for cell in soup.find_all(["td", "th"]):
        assert cell.parent.name == "tr"


@pytest.mark.parametrize("language", ["ko", "en", "ja"])
def test_digest_has_working_contents_and_separates_skipped_counts(report_markup, language):
    soup = BeautifulSoup(report_markup("digest", language), "html.parser")
    assert len(soup.select(".azb-digest-row")) == 3
    assert len(soup.select(".azb-digest-skip")) == 1
    assert len(soup.select(".azb-digest-detail")) == 3
    assert [p.get_text() for p in soup.select(".azb-digest-counts .azb-count-value")] == [
        "1",
        "1",
        "1",
    ]
    for index in (1, 2, 3):
        assert soup.find(id=f"azbrief-detail-{index}")
        assert soup.select_one(f'.azb-digest-title a[href="#azbrief-detail-{index}"]')
    assert soup.find(id="azbrief-summary")
    assert len(soup.select('a[href="#azbrief-summary"]')) == 3
    assert get_labels(language)["digest_skipped"].format(count=1) in soup.get_text()
    assert get_labels(language)["digest_analyzed"].format(count=3) in soup.get_text()


@pytest.mark.parametrize("language", ["ko", "en", "ja"])
def test_digest_chapter_openers_are_numbered_and_link_back_to_contents(report_markup, language):
    soup = BeautifulSoup(report_markup("digest", language), "html.parser")
    assert [number.get_text() for number in soup.select(".azb-chapter-number")] == [
        "01",
        "02",
        "03",
    ]
    for chapter in soup.select(".azb-chapter"):
        assert (
            chapter.select_one('a[href="#azbrief-summary"]').get_text()
            == get_labels(language)["email_back_to_contents"]
        )
        assert "font-size: 32px" in chapter.select_one(".azb-chapter-number")["style"]
        assert FONT_STACK_DISPLAY in chapter.select_one(".azb-chapter-number")["style"]
    for band in soup.select(".azb-chapter-band"):
        assert f'background-color: {EMAIL_COLORS["paper"]}' in band["style"]
    single = BeautifulSoup(report_markup("single", language), "html.parser")
    assert single.select_one(".azb-chapter") is None
    assert "letter-spacing: 0" in single.find("h1")["style"]


@pytest.mark.parametrize("kind", ["single", "digest"])
def test_operational_facts_are_a_white_ruled_ledger(report_markup, kind):
    soup = BeautifulSoup(report_markup(kind, "ko"), "html.parser")
    for style in soup.find_all("style"):
        style.decompose()
    facts = soup.select_one(".azb-qd")
    assert f'background-color: {EMAIL_COLORS["paper"]}' in facts["style"]
    assert "border-top" not in facts["style"]
    assert "2026-12-31 (sample)" in facts.get_text()
    for cell in facts.select(".azb-fact-cell"):
        assert "padding: 12px 16px 12px 0" in cell["style"]
        assert "border-bottom: 1px" in cell["style"]


@pytest.mark.parametrize("kind", ["single", "digest"])
def test_concept_boxes_are_shaded_without_coloring_action_surfaces(report_markup, kind):
    soup = BeautifulSoup(report_markup(kind, "ko"), "html.parser")
    for selector in (".azb-concept", ".azb-checks", ".azb-action", ".azb-action-schedule"):
        blocks = soup.select(selector)
        assert blocks, selector
        background = EMAIL_COLORS["wash"] if selector == ".azb-concept" else EMAIL_COLORS["paper"]
        for block in blocks:
            assert f"background-color: {background}" in block["style"]
    for action in soup.select(".azb-action"):
        assert "border: 1px" not in action["style"]
        assert "border-top: 1px" in action["style"]
        assert action.select_one(".azb-verify")
        assert action.select_one(".azb-action-title")


@pytest.mark.parametrize("counts", [(2, 1, 1), (0, 3, 1), (4, 0, 0), (0, 0, 0), (123, 9, 1)])
def test_digest_distribution_represents_analyzed_counts_only(counts):
    high, medium, low = counts
    analyzed = sum(counts)
    markup = format_digest_intro_html(analyzed + 5, high, medium, low, 5, "en")
    soup = BeautifulSoup(markup, "html.parser")
    chart = soup.select_one(".azb-digest-distribution")
    assert [p.get_text() for p in soup.select(".azb-digest-counts .azb-count-value")] == [
        str(count) for count in counts
    ]
    assert chart.get("aria-hidden") is None
    assert chart.get("role") != "presentation"
    assert get_labels("en")["digest_analyzed"].format(count=analyzed) in chart.caption.get_text()
    assert len(chart.select('th[scope="row"]')) == 3
    assert len(chart.select(".azb-count-track")) == 3
    for track in chart.select(".azb-count-track"):
        assert track.parent["aria-hidden"] == "true"
        assert sum(
            float(cell["width"].rstrip("%")) for cell in track.select("td")
        ) == pytest.approx(100)
    for level, count in zip(("high", "medium", "low"), counts):
        segment = chart.select_one(f".azb-distribution-{level}")
        if count:
            assert float(segment["width"].rstrip("%")) == pytest.approx(count / analyzed * 100)
            assert segment["bgcolor"] == _LEVEL_COLORS[level]["color"]
        else:
            assert segment is None


@pytest.mark.parametrize("kind", ["single", "digest"])
@pytest.mark.parametrize("language", ["ko", "en", "ja"])
def test_semantic_status_uses_shaded_cells_including_inline_layout(report_markup, kind, language):
    soup = BeautifulSoup(report_markup(kind, language), "html.parser")
    for style in soup.find_all("style"):
        style.decompose()
    assert SEMANTIC_ACCENT_WIDTH_PX == 2
    for selector in (
        ".azb-verify",
        ".azb-concept",
        ".azb-checks",
    ):
        elements = soup.select(selector)
        assert elements, selector
        for element in elements:
            assert re.search(r"border-left:\s*2px solid #[0-9a-f]{6}", element["style"])
            assert element.get_text(strip=True)
    for badge in soup.select(".azb-verify"):
        assert re.search(r"padding:\s*4px (4|8)px", badge["style"])
    assert FONT_SIZE_PX["badge_text"] == 12
    for badge in soup.select("[class^='azb-badge-']"):
        assert f'font-size:{FONT_SIZE_PX["badge_text"]}px' in badge["style"]
        assert "line-height:18px" in badge["style"]
        assert "text-align:center" in badge["style"]
        assert "background-color" not in badge["style"]
        assert "border" not in badge["style"]
        assert "padding" not in badge["style"]
        assert badge.find(attrs={"aria-hidden": "true"}) is None
        level = badge["class"][0].removeprefix("azb-badge-")
        cell = badge.find_parent("td", class_="azb-level-cell")
        assert cell is not None
        assert cell["bgcolor"] == _LEVEL_COLORS[level]["bg"]
        assert f'background-color: {_LEVEL_COLORS[level]["bg"]}' in cell["style"]
        assert cell["bgcolor"] != EMAIL_COLORS["paper"]
        assert "border" not in cell["style"]
        metric = badge.find_parent("table", class_="azb-metric")
        assert metric is not None
        assert metric["width"] == "33%"
        assert metric["align"] == "left"
        assert "table-layout: auto" in metric["style"]


def test_digest_fallback_gives_titles_full_width_and_labels_each_metric(report_markup):
    soup = BeautifulSoup(report_markup("digest", "en"), "html.parser")
    headers = soup.select(".azb-digest-heading > th")
    assert [header["width"] for header in headers] == ["52%", "16%", "16%", "16%"]
    for style in soup.find_all("style"):
        style.decompose()
    assert "display:none" in soup.select_one(".azb-digest-heading")["style"]
    for row in soup.select(".azb-digest-row"):
        assert row.select_one(".azb-digest-entry")["colspan"] == "4"
        assert row.select_one(".azb-digest-copy")["width"] == "100%"
        assert row.select_one(".azb-digest-metrics")["width"] == "100%"
        for label in row.select(".azb-metric-label"):
            assert "display:block" in label["style"]
            assert "mso-hide" not in label["style"]


@pytest.mark.parametrize("kind", ["single", "digest"])
def test_report_typography_uses_larger_sizes_without_scaling_layout_reset(report_markup, kind):
    markup = report_markup(kind, "ko")
    soup = BeautifulSoup(markup, "html.parser")
    assert f'font-size: {FONT_SIZE_PX["body"]}px' in soup.body["style"]
    title_size = FONT_SIZE_PX["cover"]
    assert f"font-size: {title_size}px" in soup.find("h1")["style"]
    assert "word-break: keep-all" in soup.find("h1")["style"]
    assert "overflow-wrap: anywhere" in soup.find("h1")["style"]
    assert FONT_SIZE_PX["section"] == 18
    assert FONT_SIZE_PX["section_mobile"] == 16
    assert FONT_SIZE_PX["section_heading"] == 24
    assert FONT_SIZE_PX["section_heading_mobile"] == 20
    for heading in soup.select("h2.azb-heading"):
        assert f'font-size: {FONT_SIZE_PX["section_heading"]}px' in heading["style"]
        assert "font-weight: 700" in heading["style"]
        assert f'color: {EMAIL_COLORS["editorial"]}' in heading["style"]
    for summary in soup.select(".azb-summary"):
        assert f'font-size: {FONT_SIZE_PX["section"]}px' in summary["style"]
        assert "font-weight: 400" in summary["style"]
    assert ".azb-hero-title { font-size: 28px !important; }" in markup
    assert ".azb-heading { font-size: 20px !important; }" in markup
    assert ".azb-summary { font-size: 16px !important; }" in markup
    assert "font-size: 0 !important" in markup
    assert "border-left" not in soup.select_one(".azb-takeaway")["style"]
    assert "background-color" not in soup.select_one(".azb-takeaway")["style"]
    assert "#08746b" not in markup
    assert "#182b32" not in markup
    source_links = soup.select_one(".azb-source-links")
    assert source_links is not None
    assert len(source_links.select("a")) == 2
    assert "display: inline-block" in source_links.find("p")["style"]


@pytest.mark.parametrize("kind", ["single", "digest"])
def test_brief_summary_and_assessment_stack_without_media_queries(report_markup, kind):
    soup = BeautifulSoup(report_markup(kind, "en"), "html.parser")
    for style in soup.find_all("style"):
        style.decompose()
    for brief in soup.select(".azb-brief"):
        summary = brief.select_one(".azb-brief-copy")
        assessment = brief.select_one(".azb-brief-assessment")
        assert summary["width"] == assessment["width"] == "100%"
        assert summary.select_one(".azb-takeaway")
        assert summary.select_one(".azb-source-links")
        assert len(assessment.select(".azb-metric")) == 3
        assert summary.find_next("table", class_="azb-brief-assessment") == assessment


@pytest.mark.parametrize("kind", ["single", "digest"])
def test_sections_follow_a_full_width_reading_order_including_fallback(report_markup, kind):
    soup = BeautifulSoup(report_markup(kind, "en"), "html.parser")
    for style in soup.find_all("style"):
        style.decompose()
    assert soup.select(".azb-section-head h2")
    assert not soup.select(".azb-section-label, .azb-heading-rest")
    for table in soup.select(".azb-section-frame, .azb-section-wide"):
        assert table["width"] == "100%"
        rows = table.find_all("tr", recursive=False)
        assert rows[0].select_one(".azb-section-head h2")
        assert rows[1].select_one(".azb-section-copy")


@pytest.mark.parametrize(
    "label",
    ["요약 판정", "연관 리소스", "Affected Resources", "References", "<em>Safe & text</em>"],
)
@pytest.mark.parametrize("full_width", [False, True])
def test_section_heading_preserves_complete_text_including_inline_fallback(label, full_width):
    markup = format_email_section_html(label, "<p>Content</p>", full_width=full_width)
    heading = BeautifulSoup(markup, "html.parser").select_one("h2.azb-heading")
    assert heading.get_text() == label
    assert heading.find("em") is None
    assert heading.select_one(".azb-heading-rest") is None
    assert heading.find("br") is None


@pytest.mark.parametrize("kind", ["single", "digest"])
def test_resource_heading_keeps_its_count_separate_from_title_text(report_markup, kind):
    soup = BeautifulSoup(report_markup(kind, "ko"), "html.parser")
    counts = soup.select(".azb-heading-count")
    assert counts
    for count in counts:
        assert count.parent["class"] == ["azb-heading"]
        assert count.parent.contents[0] == "연관 리소스"
        assert count.get_text().startswith(" · ")
        assert "font-size:11px" in count["style"]
        assert "white-space:nowrap" in count["style"]


@pytest.mark.parametrize("count", [20, 21, 327])
def test_resource_summary_starts_after_twenty_rows(count):
    resources = [
        {"name": f"legacy-{index:04d}", "reason": "Shared applicability"} for index in range(count)
    ]
    soup = BeautifulSoup(
        format_affected_resources_html(resources, "en", "retirement"), "html.parser"
    )
    assert len(soup.select(".azb-resource-row")) == (count if count <= 20 else 0)
    assert bool(soup.select_one(".azb-resource-summary")) == (count > 20)


def _render_resource_report(item: dict, kind: str, language: str) -> dict:
    settings = SimpleNamespace(
        use_email=False,
        communication_services_connection_string=None,
        communication_services_endpoint=None,
        email_sender_address=None,
        feedback_ui_enabled=False,
        feedback_base_url="",
    )
    with (
        patch("src.email.service.get_settings", return_value=settings),
        patch("src.agent.history.get_retirement_countdown", return_value=[]),
    ):
        service = EmailService()
        if kind == "single":
            return service.build_email_content(
                item["update"], item["result"], language, archive_url=item.get("archive_url", "")
            )
        return service.build_digest_content([item], "2026-09-08", language)


@pytest.mark.parametrize("count", [20, 21, 327])
@pytest.mark.parametrize("language", ["ko", "en", "ja"])
@pytest.mark.parametrize("kind", ["single", "digest"])
def test_verified_resource_summary_is_shared_by_html_and_plain_text(count, language, kind):
    item = build_demo_items(language, resource_count=count)[0]
    result = item["result"]
    before = deepcopy(result.affected_resources)
    content = _render_resource_report(item, kind, language)
    soup = BeautifulSoup(content["html_content"], "html.parser")
    plain = content["plain_content"]
    labels = get_labels(language)
    assert result.affected_resources == before
    assert len(soup.select(".azb-resource-row")) == (count if count <= 20 else 0)
    if count <= 20:
        for resource in result.affected_resources:
            assert resource["name"] in soup.get_text()
            assert resource["name"] in plain
        return
    summary = soup.select_one(".azb-resource-summary")
    assert len(summary.select(".azb-resource-summary-group")) == 2
    assert labels["resource_summary_total"].format(count=count) in soup.get_text()
    assert labels["resource_summary_total"].format(count=count) in plain
    assert labels["resource_query_caveat"] in soup.get_text()
    assert labels["resource_query_caveat"] in plain
    assert labels["resource_snapshot_open"] in soup.get_text()
    assert labels["resource_snapshot_open"] in plain
    assert result.affected_resources[-1]["name"] not in content["html_content"]
    assert result.affected_resources[-1]["name"] not in plain
    for group, evidence in zip(
        summary.select(".azb-resource-summary-group"), result.resource_queries
    ):
        assert evidence.reason in group.get_text()
        assert evidence.reason in plain
        assert labels["resource_group_count"].format(count=evidence.count) in group.get_text()
        assert "2026-09-08 09:00:00+00:00" in group.get_text()
        assert "2026-09-08 09:00:00+00:00" in plain
        assert evidence.reference not in content["html_content"]
        assert evidence.reference not in plain
        assert group.find("a")["href"] == evidence.portal_url()
        assert evidence.portal_url() in plain
        decoded = unquote(group.find("a")["href"].split("/query/", 1)[1])
        assert decoded == evidence.portal_query
        assert "where properties.minimumTlsVersion =~ 'TLS1_" in decoded
        assert evidence.scope.subscriptions[0] in decoded


@pytest.mark.parametrize("kind", ["single", "digest"])
def test_summary_deduplicates_arm_ids_without_merging_names_across_subscriptions(kind):
    item = build_demo_items("en", resource_count=21)[0]
    resources = item["result"].affected_resources
    first, second = item["result"].resource_queries
    other_subscription = "00000000-0000-0000-0000-000000000002"
    resources[-1] = {
        **resources[-1],
        "name": resources[0]["name"],
        "id": f"/subscriptions/{other_subscription}/resourceGroups/rg-platform-production/providers/Microsoft.Storage/storageAccounts/{resources[0]['name']}",
        "subscriptionId": other_subscription,
    }
    for index, resource in enumerate(resources):
        resource["query_refs"] = ([first.reference] if index < 20 else []) + (
            [second.reference] if index >= 19 else []
        )
    resources.append({**resources[0], "id": resources[0]["id"].upper()})
    queries = [
        first.model_copy(update={"count": 20}),
        second.model_copy(
            update={
                "count": 2,
                "scope": AnalysisScope(
                    subscriptions=(*first.scope.subscriptions, other_subscription)
                ),
                "portal_query": (
                    "Resources | where subscriptionId in~ "
                    f"('{first.scope.subscriptions[0]}', '{other_subscription}') "
                    "| where properties.minimumTlsVersion =~ 'TLS1_1' | project id"
                ),
            }
        ),
    ]
    item["result"] = item["result"].model_copy(update={"resource_queries": queries})
    content = _render_resource_report(item, kind, "en")
    soup = BeautifulSoup(content["html_content"], "html.parser")
    for text in (soup.get_text(), content["plain_content"]):
        assert "Total: 21 unique resources" in text
        assert get_labels("en")["resource_summary_overlap"] in text
    groups = soup.select(".azb-resource-summary-group")
    assert "20 resources" in groups[0].get_text()
    assert "2 resources" in groups[1].get_text()
    assert all(group.find("a") for group in groups)


@pytest.mark.parametrize("count", [0, 21, 327])
@pytest.mark.parametrize("language", ["ko", "en", "ja"])
@pytest.mark.parametrize("kind", ["single", "digest"])
def test_partial_evidence_never_reports_an_exact_total_or_confirmed_absence(count, language, kind):
    item = build_demo_items(language, resource_count=count)[0]
    queries = [
        evidence.model_copy(update={"complete": False})
        for evidence in item["result"].resource_queries
    ]
    item["result"] = item["result"].model_copy(update={"resource_queries": queries})
    content = _render_resource_report(item, kind, language)
    soup = BeautifulSoup(content["html_content"], "html.parser")
    labels = get_labels(language)
    assert not soup.select('a[href*="ArgQueryBlade"]')
    for text in (soup.get_text(), content["plain_content"]):
        assert labels["resource_summary_partial"].format(count=count) in text
        assert labels["resource_summary_incomplete"] in text
        assert labels["no_affected_resources"] not in text
        assert labels["resource_snapshot_open"] in text


@pytest.mark.parametrize("language", ["ko", "en", "ja"])
@pytest.mark.parametrize("kind", ["single", "digest"])
def test_legacy_many_unique_reasons_are_bounded_and_do_not_invent_query_links(language, kind):
    item = build_demo_items(language, resource_count=327)[0]
    resources = [
        {"name": "ambiguous-name", "reason": f"Reason {index:04d}"} for index in range(327)
    ]
    item["result"] = item["result"].model_copy(
        update={"affected_resources": resources, "resource_queries": []}
    )
    item["archive_url"] = ""
    content = _render_resource_report(item, kind, language)
    soup = BeautifulSoup(content["html_content"], "html.parser")
    labels = get_labels(language)
    assert len(soup.select(".azb-resource-summary-group")) == 10
    assert not soup.select('a[href*="ArgQueryBlade"]')
    for text in (soup.get_text(), content["plain_content"]):
        assert labels["resource_summary_records"].format(records=327) in text
        assert labels["resource_summary_remaining"].format(count=317) in text
        assert labels["resource_snapshot_unavailable"] in text
        assert "Reason 0009" in text
        assert "Reason 0010" not in text
        assert "Reason 0326" not in text
        assert "ambiguous-name" not in text


@pytest.mark.parametrize(
    "mode", ["legacy", "count", "membership", "duplicate_ref", "scope", "id_scope"]
)
@pytest.mark.parametrize("kind", ["single", "digest"])
def test_unverified_metadata_cannot_supply_exact_counts_or_query_links(mode, kind):
    item = build_demo_items("en", resource_count=21)[0]
    result = item["result"]
    resources = result.affected_resources
    queries = result.resource_queries
    other_subscription = "00000000-0000-0000-0000-000000000002"
    if mode == "legacy":
        queries = []
        for resource in resources:
            resource.pop("query_refs")
    elif mode == "count":
        queries = [evidence.model_copy(update={"count": 9999}) for evidence in queries]
    elif mode == "membership":
        for resource in resources:
            resource["query_refs"] = ["rq-" + "f" * 32]
    elif mode == "duplicate_ref":
        queries = queries + queries
    elif mode == "scope":
        queries = [
            evidence.model_copy(
                update={"scope": AnalysisScope(subscriptions=(other_subscription,))}
            )
            for evidence in queries
        ]
    else:
        for resource in resources:
            resource["id"] = resource["id"].replace(resource["subscriptionId"], other_subscription)
    item["result"] = result.model_copy(update={"resource_queries": queries})
    content = _render_resource_report(item, kind, "en")
    soup = BeautifulSoup(content["html_content"], "html.parser")
    assert not soup.select('a[href*="ArgQueryBlade"]')
    for text in (soup.get_text(), content["plain_content"]):
        assert "Confirmed: 21 unique resources; overall total unknown" in text
        assert "9999" not in text
        assert item["archive_url"] in content["html_content"]
        assert get_labels("en")["resource_snapshot_open"] in text


@pytest.mark.parametrize(
    "mode", ["missing", "join_unavailable", "long", "management_group", "rejected"]
)
@pytest.mark.parametrize("kind", ["single", "digest"])
def test_unavailable_portal_query_falls_back_to_analysis_time_archive(mode, kind):
    item = build_demo_items("en", resource_count=21)[0]
    changes = {"portal_query": ""}
    if mode == "long":
        changes = {
            "portal_query": "Resources | where name in (" + "'synthetic'," * 1000 + "'last')"
        }
    elif mode == "management_group":
        changes = {"scope": AnalysisScope(management_groups=("synthetic-mg",))}
    elif mode == "rejected":
        changes = {}
    queries = [evidence.model_copy(update=changes) for evidence in item["result"].resource_queries]
    item["result"] = item["result"].model_copy(update={"resource_queries": queries})
    if mode == "rejected":
        with patch.object(
            ResourceQueryEvidence, "portal_url", return_value="https://evil.example/query"
        ):
            content = _render_resource_report(item, kind, "en")
    else:
        content = _render_resource_report(item, kind, "en")
    soup = BeautifulSoup(content["html_content"], "html.parser")
    assert not soup.select('a[href*="ArgQueryBlade"]')
    assert soup.select_one(".azb-resource-snapshot a")["href"] == item["archive_url"]
    for text in (soup.get_text(), content["plain_content"]):
        assert "Total: 21 unique resources" in text
        assert get_labels("en")["resource_query_unavailable"] in text
        assert get_labels("en")["resource_snapshot_open"] in text
        assert "evil.example" not in text


@pytest.mark.parametrize(
    "archive_url",
    [
        "",
        "javascript:alert(1)",
        "https://user:secret@azbrief.example/archive",
        "https://azbrief.example:bad/archive",
    ],
)
def test_rejected_archive_links_have_an_explicit_unavailable_fallback(archive_url):
    item = build_demo_items("en", resource_count=21)[0]
    queries = [
        evidence.model_copy(update={"portal_query": ""})
        for evidence in item["result"].resource_queries
    ]
    markup = format_affected_resources_html(
        item["result"].affected_resources,
        "en",
        "retirement",
        resource_queries=queries,
        archive_url=archive_url,
    )
    plain = format_affected_resources_text(
        item["result"].affected_resources,
        "en",
        "retirement",
        resource_queries=queries,
        archive_url=archive_url,
    )
    soup = BeautifulSoup(markup, "html.parser")
    assert not soup.find("a")
    for text in (soup.get_text(), plain):
        assert get_labels("en")["resource_snapshot_unavailable"] in text
        assert "secret" not in text


@pytest.mark.parametrize("language", ["ko", "en", "ja"])
@pytest.mark.parametrize("kind", ["single", "digest"])
def test_resource_summary_escapes_untrusted_reasons_and_preserves_encoded_query(language, kind):
    item = build_demo_items(language, resource_count=21)[0]
    reason = '<img src=x onerror="alert(1)"> & <script>alert(2)</script>'
    queries = [
        evidence.model_copy(
            update={
                "reason": reason,
                "portal_query": evidence.portal_query + " | where strlen(name) > 3",
            }
        )
        for evidence in item["result"].resource_queries
    ]
    item["result"] = item["result"].model_copy(update={"resource_queries": queries})
    content = _render_resource_report(item, kind, language)
    soup = BeautifulSoup(content["html_content"], "html.parser")
    summary = soup.select_one(".azb-resource-summary")
    assert not summary.select("img, script, [onerror]")
    assert reason in summary.get_text()
    assert reason in content["plain_content"]
    assert "&lt;img" in content["html_content"]
    for link, evidence in zip(summary.find_all("a"), queries):
        assert unquote(link["href"].split("/query/", 1)[1]) == evidence.portal_query
        assert "%3E%203" in link["href"]


@pytest.mark.parametrize("count", [0, 1, 20, 21, 327])
def test_preview_resource_option_generates_complete_fixed_synthetic_identity_evidence(count):
    item = build_demo_items("en", resource_count=count)[0]
    resources = item["result"].affected_resources
    queries = item["result"].resource_queries
    assert len(resources) == count
    assert len({resource_identity(resource) for resource in resources}) == count
    for evidence in queries:
        members = [
            resource for resource in resources if evidence.reference in resource["query_refs"]
        ]
        assert isinstance(evidence, ResourceQueryEvidence)
        assert evidence.count == len(members)
        assert evidence.complete
        assert evidence.scope.subscriptions == ("00000000-0000-0000-0000-000000000001",)
        assert evidence.portal_url()
    assert len(build_demo_items("en")[0]["result"].affected_resources) == 2


def test_resource_preview_option_preserves_all_twelve_offline_outputs(tmp_path):
    with patch("src.email.service.get_email_client_class") as transport:
        files = render_previews(tmp_path, ["ko", "en", "ja"], resource_count=327)
    transport.assert_not_called()
    assert len(files) == 12
    for path in files:
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        assert not soup.select(".azb-resource-row")
        assert len(soup.select(".azb-resource-summary-group")) == 2
        assert "327" in soup.select_one(".azb-resource-total").get_text()
        assert "padding-right: 8px" in soup.select_one(".azb-resource-total")["style"]
        if "inline-only" in path.name:
            assert not soup.find("style")


def test_digest_uses_a_sans_masthead_and_directly_labeled_statistic_rows(report_markup):
    soup = BeautifulSoup(report_markup("digest", "en"), "html.parser")
    for style in soup.find_all("style"):
        style.decompose()
    assert "font-size: 28px" in soup.select_one(".azb-wordmark")["style"]
    assert FONT_STACK_DISPLAY in soup.select_one(".azb-wordmark")["style"]
    assert soup.select_one(".azb-masthead-brand")["width"] == "100%"
    assert soup.select_one(".azb-masthead-edition")["width"] == "100%"
    for row, level in zip(soup.select(".azb-count-row"), ("high", "medium", "low")):
        label = row.find("th", scope="row")
        assert f'color: {_LEVEL_COLORS[level]["color"]}' in label["style"]
        assert get_labels("en")["importance_" + level] == label.get_text()
        assert "font-size: 28px" in row.select_one(".azb-count-value")["style"]
        assert "font-weight: 700" in row.select_one(".azb-count-value")["style"]


def test_large_digest_counts_remain_complete_in_narrow_cells():
    soup = BeautifulSoup(format_digest_intro_html(220, 120, 90, 10, 0), "html.parser")
    values = soup.select(".azb-count-value")
    assert [value.get_text() for value in values] == ["120", "90", "10"]
    assert all("font-size: 24px" in value["style"] for value in values)


def test_digest_does_not_truncate_a_long_title():
    item = build_demo_items("en")[0]
    item["update"].title = "Storage " + "A long but meaningful operational title " * 8
    markup = format_digest_update_card_html(item["update"], item["result"], anchor_index=1)
    soup = BeautifulSoup(markup, "html.parser")
    assert item["update"].title in soup.get_text()
    metrics = soup.select(".azb-col-metric")
    assert len(metrics) == 3
    assert all(cell.select_one(".azb-metric-label") for cell in metrics)


def test_resource_mobile_labels_retain_full_identity_and_reason(report_markup):
    markup = report_markup("single", "ko")
    soup = BeautifulSoup(markup, "html.parser")
    assert ".azb-resources, .azb-resources > tbody" in markup
    assert ".azb-digest-table, .azb-digest-table > tbody" in markup
    assert ".azb-qd, .azb-qd > tbody, .azb-qd tr" in markup
    resources = soup.select(".azb-resource-row")
    assert len(resources) == 2
    for row in resources:
        assert len(row.find_all("td", recursive=False)) == 4
        assert len(row.select(".azb-resource-field-label")) == 4
        assert "rg-platform-production" in row.get_text()
        assert "Production sample" in row.get_text()
    assert len(soup.select(".azb-resource-reason")) == 1
    assert len(resources[0].find_all("a")) == 3


def test_procedure_command_verification_and_reference_survive_redesign(report_markup):
    soup = BeautifulSoup(report_markup("single", "ko"), "html.parser")
    action = soup.select_one(".azb-action")
    assert action.select_one("h3.azb-action-title")
    assert action.select_one(".azb-verify").get_text() == get_labels("ko")["verify_caution"]
    assert "minimumTlsVersion:minimumTlsVersion" in action.select_one(".azb-cli").get_text()
    assert action.select_one('.azb-cli a[href*="#cloudshell"]')
    assert action.select_one(".azb-action-guardrails")
    assert soup.select_one(".azb-concept a")
    assert soup.select_one(".azb-references a")
    assert "2026-12-31 (sample)" in soup.select_one(".azb-timeline").get_text()


@pytest.mark.parametrize("language", ["ko", "en", "ja"])
def test_countdown_uses_localized_status_and_a_two_column_list(language):
    records = [
        {
            "days_remaining": days,
            "title": "A complete retirement title " * 4,
            "affected_resource_count": 2,
            "migration_status": status,
        }
        for days, status in ((-3, "not_started"), (30, "in_progress"), (90, "completed"))
    ]
    with patch("src.agent.history.get_retirement_countdown", return_value=records):
        markup = EmailService()._build_retirement_countdown_html(language)
    soup = BeautifulSoup(markup, "html.parser")
    rows = soup.select(".azb-countdown tr")
    assert len(rows) == 3
    assert all(len(row.find_all("td", recursive=False)) == 2 for row in rows)
    assert "D+3" in soup.get_text() and "D-30" in soup.get_text()
    for status in ("not_started", "in_progress", "completed"):
        assert get_labels(language)["migration_" + status] in soup.get_text()
    assert records[0]["title"].strip() in soup.get_text()
    assert not any(icon in markup for icon in ("⏰", "⬜", "🟨", "✅"))


def test_empty_digest_has_an_explicit_empty_state_and_zero_counts():
    with patch("src.agent.history.get_retirement_countdown", return_value=[]):
        markup = EmailService().build_digest_content([], language="en")["html_content"]
    soup = BeautifulSoup(markup, "html.parser")
    assert get_labels("en")["digest_no_updates"] in soup.get_text()
    assert [p.get_text() for p in soup.select(".azb-digest-counts td > p:first-child")] == [
        "0",
        "0",
        "0",
    ]
    assert soup.select_one(".azb-footer")
    assert not soup.select(".azb-digest-row, .azb-digest-detail")


def _luminance(color: str) -> float:
    rgb = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in rgb
    ]
    return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def test_email_text_palette_meets_normal_text_contrast():
    assert EMAIL_COLORS["canvas"] == "#ffffff"
    pairs = [
        (EMAIL_COLORS[role], EMAIL_COLORS[surface])
        for role in ("ink", "body", "muted", "accent", "editorial", "danger", "warning", "success")
        for surface in ("paper", "wash", "accent_wash")
    ]
    pairs.extend((scheme["color"], scheme["bg"]) for scheme in _LEVEL_COLORS.values())
    pairs.extend((EMAIL_COLORS["muted"], scheme["bg"]) for scheme in _LEVEL_COLORS.values())
    pairs.extend((color, EMAIL_COLORS["paper"]) for color in _VERIFY_COLOR.values())
    pairs.append((EMAIL_COLORS["paper"], EMAIL_COLORS["ink"]))
    pairs.append((EMAIL_COLORS["on_accent"], EMAIL_COLORS["accent"]))
    for foreground, background in pairs:
        light, dark = sorted((_luminance(foreground), _luminance(background)), reverse=True)
        assert (light + 0.05) / (dark + 0.05) >= 4.5, (foreground, background)


def test_offline_preview_produces_style_stripped_fallbacks(tmp_path):
    with patch("src.email.service.get_email_client_class") as transport:
        files = render_previews(tmp_path, ["ko", "en", "ja"])
    transport.assert_not_called()
    assert len(files) == 12
    for path in files:
        markup = path.read_text(encoding="utf-8")
        soup = BeautifulSoup(markup, "html.parser")
        assert soup.select_one(".azb-paper")["width"] == "100%"
        assert "max-width: 640px" in soup.select_one(".azb-paper")["style"]
        assert "<!--[if mso]>" in markup
        assert "@font-face" not in markup
        assert "display: flex" not in markup and "display: grid" not in markup
        if "inline-only" in path.name:
            assert soup.find("style") is None
        used = {int(px) for px in re.findall(r"font-size:\s*(\d+)px", markup)}
        assert used <= set(FONT_SIZE_PX.values())
