"use strict";

const state = {
  sessionId: "web-" + Math.random().toString(36).slice(2, 10),
  busy: false,
  patches: [],
  facts: [],
  sessionCost: 0,
  runs: [],
};

const $ = (id) => document.getElementById(id);
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]
));

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
    $("rail-status-text").textContent = ready
      ? `${health.chunks} 条语料 · Jev ${health.jev?.enabled ? "启用" : "未启用"}`
      : "未就绪";
    if (ready) {
      const chunks = $("stat-chunks");
      countUp(chunks, health.chunks);
      $("stat-patches").textContent = health.patches.supported.length;
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
    const words = [...state.patches, "JEV 重排", "回答自检", "盲测 38 题", "数值只来自公告", "本地推理"];
    ticker.innerHTML = words.concat(words).map((word) => `<span>${esc(word)}</span>`).join("");
    $("examples").innerHTML = (meta.examples || [])
      .map((text) => `<button type="button" data-q="${esc(text)}">${esc(text)}</button>`)
      .join("");
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

function renderJev(session) {
  const bars = $("jev-bars");
  const pick = session.jev;
  const check = session.self_check;
  state.sessionCost = Number(session.cost_usd || 0);
  $("jev-cost").textContent = "$" + Number(session.cost_usd || 0).toFixed(6);
  $("session-cost").textContent = "$" + state.sessionCost.toFixed(6);

  if (!pick) {
    bars.innerHTML = '<p class="ghost">本次没有重排（候选不足 2 条，或未配置 TYPESAFE_API_KEY）</p>';
    $("jev-verdict").textContent = "";
    $("jev-note").textContent = "未调用";
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
      ? `分差 ${(pick.gap ?? 0).toFixed(2)} → 采用 Jev 顺序，首选 <b>${esc((byId[pick.choice_id] || {}).field || "该条")}</b>`
      : `分差 ${(pick.gap ?? 0).toFixed(2)} 低于 0.15 → 保留检索顺序（两条候选无法区分）`;
    if (pick.retrieval_top && pick.after_top) {
      const label = (id) => esc((byId[id] || {}).field || id);
      const same = pick.retrieval_top === pick.after_top;
      $("jev-verdict").innerHTML +=
        `<br>检索第 1：${label(pick.retrieval_top)} ${same ? "=" : "→"} Jev 第 1：${label(pick.after_top)}`;
    }
    $("jev-note").textContent = `${pick.model || "jev"} · ${pick.latency_ms}ms`;
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
    $("jev-self-note").textContent = "本次没有自检（未配置 key 或没有数值行）";
  }
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

/* ------------------------------------------------------------------ charts */
const NS = "http://www.w3.org/2000/svg";

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
  add(gradient, "stop", { offset: "0", "stop-color": "#b79cff" });
  add(gradient, "stop", { offset: "1", "stop-color": "#7cd4ff" });
}

/* 盲测成绩：横向条形 */
function chartBlind(box, runs) {
  const blind = runs.find((run) => run.name === "盲测报告-合并") || {};
  const arms = blind.arms || {};
  const arm = arms["hybrid+jev"] || arms["hybrid"] || {};
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
  box.appendChild(node);
  add(node, "text", { x: 132, y: height - 1, class: "axis" }, "满分 100% · 全部来自 docs/盲测报告-合并.json");
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
    corpus: chartCorpus,
    scale: chartScale,
  };
  document.querySelectorAll("[data-chart]").forEach((box) => {
    const renderer = boxes[box.dataset.chart];
    if (renderer) renderer(box, payload);
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

  const blindCost = jevArm(same).jev;
  if (blindCost?.calls) $("stat-cost").textContent = "$" + (blindCost.cost_usd / blindCost.calls).toFixed(6);

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
  renderCharts(payload);
  observeReveals();
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
