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
| [scripts/preview_email.py](../../../scripts/preview_email.py) | Synthetic, offline ko/en/ja single/digest previews with full and inline-only styles |
| [tests/test_email_editorial.py](../../../tests/test_email_editorial.py) | Editorial structure, navigation, count/identity preservation, contrast, and offline preview contracts |
| [tests/browser/email_reports.cjs](../../../tests/browser/email_reports.cjs) | Repeatable local-only 72-layout Playwright check, navigation, content parity, badge bounds, and screenshots |

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

### `HTML_EMAIL_TEMPLATE` / `HTML_DIGEST_TEMPLATE`

Both use `str.format()` placeholders and the shared `_EMAIL_DOCUMENT_START` / `_EMAIL_DOCUMENT_END`
shell: one preheader, masthead, white paper surface, and footer. Inline CSS is the fallback;
head styles add client resets and responsive enhancements. This is not a Jinja template.

`EMAIL_COLORS` centralizes white paper, ink `#182b32`, teal `#08746b`, and the pale neutral
canvas `#eef1f0`. Keep the editorial hierarchy, not the former dark navy hero or rounded,
shadowed cards. Shared section headings are 21px `h2` elements in a desktop label rail; prose uses 13px
text with 1.8–1.85 line height. Single reports and digest details share a white hero, takeaway,
independent importance/impact/job-relevance strip, and two-column operational facts.

Use shared `SEMANTIC_ACCENT_WIDTH_PX = 4` for level and verification badges,
the summary takeaway, concept boxes, and additional checks. Retain badge top/bottom padding at
4px. Preserve visible status text, existing colors, thin neutral dividers, and text contrast
**≥4.5:1**.

### Helper Functions

| Function | Purpose |
|----------|---------|
| `format_email_masthead_html()` | 36px publication wordmark and edition/date metadata; stacked baseline, desktop columns |
| `format_report_header_html()` | White title/metadata area, takeaway, independent three-axis strip, safe source/Archive links, and digest back-link |
| `format_email_section_html()` | 21px heading, 24%/76% desktop label/content rail, stacked fallback; `full_width=True` for contents |
| `format_email_footer_html()` | Shared localized disclaimer, generation metadata, and Feedback link |
| `format_digest_intro_html()` | HTML digest totals with analyzed high/medium/low counts separate from skipped items |
| `format_impact_section_html()` | 영향/기회 차원(비용·보안·성능·운영). `update_category`가 `CAPABILITY_CATEGORIES`(new_feature, new_service, region_expansion, preview, sdk_tooling)면 섹션 제목이 `impact_analysis`(영향 분석) 대신 `opportunity_analysis`(활용 기회)로 바뀜다 |
| `format_affected_resources_html()` | Full-width shared reason followed by resource/subscription/resource-group/type columns; mobile labels preserve grouping and Portal identity |
| `format_action_items_html()` | Numbered `01` action sheets: context, procedure, dark monospaced command, schedule, guardrails; verification and safe Portal/Cloud Shell links are unchanged |
| `format_reference_docs_html()` | Numbered references with a factual 1-2 sentence `description` and report-specific `related_content` |
| `format_additional_checks_html()` | Additional verification items, placed before references |
| `format_quick_decision_html()` | Two-column operational facts (scope, action, deadline, work), stacked on mobile |
| `format_relevance_evidence_html()` | 단건과 digest 상세의 환경 연관성: 적용/가치 근거와 판단 한계를 원문대로 표시 |
| `format_timeline_html()` | Two-column date/milestone list |
| `format_visual_assets_html()` | Optional full-width Learn screenshots with alt text, captions, and source links; trusted Microsoft HTTPS hosts and PNG/JPEG/GIF only |
| `format_visual_assets_text()` | Plain-text caption/source fallback for accepted visuals |
| `format_digest_table_header_html()` | Table header row for digest summary (columns: title, importance, impact, job relevance) |
| `format_digest_update_card_html()` | Numbered, untruncated contents title and three labeled metrics; analyzed items link to details, skipped items remain explicit rows |
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

Digest counts use 48px tabular numerals on three tinted panels and an 8px proportional bar. Its denominator
is `high + medium + low`, never total items including skipped rows; omit zero segments and the
whole bar for zero analyzed items. The bar is redundant/aria-hidden, with exact localized counts
always visible. At three digits all counters use 29px. Contents use 17px titles, 13px summaries
and separate 29px number cells. Digest details open with a full-width teal band holding a 48px
number and contents-return link in `on_accent`; single reports omit chapter navigation. Takeaways
use 21px text. Operational
facts use a pale inline background with a teal top rule and 12px/16px cell padding. Title letter
spacing is zero. Remote images are limited to deterministic `visual_assets` extracted from fetched
Microsoft Learn article bodies: PNG/JPEG/GIF over HTTPS on the dedicated Microsoft image allow-list,
with non-empty alt text, captions, and source links. Single reports show at most two; digests show at
most one per update and four total. Never accept an LLM-invented image URL, tracking image, SVG,
`data:` URL, credentialed URL, or custom remote font. Keep the full text useful when images are blocked.
The delivery-only field is excluded from Archive v1 rather than changing its immutable schema.

### Email/Archive Markdown parity

The editorial redesign adds no Markdown vocabulary and changes no analysis behavior, transport,
or Archive schema. Keep the bounded Foundry Runtime Guidance section unchanged for styling-only work.

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

Archive and Feedback now share the Admin browser shell and local icons. Browser-only `archive_*`
and `feedback_*` interaction labels still belong in the canonical ko bundle before en/ja translations.
Archive feedback links carry only `archive:<immutable-id>` and preserve the browser language;
never put report bodies, subscriber details, or storage URLs in those links. Feedback acceptance
and notification delivery remain separate states. `scripts/preview_web.py` reuses synthetic email
fixtures for the three web surfaces without Azure calls or delivery; pair page tests with
`tests/browser/control_surfaces.cjs`. None of these browser changes alter email markup or runtime prompts.

## Email Client Compatibility Rules

1. **Inline CSS for the light-mode baseline** — preserve a usable white-paper document when head styles are stripped
2. **Head styles enhance the baseline** — `_CLIENT_COMPAT_STYLE` supplies resets and `_RESPONSIVE_STYLE` supplies media queries; neither replaces inline defaults
3. **CSS classes for responsive targeting** — retain matching `azb-*` classes so media queries can override inline styles. `azb-card` is the width hook, not a rounded-card design
4. **No custom dark-mode overrides** — `_DARK_MODE_STYLE` is intentionally inert and the document declares `light only`; client auto-dark-mode remains client-controlled
5. **`_CLIENT_COMPAT_STYLE` constant (Outlook/Windows hardening)** — head `<style>` block with `table { mso-table-lspace/rspace: 0pt }` (removes Outlook Word-engine cell spacing), `img` resets, and `word-break` for `.azb-cli`/`.azb-code`. Windows Outlook honors `<head>` styles (Gmail strips them, but Gmail needs no `mso-*`). Use `_CLIENT_COMPAT_STYLE_ESCAPED` in `.format()` contexts. Set the shared `FONT_STACK_SANS` exactly to `'Apple SD Gothic Neo', 'Malgun Gothic', 'Dotum', Arial, Helvetica, sans-serif`; commands and code blocks retain the separate monospace stack
6. **`_RESPONSIVE_STYLE` constant (hybrid responsive layout)** — see "Responsive Layout" below. Use `_RESPONSIVE_STYLE_ESCAPED` in `.format()` contexts
7. **Table-based layout** — do not rely on flexbox or grid
8. **No JavaScript** — email clients strip all scripts
9. **Image fallback** — always provide alt text
     - Visuals must also retain a visible caption and source-document link; absence or rejection omits
         the entire visual section without removing any report text
10. **`{` braces escape** — literal `{` in HTML must use `_escape_braces()` to avoid `KeyError` in `str.format()`
11. **Paper width** — fluid `width="100%"` with inline `max-width: 640px`; only the MSO ghost table uses fixed `width="640"`. Media-query caps are 760px at 800px and 900px at 1100px
12. **Untrusted report values** — RSS text, tool output, and LLM fields must pass through `escape_email_text()` or a renderer that calls `_inline_format()`; never interpolate them directly into markup
13. **Link allow-list** — call `safe_email_href()` before writing `href`. HTTP, credentials in URLs, non-approved hosts, `javascript:`, and `data:` never become links. Apply the same validation to HTML and plain-text archive links
14. **No remote webfonts** — Admin/Archive may use the pinned browser font policy from `src/web_fonts.py`, but email HTML must keep its system-font stack. Do not add `@font-face`, remote font URLs, or an Apple font binary to email templates
15. **Portal identity before convenience** — create `#resource` links only from a validated subscription GUID plus an unambiguous ARM ID. A top-level resource may be reconstructed from subscription, resource group, type, and name; nested resources need their complete ID. Do not guess management-group blades or service-specific menu routes from prose
16. **Cloud Shell opens, it does not execute** — use only the documented `feature.azureconsole.shell=bash|pwsh#cloudshell` entry points. The command remains visible for manual copy; never imply that clicking prefills or runs it
17. **Reference summaries are evidence-bound** — render `description` as the document's 1-2 sentence factual summary and `related_content` as what this report asks the reader to verify. If fetched page content was unavailable, leave `description` empty rather than inferring it from the URL or title
18. **Scoped subscriber isolation** — A Management Group/Subscription/Resource Group subscriber gets only the scoped Hosted result. Never fall back to the canonical result, render the global retirement tracker, or link to the differently scoped canonical Archive entry when scoped analysis fails or succeeds
19. **Remote visual allow-list** — only descriptive PNG/JPEG/GIF candidates extracted by
    `MicrosoftLearnService.fetch_page_content()` may become `<img>` elements. Revalidate scheme,
    hostname, port, credentials, extension, and alt text in the renderer; never trust model output

## Type Scale

`FONT_SIZE_PX` is the single source of truth. Body copy stays **13px**; `cover=36` and `stat=48`
are separate publication display steps, not a global text increase. Ratios use the body baseline.

| Key | px | Ratio | Used for |
|-----|----|-------|----------|
| `meta` | 11 | 0.846x | field labels, table headers, timestamps, footer fine print |
| `secondary` | 12 | 0.923x | badges, table cells, action detail lines, CLI blocks, inline code |
| `body` | 13 | 1x | prose paragraphs, list items, concept boxes, impact values |
| `heading` | 15 | 1.154x | action `h3` titles |
| `title` | 17 | 1.308x | contents titles and retirement countdown values |
| `masthead` | 21 | 1.615x | section headings, takeaways, action numbers |
| `display` | 25 | 1.923x | mobile main hero override |
| `hero` | 29 | 2.231x | digest detail titles, contents numbers, three-digit counters, mobile wordmark |
| `cover` | 36 | 2.769x | wordmark, document title, mobile two-digit counters |
| `stat` | 48 | 3.692x | desktop/inline two-digit counters and chapter numbers |

`markdown_to_html()` derives its `#`–`####` heading sizes from the same dict.
`test_font_sizes_follow_the_type_scale` fails the build if a rendered email
contains any `font-size` outside this set — add a step to `FONT_SIZE_PX` rather
than introducing a one-off px value.

## Responsive Layout

The paper surface is **hybrid** (fluid + media queries), so it degrades gracefully in clients
that strip `<style>` (e.g. Gmail app with a non-Gmail account):

| Layer | Mechanism | Covers |
|-------|-----------|--------|
| Fluid paper | `width="100%"` + `style="max-width: 640px"` | Inline-only baseline, with 32px section gutters |
| `@media` overrides | `_RESPONSIVE_STYLE` in `<head>` | Apple Mail, iOS, Gmail, Outlook.com |
| MSO ghost table | `<!--[if mso]><table width="640">…<![endif]-->` around the paper | Windows Outlook (ignores `@media` and `max-width`) |

Media queries key off classes, because `!important` cannot override an inline
style without a selector. Add the matching class when you add an element:

| Class | Effect at ≤640px |
|-------|--------------------|
| `azb-outer` | Outer gutter shrinks to 6px |
| `azb-pad` | Section gutters 32px → 20px (→ 16px at ≤400px); inline-only fallback stays 32px |
| `azb-hero-title` | Main hero title uses 25px |
| `azb-masthead-brand` / `azb-masthead-edition` | Full-width baseline; desktop uses 44%/56% columns |
| `azb-section-label` / `azb-section-copy` | Full-width baseline; at >=800px uses a 24%/76% label/content rail |
| `azb-stack-cell` / `azb-stack-tail` | Masthead metadata cells stack with spacing between them |
| `azb-digest-row` / `azb-digest-entry` | The spanning row contains a full-width title table followed by a metric table |
| `azb-digest-copy` / `azb-digest-metrics` | Full-width by default; media-query desktops set 52%/48% widths for side-by-side comparison |
| `azb-col-metric` / `azb-metric-label` | Three labeled metrics beneath the title by default; only desktop media queries hide the repeated labels |
| `azb-resource-row` / `azb-resource-field-label` | All four identity cells stack with labels; full reasons, groups, and Portal links remain intact |
| `azb-fact-cell` | Two-column operational facts stack vertically |
| `azb-action-detail-row` | Both label and value cells stack together, never just one cell |

Desktop media queries add room for content while retaining a bounded reading width:

| Breakpoint | Effect |
|-----------|--------|
| `min-width: 800px` | Paper 640px → 760px; section gutters remain 32px |
| `min-width: 1100px` | Paper → 900px; section gutters → 48px |

Inline-only/MSO digest entries use a **full-width title and summary above three labeled metrics**.
Only media-query desktops use **52% title / 16% per metric**. The earlier 28%/24% fallback made
English titles wrap almost one character per line and spilled `Medium` badges at 320px. Check
badge-to-cell bounds and title readability, not only document overflow. Never fix this by shrinking
text or dropping titles, summaries, or axes.

The impact dimension label (`azb-impact-label`) uses both HTML `width="96"` and inline
`width: 96px`/`min-width: 96px` with `white-space: nowrap` and `word-break: keep-all`. Keep all of these because
Windows Outlook can ignore individual CSS sizing mechanisms and otherwise wraps Korean one glyph
per line. Impact dimensions remain definition rows at every width; do not restore the old desktop 2×2 split.

Windows Outlook ignores `min-width` queries too, so it stays at the 640px ghost
table — intended, since its reading pane is usually narrow.

## Localized Content Rules

- User-facing text follows the requested language; Korean is the default, with curated ko/en/ja labels
- Add new label keys to `src/i18n/labels/ko.py` first, then translate in `en.py` / `ja.py`
- Urgency prefixes: `[긴급]`, `[중요]`
- Default "no data" messages: `"영향받는 리소스가 없습니다."`, etc.

## `EmailService` in `service.py`

- Sends via **Azure Communication Services** (`azure-communication-email` SDK)
- Lazy-imports `EmailClient` to avoid import errors when email is not configured
- Falls back to **console output** when `COMMUNICATION_SERVICES_CONNECTION_STRING` is not set
- Builds plain text separately in `_build_plain_text()`
- Digest HTML keeps every supplied item: analyzed high/medium/low counts exclude skipped items,
    which have their own count and rows. Numbered, full-length contents titles link to
    `#azbrief-detail-N`; every analyzed detail links back to `#azbrief-summary`
- `_build_retirement_countdown_html()` renders a two-column D-day/item list with localized
    migration-status text, not emoji; the history source and scoped-delivery exclusion are unchanged
- Supports protected bootstrap subscribers from `SUBSCRIBERS` plus mutable profiles from the
    private Admin configuration Blob; delivery code resolves the merged list through
    `get_admin_configuration().get_subscribers()`
- Accepts an optional `archive_url` for single and digest HTML/plain-text output. The link always names the shared canonical analysis; subscriber-customized content is not archived
- Adds one localized Feedback link to the bottom of single and digest HTML/plain-text reports. The URL may carry only a bounded Update ID or digest date range
- `send_feedback_notification()` sends the already-persisted strict feedback document to the explicitly configured `FEEDBACK_RECIPIENT_ADDRESS` and never logs its body or contact address; when unset, persistence succeeds and notification fails closed

## Adding a New Section

Adding a new section is a **4+ file chain**:

1. Add the label key to `src/i18n/labels/ko.py`, then translate it in `en.py` / `ja.py`
2. Create helper function: `format_<section>_html(data, lang="ko") -> str`, using `format_email_section_html()` for its wrapper
3. Wire the section into `HTML_EMAIL_TEMPLATE` and the matching digest detail path
4. Update `EmailService.build_email_content()` and `_build_update_detail_html()` to use the shared formatter
5. Update `_build_plain_text()` in `service.py` with text equivalent
6. Escape any literal `{}` braces with `_escape_braces()`

### Verification After Changes

🚨 **MANDATORY** — Run these after any template change:

```powershell
& .\.venv\Scripts\Activate.ps1
python -c "import src"
python -m pytest tests/test_email.py tests/test_email_editorial.py -o "addopts=" -x
python -m scripts.preview_email --output-dir out/email-editorial-preview --language all
```

The preview uses **SYNTHETIC** ko/en/ja data and mocked transport settings/history; it writes
12 single/digest full-style and inline-only HTML files without Azure calls or email delivery.
The editorial tests check structure, links/anchors, skipped counts, full titles, resource identity,
verification, defined text/background contrast pairs ≥4.5:1, and offline previews. Report focused,
full-suite, browser, and real email-client validation separately; do not infer unfinished results.

For the visual improvement loop, keep a frozen `before` preview, make one renderer change, run
the focused tests, and regenerate into a separate directory. Open a generated local HTML file in
Playwright MCP and pass the absolute path of `tests/browser/email_reports.cjs` as `filename` to
`browser_run_code_unsafe`. The standalone async page function tests 72 combinations of language,
report type, style fallback, and viewport; require `passed=true`. It writes representative screenshots
beside file previews, which must remain under `out/`. Inspect them, state the observed defect,
make the smallest correction, and repeat. See `src/email/README.md` for the design rationale.
The display-scale redesign checks wordmark/number text bounds and rail alignment as well as
badge bounds. Keep same-size before/after images, decompose reference compositions explicitly,
and never present passing regression tests as proof that a design looks better.

## Common Pitfalls

| Issue | Cause | Fix |
|-------|-------|-----|
| `KeyError` in `str.format()` | Literal `{}` in template | Use `_escape_braces()` |
| Broken layout in Outlook | CSS not inline | Move styles to `style=""` attributes |
| Missing Korean text | Label not in `src/i18n/labels/ko.py` | Add it there — `ko.py` is the canonical key set |
| Email not sent | No connection string | Falls back to console — this is expected |
| ⚠️ Label renders in Korean for a `ja` reader | Key missing from `ja.py` | Expected fallback — check `missing_label_keys("ja")` and translate |
| ⚠️ Badge color wrong | Level mapping returns unexpected value | Check `_urgency_to_level()` / `_relevance_to_level()` |
| Low-contrast text | Ad hoc colors bypass `EMAIL_COLORS` | Reuse palette roles and run the contrast test; do not add dark-mode overrides |
| Inline-only preview keeps 32px gutters | Head styles were stripped | Expected; contents still stack title/summary above labeled metrics without head styles |
| Gutters do not shrink in a media-query-supporting client | `azb-pad` missing on the section `<td>` | Add the class and verify 20px at ≤640px / 16px at ≤400px |
| Paper stretches full width in Windows Outlook | MSO ghost table missing or unbalanced | Keep both conditional wrappers around the 640px ghost table |
