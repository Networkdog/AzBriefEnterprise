"""Minimal HTML shell for browsing canonical analysis archives."""

import html
import json

from src.i18n import get_language
from src.i18n.labels import get_labels
from src.web_design import (
    CONTROL_SURFACE_BASE_CSS,
    CONTROL_SURFACE_FAVICON,
    CONTROL_SURFACE_ICONS,
    CONTROL_SURFACE_SCRIPT,
    DEFAULT_WEB_UI_LANGUAGE,
)
from src.web_fonts import WEB_FONT_FACE_CSS, WEB_FONT_STACK

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
    :root { --paper:var(--surface); --navy:var(--ink); --cyan:var(--primary);
      --gold:var(--accent); --red:var(--danger); --green:var(--success); }
    * { box-sizing:border-box; }
    body { margin:0; color:var(--ink); background-color:var(--canvas);
      font-family:__WEB_FONT_STACK__;
      letter-spacing:0; }
    button,input,select { font:inherit; letter-spacing:0; }
    button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible {
      outline:2px solid var(--focus); outline-offset:2px; }
    header { background:var(--surface); color:var(--ink); border-bottom:1px solid var(--line); }
    .bar { max-width:1240px; margin:auto; padding:18px 24px; display:flex;
      justify-content:space-between; align-items:flex-end; gap:20px; }
    .brand { font-family:Bahnschrift,Aptos,sans-serif; font-size:24px; font-weight:700; }
    .title { color:var(--muted); font-size:14px; margin-top:3px; }
    .identity { text-align:right; font-size:12px; color:var(--muted); line-height:1.5; }
    main { width:100%; max-width:1240px; margin:0 auto; padding:24px; }
    .filters { background:var(--surface); border:1px solid var(--line);
      border-radius:6px; padding:16px; display:grid;
      grid-template-columns:minmax(220px,2fr) repeat(4,minmax(130px,1fr)); gap:12px; }
    .field { display:flex; flex-direction:column; gap:5px; min-width:0; }
    .field label { color:var(--muted); font-size:12px; font-weight:700; }
    .field .field-requirement { color:var(--muted); font-size:10px; font-weight:700; }
    input,select { width:100%; height:var(--control-height); min-height:var(--control-height);
      border:1px solid #b9c3ce; border-radius:4px;
      background:#fff; color:var(--ink); padding:7px 9px; }
    .actions { grid-column:1/-1; display:flex; justify-content:flex-end; gap:8px; }
    .filter-toggle { display:none; }
    .command { height:var(--control-height); min-height:var(--control-height);
      border:1px solid var(--navy); border-radius:4px; padding:8px 14px;
      background:var(--navy); color:#fff; cursor:pointer; font-weight:700; white-space:nowrap; }
    .command.secondary { color:var(--navy); background:#fff; }
    .actions .command,.more { width:var(--command-width); }
    .result-head { display:flex; align-items:center; justify-content:space-between;
      gap:16px; margin:24px 0 8px; }
    h1,h2,h3 { font-family:__WEB_FONT_STACK__; letter-spacing:0; }
    h1 { font-size:22px; margin:0; } h2 { font-size:18px; margin:0; }
    .status { color:var(--muted); font-size:13px; min-height:20px; }
    .results { list-style:none; margin:0; padding:0; background:var(--paper);
      border-top:2px solid var(--navy); }
    .row { display:grid; grid-template-columns:minmax(260px,2fr) 150px 110px 110px;
      gap:12px; align-items:center; padding:15px 12px; border-bottom:1px solid var(--line); }
    .row:hover { background:#f7fafb; }
    .row-title { height:auto; min-height:0; border:0; padding:0; background:none;
      color:var(--navy); text-align:left; font-weight:700; white-space:normal;
      cursor:pointer; line-height:1.4; }
    .summary { color:var(--muted); font-size:13px; margin-top:5px; line-height:1.45; }
    .meta { color:var(--muted); font-size:12px; }
    .badge { display:inline-block; min-width:48px; padding:3px 7px; border-radius:3px;
      text-align:center; font-size:12px; font-weight:700; background:#e8edf1; color:#44515e; }
    .badge.high { background:#fbe7e7; color:var(--red); }
    .badge.medium { background:#fff2d8; color:var(--gold); }
    .badge.low { background:#e4f3eb; color:var(--green); }
    .more { display:block; margin:18px auto 0; }
    [hidden] { display:none !important; }
    .detail-head { border-bottom:2px solid var(--navy); padding-bottom:18px; margin-bottom:20px; }
    .back { border:0; background:none; color:var(--cyan); padding:4px 0; cursor:pointer;
      font-weight:700; margin-bottom:16px; }
    .detail-title { font-size:26px; line-height:1.25; margin:0 0 9px; }
    .detail-meta { display:flex; flex-wrap:wrap; gap:8px 18px; color:var(--muted); font-size:13px; }
    .detail-section { padding:18px 0; border-bottom:1px solid var(--line); }
    .detail-section h2 { margin-bottom:10px; }
    .prose { line-height:1.7; overflow-wrap:anywhere; }
    .markdown > :first-child { margin-top:0; }
    .markdown > :last-child { margin-bottom:0; }
    .markdown p { margin:0 0 10px; }
    .markdown h3,.markdown h4,.markdown h5,.markdown h6 { margin:16px 0 8px;
      color:var(--navy); font-size:16px; line-height:1.4; }
    .markdown ul,.markdown ol { margin:0 0 10px; padding-left:24px; }
    .markdown li { margin:4px 0; }
    .markdown blockquote { margin:0 0 10px; padding:10px 14px; border-left:3px solid var(--cyan);
      background:#edf6f7; color:#334a55; }
    .markdown pre { margin:0 0 10px; padding:10px 12px; background:#edf2f4;
      overflow:auto; white-space:pre; border-radius:4px; }
    .markdown pre code { display:inline; margin:0; padding:0; background:none; }
    .inline-code { display:inline; margin:0 2px; padding:2px 4px; background:#edf2f4;
      border-radius:3px; font-family:"Cascadia Mono",Consolas,monospace; font-size:.92em; }
    .markdown hr { border:0; border-top:1px solid var(--line); margin:16px 0; }
    .facts { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }
    .fact { border-left:3px solid var(--cyan); padding:8px 10px; background:#fff; }
    .fact dt { color:var(--muted); font-size:12px; font-weight:700; }
    .fact dd { margin:4px 0 0; line-height:1.5; overflow-wrap:anywhere; }
    .stack { list-style:none; padding:0; margin:0; }
    .stack li { background:#fff; border-left:3px solid #9babb8; padding:12px;
      margin:0 0 8px; line-height:1.55; overflow-wrap:anywhere; }
    code { display:block; margin-top:7px; padding:9px; background:#edf2f4; overflow:auto;
      font-family:"Cascadia Mono",Consolas,monospace; font-size:13px; }
    a { color:#0b6670; }
    @media (max-width:900px) {
      .filters { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .row { grid-template-columns:minmax(0,1fr) repeat(2,70px); }
      .row .source { display:none; } .facts { grid-template-columns:repeat(2,minmax(0,1fr)); }
    }
    @media (max-width:600px) {
      .bar,main { padding-left:14px; padding-right:14px; }
      .bar { align-items:flex-start; } .identity { max-width:45%; }
      .filters { grid-template-columns:1fr; } .actions { grid-column:1; }
      .advanced-filter { display:none; }
      .filters.expanded .advanced-filter { display:flex; }
      .filter-toggle { display:inline-block; }
      .row { grid-template-columns:repeat(2,1fr); gap:8px; }
      .row-main { grid-column:1/-1; } .row .source { display:block; grid-column:1/-1; }
      .facts { grid-template-columns:1fr; } .detail-title { font-size:22px; }
    }

    /* Shared operations workspace visual language. */
    :root { --paper:var(--surface); --navy:var(--ink); --cyan:var(--primary);
      --gold:var(--accent); --red:var(--danger); --green:var(--success);
      --line:#dce3e6; --muted:#5f6f77; }
    body { background:var(--surface); background-image:none; color:var(--ink); }
    .app-header { padding:0; border-top:3px solid var(--primary);
      border-bottom:1px solid var(--line); background:var(--surface); color:var(--ink); }
    main { width:100%; max-width:1280px; margin:0 auto; padding:32px 24px 64px; }
    .page-intro { margin-bottom:24px; padding-bottom:18px; }
    .page-intro h1 { font-size:36px; line-height:1.1; }
    main.detail-view { padding-top:24px; }
    main.detail-view .page-intro { display:none; }
    .filters { display:block; padding:16px 0; border:0; border-bottom:1px solid var(--line);
      border-radius:0; background:transparent; box-shadow:none; }
    .filter-main { display:grid; grid-template-columns:minmax(180px,2fr) minmax(160px,1fr) auto;
      align-items:end; gap:12px; }
    .field:first-child { grid-column:auto; }
    .filter-advanced { display:grid; grid-template-columns:repeat(4,minmax(0,1fr));
      gap:12px; margin-top:16px; padding-top:16px; border-top:1px solid var(--line); }
    .filters .advanced-filter { display:flex; }
    .filter-toggle { display:inline-flex; }
    .active-filters { display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }
    .active-filters:empty { display:none; }
    .filter-chip { display:inline-flex; align-items:center; gap:8px; height:auto; min-height:30px;
      max-width:100%; padding:4px 8px; background:var(--surface); border:1px solid var(--line-strong);
      color:var(--ink-soft); font-size:12px; font-weight:500; text-align:left; overflow-wrap:anywhere; }
    .filter-chip:hover { background:var(--primary-soft); color:var(--primary); }
    .filter-chip .ui-icon { width:14px; height:14px; }
    .field label { color:var(--ink-soft); font-size:11px; font-weight:800; }
    input,select { height:var(--control-height); min-height:var(--control-height);
      border:1px solid var(--line-strong);
      border-radius:var(--radius-sm); background:var(--surface); color:var(--ink); }
    .actions { grid-column:auto; padding-top:0; }
    .actions .command { width:auto; }
    .command { height:var(--control-height); min-height:var(--control-height);
      padding:7px 13px; border:1px solid transparent;
      border-radius:var(--radius-sm); background:var(--primary); color:#fff; }
    .command:hover { background:var(--primary-hover); }
    .command.secondary { border-color:var(--line-strong); background:var(--surface);
      color:var(--ink-soft); }
    .command.secondary:hover { border-color:#8fa0a8; background:var(--surface-subtle); color:var(--ink); }
    .result-head { margin:20px 0 8px; }
    .result-head h2 { color:var(--ink); font-size:16px; }
    .status { color:var(--muted); font-size:12px; }
    .results { overflow:hidden; border:1px solid var(--line); border-top:0;
      border-radius:0 0 var(--radius-sm) var(--radius-sm); background:var(--surface); box-shadow:none; }
    .list-columns { display:grid; grid-template-columns:minmax(260px,2fr) 150px 150px 90px 90px;
      gap:14px; padding:10px 16px; border:1px solid var(--line); background:var(--surface-strong);
      color:var(--muted); font-size:11px; font-weight:700; }
    .row { grid-template-columns:minmax(260px,2fr) 150px 150px 90px 90px;
      gap:14px; padding:14px 16px; border-bottom:1px solid var(--line); }
    .row:last-child { border-bottom:0; }
    .row:hover { background:var(--surface-subtle); }
    .row-title { display:inline; color:var(--ink); font-size:14px; line-height:1.4; text-decoration:none; }
    .row-title:hover { background:transparent; color:var(--primary); }
    .summary { color:var(--muted); font-size:12px; line-height:1.5; }
    .meta,.source { color:var(--muted); }
    .badge { min-width:54px; padding:4px 7px; border-radius:var(--radius-sm);
      background:var(--surface-strong); color:var(--ink-soft); }
    .row-date { color:var(--muted); font-size:12px; font-variant-numeric:tabular-nums; }
    .row-category { display:block; margin-top:6px; font-size:11px; color:var(--muted); }
    .metric { display:flex; align-items:center; gap:8px; }
    .metric-label { display:none; color:var(--muted); font-size:11px; }
    .badge.high { background:var(--danger-soft); color:var(--danger); }
    .badge.medium { background:var(--warning-soft); color:var(--warning); }
    .badge.low { background:var(--success-soft); color:var(--success); }
    .more { margin-top:14px; }
    #detail { width:100%; max-width:1120px; margin:0 auto; padding:8px 0;
      border:0; border-radius:0; background:transparent; box-shadow:none; }
    .detail-toolbar { display:flex; justify-content:space-between; align-items:center; gap:12px;
      flex-wrap:wrap; margin-bottom:28px; }
    .detail-toolbar .back { margin:0; display:inline-flex; align-items:center; gap:8px; }
    .detail-toolbar a.command { display:inline-flex; align-items:center; text-decoration:none; }
    .detail-layout { display:grid; grid-template-columns:192px minmax(0,1fr); gap:48px; }
    .detail-outline { display:grid; gap:0; position:sticky; top:90px; align-self:start;
      counter-reset:report-section; border-top:1px solid var(--line-strong); }
    .detail-outline a { position:relative; display:block; padding:10px 8px 10px 34px;
      border-bottom:1px solid var(--line); color:var(--muted); font-size:12px;
      line-height:1.4; text-decoration:none; counter-increment:report-section; }
    .detail-outline a::before { position:absolute; top:10px; left:0;
      content:counter(report-section, decimal-leading-zero); color:var(--primary);
      font-size:10px; font-weight:800; font-variant-numeric:tabular-nums; }
    .detail-outline a:hover { color:var(--primary); }
    #detail-body { min-width:0; }
    .archive-notice { display:flex; align-items:center; justify-content:space-between; gap:16px;
      padding:14px 16px; margin:16px 0; border-left:3px solid var(--danger);
      background:var(--danger-soft); color:var(--danger); }
    .list-state { display:flex; flex-direction:column; align-items:center; gap:14px;
      padding:40px 20px; border-bottom:1px solid var(--line); color:var(--muted); text-align:center; }
    .list-state > .ui-icon { width:28px; height:28px; }
    .list-state p { margin:0; }
    .loading-lines { width:100%; display:grid; gap:14px; }
    .loading-lines span { height:12px; width:74%; background:var(--line); border-radius:3px; }
    .loading-lines span:nth-child(2) { width:90%; }
    .loading-lines span:nth-child(3) { width:60%; }
    .copy-feedback { min-height:20px; font-size:12px; color:var(--muted); }
    .copy-feedback:empty { display:none; }
    .back { min-height:auto; margin:0 0 16px; padding:3px 0; border:0;
      background:transparent; color:var(--link); }
    .back:hover { background:transparent; color:var(--primary); }
    .detail-head { margin-bottom:0; padding-bottom:28px; border-bottom:2px solid var(--ink); }
    .detail-title { max-width:1040px; color:var(--ink); font-size:48px; font-weight:800;
      line-height:1.08; }
    .detail-title:focus-visible { outline:0; box-shadow:0 3px 0 var(--focus); }
    .detail-meta { margin-top:16px; color:var(--muted); }
    .detail-section { padding:30px 0; border-bottom:1px solid var(--line); }
    .detail-section:last-child { border-bottom:0; }
    .detail-section h2 { margin-bottom:16px; color:var(--ink); font-size:25px;
      line-height:1.2; }
    .prose { max-width:820px; color:var(--ink-soft); font-size:16px; line-height:1.75; }
    .markdown p { margin-bottom:16px; }
    .markdown ul,.markdown ol { margin-bottom:16px; }
    .markdown h3,.markdown h4,.markdown h5,.markdown h6 { color:var(--ink); }
    .markdown blockquote { max-width:820px; padding:16px 18px; border-left:4px solid var(--primary);
      background:var(--primary-soft); color:var(--ink-soft); }
    .markdown pre,code,.inline-code { background:#eef2f3; color:#24343b; }
    .facts { grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:0;
      border-top:1px solid var(--line-strong); border-bottom:1px solid var(--line); }
    .fact { padding:16px 20px 16px 0; border:0; border-radius:0; background:transparent; }
    .fact + .fact { padding-left:20px; border-left:1px solid var(--line); }
    .fact dt { color:var(--muted); font-size:11px; }
    .fact dd { margin-top:7px; color:var(--ink); font-size:17px; font-weight:700; }
    .stack { border-top:1px solid var(--line-strong); }
    .stack li { margin:0; padding:18px 0; border:0; border-bottom:1px solid var(--line);
      border-radius:0; background:transparent; }
    .stack li > strong { display:block; margin-bottom:5px; color:var(--ink); font-size:17px;
      line-height:1.45; }
    #detail-body > .detail-section:first-child { margin-top:28px; padding:24px 28px;
      border:0; border-left:4px solid var(--primary); background:var(--primary-soft); }
    #detail-body > .detail-section:first-child h2 { margin-bottom:8px; color:var(--primary);
      font-size:11px; }
    #detail-body > .detail-section:first-child .prose { max-width:none; color:var(--ink);
      font-size:25px; font-weight:700; line-height:1.45; }
    a { color:var(--link); }
    @media (max-width:960px) {
      .filter-main { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .actions { grid-column:1/-1; }
      .filter-advanced { grid-template-columns:repeat(3,minmax(0,1fr)); }
      .field:first-child { grid-column:auto; }
      .row,.list-columns { grid-template-columns:minmax(0,1fr) 140px repeat(2,78px); }
      .list-columns .service-column { display:none; }
      .row .source { display:none; }
    }
    @media (max-width:640px) {
      main { padding:20px 14px 40px; }
      .page-intro h1 { font-size:29px; }
      main.detail-view { padding-top:16px; }
      .filters { padding:12px 0; }
      .filter-main,.filter-advanced { grid-template-columns:1fr; }
      .field:first-child,.actions { grid-column:1; }
      .actions { display:grid; grid-template-columns:repeat(3,auto); }
      .actions .command { width:100%; min-width:0; padding-right:6px; padding-left:6px; }
      .list-columns { display:none; }
      .results { border-top:1px solid var(--line); }
      .row { grid-template-columns:repeat(2,minmax(0,1fr)); padding:13px 12px; }
      .row-main,.row-date { grid-column:1/-1; }
      .metric-label { display:inline; }
      .detail-layout { grid-template-columns:1fr; gap:8px; }
      .detail-outline { position:static; display:flex; flex-wrap:nowrap; gap:0;
        overflow-x:auto; border-top:0; border-bottom:1px solid var(--line); }
      .detail-outline a { flex:none; padding:8px 12px 8px 28px; border:0;
        border-right:1px solid var(--line); white-space:nowrap; }
      .detail-outline a::before { top:8px; left:8px; }
      .detail-toolbar .page-actions { flex-wrap:wrap; }
      .archive-notice { align-items:flex-start; flex-direction:column; }
      #detail { padding:8px 0; }
      .detail-toolbar { margin-bottom:20px; }
      .detail-head { padding-bottom:20px; }
      .detail-title { font-size:32px; line-height:1.12; }
      .detail-meta { margin-top:14px; }
      .detail-section { padding:24px 0; }
      .detail-section h2 { margin-bottom:14px; font-size:21px; }
      .prose { max-width:none; font-size:14px; line-height:1.7; }
      .fact { padding:14px 0; }
      .fact + .fact { padding-left:0; border-top:1px solid var(--line); border-left:0; }
      .detail-facts .facts { grid-template-columns:repeat(3,minmax(0,1fr)); }
      .detail-facts .fact { padding:12px 8px; }
      .detail-facts .fact + .fact { border-top:0; border-left:1px solid var(--line); }
      .detail-impact .facts { grid-template-columns:1fr; }
      .stack li { padding:16px 0; }
      #detail-body > .detail-section:first-child { margin-top:20px; padding:20px 18px; }
      #detail-body > .detail-section:first-child h2 { font-size:11px; }
      #detail-body > .detail-section:first-child .prose { font-size:21px; }
    }
  </style>
</head>
<body>
  __WEB_ICONS__
  <a class="skip-link" href="#main-content" data-i18n="skip_to_content"></a>
  <header class="app-header"><div class="app-bar">
    <a class="brand-lockup" href="/archive" aria-label="AzBrief Archive">
      <span class="brand-mark" aria-hidden="true">AZ</span>
      <span class="brand-copy"><span class="brand-name">AzBrief</span><span class="brand-area">Operations</span></span>
    </a>
    <nav class="primary-nav" aria-label="__NAV_LABEL__">
      __ADMIN_LINK__
      <a class="nav-link" href="/archive" aria-current="page">Archive</a>
      __FEEDBACK_LINK__
    </nav>
    <div class="shell-identity"><span class="shell-profile">__PROFILE__</span><span class="shell-user">__USER__</span></div>
  </div></header>
  <main id="main-content">
    <div class="page-intro">
      <div><p class="page-kicker" data-i18n="archive_kicker"></p><h1 data-i18n="archive_title"></h1></div>
    </div>
    <div id="archive-notice" class="archive-notice" role="alert" hidden><span id="notice-text"></span><button id="notice-retry" type="button" class="command secondary" data-i18n="archive_retry"></button></div>
    <section id="browser">
      <form id="filters" class="filters">
        <div class="filter-main">
        <div class="field"><label for="q" class="field-heading"><span data-i18n="archive_search"></span><span class="field-requirement" data-i18n="field_optional"></span></label><input id="q" name="q" type="search" maxlength="200"></div>
        <div class="field"><label for="service" class="field-heading"><span data-i18n="archive_service"></span><span class="field-requirement" data-i18n="field_optional"></span></label><input id="service" name="service" maxlength="200"></div>
        <div class="actions"><button id="filter-toggle" type="button" class="command secondary filter-toggle" aria-expanded="false" aria-controls="advanced-filters" data-icon="sliders-horizontal"><span data-i18n="archive_filters"></span></button><button type="reset" class="command secondary" data-i18n="archive_reset"></button><button type="submit" class="command" data-icon="search"><span data-i18n="archive_apply"></span></button></div>
        </div>
        <div id="advanced-filters" class="filter-advanced" hidden>
        <div class="field advanced-filter"><label for="category" class="field-heading"><span data-i18n="archive_category"></span><span class="field-requirement" data-i18n="field_optional"></span></label><select id="category" name="category"><option value="" data-i18n="archive_all"></option><option value="retirement" data-i18n="archive_category_retirement"></option><option value="feature_change" data-i18n="archive_category_feature_change"></option><option value="new_feature" data-i18n="archive_category_new_feature"></option><option value="new_service" data-i18n="archive_category_new_service"></option><option value="region_expansion" data-i18n="archive_category_region_expansion"></option><option value="preview" data-i18n="archive_category_preview"></option><option value="sdk_tooling" data-i18n="archive_category_sdk_tooling"></option><option value="pricing" data-i18n="archive_category_pricing"></option></select></div>
        <div class="field advanced-filter"><label for="importance" class="field-heading"><span data-i18n="col_importance"></span><span class="field-requirement" data-i18n="field_optional"></span></label><select id="importance" name="importance"><option value="" data-i18n="archive_all"></option><option value="high" data-i18n="level_high"></option><option value="medium" data-i18n="level_medium"></option><option value="low" data-i18n="level_low"></option></select></div>
        <div class="field advanced-filter"><label for="impact_level" class="field-heading"><span data-i18n="col_impact"></span><span class="field-requirement" data-i18n="field_optional"></span></label><select id="impact_level" name="impact_level"><option value="" data-i18n="archive_all"></option><option value="high" data-i18n="level_high"></option><option value="medium" data-i18n="level_medium"></option><option value="low" data-i18n="level_low"></option></select></div>
        <div class="field advanced-filter"><label for="relevance" class="field-heading"><span data-i18n="relevance"></span><span class="field-requirement" data-i18n="field_optional"></span></label><select id="relevance" name="relevance"><option value="" data-i18n="archive_all"></option><option value="relevant" data-i18n="relevance_relevant"></option><option value="opportunity" data-i18n="relevance_opportunity"></option><option value="not_relevant" data-i18n="relevance_not_relevant"></option><option value="unknown" data-i18n="relevance_unknown"></option></select></div>
        <div class="field advanced-filter"><label for="source" class="field-heading"><span data-i18n="archive_source"></span><span class="field-requirement" data-i18n="field_optional"></span></label><select id="source" name="source"><option value="" data-i18n="archive_all"></option><option value="scheduled_digest" data-i18n="archive_source_scheduled"></option><option value="admin_run" data-i18n="archive_source_admin"></option><option value="api_orchestrate" data-i18n="archive_source_orchestrate"></option><option value="api_analyze" data-i18n="archive_source_analyze"></option><option value="api_batch" data-i18n="archive_source_batch"></option><option value="mcp" data-i18n="archive_source_mcp"></option></select></div>
        <div class="field advanced-filter"><label for="from" class="field-heading"><span data-i18n="archive_from"></span><span class="field-requirement" data-i18n="field_optional"></span></label><input id="from" type="date"></div>
        <div class="field advanced-filter"><label for="to" class="field-heading"><span data-i18n="archive_to"></span><span class="field-requirement" data-i18n="field_optional"></span></label><input id="to" type="date"></div>
        </div>
        <div id="active-filters" class="active-filters" aria-label="__ACTIVE_FILTERS_LABEL__"></div>
      </form>
      <div class="result-head"><h2 data-i18n="archive_results"></h2><div id="status" class="status" role="status" aria-live="polite"></div></div>
      <div class="list-columns" aria-hidden="true"><span data-i18n="archive_update"></span><span data-i18n="archive_analyzed_at"></span><span class="service-column" data-i18n="archive_service"></span><span data-i18n="col_importance"></span><span data-i18n="col_impact"></span></div>
      <ol id="results" class="results"></ol>
      <div id="list-state" class="list-state" hidden></div>
      <button id="more" type="button" class="command more" data-i18n="archive_load_more" hidden></button>
    </section>
    <article id="detail" hidden>
      <div class="detail-toolbar"><button id="back" type="button" class="back" data-icon="arrow-left"><span data-i18n="archive_back"></span></button><div class="page-actions"><button id="copy-link" class="command secondary" type="button" data-icon="copy"><span data-i18n="archive_copy_link"></span></button>__DETAIL_FEEDBACK_LINK__</div></div>
      <div id="copy-status" class="copy-feedback" role="status"></div>
      <div class="detail-head"><h2 id="detail-title" class="detail-title"></h2><div id="detail-meta" class="detail-meta"></div></div>
      <div class="detail-layout"><nav id="detail-outline" class="detail-outline" aria-label="__OUTLINE_LABEL__"></nav><div id="detail-body"></div></div>
    </article>
  </main>
  <script nonce="__NONCE__">
  'use strict';
  __WEB_SCRIPT__
  const L = __LABELS__;
  const byId = id => document.getElementById(id);
  const filterIds = ['q','service','category','importance','impact_level','relevance','source','from','to'];
  const state = { cursor:'', loading:false, listRequest:0, detailRequest:0, listAbort:null,
    detailAbort:null, listKey:null, returnId:'', scroll:0 };
  function el(tag, className, value) { const node=document.createElement(tag); if(className) node.className=className; if(value!==undefined&&value!==null) node.textContent=String(value); return node; }
  function label(key) { return L[key] || key; }
  function localTime(value) { if(!value) return '-'; const date=new Date(value); return Number.isNaN(date.valueOf()) ? value : date.toLocaleString(document.documentElement.lang||undefined,{dateStyle:'medium',timeStyle:'short'}); }
  function badge(value) { return el('span','badge '+(value||''),value ? label('level_'+value) : '-'); }
  function levelText(value) { return value ? label('level_'+value) : '-'; }
  function relevanceText(value) { return value ? label('relevance_'+value) : '-'; }
  function sourceText(value) { const keys={scheduled_digest:'archive_source_scheduled',admin_run:'archive_source_admin',api_orchestrate:'archive_source_orchestrate',api_analyze:'archive_source_analyze',api_batch:'archive_source_batch',mcp:'archive_source_mcp'};return label(keys[value]||value); }
  function safeLink(raw, text) { try { const url=new URL(raw,location.origin); const host=url.hostname.toLowerCase(); const allowed=url.origin===location.origin || (url.protocol==='https:' && ['microsoft.com','azure.com','github.com','azureweekly.info'].some(domain=>host===domain||host.endsWith('.'+domain))); if(!allowed) return el('span','',text); const link=el('a','',text); link.href=url.href; if(url.origin!==location.origin){link.target='_blank';link.rel='noopener noreferrer';} return link; } catch (_) { return el('span','',text); } }
  function setStatus(key) { byId('status').textContent=key ? label(key) : ''; }
  function applyLabels() { document.querySelectorAll('[data-i18n]').forEach(node=>{node.textContent=label(node.dataset.i18n);}); byId('q').placeholder=label('archive_search_placeholder'); byId('service').placeholder=label('archive_service_placeholder'); ['from','to'].forEach(id=>{byId(id).placeholder=label('archive_date_placeholder');byId(id).title=label('archive_date_placeholder');}); }
  function appendMeta(parent,key,value){const span=el('span','');span.append(el('strong','',label(key)+': '),document.createTextNode(value||'-'));parent.append(span);}
  function buildParams(reset){const params=new URLSearchParams();['q','service','category','importance','impact_level','relevance','source'].forEach(id=>{const value=byId(id).value.trim();if(value)params.set(id,value);});if(byId('from').value)params.set('analyzed_after',byId('from').value+'T00:00:00Z');if(byId('to').value)params.set('analyzed_before',byId('to').value+'T23:59:59.999999Z');params.set('limit','25');if(!reset&&state.cursor)params.set('cursor',state.cursor);return params;}
  function viewParams() {
    const params = new URLSearchParams();
    filterIds.forEach(id => { if (byId(id).value.trim()) params.set(id, byId(id).value.trim()); });
    const language = new URLSearchParams(location.search).get('lang');
    if (language) params.set('lang', language);
    return params;
  }
  function browserUrl() { const query = viewParams().toString(); return '/archive' + (query ? '?' + query : ''); }
  function detailUrl(id) { const query = viewParams().toString(); return '/archive/' + encodeURIComponent(id) + (query ? '?' + query : ''); }
  function restoreFilters() {
    const params = new URLSearchParams(location.search);
    filterIds.forEach(id => { byId(id).value = params.get(id) || ''; });
    const expanded = filterIds.slice(2).some(id => byId(id).value);
    byId('advanced-filters').hidden = !expanded;
    byId('filter-toggle').setAttribute('aria-expanded', String(expanded));
    renderFilterChips();
  }
  function renderFilterChips() {
    const container = byId('active-filters'); container.replaceChildren();
    filterIds.forEach(id => {
      const field = byId(id); if (!field.value.trim()) return;
      const name = document.querySelector('label[for="' + id + '"] > span').textContent;
      const value = field.tagName === 'SELECT' ? field.selectedOptions[0].textContent : field.value.trim();
      const button = el('button', 'filter-chip', name + ': ' + value); button.type = 'button';
      button.append(uiIcon('x')); button.title = label('archive_remove_filter') + ': ' + name;
      button.setAttribute('aria-label', button.title);
      button.addEventListener('click', () => { field.value = ''; submitFilters(); }); container.append(button);
    });
  }
  function notice(error, retry) {
    const key = error.status === 401 || error.status === 403 ? 'archive_access_error'
      : error.status === 404 ? 'archive_not_found' : 'archive_error';
    byId('notice-text').textContent = label(key); byId('archive-notice').hidden = false;
    byId('notice-retry').onclick = retry;
  }
  async function api(path, signal) {
    const response = await fetch(path, {signal, credentials:'same-origin', headers:{'Accept':'application/json'}});
    if (!response.ok) { const error = new Error(String(response.status)); error.status = response.status; throw error; }
    return response.json();
  }
  function renderRow(item) {
    const row = el('li','row'); const main = el('div','row-main');
    const link = el('a','row-title',item.title); link.href = detailUrl(item.archive_id);
    link.dataset.archiveId = item.archive_id;
    link.addEventListener('click', event => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); openDetail(item.archive_id, true);
    });
    main.append(link, el('div','summary',item.one_line_summary),
      el('span','row-category',label('archive_category_' + item.update_category)));
    const date = el('time','row-date',localTime(item.analyzed_at)); date.dateTime = item.analyzed_at;
    row.append(main, date, el('div','source',(item.azure_services || []).join(', ') || '-'));
    [['col_importance',item.importance],['col_impact',item.impact_level]].forEach(([key,value]) => {
      const metric = el('div','metric'); metric.append(el('span','metric-label',label(key)),badge(value));
      metric.setAttribute('aria-label',label(key) + ': ' + levelText(value)); row.append(metric);
    });
    return row;
  }
  function showListState(key, loading = false) {
    const container = byId('list-state'); container.replaceChildren(); container.hidden = !key;
    if (!key) return;
    if (loading) {
      const lines = el('div','loading-lines'); lines.setAttribute('aria-hidden','true');
      for (let index = 0; index < 3; index += 1) lines.append(el('span',''));
      container.append(lines);
    } else {
      container.append(uiIcon('search'),el('p','',label(key)));
      if (filterIds.some(id => byId(id).value)) {
        const reset = el('button','command secondary',label('archive_reset')); reset.type = 'button';
        reset.addEventListener('click', () => byId('filters').reset()); container.append(reset);
      }
    }
  }
  async function load(reset) {
    if (state.loading && !reset) return;
    state.listAbort?.abort(); const controller = new AbortController(); state.listAbort = controller;
    const request = ++state.listRequest; state.loading = true;
    byId('archive-notice').hidden = true; setStatus('archive_loading');
    byId('results').setAttribute('aria-busy','true'); byId('more').disabled = true;
    if (reset) { state.cursor = ''; state.listKey = null; byId('results').replaceChildren();
      byId('more').hidden = true; showListState('archive_loading', true); }
    try {
      const page = await api('/api/archive/analyses?' + buildParams(reset), controller.signal);
      if (request !== state.listRequest) return;
      page.items.forEach(item => byId('results').append(renderRow(item)));
      state.cursor = page.next_cursor || ''; state.listKey = browserUrl();
      byId('more').hidden = !page.has_more;
      const count = byId('results').children.length;
      byId('status').textContent = label('archive_loaded_count').replace('{count}',String(count));
      showListState(count ? '' : page.has_more ? 'archive_scan_more' : 'archive_empty');
    } catch (error) {
      if (error.name === 'AbortError' || request !== state.listRequest) return;
      showListState(''); setStatus(''); notice(error, () => load(reset));
    } finally {
      if (request === state.listRequest) { state.loading = false; byId('more').disabled = false;
        byId('results').setAttribute('aria-busy','false'); }
    }
  }
  function submitFilters() {
    byId('to').setCustomValidity('');
    if (byId('from').value && byId('to').value && byId('from').value > byId('to').value) {
      byId('to').setCustomValidity(label('archive_date_error')); byId('to').reportValidity(); return;
    }
    history.pushState({},'',browserUrl()); state.returnId = ''; state.scroll = 0;
    renderFilterChips(); load(true);
  }
  function appendInlineMarkdown(parent,raw){const value=String(raw||'');const pattern=/(\*\*([^*]+)\*\*|`([^`\n]+)`|\[([^\]]+)\]\(([^)\s]+)\))/g;let cursor=0;let match;while((match=pattern.exec(value))!==null){if(match.index>cursor)parent.append(document.createTextNode(value.slice(cursor,match.index)));if(match[2]!==undefined)parent.append(el('strong','',match[2]));else if(match[3]!==undefined)parent.append(el('code','inline-code',match[3]));else parent.append(safeLink(match[5],match[4]));cursor=pattern.lastIndex;}if(cursor<value.length)parent.append(document.createTextNode(value.slice(cursor)));}
  function renderMarkdown(raw){const root=el('div','prose markdown');const lines=String(raw||'').replace(/\r\n?/g,'\n').split('\n');let paragraph=[];let list=null;let listTag='';let quote=[];let fenced=false;let codeLines=[];function flushParagraph(){if(!paragraph.length)return;const node=el('p','');appendInlineMarkdown(node,paragraph.join(' '));root.append(node);paragraph=[];}function flushList(){if(list)root.append(list);list=null;listTag='';}function flushQuote(){if(!quote.length)return;const node=el('blockquote','');appendInlineMarkdown(node,quote.join('\n'));root.append(node);quote=[];}function flushAll(){flushParagraph();flushList();flushQuote();}lines.forEach(line=>{if(fenced){if(/^\s*```/.test(line)){const pre=el('pre','');pre.append(el('code','',codeLines.join('\n')));root.append(pre);fenced=false;codeLines=[];}else codeLines.push(line);return;}if(/^\s*```/.test(line)){flushAll();fenced=true;return;}if(!line.trim()){flushAll();return;}const heading=line.match(/^(#{1,4})\s+(.+)$/);if(heading){flushAll();const node=el('h'+String(Math.min(6,heading[1].length+2)),'');appendInlineMarkdown(node,heading[2]);root.append(node);return;}if(/^\s*(---+|___+|\*\*\*+)\s*$/.test(line)){flushAll();root.append(el('hr',''));return;}const quoted=line.match(/^\s*>\s?(.*)$/);if(quoted){flushParagraph();flushList();quote.push(quoted[1]);return;}const unordered=line.match(/^\s*[-+*]\s+(.+)$/);const ordered=line.match(/^\s*\d+[.)]\s+(.+)$/);if(unordered||ordered){flushParagraph();flushQuote();const tag=ordered?'ol':'ul';if(list&&listTag!==tag)flushList();if(!list){list=el(tag,'');listTag=tag;}const item=el('li','');appendInlineMarkdown(item,(ordered||unordered)[1]);list.append(item);return;}flushList();flushQuote();paragraph.push(line.trim());});if(fenced){const pre=el('pre','');pre.append(el('code','',codeLines.join('\n')));root.append(pre);}flushAll();return root;}
  function section(title,value){if(value===undefined||value===null||value===''||(Array.isArray(value)&&!value.length))return;const block=el('section','detail-section');block.append(el('h2','',title));if(Array.isArray(value)){const list=el('ul','stack');value.forEach(item=>{const li=el('li','');if(typeof item==='string')appendInlineMarkdown(li,item);else li.textContent=JSON.stringify(item,null,2);list.append(li);});block.append(list);}else{block.append(renderMarkdown(value));}byId('detail-body').append(block);}
  function renderFacts(result){const block=el('section','detail-section detail-facts');block.append(el('h2','',label('quick_decision')));const facts=el('dl','facts');[['col_importance',levelText(result.importance)],['col_impact',levelText(result.impact_level)],['relevance',relevanceText(result.relevance)]].forEach(([key,value])=>{const fact=el('div','fact');fact.append(el('dt','',label(key)),el('dd','',value));facts.append(fact);});block.append(facts);byId('detail-body').append(block);}
  function renderImpact(result){const title=label(result.update_category&&['new_feature','new_service','region_expansion','preview','sdk_tooling'].includes(result.update_category)?'opportunity_analysis':'impact_analysis');if(!result.impact_details){section(title,result.impact_summary);return;}const block=el('section','detail-section detail-impact');block.append(el('h2','',title));const facts=el('dl','facts');[['cost_impact','cost'],['security_impact','security'],['performance_impact','performance'],['operational_impact','operational']].forEach(([field,key])=>{if(!result.impact_details[field])return;const fact=el('div','fact');fact.append(el('dt','',label(key)),el('dd','',result.impact_details[field]));facts.append(fact);});block.append(facts);byId('detail-body').append(block);}
  function renderResources(resources){if(!resources||!resources.length)return;const block=el('section','detail-section');block.append(el('h2','',label('affected_resources')));const list=el('ul','stack');resources.forEach(resource=>{const li=el('li','');li.append(el('strong','',resource.name||label('unknown_scope')));const scope=[resource.type,resource.resourceGroup,resource.subscription||resource.subscriptionId].filter(Boolean).join(' · ');if(scope)li.append(el('div','meta',scope));if(resource.reason)li.append(el('div','prose',resource.reason));list.append(li);});block.append(list);byId('detail-body').append(block);}
  function renderActions(actions){if(!actions||!actions.length)return;const names={urgency:'urgency',target_resources:'target',procedure:'procedure',deadline:'deadline',risk_if_not_done:'risk_if_not_done',precaution:'precaution',rollback:'rollback',verification_status:'verification'};const block=el('section','detail-section');block.append(el('h2','',label('action_items')));const list=el('ol','stack');actions.forEach(action=>{const li=el('li','');li.append(el('strong','',action.task||''));Object.keys(names).forEach(key=>{let value=action[key];if(value&&(!Array.isArray(value)||value.length)){if(key==='verification_status')value=label('verify_'+value);if(key==='urgency')value=levelText(value);li.append(el('div','meta',label(names[key])+': '+(Array.isArray(value)?value.join(', '):value)));}});if(action.cli_command)li.append(el('code','',action.cli_command));list.append(li);});block.append(list);byId('detail-body').append(block);}
  function renderReferences(docs){if(!docs||!docs.length)return;const block=el('section','detail-section');block.append(el('h2','',label('reference_docs')));const list=el('ul','stack');docs.forEach(doc=>{const li=el('li','');li.append(safeLink(doc.url||'',doc.title||doc.url||'-'));list.append(li);});block.append(list);byId('detail-body').append(block);}
  function renderDetail(documentData){const update=documentData.update,result=documentData.result;byId('detail-title').textContent=update.title;byId('detail-meta').replaceChildren();appendMeta(byId('detail-meta'),'archive_analyzed_at',localTime(documentData.analyzed_at));appendMeta(byId('detail-meta'),'published_date',localTime(update.published_date));appendMeta(byId('detail-meta'),'archive_source',sourceText(documentData.source));byId('detail-meta').append(safeLink(update.link,label('archive_original_update')));byId('detail-body').replaceChildren();section(label('importance_section'),result.one_line_summary);renderFacts(result);section(label('analysis_summary'),result.relevance_reason);section(label('relevance_evidence'),result.relevance_evidence);renderImpact(result);renderResources(result.affected_resources);renderActions(result.action_items);renderReferences(result.reference_docs);section(label('additional_checks'),result.additional_checks);}
  function buildOutline() {
    byId('detail-outline').replaceChildren();
    byId('detail-body').querySelectorAll('.detail-section').forEach((section, index) => {
      section.id = 'report-section-' + index;
      const link = el('a','',section.querySelector('h2').textContent); link.href = '#' + section.id;
      link.addEventListener('click', event => {
        if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault(); history.replaceState(history.state, '', link.href);
        section.scrollIntoView({block:'start'});
      });
      byId('detail-outline').append(link);
    });
  }
  async function openDetail(id, push) {
    state.detailAbort?.abort(); const controller = new AbortController(); state.detailAbort = controller;
    const request = ++state.detailRequest;
    if (push) { state.scroll = window.scrollY; state.returnId = id;
      history.pushState({fromList:true},'',detailUrl(id)); }
    byId('main-content').classList.add('detail-view');
    byId('archive-notice').hidden = true; byId('browser').hidden = true; byId('detail').hidden = false;
    byId('detail').setAttribute('aria-busy','true'); byId('copy-link').disabled = true;
    byId('copy-status').textContent = ''; byId('detail-title').textContent = label('archive_loading');
    byId('detail-meta').replaceChildren(); byId('detail-body').replaceChildren(); byId('detail-outline').replaceChildren();
    const feedback = byId('detail-feedback'); if (feedback) feedback.hidden = true;
    try {
      const data = await api('/api/archive/analyses/' + encodeURIComponent(id), controller.signal);
      if (request !== state.detailRequest) return;
      renderDetail(data); buildOutline();
      if (feedback) { const params = new URLSearchParams({lang:document.documentElement.lang, report:'archive:' + id});
        feedback.href = '/feedback?' + params; feedback.hidden = false; }
      byId('copy-link').disabled = false;
      byId('detail-title').tabIndex = -1; byId('detail-title').focus({preventScroll:true});
      const section = /^#report-section-\d+$/.test(location.hash) ? byId(location.hash.slice(1)) : null;
      if (section) section.scrollIntoView({block:'start'}); else window.scrollTo(0,0);
    } catch (error) {
      if (error.name === 'AbortError' || request !== state.detailRequest) return;
      byId('detail-title').textContent = label('archive_not_found'); notice(error, () => openDetail(id,false));
    } finally { if (request === state.detailRequest) byId('detail').setAttribute('aria-busy','false'); }
  }
  async function showBrowser(push) {
    if (push && history.state?.fromList) { history.back(); return; }
    if (push) history.replaceState({},'',browserUrl());
    state.detailAbort?.abort(); state.detailRequest += 1;
    byId('main-content').classList.remove('detail-view');
    byId('detail').hidden = true; byId('browser').hidden = false; byId('archive-notice').hidden = true;
    restoreFilters();
    if (state.listKey !== browserUrl()) await load(true);
    requestAnimationFrame(() => {
      window.scrollTo(0,state.scroll);
      const link = Array.from(document.querySelectorAll('[data-archive-id]')).find(node => node.dataset.archiveId === state.returnId);
      if (link) link.focus({preventScroll:true});
    });
  }
  byId('filters').addEventListener('submit',event=>{event.preventDefault();submitFilters();});
  ['from','to'].forEach(id => byId(id).addEventListener('input', () => byId('to').setCustomValidity('')));
  byId('filters').addEventListener('reset',()=>setTimeout(()=>{
    byId('to').setCustomValidity(''); byId('advanced-filters').hidden = true;
    byId('filter-toggle').setAttribute('aria-expanded','false'); submitFilters();
  },0));
  byId('filter-toggle').addEventListener('click',()=>{
    const expanded = byId('advanced-filters').hidden; byId('advanced-filters').hidden = !expanded;
    byId('filter-toggle').setAttribute('aria-expanded',String(expanded));
  });
  byId('more').addEventListener('click',()=>load(false));
  byId('back').addEventListener('click',()=>showBrowser(true));
  byId('copy-link').addEventListener('click',async()=>{
    try { await navigator.clipboard.writeText(location.origin + location.pathname);
      byId('copy-status').textContent = label('archive_copied'); }
    catch (_) { byId('copy-status').textContent = label('archive_copy_error'); }
  });
  window.addEventListener('popstate',()=>{
    restoreFilters(); const id=location.pathname.split('/').filter(Boolean)[1];
    if(id)openDetail(id,false);else showBrowser(false);
  });
  applyLabels(); decorateIcons(); restoreFilters();
  const initialId=location.pathname.split('/').filter(Boolean)[1];
  if(initialId)openDetail(initialId,false);else load(true);
  </script>
</body>
</html>"""


def render_archive_page(
    nonce: str,
    profile: str,
    user: str,
    language: str = DEFAULT_WEB_UI_LANGUAGE,
    admin_enabled: bool = False,
    feedback_enabled: bool = False,
) -> str:
    """Render the archive shell with escaped identity and a nonce-bound script."""
    labels = dict(get_labels(language))
    labels.pop("col_job_relevance", None)
    labels_json = json.dumps(labels, ensure_ascii=False, separators=(",", ":"))
    labels_json = (
        labels_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    admin_link = '<a class="nav-link" href="/admin">Admin</a>' if admin_enabled else ""
    feedback_link = '<a class="nav-link" href="/feedback">Feedback</a>' if feedback_enabled else ""
    detail_feedback_link = (
        '<a id="detail-feedback" class="command secondary" data-icon="message-square" '
        'href="/feedback" hidden><span data-i18n="archive_feedback"></span></a>'
        if feedback_enabled
        else ""
    )
    return (
        _PAGE.replace("__NONCE__", html.escape(nonce, quote=True))
        .replace("__WEB_FONT_FACE__", WEB_FONT_FACE_CSS)
        .replace("__CONTROL_SURFACE_BASE__", CONTROL_SURFACE_BASE_CSS)
        .replace("__WEB_ICONS__", CONTROL_SURFACE_ICONS)
        .replace("__WEB_FAVICON__", CONTROL_SURFACE_FAVICON)
        .replace("__WEB_SCRIPT__", CONTROL_SURFACE_SCRIPT)
        .replace("__WEB_FONT_STACK__", WEB_FONT_STACK)
        .replace("__LANG__", html.escape(get_language(language).lang_attr, quote=True))
        .replace("__TITLE__", html.escape(labels["archive_title"]))
        .replace("__NAV_LABEL__", html.escape(labels["primary_navigation"], quote=True))
        .replace("__ADMIN_LINK__", admin_link)
        .replace("__FEEDBACK_LINK__", feedback_link)
        .replace("__DETAIL_FEEDBACK_LINK__", detail_feedback_link)
        .replace(
            "__ACTIVE_FILTERS_LABEL__", html.escape(labels["archive_active_filters"], quote=True)
        )
        .replace("__OUTLINE_LABEL__", html.escape(labels["archive_outline"], quote=True))
        .replace("__PROFILE__", html.escape(profile))
        .replace("__USER__", html.escape(user))
        .replace("__LABELS__", labels_json)
    )
