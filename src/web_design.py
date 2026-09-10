"""Shared visual foundation for the Admin, Archive, and Feedback surfaces."""

DEFAULT_WEB_UI_LANGUAGE = "en"

CONTROL_SURFACE_FAVICON = (
    '<link rel="icon" href="data:image/svg+xml,%3Csvg%20xmlns=%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22'
    "%20viewBox=%220%200%2032%2032%22%3E%3Crect%20width=%2232%22%20height=%2232%22%20rx=%224%22"
    "%20fill=%22%23172126%22%2F%3E%3Ctext%20x=%2216%22%20y=%2221%22%20text-anchor=%22middle%22"
    "%20fill=%22white%22%20font-family=%22sans-serif%22%20font-size=%2213%22"
    '%20font-weight=%22700%22%3EAZ%3C%2Ftext%3E%3C%2Fsvg%3E">'
)

_LUCIDE_ICONS = {
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "refresh-cw": (
        '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>'
        '<path d="M21 3v5h-5"/>'
        '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>'
        '<path d="M8 16H3v5"/>'
    ),
    "arrow-left": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    "sliders-horizontal": (
        '<path d="M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3"/>'
        '<path d="M14 2v4M8 10v4M16 18v4"/>'
    ),
    "copy": (
        '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>'
        '<path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>'
    ),
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "external-link": (
        '<path d="M15 3h6v6"/><path d="M10 14 21 3"/>'
        '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
    ),
    "message-square": ('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'),
    "shield-check": (
        '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01'
        "C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72"
        'a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>'
        '<path d="m9 12 2 2 4-4"/>'
    ),
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
}

CONTROL_SURFACE_ICONS = "".join(
    f'<template id="ui-icon-{name}"><svg class="ui-icon" viewBox="0 0 24 24" '
    f'aria-hidden="true" focusable="false">{shapes}</svg></template>'
    for name, shapes in _LUCIDE_ICONS.items()
)

CONTROL_SURFACE_SCRIPT = """
function uiIcon(name) {
  return document.getElementById('ui-icon-' + name).content.firstElementChild.cloneNode(true);
}
function decorateIcons(root = document) {
  root.querySelectorAll('[data-icon]:not([data-icon-ready])').forEach(node => {
  node.prepend(uiIcon(node.dataset.icon));
  node.classList.add('button-content');
  node.dataset.iconReady = 'true';
  });
}
"""

CONTROL_SURFACE_BASE_CSS = """
:root {
  --canvas: #f4f6f7;
  --surface: #ffffff;
  --surface-subtle: #f8fafb;
  --surface-strong: #edf2f3;
  --ink: #172126;
  --ink-soft: #35434a;
  --muted: #5f6f77;
  --line: #dce3e6;
  --line-strong: #bcc8cd;
  --primary: #0f766e;
  --primary-hover: #115e59;
  --primary-soft: #e7f4f1;
  --link: #0b64a0;
  --accent: #a16207;
  --accent-soft: #fff5df;
  --success: #16794a;
  --success-soft: #eaf8f0;
  --warning: #9a5b08;
  --warning-soft: #fff5df;
  --danger: #b42318;
  --danger-soft: #fef0ee;
  --focus: #0b64a0;
  --radius-sm: 4px;
  --radius-md: 6px;
  --shadow-sm: 0 1px 2px rgba(23, 33, 38, .06);
  --control-height: 40px;
  --command-width: 144px;
  --compact-command-width: 88px;
  --time-basis-width: 320px;
}
* { box-sizing: border-box; }
html { color-scheme: light; scroll-padding-top: 88px; }
body { margin: 0; background: var(--canvas); color: var(--ink);
  font-family: __WEB_FONT_STACK__; font-size: 14px; line-height: 1.5; letter-spacing: 0; }
button, input, select, textarea { font: inherit; letter-spacing: 0; }
button, a, input, select, textarea { -webkit-tap-highlight-color: transparent; }
button:focus-visible, input:focus-visible, select:focus-visible, a:focus-visible,
textarea:focus-visible, [tabindex="-1"]:focus-visible {
  outline: 2px solid var(--focus); outline-offset: 2px;
}
a { color: var(--link); }
[hidden] { display: none !important; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
.ui-icon { display: inline-block; width: 18px; height: 18px; flex: none;
  vertical-align: middle; fill: none; stroke: currentColor; stroke-width: 1.75;
  stroke-linecap: round; stroke-linejoin: round; }
.button-content { display: inline-flex; align-items: center; justify-content: center; gap: 8px; }
.icon-button { display: inline-grid; place-items: center; width: var(--control-height);
  padding: 0; flex: none; }
.page-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.page-intro > div { min-width: 0; }
.page-intro h1 { overflow-wrap: anywhere; }
.field-error { margin: 4px 0 0; color: var(--danger); font-size: 12px; }
[aria-invalid="true"] { border-color: var(--danger) !important; }
.number { font-variant-numeric: tabular-nums; }
.skip-link { position: fixed; z-index: 100; top: 8px; left: 8px; padding: 8px 12px;
  border-radius: var(--radius-sm); background: var(--ink); color: #fff;
  transform: translateY(-160%); text-decoration: none; }
.skip-link:focus { transform: translateY(0); }
.app-header { position: sticky; top: 0; z-index: 20;
  background: var(--surface); border-top: 3px solid var(--primary);
  border-bottom: 1px solid var(--line); }
.app-bar { width: 100%; max-width: 1280px; min-height: 64px; margin: 0 auto; padding: 0 24px;
  display: flex; align-items: center; gap: 24px; }
.brand-lockup { display: inline-flex; align-items: center; gap: 10px; color: var(--ink);
  text-decoration: none; white-space: nowrap; }
.brand-mark { display: grid; place-items: center; width: 32px; height: 32px;
  border-radius: var(--radius-sm); background: var(--ink); color: #fff;
  font-size: 11px; font-weight: 800; letter-spacing: 0; }
.brand-copy { display: flex; flex-direction: column; line-height: 1.15; }
.brand-name { font-size: 16px; font-weight: 800; }
.brand-area { margin-top: 3px; color: var(--muted); font-size: 10px;
  font-weight: 700; letter-spacing: 0; text-transform: uppercase; }
.primary-nav { align-self: stretch; display: flex; align-items: stretch; gap: 4px; }
.nav-link { position: relative; min-width: 72px; padding: 0 14px; display: inline-flex;
  align-items: center; justify-content: center; color: var(--muted); font-size: 13px;
  font-weight: 700; text-decoration: none; }
.nav-link:hover { color: var(--ink); background: var(--surface-subtle); }
.nav-link[aria-current="page"] { color: var(--primary); }
.nav-link[aria-current="page"]::after { content: ""; position: absolute; right: 12px;
  bottom: -1px; left: 12px; height: 2px; background: var(--primary); }
.shell-identity { min-width: 0; max-width: 260px; margin-left: auto; text-align: right; }
.shell-profile { display: block; color: var(--ink-soft); font-size: 11px; font-weight: 700;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.shell-user { display: block; margin-top: 2px; color: var(--muted); font-size: 11px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.page-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px;
  margin-bottom: 20px; padding-bottom: 14px; border-bottom: 1px solid var(--line-strong); }
.page-intro h1 { margin: 0; font-size: 24px; line-height: 1.2; letter-spacing: 0; }
.page-kicker { margin: 0 0 5px; color: var(--primary); font-size: 10px;
  font-weight: 800; letter-spacing: 0; text-transform: uppercase; }
.page-context { margin: 7px 0 0; color: var(--muted); font-size: 12px; }
.button, button { height: var(--control-height); min-height: var(--control-height);
  border: 1px solid transparent; border-radius: var(--radius-sm);
  padding: 0 13px; background: var(--primary); color: #fff;
  font-weight: 700; cursor: pointer; transition: background-color .16s ease,
  border-color .16s ease, color .16s ease; }
.button:hover, button:hover { background: var(--primary-hover); }
button[disabled] { cursor: not-allowed; opacity: .5; }
input:not([type="checkbox"]):not([type="radio"]), select {
  width: 100%; height: var(--control-height); min-height: var(--control-height);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm); background: var(--surface); color: var(--ink);
  padding: 7px 9px; }
input:hover, select:hover { border-color: #8fa0a8; }
.field-heading { min-height: 18px; display: flex; align-items: baseline;
  justify-content: space-between; gap: 8px; }
.field-requirement { flex: none; color: var(--muted); font-size: 10px;
  font-weight: 700; letter-spacing: 0; }
.field-requirement.required { color: var(--primary); }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition: none !important;
    animation: none !important; }
}
@media (max-width: 640px) {
  html { scroll-padding-top: 120px; }
  .app-bar { min-height: 58px; padding: 12px 14px 0; gap: 10px; flex-wrap: wrap; }
  .brand-area, .shell-profile { display: none; }
  .primary-nav { order: 3; width: 100%; min-height: 44px; margin: 0; }
  .nav-link { flex: 1; min-width: 0; padding: 0 10px; }
  .shell-identity { max-width: 44%; margin-left: auto; }
  .page-intro { align-items: flex-start; flex-direction: column; gap: 8px; }
}
"""
