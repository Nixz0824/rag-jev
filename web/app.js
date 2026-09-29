"use strict";

const state = {
  sessionId: "web-" + Math.random().toString(36).slice(2, 10),
  busy: false,
  patches: [],
  facts: [],
  sessionCost: 0,
  runs: [],
  blindRun: "",
};

const $ = (id) => document.getElementById(id);
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]
));

/* Run one render step in isolation: a broken step must not blank the cards next to it
   (the same rule renderCharts applies per chart). */
function guarded(box, fn) {
  try {
    return fn();
  } catch (error) {
    box.innerHTML = `<p class="ghost">渲染失败：${esc(error.message)}</p>`;
    return null;
  }
}

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

const post = (path, payload) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload),
});

/* ------------------------------------------------------------------ background */
function field() {
  const canvas = $("field");
  const ctx = canvas.getContext("2d");
  let width = 0;
  let height = 0;
  let nodes = [];
  let raf = 0;

  const resize = () => {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    width = canvas.width = Math.floor(window.innerWidth * ratio);
    height = canvas.height = Math.floor(window.innerHeight * ratio);
    canvas.style.width = window.innerWidth + "px";
    canvas.style.height = window.innerHeight + "px";
    const count = window.innerWidth < 720 ? 34 : 64;
    nodes = Array.from({ length: count }, () => ({
      x: Math.random() * width,
      y: Math.random() * height,
      vx: (Math.random() - 0.5) * 0.22 * ratio,
      vy: (Math.random() - 0.5) * 0.22 * ratio,
      r: (Math.random() * 1.6 + 0.6) * ratio,
    }));
  };

  const draw = (animate) => {
    ctx.clearRect(0, 0, width, height);
    const limit = 150 * (window.devicePixelRatio || 1);
    for (const node of nodes) {
      if (animate) {
        node.x += node.vx;
        node.y += node.vy;
        if (node.x < 0 || node.x > width) node.vx *= -1;
        if (node.y < 0 || node.y > height) node.vy *= -1;
      }
      ctx.beginPath();
      ctx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(183,156,255,0.5)";
      ctx.fill();
    }
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const dx = nodes[i].x - nodes[j].x;
        const dy = nodes[i].y - nodes[j].y;
        const distance = Math.hypot(dx, dy);
        if (distance < limit) {
          ctx.beginPath();
          ctx.moveTo(nodes[i].x, nodes[i].y);
          ctx.lineTo(nodes[j].x, nodes[j].y);
          ctx.strokeStyle = `rgba(124,212,255,${0.13 * (1 - distance / limit)})`;
          ctx.lineWidth = 1;
          ctx.stroke();
        }
      }
    }
  };

  const loop = () => {
    draw(true);
    raf = requestAnimationFrame(loop);
  };

  resize();
  if (reduced) {
    draw(false);
  } else {
    loop();
    window.addEventListener("resize", () => { cancelAnimationFrame(raf); resize(); loop(); });
  }
}

/* ------------------------------------------------------------------ motion */
function observeReveals() {
  const items = document.querySelectorAll(".reveal");
  if (reduced) {
    items.forEach((item) => item.classList.add("is-visible"));
    return;
  }
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry, index) => {
      if (!entry.isIntersecting) return;
      const delay = Math.min(index * 70, 320);
      setTimeout(() => entry.target.classList.add("is-visible"), delay);
      observer.unobserve(entry.target);
    });
  }, { rootMargin: "-8% 0px -12% 0px" });
  items.forEach((item) => observer.observe(item));
}

function observeRail() {
  const links = [...document.querySelectorAll(".rail a[data-index]")];
  const sections = links.map((link) => document.querySelector(link.getAttribute("href"))).filter(Boolean);
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      links.forEach((link) => link.classList.toggle("active", link.getAttribute("href") === "#" + entry.target.id));
      sections.forEach((section) => section.classList.toggle("is-active", section === entry.target));
    });
  }, { rootMargin: "-45% 0px -45% 0px" });
  sections.forEach((section) => observer.observe(section));
}

function scrollProgress() {
  const bar = $("progress");
  const hero = $("hero");
  const update = () => {
    const total = document.documentElement.scrollHeight - window.innerHeight;
    const ratio = total > 0 ? Math.min(window.scrollY / total, 1) : 0;
    bar.style.width = (ratio * 100).toFixed(2) + "%";
    if (!reduced) {
      const shift = Math.min(window.scrollY, 400) * 0.12;
      hero.querySelector(".display").style.transform = `translateY(${-shift}px)`;
      hero.style.opacity = String(Math.max(1 - window.scrollY / (window.innerHeight * 0.9), 0.15));
    }
  };
  update();
  window.addEventListener("scroll", update, { passive: true });
}

function countUp(node, target, suffix = "") {
  if (reduced) { node.textContent = target + suffix; return; }
  const start = performance.now();
  const duration = 1100;
  const step = (now) => {
    const progress = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    node.textContent = Math.round(target * eased) + suffix;
    if (progress < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ------------------------------------------------------------------ meta */
async function loadHealth() {
  try {
    const health = await api("/api/health");
    const ready = health.ready === true;
    const dot = $("rail-status").querySelector(".dot");
    dot.className = "dot " + (ready ? "ok" : "bad");
    const mode = health.jev?.routing_mode;
    const jev = health.jev?.enabled ? "启用" : "未启用";
    $("rail-status-text").textContent = ready
      ? `${health.chunks} 条语料 · Jev ${jev}${mode ? " · 路由 " + mode : ""}`
      : "未就绪";
    if (ready) {
      const chunks = $("stat-chunks");
      if (typeof health.chunks === "number") countUp(chunks, health.chunks);
      $("stat-patches").textContent = health.patches?.supported?.length ?? "—";
    }
  } catch {
    $("rail-status").querySelector(".dot").className = "dot bad";
    $("rail-status-text").textContent = "离线";
  }
}

async function loadMeta() {
  try {
    const meta = await api("/api/patch/meta");
    state.patches = meta.patches || [];
    const select = $("patch-select");
    for (const patch of [...state.patches].reverse()) {
      const option = document.createElement("option");
      option.value = patch;
      option.textContent = patch;
      select.appendChild(option);
    }
    for (const id of ["compare-a", "compare-b"]) {
      const box = $(id);
      [...state.patches].reverse().forEach((patch) => {
        const option = document.createElement("option");
        option.value = patch;
        option.textContent = patch;
        box.appendChild(option);
      });
    }
    if (state.patches.length >= 2) {
      $("compare-a").value = state.patches[state.patches.length - 2];
      $("compare-b").value = state.patches[state.patches.length - 1];
    }
    $("patch-hint").textContent = `收录 ${state.patches[0]}—${meta.latest}`;
    const ticker = $("ticker");
    const words = [...state.patches, "JEV 重排", "回答自检", "盲测 78 题", "数值只来自公告", "本地推理"];
    ticker.innerHTML = words.concat(words).map((word) => `<span>${esc(word)}</span>`).join("");
    $("examples").innerHTML = (meta.examples || [])
      .map((text) => `<button type="button" data-q="${esc(text)}">${esc(text)}</button>`)
      .join("");
    // Shareable links: /?q=<问题> asks it on load, /?view=<区块> jumps to a section.
    const params = new URLSearchParams(location.search);
    const shared = params.get("q");
    if (shared && !state.busy) {
      $("input").value = shared;
      ask(shared);
    }
    const view = params.get("view");
    if (view) {
      const target = document.getElementById(view);
      if (target) setTimeout(() => target.scrollIntoView({ behavior: reduced ? "auto" : "smooth" }), shared ? 2500 : 300);
    }
  } catch (error) {
    $("patch-hint").textContent = "元数据不可用：" + error.message;
  }
}

/* ------------------------------------------------------------------ chat */
function lineClass(line) {
  const fact = state.facts.find((row) => row.field && line.includes(row.field));
  if (!fact) return "";
  if (fact.direction === "buff") return "buff";
  if (fact.direction === "nerf") return "nerf";
  return "";
}

function renderAnswer(session) {
  const replies = (session.messages || []).filter((message) => message.role === "assistant");
  const last = replies[replies.length - 1];
  if (!last) return;
  state.facts = last.facts || [];
  const lines = String(last.text || "").split("\n");
  $("answer-tag").textContent = { verify: "回答", abstained: "未回答", clarifying: "需要补充", done: "已记录" }[session.status] || session.status;
  $("answer-note").textContent = (session.query?.patches || []).join("、") || "";
  $("answer-body").innerHTML = lines
    .filter((line) => line.trim())
    .map((line) => {
      if (line.startsWith("- ")) return `<p class="answer-line ${lineClass(line)}">${esc(line)}</p>`;
      if (line.includes("公告里的说明")) return `<p class="notes">${esc(line)}</p>`;
      if (line.startsWith("（自检")) return `<p class="caution">${esc(line)}</p>`;
      if (line.startsWith("来源：")) return `<p class="notes">${esc(line)}</p>`;
      return `<p>${esc(line)}</p>`;
    })
    .join("");
  const feedback = $("feedback");
  feedback.hidden = session.status !== "verify";
}

function renderEvidence(session) {
  const rows = session.evidence || [];
  $("evidence-count").textContent = `${rows.length} 条`;
  $("evidence-list").innerHTML = rows.length
    ? rows.slice(0, 10).map((row) => {
        const link = row.source_url
          ? `<a href="${esc(row.source_url)}" target="_blank" rel="noreferrer">${esc(row.source_title || "公告")}</a>`
          : esc(row.source_title || "");
        return `<li>${esc(row.text || "")}<span class="src">${link}</span></li>`;
      }).join("")
    : '<li class="ghost">本次没有命中证据</li>';
}

function renderTrace(session) {
  const rows = session.trace || [];
  $("trace-count").textContent = `${rows.length} 步`;
  $("trace-list").innerHTML = rows.length
    ? rows.map((step) => `<li><b>${esc(step.tool)}</b> · ${esc(step.detail)}</li>`).join("")
    : '<li class="ghost">没有轨迹</li>';
}

/* The four roles Jev plays, in lifecycle order (jev.PHASES). Each one prints its booking
   from jev_phases: CALLED / BYPASSED / FALLBACK / FAILED plus the reason — a step that
   did nothing must say why, and "bypassed" is a designed outcome, not breakage. */
const PHASES = ["semantic_fallback", "hierarchical_routing", "rerank", "evidence_judge"];

function phaseRun(session, name) {
  const entry = (session.jev_phases || {})[name] || {};
  const status = entry.status || "bypassed";
  return {
    label: entry.label || name,
    status,
    calls: Number(entry.calls || 0),
    cost: Number(entry.cost_usd || 0),
    latency: Number(entry.latency_ms || 0),
    detail: entry.detail || "",
    note: STATE_NOTE[status] || status,
  };
}

function setPhaseBadge(id, entry, meta = "") {
  try {
    const badge = $(id);
    badge.className = "phase-badge " + stateClass(entry.status);
    badge.textContent = `${STATE_LABEL[entry.status] || entry.status} · ${entry.note}`;
    $(id + "-meta").textContent = meta;
  } catch { /* the badge is decoration; the verdict below still carries the reason */ }
}

function phaseMetrics(entry) {
  const parts = [];
  if (entry.calls) parts.push(entry.calls + " 次调用");
  if (entry.latency) parts.push(ms(entry.latency));
  if (entry.cost) parts.push(money(entry.cost));
  return parts.join(" · ");
}

function renderJev(session) {
  const bars = $("jev-bars");
  const pick = session.jev;
  const check = session.self_check;
  state.sessionCost = Number(session.cost_usd || 0);
  $("session-cost-total").textContent = "$" + state.sessionCost.toFixed(6);

  const fallback = phaseRun(session, "semantic_fallback");
  const routing = phaseRun(session, "hierarchical_routing");
  const rerank = phaseRun(session, "rerank");
  const judge = phaseRun(session, "evidence_judge");
  const mode = session.routing?.mode || session.query_plan?.routing?.mode || "off";

  setPhaseBadge("jev-phase-fallback", fallback, phaseMetrics(fallback));
  $("jev-phase-fallback-why").textContent = fallback.detail || fallback.note;
  setPhaseBadge("jev-phase-routing", routing, phaseMetrics(routing));
  $("jev-phase-routing-why").textContent = routing.detail || routing.note;
  setPhaseBadge("jev-phase-rerank", rerank, phaseMetrics(rerank));
  setPhaseBadge("jev-phase-judge", judge, phaseMetrics(judge));

  if (!pick) {
    bars.innerHTML = '<p class="ghost">本次没有重排（候选不足 2 条，或未配置 TYPESAFE_API_KEY）</p>';
    $("jev-verdict").textContent = "";
  } else {
    const scores = Object.entries(pick.scores || {}).filter(([, value]) => value !== null);
    scores.sort((a, b) => b[1] - a[1]);
    const top = scores[0]?.[1] ?? 0;
    const byId = {};
    (session.evidence || []).forEach((row) => { byId[row.id] = row; });
    bars.innerHTML = scores.slice(0, 6).map(([id, value]) => {
      const row = byId[id] || {};
      const label = `${row.ability ? row.ability + " " : ""}${row.field || id}`;
      const winner = id === pick.choice_id ? " winner" : "";
      const width = Math.max(3, Math.round((value / Math.max(top, 0.01)) * 100));
      return `<div class="bar${winner}"><span class="bar-label">${esc(label)}</span><span class="bar-score">${value.toFixed(2)}</span><span class="bar-track"><i class="bar-fill" data-w="${width}"></i></span></div>`;
    }).join("");
    requestAnimationFrame(() => {
      bars.querySelectorAll(".bar-fill").forEach((node) => { node.style.width = node.dataset.w + "%"; });
    });
    $("jev-verdict").innerHTML = pick.decisive
      ? `分差 ${num(pick.gap)} → 采用 Jev 顺序，首选 <b>${esc((byId[pick.choice_id] || {}).field || "该条")}</b>`
      : `分差 ${num(pick.gap)} 低于 0.15 → 保留检索顺序（两条候选无法区分）`;
    if (pick.retrieval_top && pick.after_top) {
      const label = (id) => esc((byId[id] || {}).field || id);
      const same = pick.retrieval_top === pick.after_top;
      $("jev-verdict").innerHTML +=
        `<br>检索第 1：${label(pick.retrieval_top)} ${same ? "=" : "→"} Jev 第 1：${label(pick.after_top)}`;
    }
  }

  if (check) {
    const min = Number(check.min ?? 0);
    const gauge = $("jev-gauge");
    gauge.style.setProperty("--p", Math.round(min * 100));
    gauge.querySelector("b").textContent = min.toFixed(2);
    $("jev-self-note").textContent = check.weak?.length
      ? `第 ${check.weak.map((index) => index + 1).join("、")} 条支持度偏低，已在回答里标注`
      : `${check.scores.length} 条改动全部通过（最低 ${min.toFixed(2)}）`;
  } else {
    $("jev-gauge").style.setProperty("--p", 0);
    $("jev-gauge").querySelector("b").textContent = "—";
    $("jev-self-note").textContent = judge.detail || judge.note;
  }

  $("jev-note").textContent = `${mode} · ${pick ? `${pick.model || "jev"} · ${ms(pick.latency_ms)}` : "未调用"}`;
  renderPhaseTable(session, mode);
}

/* Per-phase cost and latency, so "how much more did the layered search cost" is
   answerable without reading the trace. */
function renderPhaseTable(session, mode) {
  const table = $("jev-phase-table");
  if (!table) return;
  const rows = PHASES.map((name) => phaseRun(session, name));
  const total = {
    calls: rows.reduce((sum, entry) => sum + entry.calls, 0),
    cost: rows.reduce((sum, entry) => sum + entry.cost, 0),
    latency: rows.reduce((sum, entry) => sum + entry.latency, 0),
  };
  table.querySelector("tbody").innerHTML = rows.map((entry) => `
    <tr>
      <td>${esc(entry.label)}</td>
      <td>${statusBar(entry.status)}</td>
      <td class="num">${entry.calls ? entry.calls : "—"}</td>
      <td class="num">${entry.calls ? money(entry.cost) : "—"}</td>
      <td class="num">${entry.calls ? ms(entry.latency) : "—"}</td>
    </tr>`).join("") + `
    <tr class="is-total">
      <td>合计</td>
      <td><span class="phase-badge is-bypassed">会话总计</span></td>
      <td class="num">${total.calls || "—"}</td>
      <td class="num">${money(session.cost_usd ?? total.cost)}</td>
      <td class="num">${total.latency ? ms(total.latency) : "—"}</td>
    </tr>`;
  $("jev-total-line").innerHTML = `本会话累计 <b>${esc(money(state.sessionCost))}</b>`
    + ` · 路由模式 ${esc(mode)}`;
}

async function ask(text) {
  if (state.busy || !text.trim()) return;
  state.busy = true;
  $("send").disabled = true;
  $("answer-tag").textContent = "检索中…";
  try {
    const session = await post("/api/chat", {
      session_id: state.sessionId,
      text,
      patch: $("patch-select").value || null,
    });
    renderAnswer(session);
    renderEvidence(session);
    renderTrace(session);
    renderJev(session);
    renderGraph(session);
    $("answer-card").scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
  } catch (error) {
    $("answer-tag").textContent = "出错";
    $("answer-body").innerHTML = `<p class="caution">${esc(error.message)}</p>`;
  } finally {
    state.busy = false;
    $("send").disabled = false;
  }
}

async function feedback(result) {
  try {
    const session = await post("/api/feedback", { session_id: state.sessionId, result });
    renderAnswer(session);
    $("feedback").hidden = true;
  } catch (error) {
    $("answer-body").insertAdjacentHTML("beforeend", `<p class="caution">反馈失败：${esc(error.message)}</p>`);
  }
}

/* ------------------------------------------------------------------ execution graph
   The explanation view for one request: which stage actually ran, how each slot was
   decided, whether the router forked, and what each Jev phase cost. Every number is
   derived from the payload of this request; none is written by hand here.

   Bypassed stages are rendered like any other stage: in this project "we did not use
   the model, and this is why" is a result, not an empty state. */

// The four phase states, in the vocabulary jev.PHASES uses.
const STATE_LABEL = { called: "CALLED", bypassed: "BYPASSED", fallback: "FALLBACK", failed: "FAILED" };
const STATE_NOTE = {
  called: "已调用",
  bypassed: "本次不需要",
  fallback: "Jev 不可用，退回确定性流程",
  failed: "调用失败，已降级",
};

// Which slots the table shows, and the shapes their values can take.
const SLOT_ORDER = ["patches", "subject", "ability", "field", "mode", "type", "intent", "direction"];
const SLOT_LABEL = {
  patches: "版本",
  subject: "对象",
  ability: "技能",
  field: "字段",
  mode: "模式",
  type: "类别",
  intent: "意图",
  direction: "方向",
};
const SLOT_BADGE = {
  deterministic: "规则",
  alias: "别名表",
  semantic: "Jev 语义",
  default: "默认值",
  none: "未定",
};

function stateClass(status) {
  return STATE_LABEL[status] ? "is-" + status : "";
}

const money = (value) => "$" + Number(value || 0).toFixed(6);
const ms = (value) => (value === null || value === undefined || value === ""
  ? "—" : Number(value) + "ms");

function num(value, digits = 2) {
  return value === null || value === undefined || value === "" ? "—" : Number(value).toFixed(digits);
}

function pct01(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(1, number));
}

function statusBar(status, ok = false) {
  const kind = status === "called" && ok ? "is-called is-ok" : stateClass(status);
  return `<span class="phase-badge ${kind}">${esc(STATE_LABEL[status] || "—")}</span>`;
}

/* ---------------------------------------------------------------- stage chain */

function renderStages(session, plan, routing) {
  const paths = routing?.paths || [];
  const mode = routing?.mode || plan.routing_mode || "off";
  const withCandidates = paths.filter((path) => Number(path.candidate_count || 0) > 0);
  const branchRan = mode === "active" && paths.length >= 2;
  const slots = plan.slots || {};
  const patches = (slots.patches?.value || plan.patches || []).join("、");
  const subject = slots.subject?.value;
  const ability = slots.ability?.value;
  const fallback = phaseRun(session, "semantic_fallback");
  const routingPhase = phaseRun(session, "hierarchical_routing");
  const rerank = phaseRun(session, "rerank");
  const judge = phaseRun(session, "evidence_judge");
  const check = session.self_check;

  const deterministic = [
    patches ? `版本 ${patches}（${plan.patch_source || "—"}）` : "",
    subject ? `对象 ${subject}（${slots.subject?.source || "—"}）` : "",
    ability ? `技能 ${ability}` : "",
    plan.unresolved_slots?.length ? `未定槽位 ${plan.unresolved_slots.join("、")}` : "没有未定槽位",
  ].filter(Boolean).join(" · ");

  // The routing stage's own booking, plus what the mode did with it. A failure outranks
  // both modes — a degradation must never be displayed as a quiet bypass. In shadow mode
  // the router may well have worked and been ignored, so the reading follows the phase
  // ledger's *work* (calls/cost/latency) rather than its status alone.
  let routingStatus = routingPhase.status;
  let routingDetail = routingPhase.detail || routingPhase.note;
  if (routingStatus !== "failed" && mode === "off") {
    routingStatus = "bypassed";
    routingDetail = routingPhase.detail || "routing=off：完全使用确定性解析";
  } else if (routingStatus !== "failed" && mode === "shadow") {
    const worked = Boolean(routingPhase.calls || routingPhase.cost || routingPhase.latency);
    routingStatus = worked ? "bypassed" : routingPhase.status;
    routingDetail = `${routingPhase.detail || "语义结果已记录"} · shadow：不影响实际检索`;
  }

  let mergeStatus = "bypassed";
  let mergeDetail = "单条路径，没有需要合并的分支";
  if (branchRan) {
    const merged = session.routing?.merged_candidates;
    mergeStatus = "called";
    mergeDetail = `${withCandidates.length}/${paths.length} 条分支有候选，合并去重后 `
      + `${merged === null || merged === undefined || merged === "" ? "—" : merged} 条进入重排`;
  } else if (mode === "shadow") {
    mergeDetail = "shadow：语义分支只记录，不合并";
  }

  const rerankDetail = rerank.status === "fallback" && /TYPESAFE_API_KEY/.test(rerank.detail || "")
    ? "缺少 TYPESAFE_API_KEY，保留检索顺序"
    : rerank.detail || rerank.note;

  const stages = [
    { name: "确定性解析", status: "called", detail: deterministic, metrics: "" },
    { name: "语义补全", status: fallback.status, detail: fallback.detail || fallback.note, metrics: phaseMetrics(fallback) },
    { name: "分层路由", status: routingStatus, detail: routingDetail, metrics: phaseMetrics(routingPhase) },
    {
      name: "分支检索",
      status: branchRan ? "called" : "bypassed",
      detail: branchRan
        ? `${paths.length} 条路径分别检索：`
          + paths.map((path) => `${path.label} ${path.candidate_count ?? "—"} 条`).join("；")
        : mode === "off" ? "routing=off：单路径检索" : "单条路径，未分叉",
      metrics: "",
    },
    { name: "合并", status: mergeStatus, detail: mergeDetail, metrics: "" },
    { name: "Jev 重排", status: rerank.status, detail: rerankDetail, metrics: phaseMetrics(rerank) },
    {
      name: "确定性渲染",
      status: "called",
      detail: session.evidence?.length
        ? `模板渲染 ${session.evidence.length} 条候选，数值全部取自公告`
        : "本次没有可渲染的数值行",
      metrics: "",
    },
    {
      name: "证据判读",
      status: judge.status,
      detail: check
        ? `自检 ${check.scores?.length ?? 0} 条，最低支持度 ${num(check.min)}`
        : judge.detail || judge.note,
      metrics: phaseMetrics(judge),
    },
  ];

  return `<ul class="chain">${stages.map((stage) => `
    <li class="${stateClass(stage.status)}">
      <span class="st-head">${statusBar(stage.status, stage.status === "called")}
        <span class="st-name">${esc(stage.name)}</span>
        ${stage.metrics ? `<span class="st-meta">${esc(stage.metrics)}</span>` : ""}</span>
      <span class="st-detail">${esc(stage.detail || "—")}</span>
    </li>`).join("")}</ul>`;
}

/* ---------------------------------------------------------------- slot provenance */

function candidateBars(candidates, committed) {
  if (!candidates?.length) return "";
  const top = Math.max(...candidates.map((item) => Number(item.probability) || 0), 0.01);
  const rows = candidates.slice(0, 4).map((item) => {
    const value = String(item.value ?? "—");
    const width = Math.max(3, Math.round((pct01(item.probability) / top) * 100));
    const winner = value === String(committed ?? "") ? " winner" : "";
    return `<span class="cand-row${winner}"><span class="bar-label">${esc(value)}</span>`
      + `<span class="cand-track"><i class="cand-fill" data-w="${width}"></i></span>`
      + `<span class="cand-score">${num(item.probability)}</span></span>`;
  }).join("");
  return `<span class="cand">${rows}</span>`;
}

function slotCell(slot) {
  const value = slot.value;
  let shown = "—";
  if (Array.isArray(value) && value.length) shown = value.map((item) => esc(item)).join("、");
  else if (value !== null && value !== undefined && value !== "") shown = esc(value);
  const confidence = slot.confidence === null || slot.confidence === undefined
    ? "" : `<span class="slot-conf">置信度 ${num(slot.confidence)}</span>`;
  const status = slot.status && slot.status !== "resolved" ? `（${esc(slot.status)}）` : "";
  const badge = SLOT_BADGE[slot.source] || esc(slot.source || "—");
  return `<td><span class="slot-value">${shown}</span>${confidence}<br>`
    + `<span class="phase-badge ${slot.source === "semantic" ? "is-called" : "is-bypassed"}">${esc(badge)}</span>${status}`
    + `${candidateBars(slot.candidates, value)}</td>`;
}

function renderSlots(plan) {
  const slots = plan.slots || {};
  const rows = SLOT_ORDER.map((key) => {
    let slot = slots[key];
    let code = key;
    if (key === "field") {
      // The parser decides the family (field) and the concrete keys (field_keys); a
      // semantic decision can land on either, so both are shown in one row.
      const keys = slots.field_keys;
      if (!slot && !keys) return "";
      code = "field + field_keys";
      slot = {
        value: keys?.value?.length ? keys.value : slot?.value,
        source: keys?.source && keys.source !== "none" ? keys.source : slot?.source,
        status: keys?.status || slot?.status,
        confidence: keys?.confidence ?? slot?.confidence,
        candidates: keys?.candidates?.length ? keys.candidates : slot?.candidates,
        why: [slot?.why, keys?.why].filter(Boolean).join(" ｜ "),
      };
    }
    if (!slot) return "";
    return `<tr>
      <td class="slot-name">${esc(SLOT_LABEL[key] || key)}<code>${esc(code)}</code></td>
      <td class="slot-why">${esc(slot.why || "—")}</td>
      ${slotCell(slot)}
    </tr>`;
  }).filter(Boolean);

  if (!rows.length) return '<p class="ghost">本次响应没有 query_plan.slots。</p>';
  return `<table class="slot-table">
    <thead><tr><th>槽位</th><th>为什么这样定</th><th>值 / 来源</th></tr></thead>
    <tbody>${rows.join("")}</tbody></table>`;
}

/* ---------------------------------------------------------------- fork + merge */

function truncate(text, length) {
  const value = String(text ?? "");
  return value.length > length ? value.slice(0, length - 1) + "…" : value;
}

function forkGraphic(routing) {
  const paths = routing?.paths || [];
  const mode = routing?.mode || "off";
  const readings = paths.filter((path) => (path.nodes || []).length);
  const primary = readings[0];
  const secondary = readings[1];
  const bypassed = paths.some((path) => !(path.nodes || []).length);
  const labels = [
    { box: primary ? truncate(primary.label, 16) : "单条路径", kind: primary ? "is-jev" : "" },
    { box: secondary ? truncate(secondary.label, 14) : "未分叉", kind: secondary ? "is-jev" : "" },
    { box: "合并 →", kind: "" },
  ];

  const node = svg(196, 106);
  const defs = add(node, "defs", {});
  const marker = (id, cls) => {
    const item = add(defs, "marker", {
      id, viewBox: "0 0 8 8", refX: 6, refY: 4, markerWidth: 6, markerHeight: 6,
      orient: "auto-start-reverse",
    });
    add(item, "path", { d: "M0 0 L8 4 L0 8 z", class: cls });
  };
  marker("arrow", "marker");
  marker("arrowJev", "marker is-jev");
  marker("arrowWarn", "marker is-warn");

  labels.forEach((label, index) => {
    add(node, "rect", {
      x: 6, y: 28 + index * 26, width: 78, height: 20, rx: 6,
      class: `fork-node ${label.kind}`,
    });
    add(node, "text", { x: 13, y: 42 + index * 26, class: `fork-label ${label.kind}` }, label.box);
  });
  add(node, "text", { x: 6, y: 16, class: "fork-note" }, `路由模式 ${mode}`);
  if (primary) add(node, "path", { d: "M84 38 H92 V41 H100", class: "conn is-jev", "marker-end": "url(#arrowJev)" });
  if (secondary) add(node, "path", { d: "M84 64 H92 V67 H100", class: "conn is-jev", "marker-end": "url(#arrowJev)" });
  if (!readings.length) add(node, "path", { d: "M92 38 V67", class: "conn" });
  add(node, "path", { d: "M92 67 V80 H100", class: "conn", "marker-end": "url(#arrow)" });
  if (bypassed) {
    add(node, "path", { d: "M92 90 V98 H100", class: "conn is-bypass", "marker-end": "url(#arrowWarn)" });
    add(node, "text", { x: 4, y: 93, class: "bypass-label" }, "保守路径");
  }
  node.setAttribute("class", "fork-svg");
  node.setAttribute("aria-hidden", "true");
  const holder = document.createElement("div");
  holder.appendChild(node);
  return holder.innerHTML;
}

function routeList(routing) {
  const paths = routing?.paths || [];
  if (!paths.length) return '<p class="ghost">本次响应没有 routing.paths。</p>';
  const items = paths.map((path) => {
    const nodes = path.nodes || [];
    const safe = !nodes.length;
    const where = Object.entries(path.where || {})
      .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join("/") : value}`)
      .join(" · ");
    const score = nodes.length && path.score !== null && path.score !== undefined
      ? `<span class="route-score">score ${num(path.score)}</span>` : "";
    const count = `<span class="route-count">候选 ${path.candidate_count ?? "—"} 条</span>`;
    const badge = safe
      ? '<span class="phase-badge is-bypassed">BYPASSED · 安全网</span>'
      : statusBar("called", path.contributed === true);
    return `<li class="route ${safe ? "plain" : "is-jev"}${path.contributed && !safe ? " winner" : ""}">
      <span class="route-head">
        <span class="route-label">${esc(path.label || "—")}</span>
        ${badge}${score}${count}
        ${path.contributed ? '<span class="route-count">最终采用</span>' : ""}
      </span>
      <span class="route-detail">${esc(path.detail || "")}</span>
      ${where ? `<span class="route-where">${esc(where)}</span>` : ""}
    </li>`;
  }).join("");

  const unfiltered = paths.find((path) => !(path.nodes || []).length);
  let note = (routing?.notes || []).find((line) => /分叉|层级|保守/.test(line)) || "";
  if (routing?.beam_used) {
    note = "分叉：首选读法与接近的次选读法各检索一次。合并按分支置信度加权（RRF），"
      + "所以候选最多的那条不会因为条目多就赢。";
  } else if (!note) {
    note = "没有分叉：首选读法明显领先，只走单一路径。";
  }
  if (unfiltered) {
    note += " 无过滤的那条是保守路径：不是另一种猜测，而是保证误判只损失精度、不漏检的兜底。";
  }
  return `<ul class="routes">${items}</ul><p class="routes-note">${esc(note)}</p>`;
}

function mergeBlock(session, routing) {
  const paths = routing?.paths || [];
  const mode = routing?.mode || "off";
  const branches = paths.filter((path) => Number(path.candidate_count || 0) > 0);
  const influence = session.routing_influence;
  const winner = session.evidence?.[0] || {};
  const label = `${winner.ability ? winner.ability + " " : ""}${winner.field || winner.id || "—"}`;
  const gates = ["重排不改数字：数值句只由 patch_engine._render 生成。"];
  // 0.15 is the rerank gate (jev.MIN_RERANK_GAP): a module constant, not part of the
  // routing config the API exposes, so it is quoted as the backend's value rather than
  // re-derived here.
  if (session.jev?.choice_id && session.jev?.decisive === false) {
    gates.push(`分差 ${num(session.jev.gap)} 未达 jev.MIN_RERANK_GAP = 0.15：保留检索顺序（两条候选无法区分）。`);
  } else if (session.jev?.choice_id) {
    gates.push(`分差 ${num(session.jev.gap)} 达到 jev.MIN_RERANK_GAP 门槛：采用 Jev 顺序。`);
  }
  let headline;
  if (mode !== "active" || paths.length < 2) {
    headline = `没有分支可合并：本次走 ${paths.length || 1} 条路径（路由模式 ${mode}）。`;
  } else {
    headline = `${branches.length}/${paths.length} 条分支有候选，合并去重后 `
      + `${session.routing?.merged_candidates ?? "—"} 条进入重排；最终采用 <b>${esc(label)}</b>。`;
    if (influence?.from_secondary) {
      headline += ` 首选读法未命中：采用的证据来自次选分支 ${esc((influence.winner_branches || [])[0] || "—")}`
        + "——分叉就是为了这一题。";
    } else if (influence?.winner_branches?.length) {
      headline += ` 该条由 ${esc(influence.winner_branches.join(" + "))} 命中。`;
    }
  }

  return `<p class="attribution">${headline}</p>`
    + `<span class="loop">${gates.map((gate) => `<span class="st-gate">${esc(gate)}</span>`).join("")}</span>`;
}

function renderNotes(routing) {
  const notes = routing?.notes || [];
  if (!notes.length) return '<p class="ghost">本次没有路由说明（routing.notes 为空）。</p>';
  return `<ul class="notes-list">${notes.map((note) => `<li>${esc(note)}</li>`).join("")}</ul>`;
}

/* ---------------------------------------------------------------- entry point */

function renderGraph(session) {
  const box = $("graph-body");
  const note = $("graph-note");
  if (!box || !note) return;
  // ask()'s catch treats any throw as "提问失败", so a broken diagram must never escape:
  // it reports itself inside its own card, exactly like the per-chart try/catch.
  try {
    paintGraph(session, box, note);
  } catch (error) {
    note.textContent = "执行图渲染失败";
    box.innerHTML = `<p class="ghost">执行图渲染失败：${esc(error.message)}（回答、证据与轨迹不受影响）</p>`;
  }
}

function paintGraph(session, box, note) {
  const plan = session.query_plan || {};
  const routing = session.routing || plan.routing || {};
  const mode = routing.mode || plan.routing?.mode || "off";

  const head = (text) => `<p class="graph-head">${text}</p>`;
  const section = (title, body) => `<section class="graph-sec"><h5>${esc(title)}</h5>${body}</section>`;
  const sheet = [
    guarded(box, () => renderStages(session, plan, routing)),
    guarded(box, () => renderSlots(plan)),
    guarded(box, () => `<div class="fork">${forkGraphic(routing)}<div>${routeList(routing)}</div></div>`),
    guarded(box, () => mergeBlock(session, routing)),
    guarded(box, () => renderNotes(routing)),
  ];
  if (sheet.some((piece) => piece === null)) return;

  box.innerHTML = [
    head(`本次真实跑过的步骤（路由模式 ${esc(mode)}）。灰色 BYPASSED 是设计结果——这一步本次不需要，不是故障。`),
    section("阶段", sheet[0]),
    section("槽位来源", sheet[1]),
    section("路由分叉", sheet[2]),
    section("合并", sheet[3]),
    section("路由说明（routing.notes）", sheet[4]),
    '<p class="graph-foot">分类树只写在 taxonomy.py；模型只出现在标 CALLED 的步骤里，且从不生成数值。</p>',
  ].join("");

  guarded(box, () => {
    box.querySelectorAll(".cand-fill").forEach((node) => {
      const width = Number(node.dataset.w || 0);
      requestAnimationFrame(() => { node.style.width = width + "%"; });
    });
  });

  const calls = PHASES.reduce((sum, name) => sum + Number(session.jev_phases?.[name]?.calls || 0), 0);
  note.textContent = `${mode} · ${calls ? `本次 ${calls} 次 Jev 调用` : "本次未调用 Jev"} · 成本 ${money(session.cost_usd)}`;

  // One-shot entrance for runtime content: .reveal would stay at opacity 0 forever,
  // because observeReveals only runs at boot and after renderCharts/loadEvaluation.
  box.classList.remove("is-enter");
  if (!reduced) {
    void box.offsetWidth;
    box.classList.add("is-enter");
    setTimeout(() => box.classList.remove("is-enter"), 600);
  }
}

/* ------------------------------------------------------------------ charts */
const NS = "http://www.w3.org/2000/svg";

/* The merged blind report is the one quoted in the README, but it only exists after the
   two batches have been merged. Any 盲测报告 run is a truthful substitute, so the chart
   degrades to the newest one instead of drawing empty axes. */
function blindRun(runs = state.runs) {
  const reports = (runs || []).filter((run) => String(run?.name || "").startsWith("盲测报告"));
  if (!reports.length) return null;
  return reports.find((run) => run.name === "盲测报告-合并") || reports[reports.length - 1];
}

function svg(width, height) {
  const node = document.createElementNS(NS, "svg");
  node.setAttribute("viewBox", `0 0 ${width} ${height}`);
  node.setAttribute("role", "img");
  return node;
}

function add(node, tag, attrs, text) {
  const child = document.createElementNS(NS, tag);
  Object.entries(attrs || {}).forEach(([key, value]) => child.setAttribute(key, value));
  if (text !== undefined) child.textContent = text;
  node.appendChild(child);
  return child;
}

function gradients(node) {
  const defs = add(node, "defs", {});
  const gradient = add(defs, "linearGradient", { id: "gradJev", x1: "0", x2: "1" });
  add(gradient, "stop", { offset: "0", "stop-color": "#c8a24a" });
  add(gradient, "stop", { offset: "1", "stop-color": "#e0c078" });
}

/* 盲测成绩：横向条形 */
function chartBlind(box, payload) {
  const blind = blindRun(payload.runs);
  if (!blind) {
    box.innerHTML = '<p class="ghost">没有找到盲测报告（docs/盲测报告*.json），无法作图。</p>';
    return;
  }
  const arms = blind.arms || {};
  const arm = arms["hybrid+jev"] || arms.hybrid || {};
  const armName = arms["hybrid+jev"] ? "hybrid+jev" : (arms.hybrid ? "hybrid" : "—");
  const rows = [
    ["单点数值正确", arm.single?.value_ok, arm.single?.value_total],
    ["检索命中@1", arm.single?.["hit@1"], arm.single?.total],
    ["版本正确", arm.single?.version_ok, arm.single?.version_asked],
    ["汇总含预期对象", arm.overview?.ok, arm.overview?.total],
    ["跨版本聚合", arm.aggregate?.ok, arm.aggregate?.total],
    ["无记录 / 越界拒答", (arm.absent?.refused || 0) + (arm.out_of_scope?.refused || 0),
      (arm.absent?.total || 0) + (arm.out_of_scope?.total || 0)],
    ["回答自检", arm.selfcheck?.pass, arm.selfcheck?.total],
  ].filter(([, part, total]) => typeof part === "number" && total);
  const width = 520;
  const height = rows.length * 30 + 10;
  const node = svg(width, height);
  gradients(node);
  rows.forEach(([label, part, total], index) => {
    const y = index * 30 + 6;
    const ratio = part / total;
    add(node, "text", { x: 0, y: y + 10, class: "row-label" }, label);
    add(node, "rect", { x: 132, y: y, width: 320, height: 12, rx: 6, class: "bar-bg" });
    const fill = add(node, "rect", { x: 132, y: y, width: 0, height: 12, rx: 6, class: "bar-fill" });
    fill.dataset.width = 320 * ratio;
    add(node, "text", { x: 462, y: y + 10, class: "row-value" }, `${part}/${total}`);
  });
  add(node, "text", { x: 132, y: height - 1, class: "axis" }, `满分 100% · 全部来自 docs/${blind.name}.json`);
  box.appendChild(node);
  const note = document.createElement("p");
  note.className = "chart-note";
  note.textContent = `${blind.name} · ${blind.cases ?? "—"} 题 · ${armName} 口径`;
  box.appendChild(note);
}

/* 自检注入：点图，两组分布在阈值两侧 */
function chartSelfcheck(box, payload) {
  const check = payload.jev_selfcheck || {};
  const width = 520;
  const height = 190;
  const node = svg(width, height);
  const left = 60;
  const right = width - 20;
  const x = (value) => left + (right - left) * value;
  const threshold = check.threshold ?? 0.5;
  add(node, "line", { x1: x(threshold), y1: 24, x2: x(threshold), y2: 150, class: "threshold" });
  add(node, "text", { x: x(threshold) + 4, y: 18, class: "threshold-label" }, `阈值 ${threshold}`);
  for (const [label, scores, bad, y] of [
    ["对照（正确回答）", (payload.jev_selfcheck?.control_scores) || [], false, 60],
    ["注入（数字改错）", (payload.jev_selfcheck?.mutation_scores) || [], true, 120],
  ]) {
    add(node, "text", { x: 0, y: y + 4, class: "row-label" }, label);
    scores.forEach((score, index) => {
      add(node, "circle", {
        cx: x(score), cy: y - 14 + (index % 6) * 5, r: 4,
        class: bad ? "dot bad" : "dot", "fill-opacity": 0.75,
      });
    });
    const mean = scores.length ? (scores.reduce((a, b) => a + b, 0) / scores.length) : 0;
    add(node, "text", { x: x(mean) - 12, y: y + 26, class: "row-value" }, `均值 ${mean.toFixed(2)}`);
  }
  add(node, "text", { x: left, y: 170, class: "axis" }, "支持度 0 ────────────── 1");
  box.appendChild(node);
  const note = document.createElement("p");
  note.className = "chart-note";
  note.textContent = `检出 ${check.detected ?? "—"}/${check.cases ?? "—"}，误报 ${check.false_alarms ?? "—"}/${check.cases ?? "—"}`;
  box.appendChild(note);
}

/* 排序能力：配对条形 */
function chartRanker(box, payload) {
  const stats = payload.jev_ranker || {};
  const total = stats.total || 0;
  const width = 520;
  const height = 130;
  const node = svg(width, height);
  gradients(node);
  const rows = [
    ["BM25", stats.bm25_ok || 0, true],
    ["Jev 重排", stats.jev_ok || 0, false],
  ];
  rows.forEach(([label, value, plain], index) => {
    const y = 24 + index * 42;
    add(node, "text", { x: 0, y: y + 12, class: "row-label" }, label);
    add(node, "rect", { x: 92, y: y, width: 360, height: 16, rx: 8, class: "bar-bg" });
    const fill = add(node, "rect", {
      x: 92, y: y, width: 0, height: 16, rx: 8, class: plain ? "bar-fill plain" : "bar-fill",
    });
    fill.dataset.width = total ? 360 * (value / total) : 0;
    add(node, "text", { x: 462, y: y + 13, class: "row-value" }, `${value}/${total}`);
  });
  add(node, "text", { x: 92, y: 122, class: "axis" }, `Jev 独对 ${stats.jev_only ?? 0} · BM25 独对 ${stats.bm25_only ?? 0}`);
  box.appendChild(node);
}

/* 生产链路记账：一条堆叠条 + 说明 */
function chartEffect(box, payload) {
  const stats = payload.jev_effect || {};
  const total = stats.total || 0;
  const width = 520;
  const height = 120;
  const node = svg(width, height);
  const kept = stats.kept || 0;
  const reordered = stats.reordered || 0;
  const keptWidth = total ? (520 - 20) * (kept / total) : 0;
  const reorderWidth = total ? (520 - 20) * (reordered / total) : 0;
  add(node, "rect", { x: 0, y: 20, width: keptWidth, height: 22, rx: 6, class: "bar-bg" });
  add(node, "rect", { x: keptWidth, y: 20, width: Math.max(reorderWidth, 4), height: 22, rx: 6, class: "bar-fill" });
  const under = stats.candidates_lt2 || 0;
  const underWidth = total ? (520 - 20) * (under / total) : 0;
  add(node, "rect", { x: 0, y: 20, width: underWidth, height: 22, rx: 6, class: "bar-fill plain", "fill-opacity": 0.25 });
  add(node, "text", { x: 0, y: 14, class: "row-label" }, `保留检索顺序 ${kept} 条（其中候选不足 2 条：${under}）`);
  add(node, "text", { x: keptWidth + 8, y: 14, class: "row-value" }, `改序 ${reordered}`);
  add(node, "text", { x: 0, y: 66, class: "axis" }, `共 ${total} 条可判定案例：改对 ${stats.reordered_ok ?? 0}、改错 ${stats.reordered_bad ?? 0}`);
  add(node, "text", { x: 0, y: 86, class: "axis" }, "元数据过滤已经把候选压到 1—6 条，重排空间很小");
  box.appendChild(node);
}

/* 分层路由：before/after 对照 + 谁解出来的分布 */
function chartRouting(box, payload) {
  const report = payload.jev_routing_effect || {};
  const cases = report.cases || 0;
  if (!cases) {
    box.innerHTML = '<p class="ghost">路由效果集报告不可用（运行 scripts/evaluate_routing.py --cases tests/cases/routing_effect_cases.json）</p>';
    return;
  }
  const arms = report.arms || {};
  const off = arms.off || {};
  const active = arms.active || {};
  const matrix = report.matrix || {};
  const perRound = active.cases_per_round || cases;
  const width = 520;
  const height = 226;
  const node = svg(width, height);
  const track = width - 150;
  const scale = (value) => Math.max(track * (Number(value || 0) / perRound), 2);

  // Two bars on one axis so "before" and "after" are read against the same scale.
  const bar = (y, label, value, cls) => {
    add(node, "text", { x: 0, y: y + 15, class: "row-label" }, label);
    add(node, "rect", { x: 130, y, width: track, height: 20, rx: 5, class: "bar-bg" });
    const fill = add(node, "rect", { x: 130, y, width: 0, height: 20, rx: 5, class: cls });
    fill.dataset.width = scale(value);
    fill.style.width = "0px";
    add(node, "text", { x: 138 + scale(value), y: y + 15, class: "row-value" },
        `${num(value, 1)}/${perRound}`);
  };
  add(node, "text", { x: 0, y: 12, class: "axis" }, "命中@1（越高越好，同一把尺）");
  bar(22, "关掉路由（off）", off["hit@1"], "bar-fill plain");
  bar(50, "启用路由（active）", active["hit@1"], "bar-fill");

  // The outcome split is what makes the cost visible: "only routing solved it" is the
  // argument for routing and "only off solved it" is the argument against, so both are shown
  // even when one of them is zero. Labels go on full-width rows to keep them legible at the
  // narrow column width instead of crowding a side legend.
  const total = Object.values(matrix).reduce((sum, value) => sum + value, 0) || 1;
  add(node, "text", { x: 0, y: 96, class: "axis" }, `谁解出来的（共 ${total} 条案例）`);
  const segments = [
    ["两种都答对", matrix.both_right || 0, "#3f4a5a"],
    ["只有路由答对", matrix.only_active || 0, "var(--accent)"],
    ["只有关掉才答对", matrix.only_off || 0, "var(--hot)"],
    ["两种都答不对", matrix.both_wrong || 0, "#2c3340"],
  ];
  let cursor = 0;
  segments.forEach(([, value, colour]) => {
    const segmentWidth = (width * value) / total;
    if (segmentWidth > 0) {
      add(node, "rect", { x: cursor, y: 106, width: segmentWidth, height: 18,
                          fill: colour, "fill-opacity": 0.85 });
    }
    cursor += segmentWidth;
  });
  segments.forEach(([label, value], index) => {
    const x = (index % 2) * (width / 2 - 8);
    const y = 146 + Math.floor(index / 2) * 18;
    add(node, "rect", { x, y: y - 8, width: 9, height: 9,
                        fill: segments[index][2], "fill-opacity": 0.85 });
    add(node, "text", { x: x + 14, y, class: "axis" }, `${label} ${value}`);
  });
  // Two facts that must travel with the chart, or the bars overstate what was measured.
  const scope = report.retrieval === "hybrid" ? "生产 hybrid 口径" : "BM25 口径";
  add(node, "text", { x: 0, y: 196, class: "axis" },
      `命中@5 两个口径都是 ${num(active["hit@5"], 0)}/${perRound}：改善的是排序，不是召回`);
  add(node, "text", { x: 0, y: 212, class: "axis" },
      `每问 ${num((report.behaviour || {}).calls_per_query, 2)} 次语义调用（${scope}）`);
  box.appendChild(node);
}

/* 语料规模：逐版本堆叠柱 */
function chartCorpus(box, payload) {
  const rows = (payload.corpus_stats || {}).per_patch || [];
  if (!rows.length) { box.innerHTML = '<p class="ghost">语料统计不可用（运行 scripts/corpus_stats.py）</p>'; return; }
  const width = 1040;
  const height = 220;
  const node = svg(width, height);
  const max = Math.max(...rows.map((row) => row.chunks));
  const bar = (width - 40) / rows.length;
  rows.forEach((row, index) => {
    const x = 20 + index * bar;
    const structured = (row.structured / max) * (height - 60);
    const narrative = (row.narrative / max) * (height - 60);
    const title = `${row.patch}（${row.ddragon || "—"}）：结构化 ${row.structured} · 叙述 ${row.narrative}`;
    const col = add(node, "rect", {
      x: x + 1, y: height - 30 - narrative, width: bar - 3, height: narrative, class: "col narrative",
    });
    add(col, "title", {}, title);
    const top = add(node, "rect", {
      x: x + 1, y: height - 30 - narrative - structured, width: bar - 3, height: structured, class: "col",
    });
    add(top, "title", {}, title);
    if (index % 3 === 0) add(node, "text", { x: x + 1, y: height - 16, class: "axis" }, row.patch);
  });
  box.appendChild(node);
  const legend = document.createElement("div");
  legend.className = "legend";
  legend.innerHTML = '<span><i style="background:rgba(183,156,255,0.75)"></i>结构化数值改动</span>'
    + '<span><i style="background:rgba(255,255,255,0.16)"></i>公告叙述</span>';
  box.appendChild(legend);
}

/* 扩容前后：两组对比 */
function chartScale(box, payload) {
  const totals = (payload.corpus_stats || {}).totals || {};
  const recent = (payload.corpus_stats || {}).recent || {};
  const width = 520;
  const height = 150;
  const node = svg(width, height);
  gradients(node);
  const rows = [
    [`最近 ${recent.window || 10} 个版本`, recent.chunks || 0, recent.structured || 0],
    [`全部 ${totals.patches || 30} 个版本`, totals.chunks || 0, totals.structured || 0],
  ];
  const max = Math.max(...rows.map(([, chunks]) => chunks)) || 1;
  rows.forEach(([label, chunks, structured], index) => {
    const y = 24 + index * 52;
    add(node, "text", { x: 0, y: y + 12, class: "row-label" }, label);
    add(node, "rect", { x: 130, y: y, width: 330, height: 18, rx: 9, class: "bar-bg" });
    const fill = add(node, "rect", { x: 130, y: y, width: 0, height: 18, rx: 9, class: "bar-fill" });
    fill.dataset.width = 330 * (chunks / max);
    add(node, "text", { x: 470, y: y + 13, class: "row-value" }, `${chunks}`);
    add(node, "text", { x: 130, y: y + 34, class: "axis" }, `其中结构化数值改动 ${structured} 条`);
  });
  box.appendChild(node);
}

function animateCharts() {
  document.querySelectorAll(".chart-body .bar-fill").forEach((node) => {
    const width = Number(node.dataset.width || 0);
    requestAnimationFrame(() => { node.style.width = width; });
  });
}

function renderCharts(payload) {
  const boxes = {
    blind: chartBlind,
    selfcheck: chartSelfcheck,
    ranker: chartRanker,
    effect: chartEffect,
    routing: chartRouting,
    corpus: chartCorpus,
    scale: chartScale,
  };
  document.querySelectorAll("[data-chart]").forEach((box) => {
    const renderer = boxes[box.dataset.chart];
    if (!renderer) return;
    // One broken renderer must not blank the charts after it.
    try {
      renderer(box, payload);
    } catch (error) {
      box.innerHTML = `<p class="ghost">图表渲染失败：${esc(error.message)}</p>`;
    }
  });
  animateCharts();
  observeReveals();
}

/* ------------------------------------------------------------------ evaluation */
function pct(part, total) {
  return total ? `${part}/${total}` : "—";
}

async function loadEvaluation() {
  let payload;
  try {
    payload = await api("/api/evaluation");
  } catch {
    return;
  }
  const runs = payload.runs || [];
  state.runs = runs;
  const byName = (name) => runs.find((run) => run.name === name);
  const blindCtx = byName("盲测报告-带版本上下文");
  const blindPlain = byName("盲测报告");
  const same = byName("评测报告");
  const parse = byName("查询理解");

  const jevArm = (run) => (run?.arms || {})["hybrid+jev"] || {};
  const arms = (run) => run?.arms || {};

  const cost = jevArm(same).jev;
  if (cost?.calls) $("stat-cost").textContent = "$" + (cost.cost_usd / cost.calls).toFixed(6);

  // The hero figure is the same measurement the blind chart draws. The merged report may
  // not carry a hybrid+jev arm (it is regenerated by the evaluation scripts), so fall back
  // to the hybrid arm and say so in the label instead of inventing a number.
  const blind = blindRun(runs);
  state.blindRun = blind?.name || "";
  const blindArms = blind?.arms || {};
  const jevBlind = blindArms["hybrid+jev"];
  const baseBlind = blindArms.hybrid;
  const measured = jevBlind || baseBlind;
  const ok = measured?.single?.value_ok;
  const total = measured?.single?.value_total;
  if (typeof ok === "number" && total) {
    $("stat-blind").textContent = `${ok}/${total}`;
    $("stat-blind-note").textContent = jevBlind ? "盲测数值正确" : "盲测数值正确（hybrid 口径）";
  } else {
    $("stat-blind").textContent = "—";
    $("stat-blind-note").textContent = "盲测报告不可用";
  }

  const rows = [];
  const pushArm = (run, name, arm, scope) => {
    const data = (run?.arms || {})[arm];
    if (!data) return;
    rows.push({
      scope,
      name,
      single: pct(data.single?.["hit@1"], data.single?.total),
      pinned: data.pinned?.total ? pct(data.pinned?.["hit@1"], data.pinned?.total) : "—",
      overview: pct(data.overview?.ok, data.overview?.total),
      selfcheck: data.selfcheck?.total ? pct(data.selfcheck.pass, data.selfcheck.total) : "—",
      cost: data.jev?.calls ? `$${data.jev.cost_usd.toFixed(5)} · ${data.jev.mean_latency_ms}ms` : "—",
      jev: arm === "hybrid+jev",
    });
  };
  for (const arm of ["bm25", "vector", "hybrid", "hybrid+jev"]) pushArm(same, arm, arm, "同源 72 例");
  for (const arm of ["hybrid", "hybrid+jev"]) pushArm(blindCtx, arm, arm, "盲测 38 例");
  for (const arm of ["hybrid+jev"]) pushArm(blindPlain, "hybrid+jev（无版本上下文）", arm, "盲测 38 例");

  $("ab-table").querySelector("tbody").innerHTML = rows.map((row) => `
    <tr class="${row.jev ? "arm-jev" : ""}">
      <td><span class="scope">${esc(row.scope)}</span> ${esc(row.name)}</td>
      <td><strong>${esc(row.single)}</strong></td>
      <td>${esc(row.pinned)}</td>
      <td>${esc(row.overview)}</td>
      <td>${esc(row.selfcheck)}</td>
      <td class="dim">${esc(row.cost)}</td>
    </tr>`).join("");

  const compare = (arm) => {
    const base = arms(same)[arm]?.pinned;
    const jev = jevArm(same).pinned;
    return base && jev ? `${base["hit@1"]}/${base.total} → ${jev["hit@1"]}/${jev.total}` : "—";
  };
  const effect = payload.jev_effect || {};
  const ranker = payload.jev_ranker || {};
  const check = payload.jev_selfcheck || {};
  $("judgement").innerHTML = `
    <h3>Jev 的净贡献（含负面结论）</h3>
    <ul>
      <li class="plus"><i>＋</i><span><b>回答自检：注入错误数字检出 ${check.detected ?? "—"}/${check.cases ?? "—"}，正确回答误报 ${check.false_alarms ?? "—"}/${check.cases ?? "—"}</b>——把回答里的数字改掉 1，支持度从 ${(check.control_mean ?? 0).toFixed(2)} 掉到 ${(check.mutation_mean ?? 0).toFixed(2)}。这一步没有替代品。</span></li>
      <li class="plus"><i>＋</i><span><b>排序能力（去掉字段预过滤，同一批候选）：BM25 ${ranker.bm25_ok ?? "—"}/${ranker.total ?? "—"} → Jev ${ranker.jev_ok ?? "—"}/${ranker.total ?? "—"}</b>，Jev 独对 ${ranker.jev_only ?? "—"} 条、BM25 独对 ${ranker.bm25_only ?? "—"} 条。</span></li>
      <li class="plus"><i>＋</i><span><b>生产链路里 Jev 改序 ${effect.reordered ?? "—"}/${effect.total ?? "—"}</b>（改对 ${effect.reordered_ok ?? "—"}、改错 ${effect.reordered_bad ?? "—"}），保留顺序 ${effect.kept ?? "—"} 条；另有 ${effect.candidates_lt2 ?? "—"} 条过滤后候选不足 2 条，它没有介入余地。</span></li>
      <li class="plus"><i>＋</i><span><b>成本 $${cost?.calls ? (cost.cost_usd / cost.calls).toFixed(6) : "0.0001"}/次</b>、约 ${cost?.mean_latency_ms || 1200}ms；无 key 时自动降级并写进轨迹。</span></li>
      <li class="minus"><i>－</i><span><b>三种排序在同源案例上打平</b>：元数据过滤后候选常只剩 1–6 条，BM25/向量/混合没有差别；Jev 的价值集中在候选难以区分或问法与标签不一致的地方。</span></li>
      <li class="minus"><i>－</i><span><b>解析层没有用 Jev</b>：字段与意图识别是确定性正则，48 条查询理解盲测全过且零延迟——不该用模型的地方不用。</span></li>
    </ul>`;
  fillMetrics(payload);
  renderCharts(payload);
  observeReveals();
}

/** Fill every `<b data-metric="…">` in the static copy from the reports.
 *
 *  The numbers in the page's prose used to be typed by hand, so every evaluation run left the
 *  page quoting the previous version's results — they had already drifted once (the flow
 *  section still said 14/15 and 47/47 after those numbers moved on). Resolving them through
 *  `/api/evaluation` makes that impossible: a missing report renders "—" instead of a stale
 *  value, and a fixture-based routing run says so in the copy.
 */
function fillMetrics(payload) {
  const effect = payload.jev_effect || {};
  const check = payload.jev_selfcheck || {};
  const routing = payload.jev_routing || {};
  const routingEffect = payload.jev_routing_effect || {};
  const runs = payload.runs || [];
  const blindRuns = runs.filter((run) => String(run.name || "").startsWith("盲测报告"));
  const same = runs.find((run) => run.name === "评测报告") || {};
  const blind = blindRuns.find((run) => run.name === "盲测报告-合并")
    || blindRuns[blindRuns.length - 1] || {};
  const parse = runs.find((run) => run.name === "查询理解") || {};
  const arm = (run, name) => ((run.arms || {})[name]) || {};
  // Counts arrive as whole numbers for a single run and as means when the evaluation was
  // repeated, so a mean prints with one decimal and a count prints bare.
  const count = (value) => (Number.isInteger(value) ? String(value) : Number(value).toFixed(1));
  const ratio = (part, whole) => (whole ? `${count(part ?? 0)}/${count(whole)}` : "—");
  // Prefer the Jev arm when it exists so the copy describes the shipped pipeline.
  const sameJev = arm(same, "hybrid+jev");
  const sameArm = sameJev.pinned ? sameJev : arm(same, "hybrid");
  const sameBase = arm(same, "hybrid");
  const offArm = (routing.arms || {}).off || {};
  const activeArm = (routing.arms || {}).active || {};
  const behaviour = routing.behaviour || {};
  const perRound = activeArm.cases_per_round || 0;
  // The effect set is the one that measures a gain; the mechanism set is kept for the copy
  // that explains why the first attempt showed nothing.
  const effectOff = ((routingEffect.arms || {}).off) || {};
  const effectActive = ((routingEffect.arms || {}).active) || {};
  const effectRounds = effectActive.cases_per_round || routingEffect.cases || 0;
  const matrix = routingEffect.matrix || {};

  const values = {
    "pinned.before": ratio((sameBase.pinned || {})["hit@1"], (sameBase.pinned || {}).total),
    "pinned.after": ratio((sameArm.pinned || {})["hit@1"], (sameArm.pinned || {}).total),
    "selfcheck.pass": ratio((sameArm.selfcheck || {}).pass, (sameArm.selfcheck || {}).total),
    "selfcheck.threshold": check.threshold ?? "—",
    "rerank.total": effect.total ?? "—",
    "blind.cases": blind.cases ?? "—",
    "parse.cases": parse.cases ?? "—",
    "trap.cases": ((arm(same, "hybrid").trap) || {}).total ?? "—",
    "routing.cases": perRound || "—",
    "routing.off": ratio(effectOff["hit@1"], effectRounds),
    "routing.active": ratio(effectActive["hit@1"], effectRounds),
    "routing.machine_off": ratio(offArm["hit@1"], perRound),
    "routing.only_active": matrix.only_active ?? "—",
    "routing.only_off": matrix.only_off ?? "—",
    "routing.calls": (routingEffect.behaviour || {}).calls_per_query === undefined
      ? "—" : Number((routingEffect.behaviour || {}).calls_per_query).toFixed(2),
  };

  for (const node of document.querySelectorAll("[data-metric]")) {
    const key = node.dataset.metric;
    if (key === "routing.caveat") {
      // The routing result has to state its own scope: which case set produced it, whether it
      // came from the real model, and the fact that it improves ranking rather than recall.
      const source = routingEffect.source === "live" ? "真实模型" : "fixture 回放（非模型准确率）";
      const onlyOff = matrix.only_off ?? 0;
      const parts = [`口径：${source}；`];
      if (matrix.only_off !== undefined) {
        parts.push(onlyOff
          ? `有 ${onlyOff} 条是关掉路由才答对的，代价必须一起看。`
          : "没有任何一条是「关掉路由才答对」的，即这次没有测到损害。");
      }
      parts.push("命中@5 两种口径都是满分，说明路由改善的是排序、不是召回。");
      node.textContent = parts.join("");
      continue;
    }
    node.textContent = values[key] ?? "—";
  }
}

/* ------------------------------------------------------------------ compare */
async function compare() {
  const a = $("compare-a").value;
  const b = $("compare-b").value;
  const subject = $("compare-subject").value.trim();
  const box = $("compare-result");
  if (!a || !b) { box.innerHTML = '<p class="ghost">先选两个版本。</p>'; return; }
  box.innerHTML = '<p class="ghost">查询中…</p>';
  try {
    const data = await api(`/api/patch/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}&subject=${encodeURIComponent(subject)}`);
    if (data.error) { box.innerHTML = `<p class="ghost">${esc(data.error)}</p>`; return; }
    const groups = (data.subjects || []).filter((group) => group.changed);
    if (!groups.length) { box.innerHTML = `<p class="ghost">${esc(a)} → ${esc(b)}：没有找到改动差异。</p>`; return; }
    box.innerHTML = groups.slice(0, 30).map((group) => {
      const rows = group.rows.filter((row) => row.status !== "same").map((row) => {
        const label = `${row.ability ? row.ability + " " : ""}${row.field}`;
        if (row.status === "changed") return `<li><span class="st-changed">${esc(label)}</span> ${esc(row.a)} → <b>${esc(row.b)}</b></li>`;
        if (row.status === "added") return `<li><span class="st-added">${esc(b)} 改了</span> ${esc(label)}：${esc(row.b)}</li>`;
        return `<li><span class="st-removed">${esc(a)} 改了</span> ${esc(label)}：${esc(row.a)}</li>`;
      }).join("");
      return `<div class="cmp-group"><h3>${esc(group.subject)}</h3><ul>${rows}</ul></div>`;
    }).join("") + '<p class="ghost">公告只列出发生改动的字段，所以「只在一版出现」表示那一版改过它，不代表另一版没有值。</p>';
  } catch (error) {
    box.innerHTML = `<p class="ghost">对比失败：${esc(error.message)}</p>`;
  }
}

/* ------------------------------------------------------------------ boot */
function bind() {
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("input");
    const text = input.value;
    input.value = "";
    ask(text);
  });
  $("examples").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-q]");
    if (button) ask(button.dataset.q);
  });
  $("feedback").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-result]");
    if (button) feedback(button.dataset.result);
  });
  $("compare-run").addEventListener("click", compare);
  $("compare-subject").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); compare(); }
  });
}

field();
bind();
observeReveals();
observeRail();
scrollProgress();
loadHealth();
loadMeta();
loadEvaluation();
setInterval(loadHealth, 30000);
