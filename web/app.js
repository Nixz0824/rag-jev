"use strict";

const state = {
  sessionId: null,
  busy: false,
  patches: [],
};

const el = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

function newSessionId() {
  return "web-" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
}

async function loadHealth() {
  try {
    const health = await api("/api/health");
    const ready = health.ready === true;
    el("status-dot").className = "dot " + (ready ? "ok" : "bad");
    const jev = health.jev || {};
    el("status-text").textContent = ready
      ? `就绪 · ${health.chunks} 条语料 · Jev ${jev.enabled ? "已启用" : "未启用"}`
      : `未就绪：${health.error || "构建中"}`;
  } catch (error) {
    el("status-dot").className = "dot bad";
    el("status-text").textContent = "无法连接本地服务";
  }
}

async function loadMeta() {
  try {
    const meta = await api("/api/patch/meta");
    state.patches = meta.patches || [];
    const select = el("patch-select");
    for (const patch of [...state.patches].reverse()) {
      const option = document.createElement("option");
      option.value = patch;
      option.textContent = patch;
      select.appendChild(option);
    }
    el("patch-hint").textContent = `覆盖 ${state.patches[0] || "?"}—${meta.latest || "?"}`;
    el("source-note").textContent = meta.source_note || "";
    const stats = meta.stats || {};
    el("coverage").innerHTML = [
      ["收录版本", `${state.patches.length} 个（${state.patches[0]}—${meta.latest}）`],
      ["结构化变化", `${stats.structured ?? "?"} 条`],
      ["公告叙述", `${stats.narrative ?? "?"} 段`],
      ["生成时间", (meta.generated_at || "").slice(0, 19).replace("T", " ")],
    ].map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
    el("examples").innerHTML = (meta.examples || [])
      .map((text) => `<button type="button" data-q="${escapeHtml(text)}">${escapeHtml(text)}</button>`)
      .join("");
  } catch (error) {
    el("patch-hint").textContent = "元数据不可用：" + error.message;
  }
}

function addBubble(role, text, facts) {
  const bubble = document.createElement("div");
  bubble.className = "bubble " + role;
  const who = role === "user" ? "你" : "RAG Jev";
  let html = `<div class="meta">${who}</div><pre>${escapeHtml(text)}</pre>`;
  if (facts && facts.length) {
    html += `<ul class="facts">${facts.slice(0, 10).map((row) => {
      const source = row.source_url
        ? ` · <a href="${escapeHtml(row.source_url)}" target="_blank" rel="noreferrer">公告</a>`
        : "";
      return `<li>${escapeHtml(row.text || row.subject)}${source}</li>`;
    }).join("")}</ul>`;
  }
  bubble.innerHTML = html;
  el("messages").appendChild(bubble);
  el("messages").scrollTop = el("messages").scrollHeight;
  return bubble;
}

function renderFeedback(session) {
  document.querySelectorAll(".feedback").forEach((node) => node.remove());
  if (session.status !== "verify") return;
  const bubble = addBubble("assistant", "这条回答准确吗？反馈会写进本地记录。");
  const bar = document.createElement("div");
  bar.className = "feedback";
  for (const [value, label] of [["accurate", "准确"], ["incorrect", "有误"], ["outdated", "可能过时"]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", () => sendFeedback(value));
    bar.appendChild(button);
  }
  bubble.appendChild(bar);
}

function renderEvidence(session) {
  const evidence = session.evidence || [];
  el("evidence").innerHTML = evidence.length
    ? evidence.slice(0, 8).map((row) => {
        const link = row.source_url
          ? `<a href="${escapeHtml(row.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(row.source_title || "公告")}</a>`
          : escapeHtml(row.source_title || "");
        return `<li>${escapeHtml(row.text || "")}<div class="src">${link}</div></li>`;
      }).join("")
    : '<li class="muted">本次没有命中证据</li>';
  const trace = session.trace || [];
  el("trace").innerHTML = trace.length
    ? trace.map((step) => `<li>${escapeHtml(step.tool)}：${escapeHtml(step.detail)}</li>`).join("")
    : '<li class="muted">没有轨迹</li>';
}

async function ask(text) {
  if (state.busy || !text.trim()) return;
  state.busy = true;
  el("send").disabled = true;
  addBubble("user", text);
  const patch = el("patch-select").value || null;
  try {
    const session = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, text, patch }),
    });
    const replies = (session.messages || []).filter((message) => message.role === "assistant");
    const last = replies[replies.length - 1];
    if (last) addBubble("assistant", last.text, last.facts);
    renderEvidence(session);
    renderFeedback(session);
  } catch (error) {
    addBubble("assistant", "出错了：" + error.message);
  } finally {
    state.busy = false;
    el("send").disabled = false;
  }
}

async function sendFeedback(result) {
  try {
    const session = await api("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, result }),
    });
    const replies = (session.messages || []).filter((message) => message.role === "assistant");
    const last = replies[replies.length - 1];
    if (last) addBubble("assistant", last.text);
    renderEvidence(session);
    document.querySelectorAll(".feedback").forEach((node) => node.remove());
  } catch (error) {
    addBubble("assistant", "反馈失败：" + error.message);
  }
}

function bind() {
  state.sessionId = newSessionId();
  el("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = el("input");
    const text = input.value;
    input.value = "";
    ask(text);
  });
  el("examples").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-q]");
    if (button) ask(button.dataset.q);
  });
}

bind();
loadHealth();
loadMeta();
setInterval(loadHealth, 30000);
