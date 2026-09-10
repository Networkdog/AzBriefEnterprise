"""Server-rendered public feedback form with no external script dependency."""

from __future__ import annotations

import html
import json

from src.i18n import normalize_language
from src.i18n.labels import get_labels
from src.web_design import (
    CONTROL_SURFACE_BASE_CSS,
    CONTROL_SURFACE_FAVICON,
    CONTROL_SURFACE_ICONS,
    CONTROL_SURFACE_SCRIPT,
    DEFAULT_WEB_UI_LANGUAGE,
)
from src.web_fonts import WEB_FONT_FACE_CSS, WEB_FONT_STACK

_LABELS = {
    "en": {
        "title": "Send feedback",
        "kicker": "AzBrief feedback",
        "context": "Help us improve the product and make future reports more useful.",
        "category": "Feedback type",
        "bug": "Program bug",
        "improvement": "Improvement",
        "report_context": "Report context",
        "subject": "Subject",
        "subject_placeholder": "Briefly name the issue or request",
        "details": "Details",
        "details_placeholder": "Describe what happened, what should change, or what context future reports should apply.",
        "report_reference": "Report reference",
        "report_placeholder": "Update ID, digest date, or report title",
        "contact": "Contact email",
        "contact_placeholder": "name@example.com",
        "required": "Required",
        "optional": "Optional",
        "privacy": "Feedback is stored privately. Contact information is used only to follow up on this submission.",
        "submit": "Submit feedback",
        "submitting": "Submitting...",
        "success": "Feedback submitted. The developer has been notified.",
        "stored_only": "Feedback was saved, but the email notification could not be delivered.",
        "error": "Feedback could not be submitted. Please try again.",
    },
    "ko": {
        "title": "피드백 보내기",
        "kicker": "AzBrief 피드백",
        "context": "제품 개선과 더 유용한 이메일 보고서를 위해 의견을 남겨 주세요.",
        "category": "피드백 유형",
        "bug": "프로그램 버그",
        "improvement": "개선 사항",
        "report_context": "보고서 컨텍스트",
        "subject": "제목",
        "subject_placeholder": "문제 또는 요청 사항을 간단히 적어 주세요",
        "details": "상세 내용",
        "details_placeholder": "발생한 문제, 원하는 개선 사항 또는 향후 보고서에 적용할 컨텍스트를 적어 주세요.",
        "report_reference": "보고서 참조",
        "report_placeholder": "업데이트 ID, digest 날짜 또는 보고서 제목",
        "contact": "연락받을 이메일",
        "contact_placeholder": "name@example.com",
        "required": "필수",
        "optional": "선택",
        "privacy": "피드백은 비공개로 저장됩니다. 연락처는 이 피드백에 대한 회신에만 사용됩니다.",
        "submit": "피드백 제출",
        "submitting": "제출 중...",
        "success": "피드백이 접수되었고 개발자에게 알림을 보냈습니다.",
        "stored_only": "피드백은 저장되었지만 이메일 알림을 보내지 못했습니다.",
        "error": "피드백을 제출하지 못했습니다. 다시 시도해 주세요.",
    },
    "ja": {
        "title": "フィードバックを送信",
        "kicker": "AzBrief フィードバック",
        "context": "製品の改善と、より役立つメールレポートのためにご意見をお寄せください。",
        "category": "フィードバックの種類",
        "bug": "プログラムの不具合",
        "improvement": "改善提案",
        "report_context": "レポートのコンテキスト",
        "subject": "件名",
        "subject_placeholder": "問題または要望を簡潔に入力してください",
        "details": "詳細",
        "details_placeholder": "発生した問題、改善案、または今後のレポートに反映するコンテキストを入力してください。",
        "report_reference": "レポート参照",
        "report_placeholder": "更新 ID、ダイジェストの日付、またはレポート名",
        "contact": "連絡先メール",
        "contact_placeholder": "name@example.com",
        "required": "必須",
        "optional": "任意",
        "privacy": "フィードバックは非公開で保存されます。連絡先はこの送信内容への返信にのみ使用します。",
        "submit": "フィードバックを送信",
        "submitting": "送信中...",
        "success": "フィードバックを受け付け、開発者に通知しました。",
        "stored_only": "フィードバックは保存されましたが、メール通知を送信できませんでした。",
        "error": "フィードバックを送信できませんでした。もう一度お試しください。",
    },
}

_PAGE = r"""<!doctype html>
<html lang="__LANG__">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AzBrief · __TITLE__</title>
  __WEB_FAVICON__
  <style nonce="__NONCE__">
    __WEB_FONT_FACE__
    __CONTROL_SURFACE_BASE__
    main { width:100%; max-width:1280px; margin:0 auto; padding:28px 24px 56px; }
    .page-intro { align-items:flex-start; }
    .page-intro h1 { font-size:24px; }
    .feedback-layout { display:grid; grid-template-columns:minmax(0,700px) minmax(200px,260px);
      gap:48px; align-items:start; }
    .feedback-form { min-width:0; border:0; background:transparent; box-shadow:none; }
    .form-body { display:grid; gap:22px; padding:8px 0 24px; }
    .language-field { display:flex; align-items:center; gap:10px; color:var(--muted); font-size:12px; }
    .language-field select { width:140px; }
    fieldset { min-width:0; margin:0; padding:0; border:0; }
    legend { width:100%; margin:0 0 7px; color:var(--ink-soft); font-size:12px;
      font-weight:800; }
    .field { display:flex; flex-direction:column; gap:7px; min-width:0; }
    .field label { color:var(--ink-soft); font-size:12px; font-weight:800; }
    textarea { width:100%; min-height:200px; resize:vertical; border:1px solid var(--line-strong);
      border-radius:var(--radius-sm); padding:10px; background:var(--surface); color:var(--ink);
      font:inherit; line-height:1.55; letter-spacing:0; }
    textarea:hover { border-color:#8fa0a8; }
    textarea:focus-visible { outline:2px solid var(--focus); outline-offset:2px; }
    .segments { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:0;
      overflow:hidden; border:1px solid var(--line-strong); border-radius:var(--radius-sm); }
    .segment { position:relative; min-width:0; }
    .segment input { position:absolute; width:1px; height:1px; opacity:0; }
    .segment span { min-height:var(--control-height); display:flex; align-items:center;
      justify-content:center; padding:7px 10px; border-right:1px solid var(--line-strong);
      background:var(--surface); color:var(--ink-soft); text-align:center; font-size:12px;
      font-weight:700; cursor:pointer; }
    .segment:last-child span { border-right:0; }
    .segment input:checked + span { background:var(--primary-soft); color:var(--primary);
      box-shadow:inset 0 -2px 0 var(--primary); }
    .segment input:focus-visible + span { outline:2px solid var(--focus); outline-offset:-3px; }
    .form-actions { display:flex; align-items:center; justify-content:flex-end; gap:16px;
      padding:18px 0; border-top:1px solid var(--line); background:transparent; }
    .privacy { margin:0; max-width:560px; color:var(--muted); font-size:11px; }
    .submit { flex:none; width:auto; min-width:var(--command-width); white-space:nowrap; }
    .status { min-height:44px; margin-top:12px; padding:12px 14px; border:1px solid var(--line);
      border-radius:var(--radius-sm); background:var(--surface); color:var(--ink-soft); }
    .status.success { border-color:#add9c1; background:var(--success-soft); color:var(--success); }
    .status.warning { border-color:#e9c98d; background:var(--warning-soft); color:var(--warning); }
    .status.error { border-color:#efb8b3; background:var(--danger-soft); color:var(--danger); }
    .side-note { position:sticky; top:100px; padding:12px 0 4px 24px; border-left:1px solid var(--line); }
    .side-note > .ui-icon { width:28px; height:28px; color:var(--primary); margin-bottom:16px; }
    .side-note h2 { margin:0 0 8px; font-size:14px; }
    .side-note p { margin:0; color:var(--muted); font-size:12px; line-height:1.65; }
    .reference-context { margin-top:24px; padding-top:20px; border-top:1px solid var(--line); }
    .reference-context .reference-value { margin:8px 0 12px; overflow-wrap:anywhere; }
    .counter { color:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; text-align:right; }
    .field-footer { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }
    .field-footer .counter { margin-left:auto; flex:none; }
    .field-error { margin:0; font-weight:500; }
    .receipt { max-width:700px; padding:28px 0; }
    .receipt > .ui-icon { width:36px; height:36px; color:var(--success); }
    .receipt h2 { margin:18px 0 10px; font-size:22px; }
    .receipt p { color:var(--ink-soft); }
    .receipt-id { margin:24px 0; padding:18px 0; border-top:1px solid var(--line);
      border-bottom:1px solid var(--line); }
    .receipt-id dt { color:var(--muted); font-size:12px; }
    .receipt-id dd { margin:8px 0 0; overflow-wrap:anywhere; font-family:'Cascadia Mono',Consolas,monospace; }
    .honeypot { position:absolute !important; left:-10000px !important; width:1px !important;
      height:1px !important; overflow:hidden !important; }
    @media (max-width:720px) {
      main { padding:22px 14px 40px; }
      .feedback-layout { grid-template-columns:1fr; gap:18px; }
      .form-body { padding:8px 0 20px; }
      .form-actions { align-items:stretch; flex-direction:column; padding:16px 0; }
      .submit { width:100%; }
      .side-note { position:static; padding:16px 0; border-left:0; border-top:1px solid var(--line); }
      .side-note > .ui-icon { display:none; }
      .language-field { width:100%; justify-content:space-between; }
    }
    @media (max-width:480px) {
      .segments { grid-template-columns:1fr; }
      .segment span { border-right:0; border-bottom:1px solid var(--line-strong); }
      .segment:last-child span { border-bottom:0; }
    }
  </style>
</head>
<body>
  __WEB_ICONS__
  <a class="skip-link" href="#main-content">__SKIP__</a>
  <header class="app-header"><div class="app-bar">
    <a class="brand-lockup" href="/feedback" aria-label="AzBrief Feedback">
      <span class="brand-mark" aria-hidden="true">AZ</span>
      <span class="brand-copy"><span class="brand-name">AzBrief</span><span class="brand-area">Operations</span></span>
    </a>
    <nav class="primary-nav" aria-label="__NAV_LABEL__">__ADMIN_LINK____ARCHIVE_LINK__<a class="nav-link" href="/feedback" aria-current="page">Feedback</a></nav>
  </div></header>
  <main id="main-content">
    <div class="page-intro"><div><p class="page-kicker" data-message="kicker">__KICKER__</p><h1 data-message="title">__TITLE__</h1></div><label class="language-field"><span data-message="language">__LANGUAGE__</span><select id="language"><option value="en">English</option><option value="ko">한국어</option><option value="ja">日本語</option></select></label></div>
    <div id="feedback-layout" class="feedback-layout">
      <form id="feedback-form" class="feedback-form" novalidate>
        <div class="form-body">
          <fieldset>
            <legend class="field-heading"><span data-message="category">__CATEGORY__</span><span class="field-requirement required" data-message="required">__REQUIRED__</span></legend>
            <div class="segments">
              <label class="segment"><input type="radio" name="category" value="bug" checked><span data-message="bug">__BUG__</span></label>
              <label class="segment"><input type="radio" name="category" value="improvement"><span data-message="improvement">__IMPROVEMENT__</span></label>
              <label class="segment"><input type="radio" name="category" value="report_context"><span data-message="report_context">__REPORT_CONTEXT__</span></label>
            </div>
          </fieldset>
          <div class="field">
            <label for="subject" class="field-heading"><span data-message="subject">__SUBJECT__</span><span class="field-requirement required" data-message="required">__REQUIRED__</span></label>
            <input id="subject" name="subject" maxlength="160" minlength="3" placeholder="__SUBJECT_PLACEHOLDER__" required aria-describedby="subject-error subject-count">
            <div class="field-footer"><p id="subject-error" class="field-error" hidden></p><span id="subject-count" class="counter">0 / 160</span></div>
          </div>
          <div class="field">
            <label for="details" class="field-heading"><span data-message="details">__DETAILS__</span><span class="field-requirement required" data-message="required">__REQUIRED__</span></label>
            <textarea id="details" name="details" maxlength="8000" minlength="10" placeholder="__DETAILS_PLACEHOLDER__" required aria-describedby="details-error details-count"></textarea>
            <div class="field-footer"><p id="details-error" class="field-error" hidden></p><span id="details-count" class="counter">0 / 8,000</span></div>
          </div>
          <div class="field">
            <label for="report-reference" class="field-heading"><span data-message="report_reference">__REPORT_REFERENCE__</span><span class="field-requirement" data-message="optional">__OPTIONAL__</span></label>
            <input id="report-reference" name="report_reference" maxlength="500" value="__REPORT_VALUE__" placeholder="__REPORT_PLACEHOLDER__">
          </div>
          <div class="field">
            <label for="contact-email" class="field-heading"><span data-message="contact">__CONTACT__</span><span class="field-requirement" data-message="optional">__OPTIONAL__</span></label>
            <input id="contact-email" name="contact_email" type="email" maxlength="254" autocomplete="email" placeholder="__CONTACT_PLACEHOLDER__" aria-describedby="contact-email-error">
            <p id="contact-email-error" class="field-error" hidden></p>
          </div>
          <div class="honeypot" aria-hidden="true"><label for="website">Website</label><input id="website" name="website" tabindex="-1" autocomplete="off"></div>
        </div>
        <div id="status" class="status" role="alert" tabindex="-1" hidden></div>
        <div class="form-actions"><button id="submit" class="submit" type="submit" data-icon="message-square"><span id="submit-label" data-message="submit">__SUBMIT__</span></button></div>
      </form>
      <aside class="side-note"><span data-icon="shield-check"></span><h2 data-message="private_title">__PRIVATE_TITLE__</h2><p data-message="privacy">__PRIVACY__</p><div id="reference-context" class="reference-context" hidden><h2 data-message="report_reference">__REPORT_REFERENCE__</h2><p id="reference-value" class="reference-value"></p><a id="back-report" data-message="back_report" hidden>__BACK_REPORT__</a></div></aside>
    </div>
    <section id="receipt" class="receipt" tabindex="-1" aria-labelledby="receipt-title" hidden><span data-icon="check"></span><h2 id="receipt-title" data-message="receipt_title">__RECEIPT_TITLE__</h2><p id="receipt-message"></p><dl id="receipt-reference" class="receipt-id"><dt data-message="receipt_id">__RECEIPT_ID__</dt><dd id="receipt-id"></dd></dl><button id="send-another" type="button" class="secondary" data-icon="plus"><span data-message="another">__ANOTHER__</span></button></section>
  </main>
  <script nonce="__NONCE__">
  'use strict';
  __WEB_SCRIPT__
  const byId = id => document.getElementById(id);
  const form = document.getElementById('feedback-form');
  const submit = document.getElementById('submit');
  const status = document.getElementById('status');
  const translations = __MESSAGES__;
  let messages = translations[document.documentElement.lang];
  let dirty = false;
  let submitting = false;
  let receiptNotified = false;
  const validationIds = ['subject', 'details', 'contact-email'];
  function validateField(id) {
    const field = byId(id); const value = field.value.trim();
    const valid = id === 'contact-email' ? field.validity.valid
      : value.length >= field.minLength && value.length <= field.maxLength;
    const error = byId(id + '-error'); error.hidden = valid;
    error.textContent = valid ? '' : messages[id === 'contact-email' ? 'contact_error' : id + '_error'];
    field.setAttribute('aria-invalid', String(!valid)); return valid;
  }
  function updateFormContext() {
    ['subject','details'].forEach(id => {
      const field = byId(id); byId(id + '-count').textContent = field.value.length.toLocaleString(document.documentElement.lang)
        + ' / ' + field.maxLength.toLocaleString(document.documentElement.lang);
    });
    const category = form.elements.category.value;
    byId('details').placeholder = messages[category + '_placeholder'];
    const reference = byId('report-reference').value.trim();
    byId('reference-context').hidden = !reference; byId('reference-value').textContent = reference;
    const archive = reference.match(/^archive:([0-9]{13}-[0-9a-f]{32})$/);
    byId('back-report').hidden = !archive;
    if (archive) byId('back-report').href = '/archive/' + archive[1] + '?lang=' + document.documentElement.lang;
  }
  function setLanguage(code) {
    messages = translations[code]; document.documentElement.lang = code;
    document.title = 'AzBrief - ' + messages.title;
    document.querySelectorAll('[data-message]').forEach(node => { node.textContent = messages[node.dataset.message]; });
    [['subject','subject_placeholder'],['report-reference','report_placeholder'],['contact-email','contact_placeholder']]
      .forEach(([id,key]) => { byId(id).placeholder = messages[key]; });
    validationIds.forEach(id => { if (byId(id).getAttribute('aria-invalid') === 'true') validateField(id); });
    if (!byId('receipt').hidden) byId('receipt-message').textContent = receiptNotified ? messages.success : messages.stored_only;
    if (submitting) byId('submit-label').textContent = messages.submitting;
    const url = new URL(location.href); url.searchParams.set('lang',code); history.replaceState({},'',url.pathname + url.search);
    updateFormContext();
  }
  byId('language').value = document.documentElement.lang;
  byId('language').addEventListener('change',event => setLanguage(event.target.value));
  form.addEventListener('input',event => {
    dirty = true; updateFormContext();
    if (validationIds.includes(event.target.id) && event.target.getAttribute('aria-invalid') === 'true') validateField(event.target.id);
  });
  form.addEventListener('change',updateFormContext);
  validationIds.forEach(id => byId(id).addEventListener('blur', () => { if (byId(id).value) validateField(id); }));
  window.addEventListener('beforeunload',event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  byId('send-another').addEventListener('click',() => {
    form.reset(); byId('report-reference').value = __REPORT_VALUE_JSON__;
    validationIds.forEach(id => { byId(id).removeAttribute('aria-invalid'); byId(id + '-error').hidden = true; });
    byId('receipt').hidden = true; byId('feedback-layout').hidden = false; status.hidden = true;
    dirty = false; updateFormContext(); byId('subject').focus();
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (submitting || !byId('receipt').hidden) return;
    const invalid = validationIds.filter(id => !validateField(id));
    if (invalid.length) { byId(invalid[0]).focus(); return; }
    submitting = true;
    submit.disabled = true;
    form.setAttribute('aria-busy','true');
    byId('submit-label').textContent = messages.submitting;
    status.hidden = true;
    const data = new FormData(form);
    const payload = Object.fromEntries(data.entries());
    ['subject','details','contact_email','report_reference'].forEach(key => { payload[key] = payload[key].trim(); });
    payload.language = document.documentElement.lang;
    try {
      const response = await fetch('/api/feedback', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      if (!response.ok) { const error = new Error(String(response.status)); error.status = response.status; throw error; }
      const receipt = await response.json();
      if (!receipt.accepted) throw new Error('Not accepted');
      receiptNotified = receipt.notification_sent;
      byId('receipt-message').textContent = receiptNotified ? messages.success : messages.stored_only;
      byId('receipt-id').textContent = receipt.feedback_id || '';
      byId('receipt-reference').hidden = !receipt.feedback_id;
      byId('feedback-layout').hidden = true; byId('receipt').hidden = false;
      dirty = false; byId('receipt').focus();
    } catch (error) {
      status.className = 'status error';
      status.textContent = error.status === 429 ? messages.rate_error
        : error.status === 503 ? messages.storage_error : messages.error;
      status.hidden = false;
      status.focus();
    } finally {
      submitting = false; form.setAttribute('aria-busy','false');
      submit.disabled = false;
      byId('submit-label').textContent = messages.submit;
    }
  });
  decorateIcons(); updateFormContext();
  </script>
</body>
</html>"""


def _json_for_script(value) -> str:
    """Serialize trusted-shaped data without allowing an HTML script close tag."""
    return (
        json.dumps(value, ensure_ascii=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def render_feedback_page(
    nonce: str,
    language: str = DEFAULT_WEB_UI_LANGUAGE,
    report_reference: str = "",
    admin_enabled: bool = False,
    archive_enabled: bool = False,
) -> str:
    """Render the localized feedback form with escaped report context."""
    code = normalize_language(language)
    if code not in _LABELS:
        code = DEFAULT_WEB_UI_LANGUAGE
    translations = {}
    for locale, base_labels in _LABELS.items():
        bundle = get_labels(locale)
        translations[locale] = dict(base_labels)
        for key in (
            "language",
            "private_title",
            "back_report",
            "receipt_title",
            "receipt_id",
            "another",
            "subject_error",
            "details_error",
            "contact_error",
            "rate_error",
            "storage_error",
            "bug_placeholder",
            "improvement_placeholder",
            "report_context_placeholder",
        ):
            translations[locale][key] = bundle[f"feedback_{key}"]
    labels = translations[code]
    replacements = {
        "__LANG__": html.escape(code, quote=True),
        "__NONCE__": html.escape(nonce, quote=True),
        "__WEB_FONT_FACE__": WEB_FONT_FACE_CSS,
        "__WEB_FONT_STACK__": WEB_FONT_STACK,
        "__WEB_ICONS__": CONTROL_SURFACE_ICONS,
        "__WEB_FAVICON__": CONTROL_SURFACE_FAVICON,
        "__WEB_SCRIPT__": CONTROL_SURFACE_SCRIPT,
        "__CONTROL_SURFACE_BASE__": CONTROL_SURFACE_BASE_CSS.replace(
            "__WEB_FONT_STACK__", WEB_FONT_STACK
        ),
        "__SKIP__": html.escape(get_labels(code)["skip_to_content"]),
        "__NAV_LABEL__": html.escape(get_labels(code)["primary_navigation"], quote=True),
        "__ADMIN_LINK__": '<a class="nav-link" href="/admin">Admin</a>' if admin_enabled else "",
        "__ARCHIVE_LINK__": (
            '<a class="nav-link" href="/archive">Archive</a>' if archive_enabled else ""
        ),
        "__REPORT_VALUE__": html.escape(report_reference, quote=True),
        "__REPORT_VALUE_JSON__": _json_for_script(report_reference),
        "__MESSAGES__": _json_for_script(translations),
    }
    replacements.update({f"__{key.upper()}__": html.escape(value) for key, value in labels.items()})
    page = _PAGE
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    return page
