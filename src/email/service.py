"""Email service using Azure Communication Services."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from azure.core.exceptions import HttpResponseError
from structlog import get_logger

from src.agent.analyzer import AnalysisResult
from src.agent.scope import AnalysisScope
from src.config import Subscriber, get_settings
from src.feedback.models import FeedbackSubmission

if TYPE_CHECKING:  # analyzer imports this module's package at runtime
    from src.agent.analyzer import AzureUpdateAnalyzer
from src.email.templates import (
    EMAIL_COLORS,
    FONT_STACK_SANS,
    HTML_DIGEST_TEMPLATE,
    HTML_EMAIL_TEMPLATE,
    escape_email_text,
    format_action_items_html,
    format_additional_checks_html,
    format_affected_resources_html,
    format_batch_context_html,
    format_digest_intro_html,
    format_digest_table_header_html,
    format_digest_update_card_html,
    format_email_footer_html,
    format_email_masthead_html,
    format_email_section_html,
    format_impact_section_html,
    format_quick_decision_html,
    format_reference_docs_html,
    format_relevance_evidence_html,
    format_report_header_html,
    format_timeline_html,
    get_importance_level,
    get_labels,
    markdown_to_html,
    safe_archive_url,
    safe_email_href,
)
from src.feedback.service import build_feedback_page_url
from src.i18n import get_language
from src.rss.parser import AzureUpdate

logger = get_logger()

# Lazy import for EmailClient (only when needed)
EmailClient = None


def _escape_braces(s: str) -> str:
    """Escape curly braces in strings to prevent format() errors in f-strings."""
    return s.replace("{", "{{").replace("}", "}}")


def _save_html_to_out(html_content: str, filename: str) -> Optional[str]:
    """Save HTML content to the out/ directory for debugging.

    Best-effort by design: this runs before delivery, so a write failure
    (read-only filesystem, unwritable directory) must not cost the caller its
    email or console output.

    Args:
        html_content: The HTML email content.
        filename: Output filename (e.g., 'digest_ko.html').

    Returns:
        Absolute path of the saved file, or None when it could not be written.
    """
    import os
    from pathlib import Path

    filepath = Path(os.environ.get("AZBRIEF_OUT_DIR", "out")) / filename
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(html_content, encoding="utf-8")
    except OSError as exc:
        logger.warning("html_report_save_failed", path=str(filepath), error=str(exc))
        return None
    logger.info("html_report_saved", path=str(filepath), size=len(html_content))
    return str(filepath)


def get_email_client_class():
    """Lazy load EmailClient to avoid import errors when not using email."""
    global EmailClient
    if EmailClient is None:
        from azure.communication.email import EmailClient as _EmailClient

        EmailClient = _EmailClient
    return EmailClient


class EmailService:
    """Service for sending analysis reports via email or console."""

    def __init__(self):
        """Initialize email service."""
        self.settings = get_settings()
        self._client = None
        self._use_email = self.settings.use_email
        self._transport_ready = bool(
            (
                self.settings.communication_services_connection_string
                or self.settings.communication_services_endpoint
            )
            and self.settings.email_sender_address
        )

    @property
    def client(self):
        """Get or create email client.

        Uses the connection string when one is configured; otherwise
        authenticates to the ACS endpoint with the managed identity, which is
        how the enterprise profile avoids storing an email secret at all.
        """
        if not self._transport_ready:
            return None
        if self._client is None:
            EmailClientClass = get_email_client_class()
            if self.settings.communication_services_connection_string:
                self._client = EmailClientClass.from_connection_string(
                    self.settings.communication_services_connection_string
                )
            else:
                from src.config import get_azure_credential

                self._client = EmailClientClass(
                    self.settings.communication_services_endpoint,
                    get_azure_credential(),
                )
        return self._client

    async def send_feedback_notification(self, submission: FeedbackSubmission) -> bool:
        """Send one persisted feedback submission to the configured developer mailbox."""
        recipient = (self.settings.feedback_recipient_address or "").strip()
        if not self._transport_ready or not recipient:
            logger.error(
                "feedback_notification_not_configured",
                feedback_id=submission.feedback_id,
            )
            return False

        category_labels = {
            "bug": "프로그램 버그",
            "improvement": "개선 사항",
            "report_context": "보고서 컨텍스트",
        }
        category = category_labels[submission.category.value]
        subject = f"[AzBrief Feedback] [{category}] {submission.subject}"[:120]
        report_reference = submission.report_reference or "없음"
        contact_email = submission.contact_email or "미제공"
        created_at = submission.created_at.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        safe_details = escape_email_text(submission.details).replace("\n", "<br>")
        html_content = f"""<!doctype html>
<html lang="ko"><body style="margin:0;padding:24px;background:#f4f6f7;font-family:{FONT_STACK_SANS};color:#172126;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width:680px;margin:0 auto;background:#ffffff;border:1px solid #dce3e6;border-collapse:separate;">
<tr><td style="padding:20px 24px;background:#172126;color:#ffffff;"><strong style="font-size:16px;">AzBrief Feedback</strong></td></tr>
<tr><td style="padding:22px 24px;">
<p style="margin:0 0 14px;font-size:14px;font-weight:700;">{escape_email_text(submission.subject)}</p>
<p style="margin:0 0 8px;font-size:12px;"><strong>유형:</strong> {category}</p>
<p style="margin:0 0 8px;font-size:12px;"><strong>보고서 참조:</strong> {escape_email_text(report_reference)}</p>
<p style="margin:0 0 8px;font-size:12px;"><strong>연락처:</strong> {escape_email_text(contact_email)}</p>
<p style="margin:0 0 16px;font-size:12px;"><strong>작성 언어:</strong> {escape_email_text(submission.language)}</p>
<div style="padding:14px;background:#f8fafb;border-left:3px solid #0f766e;font-size:12px;line-height:1.65;overflow-wrap:anywhere;">{safe_details}</div>
</td></tr>
<tr><td style="padding:12px 24px;background:#f8f9fb;border-top:1px solid #dce3e6;color:#5f6f77;font-size:10px;">{escape_email_text(submission.feedback_id)} &middot; {created_at}</td></tr>
</table></body></html>"""
        plain_content = "\n".join(
            [
                "AzBrief Feedback",
                f"유형: {category}",
                f"제목: {submission.subject}",
                f"보고서 참조: {report_reference}",
                f"연락처: {contact_email}",
                f"작성 언어: {submission.language}",
                "",
                submission.details,
                "",
                f"Feedback ID: {submission.feedback_id}",
                f"작성 시각: {created_at}",
            ]
        )
        message = {
            "senderAddress": self.settings.email_sender_address,
            "recipients": {"to": [{"address": recipient}]},
            "content": {
                "subject": subject,
                "html": html_content,
                "plainText": plain_content,
            },
        }

        try:
            poller = self.client.begin_send(message)
            send_result = poller.result()
            logger.info(
                "feedback_notification_sent",
                feedback_id=submission.feedback_id,
                category=submission.category.value,
                message_id=send_result.get("id"),
            )
            return True
        except HttpResponseError as exc:
            logger.error(
                "feedback_notification_failed",
                feedback_id=submission.feedback_id,
                category=submission.category.value,
                status_code=getattr(exc, "status_code", None),
                error=str(exc),
            )
            return False
        except Exception as exc:
            logger.error(
                "feedback_notification_failed",
                feedback_id=submission.feedback_id,
                category=submission.category.value,
                error=str(exc),
            )
            return False

    def build_email_content(
        self,
        update: AzureUpdate,
        result: AnalysisResult,
        language: str = "ko",
        batch_stats: Optional[dict] = None,
        archive_url: str = "",
    ) -> dict:
        """Build email content from analysis result.

        Args:
            update: Original Azure Update
            result: Analysis result
            language: Language code for UI labels (default: ko)
            batch_stats: Optional batch filtering stats (total_updates, relevant_count)

        Returns:
            Email content dictionary
        """
        L = get_labels(language)
        archive_url = safe_archive_url(archive_url)
        feedback_url = build_feedback_page_url(
            self.settings,
            language,
            f"update:{update.id}",
        )

        # Get urgency info
        urgency_value = (
            result.urgency.value if hasattr(result, "urgency") and result.urgency else "medium"
        )
        # One line summary
        one_line = (
            result.one_line_summary
            if hasattr(result, "one_line_summary") and result.one_line_summary
            else update.title[:80]
        )

        # Build HTML content from professional template
        relevance_value = (
            result.relevance.value if hasattr(result.relevance, "value") else str(result.relevance)
        )
        update_category = getattr(result, "update_category", "new_feature")

        html_content = HTML_EMAIL_TEMPLATE.format(
            html_lang=get_language(language).lang_attr,
            document_title=escape_email_text(update.title),
            preheader=escape_email_text(one_line),
            masthead_html=format_email_masthead_html(L["email_report_label"]),
            report_header_html=format_report_header_html(update, result, language, archive_url),
            # 환경 연관성
            relevance_evidence_html=format_relevance_evidence_html(
                getattr(result, "relevance_evidence", ""),
                language,
            ),
            # Batch context (filtering stats)
            batch_context_html=(
                format_batch_context_html(
                    batch_stats.get("total_updates", 0),
                    batch_stats.get("relevant_count", 0),
                    language,
                )
                if batch_stats
                else ""
            ),
            # Quick decision card
            quick_decision_html=format_quick_decision_html(result, language),
            # Analysis
            analysis_section_html=format_email_section_html(
                L["analysis_summary"],
                markdown_to_html(result.relevance_reason or "", strip_headings=True),
            ),
            # Key dates timeline
            timeline_html=format_timeline_html(
                result.action_items if hasattr(result, "action_items") else [],
                update_category,
                language,
            ),
            # Impact analysis
            impact_section_html=format_impact_section_html(
                result.impact_details if hasattr(result, "impact_details") else None,
                language,
                update_category=update_category,
            ),
            # Affected resources (conditional by update category)
            affected_resources_section_html=format_affected_resources_html(
                result.affected_resources,
                language,
                update_category=update_category,
            ),
            # Action items (self-contained <tr>, conditional by update category)
            action_items_section_html=format_action_items_html(
                result.action_items if hasattr(result, "action_items") else [],
                result.recommendations,
                language,
                update_category=update_category,
                affected_resources=result.affected_resources,
            ),
            # Reference docs (self-contained <tr>)
            reference_docs_section_html=format_reference_docs_html(result.reference_docs, language),
            # Additional checks
            additional_checks_html=format_additional_checks_html(
                result.additional_checks if hasattr(result, "additional_checks") else [],
                language,
            ),
            footer_html=format_email_footer_html(
                language,
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                feedback_url,
            ),
        )

        # Build subject (urgency prefix for critical/high, relevance suffix for context)
        urgency_prefix = {
            "critical": L["urgency_prefix_critical"],
            "high": L["urgency_prefix_high"],
        }
        relevance_tag = ""
        if relevance_value == "opportunity":
            relevance_tag = " [FYI]"
        elif relevance_value == "not_relevant":
            relevance_tag = " [INFO]"
        prefix = urgency_prefix.get(urgency_value, "")

        # Use one_line_summary (concise) when available, fall back to title
        subject_text = one_line if one_line else update.title
        # Calculate remaining space for subject text
        # Email subject recommended max ~120 chars; reserve room for tags
        tag_part = f"[AzBrief]{' ' + prefix if prefix else ''}{relevance_tag} "
        max_text_len = 120 - len(tag_part)
        if len(subject_text) > max_text_len:
            subject_text = subject_text[: max_text_len - 1] + "…"
        subject = f"{tag_part}{subject_text}"

        # Build plain text version
        plain_content = self._build_plain_text(
            update,
            result,
            language,
            archive_url,
            feedback_url,
        )

        return {
            "subject": subject,
            "html_content": html_content,
            "plain_content": plain_content,
        }

    def _build_plain_text(
        self,
        update: AzureUpdate,
        result: AnalysisResult,
        language: str = "ko",
        archive_url: str = "",
        feedback_url: str = "",
    ) -> str:
        """Build plain text version of the email."""
        L = get_labels(language)
        archive_url = safe_archive_url(archive_url)
        feedback_url = safe_archive_url(feedback_url)
        urgency_value = (
            result.urgency.value.upper()
            if hasattr(result, "urgency") and result.urgency
            else "MEDIUM"
        )
        relevance_value = result.relevance.value if result.relevance else "unknown"
        one_line = (
            result.one_line_summary
            if hasattr(result, "one_line_summary") and result.one_line_summary
            else ""
        )

        lines = [
            "=" * 60,
            f"AzBrief - Azure Update Analysis Report [{urgency_value}]",
            "=" * 60,
            "",
            update.title,
        ]

        if one_line:
            lines.append(one_line)

        # Relevance evidence
        relevance_evidence = getattr(result, "relevance_evidence", "")
        if relevance_evidence:
            lines.append(f"  {L['relevance_evidence']}: {relevance_evidence}")

        published = update.published_date.strftime("%Y-%m-%d") if update.published_date else "-"
        lines.extend(
            [
                "",
                f"{L['urgency']}: {urgency_value} | {L['relevance']}: {relevance_value}",
                f"{L['published_date']}: {published}",
                f"{L['link']}: {update.link}",
            ]
        )
        if archive_url:
            lines.append(f"{L['archive_shared_original']}: {archive_url}")
        lines.extend(
            [
                "",
                "-" * 40,
                L["analysis_summary"],
                "-" * 40,
                "",
                result.relevance_reason or L["no_analysis"],
                "",
            ]
        )

        # Impact details
        if hasattr(result, "impact_details") and result.impact_details:
            lines.extend(
                [
                    "-" * 40,
                    L["impact_analysis"],
                    "-" * 40,
                    f"  {L['cost']}: {result.impact_details.cost_impact}",
                    f"  {L['security']}: {result.impact_details.security_impact}",
                    f"  {L['performance']}: {result.impact_details.performance_impact}",
                    f"  {L['operational']}: {result.impact_details.operational_impact}",
                    "",
                ]
            )

        # Affected resources (conditional by update category)
        update_cat = getattr(result, "update_category", "new_feature")
        skip_resources_categories = {"new_service", "region_expansion", "sdk_tooling"}
        skip_actions_categories = {"new_service", "region_expansion", "preview"}

        # Opportunity categories use "replaceable resources" label
        opportunity_categories = {"new_feature", "preview"}
        is_opportunity = update_cat in opportunity_categories
        resources_label = L["replaceable_resources"] if is_opportunity else L["affected_resources"]
        no_resources_label = (
            L["no_replaceable_resources"] if is_opportunity else L["no_affected_resources"]
        )

        if update_cat not in skip_resources_categories:
            if result.affected_resources:
                count_display = f"{len(result.affected_resources)}{L['count_suffix']}"
                lines.extend(
                    [
                        "-" * 40,
                        f"{resources_label} ({count_display})",
                        "-" * 40,
                    ]
                )

                for resource in result.affected_resources:
                    name = resource.get("name", "Unknown")
                    res_type = resource.get("type", "Unknown")
                    subscription = resource.get("subscription", resource.get("subscriptionId", ""))
                    rg = resource.get("resourceGroup", "")
                    reason = resource.get("reason", "")
                    location_parts = []
                    if subscription:
                        location_parts.append(f"{L['subscription']}: {subscription}")
                    if rg:
                        location_parts.append(f"RG: {rg}")
                    location_info = " | ".join(location_parts)
                    lines.append(f"  - {name} ({res_type})")
                    if location_info:
                        lines.append(f"    {location_info}")
                    if reason:
                        lines.append(f"    {reason}")
                lines.append("")
            elif update_cat in ("retirement", "feature_change"):
                # Show section header with "none found" for mandatory categories
                lines.extend(
                    [
                        "-" * 40,
                        resources_label,
                        "-" * 40,
                        f"  {no_resources_label}",
                        "",
                    ]
                )

        # Action items (conditional by update category)
        if (
            update_cat not in skip_actions_categories
            and hasattr(result, "action_items")
            and result.action_items
        ):
            count_display = f"{len(result.action_items)}{L['count_suffix']}"
            lines.extend(
                [
                    "-" * 40,
                    f"{L['action_items']} ({count_display})",
                    "-" * 40,
                ]
            )
            for item in result.action_items:
                lines.append(f"  [{item.priority}] {item.task}")
                if item.target_resources:
                    lines.append(f"    {L['target']}: {', '.join(item.target_resources[:5])}")
                if item.procedure:
                    lines.append(f"    {L['procedure']}: {item.procedure}")
                if item.cli_command:
                    lines.append(f"    CLI: {item.cli_command}")
                if item.deadline:
                    lines.append(f"    {L['deadline']}: {item.deadline}")
                if item.risk_if_not_done:
                    lines.append(f"    {L['risk_if_not_done']}: {item.risk_if_not_done}")
                lines.append("")
        elif result.recommendations:
            lines.extend(
                [
                    "-" * 40,
                    L["recommendations"],
                    "-" * 40,
                ]
            )
            for i, rec in enumerate(result.recommendations, 1):
                lines.append(f"  {i}. {rec}")
            lines.append("")

        # Additional checks (open items first, references after)
        if hasattr(result, "additional_checks") and result.additional_checks:
            lines.extend(
                [
                    "-" * 40,
                    L["additional_checks"],
                    "-" * 40,
                ]
            )
            for check in result.additional_checks:
                lines.append(f"  - {check}")
            lines.append("")

        # Reference docs
        if result.reference_docs:
            lines.extend(
                [
                    "-" * 40,
                    L["reference_docs"],
                    "-" * 40,
                ]
            )
            for doc in result.reference_docs[:5]:
                if isinstance(doc, dict):
                    lines.append(f"  - {doc.get('title', 'Document')}")
                    summary = doc.get("description", "") or doc.get("related_content", "")
                    if summary:
                        lines.append(f"    {summary}")
                    related_content = doc.get("related_content", "")
                    if doc.get("description") and related_content != summary:
                        lines.append(f"    {L['doc_context']}: {related_content}")
                    url = doc.get("url", "")
                    if url:
                        lines.append(f"    {url}")
                else:
                    lines.append(f"  - {doc}")
            lines.append("")

        lines.extend(
            [
                "",
                f"{L['disclaimer_title']}: {L['disclaimer_body']}",
                *([f"{L['feedback_link']}: {feedback_url}"] if feedback_url else []),
                "",
                "=" * 60,
                L["footer_auto"],
                "=" * 60,
            ]
        )

        return "\n".join(lines)

    async def send_analysis_report(
        self,
        update: AzureUpdate,
        result: AnalysisResult,
        recipient: Optional[str] = None,
        language: str = "ko",
        batch_stats: Optional[dict] = None,
        archive_url: str = "",
    ) -> bool:
        """Send analysis report via email or print to console.

        Args:
            update: Original Azure Update
            result: Analysis result
            recipient: Optional override for recipient email
            language: Language code for UI labels
            batch_stats: Optional batch filtering stats (total_updates, relevant_count)

        Returns:
            True if report was sent/printed successfully
        """
        if self.settings.report_filtering_enabled and not result.should_notify:
            logger.info("Skipping notification - not relevant", update_id=update.id)
            return False

        email_content = self.build_email_content(
            update,
            result,
            language,
            batch_stats,
            archive_url,
        )

        # Save HTML report to out/ for debugging
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in update.id[:30])
        _save_html_to_out(
            email_content["html_content"],
            f"report_{safe_id}_{language}.html",
        )

        # A console-managed subscriber supplies an explicit recipient even when
        # no static fallback recipient exists in the deployment environment.
        if not self._use_email and not (recipient and self._transport_ready):
            return self._print_to_console(update, result, email_content)

        # Controlled autonomy: withhold auto-dispatch when approval is required.
        # The rendered report is already saved to out/ above; log it as pending so
        # a human can review and send manually. AzBrief only ever sends email, so
        # this is the single delivery gate that makes the agent non-autonomous.
        if self.settings.require_approval_before_send:
            logger.info(
                "email_send_withheld_pending_approval",
                update_id=update.id,
                recipient=recipient or self.settings.email_recipient_address,
                subject=email_content["subject"],
            )
            return self._print_to_console(update, result, email_content)

        recipient = recipient or self.settings.email_recipient_address

        logger.info(
            "Sending analysis report email",
            update_id=update.id,
            recipient=recipient,
            relevance=result.relevance,
        )

        try:
            message = {
                "senderAddress": self.settings.email_sender_address,
                "recipients": {
                    "to": [{"address": recipient}],
                },
                "content": {
                    "subject": email_content["subject"],
                    "html": email_content["html_content"],
                    "plainText": email_content["plain_content"],
                },
            }

            poller = self.client.begin_send(message)
            send_result = poller.result()

            logger.info(
                "Email sent successfully",
                message_id=send_result.get("id"),
                update_id=update.id,
            )
            return True

        except HttpResponseError as e:
            # Retry once on transient errors (429, 503, 500)
            if hasattr(e, "status_code") and e.status_code in (429, 500, 503):
                import asyncio

                logger.warning(
                    "email_send_transient_error_retrying",
                    status_code=e.status_code,
                    update_id=update.id,
                )
                await asyncio.sleep(2)
                try:
                    poller = self.client.begin_send(message)
                    poller.result()
                    logger.info("email_send_retry_succeeded", update_id=update.id)
                    return True
                except Exception:
                    pass
            logger.error(
                "Failed to send email",
                error=str(e),
                update_id=update.id,
            )
            return False
        except Exception as e:
            logger.error(
                "Unexpected error sending email",
                error=str(e),
                update_id=update.id,
            )
            return False

    async def send_to_subscribers(
        self,
        update: AzureUpdate,
        base_result: AnalysisResult,
        analyzer: "AzureUpdateAnalyzer",
        subscribers: list[Subscriber],
        archive_url: str = "",
    ) -> dict[str, bool]:
        """Send customized reports to multiple subscribers.

        Customizes reports for all subscribers in parallel, then sends
        emails in parallel. Subscribers without a role receive the base report.
        Respects subscriber alert_level: critical_only, important_and_above, all.

        Args:
            update: Original Azure Update
            base_result: Base analysis result (from analyze_update)
            analyzer: AzureUpdateAnalyzer instance (for customize_for_subscriber)
            subscribers: List of subscriber profiles

        Returns:
            Dict mapping subscriber email → send success
        """
        import asyncio
        import time

        if self.settings.report_filtering_enabled and not base_result.should_notify:
            logger.info("Skipping subscriber notifications - not relevant", update_id=update.id)
            return {s.email: False for s in subscribers}

        # Phase 1: Customize all reports in parallel
        _t0 = time.time()

        def _scope_key(scope: AnalysisScope) -> tuple[tuple[str, ...], ...]:
            return (
                tuple(sorted(value.casefold() for value in scope.management_groups)),
                tuple(sorted(value.casefold() for value in scope.subscriptions)),
                tuple(sorted(value.casefold() for value in scope.resource_groups)),
            )

        subscriber_scopes = {
            subscriber.email: AnalysisScope.from_subscriber(subscriber)
            for subscriber in subscribers
        }
        unique_scopes: dict[tuple[tuple[str, ...], ...], AnalysisScope] = {}
        for scope in subscriber_scopes.values():
            if scope.is_bounded:
                unique_scopes.setdefault(_scope_key(scope), scope)

        scope_outcomes: dict[tuple[tuple[str, ...], ...], object] = {}
        if unique_scopes:
            semaphore = asyncio.Semaphore(max(1, self.settings.max_concurrent_analyses))

            async def _analyze_scoped(scope: AnalysisScope):
                async with semaphore:
                    return await analyzer.analyze_update(update, scope=scope)

            outcomes = await asyncio.gather(
                *[_analyze_scoped(scope) for scope in unique_scopes.values()],
                return_exceptions=True,
            )
            scope_outcomes = dict(zip(unique_scopes, outcomes))

        async def _customize(sub):
            scope = subscriber_scopes[sub.email]
            source_result = base_result
            if scope.is_bounded:
                source_result = scope_outcomes.get(_scope_key(scope))
                if isinstance(source_result, BaseException) or source_result is None:
                    logger.warning(
                        "subscriber_scoped_analysis_failed",
                        subscriber=sub.email,
                        update_id=update.id,
                        error=(
                            type(source_result).__name__
                            if isinstance(source_result, BaseException)
                            else "missing_result"
                        ),
                    )
                    return None
            try:
                return await analyzer.customize_for_subscriber(source_result, sub, update)
            except Exception as e:
                logger.error("Customization failed", subscriber=sub.email, error=str(e))
                return source_result

        customized_results = await asyncio.gather(*[_customize(s) for s in subscribers])

        _custom_elapsed = time.time() - _t0
        logger.info(
            "All subscriber reports customized",
            count=len(subscribers),
            elapsed=f"{_custom_elapsed:.1f}s",
        )

        # Phase 2: Send all emails in parallel
        _t1 = time.time()

        async def _send(sub, result):
            try:
                if result is None:
                    return False
                if self.settings.report_filtering_enabled and not result.should_notify:
                    logger.info(
                        "Subscriber report skipped - not relevant to role",
                        subscriber=sub.email,
                        name=sub.name,
                        role=sub.role,
                    )
                    return False

                # Priority-based delivery filtering
                alert_level = getattr(sub, "alert_level", "all") or "all"
                urg = result.urgency.value if hasattr(result.urgency, "value") else "medium"
                if alert_level == "critical_only" and urg != "critical":
                    logger.info(
                        "Subscriber report filtered by alert_level",
                        subscriber=sub.email,
                        alert_level=alert_level,
                        urgency=urg,
                    )
                    return False
                if alert_level == "important_and_above" and urg not in ("critical", "high"):
                    logger.info(
                        "Subscriber report filtered by alert_level",
                        subscriber=sub.email,
                        alert_level=alert_level,
                        urgency=urg,
                    )
                    return False

                sent = await self.send_analysis_report(
                    update,
                    result,
                    recipient=sub.email,
                    language=sub.language,
                    archive_url=("" if subscriber_scopes[sub.email].is_bounded else archive_url),
                )
                logger.info(
                    "Subscriber report sent", subscriber=sub.email, name=sub.name, sent=sent
                )
                return sent
            except Exception as e:
                logger.error("Failed to send to subscriber", subscriber=sub.email, error=str(e))
                return False

        send_results = await asyncio.gather(
            *[_send(s, r) for s, r in zip(subscribers, customized_results)]
        )

        _send_elapsed = time.time() - _t1
        logger.info(
            "All subscriber emails sent",
            count=len(subscribers),
            elapsed=f"{_send_elapsed:.1f}s",
        )

        return {s.email: sent for s, sent in zip(subscribers, send_results)}

    def _print_to_console(
        self,
        update: AzureUpdate,
        result: AnalysisResult,
        email_content: dict,
    ) -> bool:
        """Print analysis report to console instead of sending email.

        Args:
            update: Original Azure Update
            result: Analysis result
            email_content: Formatted email content

        Returns:
            True always (console print is always successful)
        """
        logger.info(
            "Email not configured - printing to console",
            update_id=update.id,
            relevance=result.relevance,
        )

        # Print formatted console output
        print("\n" + "=" * 80)
        print("📢 AZBRIEF ANALYSIS REPORT")
        print("=" * 80)
        print(f"\n📌 Subject: {email_content['subject']}")
        print(f"\n🔗 Update URL: {update.link}")
        print(f"📅 Published: {update.published_date}")
        print(f"\n📊 Relevance: {result.relevance.value}")
        print(f"\n💬 Analysis:")
        print("-" * 40)
        print(result.relevance_reason)
        print("-" * 40)

        if result.affected_resources:
            print(f"\n🎯 Affected Resources ({len(result.affected_resources)}):")
            display_limit = 100
            for resource in result.affected_resources[:display_limit]:
                sub = resource.get("subscription", resource.get("subscriptionId", ""))
                rg = resource.get("resourceGroup", "")
                loc_parts = []
                if sub:
                    loc_parts.append(sub)
                if rg:
                    loc_parts.append(rg)
                loc_str = f" [{' / '.join(loc_parts)}]" if loc_parts else ""
                print(
                    f"   - {resource.get('name', 'Unknown')} ({resource.get('type', 'Unknown')}){loc_str}"
                )
            if len(result.affected_resources) > display_limit:
                print(f"   ... and {len(result.affected_resources) - display_limit} more")

        if result.recommendations:
            print(f"\n💡 Recommendations:")
            for i, rec in enumerate(result.recommendations, 1):
                print(f"   {i}. {rec}")

        if result.impact_summary:
            print(f"\n📝 Impact Summary:")
            print(f"   {result.impact_summary[:500]}")

        if result.reference_docs:
            print(f"\n📚 Reference Documents:")
            for doc in result.reference_docs[:10]:
                if isinstance(doc, dict):
                    title = doc.get("title", "Document")
                    url = doc.get("url", "")
                    print(f"   - {title}")
                    print(f"     {url}")
                elif isinstance(doc, str):
                    print(f"   - {doc}")

        print("\n" + "=" * 80)
        print(
            "[Console output mode - set COMMUNICATION_SERVICES_CONNECTION_STRING to enable email]"
        )
        print("=" * 80 + "\n")

        return True

    # ================================================================
    # Daily digest — consolidated email for multiple updates
    # ================================================================

    def _build_retirement_countdown_html(self, language: str = "ko") -> str:
        """Build retirement countdown section for digest email.

        Shows active retirements with D-day countdown, sorted by urgency.

        Args:
            language: Language code for labels

        Returns:
            HTML string (empty if no active retirements)
        """
        from src.agent.history import get_retirement_countdown

        countdowns = get_retirement_countdown()
        if not countdowns:
            return ""

        L = get_labels(language)
        retirement_title = L["retirement_countdown"]

        rows_html = ""
        for item in countdowns[:8]:  # Limit to 8
            days = item.get("days_remaining")
            title = escape_email_text(item.get("title", ""))
            count = item.get("affected_resource_count", 0)
            status = item.get("migration_status", "not_started")

            # Color based on urgency
            if days is not None and days <= 30:
                day_color = EMAIL_COLORS["danger"]
            elif days is not None and days <= 90:
                day_color = EMAIL_COLORS["warning"]
            else:
                day_color = EMAIL_COLORS["success"]

            day_text = f"D-{days}" if days is not None and days >= 0 else "TBD"
            if days is not None and days < 0:
                day_text = f"D+{abs(days)}"
                day_color = EMAIL_COLORS["danger"]

            status_label = {
                "not_started": L["migration_not_started"],
                "in_progress": L["migration_in_progress"],
                "completed": L["migration_completed"],
            }.get(status, L["migration_not_started"])

            rows_html += f"""<tr>
                <td width="80" style="padding: 14px 12px 14px 0; vertical-align: top; font-size: 17px; font-weight: 700; color: {day_color}; border-bottom: 1px solid {EMAIL_COLORS['line']};">{day_text}</td>
                <td style="padding: 14px 0; border-bottom: 1px solid {EMAIL_COLORS['line']}; overflow-wrap: anywhere;">
                    <p style="margin: 0; font-size: 13px; font-weight: 600; color: {EMAIL_COLORS['ink']}; line-height: 1.8;">{title}</p>
                    <p style="margin: 6px 0 0; font-size: 12px; color: {EMAIL_COLORS['muted']};">{L['col_resource']}: {escape_email_text(count)} &middot; {status_label}</p>
                </td>
            </tr>"""

        return format_email_section_html(
            retirement_title,
            '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" '
            f'class="azb-countdown" style="table-layout: fixed; border-collapse: collapse;">{rows_html}</table>',
        )

    def _build_update_detail_html(
        self,
        update: AzureUpdate,
        result: AnalysisResult,
        index: int,
        language: str = "ko",
        archive_url: str = "",
    ) -> str:
        """Build the full analysis detail section for one update inside a digest.

        Includes: header, analysis summary, environment relevance, impact, affected resources,
        action items, references, additional checks.

        Args:
            update: AzureUpdate object
            result: AnalysisResult object
            index: 1-based index of this update in the digest
            language: Language code

        Returns:
            HTML rows to embed inside the digest table.
        """
        L = get_labels(language)
        update_category = getattr(result, "update_category", "new_feature")

        # Build each section via existing helpers
        analysis_html = markdown_to_html(result.relevance_reason or "", strip_headings=True)
        relevance_html = format_relevance_evidence_html(result.relevance_evidence, language)
        timeline_html = format_timeline_html(
            result.action_items if hasattr(result, "action_items") else [],
            update_category,
            language,
        )
        impact_html = format_impact_section_html(
            result.impact_details if hasattr(result, "impact_details") else None,
            language,
            update_category=update_category,
        )
        resources_html = format_affected_resources_html(
            result.affected_resources,
            language,
            update_category=update_category,
        )
        actions_html = format_action_items_html(
            result.action_items if hasattr(result, "action_items") else [],
            result.recommendations,
            language,
            update_category=update_category,
            affected_resources=result.affected_resources,
        )
        checks_html = format_additional_checks_html(
            result.additional_checks if hasattr(result, "additional_checks") else [],
            language,
        )
        refs_html = format_reference_docs_html(result.reference_docs, language)

        return f"""<tr><td class="azb-digest-detail" style="padding: 0;">
<a name="azbrief-detail-{index}" id="azbrief-detail-{index}"></a>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="table-layout: fixed; border-top: 2px solid {EMAIL_COLORS['ink']};">
{format_report_header_html(update, result, language, archive_url, index)}
{format_quick_decision_html(result, language)}
{format_email_section_html(L['analysis_summary'], analysis_html)}
{relevance_html}{timeline_html}{impact_html}{resources_html}{actions_html}{checks_html}{refs_html}
</table></td></tr>"""

    def build_digest_content(
        self,
        items: list[dict],
        date_range: str = "",
        language: str = "ko",
    ) -> dict:
        """Build a single digest email from multiple analysis results.

        Each item in ``items`` is a dict with:
        - ``update``: AzureUpdate
        - ``result``: AnalysisResult or None (if skipped)
        - ``skip_reason``: str (empty if analyzed)

        Args:
            items: List of analyzed/skipped update dicts (max ~10).
            date_range: Display string like "2026-04-15 ~ 2026-04-17".
            language: Language code for UI labels.

        Returns:
            Dict with ``subject``, ``html_content``, ``plain_content``.
        """
        L = get_labels(language)
        feedback_url = build_feedback_page_url(
            self.settings,
            language,
            f"digest:{date_range}" if date_range else "digest",
        )

        # Classify items by importance (high / medium / low)
        high_items = []  # important: directly relevant, high/critical urgency
        medium_items = []  # normal: relevant but moderate, or opportunity
        low_items = []  # FYI: not directly relevant

        for item in items:
            result = item.get("result")
            skip_reason = item.get("skip_reason", "")
            if skip_reason or result is None:
                low_items.append(item)
                continue
            urg = result.urgency.value if hasattr(result.urgency, "value") else "medium"
            rel = result.relevance.value if hasattr(result.relevance, "value") else "unknown"
            imp = getattr(result, "importance", "") or ""
            importance = get_importance_level(urg, rel, imp)
            if importance == "high":
                high_items.append(item)
            elif importance == "medium":
                medium_items.append(item)
            else:
                low_items.append(item)

        high_count = len(high_items)
        medium_count = len(medium_items)
        low_count = len(low_items)

        # --- Subject line ---
        if high_count > 0:
            prefix = (
                L["urgency_prefix_critical"]
                if any(
                    (it["result"].urgency.value if hasattr(it["result"].urgency, "value") else "")
                    == "critical"
                    for it in high_items
                    if it.get("result")
                )
                else L["urgency_prefix_high"]
            )
            subject = f"[AzBrief] {prefix} {L['importance_high']} {high_count} | {date_range}"
        elif medium_count > 0:
            subject = f"[AzBrief] {L['importance_medium']} {medium_count} | {date_range}"
        else:
            subject = f"[AzBrief] {L['importance_low']} {low_count} | {date_range}"
        if len(subject) > 120:
            subject = subject[:119] + "\u2026"

        # --- Ordered list: sorted by importance → impact → job_relevance (high first) ---
        analyzed_items = []
        for group in [high_items, medium_items, low_items]:
            for item in group:
                if item.get("result") and not item.get("skip_reason"):
                    analyzed_items.append(item)
        non_analyzed = [item for item in items if not item.get("result") or item.get("skip_reason")]

        # Fine-grained sort within each importance tier:
        # importance (high>medium>low) → impact_level → job_relevance
        _LEVEL_ORDER = {"high": 0, "medium": 1, "low": 2, "": 3}

        def _sort_key(item):
            r = item.get("result")
            if not r:
                return (3, 3, 3)
            urg = r.urgency.value if hasattr(r.urgency, "value") else "medium"
            rel = r.relevance.value if hasattr(r.relevance, "value") else "unknown"
            imp_raw = getattr(r, "importance", "") or ""
            importance = get_importance_level(urg, rel, imp_raw)
            impact = getattr(r, "impact_level", "") or ""
            job_rel = getattr(r, "job_relevance", "") or ""
            return (
                _LEVEL_ORDER.get(importance, 3),
                _LEVEL_ORDER.get(impact, 3),
                _LEVEL_ORDER.get(job_rel, 3),
            )

        analyzed_items.sort(key=_sort_key)

        # --- Build summary cards with anchor links ---
        cards_html = format_digest_table_header_html(language)
        anchor_idx = 0
        for item in analyzed_items:
            anchor_idx += 1
            cards_html += format_digest_update_card_html(
                item["update"],
                item.get("result"),
                item.get("skip_reason", ""),
                language,
                anchor_index=anchor_idx,
            )
        for item in non_analyzed:
            cards_html += format_digest_update_card_html(
                item["update"],
                item.get("result"),
                item.get("skip_reason", ""),
                language,
                anchor_index=0,
            )

        # --- Build per-update detail sections (all analyzed updates) ---
        details_html = ""
        detail_idx = 0
        for item in analyzed_items:
            detail_idx += 1
            details_html += self._build_update_detail_html(
                item["update"],
                item["result"],
                detail_idx,
                language,
                item.get("archive_url", ""),
            )

        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # --- Build retirement countdown section ---
        scoped_delivery = any(item.get("subscriber_scope_bounded") for item in items)
        retirement_html = "" if scoped_delivery else self._build_retirement_countdown_html(language)

        # --- Assemble full HTML ---
        summary_table = (
            '<a name="azbrief-summary" id="azbrief-summary"></a>'
            '<table cellspacing="0" cellpadding="0" border="0" width="100%" '
            'class="azb-digest-table" style="table-layout: fixed; border-collapse: collapse;">'
            f"{cards_html}</table>"
        )
        html_content = HTML_DIGEST_TEMPLATE.format(
            html_lang=get_language(language).lang_attr,
            document_title=escape_email_text(L["digest_title"]),
            preheader=escape_email_text(L["digest_total"].format(total=len(items))),
            masthead_html=format_email_masthead_html(L["email_report_label"], date_range),
            digest_intro_html=format_digest_intro_html(
                len(items),
                high_count,
                medium_count,
                low_count - len(non_analyzed),
                len(non_analyzed),
                language,
            ),
            summary_table_html=format_email_section_html(L["email_contents"], summary_table),
            retirement_html=retirement_html,
            details_html=details_html,
            footer_html=format_email_footer_html(language, generated_at, feedback_url),
        )

        plain_content = self._build_digest_plain_text(
            items,
            date_range,
            language,
            feedback_url,
        )

        return {
            "subject": subject,
            "html_content": html_content,
            "plain_content": plain_content,
        }

    @staticmethod
    def _build_digest_plain_text(
        items: list[dict],
        date_range: str,
        language: str = "ko",
        feedback_url: str = "",
    ) -> str:
        """Build plain text version of the digest email."""
        L = get_labels(language)
        lines = [
            "=" * 60,
            f"AzBrief — {L['digest_title']}",
            f"{date_range}",
            "=" * 60,
            "",
        ]
        idx = 0
        for item in items:
            idx += 1
            update = item["update"]
            result = item.get("result")
            skip_reason = item.get("skip_reason", "")
            title = update.title[:60]

            if skip_reason or result is None:
                lines.append(f"  {idx}. [SKIP] {title}")
                if skip_reason:
                    lines.append(f"     {skip_reason}")
                lines.append("")
                continue

            urg = result.urgency.value.upper() if hasattr(result.urgency, "value") else "?"
            rel = result.relevance.value if hasattr(result.relevance, "value") else "?"
            one_line = result.one_line_summary or ""
            evidence = getattr(result, "relevance_evidence", "")
            affected = len(result.affected_resources) if result.affected_resources else 0
            actions = len(result.action_items) if hasattr(result, "action_items") else 0

            lines.append(f"  {idx}. [{urg}] {title}")
            lines.append(
                f"     {L['relevance']}: {rel} | {L['affected_resources']}: {affected}{L['count_suffix']} | {L['action_items']}: {actions}{L['count_suffix']}"
            )
            if one_line:
                lines.append(f"     {one_line}")
            if evidence:
                lines.append(f"     {L['relevance_evidence']}: {evidence}")
            lines.append(f"     {update.link}")
            archive_url = safe_archive_url(item.get("archive_url", ""))
            if archive_url:
                lines.append(f"     {L['archive_shared_original']}: {archive_url}")
            lines.append("")

        # --- Detailed analysis per update ---
        lines.extend(["", "=" * 60, ""])
        detail_idx = 0
        for item in items:
            result = item.get("result")
            if not result or item.get("skip_reason"):
                continue
            detail_idx += 1
            update = item["update"]
            urg = result.urgency.value.upper() if hasattr(result.urgency, "value") else "?"
            lines.extend(
                [
                    "-" * 60,
                    f"[{detail_idx}] [{urg}] {update.title}",
                    "-" * 60,
                    "",
                ]
            )
            if result.relevance_reason:
                lines.extend([result.relevance_reason, ""])
            if hasattr(result, "impact_details") and result.impact_details:
                if result.impact_details.cost_impact:
                    lines.append(f"  {L['cost']}: {result.impact_details.cost_impact}")
                if result.impact_details.security_impact:
                    lines.append(f"  {L['security']}: {result.impact_details.security_impact}")
                if result.impact_details.performance_impact:
                    lines.append(
                        f"  {L['performance']}: {result.impact_details.performance_impact}"
                    )
                if result.impact_details.operational_impact:
                    lines.append(
                        f"  {L['operational']}: {result.impact_details.operational_impact}"
                    )
                lines.append("")
            if result.affected_resources:
                lines.append(
                    f"{L['affected_resources']} ({len(result.affected_resources)}{L['count_suffix']}):"
                )
                for res in result.affected_resources:
                    name = res.get("name", "?")
                    reason = res.get("reason", "")
                    lines.append(f"  - {name}" + (f": {reason}" if reason else ""))
                lines.append("")
            if hasattr(result, "action_items") and result.action_items:
                lines.append(
                    f"{L['action_items']} ({len(result.action_items)}{L['count_suffix']}):"
                )
                for ai in result.action_items:
                    task = ai.task if hasattr(ai, "task") else str(ai)
                    dl = ai.deadline if hasattr(ai, "deadline") and ai.deadline else ""
                    lines.append(f"  - {task}" + (f" ({L['deadline']}: {dl})" if dl else ""))
                lines.append("")

        lines.extend(
            [
                "-" * 60,
                f"{L['disclaimer_title']}: {L['disclaimer_body']}",
                *(
                    [f"{L['feedback_link']}: {safe_archive_url(feedback_url)}"]
                    if safe_archive_url(feedback_url)
                    else []
                ),
            ]
        )
        return "\n".join(lines)

    async def send_digest_report(
        self,
        items: list[dict],
        date_range: str = "",
        recipient: Optional[str] = None,
        language: str = "ko",
    ) -> bool:
        """Send a consolidated daily digest email.

        Args:
            items: List of dicts, each with ``update``, ``result``, ``skip_reason``.
            date_range: Display string (e.g. "2026-04-15 ~ 2026-04-17").
            recipient: Optional override for recipient email.
            language: Language code.

        Returns:
            True if sent/printed successfully.
        """
        if not items:
            logger.info("No items for digest — skipping")
            return False

        email_content = self.build_digest_content(items, date_range, language)

        # Save HTML digest to out/ for debugging
        safe_recipient = ""
        if recipient:
            safe_recipient = "_" + recipient.split("@")[0][:15]
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        _save_html_to_out(
            email_content["html_content"],
            f"digest_{language}{safe_recipient}_{ts}.html",
        )

        if not self._use_email and not (recipient and self._transport_ready):
            # Console output
            print("\n" + "=" * 80)
            print(f"📧 {email_content['subject']}")
            print("=" * 80)
            print(email_content["plain_content"])
            print("=" * 80 + "\n")
            return True

        # Controlled autonomy: withhold auto-dispatch when approval is required.
        if self.settings.require_approval_before_send:
            logger.info(
                "digest_send_withheld_pending_approval",
                recipient=recipient or self.settings.email_recipient_address,
                subject=email_content["subject"],
                update_count=len(items),
            )
            print("\n" + "=" * 80)
            print(f"📧 [APPROVAL PENDING] {email_content['subject']}")
            print("   Preview saved to out/. Set REQUIRE_APPROVAL_BEFORE_SEND=false to auto-send.")
            print("=" * 80 + "\n")
            return True

        recipient = recipient or self.settings.email_recipient_address

        logger.info(
            "Sending digest report",
            recipient=recipient,
            update_count=len(items),
        )

        try:
            message = {
                "senderAddress": self.settings.email_sender_address,
                "recipients": {
                    "to": [{"address": recipient}],
                },
                "content": {
                    "subject": email_content["subject"],
                    "html": email_content["html_content"],
                    "plainText": email_content["plain_content"],
                },
            }

            poller = self.client.begin_send(message)
            poller.result()
            logger.info("Digest email sent", recipient=recipient)
            return True

        except HttpResponseError as e:
            logger.error("Failed to send digest", error=str(e))
            return False
        except Exception as e:
            logger.error("Unexpected error sending digest", error=str(e))
            return False
