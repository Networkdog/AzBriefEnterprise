"""Server-rendered HTML for the AzBrief admin console.

Everything ships inline: no CDN, no bundler, no external fetch. That keeps the
page usable behind a locked-down egress policy and lets the response carry a
strict Content-Security-Policy with a per-request script nonce.
"""

from __future__ import annotations

from html import escape

# `__NONCE__` is substituted per request. Placeholders use double underscores
# rather than str.format so the CSS braces need no escaping.
_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>AzBrief 관리 콘솔</title>
<style nonce="__NONCE__">
:root {
  --bg: #0f172a; --panel: #16233b; --line: #24344f; --text: #e6edf7;
  --muted: #93a4bd; --accent: #4f9cf9; --ok: #34d399; --warn: #fbbf24; --bad: #f87171;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font-family: 'Segoe UI', 'Malgun Gothic', -apple-system, sans-serif; font-size: 14px; }
header { padding: 20px 28px; border-bottom: 1px solid var(--line);
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
h1 { margin: 0; font-size: 18px; letter-spacing: -0.2px; }
.badge { padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600;
  background: #1e3a5f; color: var(--accent); }
.who { margin-left: auto; color: var(--muted); font-size: 13px; }
.nav { color: #b8ddff; font-size: 13px; font-weight: 600; text-decoration: none; }
main { padding: 24px 28px 48px; max-width: 1180px; }
section { margin-bottom: 28px; }
h2 { font-size: 14px; text-transform: uppercase; letter-spacing: 0.6px;
  color: var(--muted); margin: 0 0 12px; }
.grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }
.card .k { color: var(--muted); font-size: 12px; margin-bottom: 6px; }
.card .v { font-size: 15px; font-weight: 600; word-break: break-word; }
table { width: 100%; border-collapse: collapse; background: var(--panel);
  border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
th, td { padding: 9px 12px; text-align: left; border-bottom: 1px solid var(--line); font-size: 13px; }
th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; }
tr:last-child td { border-bottom: none; }
button { background: var(--accent); color: #06122a; border: 0; border-radius: 8px;
  padding: 9px 18px; font-size: 13px; font-weight: 700; cursor: pointer; }
button[disabled] { opacity: 0.5; cursor: not-allowed; }
label { color: var(--muted); font-size: 13px; display: inline-flex; align-items: center; gap: 6px; }
input[type=text] { background: #0d1830; border: 1px solid var(--line); color: var(--text);
  border-radius: 8px; padding: 8px 10px; font-size: 13px; min-width: 230px; }
.controls { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.msg { margin-left: 4px; font-size: 13px; color: var(--muted); }
.s-completed { color: var(--ok); } .s-running, .s-queued { color: var(--warn); }
.s-failed { color: var(--bad); }
.empty { color: var(--muted); padding: 12px 2px; }
a { color: var(--accent); }
</style>
</head>
<body>
<header>
  <h1>AzBrief 관리 콘솔</h1>
  <span class="badge">__PROFILE__</span>
  __ARCHIVE_LINK__
  <span class="who">__USER__</span>
</header>
<main>
  <section>
    <h2>구성 상태</h2>
    <div class="grid" id="status"><div class="empty">불러오는 중…</div></div>
  </section>

  <section>
    <h2>분석 실행</h2>
    <div class="controls">
      <button id="run">지금 실행</button>
      <label>기준 시각(UTC, 선택)
        <input type="text" id="since" placeholder="2026-08-18T00:00:00Z">
      </label>
      <label><input type="checkbox" id="dry"> 드라이런</label>
      <span class="msg" id="msg"></span>
    </div>
    <table>
      <thead><tr><th>실행 ID</th><th>상태</th><th>대상</th><th>분석</th><th>실패</th>
        <th>연기</th><th>소요(초)</th><th>시작(UTC)</th></tr></thead>
      <tbody id="runs"><tr><td colspan="8" class="empty">불러오는 중…</td></tr></tbody>
    </table>
  </section>

  <section>
    <h2>구독자</h2>
    <table>
      <thead><tr><th>이메일</th><th>이름</th><th>역할</th><th>언어</th><th>알림 수준</th></tr></thead>
      <tbody id="subs"><tr><td colspan="5" class="empty">불러오는 중…</td></tr></tbody>
    </table>
  </section>

  <section>
    <h2>최근 Azure 업데이트</h2>
    <table>
      <thead><tr><th>발행일(UTC)</th><th>제목</th><th>유형</th></tr></thead>
      <tbody id="updates"><tr><td colspan="3" class="empty">불러오는 중…</td></tr></tbody>
    </table>
  </section>
</main>
<script nonce="__NONCE__">
const $ = (id) => document.getElementById(id);
const text = (v) => (v === null || v === undefined || v === '') ? '—' : String(v);

async function api(path, options) {
  const res = await fetch(path, Object.assign({credentials: 'same-origin'}, options || {}));
  if (!res.ok) throw new Error(await res.text());
  return res.json();
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

async function loadStatus() {
  const data = await api('/api/admin/status');
  const box = $('status');
  box.replaceChildren();
  Object.entries(data).forEach(([k, v]) => {
    const card = document.createElement('div');
    card.className = 'card';
    const kk = document.createElement('div'); kk.className = 'k'; kk.textContent = k;
    const vv = document.createElement('div'); vv.className = 'v';
    vv.textContent = Array.isArray(v) ? (v.length ? v.join(', ') : '—') : text(v);
    card.appendChild(kk); card.appendChild(vv); box.appendChild(card);
  });
}

async function loadRuns() {
  const data = await api('/api/admin/runs');
  fill($('runs'), data.runs.map((r) => row(
    [r.run_id.slice(0, 8), r.status, r.total, r.analyzed, r.failed, r.deferred,
     r.elapsed_seconds, r.started_at],
    's-' + r.status)), 8, '실행 기록이 없습니다.');
}

async function loadSubs() {
  const data = await api('/api/admin/subscribers');
  fill($('subs'), data.subscribers.map((s) => row(
    [s.email, s.name, s.role, s.language, s.alert_level])), 5, '구독자가 없습니다.');
}

async function loadUpdates() {
  const data = await api('/api/admin/updates');
  fill($('updates'), data.updates.map((u) => row(
    [u.published_date, u.title, u.update_type])), 3, '업데이트가 없습니다.');
}

$('run').addEventListener('click', async () => {
  const btn = $('run'); btn.disabled = true; $('msg').textContent = '실행 요청 중…';
  try {
    const body = {dry_run: $('dry').checked};
    const since = $('since').value.trim();
    if (since) body.since = since;
    const res = await api('/api/admin/runs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    $('msg').textContent = '실행 시작: ' + res.run_id.slice(0, 8);
    await loadRuns();
  } catch (e) {
    $('msg').textContent = '실행 실패: ' + e.message;
  } finally {
    btn.disabled = false;
  }
});

async function refresh() {
  try { await loadRuns(); } catch (e) { /* transient */ }
}

(async function init() {
  await Promise.allSettled([loadStatus(), loadRuns(), loadSubs(), loadUpdates()]);
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
    archive_link = '<a class="nav" href="/archive">분석 아카이브</a>' if archive_enabled else ""
    return (
        _PAGE.replace("__NONCE__", escape(nonce, quote=True))
        .replace("__PROFILE__", escape(profile))
        .replace("__ARCHIVE_LINK__", archive_link)
        .replace("__USER__", escape(user))
    )
