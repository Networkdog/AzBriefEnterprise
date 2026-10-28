"""Server-rendered HTML for the AzBrief admin console.

HTML, CSS, and JavaScript ship inline with no bundler. One pinned, open-licensed
webfont binary is allowed by the strict Content-Security-Policy; system-font
fallbacks keep the page usable when that fetch is blocked.
"""

from __future__ import annotations

from html import escape

from src.web_design import CONTROL_SURFACE_BASE_CSS
from src.web_fonts import WEB_FONT_FACE_CSS, WEB_FONT_STACK

# `__NONCE__` is substituted per request. Placeholders use double underscores
# rather than str.format so the CSS braces need no escaping.
_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>AzBrief Admin Console</title>
<style nonce="__NONCE__">
__WEB_FONT_FACE__
__CONTROL_SURFACE_BASE__
:root {
  --bg: var(--canvas); --panel: var(--surface); --text: var(--ink);
  --accent: var(--primary); --ok: var(--success); --warn: var(--warning); --bad: var(--danger);
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font-family: __WEB_FONT_STACK__; font-size: 14px; }
header { padding: 20px 28px; border-bottom: 1px solid var(--line);
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
h1 { margin: 0; font-size: 18px; letter-spacing: 0; }
.badge { padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600;
  background: var(--primary-soft); color: var(--primary); }
.who { margin-left: auto; color: var(--muted); font-size: 13px; }
.nav { color: var(--link); font-size: 13px; font-weight: 600; text-decoration: none; }
main { width: 100%; max-width: 1180px; margin: 0 auto; padding: 24px 28px 48px; }
.panel { --section-accent: var(--accent); margin-bottom: 20px; border: 1px solid var(--line);
  border-top: 3px solid var(--section-accent); background: var(--surface); }
.panel-status { --section-accent: var(--ok); }
.panel-schedule { --section-accent: var(--primary); }
.panel-subscribers { --section-accent: var(--warn); }
.panel-admins { --section-accent: var(--danger); }
.panel-updates { --section-accent: var(--line-strong); }
.panel-header { min-height: 52px; padding: 11px 16px; display: flex; align-items: center;
  justify-content: space-between; gap: 12px; background: var(--surface);
  border-bottom: 1px solid var(--line); }
.panel-header h2 { margin: 0; color: var(--text); font-size: 15px; letter-spacing: 0; }
.panel-caption { min-width: 0; margin-right: auto; color: var(--muted); font-size: 12px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.panel-toggle { flex: 0 0 32px; width: 32px; height: 32px; padding: 0;
  font-size: 18px; line-height: 1; }
.panel.collapsed .panel-header { border-bottom: 0; }
.panel-body { padding: 16px; }
.action-surface { margin: 0 0 16px; padding: 14px; border: 1px solid var(--line);
  background: var(--surface-subtle); }
.action-row { display: flex; align-items: center; justify-content: flex-end; gap: 10px;
  margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line); }
.action-row .msg { margin-right: auto; }
.subsection-heading { display: flex; align-items: center; justify-content: space-between;
  gap: 12px; min-height: 30px; margin: 0 0 7px; }
.subsection-heading h3 { margin: 0; color: var(--muted); font-size: 12px;
  font-weight: 700; letter-spacing: 0; text-transform: uppercase; }
.readiness-summary { display: flex; align-items: center; gap: 10px; padding: 9px 12px;
  border: 1px solid var(--line); border-left: 4px solid var(--line); background: var(--panel); }
.readiness-summary.ok { border-left-color: var(--ok); }
.readiness-summary.bad { border-left-color: var(--bad); }
.summary-count { font-size: 17px; font-weight: 750; }
.summary-copy { color: var(--muted); font-size: 12px; }
#status-sections { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 18px; margin-top: 12px; }
.readiness-section { min-width: 0; }
.readiness-section h3 { margin: 0 0 6px; font-size: 12px; letter-spacing: 0; color: var(--muted); }
.check-grid { border: 1px solid var(--line); background: var(--panel); }
.check { min-height: 0; padding: 7px 10px; display: grid;
  grid-template-columns: minmax(125px, .8fr) minmax(0, 1.6fr) auto; gap: 8px 12px;
  align-items: start; border-bottom: 1px solid var(--line); }
.check:last-child { border-bottom: 0; }
.check.bad { box-shadow: inset 3px 0 var(--bad); }
.check-head { display: contents; }
.check-name { grid-column: 1; grid-row: 1; font-weight: 700; font-size: 12px; }
.check-state { grid-column: 3; grid-row: 1; white-space: nowrap;
  font-size: 11px; font-weight: 750; color: var(--bad); }
.check.ok .check-state { color: var(--ok); }
.check-detail { grid-column: 2; grid-row: 1; color: var(--muted); font-size: 11px;
  line-height: 1.4; overflow-wrap: anywhere; }
.check-action { grid-column: 2 / 4; color: var(--danger); font-size: 11px; line-height: 1.4; }
table { width: 100%; border-collapse: collapse; background: var(--panel);
  border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
th, td { padding: 9px 12px; text-align: left; border-bottom: 1px solid var(--line); font-size: 13px; }
th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; }
tr:last-child td { border-bottom: none; }
button { background: var(--accent); color: #fff; border: 0; border-radius: 8px;
  padding: 9px 18px; font-size: 13px; font-weight: 700; white-space: nowrap; cursor: pointer; }
button[disabled] { opacity: 0.5; cursor: not-allowed; }
button[hidden] { display: none; }
button.danger { background: transparent; color: var(--bad); border: 1px solid var(--danger);
  padding: 6px 10px; }
button.secondary { background: transparent; color: var(--accent); border: 1px solid var(--line);
  padding: 6px 10px; }
button.table-action { padding: 5px 9px; white-space: nowrap; }
.button-group { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
label { color: var(--muted); font-size: 13px; display: inline-flex; align-items: center; gap: 6px; }
input[type=text], input[type=email], input[type=url], input[type=number], input[type=date],
input[type=datetime-local], input[type=time], select { background: var(--surface);
  border: 1px solid var(--line); color-scheme: dark;
  color: var(--text); border-radius: 6px; padding: 8px 10px; font-size: 13px; min-width: 0; }
.controls { display: grid; grid-template-columns: minmax(180px, .8fr) minmax(220px, 1.5fr);
  gap: 10px; align-items: start; }
.controls label { display: flex; flex-direction: column; align-items: stretch; gap: 5px; }
.inline-check { flex-direction: row; align-items: center; }
.run-field[hidden] { display: none !important; }
.run-field[data-run-mode="date_range"] { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.run-time-field { display: grid; grid-template-columns: minmax(220px, 1fr) minmax(220px, var(--time-basis-width));
  gap: 10px; align-items: start; }
.schedule-form { display: grid;
  grid-template-columns: minmax(180px, 280px) minmax(220px, 1fr);
  gap: 10px; align-items: start; }
.schedule-form label { display: flex; flex-direction: column; align-items: stretch; gap: 5px; }
.time-basis { width: 100%; max-width: var(--time-basis-width);
  min-width: 0; margin: 0; padding: 0; border: 0; }
.time-basis legend { min-height: 18px; margin-bottom: 5px; display: flex;
  align-items: baseline; color: var(--ink-soft); font-size: 12px; font-weight: 700; }
.segmented { display: grid; width: 100%; grid-template-columns: repeat(2, minmax(90px, 1fr));
  height: var(--control-height); border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
.segmented label { position: relative; display: block; color: var(--muted); cursor: pointer; }
.segmented label + label { border-left: 1px solid var(--line); }
.segmented input { position: absolute; inline-size: 1px; block-size: 1px; opacity: 0; }
.segmented span { height: 100%; display: grid; place-items: center; padding: 0 14px;
  text-align: center; font-weight: 700; }
.segmented input:checked + span { background: var(--primary-soft); color: var(--primary); }
.segmented input:focus-visible + span { outline: 2px solid var(--focus); outline-offset: -3px; }
.time-conversion { min-height: 18px; margin-top: 6px; }
.manage-form { display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr));
  gap: 10px; }
.manage-form label { display: flex; flex-direction: column; align-items: stretch; gap: 5px; }
.manage-form .wide { grid-column: span 2; }
.manage-actions, .schedule-form .action-row { grid-column: 1 / -1; }
.table-wrap { max-width: 100%; overflow-x: auto; }
.table-wrap table { min-width: 720px; }
.table-wrap table.compact { min-width: 480px; }
.table-wrap table.schedule-table { min-width: 560px; }
.action-table th:first-child, .action-table td:first-child { width: 1%; white-space: nowrap; }
.run-detail { margin-top: 14px; padding-top: 14px; border-top: 2px solid var(--accent); }
.run-detail[hidden] { display: none; }
.run-detail-head { display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin-bottom: 8px; }
.run-detail-head h3 { margin: 0; font-size: 13px; }
.run-facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
  margin: 0; border: 1px solid var(--line); background: var(--panel); }
.run-fact { min-width: 0; padding: 9px 11px; border-right: 1px solid var(--line);
  border-bottom: 1px solid var(--line); }
.run-fact:nth-child(4n) { border-right: 0; }
.run-fact dt { color: var(--muted); font-size: 11px; font-weight: 700; }
.run-fact dd { margin: 4px 0 0; font-size: 12px; overflow-wrap: anywhere; }
.run-error { margin-top: 8px; padding: 9px 11px; border-left: 3px solid var(--bad);
  background: var(--danger-soft); color: var(--danger); font-size: 12px; overflow-wrap: anywhere; }
.run-error[hidden] { display: none; }
.source { color: var(--muted); font-size: 12px; }
.msg { margin-left: 4px; font-size: 13px; color: var(--muted); }
.s-completed { color: var(--ok); } .s-running, .s-queued { color: var(--warn); }
.s-failed { color: var(--bad); }
.empty { color: var(--muted); padding: 12px 2px; }
a { color: var(--accent); }
@media (max-width: 820px) {
  #status-sections { grid-template-columns: 1fr; }
  .controls { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .run-time-field { grid-template-columns: 1fr; }
  .manage-form { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .run-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .run-fact:nth-child(4n) { border-right: 1px solid var(--line); }
  .run-fact:nth-child(2n) { border-right: 0; }
}
@media (max-width: 520px) {
  header, main { padding-left: 14px; padding-right: 14px; }
  .panel-header { padding: 10px 12px; }
  .panel-body { padding: 12px; }
  .action-surface { padding: 12px; }
  .check { grid-template-columns: minmax(110px, .9fr) minmax(0, 1.4fr); }
  .check-state { grid-column: 2; }
  .check-detail { grid-column: 1 / -1; grid-row: 2; }
  .check-action { grid-column: 1 / -1; }
  .controls { grid-template-columns: 1fr; }
  .run-field[data-run-mode="date_range"] { grid-template-columns: 1fr; }
  .schedule-form { grid-template-columns: 1fr; }
  .action-row { flex-wrap: wrap; }
  .action-row .msg { width: 100%; }
  .run-facts { grid-template-columns: 1fr; }
  .run-fact, .run-fact:nth-child(2n), .run-fact:nth-child(4n) { border-right: 0; }
  .manage-form { grid-template-columns: 1fr; }
  .manage-form .wide { grid-column: auto; }
  .who { width: 100%; margin-left: 0; }
}

/* Shared operations workspace visual language. */
:root {
  --bg: var(--canvas); --panel: var(--surface); --text: var(--ink);
  --accent: var(--primary); --ok: var(--success); --warn: var(--warning);
  --bad: var(--danger); --line: #dce3e6; --muted: #5f6f77;
}
body { background: var(--canvas); color: var(--ink); color-scheme: light; }
.app-header { padding: 0; border-top: 3px solid var(--primary);
  border-bottom: 1px solid var(--line); background: var(--surface); }
main { width: 100%; max-width: 1280px; margin: 0 auto; padding: 28px 24px 56px; }
.panel { --section-accent: var(--primary); margin: 0 0 12px; overflow: hidden;
  border: 1px solid var(--line); border-top: 1px solid var(--line);
  border-radius: var(--radius-md); background: var(--surface); box-shadow: var(--shadow-sm); }
.panel-header { min-height: 50px; padding: 0 14px; border-bottom: 1px solid var(--line);
  justify-content: flex-start; background: var(--surface); }
.panel-header h2 { color: var(--ink); font-size: 14px; font-weight: 800; }
.panel-caption { flex: 1; margin-right: 0; color: var(--muted); }
.panel-toggle { position: relative; width: 34px; min-height: 34px; flex-basis: 34px;
  margin-left: auto; padding: 0; border: 1px solid var(--line); border-radius: var(--radius-sm);
  background: var(--surface); color: var(--ink-soft); font-size: 0; }
.panel-header > button:not(.panel-toggle) { margin-left: auto; }
.panel-header > button:not(.panel-toggle) + .panel-toggle { margin-left: 0; }
.panel-toggle:hover { border-color: var(--line-strong); background: var(--surface-subtle); }
.panel-toggle::before { content: ""; position: absolute; top: 11px; left: 12px;
  width: 7px; height: 7px; border-right: 2px solid currentColor;
  border-bottom: 2px solid currentColor; transform: rotate(45deg); }
.panel-toggle[aria-expanded="false"]::before { top: 13px; transform: rotate(-45deg); }
.panel-body { padding: 16px; }
.action-surface { margin: 0 0 16px; padding: 14px; border: 1px solid var(--line);
  border-radius: var(--radius-sm); background: var(--surface-subtle); }
.action-row { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line); }
.subsection-heading { min-height: 28px; margin-bottom: 7px; }
.subsection-heading h3 { color: var(--muted); font-size: 11px; letter-spacing: .04em;
  text-transform: uppercase; }
.readiness-summary { padding: 11px 12px; border: 1px solid var(--line);
  border-left: 3px solid var(--line-strong); background: var(--surface-subtle); }
.readiness-summary.ok { border-left-color: var(--success); background: var(--success-soft); }
.readiness-summary.bad { border-left-color: var(--danger); background: var(--danger-soft); }
.summary-count { color: var(--ink); font-size: 18px; }
.summary-copy { color: var(--ink-soft); }
#status-sections { gap: 10px 14px; }
.readiness-section h3 { color: var(--muted); font-size: 11px; }
.check-grid { border: 1px solid var(--line); border-radius: var(--radius-sm);
  background: var(--surface); }
.check { border-bottom: 1px solid var(--line); }
.check.bad { box-shadow: inset 3px 0 var(--danger); background: var(--danger-soft); }
.check-state { color: var(--danger); }
.check.ok .check-state { color: var(--success); }
.check-detail { color: var(--muted); }
.check-action { color: var(--danger); }
table { border: 1px solid var(--line); border-radius: 0; background: var(--surface); }
th, td { padding: 9px 11px; border-bottom: 1px solid var(--line); color: var(--ink-soft); }
th { background: var(--surface-subtle); color: var(--muted); font-size: 11px;
  font-weight: 800; text-transform: none; }
tbody tr:hover td { background: #fbfcfc; }
button { min-height: 36px; padding: 7px 13px; border: 1px solid transparent;
  border-radius: var(--radius-sm); background: var(--primary); color: #fff; }
button:hover { background: var(--primary-hover); }
button.secondary { border: 1px solid var(--line-strong); background: var(--surface);
  color: var(--ink-soft); }
button.secondary:hover { border-color: #8fa0a8; background: var(--surface-subtle); color: var(--ink); }
button.danger { border: 1px solid #efb9b4; background: var(--surface); color: var(--danger); }
button.danger:hover { border-color: var(--danger); background: var(--danger-soft); }
button.table-action { min-height: 30px; padding: 4px 8px; }
.action-row > button { width: var(--command-width); }
.panel-header > button:not(.panel-toggle) { width: var(--command-width); }
.action-table button, .button-group button { width: var(--compact-command-width); }
label { color: var(--ink-soft); font-size: 12px; font-weight: 700; }
input[type=text], input[type=email], input[type=url], input[type=number], input[type=date],
input[type=datetime-local], input[type=time], select { border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm); background: var(--surface); color: var(--ink);
  color-scheme: light; }
input[type=checkbox], input[type=radio] { accent-color: var(--primary); }
.segmented { border-color: var(--line-strong); border-radius: var(--radius-sm);
  background: var(--surface); }
.segmented label { color: var(--muted); }
.segmented label + label { border-color: var(--line); }
.segmented input:checked + span { background: var(--primary-soft); color: var(--primary); }
.segmented input:focus-visible + span { outline-color: var(--focus); }
.source, .msg { color: var(--muted); }
.s-completed { color: var(--success); }
.s-running, .s-queued { color: var(--warning); }
.s-failed { color: var(--danger); }
.run-detail { border-top: 1px solid var(--line); }
.run-detail-head h3 { color: var(--ink); }
.run-facts { border: 1px solid var(--line); background: var(--surface-subtle); }
.run-fact { border-color: var(--line); }
.run-fact dt { color: var(--muted); }
.run-fact dd { color: var(--ink); }
.run-error { border-left-color: var(--danger); background: var(--danger-soft); color: var(--danger); }
.empty { color: var(--muted); }
td.empty { padding: 16px 11px; text-align: left; }
@media (max-width: 640px) {
  main { padding: 20px 14px 40px; }
  .page-intro h1 { font-size: 21px; }
  .panel-header { padding: 0 11px; }
  .panel-body { padding: 12px; }
  .action-surface { padding: 12px; }
  .panel-caption { max-width: 160px; }
}
</style>
</head>
<body>
<a class="skip-link" href="#main-content">Skip to main content</a>
<header class="app-header">
  <div class="app-bar">
    <a class="brand-lockup" href="/admin" aria-label="AzBrief Admin">
      <span class="brand-mark" aria-hidden="true">AZ</span>
      <span class="brand-copy"><span class="brand-name">AzBrief</span><span class="brand-area">Operations</span></span>
    </a>
    <nav class="primary-nav" aria-label="Primary views">
      <a class="nav-link" href="/admin" aria-current="page">Admin</a>
      __ARCHIVE_LINK__
    </nav>
    <div class="shell-identity"><span class="shell-profile">__PROFILE__</span><span class="shell-user">__USER__</span></div>
  </div>
</header>
<main id="main-content">
  <div class="page-intro">
    <div><p class="page-kicker">Control plane</p><h1>Admin console</h1></div>
  </div>
  <section class="panel panel-status" aria-labelledby="status-title">
    <div class="panel-header">
      <h2 id="status-title">Configuration status</h2>
      <button class="secondary" id="status-refresh" type="button">Refresh</button>
    </div>
    <div class="panel-body">
      <div class="readiness-summary" id="status-summary" aria-live="polite">
        <div class="empty">Loading…</div>
      </div>
      <div id="status-sections"></div>
    </div>
  </section>

  <section class="panel panel-schedule" aria-labelledby="schedule-title">
    <div class="panel-header"><h2 id="schedule-title">Automatic runs</h2></div>
    <div class="panel-body">
      <form id="schedule-form" class="schedule-form action-surface">
        <label><span class="field-heading"><span>Daily run time</span><span class="field-requirement required">Required</span></span>
          <input id="schedule-time" type="time" step="60" placeholder="Required • HH:MM" title="Required • HH:MM" required aria-required="true">
          <span id="schedule-conversion" class="source time-conversion" aria-live="polite"></span>
        </label>
        <fieldset class="time-basis">
          <legend>Input and display time</legend>
          <div class="segmented">
            <label><input id="schedule-basis-local" type="radio" name="schedule-basis" value="local" checked><span>Local</span></label>
            <label><input id="schedule-basis-utc" type="radio" name="schedule-basis" value="utc"><span>UTC</span></label>
          </div>
        </fieldset>
        <div class="action-row">
          <span id="schedule-msg" class="msg" role="status"></span>
          <button id="schedule-add" type="submit">Add schedule</button>
        </div>
      </form>
      <div class="subsection-heading"><h3>Scheduled times</h3></div>
      <div class="table-wrap"><table class="schedule-table action-table">
        <thead><tr><th>Actions</th><th id="schedule-run-time">Run time (Local)</th><th>Cron (UTC)</th><th id="schedule-next-time">Next run (Local)</th><th>Source</th></tr></thead>
        <tbody id="schedules"><tr><td colspan="5" class="empty">Loading…</td></tr></tbody>
      </table></div>
    </div>
  </section>

  <section class="panel panel-subscribers" aria-labelledby="subscriber-title">
    <div class="panel-header"><h2 id="subscriber-title">Subscribers</h2></div>
    <div class="panel-body">
      <form id="sub-form" class="manage-form action-surface">
        <label><span class="field-heading"><span>Email</span><span class="field-requirement required">Required</span></span><input id="sub-email" name="email" type="email" maxlength="320" placeholder="Required • name@company.com" required aria-required="true"></label>
        <label><span class="field-heading"><span>Name</span><span class="field-requirement required">Required</span></span><input id="sub-name" name="name" type="text" maxlength="200" placeholder="Required • subscriber name" required aria-required="true"></label>
        <label class="wide"><span class="field-heading"><span>Role</span><span class="field-requirement">Optional</span></span><input id="sub-role" name="role" type="text" maxlength="500" placeholder="Optional • e.g. Cloud Architect"></label>
        <label><span class="field-heading"><span>Report language</span><span class="field-requirement required">Required</span></span><select id="sub-language" name="language" required aria-required="true"><option value="ko">Korean</option><option value="en">English</option><option value="ja">Japanese</option></select></label>
        <label><span class="field-heading"><span>Alert level</span><span class="field-requirement required">Required</span></span><select id="sub-alert" name="alert_level" required aria-required="true"><option value="all">All updates</option><option value="important_and_above">Important and above</option><option value="critical_only">Critical only</option></select></label>
        <label class="wide"><span class="field-heading"><span>Management Group IDs</span><span class="field-requirement">Optional</span></span><input id="sub-management-groups" type="text" maxlength="4000" placeholder="Optional • comma-separated IDs"></label>
        <label class="wide"><span class="field-heading"><span>Subscription IDs</span><span class="field-requirement">Optional</span></span><input id="sub-subscriptions" type="text" maxlength="4000" placeholder="Optional • comma-separated GUIDs"></label>
        <label class="wide"><span class="field-heading"><span>Resource group names or ARM IDs</span><span class="field-requirement">Optional</span></span><input id="sub-groups" type="text" maxlength="4000" placeholder="Optional • comma-separated names or ARM IDs"></label>
        <label class="wide"><span class="field-heading"><span>Focus services</span><span class="field-requirement">Optional</span></span><input id="sub-services" type="text" maxlength="4000" placeholder="Optional • e.g. AKS, Storage"></label>
        <div class="manage-actions action-row">
          <span id="sub-msg" class="msg" role="status"></span>
          <button id="sub-cancel" class="secondary" type="button" hidden>Cancel</button>
          <button id="sub-add" type="submit">Add subscriber</button>
        </div>
      </form>
      <div class="subsection-heading"><h3>Registered subscribers</h3></div>
      <div class="table-wrap"><table class="action-table">
        <thead><tr><th>Actions</th><th>Email</th><th>Name</th><th>Role</th><th>Language</th><th>Alert level</th></tr></thead>
        <tbody id="subs"><tr><td colspan="6" class="empty">Loading…</td></tr></tbody>
      </table></div>
    </div>
  </section>

  <section class="panel panel-admins" aria-labelledby="administrator-title">
    <div class="panel-header"><h2 id="administrator-title">Administrators</h2></div>
    <div class="panel-body">
      <form id="admin-form" class="manage-form action-surface">
        <label class="wide"><span class="field-heading"><span>Entra object ID, UPN, or group ID</span><span class="field-requirement required">Required</span></span><input id="admin-principal" name="principal" type="text" maxlength="320" placeholder="Required • one object ID, UPN, or group ID" required aria-required="true"></label>
        <div class="manage-actions action-row"><span id="admin-msg" class="msg" role="status"></span><button id="admin-add" type="submit">Add administrator</button></div>
      </form>
      <div class="subsection-heading"><h3>Access list</h3></div>
      <div class="table-wrap"><table class="compact action-table">
        <thead><tr><th>Actions</th><th>Principal</th><th>Source</th></tr></thead>
        <tbody id="admins"><tr><td colspan="3" class="empty">Loading…</td></tr></tbody>
      </table></div>
    </div>
  </section>

  <section class="panel panel-updates" aria-labelledby="updates-title">
    <div class="panel-header"><h2 id="updates-title">Recent Azure updates</h2></div>
    <div class="panel-body">
      <div class="table-wrap"><table class="action-table">
        <thead><tr><th>Action</th><th id="updates-published-time">Published (Local)</th><th>Title</th><th>Type</th></tr></thead>
        <tbody id="updates"><tr><td colspan="4" class="empty">Loading…</td></tr></tbody>
      </table></div>
    </div>
  </section>

  <section class="panel panel-run" aria-labelledby="run-title">
    <div class="panel-header"><h2 id="run-title">Manual run</h2></div>
    <div class="panel-body">
      <div class="action-surface" aria-label="Manual run settings">
        <div class="controls">
          <label><span class="field-heading"><span>Target</span><span class="field-requirement required">Required</span></span><select id="run-mode" required aria-required="true">
            <option value="checkpoint">After checkpoint</option>
            <option value="date_range">Published date range</option>
            <option value="recent">Most recent updates</option>
            <option value="update_id">Update ID</option>
            <option value="update_url">Azure Update URL</option>
          </select></label>
          <div class="run-field run-time-field" data-run-mode="checkpoint">
            <label><span class="field-heading"><span>Baseline time</span><span class="field-requirement">Optional</span></span><input type="datetime-local" id="since" step="60" placeholder="Optional • date and time" title="Optional • date and time">
              <span id="since-conversion" class="source time-conversion" aria-live="polite"></span>
            </label>
            <fieldset class="time-basis">
              <legend>Input and display time</legend>
              <div class="segmented">
                <label><input id="since-basis-local" type="radio" name="since-basis" value="local" checked><span>Local</span></label>
                <label><input id="since-basis-utc" type="radio" name="since-basis" value="utc"><span>UTC</span></label>
              </div>
            </fieldset>
          </div>
          <div class="run-field" data-run-mode="date_range" hidden>
            <label><span class="field-heading"><span>Start date</span><span class="field-requirement required">Required</span></span><input type="date" id="start-date" placeholder="Required • start date" title="Required • start date"></label>
            <label><span class="field-heading"><span>End date</span><span class="field-requirement required">Required</span></span><input type="date" id="end-date" placeholder="Required • end date" title="Required • end date"></label>
          </div>
          <label class="run-field" data-run-mode="recent" hidden><span class="field-heading"><span>Recent count</span><span class="field-requirement required">Required</span></span>
            <input type="number" id="recent-count" min="1" max="100" value="10" placeholder="Required • 1–100">
          </label>
          <label class="run-field" data-run-mode="update_id" hidden><span class="field-heading"><span>Update ID</span><span class="field-requirement required">Required</span></span>
            <input type="text" id="update-id" inputmode="numeric" maxlength="32" placeholder="Required • numeric update ID">
          </label>
          <label class="run-field" data-run-mode="update_url" hidden><span class="field-heading"><span>Azure Update URL</span><span class="field-requirement required">Required</span></span>
            <input type="url" id="update-url" maxlength="2048" placeholder="Required • azure.microsoft.com/updates/…">
          </label>
        </div>
        <div class="action-row">
          <span class="msg" id="msg" role="status"></span>
          <label class="inline-check"><input type="checkbox" id="send-email"> Send digest email</label>
          <label class="inline-check"><input type="checkbox" id="dry"> Dry run</label>
          <button id="run">Start run</button>
        </div>
      </div>
      <div class="subsection-heading"><h3>Run history</h3><span id="run-summary" class="source"></span></div>
      <div class="table-wrap"><table class="action-table">
        <thead><tr><th>Details</th><th>Run ID</th><th>Status</th><th>Selection</th><th>Targets</th><th>Analyzed</th>
          <th>Failed</th><th>Deferred</th><th>Elapsed (s)</th><th id="runs-start-time">Started (Local)</th></tr></thead>
        <tbody id="runs"><tr><td colspan="10" class="empty">Loading…</td></tr></tbody>
      </table></div>
      <div id="run-detail" class="run-detail" hidden>
        <div class="run-detail-head">
          <h3 id="run-detail-title">Run details</h3>
          <button id="run-detail-close" class="secondary" type="button">Close</button>
        </div>
        <dl id="run-detail-facts" class="run-facts"></dl>
        <div id="run-detail-error" class="run-error" role="alert" hidden></div>
      </div>
    </div>
  </section>
</main>
<script nonce="__NONCE__">
const $ = (id) => document.getElementById(id);
const text = (v) => (v === null || v === undefined || v === '') ? '—' : String(v);
let timeBasis = 'local';
let activeRunDetail = null;

async function api(path, options) {
  const res = await fetch(path, Object.assign({credentials: 'same-origin'}, options || {}));
  if (!res.ok) {
    const raw = await res.text();
    try { throw new Error(JSON.parse(raw).detail || raw); } catch (e) {
      if (e instanceof SyntaxError) throw new Error(raw || String(res.status));
      throw e;
    }
  }
  return res.status === 204 ? null : res.json();
}

function row(cells, cls) {
  const tr = document.createElement('tr');
  cells.forEach((c, i) => {
    const td = document.createElement('td');
    td.textContent = text(c);
    if (i === 1 && cls) td.className = cls;
    tr.appendChild(td);
  });
  return tr;
}

function fill(tbody, rows, colspan, emptyText) {
  tbody.replaceChildren();
  if (!rows.length) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = colspan; td.className = 'empty'; td.textContent = emptyText;
    tr.appendChild(td); tbody.appendChild(tr); return;
  }
  rows.forEach((r) => tbody.appendChild(r));
}

function setPanelCaption(panelKey, value) {
  const caption = document.querySelector('[data-panel-key="' + panelKey + '"] .panel-caption');
  if (caption) caption.textContent = value;
}

function initializeCollapsiblePanels() {
  document.querySelectorAll('section.panel').forEach((panel) => {
    const header = panel.querySelector(':scope > .panel-header');
    const body = panel.querySelector(':scope > .panel-body');
    const heading = header && header.querySelector('h2');
    if (!header || !body || !heading) return;
    const panelKey = heading.id.replace(/-title$/, '');
    panel.dataset.panelKey = panelKey;
    body.id = panelKey + '-body';

    const caption = document.createElement('span');
    caption.className = 'panel-caption'; caption.textContent = 'Loading…';
    caption.hidden = true; heading.insertAdjacentElement('afterend', caption);

    const button = document.createElement('button');
    button.type = 'button'; button.className = 'panel-toggle secondary';
    button.textContent = '−'; button.setAttribute('aria-expanded', 'true');
    button.setAttribute('aria-controls', body.id);
    const updateButton = (collapsed) => {
      const action = collapsed ? 'expand' : 'collapse';
      button.textContent = collapsed ? '+' : '−';
      button.setAttribute('aria-expanded', String(!collapsed));
      button.setAttribute('aria-label', heading.textContent + ' ' + action);
      button.title = heading.textContent + ' ' + action;
    };
    button.addEventListener('click', () => {
      const collapsed = !panel.classList.contains('collapsed');
      panel.classList.toggle('collapsed', collapsed);
      body.hidden = collapsed; caption.hidden = !collapsed;
      updateButton(collapsed);
    });
    updateButton(false); header.appendChild(button);
  });
}

function commaList(value) {
  return value.split(',').map((item) => item.trim()).filter(Boolean);
}

function selectorText(selection) {
  if (!selection || selection.mode === 'checkpoint') return 'After checkpoint';
  if (selection.mode === 'date_range') return selection.start_date.slice(0, 10) + ' ~ ' + selection.end_date.slice(0, 10);
  if (selection.mode === 'recent') return 'Most recent ' + selection.recent_count;
  if (selection.mode === 'update_id') return 'ID ' + selection.update_id;
  return 'One URL';
}

function runStatusText(status) {
  return ({queued: 'Queued', running: 'Running', completed: 'Completed', failed: 'Failed'})[status] || status;
}

function dateTimeTextForBasis(value, basis) {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return String(value);
  const utc = basis === 'utc';
  const parts = [
    utc ? parsed.getUTCFullYear() : parsed.getFullYear(),
    padTimePart((utc ? parsed.getUTCMonth() : parsed.getMonth()) + 1),
    padTimePart(utc ? parsed.getUTCDate() : parsed.getDate())
  ];
  const clock = [
    padTimePart(utc ? parsed.getUTCHours() : parsed.getHours()),
    padTimePart(utc ? parsed.getUTCMinutes() : parsed.getMinutes()),
    padTimePart(utc ? parsed.getUTCSeconds() : parsed.getSeconds())
  ];
  return parts.join('-') + ' ' + clock.join(':') + ' ' + (utc ? 'UTC' : localZoneName());
}

function displayedDateTime(value) {
  return dateTimeTextForBasis(value, timeBasis);
}

function timeBasisLabel() {
  return timeBasis === 'utc' ? 'UTC' : 'Local';
}

function dateTimeInputToUtc(value, utcBasis) {
  if (!value) return '';
  const parsed = new Date(utcBasis ? value + 'Z' : value);
  return Number.isNaN(parsed.valueOf()) ? '' : parsed.toISOString();
}

function dateTimeInputValue(value, utcBasis) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return '';
  const parts = [
    utcBasis ? parsed.getUTCFullYear() : parsed.getFullYear(),
    padTimePart((utcBasis ? parsed.getUTCMonth() : parsed.getMonth()) + 1),
    padTimePart(utcBasis ? parsed.getUTCDate() : parsed.getDate())
  ];
  const clock = [
    padTimePart(utcBasis ? parsed.getUTCHours() : parsed.getHours()),
    padTimePart(utcBasis ? parsed.getUTCMinutes() : parsed.getMinutes())
  ];
  return parts.join('-') + 'T' + clock.join(':');
}

function selectedSinceUtc() {
  return dateTimeInputToUtc($('since').value, timeBasis === 'utc');
}

function updateSinceConversion() {
  const value = $('since').value;
  const conversion = $('since-conversion');
  if (timeBasis === 'utc') {
    conversion.textContent = value ? 'Request as ' + value.replace('T', ' ') + ' UTC' : 'Using UTC';
    return;
  }
  const zone = localZoneName();
  conversion.textContent = value
    ? value.replace('T', ' ') + ' ' + zone + ' → '
      + dateTimeTextForBasis(selectedSinceUtc(), 'utc')
    : zone + ' · Local is the default';
}

function padTimePart(value) {
  return String(value).padStart(2, '0');
}

function localZoneName() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Browser local time';
}

function localTimeToUtc(value) {
  const [hour, minute] = value.split(':').map(Number);
  const today = new Date();
  const local = new Date(today.getFullYear(), today.getMonth(), today.getDate(), hour, minute);
  return padTimePart(local.getUTCHours()) + ':' + padTimePart(local.getUTCMinutes());
}

function utcTimeToLocal(value) {
  if (!value) return '—';
  const [hour, minute] = value.split(':').map(Number);
  const today = new Date();
  const utc = new Date(Date.UTC(
    today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate(), hour, minute
  ));
  return padTimePart(utc.getHours()) + ':' + padTimePart(utc.getMinutes());
}

function selectedScheduleTimeUtc() {
  const value = $('schedule-time').value;
  if (!value || timeBasis === 'utc') return value;
  return localTimeToUtc(value);
}

function displayedScheduleTime(value, nextRunAt) {
  if (!value) return 'Cron';
  if (timeBasis === 'utc') return value;
  const nextRun = new Date(nextRunAt);
  return Number.isNaN(nextRun.valueOf())
    ? utcTimeToLocal(value)
    : padTimePart(nextRun.getHours()) + ':' + padTimePart(nextRun.getMinutes());
}

function updateScheduleConversion() {
  const value = $('schedule-time').value;
  const conversion = $('schedule-conversion');
  if (timeBasis === 'utc') {
    conversion.textContent = value ? 'Store as ' + value + ' UTC' : 'Using UTC';
    return;
  }
  const zone = localZoneName();
  conversion.textContent = value
    ? value + ' ' + zone + ' → ' + localTimeToUtc(value) + ' UTC'
    : zone + ' · Local is the default';
}

function updateTimeHeadings() {
  const label = timeBasisLabel();
  $('runs-start-time').textContent = 'Started (' + label + ')';
  $('schedule-run-time').textContent = 'Run time (' + label + ')';
  $('schedule-next-time').textContent = 'Next run (' + label + ')';
  $('updates-published-time').textContent = 'Published (' + label + ')';
}

function syncTimeBasisControls() {
  document.querySelectorAll('.time-basis input[type="radio"]').forEach((input) => {
    input.checked = input.value === timeBasis;
  });
}

function convertTimeInputs(nextBasis) {
  if (nextBasis === timeBasis) return;
  const since = $('since').value;
  if (since) {
    const instant = new Date(timeBasis === 'utc' ? since + 'Z' : since);
    $('since').value = dateTimeInputValue(instant, nextBasis === 'utc');
  }
  const scheduleTime = $('schedule-time').value;
  if (scheduleTime) {
    $('schedule-time').value = nextBasis === 'utc'
      ? localTimeToUtc(scheduleTime) : utcTimeToLocal(scheduleTime);
  }
}

function setTimeBasis(value, reload) {
  const nextBasis = value === 'utc' ? 'utc' : 'local';
  convertTimeInputs(nextBasis);
  timeBasis = nextBasis;
  syncTimeBasisControls(); updateTimeHeadings();
  updateSinceConversion(); updateScheduleConversion();
  if (reload === false) return;
  if (activeRunDetail && !$('run-detail').hidden) renderRunDetail(activeRunDetail);
  Promise.allSettled([loadRuns(), loadSchedules(), loadUpdates()]);
}

$('schedule-time').addEventListener('input', updateScheduleConversion);
$('since').addEventListener('input', updateSinceConversion);
document.querySelectorAll('.time-basis input[type="radio"]').forEach((input) => {
  input.addEventListener('change', () => {
    if (input.checked) setTimeBasis(input.value);
  });
});

function sourceText(source) {
  return ({scheduled_digest: 'Automatic run', admin_run: 'Admin manual run',
    api_orchestrate: 'Orchestrator API', api_analyze: 'Single analysis API',
    api_batch: 'Batch API', mcp: 'MCP'})[source] || source;
}

function deliveryText(run) {
  if (run.dry_run) return 'Target check only';
  if (!run.send_email) return 'Email not requested';
  if (run.email_sent) return 'Digest sent';
  if (run.status === 'queued' || run.status === 'running') return 'Delivery pending';
  return 'Not delivered';
}

function checkpointText(run) {
  if (!run.commit_checkpoint) return 'Unchanged';
  if (run.dry_run) return 'Dry run';
  return run.checkpoint_committed ? 'Updated' : 'Not updated';
}

function updateRunFields() {
  const mode = $('run-mode').value;
  document.querySelectorAll('.run-field').forEach((field) => {
    const active = field.dataset.runMode === mode;
    field.hidden = !active;
    field.querySelectorAll('input, select').forEach((control) => {
      const required = active && mode !== 'checkpoint';
      control.required = required;
      control.setAttribute('aria-required', String(required));
    });
  });
}

$('run-mode').addEventListener('change', updateRunFields);

function deleteButton(label, handler) {
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'danger'; button.textContent = label;
  button.addEventListener('click', handler);
  return button;
}

function managedCell(managed, handler) {
  const td = document.createElement('td');
  if (managed) td.appendChild(deleteButton('Delete', handler));
  else { td.className = 'source'; td.textContent = 'Deployment'; }
  return td;
}

function resetSubscriberEditor(message) {
  $('sub-form').reset(); delete $('sub-form').dataset.editingEmail;
  $('sub-add').textContent = 'Add subscriber'; $('sub-cancel').hidden = true;
  $('sub-msg').textContent = message || '';
}

function editSubscriber(subscriber) {
  $('sub-form').dataset.editingEmail = subscriber.email;
  $('sub-email').value = subscriber.email; $('sub-name').value = subscriber.name;
  $('sub-role').value = subscriber.role || ''; $('sub-language').value = subscriber.language;
  $('sub-alert').value = subscriber.alert_level;
  $('sub-management-groups').value = (subscriber.management_groups || []).join(', ');
  $('sub-subscriptions').value = (subscriber.subscriptions || []).join(', ');
  $('sub-groups').value = (subscriber.resource_groups || []).join(', ');
  $('sub-services').value = (subscriber.focus_services || []).join(', ');
  $('sub-add').textContent = 'Save changes'; $('sub-cancel').hidden = false;
  $('sub-msg').textContent = 'Editing: ' + subscriber.email;
  document.querySelector('.panel-subscribers').scrollIntoView({behavior: 'smooth', block: 'start'});
  $('sub-name').focus();
}

$('sub-cancel').addEventListener('click', () => resetSubscriberEditor('Changes canceled.'));

function tableButton(label, handler) {
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'secondary table-action'; button.textContent = label;
  button.addEventListener('click', handler);
  return button;
}

function addRunFact(parent, label, value) {
  const item = document.createElement('div'); item.className = 'run-fact';
  const term = document.createElement('dt'); term.textContent = label;
  const description = document.createElement('dd'); description.textContent = text(value);
  item.append(term, description); parent.appendChild(item);
}

function renderRunDetail(run) {
  const detail = $('run-detail'); const facts = $('run-detail-facts');
  activeRunDetail = run;
  $('run-detail-title').textContent = 'Run details · ' + run.run_id.slice(0, 8);
  facts.replaceChildren();
  [
    ['Status', runStatusText(run.status)], ['Source', sourceText(run.source)],
    ['Selection', selectorText(run.selection)], ['Delivery', deliveryText(run)],
    ['Started', displayedDateTime(run.started_at)], ['Finished', displayedDateTime(run.finished_at)],
    ['Targets / analyzed / failed', run.total + ' / ' + run.analyzed + ' / ' + run.failed],
    ['Archived / archive failures', run.archived + ' / ' + run.archive_failed],
    ['Relevant / deferred / pending', run.relevant + ' / ' + run.deferred + ' / ' + run.pending],
    ['Checkpoint', checkpointText(run)], ['Watermark', displayedDateTime(run.watermark)],
    ['Run ID', run.run_id]
  ].forEach(([label, value]) => addRunFact(facts, label, value));
  const error = $('run-detail-error'); error.hidden = !run.error;
  error.textContent = run.error ? 'Error: ' + run.error : '';
  detail.hidden = false;
}

async function showRunDetail(runId, button) {
  button.disabled = true;
  try { renderRunDetail(await api('/api/admin/runs/' + encodeURIComponent(runId))); }
  catch (e) {
    $('run-detail').hidden = false; $('run-detail-error').hidden = false;
    $('run-detail-error').textContent = 'Could not load run details: ' + e.message;
  } finally { button.disabled = false; }
}

$('run-detail-close').addEventListener('click', () => { $('run-detail').hidden = true; });

async function loadStatus() {
  const button = $('status-refresh'); button.disabled = true;
  try {
    const data = await api('/api/admin/status');
    const summary = $('status-summary'); summary.replaceChildren();
    summary.className = 'readiness-summary ' + (data.ok ? 'ok' : 'bad');
    const count = document.createElement('div'); count.className = 'summary-count';
    count.textContent = data.ready + ' / ' + data.total;
    const copy = document.createElement('div'); copy.className = 'summary-copy';
    copy.textContent = data.ok ? 'All components are ready.' : 'Some components need attention.';
    summary.append(count, copy);
    setPanelCaption(
      'status', data.ready + ' / ' + data.total + ' ready · ' + (data.ok ? 'No action needed' : 'Attention required')
    );

    const sections = $('status-sections'); sections.replaceChildren();
    data.sections.forEach((sectionData) => {
      const section = document.createElement('div'); section.className = 'readiness-section';
      const heading = document.createElement('h3'); heading.textContent = sectionData.title;
      const grid = document.createElement('div'); grid.className = 'check-grid';
      sectionData.checks.forEach((checkData) => {
        const check = document.createElement('div');
        check.className = 'check ' + (checkData.ok ? 'ok' : 'bad');
        const head = document.createElement('div'); head.className = 'check-head';
        const name = document.createElement('div'); name.className = 'check-name';
        name.textContent = checkData.name;
        const state = document.createElement('div'); state.className = 'check-state';
        state.textContent = checkData.ok ? 'Ready' : 'Attention';
        const detail = document.createElement('div'); detail.className = 'check-detail';
        detail.textContent = checkData.detail;
        head.append(name, state); check.append(head, detail);
        if (!checkData.ok && checkData.action) {
          const action = document.createElement('div'); action.className = 'check-action';
          action.textContent = 'Action: ' + checkData.action; check.appendChild(action);
        }
        grid.appendChild(check);
      });
      section.append(heading, grid); sections.appendChild(section);
    });
  } finally { button.disabled = false; }
}

$('status-refresh').addEventListener('click', async () => {
  try { await loadStatus(); } catch (e) {
    $('status-summary').className = 'readiness-summary bad';
    $('status-summary').textContent = 'Could not load status: ' + e.message;
  }
});

async function loadRuns() {
  const data = await api('/api/admin/runs');
  const active = data.runs.filter((run) => run.status === 'queued' || run.status === 'running').length;
  const failed = data.runs.filter((run) => run.status === 'failed').length;
  $('run-summary').textContent = active + ' active · ' + failed + ' recent failures';
  setPanelCaption('run', data.runs.length + ' recent · ' + active + ' active · ' + failed + ' failed');
  fill($('runs'), data.runs.map((r) => {
    const tr = row(
      [r.run_id.slice(0, 8), runStatusText(r.status), selectorText(r.selection), r.total,
      r.analyzed, r.failed, r.deferred, r.elapsed_seconds, displayedDateTime(r.started_at)],
      's-' + r.status
    );
    const action = document.createElement('td');
    const button = tableButton('View', () => showRunDetail(r.run_id, button));
    action.appendChild(button); tr.prepend(action); return tr;
  }), 10, 'No runs found.');
}

async function loadSubs() {
  const data = await api('/api/admin/subscribers');
  const managedCount = data.subscribers.filter((subscriber) => subscriber.managed).length;
  setPanelCaption('subscriber', data.subscribers.length + ' total · ' + managedCount + ' managed');
  $('sub-add').disabled = !data.configuration_writable;
  if (!data.configuration_writable) $('sub-msg').textContent = 'Durable state storage is required.';
  fill($('subs'), data.subscribers.map((s) => {
    const tr = row([s.email, s.name, s.role, s.language, s.alert_level]);
    const management = document.createElement('td');
    if (!s.managed) { management.className = 'source'; management.textContent = 'Deployment'; }
    else {
      const buttons = document.createElement('div'); buttons.className = 'button-group';
      buttons.append(tableButton('Edit', () => editSubscriber(s)));
      buttons.append(deleteButton('Delete', async () => {
        if (!confirm('Delete subscriber ' + s.email + '?')) return;
        try {
          await api('/api/admin/subscribers/' + encodeURIComponent(s.email), {method: 'DELETE'});
          if ($('sub-form').dataset.editingEmail === s.email) resetSubscriberEditor();
          $('sub-msg').textContent = 'Subscriber deleted.';
          await Promise.all([loadSubs(), loadStatus()]);
        } catch (e) { $('sub-msg').textContent = 'Delete failed: ' + e.message; }
      }));
      management.appendChild(buttons);
    }
    tr.prepend(management);
    return tr;
  }), 6, 'No subscribers found.');
}

async function loadAdmins() {
  const data = await api('/api/admin/administrators');
  const managedCount = data.administrators.filter((admin) => admin.managed).length;
  setPanelCaption('administrator', data.administrators.length + ' total · ' + managedCount + ' managed');
  $('admin-add').disabled = !data.configuration_writable;
  if (!data.configuration_writable) $('admin-msg').textContent = 'Durable state storage is required.';
  fill($('admins'), data.administrators.map((a) => {
    const tr = row([a.principal, a.managed ? 'Admin console' : 'Deployment']);
    tr.prepend(managedCell(a.managed, async () => {
      if (!confirm('Delete administrator ' + a.principal + '?')) return;
      try { await api('/api/admin/administrators/' + encodeURIComponent(a.principal), {method: 'DELETE'}); await loadAdmins(); }
      catch (e) { $('admin-msg').textContent = 'Delete failed: ' + e.message; }
    }));
    return tr;
  }), 3, 'No administrators found.');
}

async function loadSchedules() {
  const data = await api('/api/admin/schedules');
  const nextRuns = data.schedules.map((schedule) => schedule.next_run_at).filter(Boolean).sort();
  setPanelCaption(
    'schedule',
    data.schedules.length + ' schedules · next ' + (nextRuns.length ? displayedDateTime(nextRuns[0]) : '—')
  );
  $('schedule-add').disabled = !data.configuration_writable;
  if (!data.configuration_writable) $('schedule-msg').textContent = 'Durable state storage is required.';
  fill($('schedules'), data.schedules.map((schedule) => {
    const tr = row([
      displayedScheduleTime(schedule.time_utc, schedule.next_run_at), schedule.cron_expression,
      displayedDateTime(schedule.next_run_at),
      schedule.managed ? 'Admin console' : 'Deployment'
    ]);
    tr.prepend(managedCell(schedule.managed, async () => {
      if (!confirm(displayedScheduleTime(schedule.time_utc, schedule.next_run_at) + ' ' + timeBasisLabel()
          + ' schedule?')) return;
      try {
        await api('/api/admin/schedules/' + encodeURIComponent(schedule.time_utc), {method: 'DELETE'});
        $('schedule-msg').textContent = 'Schedule deleted.'; await loadSchedules();
      } catch (e) { $('schedule-msg').textContent = 'Delete failed: ' + e.message; }
    }));
    return tr;
  }), 5, 'No automatic schedules found.');
}

async function loadUpdates() {
  const data = await api('/api/admin/updates');
  const newest = data.updates.length ? displayedDateTime(data.updates[0].published_date) : '—';
  setPanelCaption('updates', data.updates.length + ' recent · newest ' + newest);
  fill($('updates'), data.updates.map((u) => {
    const tr = row([displayedDateTime(u.published_date), u.title, u.update_type]);
    const action = document.createElement('td');
    action.appendChild(tableButton('Select', () => {
      const runPanel = document.querySelector('.panel-run');
      if (runPanel.classList.contains('collapsed')) runPanel.querySelector('.panel-toggle').click();
      $('run-mode').value = 'update_url'; updateRunFields(); $('update-url').value = u.link;
      $('msg').textContent = 'Selected update: ' + u.id;
      document.querySelector('.panel-run').scrollIntoView({behavior: 'smooth', block: 'start'});
      $('update-url').focus();
    }));
    tr.prepend(action); return tr;
  }), 4, 'No updates found.');
}

$('dry').addEventListener('change', () => {
  $('send-email').disabled = $('dry').checked;
  if ($('dry').checked) $('send-email').checked = false;
});

$('run').addEventListener('click', async () => {
  const btn = $('run'); btn.disabled = true; $('msg').textContent = 'Starting run…';
  try {
    const mode = $('run-mode').value;
    const body = {
      mode: mode, dry_run: $('dry').checked, send_email: $('send-email').checked
    };
    if (mode === 'checkpoint') {
      const since = selectedSinceUtc(); if (since) body.since = since;
    } else if (mode === 'date_range') {
      body.start_date = $('start-date').value; body.end_date = $('end-date').value;
    } else if (mode === 'recent') {
      body.recent_count = Number($('recent-count').value);
    } else if (mode === 'update_id') {
      body.update_id = $('update-id').value.trim();
    } else {
      body.update_url = $('update-url').value.trim();
    }
    const res = await api('/api/admin/runs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    $('msg').textContent = 'Run started: ' + res.run_id.slice(0, 8);
    await loadRuns();
  } catch (e) {
    $('msg').textContent = 'Run failed: ' + e.message;
  } finally {
    btn.disabled = false;
  }
});

$('sub-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const button = $('sub-add'); button.disabled = true;
  $('sub-msg').textContent = 'Saving…';
  try {
    const editingEmail = event.target.dataset.editingEmail;
    const payload = {
      email: $('sub-email').value, name: $('sub-name').value,
      role: $('sub-role').value, language: $('sub-language').value,
      alert_level: $('sub-alert').value,
      management_groups: commaList($('sub-management-groups').value),
      subscriptions: commaList($('sub-subscriptions').value),
      resource_groups: commaList($('sub-groups').value),
      focus_services: commaList($('sub-services').value)
    };
    await api(
      editingEmail ? '/api/admin/subscribers/' + encodeURIComponent(editingEmail) : '/api/admin/subscribers',
      {
        method: editingEmail ? 'PUT' : 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      }
    );
    resetSubscriberEditor(
      editingEmail ? 'Subscriber updated.' : 'Subscriber added.'
    );
    await Promise.all([loadSubs(), loadStatus()]);
  } catch (e) { $('sub-msg').textContent = 'Save failed: ' + e.message; }
  finally { button.disabled = false; }
});

$('schedule-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const button = $('schedule-add'); button.disabled = true;
  $('schedule-msg').textContent = 'Saving…';
  try {
    await api('/api/admin/schedules', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({time_utc: selectedScheduleTimeUtc()})
    });
    event.target.reset(); syncTimeBasisControls(); updateScheduleConversion();
    $('schedule-msg').textContent = 'Schedule added.';
    await loadSchedules();
  } catch (e) { $('schedule-msg').textContent = 'Add failed: ' + e.message; }
  finally { button.disabled = false; }
});

$('admin-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const button = $('admin-add'); button.disabled = true;
  $('admin-msg').textContent = 'Saving…';
  try {
    await api('/api/admin/administrators', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({principal: $('admin-principal').value})
    });
    event.target.reset(); $('admin-msg').textContent = 'Administrator added.';
    await loadAdmins();
  } catch (e) { $('admin-msg').textContent = 'Add failed: ' + e.message; }
  finally { button.disabled = false; }
});

async function refresh() {
  try { await loadRuns(); } catch (e) { /* transient */ }
}

(async function init() {
  initializeCollapsiblePanels(); updateRunFields(); setTimeBasis('local', false);
  await Promise.allSettled([
    loadStatus(), loadRuns(), loadSchedules(), loadSubs(), loadAdmins(), loadUpdates()
  ]);
  setInterval(refresh, 10000);
})();
</script>
</body>
</html>
"""


def render_admin_page(
    nonce: str,
    profile: str,
    user: str,
    archive_enabled: bool = False,
) -> str:
    """Render the admin console HTML for one request.

    Args:
        nonce: Per-request CSP nonce applied to the inline style and script.
        profile: Deployment profile label shown in the header.
        user: Display name of the signed-in administrator.

    Returns:
        A complete HTML document.
    """
    archive_link = '<a class="nav-link" href="/archive">Archive</a>' if archive_enabled else ""
    return (
        _PAGE.replace("__NONCE__", escape(nonce, quote=True))
        .replace("__WEB_FONT_FACE__", WEB_FONT_FACE_CSS)
        .replace("__CONTROL_SURFACE_BASE__", CONTROL_SURFACE_BASE_CSS)
        .replace("__WEB_FONT_STACK__", WEB_FONT_STACK)
        .replace("__PROFILE__", escape(profile))
        .replace("__ARCHIVE_LINK__", archive_link)
        .replace("__USER__", escape(user))
    )
