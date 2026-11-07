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
  - the final answer of the request, and the error if it failed.
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
    <div><span class="meta">final task</span><b>${esc(st.sink || "-")}</b></div></div>
    ${st.error ? `<div class="label">error</div><pre class="err">${esc(st.error)}</pre>` : ""}</div>`;
  h += `<div class="card"><h2>Final answer</h2>${st.output === null ? `<div class="meta">${FINISHED.has(st.status) ? "none" : "waiting for the final task..."}</div>` : output(st.output)}</div>`;
  h += `<div class="label" style="margin:16px 0 8px">Planner's plan and each task's output</div>`;
  for (const t of tasks) {
    const tok = byTask[t.id] || {};
    h += `<div class="card"><div class="row"><h3>${esc(t.id)}</h3>${badge(t.state)}<span class="meta">${esc(t.type || "")}${t.attempt ? ` · attempt ${t.attempt}` : ""}${tok.tokens_in ? ` · ${tok.tokens_in} in / ${tok.tokens_out || 0} out` : ""}${(t.depends_on || []).length ? ` · after ${t.depends_on.map(esc).join(", ")}` : ""}</span></div>
      <div>${esc(t.title || "")}</div>
      ${t.instruction ? `<details${t.state !== "COMPLETED" ? " open" : ""}><summary>instruction (from the planner)</summary><pre>${esc(t.instruction)}</pre></details>` : ""}
      ${t.state === "COMPLETED" ? output(outputs[t.id]) : ""}</div>`;
  }
  return h;
}

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
