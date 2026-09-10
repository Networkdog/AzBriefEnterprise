"""Behavior contracts for the editorial email design, independent of old card styling."""

import re
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from scripts.preview_email import build_demo_items, render_previews
from src.email.service import EmailService
from src.email.templates import (
    _LEVEL_COLORS,
    _VERIFY_COLOR,
    EMAIL_COLORS,
    FONT_SIZE_PX,
    SEMANTIC_ACCENT_WIDTH_PX,
    format_digest_intro_html,
    format_digest_update_card_html,
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
            return service.build_digest_content(items, "2026-09-01 — 2026-09-08", language)[
                "html_content"
            ]

        yield render


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
    for key in ("col_importance", "col_impact", "col_job_relevance", "relevance_evidence"):
        assert get_labels(language)[key] in soup.get_text()
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
    assert [p.get_text() for p in soup.select(".azb-digest-counts td > p:first-child")] == [
        "01",
        "01",
        "01",
    ]
    for index in (1, 2, 3):
        assert soup.find(id=f"azbrief-detail-{index}")
        assert soup.select_one(f'.azb-digest-title a[href="#azbrief-detail-{index}"]')
    assert soup.find(id="azbrief-summary")
    assert len(soup.select('a[href="#azbrief-summary"]')) == 3
    assert get_labels(language)["digest_skipped"].format(count=1) in soup.get_text()


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
        assert "font-size: 48px" in chapter.select_one(".azb-chapter-number")["style"]
    for band in soup.select(".azb-chapter-band"):
        assert f'background-color: {EMAIL_COLORS["accent"]}' in band["style"]
    single = BeautifulSoup(report_markup("single", language), "html.parser")
    assert single.select_one(".azb-chapter") is None
    assert "letter-spacing: 0" in single.find("h1")["style"]


@pytest.mark.parametrize("kind", ["single", "digest"])
def test_operational_facts_have_a_distinct_inline_surface(report_markup, kind):
    soup = BeautifulSoup(report_markup(kind, "ko"), "html.parser")
    for style in soup.find_all("style"):
        style.decompose()
    facts = soup.select_one(".azb-qd")
    assert f'background-color: {EMAIL_COLORS["wash"]}' in facts["style"]
    assert "2026-12-31 (sample)" in facts.get_text()
    for cell in facts.select(".azb-fact-cell"):
        assert "padding: 12px 16px" in cell["style"]


@pytest.mark.parametrize("counts", [(2, 1, 1), (0, 3, 1), (4, 0, 0), (0, 0, 0)])
def test_digest_distribution_represents_analyzed_counts_only(counts):
    high, medium, low = counts
    analyzed = sum(counts)
    markup = format_digest_intro_html(analyzed + 5, high, medium, low, 5, "en")
    soup = BeautifulSoup(markup, "html.parser")
    chart = soup.select_one(".azb-digest-distribution")
    assert [p.get_text() for p in soup.select(".azb-digest-counts td > p:first-child")] == [
        f"{count:02d}" for count in counts
    ]
    if not analyzed:
        assert chart is None
        return
    assert chart["aria-hidden"] == "true"
    segments = chart.select("td")
    assert len(segments) == sum(count > 0 for count in counts)
    assert sum(float(segment["width"].rstrip("%")) for segment in segments) == pytest.approx(100)
    for level, count in zip(("high", "medium", "low"), counts):
        segment = chart.select_one(f".azb-distribution-{level}")
        if count:
            assert float(segment["width"].rstrip("%")) == pytest.approx(count / analyzed * 100)
            assert segment["bgcolor"] == _LEVEL_COLORS[level]["color"]
        else:
            assert segment is None


@pytest.mark.parametrize("kind", ["single", "digest"])
@pytest.mark.parametrize("language", ["ko", "en", "ja"])
def test_semantic_color_bars_are_prominent_including_inline_fallback(report_markup, kind, language):
    soup = BeautifulSoup(report_markup(kind, language), "html.parser")
    for style in soup.find_all("style"):
        style.decompose()
    assert SEMANTIC_ACCENT_WIDTH_PX == 4
    for selector in (
        ".azb-badge-high, .azb-badge-medium, .azb-badge-low",
        ".azb-verify",
        ".azb-takeaway",
        ".azb-concept",
        ".azb-checks",
    ):
        elements = soup.select(selector)
        assert elements, selector
        for element in elements:
            assert re.search(r"border-left:\s*4px solid #[0-9a-f]{6}", element["style"])
            assert element.get_text(strip=True)
    for badge in soup.select(".azb-verify, [class^='azb-badge-']"):
        assert re.search(r"padding:\s*4px (4|8)px", badge["style"])


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
    assert "font-size: 13px" in soup.body["style"]
    title_size = FONT_SIZE_PX["cover"]
    assert f"font-size: {title_size}px" in soup.find("h1")["style"]
    for heading in soup.select("h2.azb-heading"):
        assert "font-size: 21px" in heading["style"]
    assert "h1.azb-hero-title { font-size: 25px !important; }" in markup
    assert "font-size: 0 !important" in markup
    assert not re.search(r"font-size:\s*(?:10|14|16|20|24|28)px", markup)


@pytest.mark.parametrize("kind", ["single", "digest"])
def test_sections_have_a_label_rail_with_a_full_width_fallback(report_markup, kind):
    soup = BeautifulSoup(report_markup(kind, "en"), "html.parser")
    assert len(soup.select(".azb-section-label")) == len(soup.select(".azb-section-copy"))
    assert soup.select(".azb-section-label h2")
    for table in soup.select(".azb-section-label, .azb-section-copy"):
        assert table["width"] == "100%"
    assert ".azb-section-label { width: 24% !important; }" in report_markup(kind, "en")


def test_digest_uses_a_publication_masthead_and_separate_statistic_panels(report_markup):
    soup = BeautifulSoup(report_markup("digest", "en"), "html.parser")
    for style in soup.find_all("style"):
        style.decompose()
    assert "font-size: 36px" in soup.select_one(".azb-wordmark")["style"]
    assert soup.select_one(".azb-masthead-brand")["width"] == "100%"
    assert soup.select_one(".azb-masthead-edition")["width"] == "100%"
    for cell, level in zip(soup.select(".azb-count-cell"), ("high", "medium", "low")):
        assert f'background-color: {_LEVEL_COLORS[level]["bg"]}' in cell["style"]
        assert "font-size: 48px" in cell.select_one(".azb-count-value")["style"]
        assert get_labels("en")["importance_" + level] in cell.get_text()


def test_large_digest_counts_remain_complete_in_narrow_cells():
    soup = BeautifulSoup(format_digest_intro_html(220, 120, 90, 10, 0), "html.parser")
    values = soup.select(".azb-count-value")
    assert [value.get_text() for value in values] == ["120", "90", "10"]
    assert all("font-size: 29px" in value["style"] for value in values)


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
        "00",
        "00",
        "00",
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
    pairs = [
        (EMAIL_COLORS[role], EMAIL_COLORS[surface])
        for role in ("ink", "body", "muted", "accent", "danger", "warning", "success")
        for surface in ("paper", "wash", "accent_wash")
    ]
    pairs.extend((scheme["color"], scheme["bg"]) for scheme in _LEVEL_COLORS.values())
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
