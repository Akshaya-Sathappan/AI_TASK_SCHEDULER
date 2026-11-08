"""GET /monitor: a live page of what every part of a request said (D62).

One self-contained HTML page (no build step, no external files) served by the API. It reads the
API's own JSON endpoints:

    GET /requests                               the request list (left column, refreshed every 3 s)
    GET /requests/{id}?include_outputs=true     one request (right side, refreshed every second
                                                until it finishes)

and shows, for the selected request:
  - the planner's output: the plan summary and every task's type, title, dependencies and instruction
    (with D61 the final task's instruction ends with the user's original request);
  - the runtime: each task's state and attempt (from atsd) and its tokens (from the workers);
  - each worker's answer: final_answer, content and key points (an expand task shows its subplan);
  - the final answer of the request, and the error if it failed;
  - the task graph (D63): one box per task, arrows from each dependency, coloured by state; click a
    box to jump to its card;
  - the timeline (D63): a Gantt chart from atsd's event log (GET /requests/{id}/timeline), one row
    per task and one bar per attempt (dispatch -> pickup -> end), coloured by how the attempt ended,
    with 429 pauses marked. The planner's time is not in it: the plan exists before atsd sees it.
A form at the top submits a new request (POST /requests) and selects it.

Open it at http://localhost:8000/monitor (on the VM: the same address in the VM's browser).
"""

MONITOR_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ATS Monitor</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--text:#1d2330;--muted:#5d6678;--line:#dde1e8;--accent:#2f5bd3;
--ok:#1f7a45;--okbg:#e3f4ea;--run:#8a5a00;--runbg:#fdf1d8;--bad:#b42318;--badbg:#fde7e5;--wait:#5d6678;--waitbg:#eceef2;
--code:#f1f3f6}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--panel:#1a1e26;--text:#e6e9ef;--muted:#9aa3b2;--line:#2c323d;
--accent:#7aa2ff;--ok:#6fd39a;--okbg:#173225;--run:#f2c46b;--runbg:#3a2e14;--bad:#ff8a80;--badbg:#3d1c1a;--wait:#9aa3b2;
--waitbg:#272c35;--code:#222731}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{padding:12px 16px;border-bottom:1px solid var(--line);background:var(--panel);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header h1{font-size:16px;margin:0 8px 0 0}
header form{display:flex;gap:8px;flex:1;min-width:260px}
header input{flex:1;padding:7px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--text)}
button,select{padding:7px 12px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--text);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
main{display:grid;grid-template-columns:320px 1fr;min-height:calc(100vh - 58px)}
@media (max-width:800px){main{grid-template-columns:1fr}#list{max-height:240px}}
#list{border-right:1px solid var(--line);overflow:auto;background:var(--panel)}
.req{padding:9px 14px;border-bottom:1px solid var(--line);cursor:pointer}
.req:hover,.req.sel{background:var(--bg)}
.req .sum{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.meta{color:var(--muted);font-size:12px}
#detail{padding:16px;overflow:auto;min-width:0}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600}
.s-COMPLETED,.s-SUCCEEDED{color:var(--ok);background:var(--okbg)}
.s-RUNNING,.s-DISPATCHED,.s-READY,.s-SUBMITTED,.s-ACCEPTED{color:var(--run);background:var(--runbg)}
.s-FAILED,.s-REJECTED,.s-CANCELLED{color:var(--bad);background:var(--badbg)}
.s-WAITING,.s-PENDING,.s-UNKNOWN,.s-SKIPPED{color:var(--wait);background:var(--waitbg)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:0 0 12px}
.card h2{font-size:15px;margin:0 0 6px}
.card h3{font-size:14px;margin:0}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em;margin:10px 0 3px}
.answer{font-size:16px;font-weight:600}
pre{white-space:pre-wrap;word-break:break-word;background:var(--code);padding:8px 10px;border-radius:6px;margin:0;font:12.5px/1.45 ui-monospace,Menlo,Consolas,monospace}
ul{margin:0;padding-left:20px}
.stats{display:flex;gap:18px;flex-wrap:wrap;margin-top:6px}
.stats div b{display:block;font-size:16px}
details summary{cursor:pointer;color:var(--muted);font-size:12px;margin-top:8px}
.err{color:var(--bad)}
.empty{color:var(--muted);padding:24px}
.chart{overflow-x:auto;margin-top:6px}
.chart svg{display:block}
.node rect{stroke-width:1.5;rx:7}
.node{cursor:pointer}
.node text{font:12px system-ui,sans-serif;fill:var(--text)}
.node .tid{font-weight:700}
.n-COMPLETED rect,.b-completed{fill:var(--okbg);stroke:var(--ok)}
.n-RUNNING rect,.n-DISPATCHED rect,.n-READY rect,.n-QUEUED rect,.n-RETRYING rect,.b-running{fill:var(--runbg);stroke:var(--run)}
.n-FAILED rect,.n-CANCELLED rect,.b-failed,.b-lost{fill:var(--badbg);stroke:var(--bad)}
.n-WAITING rect,.n-UNKNOWN rect,.n-SKIPPED rect,.n-PENDING rect{fill:var(--waitbg);stroke:var(--wait)}
.b-retried{fill:var(--runbg);stroke:var(--bad);stroke-dasharray:3 2}
.b-late{fill:var(--waitbg);stroke:var(--wait)}
.b-running{animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{50%{opacity:.55}}
.edge{fill:none;stroke:var(--muted);stroke-width:1.3}
.arrow{fill:var(--muted)}
.wait{stroke:var(--muted);stroke-width:2;stroke-dasharray:2 3}
.axis text,.rowlabel{font:11px system-ui,sans-serif;fill:var(--muted)}
.axis line{stroke:var(--line)}
.pause{stroke:var(--bad);stroke-width:1.5;stroke-dasharray:4 3}
.joinpt{fill:var(--ok)}
.legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:12px;margin-top:8px}
.legend i{display:inline-block;width:14px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px;border:1.5px solid}
</style>
</head>
<body>
<header>
  <h1>ATS Monitor</h1>
  <form id="submit">
    <input id="text" placeholder="Ask something to run through the scheduler..." autocomplete="off">
    <select id="mode" title="How the API runs it"><option value="workflow">workflow</option><option value="auto">auto</option><option value="direct">direct</option></select>
    <button class="primary" type="submit">Run</button>
  </form>
  <span id="note" class="meta"></span>
</header>
<main>
  <div id="list"><div class="empty">Loading requests...</div></div>
  <div id="detail"><div class="empty">Pick a request on the left, or run a new one.</div></div>
</main>
<script>
const FINISHED = new Set(["SUCCEEDED", "FAILED", "REJECTED"]);
let selected = location.hash.slice(1) || null;
let timer = null;
let shown = "";
let timeline = null;

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const badge = s => `<span class="badge s-${esc(s)}">${esc(s)}</span>`;
const order = id => id.split(".").map(p => /^\d+$/.test(p.replace(/^t/, "")) ? Number(p.replace(/^t/, "")) : (p === "join" ? 1e9 : 0));

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail ? JSON.stringify(body.detail) : `HTTP ${r.status}`);
  return body;
}

function output(value) {
  if (value === null || value === undefined) return `<div class="meta">no output yet</div>`;
  if (typeof value === "string") return `<pre>${esc(value)}</pre>`;
  if (typeof value.content === "string") {
    let h = "";
    if (value.final_answer) h += `<div class="label">final_answer</div><div class="answer">${esc(value.final_answer)}</div>`;
    h += `<div class="label">content</div><pre>${esc(value.content)}</pre>`;
    if ((value.key_points || []).length) h += `<div class="label">key points</div><ul>${value.key_points.map(p => `<li>${esc(p)}</li>`).join("")}</ul>`;
    return h;
  }
  if (Array.isArray(value.tasks)) {  // an expand task's subplan
    return `<div class="label">subplan (${value.tasks.length} tasks)</div><ul>${value.tasks.map(t =>
      `<li><b>${esc(t.id)}</b> ${esc(t.type)}: ${esc(t.title)}</li>`).join("")}</ul>`;
  }
  return `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`;
}

async function loadList() {
  try {
    const reqs = await getJSON("/requests?limit=100");
    const list = document.getElementById("list");
    if (!reqs.length) { list.innerHTML = `<div class="empty">No requests yet.</div>`; return; }
    list.innerHTML = reqs.map(r => `<div class="req ${r.request_id === selected ? "sel" : ""}" data-id="${esc(r.request_id)}">
      <div class="row">${badge(r.status)}<span class="meta">${esc(r.request_id)}</span></div>
      <div class="sum">${esc(r.summary)}</div>
      <div class="meta">${esc(r.created_at)} · ${r.tasks} task${r.tasks === 1 ? "" : "s"}</div></div>`).join("");
    list.querySelectorAll(".req").forEach(el => el.onclick = () => select(el.dataset.id));
  } catch (e) { document.getElementById("list").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
}

function select(id) {
  selected = id; shown = ""; location.hash = id;
  document.querySelectorAll(".req").forEach(el => el.classList.toggle("sel", el.dataset.id === id));
  clearTimeout(timer); loadDetail();
}

async function loadDetail() {
  if (!selected) return;
  let st;
  try { st = await getJSON(`/requests/${encodeURIComponent(selected)}?include_outputs=true`); }
  catch (e) { document.getElementById("detail").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; return; }
  try { timeline = await getJSON(`/requests/${encodeURIComponent(selected)}/timeline`); }
  catch (e) { timeline = null; }
  const html = render(st);
  if (html !== shown) { document.getElementById("detail").innerHTML = html; shown = html; }
  if (!FINISHED.has(st.status)) timer = setTimeout(loadDetail, 1000);
  else loadList();
}

function render(st) {
  const usage = st.usage || {};
  const byTask = usage.by_task || {};
  const outputs = st.outputs || {};
  const tasks = [...st.tasks].sort((a, b) => { const x = order(a.id), y = order(b.id);
    for (let i = 0; i < Math.max(x.length, y.length); i++) { const d = (x[i] ?? -1) - (y[i] ?? -1); if (d) return d; } return 0; });
  const done = tasks.filter(t => t.state === "COMPLETED").length;
  const retries = tasks.reduce((n, t) => n + Math.max(0, (t.attempt || 0) - 1), 0);
  let h = `<div class="card"><div class="row">${badge(st.status)}<span class="meta">${esc(st.request_id)} · ${esc(st.created_at)}</span></div>
    <h2>${esc(st.summary)}</h2>
    <div class="stats"><div><span class="meta">tasks done</span><b>${done} / ${tasks.length}</b></div>
    <div><span class="meta">retries</span><b>${retries}</b></div>
    <div><span class="meta">LLM calls</span><b>${usage.llm_calls ?? 0}</b></div>
    <div><span class="meta">tokens in / out</span><b>${usage.tokens_in ?? 0} / ${usage.tokens_out ?? 0}</b></div>
    <div><span class="meta">input cached</span><b>${usage.tokens_in ? Math.round(100 * (usage.tokens_cached ?? 0) / usage.tokens_in) : 0} %</b></div>
    <div><span class="meta">final task</span><b>${esc(st.sink || "-")}</b></div></div>
    ${st.error ? `<div class="label">error</div><pre class="err">${esc(st.error)}</pre>` : ""}</div>`;
  h += `<div class="card"><h2>Final answer</h2>${st.output === null ? `<div class="meta">${FINISHED.has(st.status) ? "none" : "waiting for the final task..."}</div>` : output(st.output)}</div>`;
  h += `<div class="card"><h2>Task graph</h2><div class="meta">Arrows run from a task to the tasks that use its result. Click a task to see its output.</div>${graph(tasks)}
    <div class="legend"><span><i class="n-WAITING" style="background:var(--waitbg);border-color:var(--wait)"></i>waiting</span>
    <span><i style="background:var(--runbg);border-color:var(--run)"></i>running / retrying</span>
    <span><i style="background:var(--okbg);border-color:var(--ok)"></i>completed</span>
    <span><i style="background:var(--badbg);border-color:var(--bad)"></i>failed</span></div></div>`;
  h += `<div class="card"><h2>Timeline</h2>${gantt(timeline, tasks, FINISHED.has(st.status))}</div>`;
  h += `<div class="label" style="margin:16px 0 8px">Planner's plan and each task's output</div>`;
  for (const t of tasks) {
    const tok = byTask[t.id] || {};
    h += `<div class="card" id="task-${esc(t.id)}"><div class="row"><h3>${esc(t.id)}</h3>${badge(t.state)}<span class="meta">${esc(t.type || "")}${t.attempt ? ` · attempt ${t.attempt}` : ""}${tok.tokens_in ? ` · ${tok.tokens_in} in / ${tok.tokens_out || 0} out` : ""}${(t.depends_on || []).length ? ` · after ${t.depends_on.map(esc).join(", ")}` : ""}</span></div>
      <div>${esc(t.title || "")}</div>
      ${t.instruction ? `<details${t.state !== "COMPLETED" ? " open" : ""}><summary>instruction (from the planner)</summary><pre>${esc(t.instruction)}</pre></details>` : ""}
      ${t.state === "COMPLETED" ? output(outputs[t.id]) : ""}</div>`;
  }
  return h;
}

// --- task graph (D63): layers by longest path from a task with no dependencies --------------------
function graph(tasks) {
  const byId = Object.fromEntries(tasks.map(t => [t.id, t]));
  const depth = {};
  const level = (id, seen = new Set()) => {
    if (depth[id] !== undefined) return depth[id];
    if (seen.has(id)) return 0;
    seen.add(id);
    const deps = (byId[id]?.depends_on || []).filter(d => byId[d]);
    return depth[id] = deps.length ? 1 + Math.max(...deps.map(d => level(d, seen))) : 0;
  };
  tasks.forEach(t => level(t.id));
  const cols = [];
  tasks.forEach(t => (cols[depth[t.id]] ||= []).push(t));
  const W = 168, H = 48, GX = 64, GY = 16, PAD = 10;
  const rows = Math.max(...cols.map(c => c.length));
  const width = PAD * 2 + cols.length * W + (cols.length - 1) * GX;
  const height = PAD * 2 + rows * H + (rows - 1) * GY;
  const pos = {};
  cols.forEach((col, x) => {
    const offset = (height - (col.length * H + (col.length - 1) * GY)) / 2;
    col.forEach((t, y) => pos[t.id] = {x: PAD + x * (W + GX), y: offset + y * (H + GY)});
  });
  let edges = "", nodes = "";
  for (const t of tasks) for (const d of t.depends_on || []) {
    if (!pos[d]) continue;
    const a = pos[d], b = pos[t.id];
    const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x - 6, y2 = b.y + H / 2, mx = (x1 + x2) / 2;
    edges += `<path class="edge" d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}"/>` +
             `<polygon class="arrow" points="${x2},${y2 - 4} ${x2 + 6},${y2} ${x2},${y2 + 4}"/>`;
  }
  const cut = (s, n) => s.length > n ? s.slice(0, n - 1) + "…" : s;
  for (const t of tasks) {
    const p = pos[t.id];
    nodes += `<g class="node n-${esc(t.state)}" data-task="${esc(t.id)}"><title>${esc(t.id)} · ${esc(t.state)}
${esc(t.title || "")}</title>
      <rect x="${p.x}" y="${p.y}" width="${W}" height="${H}"/>
      <text class="tid" x="${p.x + 10}" y="${p.y + 19}">${esc(t.id)} <tspan font-weight="400" fill="var(--muted)">${esc(t.type || "")}</tspan></text>
      <text x="${p.x + 10}" y="${p.y + 36}">${esc(cut(t.title || "", 24))}</text></g>`;
  }
  return `<div class="chart"><svg width="${width}" height="${height}" role="img" aria-label="Task dependency graph">${edges}${nodes}</svg></div>`;
}

// --- timeline (D63): one row per task, one bar per attempt, from atsd's event log -----------------
function gantt(tl, tasks, finished) {
  if (!tl || !tl.available) return `<div class="meta">No events for this request in atsd's event log yet${tl ? "" : " (or the API cannot read runs/)"}.</div>`;
  const ids = tasks.map(t => t.id);
  tl.attempts.forEach(a => { if (!ids.includes(a.task)) ids.push(a.task); });
  const now = finished ? (tl.finished_ms ?? tl.end_ms) : Math.max(tl.now_ms ?? 0, tl.end_ms);
  const span = Math.max(now, tl.end_ms, 1);
  const LABEL = 70, RH = 26, PAD = 8, plotW = 760;
  const width = LABEL + plotW + PAD * 2, height = PAD + 22 + ids.length * RH + PAD;
  const x = ms => LABEL + PAD + (Math.min(ms, span) / span) * plotW;
  const row = id => PAD + 22 + ids.indexOf(id) * RH;
  const step = [100, 250, 500, 1000, 2000, 5000, 10000, 15000, 30000, 60000, 120000, 300000].find(s => span / s <= 8) || 600000;
  let axis = "";
  for (let t = 0; t <= span; t += step) axis += `<line x1="${x(t)}" y1="${PAD + 16}" x2="${x(t)}" y2="${height - PAD}"/><text x="${x(t)}" y="${PAD + 10}" text-anchor="middle">${(t / 1000).toFixed(step < 1000 ? 1 : 0)} s</text>`;
  let rowsSvg = ids.map(id => `<text class="rowlabel" x="${PAD}" y="${row(id) + 16}">${esc(id)}</text>`).join("");
  let bars = "";
  for (const a of tl.attempts) {
    const y = row(a.task) + 5, h = RH - 10;
    const d = a.dispatched ?? a.started ?? 0, s = a.started, e = a.end ?? now;
    const tip = `${a.task} attempt ${a.attempt}${a.backup ? " (backup)" : ""}: ${a.outcome}` +
      `\ndispatched ${(d / 1000).toFixed(2)} s` + (s != null ? `, started ${(s / 1000).toFixed(2)} s` : "") +
      (a.end != null ? `, ended ${(a.end / 1000).toFixed(2)} s` : ", still running") +
      (a.worker ? `\nworker ${a.worker}` : "") + (a.detail ? `\n${a.detail}` : "");
    if (s != null && s > d) bars += `<line class="wait" x1="${x(d)}" y1="${y + h / 2}" x2="${x(s)}" y2="${y + h / 2}"/>`;
    const b0 = s ?? d;
    bars += `<rect class="b-${esc(a.outcome)}" x="${x(b0)}" y="${y}" width="${Math.max(2, x(e) - x(b0))}" height="${h}" rx="3" stroke-width="1.5"><title>${esc(tip)}</title></rect>`;
    if (a.attempt > 1 || a.backup) bars += `<text class="rowlabel" x="${x(b0) + 4}" y="${y + h - 4}">${a.backup ? "backup" : "#" + a.attempt}</text>`;
  }
  for (const j of tl.joins) bars += `<circle class="joinpt" cx="${x(j.at)}" cy="${row(j.task) + RH / 2}" r="4"><title>${esc(j.task)} joined at ${(j.at / 1000).toFixed(2)} s</title></circle>`;
  for (const p of tl.pauses) bars += `<line class="pause" x1="${x(p.at)}" y1="${PAD + 16}" x2="${x(p.at)}" y2="${height - PAD}"><title>paused after a 429 at ${(p.at / 1000).toFixed(2)} s\n${esc(p.detail || "")}</title></line>`;
  const total = ((tl.finished_ms ?? tl.end_ms) / 1000).toFixed(2);
  return `<div class="meta">From atsd accepting the request${finished ? ` to the last task: ${total} s` : " (live)"}. Hover a bar for its times. The planner ran before this.</div>
    <div class="chart"><svg viewBox="0 0 ${width} ${height}" width="100%" style="min-width:560px;max-width:${width}px" role="img" aria-label="Task timeline"><g class="axis">${axis}</g>${rowsSvg}${bars}</svg></div>
    <div class="legend"><span><svg width="26" height="10"><line class="wait" x1="0" y1="5" x2="26" y2="5"/></svg>waiting for a worker</span>
    <span><i style="background:var(--okbg);border-color:var(--ok)"></i>completed</span>
    <span><i style="background:var(--runbg);border-color:var(--run)"></i>running</span>
    <span><i style="background:var(--runbg);border-color:var(--bad);border-style:dashed"></i>failed, retried</span>
    <span><i style="background:var(--badbg);border-color:var(--bad)"></i>failed / timed out</span>
    <span><i style="background:var(--waitbg);border-color:var(--wait)"></i>finished after another attempt won</span>
    <span><svg width="14" height="12"><line class="pause" x1="7" y1="0" x2="7" y2="12"/></svg>paused after a 429</span></div>`;
}

document.getElementById("detail").addEventListener("click", ev => {
  const node = ev.target.closest(".node");
  if (node) document.getElementById(`task-${node.dataset.task}`)?.scrollIntoView({behavior: "smooth", block: "start"});
});

document.getElementById("submit").onsubmit = async ev => {
  ev.preventDefault();
  const text = document.getElementById("text").value.trim();
  if (!text) return;
  const note = document.getElementById("note");
  note.textContent = "planning...";
  try {
    const r = await getJSON("/requests", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text, mode: document.getElementById("mode").value})});
    note.textContent = `${r.request_id} accepted`;
    document.getElementById("text").value = "";
    await loadList(); select(r.request_id);
  } catch (e) { note.textContent = `rejected: ${e.message}`; }
};

loadList(); setInterval(loadList, 3000);
if (selected) loadDetail();
</script>
</body>
</html>
"""
