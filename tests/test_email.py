"""Tests for email content building in src/email/service.py."""

import re

import pytest

from src.email.service import EmailService
from src.email.templates import (
    FONT_SIZE_PX,
    FONT_STACK_MONO,
    FONT_STACK_SANS,
    HTML_EMAIL_TEMPLATE,
    _split_procedure,
    format_action_items_html,
    format_affected_resources_html,
    get_labels,
    get_urgency_colors,
    markdown_to_html,
)


class TestEmailContentBuilding:
    """Test email HTML/text generation."""

    def test_build_email_content(self, sample_update, sample_analysis_result):
        """Email content is generated with subject, HTML, and plain text."""
        service = EmailService()
        content = service.build_email_content(sample_update, sample_analysis_result, language="ko")
        assert "subject" in content
        assert "html_content" in content
        assert "plain_content" in content

    def test_email_subject_format(self, sample_update, sample_analysis_result):
        """Subject line contains [AzBrief] prefix and update title."""
        service = EmailService()
        content = service.build_email_content(sample_update, sample_analysis_result, language="ko")
        assert content["subject"].startswith("[AzBrief]")
        assert "SFTP" in content["subject"] or "Blob" in content["subject"]

    def test_email_html_contains_title(self, sample_update, sample_analysis_result):
        """HTML body contains the update title."""
        service = EmailService()
        content = service.build_email_content(sample_update, sample_analysis_result, language="ko")
        assert sample_update.title[:30] in content["html_content"]

    def test_email_plain_text_contains_urgency(self, sample_update, sample_analysis_result):
        """Plain text fallback includes urgency information."""
        service = EmailService()
        content = service.build_email_content(sample_update, sample_analysis_result, language="ko")
        assert "LOW" in content["plain_content"]

    def test_email_multi_language_ko(self, sample_update, sample_analysis_result):
        """Korean language labels are used for ko."""
        service = EmailService()
        content = service.build_email_content(sample_update, sample_analysis_result, language="ko")
        assert "분석 요약" in content["html_content"] or "유형" in content["html_content"]

    def test_email_multi_language_en(self, sample_update, sample_analysis_result):
        """English language labels are used for en."""
        service = EmailService()
        content = service.build_email_content(sample_update, sample_analysis_result, language="en")
        assert "Analysis Summary" in content["html_content"] or "Type" in content["html_content"]

    def test_email_enterprise_client_rendering_hardening(
        self, sample_update, sample_analysis_result
    ):
        """HTML includes Outlook mso resets and the Windows Korean system font.

        These make the report render correctly in the enterprise Outlook/Windows
        clients that are the primary audience (see research: table-based layout,
        mso cell-spacing reset, Malgun Gothic fallback).
        """
        service = EmailService()
        html = service.build_email_content(sample_update, sample_analysis_result, language="ko")[
            "html_content"
        ]
        # Outlook (Word engine) cell-spacing reset
        assert "mso-table-lspace" in html
        assert "mso-table-rspace" in html
        # Windows Korean system font present in the fallback stack
        assert "Malgun Gothic" in html

    def test_email_uses_preinstalled_system_fonts_only(self, sample_update, sample_analysis_result):
        """Font stack covers Windows/macOS/iOS/Linux/Android with no webfont."""
        service = EmailService()
        html = service.build_email_content(sample_update, sample_analysis_result, language="ko")[
            "html_content"
        ]
        assert FONT_STACK_SANS in html
        # Email clients block downloaded fonts, and bundled (non-system) Korean
        # fonts such as AppleSDGothicNeoR00 are not installed by any OS.
        assert "@font-face" not in html
        assert "AppleSDGothicNeoR00" not in html

    @pytest.mark.parametrize("builder", ["single", "digest"])
    def test_email_layout_is_responsive(self, sample_update, sample_analysis_result, builder):
        """Card is fluid up to 640px and media queries restyle narrow viewports."""
        service = EmailService()
        if builder == "single":
            html = service.build_email_content(
                sample_update, sample_analysis_result, language="ko"
            )["html_content"]
        else:
            html = service.build_digest_content(
                [{"update": sample_update, "result": sample_analysis_result, "skip_reason": ""}],
                date_range="2026-08-01 ~ 2026-08-09",
                language="ko",
            )["html_content"]

        assert 'meta name="viewport"' in html
        assert "@media only screen and (max-width: 640px)" in html
        assert "@media only screen and (max-width: 400px)" in html
        # Desktop: the card grows instead of padding the backdrop
        assert "@media only screen and (min-width: 800px)" in html
        assert "@media only screen and (min-width: 1100px)" in html
        # Fluid card, not a hardcoded 640px table
        assert 'width="640" align="center" class="azb-card"' not in html
        assert "max-width: 640px" in html
        # Windows Outlook ignores @media — the ghost table pins it to 640px there
        assert "<!--[if mso]>" in html
        assert html.count("<![endif]-->") >= 2
        # Hooks the media queries target
        assert 'class="azb-outer"' in html
        assert "azb-pad" in html
        assert "azb-stack" in html

    @pytest.mark.parametrize("builder", ["single", "digest"])
    def test_font_sizes_follow_the_type_scale(self, sample_update, sample_analysis_result, builder):
        """Every rendered size is a step on the 16px-body scale."""
        service = EmailService()
        if builder == "single":
            html = service.build_email_content(
                sample_update, sample_analysis_result, language="ko"
            )["html_content"]
        else:
            html = service.build_digest_content(
                [{"update": sample_update, "result": sample_analysis_result, "skip_reason": ""}],
                date_range="2026-08-07 ~ 2026-08-09",
                language="ko",
            )["html_content"]

        used = {int(px) for px in re.findall(r"font-size:\s*(\d+)px", html)}
        assert used, "no font sizes rendered"
        assert not used - set(
            FONT_SIZE_PX.values()
        ), f"off-scale font sizes: {sorted(used - set(FONT_SIZE_PX.values()))}"
        # Body copy sits at the email/browser default, headings a step above it.
        assert f"font-size: {FONT_SIZE_PX['body']}px" in html
        assert f"font-size: {FONT_SIZE_PX['heading']}px" in html

    def test_type_scale_is_ordered_and_body_is_the_email_default(self):
        """The scale keeps its hierarchy and anchors body copy at 16px."""
        assert FONT_SIZE_PX["body"] == 16
        steps = [
            FONT_SIZE_PX[k] for k in ("meta", "secondary", "body", "heading", "title", "masthead")
        ]
        assert steps == sorted(steps)
        assert len(set(steps)) == len(steps)

    def test_digest_metric_columns_are_shrinkable(self, sample_update, sample_analysis_result):
        """Digest importance/impact/job-relevance cells carry the narrow-screen class."""
        service = EmailService()
        html = service.build_digest_content(
            [{"update": sample_update, "result": sample_analysis_result, "skip_reason": ""}],
            date_range="2026-08-01 ~ 2026-08-09",
            language="ko",
        )["html_content"]
        # 3 header cells + 3 body cells
        assert html.count("azb-col-metric") >= 6

    def test_font_stacks_cover_every_platform(self):
        """Each stack names a preinstalled family for every target platform."""
        for family in (
            "'Segoe UI'",  # Windows
            "-apple-system",  # macOS / iOS / iPadOS
            "'Apple SD Gothic Neo'",  # macOS / iOS / iPadOS (Korean)
            "'Malgun Gothic'",  # Windows (Korean)
            "Roboto",  # Android / Chrome OS
            "'Noto Sans CJK KR'",  # Linux / Android (Korean)
        ):
            assert family in FONT_STACK_SANS
        assert FONT_STACK_SANS.endswith("sans-serif")

        for family in (
            "Consolas",  # Windows
            "Menlo",  # macOS / iOS / iPadOS
            "'DejaVu Sans Mono'",  # Linux / Android
        ):
            assert family in FONT_STACK_MONO
        assert FONT_STACK_MONO.endswith("monospace")


class TestTemplateHelpers:
    """Test template helper functions."""

    def test_get_labels_ko(self):
        labels = get_labels("ko")
        assert labels["update_type"] == "유형"
        assert labels["analysis_summary"] == "개요"

    def test_get_labels_en(self):
        labels = get_labels("en")
        assert labels["update_type"] == "Type"

    def test_get_labels_unknown_falls_back(self):
        """Unknown language falls back to Korean."""
        labels = get_labels("xx")
        assert isinstance(labels, dict)  # Should not crash

    def test_get_urgency_colors(self):
        colors = get_urgency_colors("critical")
        assert "bg_color" in colors
        assert "badge" in colors

    def test_markdown_to_html_bold(self):
        html = markdown_to_html("This is **bold** text")
        assert "<strong" in html or "<b>" in html

    def test_markdown_to_html_link(self):
        html = markdown_to_html("[test](https://example.com)")
        assert "href" in html or "example.com" in html

    def test_markdown_to_html_empty(self):
        assert markdown_to_html("") == ""
        assert markdown_to_html(None) == ""

    def test_markdown_pipe_table_renders_as_html_table(self):
        """Pipe tables must become real <table> markup, never leak as raw text.

        Email clients cannot render markdown, so an unconverted table would show
        the literal ``| a | b |`` characters to the reader.
        """
        html = markdown_to_html("| 항목 | 현재 |\n|---|---|\n| TLS | **1.0** |\n")
        assert 'class="azb-mdtable"' in html
        assert "<th " in html and "<td " in html
        assert "|---|" not in html
        assert "| 항목 |" not in html
        # Inline formatting still applies inside cells
        assert "<strong" in html

    def test_markdown_pipe_table_requires_separator_row(self):
        """A lone pipe line is prose, not a table."""
        html = markdown_to_html("| this is not a table |")
        assert 'class="azb-mdtable"' not in html

    def test_markdown_pipe_table_tolerates_ragged_rows(self):
        """A row with fewer cells than headers must not break the layout."""
        html = markdown_to_html("| a | b |\n|---|---|\n| only-one |\n")
        assert 'class="azb-mdtable"' in html
        assert "| only-one |" not in html

    def test_markdown_table_does_not_break_surrounding_blocks(self):
        html = markdown_to_html("문단 앞\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- 불릿\n")
        assert "문단 앞" in html
        assert "<ul" in html and "불릿" in html

    def test_markdown_strip_headings_drops_subheadings(self):
        """The analysis body must never show template-style subheadings.

        The report prompt forbids them, but prompt formatting rules are not always
        obeyed — the renderer is the last line of defence.
        """
        source = "첫 문단입니다.\n\n### 무엇이 바뀌었나\n\n둘째 문단입니다.\n"
        stripped = markdown_to_html(source, strip_headings=True)
        assert "무엇이 바뀌었나" not in stripped
        assert "첫 문단입니다." in stripped and "둘째 문단입니다." in stripped
        # Default behaviour is unchanged for other callers
        assert "무엇이 바뀌었나" in markdown_to_html(source)

    def test_markdown_collapses_consecutive_spacers(self):
        """A stripped heading must not leave a doubled vertical gap."""
        html = markdown_to_html("앞 문단\n\n### 제목\n\n뒤 문단", strip_headings=True)
        assert html.count('<div style="height: 8px;"></div>') == 1


class TestAffectedResourcesGrouping:
    """Resources sharing the same impact reason render as a single row."""

    def test_same_reason_merges_into_one_row(self):
        resources = [
            {
                "name": "rb-a",
                "type": "microsoft.automation/automationaccounts/runbooks",
                "resourceGroup": "rg1",
                "reason": "PowerShell 7.2 지원 종료 대상 (runbookType: PowerShell72)",
            },
            {
                "name": "rb-b",
                "type": "microsoft.automation/automationaccounts/runbooks",
                "resourceGroup": "rg1",
                "reason": "PowerShell 7.2 지원 종료 대상 (runbookType: PowerShell72)",
            },
            {
                "name": "rb-c",
                "type": "microsoft.automation/automationaccounts/runbooks",
                "resourceGroup": "rg1",
                "reason": "PowerShell 7.2 지원 종료 대상 (runbookType: PowerShell72)",
            },
        ]
        html = format_affected_resources_html(resources, "ko", "retirement")
        # One data row (one <tr> with the shared reason) — reason text appears exactly once
        assert html.count("PowerShell 7.2 지원 종료 대상") == 1
        # All three resource names are present in that single row
        assert "rb-a" in html and "rb-b" in html and "rb-c" in html
        # No group-size badge: it rendered only on grouped rows, which made the
        # resource column look inconsistent between rows.
        assert "font-weight: 700; color: #5b9bd5" not in html

    def test_different_reasons_stay_separate(self):
        resources = [
            {"name": "rb-a", "type": "t", "resourceGroup": "rg1", "reason": "reason ONE"},
            {"name": "rb-b", "type": "t", "resourceGroup": "rg1", "reason": "reason TWO"},
        ]
        html = format_affected_resources_html(resources, "ko", "retirement")
        assert html.count("reason ONE") == 1
        assert html.count("reason TWO") == 1
        # Two distinct reasons → no group-size badge
        assert "font-weight: 700; color: #5b9bd5" not in html

    def test_empty_reasons_not_merged(self):
        resources = [
            {"name": "rb-a", "type": "t", "resourceGroup": "rg1", "reason": ""},
            {"name": "rb-b", "type": "t", "resourceGroup": "rg1", "reason": ""},
        ]
        html = format_affected_resources_html(resources, "ko", "retirement")
        # Both names present; empty reasons must not collapse into a single merged row
        assert "rb-a" in html and "rb-b" in html
        assert "font-weight: 700; color: #5b9bd5" not in html  # no group-size badge

    def test_partial_grouping(self):
        resources = [
            {"name": "a", "type": "t", "resourceGroup": "rg", "reason": "shared"},
            {"name": "b", "type": "t", "resourceGroup": "rg", "reason": "shared"},
            {"name": "c", "type": "t", "resourceGroup": "rg", "reason": "unique"},
        ]
        html = format_affected_resources_html(resources, "ko", "retirement")
        assert html.count("shared") == 1  # a+b merged
        assert html.count("unique") == 1  # c alone
        assert "font-weight: 700; color: #5b9bd5" not in html  # no group-size badge


class TestReportFilteringToggle:
    """report_filtering_enabled gates not_relevant email suppression."""

    def _not_relevant_result(self):
        from src.agent.analyzer import AnalysisResult, RelevanceStatus, UrgencyLevel

        return AnalysisResult(
            update_id="u1",
            update_title="t",
            update_category="new_service",
            urgency=UrgencyLevel.LOW,
            relevance=RelevanceStatus.NOT_RELEVANT,
            relevance_reason="not used",
            affected_resources=[],
            impact_summary="",
            recommendations=[],
            reference_docs=[],
            should_notify=False,
        )

    @pytest.mark.asyncio
    async def test_not_relevant_delivered_when_filtering_disabled(self, sample_update, monkeypatch):
        """With filtering off (default), a not_relevant report is NOT skipped."""
        from unittest.mock import MagicMock

        service = EmailService()
        monkeypatch.setattr(service, "_use_email", False)
        monkeypatch.setattr(service.settings, "report_filtering_enabled", False)
        monkeypatch.setattr("src.email.service._save_html_to_out", lambda *a, **k: None)
        service.build_email_content = MagicMock(
            return_value={"html_content": "<x>", "subject": "s", "plain_content": "p"}
        )
        service._print_to_console = MagicMock(return_value=True)

        sent = await service.send_analysis_report(sample_update, self._not_relevant_result())
        assert sent is True
        assert service.build_email_content.called  # proceeded — not omitted

    @pytest.mark.asyncio
    async def test_not_relevant_skipped_when_filtering_enabled(self, sample_update, monkeypatch):
        """With filtering on, a not_relevant report is suppressed (legacy behavior)."""
        from unittest.mock import MagicMock

        service = EmailService()
        monkeypatch.setattr(service, "_use_email", False)
        monkeypatch.setattr(service.settings, "report_filtering_enabled", True)
        service.build_email_content = MagicMock()

        sent = await service.send_analysis_report(sample_update, self._not_relevant_result())
        assert sent is False
        assert not service.build_email_content.called  # skipped early


class TestProcedureFormatting:
    """Action-item procedures must render as steps, not a wall of text."""

    WALL = (
        "Azure Portal에서 Automation Account를 열고 Runtime Environment를 생성합니다. "
        "이어서 각 런북에서 연결하고 Test pane에서 검증합니다. "
        "평가 기준은 (1) 모듈 준비 가능성, (2) 패키지 의존성, (3) 테스트 결과입니다."
    )

    def test_inline_enumeration_becomes_sub_items(self):
        steps = _split_procedure(self.WALL)
        tops = [t for t, sub in steps if not sub]
        subs = [t for t, sub in steps if sub]
        assert len(tops) == 3  # two sentences + the "평가 기준은" lead
        assert len(subs) == 3  # (1)(2)(3) nested under the lead
        assert not any(s.startswith("(") for s in subs)

    def test_short_procedure_stays_one_paragraph(self):
        """A single click-path must not be padded with pointless numbering."""
        steps = _split_procedure("Azure Portal > Storage Account > Configuration > Save")
        assert len(steps) == 1

    def test_decimal_version_does_not_split(self):
        """'TLS 1.2' must stay intact — a decimal point is not a sentence end."""
        steps = _split_procedure("TLS 1.2 이상을 요구합니다. 그다음 저장합니다.")
        assert any("TLS 1.2" in t for t, _ in steps)

    def test_markdown_list_lines_win(self):
        steps = _split_procedure("- 첫번째 단계입니다\n- 두번째 단계입니다")
        assert [t for t, _ in steps] == ["첫번째 단계입니다", "두번째 단계입니다"]

    def test_empty_procedure(self):
        assert _split_procedure("   ") == []


class TestActionItemRendering:
    """Reference links and layout of the action-item block."""

    def _item(self, **kw):
        from src.agent.analyzer import ActionItem

        return ActionItem(step=1, task="Do the thing", **kw)

    def test_reference_url_renders_as_link(self):
        url = "https://learn.microsoft.com/azure/automation/runtime-environment"
        html = format_action_items_html(
            [self._item(reference_url=url)], language="ko", update_category="feature_change"
        )
        assert f'href="{url}"' in html
        assert get_labels("ko")["action_reference"] in html

    def test_unsafe_reference_url_is_dropped(self):
        """reference_url comes from LLM output — only http(s) may become an anchor."""
        html = format_action_items_html(
            [self._item(reference_url="javascript:alert(1)")],
            language="ko",
            update_category="feature_change",
        )
        assert "javascript:" not in html

    def test_missing_reference_url_adds_nothing(self):
        html = format_action_items_html(
            [self._item()], language="ko", update_category="feature_change"
        )
        assert get_labels("ko")["action_reference"] not in html


class TestActionVerificationRendering:
    """Safety-gate badge and findings shown on an action item."""

    def _item(self, **kw):
        from src.agent.analyzer import ActionItem

        return ActionItem(step=1, task="Do the thing", **kw)

    def _html(self, item, language="ko"):
        return format_action_items_html([item], language=language, update_category="feature_change")

    def test_no_badge_when_verification_did_not_run(self):
        """Reports produced with the gate disabled must render exactly as before."""
        html = self._html(self._item())
        L = get_labels("ko")
        assert L["verification"] not in html
        for status in ("verified", "caution", "blocked", "unverified"):
            assert L[f"verify_{status}"] not in html

    def test_verified_badge_is_shown(self):
        html = self._html(self._item(verification_status="verified"))
        assert get_labels("ko")["verify_verified"] in html

    def test_blocked_item_shows_badge_and_findings(self):
        item = self._item(
            verification_status="blocked",
            verification_notes=["삭제성 명령인데 롤백 절차가 없습니다."],
        )
        html = self._html(item)
        L = get_labels("ko")
        assert L["verify_blocked"] in html
        assert L["verification"] in html
        assert "롤백 절차가 없습니다" in html

    def test_notes_are_escaped_not_interpreted_as_html(self):
        """Notes quote untrusted text (LLM verdicts, withheld commands)."""
        item = self._item(
            verification_status="blocked",
            verification_notes=["<script>alert(1)</script>"],
        )
        html = self._html(item)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_badge_is_localized(self):
        for lang in ("ko", "en", "ja"):
            html = self._html(self._item(verification_status="caution"), language=lang)
            assert get_labels(lang)["verify_caution"] in html


class TestAffectedResourceTable:
    """Layout fixes for the affected/related resources grid."""

    ROWS = [
        {"name": "a1", "type": "Microsoft.Storage/storageAccounts", "reason": "공통 사유"},
        {"name": "a2", "type": "Microsoft.Storage/storageAccounts", "reason": "공통 사유"},
    ]

    def test_grouped_cell_has_no_count_badge(self):
        """The badge only appeared on grouped rows, making the column inconsistent."""
        html = format_affected_resources_html(
            self.ROWS, language="ko", update_category="retirement"
        )
        # Badge signature (9px/700) — distinct from the header total (11px/600).
        assert "font-weight: 700; color: #5b9bd5" not in html
        # The header total is still shown.
        assert f"2{get_labels('ko')['count_suffix']}" in html

    def test_reason_cell_is_middle_aligned(self):
        html = format_affected_resources_html(
            self.ROWS, language="ko", update_category="retirement"
        )
        assert "vertical-align: middle;" in html

    def test_every_row_shows_subscription_and_resource_group(self):
        """Scope is mandatory: a resource without it is not locatable."""
        rows = [
            {
                "name": "a",
                "type": "t",
                "resourceGroup": "rg-1",
                "subscription": "Sub-A",
                "reason": "r1",
            },
            {
                "name": "b",
                "type": "t",
                "resourceGroup": "rg-2",
                "subscriptionName": "Sub-B",
                "reason": "r2",
            },
        ]
        html = format_affected_resources_html(rows, language="ko", update_category="retirement")
        # Rendered as "<subscription> / <resource group>" without labels.
        assert "Sub-A / rg-1" in html
        # subscriptionName is a valid fallback source
        assert "Sub-B / rg-2" in html

    def test_missing_scope_renders_placeholder_not_blank(self):
        """A missing value must keep the row shape, not silently drop the line."""
        L = get_labels("ko")
        both_missing = format_affected_resources_html(
            [{"name": "orphan", "type": "t", "reason": "no metadata"}],
            language="ko",
            update_category="retirement",
        )
        # Said once, not repeated on both sides of the slash.
        assert both_missing.count(L["unknown_scope"]) == 1

        one_missing = format_affected_resources_html(
            [{"name": "half", "type": "t", "resourceGroup": "rg-9", "reason": "r"}],
            language="ko",
            update_category="retirement",
        )
        assert f"{L['unknown_scope']} / rg-9" in one_missing

    def test_uniform_type_moves_to_header(self):
        """73.8% of tables are single-type — repeating it per row wastes a line."""
        rows = [
            {
                "name": "a",
                "type": "microsoft.automation/automationaccounts/runbooks",
                "reason": "r",
            },
            {
                "name": "b",
                "type": "microsoft.automation/automationaccounts/runbooks",
                "reason": "r",
            },
        ]
        html = format_affected_resources_html(rows, language="ko", update_category="retirement")
        # Once in the header, never per row.
        assert html.count("runbooks") == 1
        assert get_labels("ko")["col_resource"] in html

    def test_mixed_types_render_per_row(self):
        rows = [
            {"name": "a", "type": "Microsoft.Storage/storageAccounts", "reason": "r1"},
            {"name": "b", "type": "Microsoft.Compute/virtualMachines", "reason": "r2"},
        ]
        html = format_affected_resources_html(rows, language="ko", update_category="retirement")
        assert "storageAccounts" in html and "virtualMachines" in html

    def test_scope_labels_exist_in_all_languages(self):
        for lang in ("ko", "en", "ja"):
            L = get_labels(lang)
            assert L["subscription"] and L["resource_group"] and L["unknown_scope"]


def test_additional_checks_precede_references():
    """'추가 확인 필요' must come before '참고 문서' in the report layout."""
    checks = HTML_EMAIL_TEMPLATE.index("{additional_checks_html}")
    refs = HTML_EMAIL_TEMPLATE.index("{reference_docs_section_html}")
    assert checks < refs


def test_save_html_to_out_survives_an_unwritable_directory(tmp_path, monkeypatch):
    """A debugging artefact must never cost the caller its email.

    This ran before delivery, so an EACCES on out/ suppressed the whole digest.
    """
    from src.email.service import _save_html_to_out

    blocked = tmp_path / "blocked"
    blocked.write_text("a file where a directory is expected")
    monkeypatch.setenv("AZBRIEF_OUT_DIR", str(blocked))

    assert _save_html_to_out("<p>digest</p>", "digest.html") is None


def test_save_html_to_out_writes_when_it_can(tmp_path, monkeypatch):
    """The happy path still writes the file and returns its path."""
    from src.email.service import _save_html_to_out

    monkeypatch.setenv("AZBRIEF_OUT_DIR", str(tmp_path / "out"))

    saved = _save_html_to_out("<p>digest</p>", "digest.html")

    assert saved is not None
    assert (tmp_path / "out" / "digest.html").read_text(encoding="utf-8") == "<p>digest</p>"
