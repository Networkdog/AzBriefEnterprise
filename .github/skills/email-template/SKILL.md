---
name: email-template
description: 'Edit HTML email templates for AzBrief reports. Use when: email template, HTML email, email styling, format_affected_resources_html, format_action_items_html, format_digest_table_header_html, markdown_to_html, EmailService, Korean email content, inline CSS email, email client compatibility.'
---

# Email Template Editing

## Foundry Runtime Guidance

- As the report writer, return only the requested schema; the deterministic renderer owns HTML, CSS, labels,
    responsiveness, and client compatibility.
- Layer the content for scanning: decisive summary, compact evidence, operational detail,
    then executable actions. Do not repeat conclusions across fields.
- Keep resource reasons and action fields concise, self-contained, and renderable without
    reconstructing missing context.
- Use the requested language and verified HTTP(S) links only. Never expose HTML, tracking
    wrappers, unsafe URLs, fabricated links, schema names, tools, queries, or delivery details.

<!-- End Foundry Runtime Guidance -->

## When to Use

- Modifying HTML email layout in `src/email/templates.py`
- Adding new sections to the email report
- Changing Korean labels or translations (`src/i18n/labels/`)
- Working on `markdown_to_html()` converter
- Modifying `EmailService` in `src/email/service.py`
- Adding plain text fallback content

## File Structure

| File | Purpose |
|------|---------|
| `src/email/templates.py` | HTML template, helper functions, `get_labels` re-export |
| `src/email/service.py` | `EmailService` — sends via Azure Communication Services |
| `src/i18n/labels/<code>.py` | UI label bundle for one language (`ko.py` is canonical) |

## Key Components in `templates.py`

### UI labels — `get_labels(lang)`

Labels live in `src/i18n/labels/<code>.py`, one `LABELS` dict per language.
`templates.py` re-exports `get_labels` so renderers keep importing it from here.

```python
# src/i18n/labels/ko.py — the canonical key set
LABELS: dict[str, str] = {
    "analysis_summary": "개요",
    "impact_analysis": "영향 분석",
    "affected_resources": "영향받는 리소스",
    ...
}
```

Add every new key to `ko.py` first — `label_keys()` derives the canonical set from
it, and `missing_label_keys("ja")` reports what a language has not translated yet.
Missing keys are backfilled through the registry fallback chain, so a partial
translation renders in the fallback language instead of raising `KeyError`.

### `HTML_EMAIL_TEMPLATE`

Main Jinja-style HTML string with `{placeholder}` variables. Uses **inline CSS only** for email client compatibility.

### Helper Functions

| Function | Purpose |
|----------|---------|
| `format_impact_section_html()` | 영향/기회 차원(비용·보안·성능·운영). `update_category`가 `CAPABILITY_CATEGORIES`(new_feature, new_service, region_expansion, preview, sdk_tooling)면 섹션 제목이 `impact_analysis`(영향 분석) 대신 `opportunity_analysis`(활용 기회)로 바뀜다 |
| `format_affected_resources_html()` | Reason-grouped resource table; name/subscription/resource-group become Portal links when ARM identity is unambiguous |
| `format_action_items_html()` | Context/procedure/schedule/guardrails cards; unique targets reuse Portal links and CLI opens the matching Cloud Shell |
| `format_reference_docs_html()` | Microsoft Learn links with a factual 1-2 sentence `description` and report-specific `related_content` |
| `format_additional_checks_html()` | Additional verification items |
| `format_quick_decision_html()` | Summary verdict (relevance, scope, action) |
| `format_relevance_evidence_html()` | 단건과 digest 상세의 환경 연관성: 적용/가치 근거와 판단 한계를 원문대로 표시 |
| `format_timeline_html()` | Key dates/milestones |
| `format_digest_table_header_html()` | Table header row for digest summary (columns: title, importance, impact, job relevance) |
| `format_digest_update_card_html()` | Digest summary table row with importance/impact/job-relevance badges (높음/보통/낮음) and anchor link |
| `format_archive_link_html()` | Optional HTTPS-only link to the authenticated shared canonical analysis; omitted when no archive URL is available |
| `format_feedback_link_html()` | Localized HTTPS-only footer link to `/feedback`; omitted unless the trusted feedback base URL is configured |
| `markdown_to_html()` | Markdown → inline-styled HTML for email (headings, lists, `>` concept boxes, **bold**, `code`, and safe `[text](url)` links). Text is HTML-escaped before formatting; only in-message anchors and allow-listed Microsoft/Azure/GitHub/Azure Weekly HTTPS URLs become `<a>` tags |
| `escape_email_text()` / `safe_email_href()` | Escape every untrusted literal field and restrict clickable URLs to the email allow-list; unsupported URLs degrade to text or `#` without loading remote content |
| `get_labels(lang)` | Get label dict for language |
| `get_urgency_colors(urgency)` | Color scheme by urgency level |
| `get_relevance_colors(relevance)` | Color scheme by relevance level |
| `get_importance_level(urgency, relevance, importance)` | Derive importance (high/medium/low); prefers LLM-assessed `importance` field, falls back to urgency×relevance |
| `get_importance_colors(importance)` | Color scheme by importance level |
| `_urgency_to_level(urgency, impact_level)` | Map impact_level to three-tier level; falls back to urgency if not available |
| `_relevance_to_level(relevance, job_relevance)` | Map job_relevance to three-tier level; falls back to relevance if not available |
| `_level_badge_html(level, lang)` | Color-coded badge span for a level (높음/보통/낮음) |

### Email/Archive Markdown parity

The existing `relevance_evidence` key is labeled Environment Relevance / 환경 연관성 /
環境との関連性 in Archive, single/digest HTML and plain text, and the judge's rendered report.
It is not a selection justification. Never hide it merely because no resource rows exist or
rewrite a historical Archive value while rendering. Generation, not the renderer, owns the
category-aware applicability/value decision.

The email narrative and the shared canonical Archive use the same restricted Markdown vocabulary.
When adding or changing headings, lists, blockquotes/concept boxes, code, bold text, or links in
`markdown_to_html()`, update the safe DOM renderer in `src/archive/page.py` as well. Archive must not
reuse email HTML or `innerHTML`; it constructs nodes with `textContent` and allow-listed links. Cover
both the API marker round-trip and a rendered browser DOM so a `> **Term**:` box cannot silently
degrade to plain text in one channel.

## Email Client Compatibility Rules

1. **Inline CSS for light mode** — inline styles are the light mode default; all email clients use them
2. **`<style>` block for dark mode only** — `@media (prefers-color-scheme: dark)` overrides inline styles via `!important`. Clients that strip `<style>` blocks (Gmail) use their own auto-dark-mode
3. **CSS classes for dark mode targeting** — structural elements use `azb-*` class names (e.g., `azb-body`, `azb-card`, `azb-text`, `azb-panel`). Add classes to new elements that need dark mode color overrides
4. **`_DARK_MODE_STYLE` constant** — shared dark mode `<style>` block used by both `HTML_EMAIL_TEMPLATE` and digest email. Update this when adding new CSS classes
5. **`_CLIENT_COMPAT_STYLE` constant (Outlook/Windows hardening)** — head `<style>` block with `table { mso-table-lspace/rspace: 0pt }` (removes Outlook Word-engine cell spacing), `img` resets, and `word-break` for `.azb-cli`/`.azb-code`. Windows Outlook honors `<head>` styles (Gmail strips them, but Gmail needs no `mso-*`). Use `_CLIENT_COMPAT_STYLE_ESCAPED` in `.format()` contexts. Keep the system-only Korean font order as **`'AppleSDGothicNeo-Regular'`, `'Microsoft GothicNeo'`, `'맑은 고딕'`**, followed by compatibility aliases and cross-platform fallbacks
6. **`_RESPONSIVE_STYLE` constant (hybrid responsive layout)** — see "Responsive Layout" below. Use `_RESPONSIVE_STYLE_ESCAPED` in `.format()` contexts
7. **Table-based layout** — do not rely on flexbox or grid
8. **No JavaScript** — email clients strip all scripts
9. **Image fallback** — always provide alt text
10. **`{` braces escape** — literal `{` in HTML must use `_escape_braces()` to avoid `KeyError` in `str.format()`
11. **Card width** — fluid `width="100%"` capped at `max-width: 640px`, never a hardcoded `width="640"`
12. **Untrusted report values** — RSS text, tool output, and LLM fields must pass through `escape_email_text()` or a renderer that calls `_inline_format()`; never interpolate them directly into markup
13. **Link allow-list** — call `safe_email_href()` before writing `href`. HTTP, credentials in URLs, non-approved hosts, `javascript:`, and `data:` never become links. Apply the same validation to HTML and plain-text archive links
14. **No remote webfonts** — Admin/Archive may use the pinned browser font policy from `src/web_fonts.py`, but email HTML must keep its system-font stack. Do not add `@font-face`, remote font URLs, or an Apple font binary to email templates
15. **Portal identity before convenience** — create `#resource` links only from a validated subscription GUID plus an unambiguous ARM ID. A top-level resource may be reconstructed from subscription, resource group, type, and name; nested resources need their complete ID. Do not guess management-group blades or service-specific menu routes from prose
16. **Cloud Shell opens, it does not execute** — use only the documented `feature.azureconsole.shell=bash|pwsh#cloudshell` entry points. The command remains visible for manual copy; never imply that clicking prefills or runs it
17. **Reference summaries are evidence-bound** — render `description` as the document's 1-2 sentence factual summary and `related_content` as what this report asks the reader to verify. If fetched page content was unavailable, leave `description` empty rather than inferring it from the URL or title
15. **Scoped subscriber isolation** — A Management Group/Subscription/Resource Group subscriber gets only the scoped Hosted result. Never fall back to the canonical result, render the global retirement tracker, or link to the differently scoped canonical Archive entry when scoped analysis fails or succeeds

## Type Scale

`FONT_SIZE_PX` in `templates.py` is the single source of truth. Body copy sits at
**12px**, the compact size used for regular email text, and every other size is a
proportional step on the same scale.

| Key | px | Ratio | Used for |
|-----|----|-------|----------|
| `meta` | 10 | 0.833x | badges, table headers, timestamps, footer fine print |
| `secondary` | 11 | 0.917x | table cells, action detail lines, CLI blocks, inline code |
| `body` | 12 | 1x | prose paragraphs, list items, concept boxes, impact values |
| `heading` | 14 | 1.167x | section labels (개요, 영향 분석, 액션 아이템 …) |
| `title` | 16 | 1.333x | update titles in the header and digest detail |
| `masthead` | 20 | 1.667x | the AzBrief wordmark |

`markdown_to_html()` derives its `#`–`####` heading sizes from the same dict.
`test_font_sizes_follow_the_type_scale` fails the build if a rendered email
contains any `font-size` outside this set — add a step to `FONT_SIZE_PX` rather
than introducing a one-off px value.

## Responsive Layout[]

The card is **hybrid** (fluid + media queries), so it degrades gracefully in clients
that strip `<style>` (e.g. Gmail app with a non-Gmail account):

| Layer | Mechanism | Covers |
|-------|-----------|--------|
| Fluid card | `width="100%"` + `style="max-width: 640px"` | Every client, even without `<style>` |
| `@media` overrides | `_RESPONSIVE_STYLE` in `<head>` | Apple Mail, iOS, Gmail, Outlook.com |
| MSO ghost table | `<!--[if mso]><table width="640">…<![endif]-->` around the card | Windows Outlook (ignores `@media` and `max-width`) |

Media queries key off classes, because `!important` cannot override an inline
style without a selector. Add the matching class when you add an element:

| Class | Effect below 640px |
|-------|--------------------|
| `azb-outer` | Outer gutter shrinks to 6px |
| `azb-pad` | Section gutters 32px → 16px (→ 12px below 400px). Put it on **every** `<td>` with 32/36px horizontal padding |
| `azb-stack` (on the inner table) + `azb-stack-tail` (on the right-aligned `<td>`) | Two-column "text + right badge" row stacks vertically |
| `azb-col-metric` | Digest 중요성/영향도/직무연관성 columns shrink to ~46px so the title column stays readable |
| `azb-qd` / `azb-qd-label` | Quick decision 4-column grid becomes label-over-value blocks |

Above 640px the card grows so the extra width goes to the content instead of the
backdrop. The 900px ceiling is a readability limit, not a technical one: 640px
caps Korean prose at ~48 chars per line, 900px at ~67, and longer lines cost more
than the recovered space is worth.

| Breakpoint | Effect |
|-----------|--------|
| `min-width: 800px` | Card 640px → 760px; `azb-tl-task` (timeline label) 120px → 200px |
| `min-width: 1100px` | Card → 900px; gutters → 44px; `azb-impact` rows pair up 2×2 via `display: inline-table` |

The impact dimension label (`azb-impact-label`) uses both HTML `width="96"` and inline
`width`/`min-width` with `white-space: nowrap` and `word-break: keep-all`. Keep all four because
Windows Outlook can ignore individual CSS sizing mechanisms and otherwise wraps Korean one glyph
per line.

Windows Outlook ignores `min-width` queries too, so it stays at the 640px ghost
table — intended, since its reading pane is usually narrow.

## Korean Content Rules

- All user-facing text: Korean
- Add new label keys to `src/i18n/labels/ko.py` first, then translate in `en.py` / `ja.py`
- Urgency prefixes: `[긴급]`, `[중요]`
- Default "no data" messages: `"영향받는 리소스가 없습니다."`, etc.

## `EmailService` in `service.py`

- Sends via **Azure Communication Services** (`azure-communication-email` SDK)
- Lazy-imports `EmailClient` to avoid import errors when email is not configured
- Falls back to **console output** when `COMMUNICATION_SERVICES_CONNECTION_STRING` is not set
- Builds plain text separately in `_build_plain_text()`
- Supports protected bootstrap subscribers from `SUBSCRIBERS` plus mutable profiles from the
    private Admin configuration Blob; delivery code resolves the merged list through
    `get_admin_configuration().get_subscribers()`
- Accepts an optional `archive_url` for single and digest HTML/plain-text output. The link always names the shared canonical analysis; subscriber-customized content is not archived
- Adds one localized Feedback link to the bottom of single and digest HTML/plain-text reports. The URL may carry only a bounded Update ID or digest date range
- `send_feedback_notification()` sends the already-persisted strict feedback document to the explicitly configured `FEEDBACK_RECIPIENT_ADDRESS` and never logs its body or contact address; when unset, persistence succeeds and notification fails closed

## Adding a New Section

Adding a new section is a **4+ file chain**:

1. Add the label key to `src/i18n/labels/ko.py`, then translate it in `en.py` / `ja.py`
2. Create helper function: `format_<section>_html(data, lang="ko") -> str`
3. Add placeholder in `HTML_EMAIL_TEMPLATE`
4. Update `EmailService._build_email_content()` in `service.py` to populate the placeholder
5. Update `_build_plain_text()` in `service.py` with text equivalent
6. Escape any literal `{}` braces with `_escape_braces()`

### Verification After Changes

🚨 **MANDATORY** — Run these after any template change:

```bash
python -c "import src"                              # Import check
python -m pytest tests/test_email.py -o "addopts=" -x  # Email tests
```

## Common Pitfalls

| Issue | Cause | Fix |
|-------|-------|-----|
| `KeyError` in `str.format()` | Literal `{}` in template | Use `_escape_braces()` |
| Broken layout in Outlook | CSS not inline | Move styles to `style=""` attributes |
| Missing Korean text | Label not in `src/i18n/labels/ko.py` | Add it there — `ko.py` is the canonical key set |
| Email not sent | No connection string | Falls back to console — this is expected |
| ⚠️ Label renders in Korean for a `ja` reader | Key missing from `ja.py` | Expected fallback — check `missing_label_keys("ja")` and translate |
| ⚠️ Badge color wrong | Level mapping returns unexpected value | Check `_urgency_to_level()` / `_relevance_to_level()` |
| Dark mode colors not applied | Missing `azb-*` class on element | Add appropriate CSS class from `_DARK_MODE_STYLE` |
| Dark mode `{{` in output | Used `_DARK_MODE_STYLE` in `.format()` template | Use `_DARK_MODE_STYLE_ESCAPED` in `.format()` contexts; use `_DARK_MODE_STYLE` in f-strings |
| Section keeps 32px gutters on a phone | `azb-pad` missing on the section `<td>` | Add it — inline padding cannot be overridden without a class selector |
| Card stretches full width in Windows Outlook | MSO ghost table missing or unbalanced | Both `<!--[if mso]>` open and `<![endif]-->` close must wrap the card |
