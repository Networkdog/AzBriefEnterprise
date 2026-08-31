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
| `format_affected_resources_html()` | Resource table; resources sharing the same impact reason merge into one row (with a group-size badge, reason shown once) |
| `format_action_items_html()` | Action items with steps, urgency, deadlines |
| `format_reference_docs_html()` | Microsoft Learn doc links |
| `format_additional_checks_html()` | Additional verification items |
| `format_quick_decision_html()` | Summary verdict (relevance, scope, action) |
| `format_timeline_html()` | Key dates/milestones |
| `format_digest_table_header_html()` | Table header row for digest summary (columns: title, importance, impact, job relevance) |
| `format_digest_update_card_html()` | Digest summary table row with importance/impact/job-relevance badges (높음/보통/낮음) and anchor link |
| `format_archive_link_html()` | Optional HTTPS-only link to the authenticated shared canonical analysis; omitted when no archive URL is available |
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

## Email Client Compatibility Rules

1. **Inline CSS for light mode** — inline styles are the light mode default; all email clients use them
2. **`<style>` block for dark mode only** — `@media (prefers-color-scheme: dark)` overrides inline styles via `!important`. Clients that strip `<style>` blocks (Gmail) use their own auto-dark-mode
3. **CSS classes for dark mode targeting** — structural elements use `azb-*` class names (e.g., `azb-body`, `azb-card`, `azb-text`, `azb-panel`). Add classes to new elements that need dark mode color overrides
4. **`_DARK_MODE_STYLE` constant** — shared dark mode `<style>` block used by both `HTML_EMAIL_TEMPLATE` and digest email. Update this when adding new CSS classes
5. **`_CLIENT_COMPAT_STYLE` constant (Outlook/Windows hardening)** — head `<style>` block with `table { mso-table-lspace/rspace: 0pt }` (removes Outlook Word-engine cell spacing), `img` resets, and `word-break` for `.azb-cli`/`.azb-code`. Windows Outlook honors `<head>` styles (Gmail strips them, but Gmail needs no `mso-*`). Use `_CLIENT_COMPAT_STYLE_ESCAPED` in `.format()` contexts. The body `font-family` includes **`'Malgun Gothic'`** so Korean glyphs render on Windows/Outlook (the primary audience), alongside macOS `'Apple SD Gothic Neo'`
6. **`_RESPONSIVE_STYLE` constant (hybrid responsive layout)** — see "Responsive Layout" below. Use `_RESPONSIVE_STYLE_ESCAPED` in `.format()` contexts
7. **Table-based layout** — do not rely on flexbox or grid
8. **No JavaScript** — email clients strip all scripts
9. **Image fallback** — always provide alt text
10. **`{` braces escape** — literal `{` in HTML must use `_escape_braces()` to avoid `KeyError` in `str.format()`
11. **Card width** — fluid `width="100%"` capped at `max-width: 640px`, never a hardcoded `width="640"`
12. **Untrusted report values** — RSS text, tool output, and LLM fields must pass through `escape_email_text()` or a renderer that calls `_inline_format()`; never interpolate them directly into markup
13. **Link allow-list** — call `safe_email_href()` before writing `href`. HTTP, credentials in URLs, non-approved hosts, `javascript:`, and `data:` never become links. Apply the same validation to HTML and plain-text archive links

## Type Scale

`FONT_SIZE_PX` in `templates.py` is the single source of truth. Body copy sits at
**16px** — the browser/email default — and every other size is a step on the same
scale, so the hierarchy stays proportional if the base moves.

| Key | px | Ratio | Used for |
|-----|----|-------|----------|
| `meta` | 12 | 0.75x | badges, table headers, timestamps, footer fine print |
| `secondary` | 14 | 0.875x | table cells, action detail lines, CLI blocks, inline code |
| `body` | 16 | 1x | prose paragraphs, list items, concept boxes, impact values |
| `heading` | 18 | 1.125x | section labels (개요, 영향 분석, 액션 아이템 …) |
| `title` | 20 | 1.25x | update titles in the header and digest detail |
| `masthead` | 26 | 1.625x | the AzBrief wordmark |

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
| `azb-col-reason` | Affected-resources 사유 column drops its fixed 60% width |
| `azb-qd` / `azb-qd-label` | Quick decision 4-column grid becomes label-over-value blocks |

Above 640px the card grows so the extra width goes to the content instead of the
backdrop. The 900px ceiling is a readability limit, not a technical one: 640px
caps Korean prose at ~44 chars per line, 900px at ~62, and longer lines cost more
than the recovered space is worth.

| Breakpoint | Effect |
|-----------|--------|
| `min-width: 800px` | Card 640px → 760px; `azb-tl-task` (timeline label) 120px → 200px |
| `min-width: 1100px` | Card → 900px; gutters → 44px; `azb-impact` rows pair up 2×2 via `display: inline-table` |

The impact dimension label (`azb-impact-label`) is not in that table: it is `width: 1%` + `white-space: nowrap` at **every** width, so the column hugs its text (51px for 리소스 한글 2글자, wider for `Performance` / `パフォーマンス`). `nowrap` is required — Korean breaks between characters, so without it the column collapses to one glyph wide.

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
- Supports multiple subscribers (configured via `SUBSCRIBERS` in settings)
- Accepts an optional `archive_url` for single and digest HTML/plain-text output. The link always names the shared canonical analysis; subscriber-customized content is not archived

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
