"""Professional HTML email templates for AzBrief reports."""

import re

# Aliased: several renderers below bind a local name `html` for their output.
from html import escape as _escape
from html import unescape as _unescape
from urllib.parse import urlparse

# Canonical UI label bundles live in src/i18n/labels/<code>.py. Re-exported so the
# renderers below (and their callers) keep importing get_labels from templates.
from src.i18n.labels import get_labels

# Pre-compiled regex patterns for inline markdown formatting
_RE_BOLD = re.compile(r"\*\*(.+?)\*\*")
_RE_INLINE_CODE = re.compile(r"`([^`]+)`")
# Markdown link: [text](url). Only http(s)/in-page anchor URLs are linkified (XSS guard).
_RE_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
# Pre-compiled regex patterns for markdown_to_html line parsing
_RE_BLOCKQUOTE = re.compile(r"^>\s?(.*)")
_RE_HEADING = re.compile(r"^(#{1,4})\s+(.+)$")
_RE_BULLET = re.compile(r"^\s*[-*•]\s+(.+)$")
_RE_NUMBERED = re.compile(r"^\s*(\d+)\.\s+(.+)$")
# Pipe table: a header row followed by a |---|---| separator row.
_RE_TABLE_ROW = re.compile(r"^\|(.+)\|\s*$")
_RE_TABLE_SEP = re.compile(r"^\|[\s:\-|]+\|\s*$")

_EMAIL_LINK_DOMAINS = (
    "microsoft.com",
    "azure.com",
    "github.com",
    "azureweekly.info",
    "aka.ms",
)

# Action-item safety gate. The border tints the whole card; the badge colour is
# also used for the findings block so a blocked item reads as one unit.
_VERIFY_COLOR = {
    "verified": "#1e7d44",
    "caution": "#b7791f",
    "blocked": "#c0392b",
    "unverified": "#6b7785",
}
_VERIFY_BORDER = {
    "verified": "#e4e9ee",
    "caution": "#f0d9a8",
    "blocked": "#f0b8b0",
    "unverified": "#e4e9ee",
}

# Categories that add a new capability instead of changing existing behaviour.
# Their report sections are framed as an opportunity, not an impact.
CAPABILITY_CATEGORIES = (
    "new_feature",
    "new_service",
    "region_expansion",
    "preview",
    "sdk_tooling",
)

# ============================================================================
# Type scale
# ============================================================================
# Body copy sits at 16px — the browser/email default, and the size mail clients
# render normal text at. Every other size is a step on the same scale, so the
# hierarchy stays proportional if the base ever moves.
FONT_SIZE_PX: dict[str, int] = {
    "meta": 12,  # badges, table headers, timestamps, fine print   (0.75x)
    "secondary": 14,  # table cells, action detail lines, CLI blocks    (0.875x)
    "body": 16,  # prose, list items, concept boxes                (1x)
    "heading": 18,  # section labels                                  (1.125x)
    "title": 20,  # update titles                                   (1.25x)
    "masthead": 26,  # AzBrief wordmark                                (1.625x)
}

# ============================================================================
# Dark mode — DISABLED
# ============================================================================
# All colors are optimized for a white (#ffffff) background only.
# Dark mode is intentionally not supported — email clients' auto-dark-mode
# (Gmail, Outlook) will apply their own color inversions which are acceptable.
# Removing the @media block prevents conflicts between our carefully chosen
# light-mode colors and the auto-dark-mode heuristics.
# ============================================================================

_DARK_MODE_STYLE = """
<style type="text/css">
  /* AzBrief: light-mode only — no dark mode overrides */
</style>
"""

# Escaped version for use inside str.format() templates ({{ }} instead of { })
_DARK_MODE_STYLE_ESCAPED = _DARK_MODE_STYLE.replace("{", "{{").replace("}", "}}")

# ============================================================================
# Enterprise email client rendering hardening (Outlook / Windows)
# ============================================================================
# Outlook on Windows renders via the Microsoft Word engine and inserts spurious
# spacing around tables; the mso-table-lspace/rspace resets remove it. Windows
# Outlook DOES honor <head> <style> (unlike Gmail, which strips it), so these
# global table/img resets belong here. Long CLI commands and resource IDs must
# wrap instead of forcing a horizontal scroll that breaks the fixed-width card.
# ============================================================================

_CLIENT_COMPAT_STYLE = """
<style type="text/css">
  table { mso-table-lspace: 0pt; mso-table-rspace: 0pt; }
  td { mso-line-height-rule: exactly; }
  img { -ms-interpolation-mode: bicubic; border: 0; outline: none; text-decoration: none; }
  .azb-cli, .azb-code, .azb-mono { word-break: break-word; overflow-wrap: break-word; }
</style>
"""

_CLIENT_COMPAT_STYLE_ESCAPED = _CLIENT_COMPAT_STYLE.replace("{", "{{").replace("}", "}}")

# ============================================================================
# Responsive layout (hybrid: fluid card + media query overrides)
# ============================================================================
# The card itself is fluid (width="100%" + max-width: 640px), so it already
# shrinks in clients that strip <style> (e.g. Gmail app with a non-Gmail
# account). The media queries below add the layout changes that fluid width
# alone cannot express: reduced gutters, stacked two-column rows, and narrower
# metric columns. Windows Outlook ignores @media entirely — the MSO ghost table
# around the card pins it to 640px there instead.
#
# The min-width queries grow the card on desktop so the extra width goes to the
# content instead of the gray backdrop. At the 16px body size 640px holds ~36
# Korean characters per line and 900px ~50, which is the comfortable ceiling —
# past that the line length costs more than the recovered space is worth.
# ============================================================================

_RESPONSIVE_STYLE = """
<style type="text/css">
  @media only screen and (max-width: 640px) {
    .azb-outer { padding: 10px 6px 18px 6px !important; }
    .azb-pad { padding-left: 16px !important; padding-right: 16px !important; }
    /* Text + right-aligned badge rows stack instead of squeezing the text. */
    .azb-stack td { display: block !important; width: 100% !important;
      text-align: left !important; padding-left: 0 !important; }
    .azb-stack .azb-stack-tail { padding-top: 6px !important; }
    /* Digest metric columns shrink so the title column stays readable. */
    .azb-col-metric { width: 46px !important;
      padding-left: 4px !important; padding-right: 4px !important; }
    .azb-col-metric span { font-size: 12px !important; padding: 2px 4px !important; }
    /* Let the reason column share the width instead of claiming a fixed 60%. */
    .azb-col-reason { width: auto !important; }
    /* Quick decision grid: 4 columns become label-over-value blocks. */
    .azb-qd td { display: block !important; width: auto !important;
      padding-top: 0 !important; padding-bottom: 0 !important; }
    .azb-qd-label { padding-top: 6px !important; }
  }
  @media only screen and (max-width: 400px) {
    .azb-pad { padding-left: 12px !important; padding-right: 12px !important; }
  }
  @media only screen and (min-width: 800px) {
    .azb-card { max-width: 760px !important; }
    .azb-outer { padding-top: 26px !important; }
    .azb-tl-task { max-width: 200px !important; }
  }
  @media only screen and (min-width: 1100px) {
    .azb-card { max-width: 900px !important; }
    .azb-pad { padding-left: 44px !important; padding-right: 44px !important; }
    /* Impact dimensions pair up so the strip stops growing downward. */
    .azb-impact tr { display: inline-table !important; width: 50% !important;
      vertical-align: top !important; }
    .azb-impact td { border-top: 0 !important; }
  }
</style>
"""

_RESPONSIVE_STYLE_ESCAPED = _RESPONSIVE_STYLE.replace("{", "{{").replace("}", "}}")


def markdown_to_html(text: str, strip_headings: bool = False) -> str:
    """Convert markdown-formatted text to HTML for email rendering.

    Supports:
    - Line breaks
    - Bold (**text**)
    - Inline code (`code`)
    - Headings (### heading)
    - Bullet points (- item, * item, • item)
    - Numbered lists (1. item)
    - Blockquotes / concept boxes (> text)
    - Pipe tables (| a | b | with a |---|---| separator row)

    Args:
        text: Markdown source.
        strip_headings: Drop heading lines instead of rendering them. Used for the
            analysis body, where template-style subheadings must never appear.

    Returns:
        HTML string.
    """
    if not text:
        return ""

    _SPACER = '<div style="height: 8px;"></div>'

    lines = text.split("\n")
    html_parts = []
    in_list = False
    list_type = None  # 'ul' or 'ol'
    in_blockquote = False
    blockquote_lines: list[str] = []

    def _flush_blockquote():
        """Render accumulated blockquote lines as a concept box."""
        nonlocal in_blockquote, blockquote_lines
        if not blockquote_lines:
            return
        content = "<br>".join(_inline_format(l) for l in blockquote_lines)
        html_parts.append(
            '<div class="azb-concept" style="margin: 10px 0; padding: 10px 14px; '
            "background-color: #f0f5fa; border-left: 3px solid #5b9bd5; "
            "border-radius: 0 4px 4px 0; font-size: 14px; color: #3b4a5a; "
            'line-height: 1.6;">'
            f"{content}</div>"
        )
        in_blockquote = False
        blockquote_lines = []

    skip = 0
    for idx, line in enumerate(lines):
        if skip > 0:
            skip -= 1
            continue

        stripped = line.strip()

        # Pipe table (| a | b | followed by |---|---|)
        if (
            _RE_TABLE_ROW.match(stripped)
            and idx + 1 < len(lines)
            and _RE_TABLE_SEP.match(lines[idx + 1].strip())
        ):
            if in_blockquote:
                _flush_blockquote()
            if in_list:
                html_parts.append(f"</{list_type}>")
                in_list = False
                list_type = None
            table_html, consumed = _render_md_table(lines, idx)
            html_parts.append(table_html)
            skip = consumed - 1
            continue

        # Blockquote lines (> text)
        bq_match = _RE_BLOCKQUOTE.match(stripped)
        if bq_match:
            if in_list:
                html_parts.append(f"</{list_type}>")
                in_list = False
                list_type = None
            in_blockquote = True
            blockquote_lines.append(bq_match.group(1))
            continue

        # If we were in a blockquote and hit a non-blockquote line, flush it
        if in_blockquote:
            _flush_blockquote()

        # Skip empty lines
        if not stripped:
            if in_list:
                html_parts.append(f"</{list_type}>")
                in_list = False
                list_type = None
            if not html_parts or html_parts[-1] != _SPACER:
                html_parts.append(_SPACER)
            continue

        # Headings (### / ## / #)
        heading_match = _RE_HEADING.match(stripped)
        if heading_match:
            if in_list:
                html_parts.append(f"</{list_type}>")
                in_list = False
                list_type = None
            if strip_headings:
                continue
            level = len(heading_match.group(1))
            heading_text = _inline_format(heading_match.group(2))
            sizes = {
                1: f"{FONT_SIZE_PX['title']}px",
                2: f"{FONT_SIZE_PX['heading']}px",
                3: f"{FONT_SIZE_PX['body']}px",
                4: f"{FONT_SIZE_PX['secondary']}px",
            }
            font_size = sizes.get(level, f"{FONT_SIZE_PX['heading']}px")
            html_parts.append(
                f'<p style="margin: 12px 0 6px 0; font-size: {font_size}; '
                f'font-weight: 600; color: #0078d4;">{heading_text}</p>'
            )
            continue

        # Bullet points (- item, * item, • item)
        bullet_match = _RE_BULLET.match(stripped)
        if bullet_match:
            if not in_list or list_type != "ul":
                if in_list:
                    html_parts.append(f"</{list_type}>")
                html_parts.append(
                    '<ul style="margin: 4px 0; padding-left: 20px; list-style-type: disc;">'
                )
                in_list = True
                list_type = "ul"
            item_text = _inline_format(bullet_match.group(1))
            html_parts.append(
                f'<li class="azb-text" style="margin: 3px 0; font-size: 16px; color: #333; '
                f'line-height: 1.6;">{item_text}</li>'
            )
            continue

        # Numbered lists (1. item)
        num_match = _RE_NUMBERED.match(stripped)
        if num_match:
            if not in_list or list_type != "ol":
                if in_list:
                    html_parts.append(f"</{list_type}>")
                html_parts.append('<ol style="margin: 4px 0; padding-left: 20px;">')
                in_list = True
                list_type = "ol"
            item_text = _inline_format(num_match.group(2))
            html_parts.append(
                f'<li class="azb-text" style="margin: 3px 0; font-size: 16px; color: #333; '
                f'line-height: 1.6;">{item_text}</li>'
            )
            continue

        # Regular paragraph
        if in_list:
            html_parts.append(f"</{list_type}>")
            in_list = False
            list_type = None
        formatted = _inline_format(stripped)
        html_parts.append(
            f'<p class="azb-text" style="margin: 4px 0; font-size: 16px; color: #333; '
            f'line-height: 1.7;">{formatted}</p>'
        )

    # Close any remaining open list or blockquote
    if in_blockquote:
        _flush_blockquote()
    if in_list:
        html_parts.append(f"</{list_type}>")

    return "\n".join(html_parts)


def _split_table_row(line: str) -> list[str]:
    """Split a markdown pipe-table row into trimmed cell texts."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _render_md_table(lines: list[str], start: int) -> tuple[str, int]:
    """Render a markdown pipe table starting at ``lines[start]`` as HTML.

    Args:
        lines: All lines of the markdown document.
        start: Index of the header row (the separator row must be at start + 1).

    Returns:
        Tuple of (HTML string, number of lines consumed).
    """
    headers = _split_table_row(lines[start])
    body: list[list[str]] = []
    i = start + 2  # skip header + separator
    while i < len(lines):
        row = lines[i].strip()
        if not _RE_TABLE_ROW.match(row) or _RE_TABLE_SEP.match(row):
            break
        body.append(_split_table_row(row))
        i += 1

    th_style = (
        f"padding: 6px 9px; text-align: left; font-size: {FONT_SIZE_PX['meta']}px; "
        "font-weight: 600; color: #0f4c81; background-color: #eef3f8; "
        "border-bottom: 1px solid #d0d7de; border-right: 1px solid #e2e7ed;"
    )
    td_style = (
        f"padding: 6px 9px; text-align: left; font-size: {FONT_SIZE_PX['secondary']}px; "
        "color: #333; line-height: 1.55; border-bottom: 1px solid #eceff2; "
        "border-right: 1px solid #f0f2f5; vertical-align: top;"
    )

    head_cells = "".join(f'<th style="{th_style}">{_inline_format(h)}</th>' for h in headers)
    rows_html = []
    for cells in body:
        # Pad/trim so a malformed row cannot break the table layout.
        cells = (cells + [""] * len(headers))[: len(headers)]
        tds = "".join(f'<td style="{td_style}">{_inline_format(c)}</td>' for c in cells)
        rows_html.append(f"<tr>{tds}</tr>")

    table_html = (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" '
        'class="azb-mdtable" style="margin: 8px 0; border-collapse: collapse; '
        'border: 1px solid #d0d7de; border-radius: 4px;">'
        f"<tr>{head_cells}</tr>"
        f"{''.join(rows_html)}"
        "</table>"
    )
    return table_html, i - start


def _linkify_md(match: "re.Match[str]") -> str:
    """Render a markdown link as a safe <a> tag.

    Only http(s) and in-page anchor (``#``) URLs are linkified to prevent
    ``javascript:``/``data:`` injection in email HTML. Unsupported schemes fall
    back to the link text alone (the URL is dropped).
    """
    label, url = match.group(1), match.group(2)
    href = safe_email_href(url, allow_fragment=True)
    if not href:
        return label
    return (
        f'<a href="{href}" class="azb-link" '
        f'style="color: #1a6fb5; text-decoration: none;">{label}</a>'
    )


def _inline_format(text: str) -> str:
    """Apply inline markdown formatting (link, bold, code) to text."""
    text = _escape(str(text), quote=True)
    # Markdown links first so the URL is not mangled by the bold/code passes
    text = _RE_MD_LINK.sub(_linkify_md, text)
    # Bold: **text** (emphasis styling)
    text = _RE_BOLD.sub(r'<strong style="color: #0f4c81;">\1</strong>', text)
    # Inline code: `code`
    text = _RE_INLINE_CODE.sub(
        r'<code class="azb-code" style="background-color: #f0f0f0; padding: 1px 5px; '
        r'border-radius: 3px; font-family: monospace; font-size: 14px;">\1</code>',
        text,
    )
    return text


def escape_email_text(value: object) -> str:
    """Escape an untrusted value before inserting it into email HTML."""
    return _escape(str(value), quote=True)


def safe_email_href(url: str, allow_fragment: bool = False) -> str:
    """Return an escaped allow-listed HTTPS URL or in-message fragment."""
    raw = _unescape(str(url or "")).strip()
    if allow_fragment and re.fullmatch(r"#[A-Za-z0-9_.:-]+", raw):
        return raw
    parsed = urlparse(raw)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.port
        or not any(
            hostname == domain or hostname.endswith(f".{domain}") for domain in _EMAIL_LINK_DOMAINS
        )
    ):
        return ""
    return _escape(raw, quote=True)


def safe_archive_url(archive_url: str) -> str:
    """Return a normalized HTTPS archive URL, or an empty string when unsafe."""
    if not archive_url:
        return ""
    parsed = urlparse(archive_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return archive_url


def format_archive_link_html(archive_url: str, language: str = "ko") -> str:
    """Render an optional authenticated archive link using an HTTPS URL only."""
    archive_url = safe_archive_url(archive_url)
    if not archive_url:
        return ""
    label = _escape(get_labels(language)["archive_shared_original"])
    safe_url = _escape(archive_url, quote=True)
    return (
        '<p style="margin: 7px 0 0 0;">'
        f'<a href="{safe_url}" class="azb-link" '
        'style="color: #5b9bd5; font-size: 12px; text-decoration: none;">'
        f"{label}</a></p>"
    )


# ============================================================================
# HTML Email Template
# ============================================================================
# Structure (before → after):
#   - [Removed] Separate "Update Info" section → integrated into header
#   - [Removed] Long relevance_label description → short badge
#   - [Removed] Fixed 4-column impact grid → dynamic display of applicable items
#   - [Removed] 3-column affected resources table → card with current/recommended settings
#   - [Removed] impact_summary field → fully replaced by impact_details
#
# Final structure:
#   1. Header — urgency badge + update title/type/date/link
#   2. One-line summary banner
#   3. Analysis summary — relevance badge + detailed analysis text
#   4. Impact analysis — cost/security/performance/operations (only items with values)
#   5. Affected resources / Replaceable resources — card with current/recommended settings
#      (label changes to "replaceable" for new_feature/preview categories)
#   6. Action items — cards by priority
#   7. Reference docs — links + related content
#   8. Additional checks — warning (only when present)
#   9. Footer
# ============================================================================

# System fonts only: email clients block webfonts, and bundled families are not installed.
FONT_STACK_SANS = (
    "'Segoe UI', -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', "
    "'Malgun Gothic', Roboto, 'Noto Sans CJK KR', 'Helvetica Neue', Arial, sans-serif"
)
FONT_STACK_MONO = "Consolas, Menlo, 'DejaVu Sans Mono', 'Courier New', monospace"

HTML_EMAIL_TEMPLATE = (
    """<!DOCTYPE html>
<html lang="{html_lang}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="light only">
    <title>AzBrief Analysis Report</title>
"""
    + _DARK_MODE_STYLE_ESCAPED
    + _CLIENT_COMPAT_STYLE_ESCAPED
    + _RESPONSIVE_STYLE_ESCAPED
    + """</head>
<body class="azb-body" style="margin: 0; padding: 0; font-family: """
    + FONT_STACK_SANS
    + """; background-color: #f3f5f8; line-height: 1.6; -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%;">
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" class="azb-body" style="background-color: #f3f5f8;">
        <tr>
            <td align="center" class="azb-outer" style="padding: 20px 10px 28px 10px;">
                <!--[if mso]><table role="presentation" cellspacing="0" cellpadding="0" border="0" width="640" align="center"><tr><td><![endif]-->
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" align="center" class="azb-card" style="max-width: 640px; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.06);">

                    <!-- Accent bar -->
                    <tr>
                        <td style="background-color: {urgency_bg_color}; height: 4px; font-size: 0; line-height: 0;">&nbsp;</td>
                    </tr>

                    <!-- Header -->
                    <tr>
                        <td class="azb-header azb-pad" style="background-color: #0f1b2d; padding: 20px 32px 18px 32px;">
                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                                <tr>
                                    <td style="vertical-align: middle;">
                                        <span style="color: #ffffff; font-size: 26px; font-weight: 700; letter-spacing: -0.3px;">AzBrief</span>
                                    </td>
                                    <td align="right" style="vertical-align: middle;">
                                        <span style="display: inline-block; background-color: {urgency_bg_color}; color: #fff; padding: 4px 12px; border-radius: 3px; font-size: 12px; font-weight: 700; letter-spacing: 0.5px;">{urgency_badge}</span>
                                    </td>
                                </tr>
                            </table>
                            <p style="margin: 14px 0 0 0; color: #ffffff; font-size: 20px; font-weight: 600; line-height: 1.45;">{title}</p>
                            <p style="margin: 8px 0 0 0; color: #7a8fa3; font-size: 14px;">{label_update_type}: {update_type} &middot; {published_date} &middot; <a href="{link}" class="azb-link" style="color: #5b9bd5; text-decoration: none;">{label_detail_link}</a></p>
                            {archive_link_html}
                        </td>
                    </tr>

                    <!-- Executive summary + relevance badge -->
                    <tr>
                        <td class="azb-summary azb-border azb-pad" style="background-color: {urgency_summary_bg}; padding: 14px 32px; border-bottom: 1px solid #e2e7ed;">
                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" class="azb-stack">
                                <tr>
                                    <td style="vertical-align: middle;">
                                        <p class="azb-text" style="margin: 0; font-size: 16px; color: #1a1a1a; font-weight: 500; line-height: 1.55;">{one_line_summary}</p>
                                    </td>
                                    <td align="right" class="azb-stack-tail" style="vertical-align: middle; white-space: nowrap; padding-left: 12px;">
                                        <span style="display: inline-block; background-color: {relevance_bg_color}; color: {relevance_text_color}; border: 1px solid {relevance_border_color}; padding: 2px 8px; border-radius: 3px; font-size: 12px; font-weight: 700; letter-spacing: 0.3px;">{relevance_label}</span>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- Relevance evidence (why this update was selected) -->
                    {relevance_evidence_html}

                    <!-- Batch context (filtering stats) -->
                    {batch_context_html}

                    <!-- Quick decision card -->
                    {quick_decision_html}

                    <!-- Analysis -->
                    <tr>
                        <td class="azb-section azb-pad" style="padding: 22px 32px 18px 32px;">
                            <p class="azb-heading" style="margin: 0 0 10px 0; font-size: 18px; font-weight: 700; color: #1a1a1a; text-transform: uppercase; letter-spacing: 0.3px;">{label_analysis_summary}</p>
                            <div class="azb-text" style="font-size: 16px; color: #333; line-height: 1.7;">{analysis_summary}</div>
                        </td>
                    </tr>

                    <!-- Key dates timeline -->
                    {timeline_html}

                    <!-- Impact (compact) -->
                    {impact_section_html}

                    <!-- Affected resources -->
                    {affected_resources_section_html}

                    <!-- Action items -->
                    {action_items_section_html}

                    <!-- Additional checks -->
                    {additional_checks_html}

                    <!-- References -->
                    {reference_docs_section_html}

                    <!-- Footer -->
                    <tr>
                        <td class="azb-footer azb-pad" style="background-color: #f8f9fb; padding: 14px 32px; border-top: 1px solid #e2e7ed;">
                            <p style="margin: 0; font-size: 12px; color: #a0a8b4; line-height: 1.6;">{label_disclaimer_title}: {label_disclaimer_body}</p>
                            <p style="margin: 6px 0 0 0; font-size: 12px; color: #b8bfc8;">{label_footer_generated} &middot; AzBrief AI Agent &middot; {label_footer_basis} &middot; {generated_at}</p>
                        </td>
                    </tr>

                </table>
                <!--[if mso]></td></tr></table><![endif]-->
            </td>
        </tr>
    </table>
</body>
</html>"""
)


# ============================================================================
# Color / badge helpers
# ============================================================================


def get_urgency_colors(urgency: str) -> dict:
    """Get color scheme for urgency level."""
    colors = {
        "critical": {
            "bg_color": "#dc2626",
            "text_color": "#dc2626",
            "badge": "CRITICAL",
        },
        "high": {
            "bg_color": "#ea580c",
            "text_color": "#ea580c",
            "badge": "HIGH",
        },
        "medium": {
            "bg_color": "#d97706",
            "text_color": "#d97706",
            "badge": "MEDIUM",
        },
        "low": {
            "bg_color": "#16a34a",
            "text_color": "#16a34a",
            "badge": "LOW",
        },
    }
    return colors.get(urgency.lower(), colors["medium"])


def get_relevance_colors(relevance: str, language: str = "ko") -> dict:
    """Get color scheme for relevance status.

    label displays only short badge text.
    (Before: long description sentence → overlapped with detailed analysis text)
    """
    L = get_labels(language)
    colors = {
        "relevant": {
            "bg_color": "#e8f5e9",
            "border_color": "#4caf50",
            "text_color": "#2e7d32",
            "label": L["relevance_relevant"],
        },
        "opportunity": {
            "bg_color": "#fff8e1",
            "border_color": "#ffc107",
            "text_color": "#f57f17",
            "label": L["relevance_opportunity"],
        },
        "not_relevant": {
            "bg_color": "#e3f2fd",
            "border_color": "#2196f3",
            "text_color": "#1565c0",
            "label": L["relevance_not_relevant"],
        },
        "unknown": {
            "bg_color": "#fce4ec",
            "border_color": "#e91e63",
            "text_color": "#c2185b",
            "label": L["relevance_unknown"],
        },
    }
    return colors.get(relevance.lower(), colors["unknown"])


def get_importance_level(urgency: str, relevance: str, importance: str = "") -> str:
    """Derive importance level for email classification (high/medium/low).

    Uses the LLM-assessed importance field when available, otherwise falls back
    to the legacy urgency × relevance derivation.

    Args:
        urgency: Urgency level (critical, high, medium, low)
        relevance: Relevance status (relevant, opportunity, not_relevant, unknown)
        importance: LLM-assessed importance (high, medium, low) — preferred source

    Returns:
        Importance level: "high", "medium", or "low"
    """
    # Prefer LLM-assessed importance when available
    if importance and importance.lower() in ("high", "medium", "low"):
        return importance.lower()

    # Fallback: derive from urgency × relevance (backward compatibility)
    urgency = urgency.lower()
    relevance = relevance.lower()

    if relevance in ("relevant", "unknown") and urgency in ("critical", "high"):
        return "high"
    if relevance in ("relevant", "opportunity", "unknown") and urgency in ("medium", "low"):
        return "medium"
    if relevance == "opportunity" and urgency in ("critical", "high"):
        return "medium"
    return "low"


def get_importance_colors(importance: str, language: str = "ko") -> dict:
    """Get color scheme for importance level.

    Args:
        importance: Importance level (high, medium, low)
        language: Language code for label text

    Returns:
        Dict with bg_color, text_color, border_color, dot_color, label.
    """
    L = get_labels(language)
    colors = {
        "high": {
            "bg_color": "#fef2f2",
            "text_color": "#991b1b",
            "border_color": "#dc2626",
            "dot_color": "#dc2626",
            "label": L["importance_high"],
        },
        "medium": {
            "bg_color": "#fffbeb",
            "text_color": "#92400e",
            "border_color": "#d97706",
            "dot_color": "#d97706",
            "label": L["importance_medium"],
        },
        "low": {
            "bg_color": "#f0fdf4",
            "text_color": "#166534",
            "border_color": "#16a34a",
            "dot_color": "#9ca3af",
            "label": L["importance_low"],
        },
    }
    return colors.get(importance.lower(), colors["low"])


# ============================================================================
# Section formatters
# ============================================================================


def format_impact_section_html(
    impact_details,
    language: str = "ko",
    update_category: str = "new_feature",
) -> str:
    """Format impact analysis as a compact horizontal strip.

    Args:
        impact_details: ImpactSummary object or None
        language: Language code for UI labels
        update_category: Update category — capability categories are labelled
            as an opportunity rather than an impact

    Returns:
        Complete <tr> HTML block; empty string if nothing meaningful to show.
    """
    if not impact_details:
        return ""

    L = get_labels(language)
    section_label = (
        L["opportunity_analysis"]
        if update_category in CAPABILITY_CATEGORIES
        else L["impact_analysis"]
    )

    # Skip default/empty impact values (multilingual)
    skip_values = {
        "해당 없음",
        "없음",  # ko
        "not applicable",
        "none",
        "n/a",  # en
        "該当なし",
        "なし",  # ja
        "",
    }

    fields = [
        ("cost", L["cost"], "#2e7d32"),
        ("security", L["security"], "#d84315"),
        ("performance", L["performance"], "#1565c0"),
        ("operational", L["operational"], "#6a1b9a"),
    ]

    items = []
    for key, label, color in fields:
        value = getattr(impact_details, f"{key}_impact", "")
        if value and value.strip().lower() not in {v.lower() for v in skip_values}:
            items.append((label, color, escape_email_text(value)))

    if not items:
        return ""

    html = f"""
    <tr>
        <td class="azb-pad" style="padding: 0 32px 16px 32px;">
            <p class="azb-heading" style="margin: 0 0 8px 0; font-size: 18px; font-weight: 700; color: #1a1a1a; text-transform: uppercase; letter-spacing: 0.3px;">{section_label}</p>
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" class="azb-impact" style="background-color: #f8f9fb; border-radius: 6px; border: 1px solid #e8ecf0;">
    """
    for i, (label, color, value) in enumerate(items):
        border_top = " border-top: 1px solid #e8ecf0;" if i > 0 else ""
        html += f"""
                <tr>
                    <td class="azb-impact-label" style="padding: 8px 12px 8px 14px;{border_top} vertical-align: top; width: 1%; white-space: nowrap;">
                        <span style="font-size: 12px; font-weight: 700; color: {color}; text-transform: uppercase; letter-spacing: 0.3px;">{label}</span>
                    </td>
                    <td class="azb-impact-value" style="padding: 8px 14px;{border_top} vertical-align: top;">
                        <span style="font-size: 14px; color: #333; line-height: 1.5;">{value}</span>
                    </td>
                </tr>
        """
    html += """
            </table>
        </td>
    </tr>
    """
    return html


def format_affected_resources_html(
    resources: list,
    language: str = "ko",
    update_category: str = "new_feature",
) -> str:
    """Format affected resources as a full data grid table.

    For categories where affected resources are not applicable (new_service,
    region_expansion, sdk_tooling), returns empty string regardless
    of input data.

    For opportunity categories (new_feature, preview), the section label
    changes to "replaceable resources" to reflect that these are existing
    resources that could benefit from the new capability.

    All resources are displayed without truncation. Columns are determined
    dynamically based on available data (reason, subscription, resource group).

    Args:
        resources: List of resource dicts with name, type, resourceGroup,
            subscription, reason
        language: Language code for UI labels
        update_category: Update category — sections hidden for non-applicable categories

    Returns:
        Complete <tr> HTML block; empty string if no resources or category not applicable.
    """
    # Categories where affected resources section is not applicable
    if update_category in ("new_service", "region_expansion", "sdk_tooling"):
        return ""

    L = get_labels(language)

    # Opportunity categories use "replaceable resources" label
    opportunity_categories = ("new_feature", "preview")
    is_opportunity = update_category in opportunity_categories
    section_label = L["replaceable_resources"] if is_opportunity else L["affected_resources"]
    empty_label = L["no_replaceable_resources"] if is_opportunity else L["no_affected_resources"]

    # For mandatory categories (retirement, feature_change), show section even when empty
    if not resources:
        mandatory_categories = ("retirement", "feature_change")
        if update_category in mandatory_categories:
            return f"""
    <tr>
        <td class="azb-pad" style="padding: 0 32px 16px 32px;">
            <p class="azb-heading" style="margin: 0 0 8px 0; font-size: 18px; font-weight: 700; color: #1a1a1a; text-transform: uppercase; letter-spacing: 0.3px;">{section_label}</p>
            <p class="azb-text-muted" style="margin: 0; font-size: 14px; color: #8c96a3; font-style: italic;">{empty_label}</p>
        </td>
    </tr>
    """
        return ""

    count = len(resources)
    count_display = f"{count}{L['count_suffix']}"

    # Determine which optional columns are needed
    has_reason = any(r.get("reason", "") for r in resources)

    # Table cell styles
    hdr = (
        "font-size: 12px; font-weight: 700; color: #5b6a7a; "
        "text-transform: uppercase; letter-spacing: 0.4px; "
        "padding: 6px 8px; border-bottom: 2px solid #d0d7de; "
        "background-color: #f1f3f6; white-space: nowrap;"
    )
    cell = (
        "font-size: 14px; color: #333; padding: 5px 8px; "
        "border-bottom: 1px solid #edf0f3; vertical-align: top; "
        "line-height: 1.45; word-break: break-word;"
    )
    # The reason is a single value for the whole (possibly grouped) row, so it
    # reads better centred against the stacked resource names.
    reason_cell = cell.replace("vertical-align: top;", "vertical-align: middle;")

    def _short_type(res: dict) -> str:
        """Last segment of an ARM resource type ("…/runbooks" → "runbooks")."""
        res_type = res.get("type") or "Unknown"
        return res_type.split("/")[-1] if "/" in res_type else res_type

    # Measured on 319 real rows: 73.8% of tables contain a SINGLE resource type,
    # because one update usually hits one kind of resource. Repeating that type
    # on every row costs a line per resource for no information, so it is lifted
    # into the column header and only rendered per-row when types actually differ.
    distinct_types = {_short_type(r) for r in resources}
    uniform_type = distinct_types.pop() if len(distinct_types) == 1 else ""
    # The header style is uppercase, but resource types are camelCase
    # ("storageAccounts") — uppercasing them hurts readability and disagrees
    # with the per-row rendering, so the type keeps its original casing.
    resource_header = L["col_resource"]
    if uniform_type:
        resource_header += (
            ' <span style="text-transform: none; font-weight: 600; color: #6b7785;">'
            f"&middot; {escape_email_text(uniform_type)}</span>"
        )

    html = f"""
    <tr>
        <td class="azb-pad" style="padding: 0 32px 16px 32px;">
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                <tr>
                    <td><p class="azb-heading" style="margin: 0 0 8px 0; font-size: 18px; font-weight: 700; color: #1a1a1a; text-transform: uppercase; letter-spacing: 0.3px;">{section_label}</p></td>
                    <td align="right"><span style="font-size: 14px; color: #5b9bd5; font-weight: 600;">{count_display}</span></td>
                </tr>
            </table>
            <div style="overflow-x: auto;">
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" class="azb-panel" style="border: 1px solid #d0d7de; border-radius: 6px; border-collapse: separate; overflow: hidden; min-width: 100%;">
                <tr>
                    <th class="azb-th" style="{hdr} text-align: left;">{resource_header}</th>
    """
    if has_reason:
        html += (
            f'<th class="azb-th azb-col-reason" style="{hdr} text-align: left; width: 60%;">'
            f'{L["impact_reason"]}</th>\n'
        )
    html += "</tr>\n"

    # Group resources that share the SAME impact reason into a single row.
    # Resources with an empty reason are never merged — each keeps its own row.
    def _resource_entry_html(res: dict) -> str:
        """Render one resource as two lines: name, then scope (and type if mixed).

        Scope is ALWAYS rendered as ``subscription / resource group`` — no
        labels, since the slash-separated pair is self-evident — with a
        placeholder when a value is missing, so every row keeps the same shape.
        """
        name = escape_email_text(res.get("name", "Unknown"))
        subscription = (
            res.get("subscription")
            or res.get("subscriptionName")
            or res.get("subscriptionId")
            or ""
        )
        rg = res.get("resourceGroup") or ""

        if subscription or rg:
            scope = (
                f"{escape_email_text(subscription or L['unknown_scope'])} / "
                f"{escape_email_text(rg or L['unknown_scope'])}"
            )
        else:
            # Both missing — say it once instead of repeating the placeholder.
            scope = escape_email_text(L["unknown_scope"])
        if not uniform_type:
            scope += f" &middot; {escape_email_text(_short_type(res))}"

        out = f'<span style="font-weight: 600;">{name}</span>'
        out += f'<br><span style="font-size: 12px; color: #8c96a3;">{scope}</span>'
        return out

    groups: dict = {}
    for idx, res in enumerate(resources):
        reason_key = (res.get("reason") or "").strip()
        # Empty reasons get a unique key so they are never merged together.
        key = reason_key if reason_key else f"\x00__no_reason__{idx}"
        groups.setdefault(key, []).append(res)

    for gi, group in enumerate(groups.values()):
        reason = group[0].get("reason", "") or ""
        bg = "#ffffff" if gi % 2 == 0 else "#f9fafb"
        row_class = "azb-cell-even" if gi % 2 == 0 else "azb-cell-odd"

        html += f'<tr class="{row_class}" style="background-color: {bg};">'
        html += f'<td class="azb-cell azb-text" style="{cell}">'
        if len(group) == 1:
            html += _resource_entry_html(group[0])
        else:
            # Multiple resources share the same reason — stack them in one cell so
            # the reason is shown once. No group-size badge: it rendered only on
            # grouped rows, which made the resource column look inconsistent.
            for j, res in enumerate(group):
                divider = "" if j == len(group) - 1 else "border-bottom: 1px dashed #e4e9ee;"
                html += f'<div style="padding: 4px 0; {divider}">{_resource_entry_html(res)}</div>'
        html += "</td>"
        if has_reason:
            html += (
                f'<td class="azb-cell azb-text azb-col-reason" '
                f'style="{reason_cell}">{escape_email_text(reason)}</td>'
            )
        html += "</tr>\n"

    html += """
            </table>
            </div>
        </td>
    </tr>
    """
    return html


_RE_PROC_MD_STEP = re.compile(r"^\s*(?:[-*\u2022\u00b7]|\d+[.)])\s+(?P<body>.+)$")
_RE_PROC_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_RE_PROC_INLINE_ENUM = re.compile(r"\((\d+)\)\s*")


def _split_procedure(procedure: str) -> list[tuple[str, bool]]:
    """Break a procedure blob into renderable steps.

    Returns ``(text, is_sub_step)`` pairs. An explicit markdown list wins
    outright; otherwise each sentence becomes a top-level step and an inline
    ``(1) ... (2) ...`` enumeration is nested under the clause introducing it.
    A decimal point never ends a sentence, so "TLS 1.2" survives intact.
    """
    text = (procedure or "").strip()
    if not text:
        return []

    md_steps = []
    for line in text.splitlines():
        match = _RE_PROC_MD_STEP.match(line)
        if match:
            md_steps.append(match.group("body").strip())
    if len(md_steps) >= 2:
        return [(step, False) for step in md_steps]

    steps: list[tuple[str, bool]] = []
    for sentence in _RE_PROC_SENTENCE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        # split() on a capturing group yields [lead, "1", body, "2", body, ...],
        # so two or more enumerated items need at least five parts.
        parts = _RE_PROC_INLINE_ENUM.split(sentence)
        if len(parts) >= 5:
            lead = parts[0].strip()
            if lead:
                steps.append((lead, False))
            for body in parts[2::2]:
                body = body.strip().rstrip(",\uff0c\u3001").strip()
                if body:
                    steps.append((body, True))
            continue
        steps.append((sentence, False))
    return steps


def _format_verification_badge(status: str, language: str) -> str:
    """Render the safety-gate badge shown next to an action item's title.

    Returns an empty string when verification did not run, so reports produced
    with the gate disabled render exactly as before.
    """
    if not status:
        return ""
    L = get_labels(language)
    label = L.get(f"verify_{status}")
    if not label:
        return ""
    color = _VERIFY_COLOR.get(status, "#6b7785")
    return (
        '<span class="azb-verify" style="display: inline-block; margin-left: 6px; '
        f"font-size: {FONT_SIZE_PX['meta']}px; font-weight: 700; color: {color}; "
        f"border: 1px solid {color}; border-radius: 8px; padding: 1px 6px; "
        f'vertical-align: middle; white-space: nowrap;">{label}</span>'
    )


def format_action_items_html(
    action_items: list,
    recommendations: list = None,
    language: str = "ko",
    update_category: str = "new_feature",
) -> str:
    """Format action items as a self-contained <tr> section.

    For categories where action items are not applicable (new_service,
    region_expansion, preview), returns empty string regardless of input data.

    Args:
        action_items: List of ActionItem objects
        recommendations: Fallback list of string recommendations
        language: Language code for UI labels
        update_category: Update category — sections hidden for non-applicable categories

    Returns:
        Complete <tr> HTML block; empty string if nothing to show.
    """
    # Categories where action items section is not applicable
    if update_category in ("new_service", "region_expansion", "preview"):
        return ""

    L = get_labels(language)
    items = action_items if action_items else []

    # Build inner content
    inner = ""

    if not items and recommendations:
        # Only show text-style recommendations when there are NO structured action items
        # to prevent duplicate rendering of the same information
        for i, rec in enumerate(recommendations, 1):
            inner += f"""
            <div class="azb-panel" style="background-color: #f8f9fb; border-radius: 5px; padding: 10px 12px; margin-bottom: 6px; border: 1px solid #e4e9ee;">
                <p class="azb-text" style="margin: 0; font-size: 14px; color: #333;"><strong>{i}.</strong> {escape_email_text(rec)}</p>
            </div>
            """
    elif items:
        for step_num, item in enumerate(items, 1):
            task = escape_email_text(item.task if hasattr(item, "task") else str(item))
            procedure = item.procedure if hasattr(item, "procedure") else ""
            cli_command = item.cli_command if hasattr(item, "cli_command") else ""
            estimated_time = item.estimated_time if hasattr(item, "estimated_time") else ""
            deadline = item.deadline if hasattr(item, "deadline") else ""
            risk = item.risk_if_not_done if hasattr(item, "risk_if_not_done") else ""
            targets = item.target_resources if hasattr(item, "target_resources") else []
            precaution = item.precaution if hasattr(item, "precaution") else ""
            rollback = item.rollback if hasattr(item, "rollback") else ""
            reference_url = getattr(item, "reference_url", "") or ""
            verify_status = getattr(item, "verification_status", "") or ""
            verify_notes = list(getattr(item, "verification_notes", []) or [])
            card_border = _VERIFY_BORDER.get(verify_status, "#e4e9ee")
            verify_badge = _format_verification_badge(verify_status, language)

            inner += f"""
            <div class="azb-action" style="background-color: #f8f9fb; border-radius: 5px; padding: 12px 14px; margin-bottom: 8px; border: 1px solid {card_border};">
                <p class="azb-action-title" style="margin: 0 0 4px 0; font-size: 16px; font-weight: 600; color: #1a1a1a; line-height: 1.5;"><span style="display: inline-block; background-color: #0078d4; color: #fff; font-size: 12px; font-weight: 700; padding: 2px 6px; border-radius: 10px; margin-right: 6px; vertical-align: middle; min-width: 14px; text-align: center;">{step_num}</span>{task}{verify_badge}</p>
            """

            if targets:
                t_str = ", ".join(escape_email_text(target) for target in targets[:3])
                if len(targets) > 3:
                    t_str += f" {L['remaining_targets'].format(n=len(targets) - 3)}"
                inner += f'<p class="azb-text-secondary" style="margin: 2px 0; font-size: 14px; color: #6b7785;">{L["target"]}: {t_str}</p>'

            if procedure:
                steps = _split_procedure(procedure)
                if len(steps) <= 1:
                    inner += f'<p class="azb-text" style="margin: 2px 0; font-size: 14px; color: #444; line-height: 1.5;">{escape_email_text(procedure)}</p>'
                else:
                    ordinal = 0
                    for step_text, is_sub in steps:
                        if is_sub:
                            inner += (
                                f'<p class="azb-text" style="margin: 1px 0 1px 20px; font-size: 14px; '
                                f'color: #444; line-height: 1.5;">&bull; {escape_email_text(step_text)}</p>'
                            )
                        else:
                            ordinal += 1
                            inner += (
                                f'<p class="azb-text" style="margin: 3px 0; font-size: 14px; '
                                f'color: #444; line-height: 1.5;"><strong>{ordinal}.</strong> {escape_email_text(step_text)}</p>'
                            )

            if cli_command:
                inner += (
                    f'<div class="azb-cli" style="margin: 4px 0; font-size: 14px; color: #1a1a1a; '
                    f"font-family: {FONT_STACK_MONO}; "
                    f"background-color: #f5f6f8; padding: 6px 10px; border-radius: 3px; "
                    f"border: 1px solid #e4e9ee; line-height: 1.6; "
                    f'white-space: pre-wrap;">{escape_email_text(cli_command)}</div>'
                )

            meta_parts = []
            if deadline:
                meta_parts.append(f"{L['deadline']}: {escape_email_text(deadline)}")
            if estimated_time:
                meta_parts.append(f"{L['estimated']}: {escape_email_text(estimated_time)}")
            if meta_parts:
                inner += f'<p class="azb-action-meta" style="margin: 2px 0; font-size: 12px; color: #98a3af;">{" &middot; ".join(meta_parts)}</p>'

            if risk:
                inner += f'<p style="margin: 4px 0 0 0; font-size: 14px; color: #c0392b; font-weight: 600;">{L["risk_if_not_done"]}: {escape_email_text(risk)}</p>'

            if precaution:
                inner += (
                    f'<p style="margin: 4px 0 0 0; font-size: 12px; color: #5b6a7a;">'
                    f'<span style="font-weight: 600;">{L["precaution"]}:</span> {escape_email_text(precaution)}</p>'
                )

            if rollback:
                inner += (
                    f'<p style="margin: 2px 0 0 0; font-size: 12px; color: #5b6a7a;">'
                    f'<span style="font-weight: 600;">{L["rollback"]}:</span> {escape_email_text(rollback)}</p>'
                )

            # reference_url comes from LLM output — only http(s) may become an anchor.
            safe_reference_url = safe_email_href(reference_url)
            if safe_reference_url:
                inner += (
                    f'<p style="margin: 2px 0 0 0; font-size: 12px; color: #5b6a7a;">'
                    f'<span style="font-weight: 600;">{L["action_reference"]}:</span> '
                    f'<a href="{safe_reference_url}" class="azb-link" '
                    f'style="color: #1a6fb5; text-decoration: none; word-break: break-all;">'
                    f"{escape_email_text(reference_url)}</a></p>"
                )

            if verify_notes:
                # The gate's findings sit last so the reader has already seen what
                # the item asks for before reading why it is disputed. Notes quote
                # untrusted text (LLM verdicts, withheld commands), so they are
                # escaped rather than markdown-formatted.
                note_color = _VERIFY_COLOR.get(verify_status, "#5b6a7a")
                findings = "<br>".join(f"&middot; {_escape(str(n))}" for n in verify_notes[:4])
                inner += (
                    f'<p style="margin: 4px 0 0 0; font-size: 12px; color: {note_color}; '
                    f'line-height: 1.6; word-break: break-word;">'
                    f'<span style="font-weight: 700;">{L["verification"]}:</span><br>'
                    f"{findings}</p>"
                )

            inner += "</div>"

    if not inner:
        return ""

    return f"""
    <tr>
        <td class="azb-pad" style="padding: 0 32px 16px 32px;">
            <p class="azb-heading" style="margin: 0 0 8px 0; font-size: 18px; font-weight: 700; color: #1a1a1a; text-transform: uppercase; letter-spacing: 0.3px;">{L['action_items']}</p>
            {inner}
        </td>
    </tr>
    """


def format_reference_docs_html(docs: list, language: str = "ko") -> str:
    """Format reference documents as a self-contained <tr> section.

    Args:
        docs: List of doc dicts (title, url, related_content)
        language: Language code for UI labels

    Returns:
        Complete <tr> HTML block; empty string if no documents.
    """
    if not docs:
        return ""

    L = get_labels(language)
    inner = ""
    for doc in docs[:5]:
        if isinstance(doc, dict):
            title = doc.get("title", "Document")
            url = doc.get("url", "#")
            context = doc.get("related_content", "")
        else:
            title = str(doc)
            url = "#"
            context = ""

        safe_url = safe_email_href(url)
        safe_title = escape_email_text(title)
        if safe_url:
            inner += f'<p style="margin: 0 0 2px 0; font-size: 14px;"><a href="{safe_url}" class="azb-link" style="color: #1a6fb5; text-decoration: none;">{safe_title} &rarr;</a></p>'
        else:
            inner += f'<p style="margin: 0 0 2px 0; font-size: 14px;">{safe_title}</p>'
        if context:
            inner += f'<p class="azb-text-secondary" style="margin: 0 0 6px 0; font-size: 12px; color: #6b7785; padding-left: 8px;">{L["doc_context"]}: {escape_email_text(context)}</p>'
        else:
            inner += '<div style="height: 4px;"></div>'

    return f"""
    <tr>
        <td class="azb-pad" style="padding: 0 32px 16px 32px;">
            <p class="azb-heading" style="margin: 0 0 6px 0; font-size: 18px; font-weight: 700; color: #1a1a1a; text-transform: uppercase; letter-spacing: 0.3px;">{L['reference_docs']}</p>
            {inner}
        </td>
    </tr>
    """


def format_additional_checks_html(checks: list, language: str = "ko") -> str:
    """Format additional checks as HTML section (hidden when empty).

    Args:
        checks: List of check description strings
        language: Language code for UI labels

    Returns:
        Complete <tr> HTML block; empty string if no checks.
    """
    if not checks:
        return ""

    L = get_labels(language)
    html = f"""
    <tr>
        <td class="azb-pad" style="padding: 0 36px 20px 36px;">
            <p class="azb-checks-title" style="margin: 0 0 12px 0; font-size: 18px; font-weight: 700; color: #b45309;">{L['additional_checks']}</p>
            <div class="azb-checks" style="background-color: #fffbeb; border-radius: 6px; padding: 12px 14px; border: 1px solid #fde68a;">
    """

    for check in checks:
        html += f'<p style="margin: 0 0 6px 0; font-size: 14px; color: #78350f; line-height: 1.55;">• {escape_email_text(check)}</p>'

    html += """
            </div>
        </td>
    </tr>
    """

    return html


def format_relevance_evidence_html(evidence: str, language: str = "ko") -> str:
    """Format relevance evidence as a compact info bar.

    Shows WHY this update was selected for the admin's environment,
    with a reference to actual resource names/counts from Resource Graph.

    Args:
        evidence: Relevance evidence text (1-2 sentences)
        language: Language code for UI labels

    Returns:
        Complete <tr> HTML block; empty string if no evidence.
    """
    if not evidence:
        return ""

    L = get_labels(language)
    return f"""
    <tr>
        <td class="azb-pad" style="padding: 16px 32px;">
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" class="azb-info-bar" style="background-color: #eef6ff; border-left: 3px solid #0078d4;">
                <tr>
                    <td style="padding: 8px 12px;">
                        <span style="font-size: 12px; font-weight: 700; color: #0078d4; text-transform: uppercase; letter-spacing: 0.3px;">{L['relevance_evidence']}</span>
                        <p class="azb-text" style="margin: 2px 0 0 0; font-size: 14px; color: #1a1a1a; line-height: 1.5;">{_inline_format(evidence)}</p>
                    </td>
                </tr>
            </table>
        </td>
    </tr>
    """


def format_batch_context_html(
    total_updates: int,
    relevant_count: int,
    language: str = "ko",
) -> str:
    """Format batch filtering context as a compact info line.

    Shows how many updates were analyzed and how many were relevant,
    providing transparency about AzBrief's filtering process.

    Args:
        total_updates: Total RSS updates analyzed in this batch
        relevant_count: Number of updates deemed relevant
        language: Language code for UI labels

    Returns:
        Complete <tr> HTML block; empty string if no batch context.
    """
    if total_updates <= 0:
        return ""

    L = get_labels(language)
    text = L["batch_context"].format(total=total_updates, relevant=relevant_count)
    return f"""
    <tr>
        <td class="azb-pad" style="padding: 4px 32px 0 32px;">
            <p style="margin: 0; font-size: 12px; color: #8c96a3; letter-spacing: 0.2px;">{text}</p>
        </td>
    </tr>
    """


def _no_action_needed(language: str = "ko") -> str:
    """Return placeholder HTML for when no action items exist."""
    L = get_labels(language)
    return (
        '<p style="color: #8c96a3; font-size: 14px; font-style: italic;">'
        f"{L['no_action_needed']}</p>"
    )


def format_quick_decision_html(
    result,
    language: str = "ko",
) -> str:
    """Format a compact Quick Decision card showing key facts at a glance.

    Args:
        result: AnalysisResult object
        language: Language code for UI labels

    Returns:
        Complete <tr> HTML block; empty string for not_relevant updates.
    """
    L = get_labels(language)

    relevance_value = (
        result.relevance.value if hasattr(result.relevance, "value") else str(result.relevance)
    )
    if relevance_value == "not_relevant":
        return ""

    affected = result.affected_resources if result.affected_resources else []
    action_items = result.action_items if hasattr(result, "action_items") else []
    count = len(affected)

    # Scope
    if count > 0:
        types = set()
        for r in affected:
            t = r.get("type", "")
            if t:
                types.add(t.split("/")[-1] if "/" in t else t)
        type_str = ", ".join(escape_email_text(value) for value in list(types)[:3])
        scope_text = (
            f"{count}{L['count_suffix']} {type_str}" if type_str else f"{count}{L['count_suffix']}"
        )
    else:
        scope_text = L["no_affected_resources"]

    # Action needed
    has_action = len(action_items) > 0
    action_text = L["yes"] if has_action else L["no"]
    action_color = "#c0392b" if has_action else "#2e7d32"

    # Deadline (earliest from action items)
    deadline = ""
    for item in action_items:
        d = item.deadline if hasattr(item, "deadline") else ""
        if d:
            deadline = d
            break

    # Estimated work
    work_parts = []
    for item in action_items:
        est = item.estimated_time if hasattr(item, "estimated_time") else ""
        if est:
            work_parts.append(est)
    work_text = " / ".join(work_parts[:2]) if work_parts else ""

    rows_html = ""
    field_style = (
        "font-size: 12px; font-weight: 700; color: #5b6a7a; "
        "text-transform: uppercase; letter-spacing: 0.3px; "
        "padding: 5px 10px; width: 90px; vertical-align: top;"
    )
    value_style = (
        "font-size: 14px; color: #1a1a1a; padding: 5px 10px; "
        "vertical-align: top; line-height: 1.4;"
    )

    # Scope row
    rows_html += f'<tr><td style="{field_style}">{L["scope"]}</td>'
    rows_html += f'<td style="{value_style}">{scope_text}</td>'

    # Action needed row
    rows_html += f'<td style="{field_style}">{L["action_needed"]}</td>'
    rows_html += f'<td style="{value_style}"><span style="color: {action_color}; font-weight: 600;">{action_text}</span></td></tr>'

    # Deadline + work estimate row (only if applicable)
    if deadline or work_text:
        rows_html += "<tr>"
        if deadline:
            rows_html += f'<td style="{field_style}">{L["deadline"]}</td>'
            rows_html += f'<td style="{value_style}">{escape_email_text(deadline)}</td>'
        else:
            rows_html += "<td></td><td></td>"
        if work_text:
            rows_html += f'<td style="{field_style}">{L["work_estimate"]}</td>'
            rows_html += f'<td style="{value_style}">{escape_email_text(work_text)}</td>'
        else:
            rows_html += "<td></td><td></td>"
        rows_html += "</tr>"

    return f"""
    <tr>
        <td class="azb-pad" style="padding: 12px 32px 0 32px;">
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" class="azb-qd" style="background-color: #f8f9fb; border-radius: 6px; border: 1px solid #e2e7ed;">
                <tr><td colspan="4" class="azb-qd-label" style="padding: 6px 10px 2px 10px; font-size: 12px; font-weight: 700; color: #0078d4; text-transform: uppercase; letter-spacing: 0.4px;">{L['quick_decision']}</td></tr>
                {rows_html}
            </table>
        </td>
    </tr>
    """


def format_timeline_html(
    action_items: list,
    update_category: str = "new_feature",
    language: str = "ko",
) -> str:
    """Format a key dates timeline section for retirement/feature_change updates.

    Args:
        action_items: List of ActionItem objects (extracts deadlines)
        update_category: Only rendered for retirement, feature_change
        language: Language code

    Returns:
        Complete <tr> HTML block; empty string if not applicable.
    """
    if update_category not in ("retirement", "feature_change"):
        return ""

    L = get_labels(language)
    dates = []
    seen = set()
    for item in action_items or []:
        d = item.deadline if hasattr(item, "deadline") else ""
        task = item.task if hasattr(item, "task") else ""
        if d and d not in seen:
            seen.add(d)
            # Truncate task to keep timeline compact
            task_short = task[:60] + "..." if len(task) > 60 else task
            dates.append((d, task_short))

    if not dates:
        return ""

    dots_html = ""
    for i, (date, task) in enumerate(dates[:4]):
        connector = ""
        if i < len(dates) - 1 and i < 3:
            connector = (
                '<td style="padding: 0 4px; vertical-align: middle;">'
                '<span style="color: #c0c8d0;">&mdash;&mdash;</span></td>'
            )
        dots_html += (
            f'<td style="text-align: center; vertical-align: top; padding: 4px 6px;">'
            f'<div style="width: 10px; height: 10px; border-radius: 50%; '
            f'background-color: #0078d4; margin: 0 auto 4px auto;"></div>'
            f'<p style="margin: 0; font-size: 12px; font-weight: 600; color: #1a1a1a;">{escape_email_text(date)}</p>'
            f'<p class="azb-tl-task" style="margin: 2px 0 0 0; font-size: 12px; color: #6b7785; '
            f'max-width: 120px; line-height: 1.3;">{escape_email_text(task)}</p>'
            f"</td>"
        )
        if connector:
            dots_html += connector

    return f"""
    <tr>
        <td class="azb-pad" style="padding: 0 32px 14px 32px;">
            <p style="margin: 0 0 8px 0; font-size: 18px; font-weight: 700; color: #1a1a1a; text-transform: uppercase; letter-spacing: 0.3px;">{L['timeline']}</p>
            <div style="overflow-x: auto;">
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin: 0 auto;">
                <tr>{dots_html}</tr>
            </table>
            </div>
        </td>
    </tr>
    """


# ============================================================================
# Digest (daily briefing) template — level helpers
# ============================================================================


def _urgency_to_level(urgency: str, impact_level: str = "") -> str:
    """Map impact_level (or fallback urgency) to a three-tier level.

    Prefers LLM-assessed impact_level when available.

    Args:
        urgency: Urgency value for fallback (critical, high, medium, low)
        impact_level: LLM-assessed resource impact (high, medium, low)
    """
    if impact_level and impact_level.lower() in ("high", "medium", "low"):
        return impact_level.lower()
    # Fallback: derive from urgency (backward compatibility)
    urgency = urgency.lower()
    if urgency in ("critical", "high"):
        return "high"
    if urgency == "medium":
        return "medium"
    return "low"


def _relevance_to_level(relevance: str, job_relevance: str = "") -> str:
    """Map job_relevance (or fallback relevance) to a three-tier level.

    Prefers LLM-assessed job_relevance when available.

    Args:
        relevance: Relevance status for fallback (relevant, opportunity, not_relevant, unknown)
        job_relevance: LLM-assessed job relevance (high, medium, low)
    """
    if job_relevance and job_relevance.lower() in ("high", "medium", "low"):
        return job_relevance.lower()
    # Fallback: derive from relevance (backward compatibility)
    relevance = relevance.lower()
    if relevance == "relevant":
        return "high"
    if relevance in ("opportunity", "unknown"):
        return "medium"
    return "low"


_LEVEL_COLORS: dict[str, dict[str, str]] = {
    "high": {"color": "#dc2626", "bg": "#fef2f2"},
    "medium": {"color": "#d97706", "bg": "#fffbeb"},
    "low": {"color": "#16a34a", "bg": "#f0fdf4"},
}


def _level_badge_html(level: str, language: str = "ko") -> str:
    """Return an inline-styled badge span for a level (높음/보통/낮음)."""
    L = get_labels(language)
    label_map = {"high": L["level_high"], "medium": L["level_medium"], "low": L["level_low"]}
    label = label_map.get(level, label_map["low"])
    colors = _LEVEL_COLORS.get(level, _LEVEL_COLORS["low"])
    badge_class = f"azb-badge-{level}" if level in ("high", "medium", "low") else "azb-badge-low"
    return (
        f'<span class="{badge_class}" style="display:inline-block; background-color:{colors["bg"]}; '
        f'color:{colors["color"]}; padding:2px 8px; border-radius:3px; '
        f'font-size:14px; font-weight:600;">{label}</span>'
    )


def format_digest_table_header_html(language: str = "ko") -> str:
    """Return the <tr> header row for the digest summary table.

    Args:
        language: Language code for column labels.

    Returns:
        HTML string for the table header row.
    """
    L = get_labels(language)
    hdr_style = (
        "padding:10px 12px; font-size:14px; font-weight:700; color:#5b6a7a; "
        "border-bottom:2px solid #d0d5dd; text-align:left;"
    )
    return f"""
        <tr>
            <td class="azb-th" style="{hdr_style}">{L['col_update_title']}</td>
            <td class="azb-th azb-col-metric" style="{hdr_style} text-align:center; width:70px;">{L['col_importance']}</td>
            <td class="azb-th azb-col-metric" style="{hdr_style} text-align:center; width:70px;">{L['col_impact']}</td>
            <td class="azb-th azb-col-metric" style="{hdr_style} text-align:center; width:85px;">{L['col_job_relevance']}</td>
        </tr>"""


def format_digest_update_card_html(
    update,
    result,
    skip_reason: str = "",
    language: str = "ko",
    anchor_index: int = 0,
) -> str:
    """Format a single update as a compact table row for the daily digest email.

    Analyzed updates show importance / impact / job-relevance as 높음·보통·낮음
    badges. Title links to the corresponding detail section via anchor.
    Skipped updates show a muted row spanning all columns.

    Args:
        update: AzureUpdate object
        result: AnalysisResult object (None if skipped)
        skip_reason: Reason the update was skipped (empty if analyzed)
        language: Language code for UI labels
        anchor_index: 1-based index for anchor link to detail section

    Returns:
        HTML string for one update table row.
    """
    L = get_labels(language)

    raw_title = update.title[:80] + "…" if len(update.title) > 80 else update.title
    title = escape_email_text(raw_title)
    link = safe_email_href(update.link) or "#"

    # --- Skipped update — muted row spanning all columns ---
    if skip_reason or result is None:
        skip_text = escape_email_text(skip_reason or "")
        return f"""
        <tr>
            <td colspan="4" class="azb-cell" style="padding:8px 12px; border-bottom:1px solid #edf0f3;">
                <span class="azb-skip-badge" style="display:inline-block; background-color:#e8ecf0; color:#8c96a3; padding:1px 6px; border-radius:3px; font-size:12px; font-weight:600; margin-right:6px;">{L['digest_skipped_label']}</span>
                <a href="{link}" class="azb-skip-link" style="color:#8c96a3; text-decoration:none; font-size:14px;">{title}</a>
                {'<span class="azb-text-muted" style="font-size:12px; color:#b0b8c4; font-style:italic; margin-left:6px;">' + skip_text + '</span>' if skip_text else ''}
            </td>
        </tr>"""

    # --- Analyzed update — simple table row ---
    urgency_value = (
        result.urgency.value if hasattr(result.urgency, "value") else str(result.urgency)
    )
    relevance_value = (
        result.relevance.value if hasattr(result.relevance, "value") else str(result.relevance)
    )

    # Extract LLM-assessed metrics (with fallback for older results)
    importance_raw = getattr(result, "importance", "") or ""
    impact_level_raw = getattr(result, "impact_level", "") or ""
    job_relevance_raw = getattr(result, "job_relevance", "") or ""

    importance = get_importance_level(urgency_value, relevance_value, importance_raw)
    impact_level = _urgency_to_level(urgency_value, impact_level_raw)
    job_rel_level = _relevance_to_level(relevance_value, job_relevance_raw)

    anchor_href = f"#azbrief-detail-{anchor_index}" if anchor_index > 0 else link

    cell_style = "padding:10px 12px; border-bottom:1px solid #edf0f3;"
    center_cell = f"{cell_style} text-align:center;"

    importance_colors = get_importance_colors(importance, language)

    return f"""
        <tr>
            <td class="azb-cell" style="{cell_style} border-left:3px solid {importance_colors['dot_color']};">
                <a href="{anchor_href}" class="azb-text" style="color:#1a1a1a; text-decoration:none; font-size:16px; font-weight:600; line-height:1.4;">{title}</a>
            </td>
            <td class="azb-cell azb-col-metric" style="{center_cell}">{_level_badge_html(importance, language)}</td>
            <td class="azb-cell azb-col-metric" style="{center_cell}">{_level_badge_html(impact_level, language)}</td>
            <td class="azb-cell azb-col-metric" style="{center_cell}">{_level_badge_html(job_rel_level, language)}</td>
        </tr>"""
