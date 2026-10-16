"use strict";
/* 图书馆座位预约助手 —— 前端（面向用户，无框架、无外部依赖） */

const $ = (id) => document.getElementById(id);
const SLOT_LABEL = { morning: "上午 08:00–12:00", afternoon: "下午 13:30–18:00", evening: "晚上 19:00–22:00" };

const T = {};            // 各控件的原始值，用于判断用户是否改过

/* ---------------- 基础请求 ---------------- */
async function api(path, opts) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
  const text = await res.text();
  try { return JSON.parse(text); } catch (e) { return { ok: false, message: text || ("HTTP " + res.status) }; }
}
const post = (p, body) => api(p, { method: "POST", body: JSON.stringify(body || {}) });

let tipTimer = null;
function tip(id, msg, isErr) {
  const el = $(id);
  if (!el) return;
  el.textContent = msg;
  el.className = "tip" + (isErr ? " err" : " ok");
  if (tipTimer) clearTimeout(tipTimer);
  if (msg) tipTimer = setTimeout(() => { el.textContent = ""; el.className = "tip"; }, 6000);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/* ---------------- 日期下拉 ---------------- */
function ymd(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function fillDateSelect(sel, days) {
  const today = new Date();
  const cur = sel.value;
  sel.innerHTML = "";
  for (let i = 0; i < days; i++) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() + i);
    const v = ymd(d);
    const label = i === 0 ? `今天 (${v})` : i === 1 ? `明天 (${v})` : `${v} 周${"日一二三四五六"[d.getDay()]}`;
    sel.appendChild(new Option(label, v));
  }
  if (cur) sel.value = cur;
}

/* ---------------- 读写配置 ---------------- */
function getVal(id) { return $(id) ? $(id).value.trim() : ""; }
function setVal(id, v) {
  const el = $(id);
  if (!el) return;
  el.value = v === null || v === undefined ? "" : v;
  T[id] = el.value;
}

function fillForm(c) {
  fillDateSelect($("reserve_date"), 7);
  fillDateSelect($("fire_date"), 7);

  setVal("username", c.account.username);
  $("password").value = "";
  $("password").placeholder = c.account.password ? "已保存（留空=不修改）" : "请输入密码";
  T["password"] = "";

  if (c.target.space_id) {
    const sel = $("space_select");
    if (![...sel.options].some((o) => o.value === String(c.target.space_id))) {
      sel.appendChild(new Option(`已保存的自习室 (${c.target.space_id})`, String(c.target.space_id)));
    }
    sel.value = String(c.target.space_id);
  }
  setVal("reserve_date", c.target.reserve_date || ymd(new Date()));
  setVal("seat_id", c.target.seat_id);
  setVal("slot", c.target.slot || "afternoon");

  // 要求 4：定时日期默认当天，定时时间默认 13:30
  setVal("fire_date", c.schedule.fire_date || ymd(new Date()));
  setVal("fire_time", c.schedule.fire_time || "13:30");
  updateSummary();
}

function collect() {
  // 只提交界面上存在的字段，其余后端默认值/既有值保持不变
  const patch = {
    account: { username: getVal("username") },
    target: {
      space_id: $("space_select").value || "",
      seat_id: getVal("seat_id"),
      slot: getVal("slot"),
      reserve_date: getVal("reserve_date"),
    },
    schedule: {
      mode: "at_time",
      fire_date: getVal("fire_date"),
      fire_time: getVal("fire_time"),
      retry: true,
      retry_max: 3,
      retry_interval_ms: 3000,
      retry_timeout_s: 60,
      prewarm: false,
      stop_on_success: true,
      keep_running_after_success: false,
    },
  };
  if (getVal("password")) patch.account.password = $("password").value;
  return patch;
}

async function saveConfig(silent) {
  const r = await post("/api/config", collect());
  if (r.ok) {
    if (getVal("password")) T["password"] = $("password").value;
    if (!silent) tip("login-tip", "设置已保存");
  } else {
    tip("login-tip", "保存失败：" + r.message, true);
  }
  return r;
}

function updateSummary() {
  const slot = getVal("slot");
  const seat = getVal("seat_id") || "（未选座位）";
  const opt = $("space_select").selectedOptions[0];
  const roomName = opt && opt.value ? opt.textContent.replace(/\s*\(\d+\)\s*$/, "") : "（未选自习室）";
  const day = getVal("fire_date") || "今天";
  const at = getVal("fire_time") || "13:30";
  $("summary").innerHTML = `将于 <b>${escapeHtml(day)} ${escapeHtml(at)}</b> 自动预约 `
    + `<b>${escapeHtml(roomName)}</b> 的 <b>${escapeHtml(seat)}</b>，时段 `
    + `<b>${escapeHtml(SLOT_LABEL[slot] || slot)}</b>。`;
}

/* ---------------- 动作 ---------------- */
async function actLogin() {
  if (!getVal("username")) return tip("login-tip", "请先填写学号", true);
  await saveConfig(true);
  tip("login-tip", "正在检测…");
  const r = await api("/api/diagnose");
  const step = (r.steps || []).find((s) => s.step === "登录") || {};
  if (r.ok) tip("login-tip", "登录成功，可以预约");
  else tip("login-tip", "登录失败：" + (step.message || "未知错误"), true);
}

async function actRooms() {
  await saveConfig(true);
  const sel = $("space_select");
  sel.innerHTML = "<option>加载中…</option>";
  const r = await api("/api/rooms");
  if (!r.ok) {
    sel.innerHTML = '<option value="">加载失败</option>';
    return tip("login-tip", r.message || "加载自习室失败", true);
  }
  sel.innerHTML = '<option value="">请选择自习室</option>' + (r.rooms || [])
    .map((x) => `<option value="${x.roomId}">${escapeHtml(String(x.roomName))}</option>`).join("");
  updateSummary();
}

async function actSeats() {
  const room = $("space_select").value;
  if (!room) return tip("seat-tip", "请先加载并选择自习室", true);
  await saveConfig(true);
  const out = $("seats-out");
  out.classList.remove("hidden");
  out.textContent = "查询中…";
  const r = await api("/api/seats?date=" + encodeURIComponent(getVal("reserve_date")));
  if (!r.ok) { out.textContent = "查询失败：" + r.message; return; }
  const seats = r.seats || [];
  if (!seats.length) { out.textContent = "没有查到座位。"; return; }

  const free = seats.filter((s) => s.free).length;
  let html = `<div class="out-head">可选座位 <b class="ok">空闲 ${free}</b> / 共 ${seats.length}`
    + `　<span class="dim">点击一行即可选中</span></div>`
    + `<table class="seats"><tr><th>座位</th><th>状态</th><th>开放时间</th></tr>`;
  for (const s of seats) {
    html += `<tr class="${s.free ? "" : "busy"}" data-seat="${escapeHtml(String(s.seatId))}" data-name="${escapeHtml(String(s.seatName || ""))}">`
      + `<td>${escapeHtml(String(s.seatName || s.seatId))}</td>`
      + `<td class="${s.free ? "ok" : "err"}">${escapeHtml(String(s.status))}</td>`
      + `<td class="dim">${escapeHtml(String(s.openTime || "-"))}</td></tr>`;
  }
  out.innerHTML = html + "</table>";
  out.querySelectorAll("tr[data-seat]").forEach((tr) => {
    tr.addEventListener("click", () => {
      $("seat_id").value = tr.dataset.seat;
      T["seat_id"] = tr.dataset.seat;
      out.querySelectorAll("tr").forEach((x) => x.classList.remove("picked"));
      tr.classList.add("picked");
      tip("seat-tip", "已选择座位 " + tr.dataset.name);
      saveConfig(true);
      updateSummary();
    });
  });
  tip("seat-tip", `查到 ${seats.length} 个座位，空闲 ${free} 个`);
}

async function actTest60() {
  if (!getVal("username")) return tip("login-tip", "请先填写学号", true);
  if (!$("space_select").value) return tip("seat-tip", "请先加载并选择自习室", true);
  if (!getVal("seat_id")) return tip("seat-tip", "请先查询并选择座位", true);
  await saveConfig(true);
  const r = await post("/api/test-delay", { delay_s: 60 });
  tip("verify-tip", r.message || "已启动 1 分钟测试", !r.ok);
}

async function actStart() {
  if (!getVal("username")) return tip("login-tip", "请先填写学号", true);
  if (!$("space_select").value) return tip("seat-tip", "请先加载并选择自习室", true);
  if (!getVal("seat_id")) return tip("seat-tip", "请先查询并选择座位", true);
  await saveConfig(true);
  const r = await post("/api/start", {});
  tip("verify-tip", r.message || "已开始定时", !r.ok);
}

async function actVerify() {
  if (!$("space_select").value) return tip("seat-tip", "请先加载并选择自习室", true);
  if (!getVal("seat_id")) return tip("seat-tip", "请先查询并选择座位", true);
  await saveConfig(true);
  const r = await post("/api/verify", {});
  tip("verify-tip", r.message || "已开始预约验证", !r.ok);
}

async function actStop() {
  const r = await post("/api/stop", {});
  tip("verify-tip", r.message || "已停止", !r.ok);
}

/* ---------------- 日志与状态 ---------------- */
let logCount = 0;
async function pollLogs() {
  const r = await api("/api/logs?since=" + logCount);
  if (!r.ok || !r.logs || !r.logs.length) return;
  logCount = r.total;
  const el = $("logs");
  const frag = document.createDocumentFragment();
  for (const l of r.logs) {
    const div = document.createElement("div");
    div.className = "l-" + l.level;
    div.textContent = `[${l.ts}] ${l.message}`;
    frag.appendChild(div);
  }
  el.appendChild(frag);
  if ($("autoscroll").checked) el.scrollTop = el.scrollHeight;
}

const STATUS_CN = {
  idle: "空闲", waiting: "等待中", prewarm: "准备中", firing: "发送中",
  success: "预约成功", failed: "失败", stopped: "已停止",
};
async function pollState() {
  const r = await api("/api/state");
  if (!r.ok) return;
  const s = r.state;
  const el = $("st-status");
  el.textContent = STATUS_CN[s.status] || s.status;
  el.style.color = s.status === "success" ? "var(--ok)"
    : s.status === "failed" ? "var(--err)" : s.status === "idle" ? "" : "var(--warn)";
  $("st-fire").textContent = s.fire_at ? s.fire_at.slice(0, 19) : "-";
  const left = s.seconds_left;
  $("st-left").textContent = left === null || left === undefined ? "-"
    : left > 3600 ? `${Math.floor(left / 3600)} 小时 ${Math.floor((left % 3600) / 60)} 分`
    : left > 60 ? `${Math.floor(left / 60)} 分 ${Math.round(left % 60)} 秒`
    : `${Math.round(left)} 秒`;
  $("st-attempts").textContent = s.attempts || 0;
  if (s.last_message) $("st-msg").textContent = s.last_message;
}

/* ---------------- 初始化 ---------------- */
async function init() {
  const r = await api("/api/config");
  if (r.ok) fillForm(r.config);
  else { fillDateSelect($("reserve_date"), 7); fillDateSelect($("fire_date"), 7); }

  $("btn-login").onclick = actLogin;
  $("btn-rooms").onclick = actRooms;
  $("btn-seats").onclick = actSeats;
  $("btn-start").onclick = actStart;
  $("btn-test60").onclick = actTest60;
  $("btn-verify").onclick = actVerify;
  $("btn-stop").onclick = actStop;
  $("btn-clear-logs").onclick = async () => {
    await post("/api/clear-logs", {});
    $("logs").innerHTML = "";
    logCount = 0;
  };
  $("space_select").addEventListener("change", () => { saveConfig(true); updateSummary(); });
  $("slot").addEventListener("change", () => { saveConfig(true); updateSummary(); });
  $("fire_time").addEventListener("change", () => { saveConfig(true); updateSummary(); });
  $("fire_date").addEventListener("change", () => { saveConfig(true); updateSummary(); });

  setInterval(pollLogs, 1000);
  setInterval(pollState, 500);
  setInterval(() => { $("clock").textContent = new Date().toLocaleString("zh-CN", { hour12: false }); }, 1000);
  pollLogs(); pollState();
}

document.addEventListener("DOMContentLoaded", init);
