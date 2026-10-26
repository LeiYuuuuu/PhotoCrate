"""手机互传的网页（一个文件，内联样式和脚本，手机 / 平板 / 电视浏览器都能用）。

两个标签：「相册」按天浏览、点开看大图（左右滑动切换，可下载原图；视频直接播放）；「上传到电脑」选照片 / 视频，
一个个用 PUT 发过去并显示进度。文件名一律用 textContent 放进页面，不拼 HTML。界面文字由 render 的 strings 传入（跟随软件语言）。
"""
from __future__ import annotations

import json

from ..i18n import t as _t


def default_strings() -> dict:
    return {
        "title": _t("影匣 · 手机互传"), "album": _t("相册"), "upload": _t("上传到电脑"), "all": _t("全部照片"),
        "loading": _t("正在载入…"), "empty": _t("这里还没有照片"), "pick": _t("选择照片或视频"),
        "pick_sub": _t("可以一次选很多张；传完几秒后电脑上的相册就能看到"), "waiting": _t("等待上传"),
        "done": _t("已传到电脑"), "dup": _t("电脑上已经有了，跳过"), "failed": _t("失败"),
        "summary": _t("已上传 {0} / {1}"), "download": _t("下载原图"), "close": _t("关闭"),
        "weekdays": [_t("周日"), _t("周一"), _t("周二"), _t("周三"), _t("周四"), _t("周五"), _t("周六")],
        "nodate": _t("没有拍摄日期"), "count": _t("{0} 张"), "keep_open": _t("上传时请不要锁屏或切走浏览器"),
    }


def render(allow_upload: bool, allow_browse: bool, strings: dict | None = None) -> str:
    s = default_strings()
    s.update(strings or {})
    cfg = {"upload": bool(allow_upload), "browse": bool(allow_browse)}
    return (PAGE.replace("__TITLE__", _html_escape(s["title"]))
            .replace("__STRINGS__", _script_json(s))
            .replace("__CFG__", _script_json(cfg)))


def _html_escape(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _script_json(data) -> str:
    """放进 <script> 里的 JSON：转义 < > &，防止提前结束脚本。"""
    return (json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


PAGE = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="referrer" content="no-referrer">
<title>__TITLE__</title>
<style>
:root { --bg:#f4f3ef; --ink:#1d1f22; --ink2:#5b6068; --line:#dddad2; --accent:#e0a21b; --card:#fff; }
@media (prefers-color-scheme: dark) { :root { --bg:#14161a; --ink:#eceae4; --ink2:#9aa0a8; --line:#2b2f36; --card:#1d2026; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.45 -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; }
header { position:sticky; top:0; z-index:5; background:var(--bg); border-bottom:1px solid var(--line); padding:10px 14px 0; }
h1 { font-size:17px; margin:0 0 8px; }
.tabs { display:flex; gap:4px; }
.tabs button { flex:1; border:0; background:transparent; color:var(--ink2); font:inherit; font-weight:700; padding:9px 0; border-bottom:3px solid transparent; }
.tabs button.on { color:var(--ink); border-color:var(--accent); }
select { margin:8px 0 10px; width:100%; font:inherit; padding:7px; border-radius:8px; border:1px solid var(--line); background:var(--card); color:var(--ink); }
main { padding:0 6px 40px; }
.day { padding:14px 8px 6px; font-weight:700; }
.day span { color:var(--ink2); font-weight:400; margin-left:6px; font-size:13px; }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(104px, 1fr)); gap:3px; }
.cell { position:relative; aspect-ratio:1; background:var(--line); overflow:hidden; cursor:pointer; }
.cell img { width:100%; height:100%; object-fit:cover; display:block; }
.cell .v { position:absolute; right:4px; bottom:3px; color:#fff; font-size:12px; text-shadow:0 0 3px #000; }
.hint { color:var(--ink2); text-align:center; padding:40px 10px; }
#viewer { position:fixed; inset:0; background:#000; z-index:20; display:none; flex-direction:column; }
#viewer.on { display:flex; }
#stage { flex:1; display:flex; align-items:center; justify-content:center; overflow:hidden; touch-action:pan-y; }
#stage img, #stage video { max-width:100%; max-height:100%; }
#vbar { display:flex; gap:8px; align-items:center; padding:10px 12px calc(10px + env(safe-area-inset-bottom)); color:#ddd; font-size:13px; }
#vbar .name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
#vbar a, #vbar button { color:#fff; background:#333; border:0; border-radius:8px; padding:8px 12px; font:inherit; text-decoration:none; }
.nav { position:absolute; top:50%; transform:translateY(-50%); background:rgba(0,0,0,.35); color:#fff; border:0; font-size:28px; width:46px; height:64px; }
#prev { left:0; } #next { right:0; }
#up { padding:18px 10px; }
.pick { display:block; text-align:center; background:var(--accent); color:#1d1f22; font-weight:800; padding:18px; border-radius:12px; font-size:17px; }
.pick small { display:block; font-weight:400; font-size:13px; margin-top:4px; }
#upinput { display:none; }
.row { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:9px 11px; margin-top:8px; }
.row .n { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.row .s { color:var(--ink2); font-size:12px; }
.bar { height:4px; background:var(--line); border-radius:2px; margin-top:6px; overflow:hidden; }
.bar i { display:block; height:100%; width:0; background:var(--accent); }
#upsum { margin-top:14px; font-weight:700; text-align:center; }
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <div class="tabs"><button id="tabA"></button><button id="tabU"></button></div>
</header>
<main>
  <section id="album">
    <select id="albumSel"></select>
    <div id="list"><div class="hint" id="loading"></div></div>
    <div id="more"></div>
  </section>
  <section id="up">
    <label class="pick" for="upinput"><span id="pickT"></span><small id="pickS"></small></label>
    <input id="upinput" type="file" multiple accept="image/*,video/*">
    <div id="upsum"></div>
    <div id="rows"></div>
  </section>
</main>
<div id="viewer">
  <div id="stage"></div>
  <button class="nav" id="prev">‹</button><button class="nav" id="next">›</button>
  <div id="vbar"><span class="name" id="vname"></span><a id="vdl"></a><button id="vclose"></button></div>
</div>
<script>
const T = __STRINGS__, CFG = __CFG__;
const $ = id => document.getElementById(id);
const fmt = (s, ...a) => s.replace(/\{(\d)\}/g, (_, i) => a[+i]);
$("title").textContent = T.title; document.title = T.title;
$("tabA").textContent = T.album; $("tabU").textContent = T.upload;
$("pickT").textContent = T.pick; $("pickS").textContent = T.pick_sub + " · " + T.keep_open;
$("loading").textContent = T.loading; $("vclose").textContent = T.close; $("vdl").textContent = T.download;
function tab(k) {
  $("album").style.display = k === "a" ? "" : "none"; $("up").style.display = k === "u" ? "" : "none";
  $("tabA").className = k === "a" ? "on" : ""; $("tabU").className = k === "u" ? "on" : "";
}
if (!CFG.browse) $("tabA").style.display = "none";
if (!CFG.upload) $("tabU").style.display = "none";
$("tabA").onclick = () => tab("a"); $("tabU").onclick = () => tab("u");
tab(CFG.browse ? "a" : "u");

// ---- 相册 ----
let ALL = [], SHOWN = [], COUNTS = {}, rendered = 0, lastDay = null, grid = null;
function dayTitle(d) {
  if (!d) return T.nodate;
  const dt = new Date(d.slice(0, 10) + "T12:00:00");
  return d.slice(0, 10) + " " + (isNaN(dt) ? "" : T.weekdays[dt.getDay()]);
}
function renderMore() {
  const list = $("list"), end = Math.min(SHOWN.length, rendered + 300);
  for (; rendered < end; rendered++) {
    const it = SHOWN[rendered], day = it.d ? it.d.slice(0, 10) : "";
    if (day !== lastDay) {
      lastDay = day;
      const h = document.createElement("div"); h.className = "day"; h.textContent = dayTitle(it.d);
      const c = document.createElement("span");
      c.textContent = fmt(T.count, COUNTS[day] || 0);
      h.appendChild(c); list.appendChild(h);
      grid = document.createElement("div"); grid.className = "grid"; list.appendChild(grid);
    }
    const cell = document.createElement("div"); cell.className = "cell";
    const img = document.createElement("img"); img.loading = "lazy"; img.alt = ""; img.src = "t/" + it.id;
    cell.appendChild(img);
    if (it.v) { const v = document.createElement("span"); v.className = "v"; v.textContent = "▶"; cell.appendChild(v); }
    const idx = rendered; cell.onclick = () => openViewer(idx);
    grid.appendChild(cell);
  }
}
function show(ids) {
  const set = ids ? new Set(ids) : null;
  SHOWN = set ? ALL.filter(x => set.has(x.id)) : ALL;
  COUNTS = {}; for (const x of SHOWN) { const k = x.d ? x.d.slice(0, 10) : ""; COUNTS[k] = (COUNTS[k] || 0) + 1; }
  rendered = 0; lastDay = null; $("list").textContent = "";
  if (!SHOWN.length) { const h = document.createElement("div"); h.className = "hint"; h.textContent = T.empty; $("list").appendChild(h); }
  renderMore();
}
new IntersectionObserver(es => { if (es.some(e => e.isIntersecting) && rendered < SHOWN.length) renderMore(); },
                         { rootMargin: "1200px" }).observe($("more"));
if (CFG.browse) fetch("api/photos").then(r => r.json()).then(data => {
  ALL = data.items;
  const sel = $("albumSel"); const o = document.createElement("option"); o.value = ""; o.textContent = T.all; sel.appendChild(o);
  data.albums.forEach((a, i) => { const x = document.createElement("option"); x.value = i; x.textContent = a.n + "  (" + a.ids.length + ")"; sel.appendChild(x); });
  sel.onchange = () => show(sel.value === "" ? null : data.albums[+sel.value].ids);
  if (!data.albums.length) sel.style.display = "none";
  show(null);
});

// ---- 大图 ----
let cur = -1;
function openViewer(i) {
  cur = i; const it = SHOWN[i]; if (!it) return;
  const st = $("stage"); st.textContent = "";
  let el;
  if (it.v) { el = document.createElement("video"); el.controls = true; el.playsInline = true; el.src = "o/" + it.id; }
  else { el = document.createElement("img"); el.src = "p/" + it.id; el.alt = ""; }
  st.appendChild(el);
  $("vname").textContent = it.n; $("vdl").href = "o/" + it.id + "?dl=1";
  $("viewer").className = "on";
}
function closeViewer() { $("viewer").className = ""; $("stage").textContent = ""; }
const step = d => { if (cur + d >= 0 && cur + d < SHOWN.length) openViewer(cur + d); };
$("vclose").onclick = closeViewer; $("prev").onclick = () => step(-1); $("next").onclick = () => step(1);
document.addEventListener("keydown", e => {
  if (!$("viewer").className) return;
  if (e.key === "Escape") closeViewer(); else if (e.key === "ArrowLeft") step(-1); else if (e.key === "ArrowRight") step(1);
});
let tx = null;
$("stage").addEventListener("touchstart", e => { tx = e.touches[0].clientX; }, { passive: true });
$("stage").addEventListener("touchend", e => {
  if (tx === null) return; const dx = e.changedTouches[0].clientX - tx; tx = null;
  if (Math.abs(dx) > 50) step(dx < 0 ? 1 : -1);
});

// ---- 上传 ----
let queue = [], busy = false, ok = 0, total = 0;
$("upinput").onchange = e => {
  for (const f of e.target.files) {
    const row = document.createElement("div"); row.className = "row";
    const n = document.createElement("div"); n.className = "n"; n.textContent = f.name;
    const s = document.createElement("div"); s.className = "s"; s.textContent = T.waiting;
    const bar = document.createElement("div"); bar.className = "bar"; const fill = document.createElement("i"); bar.appendChild(fill);
    row.append(n, s, bar); $("rows").prepend(row);
    queue.push({ f, s, fill }); total++;
  }
  e.target.value = ""; summary(); next();
};
function summary() { $("upsum").textContent = total ? fmt(T.summary, ok, total) : ""; }
function next() {
  if (busy || !queue.length) return;
  busy = true; const job = queue.shift();
  const x = new XMLHttpRequest();
  x.open("PUT", "up?name=" + encodeURIComponent(job.f.name) + "&mtime=" + (job.f.lastModified || 0));
  x.upload.onprogress = e => { if (e.lengthComputable) job.fill.style.width = (100 * e.loaded / e.total) + "%"; };
  x.onload = () => {
    let r = {}; try { r = JSON.parse(x.responseText); } catch (_) {}
    if (x.status === 200 && r.ok) { ok++; job.s.textContent = r.dup ? T.dup : T.done; job.fill.style.width = "100%"; }
    else job.s.textContent = T.failed + (r.error ? "：" + r.error : "");
    busy = false; summary(); next();
  };
  x.onerror = () => { job.s.textContent = T.failed; busy = false; summary(); next(); };
  x.send(job.f);
}
</script>
</body>
</html>
"""
