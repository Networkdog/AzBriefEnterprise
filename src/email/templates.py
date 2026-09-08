"""Professional HTML email templates for AzBrief reports."""

import re

# Aliased: several renderers below bind a local name `html` for their output.
from html import escape as _escape
from html import unescape as _unescape
from urllib.parse import quote, urlparse

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

_AZURE_PORTAL_ROOT = "https://portal.azure.com/"
_SUBSCRIPTION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Action-item safety gate. The border tints the whole card; the badge colour is
# also used for the findings block so a blocked item reads as one unit.
_VERIFY_COLOR = {
    "verified": "#206344",
    "caution": "#85500b",
    "blocked": "#ac3028",
    "unverified": "#52656d",
}
_VERIFY_BORDER = {
    "verified": "#d9e2e1",
    "caution": "#c79b61",
    "blocked": "#c46c66",
    "unverified": "#d9e2e1",
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
# Body copy uses a 13px baseline for regular email text.
# Every scale step is 1px above the previous editorial scale.
FONT_SIZE_PX: dict[str, int] = {
    "meta": 11,  # badges, table headers, timestamps, fine print
    "secondary": 12,  # table cells, action detail lines, CLI blocks
    "body": 13,  # prose, list items, concept boxes
    "heading": 15,  # section labels
    "title": 17,  # update titles
    "masthead": 21,  # AzBrief wordmark
    "display": 25,  # digest counters
    "hero": 29,  # document title
}

EMAIL_COLORS: dict[str, str] = {
    "canvas": "#eef1f0",
    "paper": "#ffffff",
    "ink": "#182b32",
    "body": "#34474f",
    "muted": "#52656d",
    "line": "#d9e2e1",
    "wash": "#f4f7f6",
    "accent": "#08746b",
    "accent_wash": "#edf7f4",
    "danger": "#ac3028",
    "warning": "#85500b",
    "success": "#206344",
}

SEMANTIC_ACCENT_WIDTH_PX = 6

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
    td { mso-line-height-rule: at-least; }
  img { -ms-interpolation-mode: bicubic; border: 0; outline: none; text-decoration: none; }
    .azb-cli, .azb-code, .azb-mono { word-break: break-word; overflow-wrap: anywhere; }
    a:focus-visible { outline: 2px solid #08746b; outline-offset: 3px; }
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
# content instead of the gray backdrop while retaining a bounded width for 13px body text.
# ============================================================================

_RESPONSIVE_STYLE = """
<style type="text/css">
  @media only screen and (max-width: 640px) {
    .azb-outer { padding: 12px 6px 24px !important; }
    .azb-pad { padding-left: 20px !important; padding-right: 20px !important; }
    h1.azb-hero-title { font-size: 25px !important; }
        .azb-stack-cell { display: block !important; width: auto !important;
            text-align: left !important; }
    .azb-stack .azb-stack-tail { padding-top: 6px !important; }
        .azb-digest-heading, .azb-resource-columns { display: none !important; }
    .azb-digest-table, .azb-digest-table > tbody,
    .azb-resources, .azb-resources > tbody,
    .azb-qd, .azb-qd > tbody, .azb-qd tr,
    .azb-action-context, .azb-action-context > tbody,
    .azb-action-guardrails, .azb-action-guardrails > tbody,
    .azb-action-detail-row { display: block !important; width: 100% !important; }
    .azb-resource-reason, .azb-resource-reason > td,
    .azb-digest-skip, .azb-digest-skip > td { display: block !important; width: auto !important; }
        .azb-digest-row { display: block !important; font-size: 0 !important;
            border-bottom: 1px solid #d9e2e1 !important; padding-bottom: 12px !important; }
        .azb-digest-title { display: block !important; width: auto !important;
            border-bottom: 0 !important; padding: 14px 0 10px !important; }
        .azb-col-metric { display: inline-block !important; width: 33.333% !important;
            box-sizing: border-box !important; border-bottom: 0 !important;
            padding: 0 6px 0 0 !important; text-align: left !important; vertical-align: top !important; }
        .azb-metric-label, .azb-resource-field-label { display: block !important;
            margin-bottom: 4px !important; }
        .azb-resource-row { display: block !important; padding: 8px 0 !important;
            border-bottom: 1px solid #d9e2e1 !important; }
        .azb-resource-row > td { display: block !important; width: auto !important;
            padding: 6px 12px !important; border-bottom: 0 !important; }
        .azb-fact-cell { display: block !important; width: auto !important;
            padding: 10px 0 !important; }
        .azb-action-detail-row > td { display: block !important; width: auto !important;
            white-space: normal !important; padding: 6px 16px !important; }
        .azb-action-detail-row > td + td { border-top: 0 !important; padding-top: 0 !important; }
  }
  @media only screen and (max-width: 400px) {
        .azb-pad { padding-left: 16px !important; padding-right: 16px !important; }
  }
    @media only screen and (min-width: 641px) {
        .azb-digest-heading th:first-child { width: 52% !important; }
        .azb-digest-heading .azb-col-metric { width: 16% !important; }
    }
  @media only screen and (min-width: 800px) {
    .azb-card { max-width: 760px !important; }
        .azb-outer { padding-top: 32px !important; }
  }
  @media only screen and (min-width: 1100px) {
    .azb-card { max-width: 900px !important; }
        .azb-pad { padding-left: 48px !important; padding-right: 48px !important; }
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
            '<div class="azb-concept" style="margin: 14px 0; padding: 12px 16px; '
            f'background-color: {EMAIL_COLORS["wash"]}; '
            f'border-left: {SEMANTIC_ACCENT_WIDTH_PX}px solid {EMAIL_COLORS["accent"]}; '
            f'font-size: 13px; color: {EMAIL_COLORS["body"]}; line-height: 1.8;">'
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
                f'<p style="margin: 16px 0 8px; font-size: {font_size}; '
                f'font-weight: 700; color: {EMAIL_COLORS["ink"]};">{heading_text}</p>'
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
                f'<li class="azb-text" style="margin: 6px 0; font-size: 13px; color: {EMAIL_COLORS["body"]}; '
                f'line-height: 1.8;">{item_text}</li>'
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
                f'<li class="azb-text" style="margin: 6px 0; font-size: 13px; color: {EMAIL_COLORS["body"]}; '
                f'line-height: 1.8;">{item_text}</li>'
            )
            continue

        # Regular paragraph
        if in_list:
            html_parts.append(f"</{list_type}>")
            in_list = False
            list_type = None
        formatted = _inline_format(stripped)
        html_parts.append(
            f'<p class="azb-text" style="margin: 0 0 8px; font-size: 13px; color: {EMAIL_COLORS["body"]}; '
            f'line-height: 1.85; overflow-wrap: anywhere;">{formatted}</p>'
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
        f"padding: 10px 12px; text-align: left; font-size: {FONT_SIZE_PX['meta']}px; "
        f"font-weight: 700; color: {EMAIL_COLORS['muted']}; background-color: {EMAIL_COLORS['wash']}; "
        f"border-bottom: 1px solid {EMAIL_COLORS['line']}; overflow-wrap: anywhere;"
    )
    td_style = (
        f"padding: 10px 12px; text-align: left; font-size: {FONT_SIZE_PX['secondary']}px; "
        f"color: {EMAIL_COLORS['body']}; line-height: 1.7; border-bottom: 1px solid {EMAIL_COLORS['line']}; "
        "vertical-align: top; overflow-wrap: anywhere; word-break: break-word;"
    )

    head_cells = "".join(
        f'<th scope="col" style="{th_style}">{_inline_format(h)}</th>' for h in headers
    )
    rows_html = []
    for cells in body:
        # Pad/trim so a malformed row cannot break the table layout.
        cells = (cells + [""] * len(headers))[: len(headers)]
        tds = "".join(f'<td style="{td_style}">{_inline_format(c)}</td>' for c in cells)
        rows_html.append(f"<tr>{tds}</tr>")

    table_html = (
        '<table cellspacing="0" cellpadding="0" border="0" width="100%" '
        'class="azb-mdtable" style="margin: 12px 0; border-collapse: collapse; table-layout: fixed;">'
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
        f'style="color: {EMAIL_COLORS["accent"]}; text-decoration: underline;">{label}</a>'
    )


def _inline_format(text: str) -> str:
    """Apply inline markdown formatting (link, bold, code) to text."""
    text = _escape(str(text), quote=True)
    # Markdown links first so the URL is not mangled by the bold/code passes
    text = _RE_MD_LINK.sub(_linkify_md, text)
    # Bold: **text** (emphasis styling)
    text = _RE_BOLD.sub(r'<strong style="color: #182b32;">\1</strong>', text)
    # Inline code: `code`
    text = _RE_INLINE_CODE.sub(
        r'<code class="azb-code" style="background-color: #f4f7f6; color: #34474f; padding: 1px 4px; '
        r'font-family: monospace; font-size: 12px; word-break: break-word;">\1</code>',
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


def _resource_arm_id(resource: dict) -> str:
    """Return a complete ARM resource ID when it can be determined safely."""
    supplied_id = str(resource.get("id") or resource.get("resourceId") or "").strip()
    if supplied_id:
        segments = supplied_id.strip("/").split("/")
        if (
            len(segments) >= 8
            and segments[0].lower() == "subscriptions"
            and _SUBSCRIPTION_ID_RE.fullmatch(segments[1])
            and segments[2].lower() == "resourcegroups"
            and segments[4].lower() == "providers"
            and len(segments[6:]) % 2 == 0
            and all(segment and not re.search(r"[\\?#\x00-\x1f]", segment) for segment in segments)
        ):
            return "/" + "/".join(segments)

    subscription_id = str(resource.get("subscriptionId") or "").strip()
    resource_group = str(resource.get("resourceGroup") or "").strip()
    resource_type = str(resource.get("type") or "").strip("/")
    resource_name = str(resource.get("name") or "").strip()
    type_segments = resource_type.split("/")
    if (
        not _SUBSCRIPTION_ID_RE.fullmatch(subscription_id)
        or not resource_group
        or re.search(r"[\\/?#\x00-\x1f]", resource_group)
        or len(type_segments) != 2
        or not all(type_segments)
        or not resource_name
        or re.search(r"[\\/?#\x00-\x1f]", resource_name)
    ):
        return ""
    return (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/{resource_type}/{resource_name}"
    )


def _portal_scope_url(resource: dict, scope: str) -> str:
    """Build a Portal URL for a resource, subscription, or resource group."""
    arm_id = _resource_arm_id(resource)
    subscription_id = str(resource.get("subscriptionId") or "").strip()
    if arm_id and not subscription_id:
        subscription_id = arm_id.strip("/").split("/")[1]
    if not _SUBSCRIPTION_ID_RE.fullmatch(subscription_id):
        return ""

    if scope == "subscription":
        target_id = f"/subscriptions/{subscription_id}"
    elif scope == "resource_group":
        resource_group = str(resource.get("resourceGroup") or "").strip()
        if not resource_group and arm_id:
            resource_group = arm_id.strip("/").split("/")[3]
        if not resource_group or re.search(r"[\\/?#\x00-\x1f]", resource_group):
            return ""
        target_id = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
    elif scope == "resource":
        if not arm_id:
            return ""
        target_id = arm_id
    else:
        return ""

    encoded_id = quote(target_id, safe="/-._~()")
    return f"{_AZURE_PORTAL_ROOT}#resource{encoded_id}"


def _portal_link_html(value: object, url: str) -> str:
    """Render a value as a Portal link, or plain text when no safe URL exists."""
    safe_value = escape_email_text(value)
    safe_url = safe_email_href(url)
    if not safe_url:
        return safe_value
    return (
        f'<a href="{safe_url}" class="azb-link" title="Azure Portal" '
        f'style="color: {EMAIL_COLORS["accent"]}; text-decoration: underline;">'
        f"{safe_value}</a>"
    )


def _cloud_shell_url(command: str) -> str:
    """Return the official Portal Cloud Shell URL for the command's shell."""
    text = str(command or "").strip()
    if not text:
        return ""
    is_powershell = bool(
        re.search(r"(?:^|[\s;|])[A-Z][A-Za-z]+-Az[A-Z][A-Za-z]*", text)
        or re.search(r"(?m)^\s*\$[A-Za-z_][A-Za-z0-9_]*\s*=", text)
        or re.search(r"\$(?:env|Env):[A-Za-z_]", text)
    )
    shell = "pwsh" if is_powershell else "bash"
    return f"{_AZURE_PORTAL_ROOT}?feature.azureconsole.shell={shell}#cloudshell"


def _link_portal_keyword(text: object, portal_url: str) -> str:
    """Link the Portal entry point in a procedure when its target is unambiguous."""
    safe_text = escape_email_text(text)
    safe_url = safe_email_href(portal_url)
    if not safe_url or "Azure Portal" not in safe_text:
        return safe_text
    portal_link = (
        f'<a href="{safe_url}" class="azb-link" title="Azure Portal" '
        f'style="color: {EMAIL_COLORS["accent"]}; text-decoration: underline;">Azure Portal</a>'
    )
    return safe_text.replace("Azure Portal", portal_link, 1)


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
        '<p style="margin: 6px 0 0;">'
        f'<a href="{safe_url}" class="azb-link" '
        f'style="color: {EMAIL_COLORS["accent"]}; font-size: 12px; text-decoration: underline;">'
        f"{label}</a></p>"
    )


def format_feedback_link_html(feedback_url: str, language: str = "ko") -> str:
    """Render a trusted HTTPS link to the public feedback form."""
    feedback_url = safe_archive_url(feedback_url)
    if not feedback_url:
        return ""
    label = _escape(get_labels(language)["feedback_link"])
    safe_url = _escape(feedback_url, quote=True)
    return (
        '<p style="margin: 0 0 7px 0;">'
        f'<a href="{safe_url}" class="azb-link" '
        f'style="color: {EMAIL_COLORS["accent"]}; font-size: 13px; font-weight: 700; '
        'text-decoration: underline;">'
        f"{label}</a></p>"
    )


# ============================================================================
# HTML Email Template
# ============================================================================
# 단건과 다이제스트는 같은 흰색 문서 셸·헤더·섹션·푸터를 공유합니다.
# 제목 → 핵심 요약과 독립 3축 평가 → 운영 판단 → 근거와 조치 순서로 읽습니다.
# 다이제스트는 건수와 목차를 먼저 보여주며, 상세와 목차 사이를 앵커로 연결합니다.
# 스타일을 제거해도 표 기반 내용과 MSO 640px 경계는 유지합니다.
# ============================================================================

# System fonts only: email clients block webfonts, and bundled families are not installed.
FONT_STACK_SANS = (
    "'AppleSDGothicNeo-Regular', 'Microsoft GothicNeo', '맑은 고딕', "
    "'Apple SD Gothic Neo', 'Malgun Gothic', 'Segoe UI', -apple-system, "
    "BlinkMacSystemFont, Roboto, 'Noto Sans CJK KR', 'Helvetica Neue', Arial, sans-serif"
)
FONT_STACK_MONO = "Consolas, Menlo, 'DejaVu Sans Mono', 'Courier New', monospace"

_EMAIL_DOCUMENT_START = (
    """<!DOCTYPE html>
<html lang="{html_lang}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="light only">
    <title>{document_title}</title>
"""
    + _DARK_MODE_STYLE_ESCAPED
    + _CLIENT_COMPAT_STYLE_ESCAPED
    + _RESPONSIVE_STYLE_ESCAPED
    + f"""</head>
<body class="azb-body" style="margin: 0; padding: 0; font-family: {FONT_STACK_SANS}; font-size: 13px; color: {EMAIL_COLORS['body']}; background-color: {EMAIL_COLORS['canvas']}; line-height: 1.7; -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%;">
<div class="azb-preheader" aria-hidden="true" style="display: none; max-height: 0; overflow: hidden; mso-hide: all;">{{preheader}}</div>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: {EMAIL_COLORS['canvas']};">
<tr><td align="center" class="azb-outer" style="padding: 24px 10px 40px;">
<!--[if mso]><table role="presentation" cellspacing="0" cellpadding="0" border="0" width="640" align="center"><tr><td><![endif]-->
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" align="center" class="azb-card azb-paper" style="max-width: 640px; background-color: {EMAIL_COLORS['paper']}; border-collapse: collapse; table-layout: fixed; border-top: 3px solid {EMAIL_COLORS['accent']};">
"""
)

_EMAIL_DOCUMENT_END = """
{footer_html}
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table>
</body>
</html>"""

HTML_EMAIL_TEMPLATE = _EMAIL_DOCUMENT_START + """
{masthead_html}
{report_header_html}
{quick_decision_html}
{batch_context_html}
{analysis_section_html}
{relevance_evidence_html}
{timeline_html}
{impact_section_html}
{affected_resources_section_html}
{action_items_section_html}
{additional_checks_html}
{reference_docs_section_html}
""" + _EMAIL_DOCUMENT_END

HTML_DIGEST_TEMPLATE = _EMAIL_DOCUMENT_START + """
{masthead_html}
{digest_intro_html}
{summary_table_html}
{retirement_html}
{details_html}
""" + _EMAIL_DOCUMENT_END


def format_email_section_html(label: str, content_html: str) -> str:
    """Wrap trusted renderer output in an editorial section with a shared inset."""
    return (
        '<tr><td class="azb-section azb-pad" style="padding: 0 32px 28px;">'
        f'<h2 class="azb-heading" style="margin: 0 0 14px; padding-top: 14px; '
        f'border-top: 1px solid {EMAIL_COLORS["line"]}; font-size: 15px; '
        f'font-weight: 700; color: {EMAIL_COLORS["ink"]}; line-height: 1.5;">'
        f"{escape_email_text(label)}</h2>{content_html}</td></tr>"
    )


def format_email_masthead_html(label: str, date_text: str = "") -> str:
    """Render the restrained wordmark and edition metadata shared by both emails."""
    return f"""<tr><td class="azb-masthead azb-pad" style="padding: 24px 32px 0;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" class="azb-stack" style="border-bottom: 2px solid {EMAIL_COLORS['ink']};">
<tr><td class="azb-stack-cell" style="padding: 0 0 14px; vertical-align: bottom;">
<span style="font-size: 21px; font-weight: 700; color: {EMAIL_COLORS['ink']};">AzBrief<span style="color: {EMAIL_COLORS['accent']};">.</span></span>
<span style="display: inline-block; margin-left: 10px; font-size: 11px; color: {EMAIL_COLORS['muted']};">{escape_email_text(label)}</span>
</td><td class="azb-stack-cell azb-stack-tail" align="right" style="padding: 0 0 14px; vertical-align: bottom; font-size: 12px; color: {EMAIL_COLORS['muted']};">{escape_email_text(date_text)}</td></tr>
</table></td></tr>"""


def format_report_header_html(
    update, result, language: str = "ko", archive_url: str = "", index: int = 0
) -> str:
    """Render a title, takeaway and independent assessment strip, without a colored card."""
    L = get_labels(language)
    urgency = result.urgency.value if hasattr(result.urgency, "value") else str(result.urgency)
    relevance = (
        result.relevance.value if hasattr(result.relevance, "value") else str(result.relevance)
    )
    summary = result.one_line_summary or update.title[:80]
    importance = get_importance_level(urgency, relevance, getattr(result, "importance", ""))
    metrics = (
        (L["col_importance"], importance),
        (L["col_impact"], _urgency_to_level(urgency, getattr(result, "impact_level", ""))),
        (
            L["col_job_relevance"],
            _relevance_to_level(relevance, getattr(result, "job_relevance", "")),
        ),
    )
    metric_cells = "".join(
        f'<td width="33%" style="padding: 12px 8px 0 0; vertical-align: top;">'
        f'<p style="margin: 0 0 5px; font-size: 11px; color: {EMAIL_COLORS["muted"]};">'
        f"{escape_email_text(label)}</p>{_level_badge_html(level, language)}</td>"
        for label, level in metrics
    )
    source = safe_email_href(update.link)
    source_html = (
        f'<a href="{source}" class="azb-link" style="color: {EMAIL_COLORS["accent"]}; '
        f'text-decoration: underline;">{escape_email_text(L["detail_link"])}</a>'
        if source
        else ""
    )
    published = update.published_date.strftime("%Y-%m-%d") if update.published_date else "-"
    heading = "h2" if index else "h1"
    title_size = FONT_SIZE_PX["masthead"] if index else FONT_SIZE_PX["hero"]
    number = f"{index:02d} / " if index else ""
    back_link = (
        f'<a href="#azbrief-summary" style="color: {EMAIL_COLORS["accent"]}; '
        f'text-decoration: underline;">{escape_email_text(L["email_back_to_contents"])}</a>'
        if index
        else ""
    )
    services = " &middot; ".join(escape_email_text(s) for s in update.azure_services[:4])
    return f"""<tr><td class="azb-hero azb-pad" style="padding: 28px 32px 24px; overflow-wrap: anywhere; word-break: break-word;">
<p class="azb-eyebrow" style="margin: 0 0 10px; font-size: 11px; font-weight: 700; color: {EMAIL_COLORS['accent']};">{number}{escape_email_text(L['update_type'])}: {escape_email_text(update.update_type or 'Info')} &middot; {published}</p>
<{heading} class="azb-hero-title" style="margin: 0; font-size: {title_size}px; font-weight: 700; color: {EMAIL_COLORS['ink']}; line-height: 1.35; letter-spacing: -0.4px;">{escape_email_text(update.title)}</{heading}>
{f'<p style="margin: 8px 0 0; font-size: 12px; color: {EMAIL_COLORS["muted"]};">{services}</p>' if services else ''}
<p style="margin: 12px 0 0; font-size: 12px; line-height: 1.8;">{source_html}{' &nbsp; / &nbsp; ' + back_link if back_link else ''}</p>
{format_archive_link_html(archive_url, language)}
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" class="azb-takeaway" style="margin-top: 20px; background-color: {EMAIL_COLORS['accent_wash']}; border-left: {SEMANTIC_ACCENT_WIDTH_PX}px solid {EMAIL_COLORS['accent']}; table-layout: fixed;">
<tr><td style="padding: 16px 18px;">
<p style="margin: 0 0 6px; font-size: 11px; font-weight: 700; color: {EMAIL_COLORS['accent']};">{escape_email_text(L['importance_section'])}</p>
<p class="azb-summary" style="margin: 0; font-size: 17px; font-weight: 700; color: {EMAIL_COLORS['ink']}; line-height: 1.65;">{escape_email_text(summary)}</p>
</td></tr></table>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" class="azb-assessment" style="table-layout: fixed;"><tr>{metric_cells}</tr></table>
<p style="margin: 12px 0 0; font-size: 11px; color: {EMAIL_COLORS['muted']}; line-height: 1.7;">{escape_email_text(L['urgency'])}: <strong style="color: {get_urgency_colors(urgency)['text_color']};">{get_urgency_colors(urgency)['badge']}</strong> &middot; {escape_email_text(get_relevance_colors(relevance, language)['label'])}</p>
</td></tr>"""


def format_digest_intro_html(
    total: int, high: int, medium: int, low: int, skipped: int, language: str = "ko"
) -> str:
    """Render real analyzed counts separately from skipped items in the digest lead."""
    L = get_labels(language)
    cells = "".join(
        f'<td width="33%" style="padding: 14px 8px 14px 0; vertical-align: top;">'
        f'<p style="margin: 0; font-size: 25px; font-weight: 700; color: {EMAIL_COLORS["ink"]}; '
        f'line-height: 1.3;">{count:02d}</p><p style="margin: 5px 0 0; font-size: 12px; '
        f'color: {EMAIL_COLORS["muted"]};">{escape_email_text(L["importance_" + level])}</p></td>'
        for level, count in (("high", high), ("medium", medium), ("low", low))
    )
    skipped_html = (
        f'<p style="margin: 8px 0 0; font-size: 12px; color: {EMAIL_COLORS["muted"]};">'
        f'{escape_email_text(L["digest_skipped"].format(count=skipped))}</p>'
        if skipped
        else ""
    )
    empty_html = (
        f'<p style="font-size: 13px; color: {EMAIL_COLORS["body"]};">'
        f'{escape_email_text(L["digest_no_updates"])}</p>'
        if not total
        else ""
    )
    return f"""<tr><td class="azb-digest-lead azb-pad" style="padding: 28px 32px;">
<p style="margin: 0 0 8px; font-size: 12px; color: {EMAIL_COLORS['accent']};">{escape_email_text(L['digest_total'].format(total=total))}</p>
<h1 class="azb-hero-title" style="margin: 0 0 20px; font-size: 29px; font-weight: 700; color: {EMAIL_COLORS['ink']}; line-height: 1.35;">{escape_email_text(L['digest_title'])}</h1>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" class="azb-digest-counts" style="table-layout: fixed; border-top: 1px solid {EMAIL_COLORS['line']}; border-bottom: 1px solid {EMAIL_COLORS['line']};"><tr>{cells}</tr></table>
{skipped_html}{empty_html}</td></tr>"""


def format_email_footer_html(language: str, generated_at: str, feedback_url: str = "") -> str:
    """Render a legible disclaimer and feedback link without a low-contrast fine-print card."""
    L = get_labels(language)
    return f"""<tr><td class="azb-footer azb-pad" style="padding: 24px 32px 32px; background-color: {EMAIL_COLORS['wash']}; border-top: 1px solid {EMAIL_COLORS['line']};">
{format_feedback_link_html(feedback_url, language)}
<p style="margin: 12px 0 0; font-size: 11px; color: {EMAIL_COLORS['muted']}; line-height: 1.8;"><strong>{escape_email_text(L['disclaimer_title'])}</strong><br>{escape_email_text(L['disclaimer_body'])}</p>
<p style="margin: 12px 0 0; font-size: 11px; color: {EMAIL_COLORS['muted']}; line-height: 1.8;">{escape_email_text(L['footer_generated'])} &middot; AzBrief<br>{escape_email_text(L['footer_basis'])}<br>{escape_email_text(generated_at)}</p>
</td></tr>"""


# ============================================================================
# Color / badge helpers
# ============================================================================


def get_urgency_colors(urgency: str) -> dict:
    """Get color scheme for urgency level."""
    colors = {
        "critical": {
            "bg_color": EMAIL_COLORS["danger"],
            "text_color": EMAIL_COLORS["danger"],
            "badge": "CRITICAL",
        },
        "high": {
            "bg_color": "#943c1b",
            "text_color": "#943c1b",
            "badge": "HIGH",
        },
        "medium": {
            "bg_color": EMAIL_COLORS["warning"],
            "text_color": EMAIL_COLORS["warning"],
            "badge": "MEDIUM",
        },
        "low": {
            "bg_color": EMAIL_COLORS["success"],
            "text_color": EMAIL_COLORS["success"],
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
    """Format impact dimensions as quiet, consistently aligned definition rows.

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
        ("cost", L["cost"]),
        ("security", L["security"]),
        ("performance", L["performance"]),
        ("operational", L["operational"]),
    ]

    items = []
    for key, label in fields:
        value = getattr(impact_details, f"{key}_impact", "")
        if value and value.strip().lower() not in {v.lower() for v in skip_values}:
            items.append((label, escape_email_text(value)))

    if not items:
        return ""

    rows = "".join(
        f'<tr><td class="azb-impact-label" width="96" style="padding: 12px 8px 12px 0; '
        f'border-bottom: 1px solid {EMAIL_COLORS["line"]}; vertical-align: top; width: 96px; '
        f"min-width: 96px; white-space: nowrap; word-break: keep-all; font-size: 11px; "
        f'font-weight: 700; color: {EMAIL_COLORS["muted"]};">{label}</td>'
        f'<td class="azb-impact-value" style="padding: 12px 0; vertical-align: top; '
        f'border-bottom: 1px solid {EMAIL_COLORS["line"]}; font-size: 13px; '
        f'color: {EMAIL_COLORS["body"]}; line-height: 1.8; overflow-wrap: anywhere;">{value}</td></tr>'
        for label, value in items
    )
    return format_email_section_html(
        section_label,
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" '
        f'class="azb-impact" style="table-layout: fixed; border-collapse: collapse;">{rows}</table>',
    )


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

    All resources are displayed without truncation. Each reason spans one full
    row, followed by resource name, subscription, resource group, and type
    columns for the resources sharing that reason.

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
            return format_email_section_html(
                section_label,
                f'<p class="azb-text-muted" style="margin: 0; font-size: 13px; '
                f'color: {EMAIL_COLORS["muted"]};">{empty_label}</p>',
            )
        return ""

    count_display = f"{len(resources)}{L['count_suffix']}"

    header_style = (
        f"font-size: {FONT_SIZE_PX['meta']}px; font-weight: 700; color: {EMAIL_COLORS['muted']}; "
        f"padding: 10px 12px; border-bottom: 1px solid {EMAIL_COLORS['line']}; "
        "text-align: left; overflow-wrap: anywhere;"
    )
    cell_style = (
        f"font-size: {FONT_SIZE_PX['secondary']}px; color: {EMAIL_COLORS['body']}; padding: 12px; "
        f"border-bottom: 1px solid {EMAIL_COLORS['line']}; vertical-align: top; line-height: 1.7; "
        "word-break: break-word; overflow-wrap: anywhere;"
    )

    def _short_type(res: dict) -> str:
        """Last segment of an ARM resource type ("…/runbooks" → "runbooks")."""
        res_type = res.get("type") or "Unknown"
        return res_type.split("/")[-1] if "/" in res_type else res_type

    html = (
        '<table cellspacing="0" cellpadding="0" border="0" width="100%" '
        'class="azb-panel azb-resources" style="table-layout: fixed; border-collapse: collapse;">'
    )

    # Group resources that share the same impact reason. Empty reasons stay
    # separate so unrelated resources are never presented as one evidence set.
    # Resources with an empty reason are never merged — each keeps its own row.
    groups: dict = {}
    for idx, res in enumerate(resources):
        reason_key = (res.get("reason") or "").strip()
        # Empty reasons get a unique key so they are never merged together.
        key = reason_key if reason_key else f"\x00__no_reason__{idx}"
        groups.setdefault(key, []).append(res)

    column_headers = (
        (L["col_resource"], "28%"),
        (L["subscription"], "24%"),
        (L["resource_group"], "24%"),
        (L["col_type"], "24%"),
    )
    for group_index, group in enumerate(groups.values()):
        reason = (group[0].get("reason") or "").strip()
        group_border = "" if group_index == 0 else f' border-top: 2px solid {EMAIL_COLORS["line"]};'
        reason_value = escape_email_text(reason) if reason else "&mdash;"
        html += (
            '<tr class="azb-resource-reason">'
            f'<td colspan="4" style="padding: 14px 12px;{group_border} '
            f'background-color: {EMAIL_COLORS["wash"]}; line-height: 1.8; overflow-wrap: anywhere;">'
            f'<span style="font-size: {FONT_SIZE_PX["meta"]}px; font-weight: 700; '
            f'color: {EMAIL_COLORS["accent"]};">{L["impact_reason"]}</span><br>'
            f'<span class="azb-text" style="font-size: {FONT_SIZE_PX["body"]}px; '
            f'color: {EMAIL_COLORS["body"]};">{reason_value}</span></td></tr>\n'
        )
        html += '<tr class="azb-resource-columns">'
        for label, width in column_headers:
            html += f'<th scope="col" class="azb-th" width="{width}" style="{header_style} width: {width};">{label}</th>'
        html += "</tr>\n"

        for resource_index, resource in enumerate(group):
            row_class = "azb-cell-even" if resource_index % 2 == 0 else "azb-cell-odd"
            background = EMAIL_COLORS["paper"]
            subscription = (
                resource.get("subscription")
                or resource.get("subscriptionName")
                or resource.get("subscriptionId")
                or L["unknown_scope"]
            )
            resource_group = resource.get("resourceGroup") or L["unknown_scope"]
            values = (
                (
                    resource.get("name") or "Unknown",
                    True,
                    _portal_scope_url(resource, "resource"),
                ),
                (subscription, False, _portal_scope_url(resource, "subscription")),
                (resource_group, False, _portal_scope_url(resource, "resource_group")),
                (_short_type(resource), False, ""),
            )
            html += (
                f'<tr class="azb-resource-row {row_class}" '
                f'style="background-color: {background};">'
            )
            for (label, _), (value, emphasize, portal_url) in zip(column_headers, values):
                weight = " font-weight: 600;" if emphasize else ""
                html += (
                    f'<td class="azb-cell azb-text" style="{cell_style}{weight}">'
                    f'<span class="azb-resource-field-label" style="display: none; mso-hide: all; '
                    f'font-size: 11px; font-weight: 400; color: {EMAIL_COLORS["muted"]};">{label}</span>'
                    f"{_portal_link_html(value, portal_url)}</td>"
                )
            html += "</tr>\n"

    html += "</table>"
    return format_email_section_html(f"{section_label} · {count_display}", html)


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
    color = _VERIFY_COLOR.get(status, EMAIL_COLORS["muted"])
    return (
        '<span class="azb-verify" style="display: inline-block; '
        f"font-size: {FONT_SIZE_PX['meta']}px; font-weight: 700; color: {color}; "
        f"border-left: {SEMANTIC_ACCENT_WIDTH_PX}px solid {color}; padding: 4px 8px; "
        f'line-height: 1.6; white-space: nowrap;">{label}</span>'
    )


def format_action_items_html(
    action_items: list,
    recommendations: list = None,
    language: str = "ko",
    update_category: str = "new_feature",
    affected_resources: list = None,
) -> str:
    """Format action items as a self-contained <tr> section.

    For categories where action items are not applicable (new_service,
    region_expansion, preview), returns empty string regardless of input data.

    Args:
        action_items: List of ActionItem objects
        recommendations: Fallback list of string recommendations
        language: Language code for UI labels
        update_category: Update category — sections hidden for non-applicable categories
        affected_resources: Structured resources used to resolve action targets to Portal links

    Returns:
        Complete <tr> HTML block; empty string if nothing to show.
    """
    # Categories where action items section is not applicable
    if update_category in ("new_service", "region_expansion", "preview"):
        return ""

    L = get_labels(language)
    items = action_items if action_items else []

    resource_links: dict[str, str] = {}
    ambiguous_names: set[str] = set()
    for resource in affected_resources or []:
        name = str(resource.get("name") or "").strip()
        portal_url = _portal_scope_url(resource, "resource")
        if not name or not portal_url:
            continue
        key = name.casefold()
        if key in resource_links:
            ambiguous_names.add(key)
        else:
            resource_links[key] = portal_url
    for name in ambiguous_names:
        resource_links.pop(name, None)

    # Build inner content
    inner = ""

    def _action_detail_row(
        label: str,
        value_html: str,
        *,
        label_color: str = EMAIL_COLORS["muted"],
        value_color: str = EMAIL_COLORS["body"],
    ) -> str:
        return (
            '<tr class="azb-action-detail-row">'
            f'<td width="112" style="width: 112px; padding: 10px 8px 10px 16px; border-top: 1px solid {EMAIL_COLORS["line"]}; '
            f'vertical-align: top; font-size: {FONT_SIZE_PX["meta"]}px; font-weight: 700; '
            f'color: {label_color}; white-space: normal; line-height: 1.7;">{label}</td>'
            f'<td style="padding: 10px 16px 10px 8px; border-top: 1px solid {EMAIL_COLORS["line"]}; '
            f'vertical-align: top; font-size: {FONT_SIZE_PX["secondary"]}px; color: {value_color}; '
            f'line-height: 1.8; word-break: break-word; overflow-wrap: anywhere;">{value_html}</td></tr>'
        )

    if not items and recommendations:
        # Only show text-style recommendations when there are NO structured action items
        # to prevent duplicate rendering of the same information
        for i, rec in enumerate(recommendations, 1):
            inner += f"""
            <p class="azb-text" style="padding: 12px 0; margin: 0; font-size: 13px; color: {EMAIL_COLORS['body']}; border-bottom: 1px solid {EMAIL_COLORS['line']}; line-height: 1.8;"><strong style="color: {EMAIL_COLORS['accent']};">{i:02d}.</strong> {escape_email_text(rec)}</p>
            """
    elif items:
        for step_num, item in enumerate(items, 1):
            task = escape_email_text(item.task if hasattr(item, "task") else str(item))
            why = item.why if hasattr(item, "why") else ""
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
            card_border = _VERIFY_BORDER.get(verify_status, EMAIL_COLORS["line"])
            verify_badge = _format_verification_badge(verify_status, language)
            target_urls = {
                resource_links.get(str(target).strip().casefold(), "") for target in targets
            }
            target_urls.discard("")
            procedure_portal_url = (
                next(iter(target_urls)) if len(targets) == 1 and len(target_urls) == 1 else ""
            )

            inner += f"""
            <div class="azb-action" style="background-color: {EMAIL_COLORS['paper']}; margin-bottom: 20px; border: 1px solid {card_border}; border-top: 3px solid {EMAIL_COLORS['ink']};">
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" class="azb-action-header">
                    <tr>
                        <td width="48" style="padding: 16px 0 16px 16px; vertical-align: top;">
                            <span style="color: {EMAIL_COLORS['accent']}; font-size: 21px; font-weight: 700; line-height: 1.3;">{step_num:02d}</span>
                        </td>
                        <td style="padding: 16px 16px 16px 12px; vertical-align: top; overflow-wrap: anywhere;">
                            <h3 class="azb-action-title" style="margin: 0; font-size: 15px; font-weight: 700; color: {EMAIL_COLORS['ink']}; line-height: 1.65;">{task}</h3>
                            {f'<p style="margin: 8px 0 0;">{verify_badge}</p>' if verify_badge else ''}
                        </td>
                    </tr>
                </table>
            """

            context_rows = ""
            if targets:
                t_str = ", ".join(
                    _portal_link_html(
                        target,
                        resource_links.get(str(target).strip().casefold(), ""),
                    )
                    for target in targets[:3]
                )
                if len(targets) > 3:
                    t_str += f" {L['remaining_targets'].format(n=len(targets) - 3)}"
                context_rows += _action_detail_row(L["target"], t_str)
            if why:
                context_rows += _action_detail_row(L["why"], escape_email_text(why))
            if context_rows:
                inner += (
                    '<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
                    'width="100%" class="azb-action-context">'
                    f"{context_rows}</table>"
                )

            procedure_html = ""
            if procedure:
                steps = _split_procedure(procedure)
                if len(steps) <= 1:
                    procedure_html += (
                        f'<p class="azb-text" style="margin: 0; font-size: {FONT_SIZE_PX["body"]}px; '
                        f'color: {EMAIL_COLORS["body"]}; line-height: 1.8; overflow-wrap: anywhere;">'
                        f"{_link_portal_keyword(procedure, procedure_portal_url)}</p>"
                    )
                else:
                    ordinal = 0
                    for step_text, is_sub in steps:
                        if is_sub:
                            procedure_html += (
                                f'<p class="azb-text" style="margin: 6px 0 6px 20px; font-size: {FONT_SIZE_PX["body"]}px; '
                                f'color: {EMAIL_COLORS["body"]}; line-height: 1.8; overflow-wrap: anywhere;">&bull; '
                                f"{_link_portal_keyword(step_text, procedure_portal_url)}</p>"
                            )
                        else:
                            ordinal += 1
                            procedure_html += (
                                f'<p class="azb-text" style="margin: 8px 0; font-size: {FONT_SIZE_PX["body"]}px; '
                                f'color: {EMAIL_COLORS["body"]}; line-height: 1.8; overflow-wrap: anywhere;"><strong>{ordinal}.</strong> '
                                f"{_link_portal_keyword(step_text, procedure_portal_url)}</p>"
                            )

            if cli_command:
                cloud_shell_url = safe_email_href(_cloud_shell_url(cli_command))
                safe_command = escape_email_text(cli_command)
                command_html = (
                    f'<a href="{cloud_shell_url}" class="azb-link" '
                    f'title="{escape_email_text(L["cloud_shell_open"])}" '
                    f'style="display: block; color: {EMAIL_COLORS["paper"]}; text-decoration: none;">'
                    f"{safe_command}</a>"
                    if cloud_shell_url
                    else safe_command
                )
                procedure_html += (
                    f'<div class="azb-cli" style="margin: 12px 0 0; font-size: {FONT_SIZE_PX["secondary"]}px; color: {EMAIL_COLORS["paper"]}; '
                    f"font-family: {FONT_STACK_MONO}; "
                    f"background-color: {EMAIL_COLORS['ink']}; padding: 14px 16px; line-height: 1.8; "
                    f'white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere;">{command_html}</div>'
                )
                if cloud_shell_url:
                    procedure_html += (
                        f'<p style="margin: 8px 0 0; font-size: {FONT_SIZE_PX["secondary"]}px; '
                        f'color: {EMAIL_COLORS["muted"]};"><a href="{cloud_shell_url}" class="azb-link" '
                        f'style="color: {EMAIL_COLORS["accent"]}; text-decoration: underline;">'
                        f'{escape_email_text(L["cloud_shell_open"])} &rarr;</a></p>'
                    )
            if procedure_html:
                inner += (
                    '<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
                    'width="100%" class="azb-action-procedure">'
                    f'<tr><td style="padding: 16px; border-top: 1px solid {EMAIL_COLORS["line"]};">'
                    f'<p style="margin: 0 0 10px; font-size: {FONT_SIZE_PX["meta"]}px; '
                    f'font-weight: 700; color: {EMAIL_COLORS["muted"]};">{L["procedure"]}</p>'
                    f"{procedure_html}</td></tr></table>"
                )

            schedule_cells = []
            if deadline:
                schedule_cells.append((L["deadline"], escape_email_text(deadline)))
            if estimated_time:
                schedule_cells.append((L["estimated"], escape_email_text(estimated_time)))
            if schedule_cells:
                cell_width = 100 // len(schedule_cells)
                cells_html = ""
                for label, value in schedule_cells:
                    cells_html += (
                        f'<td width="{cell_width}%" style="padding: 12px 16px; border-top: 1px solid {EMAIL_COLORS["line"]}; '
                        f'vertical-align: top;"><span style="font-size: {FONT_SIZE_PX["meta"]}px; '
                        f'font-weight: 700; color: {EMAIL_COLORS["muted"]};">{label}</span><br>'
                        f'<span style="font-size: {FONT_SIZE_PX["body"]}px; color: {EMAIL_COLORS["ink"]}; '
                        f'line-height: 1.8; overflow-wrap: anywhere;">{value}</span></td>'
                    )
                inner += (
                    '<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
                    f'width="100%" class="azb-action-schedule" style="table-layout: fixed; background-color: {EMAIL_COLORS["wash"]};">'
                    f"<tr>{cells_html}</tr></table>"
                )

            guardrail_rows = ""
            if risk:
                guardrail_rows += _action_detail_row(
                    L["risk_if_not_done"],
                    escape_email_text(risk),
                    label_color=EMAIL_COLORS["danger"],
                    value_color=EMAIL_COLORS["danger"],
                )

            if precaution:
                guardrail_rows += _action_detail_row(L["precaution"], escape_email_text(precaution))

            if rollback:
                guardrail_rows += _action_detail_row(L["rollback"], escape_email_text(rollback))

            # reference_url comes from LLM output — only http(s) may become an anchor.
            safe_reference_url = safe_email_href(reference_url)
            if safe_reference_url:
                reference_link = (
                    f'<a href="{safe_reference_url}" class="azb-link" '
                    f'style="color: {EMAIL_COLORS["accent"]}; text-decoration: underline; word-break: break-all;">'
                    f"{escape_email_text(reference_url)}</a>"
                )
                guardrail_rows += _action_detail_row(L["action_reference"], reference_link)

            if verify_notes:
                note_color = _VERIFY_COLOR.get(verify_status, EMAIL_COLORS["muted"])
                findings = "<br>".join(f"&middot; {_escape(str(n))}" for n in verify_notes[:4])
                guardrail_rows += _action_detail_row(
                    L["verification"], findings, label_color=note_color, value_color=note_color
                )
            if guardrail_rows:
                inner += (
                    '<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
                    'width="100%" class="azb-action-guardrails">'
                    f"{guardrail_rows}</table>"
                )

            inner += "</div>"

    if not inner:
        return ""

    return format_email_section_html(L["action_items"], inner)


def format_reference_docs_html(docs: list, language: str = "ko") -> str:
    """Format reference documents as a self-contained <tr> section.

    Args:
        docs: List of doc dicts (title, url, description, related_content)
        language: Language code for UI labels

    Returns:
        Complete <tr> HTML block; empty string if no documents.
    """
    if not docs:
        return ""

    L = get_labels(language)
    inner = ""
    for index, doc in enumerate(docs[:5], 1):
        if isinstance(doc, dict):
            title = doc.get("title", "Document")
            url = doc.get("url", "#")
            summary = doc.get("description", "")
            context = doc.get("related_content", "")
        else:
            title = str(doc)
            url = "#"
            summary = ""
            context = ""

        safe_url = safe_email_href(url)
        safe_title = escape_email_text(title)
        inner += (
            '<tr><td width="32" style="padding: 0 10px 16px 0; vertical-align: top; '
            f'font-size: 12px; color: {EMAIL_COLORS["muted"]};">{index:02d}</td>'
            '<td style="padding: 0 0 16px; vertical-align: top; overflow-wrap: anywhere;">'
        )
        if safe_url:
            inner += f'<p style="margin: 0 0 6px; font-size: 13px;"><a href="{safe_url}" class="azb-link" style="color: {EMAIL_COLORS["accent"]}; text-decoration: underline; font-weight: 600;">{safe_title} &rarr;</a></p>'
        else:
            inner += (
                f'<p style="margin: 0 0 6px; font-size: 13px; font-weight: 600;">{safe_title}</p>'
            )
        if summary:
            inner += (
                f'<p class="azb-text" style="margin: 0 0 3px 0; font-size: '
                f'{FONT_SIZE_PX["body"]}px; color: {EMAIL_COLORS["body"]}; line-height: 1.8;">'
                f"{escape_email_text(summary)}</p>"
            )
            if context and context != summary:
                inner += f'<p class="azb-text-secondary" style="margin: 6px 0 0; font-size: 12px; color: {EMAIL_COLORS["muted"]}; line-height: 1.8;">{L["doc_context"]}: {escape_email_text(context)}</p>'
            else:
                inner += '<div style="height: 5px;"></div>'
        elif context:
            inner += (
                f'<p class="azb-text" style="margin: 0 0 8px 0; font-size: '
                f'{FONT_SIZE_PX["body"]}px; color: {EMAIL_COLORS["body"]}; line-height: 1.8;">'
                f"{escape_email_text(context)}</p>"
            )
        else:
            inner += '<div style="height: 4px;"></div>'
        inner += "</td></tr>"

    return format_email_section_html(
        L["reference_docs"],
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" '
        f'class="azb-references" style="table-layout: fixed;">{inner}</table>',
    )


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
    items = "".join(
        f'<li style="margin: 6px 0; font-size: 13px; line-height: 1.8; '
        f'color: {EMAIL_COLORS["body"]};">{escape_email_text(check)}</li>'
        for check in checks
    )
    return format_email_section_html(
        L["additional_checks"],
        f'<div class="azb-checks" style="padding: 6px 16px; '
        f'border-left: {SEMANTIC_ACCENT_WIDTH_PX}px solid {EMAIL_COLORS["warning"]}; '
        f'background-color: {EMAIL_COLORS["wash"]}; '
        f'overflow-wrap: anywhere;"><ul style="margin: 0; padding-left: 16px;">{items}</ul></div>',
    )


def format_relevance_evidence_html(evidence: str, language: str = "ko") -> str:
    """Render environment relevance with the same hierarchy as other evidence sections.

    Preserve category-aware applicability, value, and evidence limits without
    treating resource ownership as a prerequisite for relevance.

    Args:
        evidence: Relevance evidence text (1-2 sentences)
        language: Language code for UI labels

    Returns:
        Complete <tr> HTML block; empty string if no evidence.
    """
    if not evidence:
        return ""

    L = get_labels(language)
    return format_email_section_html(
        L["relevance_evidence"],
        f'<p class="azb-text" style="margin: 0; font-size: 13px; color: {EMAIL_COLORS["body"]}; '
        f'line-height: 1.85; overflow-wrap: anywhere;">{_inline_format(evidence)}</p>',
    )


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
        <td class="azb-pad" style="padding: 0 32px 20px;">
            <p style="margin: 0; font-size: 11px; color: {EMAIL_COLORS['muted']};">{text}</p>
        </td>
    </tr>
    """


def _no_action_needed(language: str = "ko") -> str:
    """Return placeholder HTML for when no action items exist."""
    L = get_labels(language)
    return (
        f'<p style="color: {EMAIL_COLORS["muted"]}; font-size: 13px;">'
        f"{L['no_action_needed']}</p>"
    )


def format_quick_decision_html(
    result,
    language: str = "ko",
) -> str:
    """Format operational facts as two-column label/value pairs, stacked on mobile.

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
        type_str = ", ".join(escape_email_text(value) for value in sorted(types)[:3])
        scope_text = (
            f"{count}{L['count_suffix']} {type_str}" if type_str else f"{count}{L['count_suffix']}"
        )
    else:
        scope_text = L["no_affected_resources"]

    # Action needed
    has_action = len(action_items) > 0
    action_text = L["yes"] if has_action else L["no"]
    action_color = EMAIL_COLORS["danger"] if has_action else EMAIL_COLORS["success"]

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

    facts = [
        (L["scope"], scope_text, EMAIL_COLORS["ink"]),
        (L["action_needed"], action_text, action_color),
    ]
    if deadline:
        facts.append((L["deadline"], escape_email_text(deadline), EMAIL_COLORS["ink"]))
    if work_text:
        facts.append((L["work_estimate"], escape_email_text(work_text), EMAIL_COLORS["ink"]))
    rows_html = ""
    for offset in range(0, len(facts), 2):
        cells = ""
        for label, value, color in facts[offset : offset + 2]:
            cells += (
                f'<td width="50%" class="azb-fact-cell" style="padding: 10px 16px 10px 0; '
                f'vertical-align: top; border-bottom: 1px solid {EMAIL_COLORS["line"]};">'
                f'<p style="margin: 0 0 4px; font-size: 11px; color: {EMAIL_COLORS["muted"]};">'
                f'{label}</p><p style="margin: 0; font-size: 13px; font-weight: 600; color: {color}; '
                f'line-height: 1.7; overflow-wrap: anywhere;">{value}</p></td>'
            )
        rows_html += f"<tr>{cells}</tr>"
    return format_email_section_html(
        L["quick_decision"],
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" '
        f'class="azb-qd" style="table-layout: fixed; border-collapse: collapse;">{rows_html}</table>',
    )


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
            dates.append((d, task))

    if not dates:
        return ""

    rows = "".join(
        f'<tr><td width="108" style="padding: 12px 12px 12px 0; vertical-align: top; '
        f'border-bottom: 1px solid {EMAIL_COLORS["line"]}; font-size: 12px; font-weight: 700; '
        f'color: {EMAIL_COLORS["accent"]}; overflow-wrap: anywhere;">{escape_email_text(date)}</td>'
        f'<td class="azb-tl-task" style="padding: 12px 0; font-size: 13px; line-height: 1.8; '
        f'border-bottom: 1px solid {EMAIL_COLORS["line"]}; color: {EMAIL_COLORS["body"]}; '
        f'overflow-wrap: anywhere;">{escape_email_text(task)}</td></tr>'
        for date, task in dates[:4]
    )
    return format_email_section_html(
        L["timeline"],
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" '
        f'class="azb-timeline" style="table-layout: fixed; border-collapse: collapse;">{rows}</table>',
    )


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
    "high": {"color": EMAIL_COLORS["danger"], "bg": "#fbefed"},
    "medium": {"color": EMAIL_COLORS["warning"], "bg": "#fcf5e9"},
    "low": {"color": EMAIL_COLORS["success"], "bg": "#edf6ef"},
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
        f'color:{colors["color"]}; border-left:{SEMANTIC_ACCENT_WIDTH_PX}px solid {colors["color"]}; '
        f"padding:4px 4px; "
        f'font-size:12px; font-weight:700; line-height:1.5; white-space:nowrap;">{label}</span>'
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
        f"padding:0 0 12px; font-size:11px; font-weight:700; color:{EMAIL_COLORS['muted']}; "
        f"border-bottom:1px solid {EMAIL_COLORS['ink']}; text-align:left;"
    )
    return f"""
        <tr class="azb-digest-heading">
            <th scope="col" class="azb-th" width="28%" style="{hdr_style}">{L['col_update_title']}</th>
            <th scope="col" class="azb-th azb-col-metric" width="24%" style="{hdr_style}">{L['col_importance']}</th>
            <th scope="col" class="azb-th azb-col-metric" width="24%" style="{hdr_style}">{L['col_impact']}</th>
            <th scope="col" class="azb-th azb-col-metric" width="24%" style="{hdr_style}">{L['col_job_relevance']}</th>
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

    title = escape_email_text(update.title)
    link = safe_email_href(update.link) or "#"

    # --- Skipped update — muted row spanning all columns ---
    if skip_reason or result is None:
        skip_text = escape_email_text(skip_reason or "")
        return f"""
        <tr class="azb-digest-skip">
            <td colspan="4" class="azb-cell" style="padding:16px 12px; background-color:{EMAIL_COLORS['wash']}; border-bottom:1px solid {EMAIL_COLORS['line']}; overflow-wrap:anywhere;">
                <span class="azb-skip-badge" style="display:block; color:{EMAIL_COLORS['muted']}; font-size:11px; font-weight:700; margin-bottom:6px;">{L['digest_skipped_label']}</span>
                <a href="{link}" class="azb-skip-link" style="color:{EMAIL_COLORS['body']}; text-decoration:underline; font-size:13px;">{title}</a>
                {f'<p class="azb-text-muted" style="margin:6px 0 0; font-size:12px; color:{EMAIL_COLORS["muted"]};">{skip_text}</p>' if skip_text else ''}
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

    cell_style = (
        f"padding:16px 8px 16px 0; border-bottom:1px solid {EMAIL_COLORS['line']}; "
        "vertical-align:top; text-align:left;"
    )
    metrics = "".join(
        f'<td class="azb-cell azb-col-metric" style="{cell_style}">'
        f'<span class="azb-metric-label" style="display:none; mso-hide:all; font-size:11px; '
        f'color:{EMAIL_COLORS["muted"]}; line-height:1.5;">{label}</span>'
        f"{_level_badge_html(level, language)}</td>"
        for label, level in (
            (L["col_importance"], importance),
            (L["col_impact"], impact_level),
            (L["col_job_relevance"], job_rel_level),
        )
    )
    number = f"{anchor_index:02d}. " if anchor_index else ""
    summary = escape_email_text(getattr(result, "one_line_summary", "") or "")
    return (
        f'<tr class="azb-digest-row"><td class="azb-cell azb-digest-title" '
        f'style="{cell_style} padding-right:20px; overflow-wrap:anywhere; word-break:break-word;">'
        f'<a href="{anchor_href}" class="azb-text" style="color:{EMAIL_COLORS["ink"]}; '
        f'text-decoration:underline; font-size:13px; font-weight:700; line-height:1.7;">'
        f"{number}{title}</a>"
        f'<p style="margin:6px 0 0; font-size:12px; color:{EMAIL_COLORS["muted"]}; '
        f'line-height:1.7;">{summary}</p></td>{metrics}</tr>'
    )
