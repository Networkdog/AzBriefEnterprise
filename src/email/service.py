"""Email service using Azure Communication Services."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from azure.core.exceptions import HttpResponseError
from structlog import get_logger

from src.agent.analyzer import AnalysisResult
from src.config import Subscriber, get_settings

if TYPE_CHECKING:  # analyzer imports this module's package at runtime
    from src.agent.analyzer import AzureUpdateAnalyzer
from src.email.templates import (
    _CLIENT_COMPAT_STYLE,
    _DARK_MODE_STYLE,
    _RESPONSIVE_STYLE,
    FONT_STACK_SANS,
    HTML_EMAIL_TEMPLATE,
    format_action_items_html,
    format_additional_checks_html,
    format_affected_resources_html,
    format_batch_context_html,
    format_digest_table_header_html,
    format_digest_update_card_html,
    format_impact_section_html,
    format_quick_decision_html,
    format_reference_docs_html,
    format_relevance_evidence_html,
    format_timeline_html,
    get_importance_colors,
    get_importance_level,
    get_labels,
    get_relevance_colors,
    get_urgency_colors,
    markdown_to_html,
)
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

    @property
    def client(self):
        """Get or create email client.

        Uses the connection string when one is configured; otherwise
        authenticates to the ACS endpoint with the managed identity, which is
        how the enterprise profile avoids storing an email secret at all.
        """
        if not self._use_email:
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

    def build_email_content(
        self,
        update: AzureUpdate,
        result: AnalysisResult,
        language: str = "ko",
        batch_stats: Optional[dict] = None,
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

        # Get urgency info
        urgency_value = (
            result.urgency.value if hasattr(result, "urgency") and result.urgency else "medium"
        )
        urgency_colors = get_urgency_colors(urgency_value)

        # One line summary
        one_line = (
            result.one_line_summary
            if hasattr(result, "one_line_summary") and result.one_line_summary
            else update.title[:80]
        )

        # Urgency-aware summary background
        urgency_summary_bgs = {
            "critical": "#fef2f2",
            "high": "#fff7ed",
            "medium": "#f0f4f8",
            "low": "#f0fdf4",
        }

        # Build HTML content from professional template
        relevance_value = (
            result.relevance.value if hasattr(result.relevance, "value") else str(result.relevance)
        )
        relevance_colors = get_relevance_colors(relevance_value, language)
        update_category = getattr(result, "update_category", "new_feature")

        html_content = HTML_EMAIL_TEMPLATE.format(
            # Language
            html_lang=get_language(language).lang_attr,
            # Urgency styling
            urgency_bg_color=urgency_colors["bg_color"],
            urgency_badge=urgency_colors["badge"],
            urgency_summary_bg=urgency_summary_bgs.get(urgency_value, "#f0f4f8"),
            # Relevance badge
            relevance_bg_color=relevance_colors["bg_color"],
            relevance_text_color=relevance_colors["text_color"],
            relevance_border_color=relevance_colors["border_color"],
            relevance_label=relevance_colors["label"],
            # Summary
            one_line_summary=one_line,
            # Relevance evidence (why this update was selected)
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
            # Update info
            title=update.title,
            update_type=update.update_type or "Info",
            published_date=(
                update.published_date.strftime("%Y-%m-%d") if update.published_date else "-"
            ),
            link=update.link,
            service_tags_html=self._build_service_tags_html(
                update.azure_services if hasattr(update, "azure_services") else []
            ),
            # Analysis
            analysis_summary=markdown_to_html(result.relevance_reason or "", strip_headings=True),
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
            ),
            # Reference docs (self-contained <tr>)
            reference_docs_section_html=format_reference_docs_html(result.reference_docs, language),
            # Additional checks
            additional_checks_html=format_additional_checks_html(
                result.additional_checks if hasattr(result, "additional_checks") else [],
                language,
            ),
            # Template labels
            label_update_type=L["update_type"],
            label_analysis_summary=L["analysis_summary"],
            label_detail_link=L["detail_link"],
            label_disclaimer_title=L["disclaimer_title"],
            label_disclaimer_body=L["disclaimer_body"],
            label_footer_generated=L["footer_generated"],
            label_footer_basis=L["footer_basis"],
            # Footer
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
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
        plain_content = self._build_plain_text(update, result, language)

        return {
            "subject": subject,
            "html_content": html_content,
            "plain_content": plain_content,
        }

    @staticmethod
    def _build_service_tags_html(services: list[str]) -> str:
        """Build inline service tag badges for the email header.

        Args:
            services: List of Azure service names

        Returns:
            HTML string with service tags; empty string if no services.
        """
        if not services:
            return ""
        tags = []
        for svc in services[:4]:
            tags.append(
                f'<span style="display: inline-block; background-color: #1a2d47; '
                f"color: #8db4d8; padding: 2px 8px; border-radius: 3px; "
                f"font-size: 12px; font-weight: 600; margin-right: 4px; "
                f'margin-top: 6px; letter-spacing: 0.2px;">{svc}</span>'
            )
        if len(services) > 4:
            tags.append(
                f'<span style="display: inline-block; color: #5b7a96; '
                f'font-size: 12px; margin-top: 6px;">+{len(services) - 4}</span>'
            )
        return f'<div style="margin-top: 2px;">{"".join(tags)}</div>'

    def _build_plain_text(
        self, update: AzureUpdate, result: AnalysisResult, language: str = "ko"
    ) -> str:
        """Build plain text version of the email."""
        L = get_labels(language)
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
            lines.append(f"  → {relevance_evidence}")

        published = update.published_date.strftime("%Y-%m-%d") if update.published_date else "-"
        lines.extend(
            [
                "",
                f"{L['urgency']}: {urgency_value} | {L['relevance']}: {relevance_value}",
                f"{L['published_date']}: {published}",
                f"{L['link']}: {update.link}",
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

        email_content = self.build_email_content(update, result, language, batch_stats)

        # Save HTML report to out/ for debugging
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in update.id[:30])
        _save_html_to_out(
            email_content["html_content"],
            f"report_{safe_id}_{language}.html",
        )

        # If email is not configured, print to console
        if not self._use_email:
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

        async def _customize(sub):
            try:
                return await analyzer.customize_for_subscriber(base_result, sub, update)
            except Exception as e:
                logger.error("Customization failed", subscriber=sub.email, error=str(e))
                return base_result

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
                    update, result, recipient=sub.email, language=sub.language
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
            title = item.get("title", "")[:60]
            count = item.get("affected_resource_count", 0)
            status = item.get("migration_status", "not_started")
            rd = item.get("retirement_date", "")

            # Color based on urgency
            if days is not None and days <= 30:
                day_color = "#dc2626"
                day_bg = "#fef2f2"
            elif days is not None and days <= 90:
                day_color = "#d97706"
                day_bg = "#fffbeb"
            else:
                day_color = "#16a34a"
                day_bg = "#f0fdf4"

            day_text = f"D-{days}" if days is not None and days >= 0 else "TBD"
            if days is not None and days < 0:
                day_text = f"D+{abs(days)}"
                day_color = "#dc2626"
                day_bg = "#fef2f2"

            status_label = {
                "not_started": "⬜",
                "in_progress": "🟨",
                "completed": "✅",
            }.get(status, "⬜")

            rows_html += f"""<tr>
                <td style="padding: 6px 8px; font-size: 14px; border-bottom: 1px solid #eee;">
                    <span style="display: inline-block; background: {day_bg}; color: {day_color}; padding: 2px 8px; border-radius: 4px; font-weight: 700; font-size: 12px;">{day_text}</span>
                </td>
                <td style="padding: 6px 8px; font-size: 14px; border-bottom: 1px solid #eee; color: #333;">{title}</td>
                <td style="padding: 6px 8px; font-size: 14px; border-bottom: 1px solid #eee; text-align: center; color: #555;">{count}</td>
                <td style="padding: 6px 8px; font-size: 14px; border-bottom: 1px solid #eee; text-align: center;">{status_label}</td>
            </tr>"""

        return f"""<tr>
            <td class="azb-pad" style="padding: 16px 32px 12px 32px; border-bottom: 1px solid #e2e7ed;">
                <p style="margin: 0 0 8px 0; font-size: 18px; font-weight: 700; color: #dc2626; text-transform: uppercase; letter-spacing: 0.3px;">⏰ {retirement_title}</p>
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="border: 1px solid #e2e7ed; border-radius: 6px; overflow: hidden;">
                    <tr style="background-color: #f1f5f9;">
                        <th style="padding: 6px 8px; font-size: 12px; font-weight: 600; color: #64748b; text-align: left;">D-Day</th>
                        <th style="padding: 6px 8px; font-size: 12px; font-weight: 600; color: #64748b; text-align: left;">Update</th>
                        <th style="padding: 6px 8px; font-size: 12px; font-weight: 600; color: #64748b; text-align: center;">Resources</th>
                        <th style="padding: 6px 8px; font-size: 12px; font-weight: 600; color: #64748b; text-align: center;">Status</th>
                    </tr>
                    {rows_html}
                </table>
            </td>
        </tr>"""

    def _build_update_detail_html(
        self,
        update: AzureUpdate,
        result: AnalysisResult,
        index: int,
        language: str = "ko",
    ) -> str:
        """Build the full analysis detail section for one update inside a digest.

        Includes: header, analysis summary, impact, affected resources,
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
        urgency_value = (
            result.urgency.value if hasattr(result.urgency, "value") else str(result.urgency)
        )
        urgency_colors = get_urgency_colors(urgency_value)
        relevance_value = (
            result.relevance.value if hasattr(result.relevance, "value") else str(result.relevance)
        )
        relevance_colors = get_relevance_colors(relevance_value, language)
        update_category = getattr(result, "update_category", "new_feature")

        one_line = (
            result.one_line_summary
            if hasattr(result, "one_line_summary") and result.one_line_summary
            else update.title[:80]
        )
        published = update.published_date.strftime("%Y-%m-%d") if update.published_date else "-"

        # Build each section via existing helpers
        analysis_html = markdown_to_html(result.relevance_reason or "", strip_headings=True)
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
        )
        checks_html = format_additional_checks_html(
            result.additional_checks if hasattr(result, "additional_checks") else [],
            language,
        )
        refs_html = format_reference_docs_html(result.reference_docs, language)

        return f"""
                    <!-- ══ Update #{index} detail ══ -->
                    <tr>
                        <td style="padding: 0;">
                            <a name="azbrief-detail-{index}" id="azbrief-detail-{index}"></a>
                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="border-top: 3px solid {urgency_colors['bg_color']};">
                                <!-- Detail header -->
                                <tr>
                                    <td class="azb-detail-hdr azb-pad" style="background-color: #1e3a5f; padding: 14px 32px 12px 32px;">
                                        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" class="azb-stack">
                                            <tr>
                                                <td>
                                                    <span style="display: inline-block; background-color: {urgency_colors['bg_color']}; color: #fff; padding: 2px 8px; border-radius: 3px; font-size: 12px; font-weight: 700; letter-spacing: 0.3px;">{urgency_colors['badge']}</span>
                                                    <span style="display: inline-block; background-color: {relevance_colors['bg_color']}; color: {relevance_colors['text_color']}; border: 1px solid {relevance_colors['border_color']}; padding: 2px 8px; border-radius: 3px; font-size: 12px; font-weight: 600; margin-left: 4px;">{relevance_colors['label']}</span>
                                                </td>
                                                <td align="right" class="azb-detail-subtitle azb-stack-tail" style="color: #9bb3cf; font-size: 12px;">{L['update_type']}: {update.update_type or 'Info'} &middot; {published}</td>
                                            </tr>
                                        </table>
                                        <p style="margin: 8px 0 0 0; color: #ffffff; font-size: 18px; font-weight: 600; line-height: 1.4;">{update.title}</p>
                                        <p class="azb-detail-subtitle" style="margin: 4px 0 0 0; color: #c0cfe0; font-size: 14px; line-height: 1.4;">{one_line}</p>
                                        <p style="margin: 6px 0 0 0;"><a href="{update.link}" class="azb-link" style="color: #7db8e8; font-size: 12px; text-decoration: none;">{L['detail_link']}</a></p>
                                    </td>
                                </tr>
                                <!-- Analysis body -->
                                <tr>
                                    <td class="azb-section azb-pad" style="padding: 18px 32px 14px 32px;">
                                        <p class="azb-heading" style="margin: 0 0 8px 0; font-size: 18px; font-weight: 700; color: #1a1a1a; text-transform: uppercase; letter-spacing: 0.3px;">{L['analysis_summary']}</p>
                                        <div class="azb-text" style="font-size: 16px; color: #333; line-height: 1.7;">{analysis_html}</div>
                                    </td>
                                </tr>
                                {timeline_html}
                                {impact_html}
                                {resources_html}
                                {actions_html}
                                {checks_html}
                                {refs_html}
                            </table>
                        </td>
                    </tr>"""

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

        # --- Determine overall urgency color ---
        if high_count > 0:
            overall_urgency = "high"
            for it in high_items:
                if it.get("result"):
                    urg = (
                        it["result"].urgency.value if hasattr(it["result"].urgency, "value") else ""
                    )
                    if urg == "critical":
                        overall_urgency = "critical"
                        break
        elif medium_count > 0:
            overall_urgency = "medium"
        else:
            overall_urgency = "low"
        urgency_colors = get_urgency_colors(overall_urgency)

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
            )

        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # --- Build retirement countdown section ---
        retirement_html = self._build_retirement_countdown_html(language)

        # --- Assemble full HTML ---
        html_content = f"""<!DOCTYPE html>
<html lang="{language}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="light only">
    <title>{L['digest_title']}</title>
{_DARK_MODE_STYLE}{_CLIENT_COMPAT_STYLE}{_RESPONSIVE_STYLE}</head>
<body class="azb-body" style="margin: 0; padding: 0; font-family: {FONT_STACK_SANS}; background-color: #f3f5f8; line-height: 1.6; -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%;">
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" class="azb-body" style="background-color: #f3f5f8;">
        <tr>
            <td align="center" class="azb-outer" style="padding: 20px 10px 28px 10px;">
                <!--[if mso]><table role="presentation" cellspacing="0" cellpadding="0" border="0" width="640" align="center"><tr><td><![endif]-->
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" align="center" class="azb-card" style="max-width: 640px; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.06);">

                    <!-- Accent bar -->
                    <tr><td style="background-color: {urgency_colors['bg_color']}; height: 4px; font-size: 0; line-height: 0;">&nbsp;</td></tr>

                    <!-- Header -->
                    <tr>
                        <td class="azb-header azb-pad" style="background-color: #0f1b2d; padding: 20px 32px 16px 32px;">
                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                                <tr>
                                    <td style="vertical-align: middle;">
                                        <span style="color: #ffffff; font-size: 26px; font-weight: 700; letter-spacing: -0.3px;">AzBrief</span>
                                    </td>
                                    <td align="right" style="vertical-align: middle;">
                                        <span class="azb-text-secondary" style="color: #7a8fa3; font-size: 14px;">{date_range}</span>
                                    </td>
                                </tr>
                            </table>
                            <p style="margin: 10px 0 0 0; color: #ffffff; font-size: 20px; font-weight: 600;">{L['digest_title']}</p>
                        </td>
                    </tr>

                    <!-- Retirement countdown (if any active retirements) -->
                    {retirement_html}

                    <!-- Update summary table (sorted by importance, linked to details) -->
                    <tr>
                        <td class="azb-pad" style="padding: 18px 32px 16px 32px;">
                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" class="azb-panel" style="border: 1px solid #d0d7de; border-radius: 6px; border-collapse: separate; overflow: hidden;">
                                {cards_html}
                            </table>
                        </td>
                    </tr>

                    <!-- Per-update detailed analysis -->
                    {details_html}

                    <!-- Footer -->
                    <tr>
                        <td class="azb-footer azb-pad" style="background-color: #f8f9fb; padding: 14px 32px; border-top: 1px solid #e2e7ed;">
                            <p style="margin: 0; font-size: 12px; color: #a0a8b4; line-height: 1.6;">{L['disclaimer_title']}: {L['disclaimer_body']}</p>
                            <p style="margin: 6px 0 0 0; font-size: 12px; color: #b8bfc8;">{L['footer_generated']} &middot; AzBrief AI Agent &middot; {L['footer_basis']} &middot; {generated_at}</p>
                        </td>
                    </tr>

                </table>
                <!--[if mso]></td></tr></table><![endif]-->
            </td>
        </tr>
    </table>
</body>
</html>"""

        plain_content = self._build_digest_plain_text(items, date_range, language)

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
                lines.append(f"     → {evidence}")
            lines.append(f"     {update.link}")
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

        if not self._use_email:
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
