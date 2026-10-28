"""Shared visual foundation for the Admin and Archive browser surfaces."""

DEFAULT_WEB_UI_LANGUAGE = "en"

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
html { color-scheme: light; scroll-padding-top: 16px; }
body { margin: 0; background: var(--canvas); color: var(--ink);
  font-family: __WEB_FONT_STACK__; font-size: 14px; line-height: 1.5; letter-spacing: 0; }
button, input, select { font: inherit; letter-spacing: 0; }
button, a, input, select { -webkit-tap-highlight-color: transparent; }
button:focus-visible, input:focus-visible, select:focus-visible, a:focus-visible {
  outline: 2px solid var(--focus); outline-offset: 2px;
}
a { color: var(--link); }
[hidden] { display: none !important; }
.skip-link { position: fixed; z-index: 100; top: 8px; left: 8px; padding: 8px 12px;
  border-radius: var(--radius-sm); background: var(--ink); color: #fff;
  transform: translateY(-160%); text-decoration: none; }
.skip-link:focus { transform: translateY(0); }
.app-header { background: var(--surface); border-top: 3px solid var(--primary);
  border-bottom: 1px solid var(--line); }
.app-bar { width: 100%; max-width: 1280px; min-height: 64px; margin: 0 auto; padding: 0 24px;
  display: flex; align-items: center; gap: 24px; }
.brand-lockup { display: inline-flex; align-items: center; gap: 10px; color: var(--ink);
  text-decoration: none; white-space: nowrap; }
.brand-mark { display: grid; place-items: center; width: 32px; height: 32px;
  border-radius: var(--radius-sm); background: var(--ink); color: #fff;
  font-size: 11px; font-weight: 800; letter-spacing: .06em; }
.brand-copy { display: flex; flex-direction: column; line-height: 1.15; }
.brand-name { font-size: 16px; font-weight: 800; }
.brand-area { margin-top: 3px; color: var(--muted); font-size: 10px;
  font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.primary-nav { align-self: stretch; display: flex; align-items: stretch; gap: 4px; }
.nav-link { position: relative; min-width: 72px; padding: 0 14px; display: inline-flex;
  align-items: center; justify-content: center; color: var(--muted); font-size: 13px;
  font-weight: 700; text-decoration: none; }
.nav-link:hover { color: var(--ink); background: var(--surface-subtle); }
.nav-link[aria-current="page"] { color: var(--primary); }
.nav-link[aria-current="page"]::after { content: ""; position: absolute; right: 12px;
  bottom: -1px; left: 12px; height: 2px; background: var(--primary); }
.shell-identity { min-width: 0; margin-left: auto; text-align: right; }
.shell-profile { display: block; color: var(--ink-soft); font-size: 11px; font-weight: 700;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.shell-user { display: block; margin-top: 2px; color: var(--muted); font-size: 11px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.page-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px;
  margin-bottom: 20px; padding-bottom: 14px; border-bottom: 1px solid var(--line-strong); }
.page-intro h1 { margin: 0; font-size: 24px; line-height: 1.2; letter-spacing: 0; }
.page-kicker { margin: 0 0 5px; color: var(--primary); font-size: 10px;
  font-weight: 800; letter-spacing: .11em; text-transform: uppercase; }
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
  font-weight: 700; letter-spacing: .02em; }
.field-requirement.required { color: var(--primary); }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; }
}
@media (max-width: 640px) {
  .app-bar { min-height: 58px; padding: 0 14px; gap: 10px; }
  .brand-area, .shell-profile { display: none; }
  .primary-nav { margin-left: auto; }
  .nav-link { min-width: 58px; padding: 0 8px; }
  .shell-identity { max-width: 90px; margin-left: 0; }
  .page-intro { align-items: flex-start; flex-direction: column; gap: 8px; }
}
"""
