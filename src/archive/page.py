"""Dependency-free HTML shell for browsing canonical analysis archives."""

import html
import json

from src.i18n import get_language
from src.i18n.labels import get_labels

_PAGE = r"""<!doctype html>
<html lang="__LANG__">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AzBrief · __TITLE__</title>
  <style nonce="__NONCE__">
    :root { --ink:#17202a; --muted:#627080; --line:#d9e0e7; --paper:#fff;
      --canvas:#f4f7f8; --navy:#17324d; --cyan:#0f7c86; --gold:#9b6a13;
      --red:#a53a3a; --green:#26734d; }
    * { box-sizing:border-box; }
    body { margin:0; color:var(--ink); background-color:var(--canvas);
      background-image:linear-gradient(rgba(23,50,77,.035) 1px,transparent 1px),
      linear-gradient(90deg,rgba(23,50,77,.035) 1px,transparent 1px);
      background-size:24px 24px; font-family:Aptos,"Segoe UI","Malgun Gothic",sans-serif;
      letter-spacing:0; }
    button,input,select { font:inherit; letter-spacing:0; }
    button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible {
      outline:3px solid #58b5bd; outline-offset:2px; }
    header { background:var(--navy); color:#fff; border-bottom:4px solid var(--cyan); }
    .bar { max-width:1240px; margin:auto; padding:18px 24px; display:flex;
      justify-content:space-between; align-items:flex-end; gap:20px; }
    .brand { font-family:Bahnschrift,Aptos,sans-serif; font-size:24px; font-weight:700; }
    .title { color:#bfe7ea; font-size:14px; margin-top:3px; }
    .identity { text-align:right; font-size:12px; color:#d7e3ec; line-height:1.5; }
    main { max-width:1240px; margin:auto; padding:24px; }
    .filters { background:rgba(255,255,255,.94); border:1px solid var(--line);
      border-radius:6px; padding:16px; display:grid;
      grid-template-columns:minmax(220px,2fr) repeat(4,minmax(130px,1fr)); gap:12px; }
    .field { display:flex; flex-direction:column; gap:5px; min-width:0; }
    .field label { color:var(--muted); font-size:12px; font-weight:700; }
    input,select { width:100%; min-height:38px; border:1px solid #b9c3ce; border-radius:4px;
      background:#fff; color:var(--ink); padding:7px 9px; }
    .actions { grid-column:1/-1; display:flex; justify-content:flex-end; gap:8px; }
    .filter-toggle { display:none; }
    .command { border:1px solid var(--navy); border-radius:4px; padding:8px 14px;
      background:var(--navy); color:#fff; cursor:pointer; font-weight:700; }
    .command.secondary { color:var(--navy); background:#fff; }
    .result-head { display:flex; align-items:center; justify-content:space-between;
      gap:16px; margin:24px 0 8px; }
    h1,h2,h3 { font-family:Bahnschrift,Aptos,sans-serif; letter-spacing:0; }
    h1 { font-size:22px; margin:0; } h2 { font-size:18px; margin:0; }
    .status { color:var(--muted); font-size:13px; min-height:20px; }
    .results { list-style:none; margin:0; padding:0; background:var(--paper);
      border-top:2px solid var(--navy); }
    .row { display:grid; grid-template-columns:minmax(260px,2fr) 150px 110px 110px;
      gap:12px; align-items:center; padding:15px 12px; border-bottom:1px solid var(--line); }
    .row:hover { background:#f7fafb; }
    .row-title { border:0; padding:0; background:none; color:var(--navy); text-align:left;
      font-weight:700; cursor:pointer; line-height:1.4; }
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
    .prose { white-space:pre-wrap; line-height:1.7; overflow-wrap:anywhere; }
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
  </style>
</head>
<body>
  <header><div class="bar">
    <div><div class="brand">AzBrief</div><div class="title" data-i18n="archive_title"></div></div>
    <div class="identity"><div>__PROFILE__</div><div>__USER__</div></div>
  </div></header>
  <main>
    <section id="browser">
      <form id="filters" class="filters">
        <div class="field"><label for="q" data-i18n="archive_search"></label><input id="q" name="q" type="search" maxlength="200"></div>
        <div class="field"><label for="service" data-i18n="archive_service"></label><input id="service" name="service" maxlength="200"></div>
        <div class="field advanced-filter"><label for="category" data-i18n="archive_category"></label><select id="category" name="category"><option value="" data-i18n="archive_all"></option><option value="retirement" data-i18n="archive_category_retirement"></option><option value="feature_change" data-i18n="archive_category_feature_change"></option><option value="new_feature" data-i18n="archive_category_new_feature"></option><option value="new_service" data-i18n="archive_category_new_service"></option><option value="region_expansion" data-i18n="archive_category_region_expansion"></option><option value="preview" data-i18n="archive_category_preview"></option><option value="sdk_tooling" data-i18n="archive_category_sdk_tooling"></option><option value="pricing" data-i18n="archive_category_pricing"></option></select></div>
        <div class="field advanced-filter"><label for="importance" data-i18n="col_importance"></label><select id="importance" name="importance"><option value="" data-i18n="archive_all"></option><option value="high" data-i18n="level_high"></option><option value="medium" data-i18n="level_medium"></option><option value="low" data-i18n="level_low"></option></select></div>
        <div class="field advanced-filter"><label for="impact_level" data-i18n="col_impact"></label><select id="impact_level" name="impact_level"><option value="" data-i18n="archive_all"></option><option value="high" data-i18n="level_high"></option><option value="medium" data-i18n="level_medium"></option><option value="low" data-i18n="level_low"></option></select></div>
        <div class="field advanced-filter"><label for="relevance" data-i18n="relevance"></label><select id="relevance" name="relevance"><option value="" data-i18n="archive_all"></option><option value="relevant" data-i18n="relevance_relevant"></option><option value="opportunity" data-i18n="relevance_opportunity"></option><option value="not_relevant" data-i18n="relevance_not_relevant"></option><option value="unknown" data-i18n="relevance_unknown"></option></select></div>
        <div class="field advanced-filter"><label for="source" data-i18n="archive_source"></label><select id="source" name="source"><option value="" data-i18n="archive_all"></option><option value="scheduled_digest" data-i18n="archive_source_scheduled"></option><option value="admin_run" data-i18n="archive_source_admin"></option><option value="api_orchestrate" data-i18n="archive_source_orchestrate"></option><option value="api_analyze" data-i18n="archive_source_analyze"></option><option value="api_batch" data-i18n="archive_source_batch"></option><option value="mcp" data-i18n="archive_source_mcp"></option></select></div>
        <div class="field advanced-filter"><label for="from" data-i18n="archive_from"></label><input id="from" type="date"></div>
        <div class="field advanced-filter"><label for="to" data-i18n="archive_to"></label><input id="to" type="date"></div>
        <div class="actions"><button id="filter-toggle" type="button" class="command secondary filter-toggle" data-i18n="archive_filters" aria-expanded="false"></button><button type="reset" class="command secondary" data-i18n="archive_reset"></button><button type="submit" class="command" data-i18n="archive_apply"></button></div>
      </form>
      <div class="result-head"><h1 data-i18n="archive_results"></h1><div id="status" class="status" role="status" aria-live="polite"></div></div>
      <ol id="results" class="results"></ol>
      <button id="more" type="button" class="command more" data-i18n="archive_load_more" hidden></button>
    </section>
    <article id="detail" hidden>
      <button id="back" type="button" class="back" data-i18n="archive_back"></button>
      <div class="detail-head"><h1 id="detail-title" class="detail-title"></h1><div id="detail-meta" class="detail-meta"></div></div>
      <div id="detail-body"></div>
    </article>
  </main>
  <script nonce="__NONCE__">
  'use strict';
  const L = __LABELS__;
  const byId = id => document.getElementById(id);
  const state = { cursor:'', loading:false };
  function el(tag, className, value) { const node=document.createElement(tag); if(className) node.className=className; if(value!==undefined&&value!==null) node.textContent=String(value); return node; }
  function label(key) { return L[key] || key; }
  function localTime(value) { if(!value) return '-'; const date=new Date(value); return Number.isNaN(date.valueOf()) ? value : date.toLocaleString(); }
  function badge(value) { return el('span','badge '+(value||''),label('level_'+value)); }
  function levelText(value) { return value ? label('level_'+value) : '-'; }
  function relevanceText(value) { return value ? label('relevance_'+value) : '-'; }
  function sourceText(value) { const keys={scheduled_digest:'archive_source_scheduled',admin_run:'archive_source_admin',api_orchestrate:'archive_source_orchestrate',api_analyze:'archive_source_analyze',api_batch:'archive_source_batch',mcp:'archive_source_mcp'};return label(keys[value]||value); }
  function safeLink(raw, text) { try { const url=new URL(raw,location.origin); const host=url.hostname.toLowerCase(); const allowed=url.origin===location.origin || (url.protocol==='https:' && ['microsoft.com','azure.com','github.com','azureweekly.info'].some(domain=>host===domain||host.endsWith('.'+domain))); if(!allowed) return el('span','',text); const link=el('a','',text); link.href=url.href; if(url.origin!==location.origin){link.target='_blank';link.rel='noopener noreferrer';} return link; } catch (_) { return el('span','',text); } }
  function setStatus(key) { byId('status').textContent=key ? label(key) : ''; }
  function applyLabels() { document.querySelectorAll('[data-i18n]').forEach(node=>{node.textContent=label(node.dataset.i18n);}); byId('q').placeholder=label('archive_search_placeholder'); }
  function appendMeta(parent,key,value){const span=el('span','');span.append(el('strong','',label(key)+': '),document.createTextNode(value||'-'));parent.append(span);}
  function buildParams(reset){const params=new URLSearchParams();['q','service','category','importance','impact_level','relevance','source'].forEach(id=>{const value=byId(id).value.trim();if(value)params.set(id,value);});if(byId('from').value)params.set('analyzed_after',byId('from').value+'T00:00:00Z');if(byId('to').value)params.set('analyzed_before',byId('to').value+'T23:59:59.999999Z');params.set('limit','25');if(!reset&&state.cursor)params.set('cursor',state.cursor);return params;}
  async function api(path){const response=await fetch(path,{credentials:'same-origin',headers:{'Accept':'application/json'}});if(!response.ok)throw new Error(String(response.status));return response.json();}
  function renderRow(item){const row=el('li','row');const main=el('div','row-main');const button=el('button','row-title',item.title);button.type='button';button.addEventListener('click',()=>openDetail(item.archive_id,true));main.append(button,el('div','summary',item.one_line_summary));row.append(main,el('div','source',item.azure_services.join(', ')||item.update_category),badge(item.importance),badge(item.impact_level));return row;}
  async function load(reset){if(state.loading)return;state.loading=true;setStatus('archive_loading');if(reset){state.cursor='';byId('results').replaceChildren();}try{const page=await api('/api/archive/analyses?'+buildParams(reset));page.items.forEach(item=>byId('results').append(renderRow(item)));state.cursor=page.next_cursor||'';byId('more').hidden=!page.has_more;setStatus(byId('results').children.length?'':'archive_empty');}catch(_){setStatus('archive_error');}finally{state.loading=false;}}
  function section(title,value){if(value===undefined||value===null||value===''||(Array.isArray(value)&&!value.length))return;const block=el('section','detail-section');block.append(el('h2','',title));if(Array.isArray(value)){const list=el('ul','stack');value.forEach(item=>{const li=el('li','');li.textContent=typeof item==='string'?item:JSON.stringify(item,null,2);list.append(li);});block.append(list);}else{block.append(el('div','prose',value));}byId('detail-body').append(block);}
  function renderFacts(result){const block=el('section','detail-section');block.append(el('h2','',label('quick_decision')));const facts=el('dl','facts');[['col_importance',levelText(result.importance)],['col_impact',levelText(result.impact_level)],['relevance',relevanceText(result.relevance)]].forEach(([key,value])=>{const fact=el('div','fact');fact.append(el('dt','',label(key)),el('dd','',value));facts.append(fact);});block.append(facts);byId('detail-body').append(block);}
  function renderImpact(result){const title=label(result.update_category&&['new_feature','new_service','region_expansion','preview','sdk_tooling'].includes(result.update_category)?'opportunity_analysis':'impact_analysis');if(!result.impact_details){section(title,result.impact_summary);return;}const block=el('section','detail-section');block.append(el('h2','',title));const facts=el('dl','facts');[['cost_impact','cost'],['security_impact','security'],['performance_impact','performance'],['operational_impact','operational']].forEach(([field,key])=>{if(!result.impact_details[field])return;const fact=el('div','fact');fact.append(el('dt','',label(key)),el('dd','',result.impact_details[field]));facts.append(fact);});block.append(facts);byId('detail-body').append(block);}
  function renderResources(resources){if(!resources||!resources.length)return;const block=el('section','detail-section');block.append(el('h2','',label('affected_resources')));const list=el('ul','stack');resources.forEach(resource=>{const li=el('li','');li.append(el('strong','',resource.name||label('unknown_scope')));const scope=[resource.type,resource.resourceGroup,resource.subscription||resource.subscriptionId].filter(Boolean).join(' · ');if(scope)li.append(el('div','meta',scope));if(resource.reason)li.append(el('div','prose',resource.reason));list.append(li);});block.append(list);byId('detail-body').append(block);}
  function renderActions(actions){if(!actions||!actions.length)return;const names={urgency:'urgency',target_resources:'target',procedure:'procedure',deadline:'deadline',risk_if_not_done:'risk_if_not_done',precaution:'precaution',rollback:'rollback',verification_status:'verification'};const block=el('section','detail-section');block.append(el('h2','',label('action_items')));const list=el('ol','stack');actions.forEach(action=>{const li=el('li','');li.append(el('strong','',action.task||''));Object.keys(names).forEach(key=>{let value=action[key];if(value&&(!Array.isArray(value)||value.length)){if(key==='verification_status')value=label('verify_'+value);if(key==='urgency')value=levelText(value);li.append(el('div','meta',label(names[key])+': '+(Array.isArray(value)?value.join(', '):value)));}});if(action.cli_command)li.append(el('code','',action.cli_command));list.append(li);});block.append(list);byId('detail-body').append(block);}
  function renderReferences(docs){if(!docs||!docs.length)return;const block=el('section','detail-section');block.append(el('h2','',label('reference_docs')));const list=el('ul','stack');docs.forEach(doc=>{const li=el('li','');li.append(safeLink(doc.url||'',doc.title||doc.url||'-'));list.append(li);});block.append(list);byId('detail-body').append(block);}
  function renderDetail(documentData){const update=documentData.update,result=documentData.result;byId('detail-title').textContent=update.title;byId('detail-meta').replaceChildren();appendMeta(byId('detail-meta'),'archive_analyzed_at',localTime(documentData.analyzed_at));appendMeta(byId('detail-meta'),'published_date',localTime(update.published_date));appendMeta(byId('detail-meta'),'archive_source',sourceText(documentData.source));byId('detail-meta').append(safeLink(update.link,label('archive_original_update')));byId('detail-body').replaceChildren();section(label('importance_section'),result.one_line_summary);renderFacts(result);section(label('analysis_summary'),result.relevance_reason);section(label('relevance_evidence'),result.relevance_evidence);renderImpact(result);renderResources(result.affected_resources);renderActions(result.action_items);renderReferences(result.reference_docs);section(label('additional_checks'),result.additional_checks);}
  async function openDetail(id,push){setStatus('archive_loading');try{const data=await api('/api/archive/analyses/'+encodeURIComponent(id));renderDetail(data);byId('browser').hidden=true;byId('detail').hidden=false;if(push)history.pushState({id},'', '/archive/'+encodeURIComponent(id));setStatus('');}catch(_){setStatus('archive_error');}}
  function showBrowser(push){byId('detail').hidden=true;byId('browser').hidden=false;if(push)history.pushState({},'', '/archive');}
  byId('filters').addEventListener('submit',event=>{event.preventDefault();load(true);});
  byId('filters').addEventListener('reset',()=>setTimeout(()=>{byId('filters').classList.remove('expanded');byId('filter-toggle').setAttribute('aria-expanded','false');load(true);},0));
  byId('filter-toggle').addEventListener('click',()=>{const expanded=byId('filters').classList.toggle('expanded');byId('filter-toggle').setAttribute('aria-expanded',String(expanded));});
  byId('more').addEventListener('click',()=>load(false));
  byId('back').addEventListener('click',()=>showBrowser(true));
  window.addEventListener('popstate',()=>{const id=location.pathname.split('/').filter(Boolean)[1];if(id)openDetail(id,false);else showBrowser(false);});
  applyLabels();const initialId=location.pathname.split('/').filter(Boolean)[1];if(initialId)openDetail(initialId,false);else load(true);
  </script>
</body>
</html>"""


def render_archive_page(nonce: str, profile: str, user: str, language: str = "ko") -> str:
    """Render the archive shell with escaped identity and a nonce-bound script."""
    labels = dict(get_labels(language))
    labels.pop("col_job_relevance", None)
    labels_json = json.dumps(labels, ensure_ascii=False, separators=(",", ":"))
    labels_json = (
        labels_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    return (
        _PAGE.replace("__NONCE__", html.escape(nonce, quote=True))
        .replace("__LANG__", html.escape(get_language(language).lang_attr, quote=True))
        .replace("__TITLE__", html.escape(labels["archive_title"]))
        .replace("__PROFILE__", html.escape(profile))
        .replace("__USER__", html.escape(user))
        .replace("__LABELS__", labels_json)
    )
