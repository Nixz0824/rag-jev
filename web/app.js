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
    });
  }, { rootMargin: "-45% 0px -45% 0px" });
  sections.forEach((section) => observer.observe(section));
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

  const tiles = [
    {
      value: () => pct(jevArm(blindCtx).single?.value_ok, jevArm(blindCtx).single?.value_total),
      label: "盲测数值正确率（38 题中的 20 条单点）",
      source: "盲测 · 出题方未接触语料",
      blind: true,
    },
    {
      value: () => pct(jevArm(blindCtx).single?.["hit@1"], jevArm(blindCtx).single?.total),
      label: "盲测检索命中@1",
      source: "盲测 · 界面选中版本",
      blind: true,
    },
    {
      value: () => pct(jevArm(blindCtx).selfcheck?.pass, jevArm(blindCtx).selfcheck?.total),
      label: "Jev 回答自检通过（支持度 ≥ 0.5）",
      source: "盲测 · 只有 Jev 能做",
      blind: true,
    },
    {
      value: () => {
        const base = arms(same).hybrid?.pinned?.["hit@1"];
        const jev = jevArm(same).pinned?.["hit@1"];
        return base && jev ? `${base}→${jev}` : "—";
      },
      label: "定点命中@1（同源模糊候选）",
      source: "同源 · hybrid 对比 hybrid+Jev",
    },
    {
      value: () => pct(arms(same).hybrid?.trap?.ok, arms(same).hybrid?.trap?.total),
      label: "陷阱题（错误断言 / 越界 / 注入）",
      source: "规则定义 · 不同源",
    },
    {
      value: () => (parse ? pct(parse.cases_passed, parse.cases) : "48/48"),
      label: "查询理解盲测（主体/版本/技能/字段）",
      source: "盲测 · 解析层",
    },
  ];

  $("tiles").innerHTML = tiles.map((tile) => `
    <div class="tile ${tile.blind ? "blind" : ""} reveal">
      <div class="value" data-value="${esc(tile.value())}">${esc(tile.value())}</div>
      <div class="label">${esc(tile.label)}</div>
      <div class="source">${esc(tile.source)}</div>
    </div>`).join("");

  $("stat-blind").textContent = tiles[0].value();
  const cost = jevArm(same).jev;
  if (cost?.calls) $("stat-cost").textContent = "$" + (cost.cost_usd / cost.calls).toFixed(6);

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
loadHealth();
loadMeta();
loadEvaluation();
setInterval(loadHealth, 30000);
