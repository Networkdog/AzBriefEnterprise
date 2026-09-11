"""Server-rendered public feedback form with no external script dependency."""

from __future__ import annotations

import html
import json

from src.i18n import normalize_language
from src.web_design import CONTROL_SURFACE_BASE_CSS, DEFAULT_WEB_UI_LANGUAGE
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
  <style nonce="__NONCE__">
    __WEB_FONT_FACE__
    __CONTROL_SURFACE_BASE__
    main { width:100%; max-width:960px; margin:0 auto; padding:32px 24px 56px; }
    .page-intro { align-items:flex-start; }
    .page-intro h1 { font-size:28px; }
    .feedback-layout { display:grid; grid-template-columns:minmax(0,1fr) 240px;
      gap:24px; align-items:start; }
    .feedback-form { border:1px solid var(--line); border-top:3px solid var(--primary);
      border-radius:var(--radius-md); background:var(--surface); box-shadow:var(--shadow-sm); }
    .form-body { display:grid; gap:18px; padding:22px; }
    fieldset { min-width:0; margin:0; padding:0; border:0; }
    legend { width:100%; margin:0 0 7px; color:var(--ink-soft); font-size:12px;
      font-weight:800; }
    .field { display:flex; flex-direction:column; gap:7px; min-width:0; }
    .field label { color:var(--ink-soft); font-size:12px; font-weight:800; }
    textarea { width:100%; min-height:170px; resize:vertical; border:1px solid var(--line-strong);
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
    .form-actions { display:flex; align-items:center; justify-content:space-between; gap:16px;
      padding:14px 22px; border-top:1px solid var(--line); background:var(--surface-subtle); }
    .privacy { margin:0; max-width:560px; color:var(--muted); font-size:11px; }
    .submit { flex:none; width:var(--command-width); white-space:nowrap; }
    .status { min-height:44px; margin-top:12px; padding:12px 14px; border:1px solid var(--line);
      border-radius:var(--radius-sm); background:var(--surface); color:var(--ink-soft); }
    .status.success { border-color:#add9c1; background:var(--success-soft); color:var(--success); }
    .status.warning { border-color:#e9c98d; background:var(--warning-soft); color:var(--warning); }
    .status.error { border-color:#efb8b3; background:var(--danger-soft); color:var(--danger); }
    .side-note { padding:4px 0 4px 18px; border-left:2px solid var(--line-strong); }
    .side-note h2 { margin:0 0 8px; font-size:14px; }
    .side-note p { margin:0; color:var(--muted); font-size:12px; line-height:1.65; }
    .honeypot { position:absolute !important; left:-10000px !important; width:1px !important;
      height:1px !important; overflow:hidden !important; }
    @media (max-width:720px) {
      main { padding:22px 14px 40px; }
      .feedback-layout { grid-template-columns:1fr; gap:18px; }
      .form-body { padding:18px 14px; }
      .form-actions { align-items:stretch; flex-direction:column; padding:14px; }
      .submit { width:100%; }
      .side-note { order:-1; }
    }
    @media (max-width:480px) {
      .segments { grid-template-columns:1fr; }
      .segment span { border-right:0; border-bottom:1px solid var(--line-strong); }
      .segment:last-child span { border-bottom:0; }
    }
  </style>
</head>
<body>
  <a class="skip-link" href="#main-content">__SKIP__</a>
  <header class="app-header"><div class="app-bar">
    <a class="brand-lockup" href="/feedback" aria-label="AzBrief Feedback">
      <span class="brand-mark" aria-hidden="true">AZ</span>
      <span class="brand-copy"><span class="brand-name">AzBrief</span><span class="brand-area">Feedback</span></span>
    </a>
  </div></header>
  <main id="main-content">
    <div class="page-intro"><div><p class="page-kicker">__KICKER__</p><h1>__TITLE__</h1><p class="page-context">__CONTEXT__</p></div></div>
    <div class="feedback-layout">
      <form id="feedback-form" class="feedback-form">
        <div class="form-body">
          <fieldset>
            <legend class="field-heading"><span>__CATEGORY__</span><span class="field-requirement required">__REQUIRED__</span></legend>
            <div class="segments">
              <label class="segment"><input type="radio" name="category" value="bug" checked><span>__BUG__</span></label>
              <label class="segment"><input type="radio" name="category" value="improvement"><span>__IMPROVEMENT__</span></label>
              <label class="segment"><input type="radio" name="category" value="report_context"><span>__REPORT_CONTEXT__</span></label>
            </div>
          </fieldset>
          <div class="field">
            <label for="subject" class="field-heading"><span>__SUBJECT__</span><span class="field-requirement required">__REQUIRED__</span></label>
            <input id="subject" name="subject" maxlength="160" minlength="3" placeholder="__SUBJECT_PLACEHOLDER__" required>
          </div>
          <div class="field">
            <label for="details" class="field-heading"><span>__DETAILS__</span><span class="field-requirement required">__REQUIRED__</span></label>
            <textarea id="details" name="details" maxlength="8000" minlength="10" placeholder="__DETAILS_PLACEHOLDER__" required></textarea>
          </div>
          <div class="field">
            <label for="report-reference" class="field-heading"><span>__REPORT_REFERENCE__</span><span class="field-requirement">__OPTIONAL__</span></label>
            <input id="report-reference" name="report_reference" maxlength="500" value="__REPORT_VALUE__" placeholder="__REPORT_PLACEHOLDER__">
          </div>
          <div class="field">
            <label for="contact-email" class="field-heading"><span>__CONTACT__</span><span class="field-requirement">__OPTIONAL__</span></label>
            <input id="contact-email" name="contact_email" type="email" maxlength="254" autocomplete="email" placeholder="__CONTACT_PLACEHOLDER__">
          </div>
          <div class="honeypot" aria-hidden="true"><label for="website">Website</label><input id="website" name="website" tabindex="-1" autocomplete="off"></div>
        </div>
        <div class="form-actions"><p class="privacy">__PRIVACY__</p><button id="submit" class="submit" type="submit">__SUBMIT__</button></div>
      </form>
      <aside class="side-note"><h2>__REPORT_CONTEXT__</h2><p>__CONTEXT__</p></aside>
    </div>
    <div id="status" class="status" role="status" aria-live="polite" hidden></div>
  </main>
  <script nonce="__NONCE__">
  'use strict';
  const form = document.getElementById('feedback-form');
  const submit = document.getElementById('submit');
  const status = document.getElementById('status');
  const messages = __MESSAGES__;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    submit.disabled = true;
    submit.textContent = messages.submitting;
    status.hidden = true;
    const data = new FormData(form);
    const payload = Object.fromEntries(data.entries());
    payload.language = document.documentElement.lang;
    try {
      const response = await fetch('/api/feedback', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      if (!response.ok) throw new Error(String(response.status));
      const receipt = await response.json();
      status.className = 'status ' + (receipt.notification_sent ? 'success' : 'warning');
      status.textContent = receipt.notification_sent ? messages.success : messages.stored_only;
      status.hidden = false;
      form.reset();
      document.getElementById('report-reference').value = __REPORT_VALUE_JSON__;
    } catch (error) {
      status.className = 'status error';
      status.textContent = messages.error;
      status.hidden = false;
    } finally {
      submit.disabled = false;
      submit.textContent = messages.submit;
    }
  });
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
) -> str:
    """Render the localized feedback form with escaped report context."""
    code = normalize_language(language)
    if code not in _LABELS:
        code = DEFAULT_WEB_UI_LANGUAGE
    labels = _LABELS[code]
    replacements = {
        "__LANG__": html.escape(code, quote=True),
        "__NONCE__": html.escape(nonce, quote=True),
        "__WEB_FONT_FACE__": WEB_FONT_FACE_CSS,
        "__WEB_FONT_STACK__": WEB_FONT_STACK,
        "__CONTROL_SURFACE_BASE__": CONTROL_SURFACE_BASE_CSS.replace(
            "__WEB_FONT_STACK__", WEB_FONT_STACK
        ),
        "__SKIP__": "본문으로 건너뛰기" if code == "ko" else "Skip to content",
        "__REPORT_VALUE__": html.escape(report_reference, quote=True),
        "__REPORT_VALUE_JSON__": _json_for_script(report_reference),
        "__MESSAGES__": _json_for_script(
            {
                "submit": labels["submit"],
                "submitting": labels["submitting"],
                "success": labels["success"],
                "stored_only": labels["stored_only"],
                "error": labels["error"],
            }
        ),
    }
    replacements.update({f"__{key.upper()}__": html.escape(value) for key, value in labels.items()})
    page = _PAGE
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    return page
