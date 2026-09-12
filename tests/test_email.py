"""Tests for email content building in src/email/service.py."""

import re
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.config import Subscriber
from src.email.service import EmailService
from src.email.templates import (
    FONT_SIZE_PX,
    FONT_STACK_MONO,
    FONT_STACK_SANS,
    HTML_EMAIL_TEMPLATE,
    _split_procedure,
    format_action_items_html,
    format_affected_resources_html,
    format_impact_section_html,
    format_reference_docs_html,
    format_visual_assets_html,
    get_labels,
    get_urgency_colors,
    markdown_to_html,
)
from src.feedback.models import FeedbackCategory, FeedbackSubmission


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

    def test_email_plain_text_includes_reference_summary(
        self, sample_update, sample_analysis_result
    ):
        sample_analysis_result.reference_docs = [
            {
                "title": "Azure Storage TLS configuration",
                "url": "https://learn.microsoft.com/azure/storage/common/transport-layer-security-configure-minimum-version",
                "description": "최소 TLS 버전을 설정하는 방법을 설명합니다.",
                "related_content": "TLS 1.2 전환 절차 확인",
            }
        ]

        content = EmailService().build_email_content(
            sample_update, sample_analysis_result, language="ko"
        )

        assert "최소 TLS 버전을 설정하는 방법을 설명합니다." in content["plain_content"]
        assert "확인 내용: TLS 1.2 전환 절차 확인" in content["plain_content"]

    def test_email_includes_trusted_visual_with_text_fallback(
        self, sample_update, sample_analysis_result
    ):
        sample_analysis_result.visual_assets = [
            {
                "url": "https://learn.microsoft.com/azure/storage/media/portal-setting.png",
                "alt": "Storage configuration pane",
                "caption": "Azure Portal에서 최소 TLS 버전을 선택하는 화면",
                "source_url": "https://learn.microsoft.com/azure/storage/configure-tls",
                "source_title": "Configure minimum TLS version",
            }
        ]

        content = EmailService().build_email_content(
            sample_update, sample_analysis_result, language="ko"
        )

        assert "시각 자료" in content["html_content"]
        assert (
            'src="https://learn.microsoft.com/azure/storage/media/portal-setting.png"'
            in content["html_content"]
        )
        assert 'alt="Storage configuration pane"' in content["html_content"]
        assert 'width="576"' in content["html_content"]
        assert "Azure Portal에서 최소 TLS 버전을 선택하는 화면" in content["plain_content"]
        assert "https://learn.microsoft.com/azure/storage/configure-tls" in content["plain_content"]

    def test_visual_renderer_rejects_untrusted_or_unsupported_images(self):
        html = format_visual_assets_html(
            [
                {"url": "https://attacker.example/screenshot.png", "alt": "Attack"},
                {"url": "data:image/png;base64,AAAA", "alt": "Inline"},
                {"url": "https://learn.microsoft.com/azure/icon.svg", "alt": "Vector"},
                {
                    "url": "https://learn.microsoft.com/azure/screenshot.png?viewer=unique",
                    "alt": "Tracked",
                },
            ]
        )

        assert html == ""

    def test_digest_limits_visuals_to_four_and_one_per_update(
        self, sample_update, sample_analysis_result
    ):
        sample_analysis_result.visual_assets = [
            {
                "url": "https://attacker.example/ignored.png",
                "alt": "Rejected screenshot",
            },
            {
                "url": "https://learn.microsoft.com/azure/storage/media/one.png",
                "alt": "First screenshot",
            },
            {
                "url": "https://learn.microsoft.com/azure/storage/media/two.png",
                "alt": "Second screenshot",
            },
        ]
        items = [
            {
                "update": sample_update,
                "result": sample_analysis_result.model_copy(deep=True),
                "skip_reason": "",
            }
            for _ in range(6)
        ]

        content = EmailService().build_digest_content(items, language="en")

        assert content["html_content"].count('class="azb-visual"') == 4
        assert "Rejected screenshot" not in content["html_content"]
        assert content["html_content"].count("Second screenshot") == 0

    def test_single_report_links_to_shared_archive_in_html_and_text(
        self, sample_update, sample_analysis_result
    ):
        service = EmailService()
        archive_url = "https://azbrief.example/archive/archive-id"
        content = service.build_email_content(
            sample_update,
            sample_analysis_result,
            language="ko",
            archive_url=archive_url,
        )
        assert archive_url in content["html_content"]
        assert "공용 분석 원본" in content["html_content"]
        assert archive_url in content["plain_content"]

    def test_digest_links_each_detail_to_its_shared_archive(
        self, sample_update, sample_analysis_result
    ):
        service = EmailService()
        archive_url = "https://azbrief.example/archive/archive-id"
        content = service.build_digest_content(
            [
                {
                    "update": sample_update,
                    "result": sample_analysis_result,
                    "skip_reason": "",
                    "archive_url": archive_url,
                }
            ],
            language="en",
        )
        assert archive_url in content["html_content"]
        assert "Shared canonical analysis" in content["html_content"]
        assert archive_url in content["plain_content"]

    def test_unsafe_archive_url_is_omitted_from_html(self, sample_update, sample_analysis_result):
        service = EmailService()
        content = service.build_email_content(
            sample_update,
            sample_analysis_result,
            archive_url="javascript:alert(1)",
        )
        assert "javascript:alert(1)" not in content["html_content"]
        assert "javascript:alert(1)" not in content["plain_content"]

    def test_single_report_footer_links_to_feedback_with_update_context(
        self, sample_update, sample_analysis_result, monkeypatch
    ):
        service = EmailService()
        monkeypatch.setattr(service.settings, "feedback_ui_enabled", True)
        monkeypatch.setattr(service.settings, "feedback_base_url", "https://azbrief.example")

        content = service.build_email_content(
            sample_update,
            sample_analysis_result,
            language="ko",
        )

        expected = "https://azbrief.example/feedback?lang=ko&amp;report=update%3A123456"
        assert expected in content["html_content"]
        assert "이 보고서에 대한 의견 보내기" in content["html_content"]
        assert expected.replace("&amp;", "&") in content["plain_content"]

    def test_digest_footer_links_to_feedback_with_digest_context(
        self, sample_update, sample_analysis_result, monkeypatch
    ):
        service = EmailService()
        monkeypatch.setattr(service.settings, "feedback_ui_enabled", True)
        monkeypatch.setattr(service.settings, "feedback_base_url", "https://azbrief.example")

        content = service.build_digest_content(
            [{"update": sample_update, "result": sample_analysis_result, "skip_reason": ""}],
            date_range="2026-09-01 ~ 2026-09-06",
            language="en",
        )

        assert "Send feedback about this report" in content["html_content"]
        assert "report=digest%3A2026-09-01+~+2026-09-06" in content["html_content"]
        assert (
            "https://azbrief.example/feedback?lang=en&report=digest%3A" in content["plain_content"]
        )

    def test_digest_omits_unsafe_archive_url_from_plain_text(
        self, sample_update, sample_analysis_result
    ):
        service = EmailService()
        content = service.build_digest_content(
            [
                {
                    "update": sample_update,
                    "result": sample_analysis_result,
                    "skip_reason": "",
                    "archive_url": "javascript:alert(1)",
                }
            ]
        )
        assert "javascript:alert(1)" not in content["html_content"]
        assert "javascript:alert(1)" not in content["plain_content"]

    @pytest.mark.asyncio
    async def test_feedback_notification_goes_to_configured_developer(self, monkeypatch):
        class Poller:
            def result(self):
                return {"id": "feedback-message-1"}

        client = MagicMock()
        client.begin_send.return_value = Poller()
        service = EmailService()
        monkeypatch.setattr(service, "_transport_ready", True)
        monkeypatch.setattr(service, "_client", client)
        monkeypatch.setattr(service.settings, "email_sender_address", "sender@example.com")
        monkeypatch.setattr(
            service.settings,
            "feedback_recipient_address",
            "feedback@contoso.com",
        )
        submission = FeedbackSubmission(
            feedback_id="feedback-1",
            created_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
            category=FeedbackCategory.BUG,
            subject="HTML <footer> is missing",
            details="The report shows <script>alert(1)</script> as text.",
            contact_email="reader@example.com",
            report_reference="update:123456",
            language="ko",
        )

        sent = await service.send_feedback_notification(submission)

        assert sent is True
        message = client.begin_send.call_args.args[0]
        assert message["recipients"]["to"] == [{"address": "feedback@contoso.com"}]
        assert "&lt;footer&gt;" in message["content"]["html"]
        assert "<script>alert(1)</script>" not in message["content"]["html"]
        assert "update:123456" in message["content"]["plainText"]

    def test_scoped_digest_omits_the_global_retirement_tracker(
        self, sample_update, sample_analysis_result, monkeypatch
    ):
        service = EmailService()
        monkeypatch.setattr(
            service,
            "_build_retirement_countdown_html",
            lambda _language: "GLOBAL-RETIREMENT-LEAK",
        )

        content = service.build_digest_content(
            [
                {
                    "update": sample_update,
                    "result": sample_analysis_result,
                    "skip_reason": "",
                    "subscriber_scope_bounded": True,
                }
            ]
        )

        assert "GLOBAL-RETIREMENT-LEAK" not in content["html_content"]

    @pytest.mark.asyncio
    async def test_bounded_subscriber_is_reanalyzed_and_does_not_receive_archive_link(
        self, sample_update, sample_analysis_result, monkeypatch
    ):
        scoped_result = sample_analysis_result.model_copy(deep=True)

        class Analyzer:
            scope = None

            async def analyze_update(self, update, scope=None):
                assert update is sample_update
                self.scope = scope
                return scoped_result

            async def customize_for_subscriber(self, result, subscriber, update):
                assert result is scoped_result
                assert update is sample_update
                assert subscriber.email == "scoped@example.com"
                return result

        sent = []

        async def fake_send(update, result, **kwargs):
            sent.append((update, result, kwargs))
            return True

        service = EmailService()
        monkeypatch.setattr(service, "send_analysis_report", fake_send)
        analyzer = Analyzer()
        subscriber = Subscriber(
            email="scoped@example.com",
            name="Scoped",
            management_groups=["platform-mg"],
            subscriptions=["11111111-1111-1111-1111-111111111111"],
            resource_groups=["production-rg"],
        )

        outcomes = await service.send_to_subscribers(
            sample_update,
            sample_analysis_result,
            analyzer,
            [subscriber],
            archive_url="https://azbrief.example/archive/canonical",
        )

        assert outcomes == {"scoped@example.com": True}
        assert analyzer.scope.management_groups == ("platform-mg",)
        assert analyzer.scope.subscriptions == ("11111111-1111-1111-1111-111111111111",)
        assert analyzer.scope.resource_groups == ("production-rg",)
        assert sent[0][1] is scoped_result
        assert sent[0][2]["archive_url"] == ""

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

    @pytest.mark.parametrize("builder", ["single", "digest"])
    def test_email_uses_preinstalled_system_fonts_only(
        self, sample_update, sample_analysis_result, builder: str
    ):
        """Email builders use the shared system-font stack without loading webfonts."""
        service = EmailService()
        if builder == "single":
            html = service.build_email_content(
                sample_update, sample_analysis_result, language="ko"
            )["html_content"]
        else:
            html = service.build_digest_content(
                [{"update": sample_update, "result": sample_analysis_result}], language="ko"
            )["html_content"]
        assert f"font-family: {FONT_STACK_SANS};" in html
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
        """Every rendered size is a step on the 13px-body scale."""
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

        used = {float(px) for px in re.findall(r"font-size:\s*(\d+(?:\.\d+)?)px", html)}
        assert used, "no font sizes rendered"
        assert not used - set(
            FONT_SIZE_PX.values()
        ), f"off-scale font sizes: {sorted(used - set(FONT_SIZE_PX.values()))}"
        # Body copy uses the regular-email size, with headings a step above it.
        assert f"font-size: {FONT_SIZE_PX['body']}px" in html
        assert f"font-size: {FONT_SIZE_PX['heading']}px" in html

    def test_type_scale_is_ordered_and_body_is_the_email_default(self):
        """The scale keeps its hierarchy and anchors body copy at 13px."""
        assert FONT_SIZE_PX == {
            "meta": 11,
            "secondary": 12,
            "body": 13,
            "heading": 15,
            "section_mobile": 15.75,
            "title": 17,
            "badge": 18,
            "section": 18.75,
            "masthead": 21,
            "display": 25,
            "hero": 29,
            "cover": 36,
            "stat": 48,
        }
        steps = [
            FONT_SIZE_PX[k]
            for k in (
                "meta",
                "secondary",
                "body",
                "heading",
                "section_mobile",
                "title",
                "badge",
                "section",
                "masthead",
                "display",
                "hero",
                "cover",
                "stat",
            )
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
        assert get_labels("ko")["col_job_relevance"] in html

    def test_font_stacks_follow_email_policy(self):
        """Prose uses the requested font order while code remains monospaced."""
        assert FONT_STACK_SANS == (
            "'Apple SD Gothic Neo', 'Malgun Gothic', 'Dotum', Arial, Helvetica, sans-serif"
        )

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
        html = markdown_to_html("[test](https://learn.microsoft.com/azure)")
        assert '<a href="https://learn.microsoft.com/azure"' in html

    def test_markdown_to_html_escapes_markup_and_drops_untrusted_links(self):
        html = markdown_to_html(
            '<img src=x onerror="alert(1)"> **safe** '
            "[bad](https://attacker.example/phish) [script](javascript:alert(1))"
        )
        assert "<img" not in html
        assert "&lt;img" in html
        assert "<strong" in html
        assert "attacker.example" not in html
        assert "javascript:" not in html

    def test_email_escapes_untrusted_update_and_analysis_fields(
        self, sample_update, sample_analysis_result
    ):
        from src.agent.analyzer import ActionItem, ImpactSummary

        service = EmailService()
        sample_update.title = '<img src=x onerror="alert(1)">'
        sample_update.azure_services = ["<script>alert(1)</script>"]
        sample_analysis_result.one_line_summary = "<b>unsafe summary</b>"
        sample_analysis_result.relevance_reason = '<svg onload="alert(1)">'
        sample_analysis_result.impact_details = ImpactSummary(
            security_impact="<iframe src=https://attacker.example></iframe>"
        )
        sample_analysis_result.affected_resources = [
            {
                "name": "<img src=x>",
                "type": "Microsoft.Storage/<script>",
                "reason": "<details open ontoggle=alert(1)>",
            }
        ]
        sample_analysis_result.action_items = [
            ActionItem(
                task="<script>alert(1)</script>",
                procedure="<img src=x onerror=alert(1)>",
                cli_command="echo '<unsafe>'",
                reference_url="https://attacker.example/phish",
                verification_notes=["<svg onload=alert(1)>"],
            )
        ]
        sample_analysis_result.additional_checks = ["<marquee>unsafe</marquee>"]
        sample_analysis_result.reference_docs = [
            {
                "title": "<i>phish</i>",
                "url": "https://attacker.example/phish",
                "related_content": "<object data=evil>",
            }
        ]
        sample_analysis_result.visual_assets = [
            {
                "url": "https://attacker.example/screenshot.png",
                "alt": '<img src=x onerror="alert(1)">',
            }
        ]

        content = service.build_email_content(sample_update, sample_analysis_result)
        rendered = content["html_content"]

        assert "<img" not in rendered
        assert "<script>alert" not in rendered
        assert "<b>unsafe" not in rendered
        assert "<svg" not in rendered
        assert "<i>phish" not in rendered
        assert "<iframe" not in rendered
        assert "<details" not in rendered
        assert "<marquee" not in rendered
        assert "<object" not in rendered
        assert 'href="https://attacker.example' not in rendered
        assert 'src="https://attacker.example' not in rendered
        assert "&lt;img" in rendered

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
    """Resources sharing a reason render below one full-width reason row."""

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
        # One merged reason row followed by one four-column row per resource.
        assert html.count("PowerShell 7.2 지원 종료 대상") == 1
        assert html.count('class="azb-resource-reason"') == 1
        assert html.count('colspan="4"') == 1
        assert html.count('class="azb-resource-row') == 3
        assert "rb-a" in html and "rb-b" in html and "rb-c" in html

    def test_different_reasons_stay_separate(self):
        resources = [
            {"name": "rb-a", "type": "t", "resourceGroup": "rg1", "reason": "reason ONE"},
            {"name": "rb-b", "type": "t", "resourceGroup": "rg1", "reason": "reason TWO"},
        ]
        html = format_affected_resources_html(resources, "ko", "retirement")
        assert html.count("reason ONE") == 1
        assert html.count("reason TWO") == 1
        assert html.count('class="azb-resource-reason"') == 2

    def test_empty_reasons_not_merged(self):
        resources = [
            {"name": "rb-a", "type": "t", "resourceGroup": "rg1", "reason": ""},
            {"name": "rb-b", "type": "t", "resourceGroup": "rg1", "reason": ""},
        ]
        html = format_affected_resources_html(resources, "ko", "retirement")
        # Empty reasons stay independent so unrelated resources are not grouped.
        assert "rb-a" in html and "rb-b" in html
        assert html.count('class="azb-resource-reason"') == 2

    def test_partial_grouping(self):
        resources = [
            {"name": "a", "type": "t", "resourceGroup": "rg", "reason": "shared"},
            {"name": "b", "type": "t", "resourceGroup": "rg", "reason": "shared"},
            {"name": "c", "type": "t", "resourceGroup": "rg", "reason": "unique"},
        ]
        html = format_affected_resources_html(resources, "ko", "retirement")
        assert html.count("shared") == 1  # a+b merged
        assert html.count("unique") == 1  # c alone
        assert html.count('class="azb-resource-reason"') == 2


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
    async def test_managed_recipient_uses_transport_without_static_recipient(
        self, sample_update, monkeypatch
    ):
        from unittest.mock import MagicMock

        class Poller:
            def result(self):
                return {"id": "message-1"}

        client = MagicMock()
        client.begin_send.return_value = Poller()
        service = EmailService()
        monkeypatch.setattr(service, "_use_email", False)
        monkeypatch.setattr(service, "_transport_ready", True)
        monkeypatch.setattr(service, "_client", client)
        monkeypatch.setattr(service.settings, "email_sender_address", "sender@example.com")
        monkeypatch.setattr(service.settings, "email_recipient_address", None)
        monkeypatch.setattr(service.settings, "report_filtering_enabled", False)
        monkeypatch.setattr(service.settings, "require_approval_before_send", False)
        monkeypatch.setattr("src.email.service._save_html_to_out", lambda *a, **k: None)
        service.build_email_content = MagicMock(
            return_value={"html_content": "<p>report</p>", "subject": "s", "plain_content": "p"}
        )

        sent = await service.send_analysis_report(
            sample_update,
            self._not_relevant_result(),
            recipient="managed@example.com",
        )

        assert sent is True
        message = client.begin_send.call_args.args[0]
        assert message["recipients"]["to"] == [{"address": "managed@example.com"}]

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

    def test_action_details_are_grouped_in_a_stable_reading_order(self):
        """A dense action stays scannable instead of becoming an unlabelled text wall."""
        item = self._item(
            why="지원 종료 전에 런타임을 전환해야 합니다.",
            target_resources=["runbook-a", "runbook-b"],
            procedure="새 런타임을 만듭니다. 테스트 후 연결을 전환합니다.",
            cli_command="az automation runtime-environment create",
            deadline="2026-10-01",
            estimated_time="30분",
            risk_if_not_done="실행이 중단될 수 있습니다.",
            precaution="현재 모듈 버전을 기록합니다.",
            rollback="기존 런타임 연결을 복원합니다.",
            reference_url="https://learn.microsoft.com/azure/automation/runtime-environment",
            verification_status="caution",
            verification_notes=["테스트 결과를 확인해야 합니다."],
        )
        html = format_action_items_html([item], language="ko", update_category="feature_change")
        section_classes = (
            "azb-action-context",
            "azb-action-procedure",
            "azb-action-schedule",
            "azb-action-guardrails",
        )
        positions = [html.index(section_class) for section_class in section_classes]
        assert positions == sorted(positions)

        L = get_labels("ko")
        for label in (
            L["target"],
            L["why"],
            L["procedure"],
            L["deadline"],
            L["estimated"],
            L["risk_if_not_done"],
            L["precaution"],
            L["rollback"],
            L["action_reference"],
            L["verification"],
        ):
            assert label in html
        assert "지원 종료 전에 런타임을 전환해야 합니다." in html

    def test_target_and_portal_procedure_link_to_the_resolved_resource(self):
        subscription_id = "00000000-0000-0000-0000-000000000001"
        resource = {
            "name": "account-a",
            "subscriptionId": subscription_id,
            "resourceGroup": "rg-a",
            "type": "Microsoft.Storage/storageAccounts",
        }
        item = self._item(
            target_resources=["account-a"],
            procedure="Azure Portal > Storage Account > Configuration > Save",
        )

        html = format_action_items_html(
            [item],
            language="ko",
            update_category="feature_change",
            affected_resources=[resource],
        )

        resource_url = (
            f"https://portal.azure.com/#resource/subscriptions/{subscription_id}"
            "/resourceGroups/rg-a/providers/Microsoft.Storage/storageAccounts/account-a"
        )
        assert html.count(f'href="{resource_url}"') == 2

    @pytest.mark.parametrize(
        ("command", "shell"),
        [
            ("az storage account show --name account-a", "bash"),
            ("az storage account show --name $ACCOUNT_NAME", "bash"),
            ("Get-AzStorageAccount -ResourceGroupName rg-a", "pwsh"),
            ("$name = $env:ACCOUNT_NAME; Get-AzStorageAccount -Name $name", "pwsh"),
        ],
    )
    def test_cli_command_opens_the_matching_cloud_shell(self, command, shell):
        html = format_action_items_html(
            [self._item(cli_command=command)],
            language="ko",
            update_category="feature_change",
        )

        cloud_shell_url = f"https://portal.azure.com/?feature.azureconsole.shell={shell}#cloudshell"
        assert f'href="{cloud_shell_url}"' in html
        assert command in html
        assert get_labels("ko")["cloud_shell_open"] in html

    def test_duplicate_resource_names_do_not_create_an_ambiguous_action_link(self):
        subscription_ids = (
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        )
        resources = [
            {
                "name": "account-a",
                "subscriptionId": subscription_id,
                "resourceGroup": "rg-a",
                "type": "Microsoft.Storage/storageAccounts",
            }
            for subscription_id in subscription_ids
        ]

        html = format_action_items_html(
            [self._item(target_resources=["account-a"])],
            language="ko",
            update_category="feature_change",
            affected_resources=resources,
        )

        assert "account-a" in html
        assert "https://portal.azure.com/#resource/" not in html

    def test_multi_target_procedure_does_not_link_to_only_one_resource(self):
        subscription_id = "00000000-0000-0000-0000-000000000001"
        resource = {
            "name": "account-a",
            "subscriptionId": subscription_id,
            "resourceGroup": "rg-a",
            "type": "Microsoft.Storage/storageAccounts",
        }
        item = self._item(
            target_resources=["account-a", "account-without-metadata"],
            procedure="Azure Portal > Storage Account > Configuration > Save",
        )

        html = format_action_items_html(
            [item],
            language="ko",
            update_category="feature_change",
            affected_resources=[resource],
        )

        resource_url = (
            f"https://portal.azure.com/#resource/subscriptions/{subscription_id}"
            "/resourceGroups/rg-a/providers/Microsoft.Storage/storageAccounts/account-a"
        )
        assert html.count(f'href="{resource_url}"') == 1


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


class TestReferenceDocumentRendering:
    """References pair each link with a concise description when available."""

    def test_description_and_report_context_render_below_the_link(self):
        html = format_reference_docs_html(
            [
                {
                    "title": "Azure Storage TLS configuration",
                    "url": "https://learn.microsoft.com/azure/storage/common/transport-layer-security-configure-minimum-version",
                    "description": (
                        "Azure Storage의 최소 TLS 버전을 Portal과 CLI에서 설정하는 방법을 설명합니다."
                    ),
                    "related_content": "TLS 1.2 전환 절차 확인",
                }
            ],
            language="ko",
        )

        assert html.index("Azure Storage의 최소 TLS") < html.index("TLS 1.2 전환 절차 확인")
        assert get_labels("ko")["doc_context"] in html

    def test_legacy_related_content_becomes_the_link_description(self):
        html = format_reference_docs_html(
            [
                {
                    "title": "Legacy document",
                    "url": "https://learn.microsoft.com/azure/example",
                    "related_content": "지원 리전과 SKU 제약을 설명합니다.",
                }
            ],
            language="ko",
        )

        assert "지원 리전과 SKU 제약을 설명합니다." in html
        assert f'{get_labels("ko")["doc_context"]}:' not in html

    def test_description_is_escaped(self):
        html = format_reference_docs_html(
            [
                {
                    "title": "Safe document",
                    "url": "https://learn.microsoft.com/azure/example",
                    "description": "<script>alert(1)</script>",
                }
            ],
            language="ko",
        )

        assert "<script>" not in html
        assert "&lt;script&gt;" in html


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

    def test_reason_spans_all_resource_columns(self):
        html = format_affected_resources_html(
            self.ROWS, language="ko", update_category="retirement"
        )
        reason_row = html[html.index('class="azb-resource-reason"') :]
        assert 'colspan="4"' in reason_row

    def test_resource_columns_follow_reason_in_required_order(self):
        rows = [
            {
                "name": "account-a",
                "subscription": "Sub-A",
                "resourceGroup": "rg-a",
                "type": "Microsoft.Storage/storageAccounts",
                "reason": "TLS 정책 영향",
            }
        ]
        html = format_affected_resources_html(rows, language="ko", update_category="retirement")
        L = get_labels("ko")
        reason_index = html.index("TLS 정책 영향")
        headers = [L["col_resource"], L["subscription"], L["resource_group"], L["col_type"]]
        header_positions = [html.index(header, reason_index) for header in headers]
        assert header_positions == sorted(header_positions)
        assert 'width="28%"' in html
        assert html.count('width="24%"') == 3

        resource_row = re.search(
            r'<tr class="azb-resource-row[^>]*>(.*?)</tr>', html, flags=re.DOTALL
        )
        assert resource_row is not None
        row_html = resource_row.group(1)
        values = ["account-a", "Sub-A", "rg-a", "storageAccounts"]
        value_positions = [row_html.index(value) for value in values]
        assert value_positions == sorted(value_positions)

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
        assert "Sub-A" in html and "rg-1" in html
        # subscriptionName remains a valid fallback source.
        assert "Sub-B" in html and "rg-2" in html
        assert "Sub-A / rg-1" not in html

    def test_missing_scope_renders_placeholder_not_blank(self):
        """A missing value must keep the row shape, not silently drop the line."""
        L = get_labels("ko")
        both_missing = format_affected_resources_html(
            [{"name": "orphan", "type": "t", "reason": "no metadata"}],
            language="ko",
            update_category="retirement",
        )
        # Subscription and resource group are separate columns.
        assert both_missing.count(L["unknown_scope"]) == 2

        one_missing = format_affected_resources_html(
            [{"name": "half", "type": "t", "resourceGroup": "rg-9", "reason": "r"}],
            language="ko",
            update_category="retirement",
        )
        assert L["unknown_scope"] in one_missing
        assert "rg-9" in one_missing
        assert f"{L['unknown_scope']} / rg-9" not in one_missing

    def test_uniform_type_stays_in_each_resource_row(self):
        """The required resource-type column remains explicit for every row."""
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
        assert html.count("runbooks") == 2
        assert get_labels("ko")["col_type"] in html

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
            assert (
                L["subscription"] and L["resource_group"] and L["col_type"] and L["unknown_scope"]
            )

    def test_resource_subscription_and_group_link_to_azure_portal(self):
        subscription_id = "00000000-0000-0000-0000-000000000001"
        rows = [
            {
                "name": "account-a",
                "subscription": "Sub-A",
                "subscriptionId": subscription_id,
                "resourceGroup": "rg-a",
                "type": "Microsoft.Storage/storageAccounts",
                "reason": "TLS 정책 영향",
            }
        ]

        html = format_affected_resources_html(rows, language="ko", update_category="retirement")

        resource_url = (
            f"https://portal.azure.com/#resource/subscriptions/{subscription_id}"
            "/resourceGroups/rg-a/providers/Microsoft.Storage/storageAccounts/account-a"
        )
        subscription_url = f"https://portal.azure.com/#resource/subscriptions/{subscription_id}"
        group_url = (
            f"https://portal.azure.com/#resource/subscriptions/{subscription_id}"
            "/resourceGroups/rg-a"
        )
        assert f'href="{resource_url}"' in html
        assert f'href="{subscription_url}"' in html
        assert f'href="{group_url}"' in html

    def test_supplied_nested_resource_id_is_linked_without_reconstruction(self):
        subscription_id = "00000000-0000-0000-0000-000000000001"
        resource_id = (
            f"/subscriptions/{subscription_id}/resourceGroups/rg-a/providers/"
            "Microsoft.Automation/automationAccounts/account-a/runbooks/runbook-a"
        )
        rows = [
            {
                "id": resource_id,
                "name": "runbook-a",
                "subscriptionId": subscription_id,
                "resourceGroup": "rg-a",
                "type": "Microsoft.Automation/automationAccounts/runbooks",
                "reason": "런타임 영향",
            }
        ]

        html = format_affected_resources_html(rows, language="ko", update_category="retirement")

        assert f'href="https://portal.azure.com/#resource{resource_id}"' in html

    def test_ambiguous_resource_is_not_given_a_fabricated_portal_link(self):
        rows = [
            {
                "name": "runbook-a",
                "subscription": "Sub-A",
                "resourceGroup": "rg-a",
                "type": "Microsoft.Automation/automationAccounts/runbooks",
                "reason": "런타임 영향",
            }
        ]

        html = format_affected_resources_html(rows, language="ko", update_category="retirement")

        assert "runbook-a" in html
        assert "https://portal.azure.com/" not in html


def test_impact_label_column_has_outlook_safe_width():
    """Impact labels must not collapse into one Korean glyph per line."""
    details = SimpleNamespace(
        cost_impact="비용 영향",
        security_impact="보안 영향",
        performance_impact="성능 영향",
        operational_impact="운영 영향",
    )
    html = format_impact_section_html(details, language="ko", update_category="retirement")
    labels = re.findall(r'<td class="azb-impact-label"([^>]*)>', html)
    assert len(labels) == 4
    for attributes in labels:
        assert 'width="96"' in attributes
        assert "min-width: 96px" in attributes
        assert "white-space: nowrap" in attributes
        assert "word-break: keep-all" in attributes


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
