"use strict";
/* 图书馆座位预约助手 —— 前端（面向用户，无框架、无外部依赖） */

const $ = (id) => document.getElementById(id);
const SLOT_LABEL = { morning: "上午 08:00–12:00", afternoon: "下午 13:30–18:00", evening: "晚上 19:00–22:00" };
const CUSTOM = "__custom__";

/** 归一化用户手填的定时时间：支持 13:01 / 13:01:30 / 1301 / 9:5 等写法，返回 HH:MM:SS 或 "" */
function normalizeTimeInput(raw) {
  let s = (raw || "").trim().replace(/：/g, ":").replace(/\s+/g, "");
  if (!s) return "";
  let m = s.match(/^(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?$/);
  if (!m) {
    m = s.match(/^(\d{1,2})(\d{2})(\d{2})?$/);   // 1301 / 130130
    if (!m) return "";
  }
  const h = parseInt(m[1], 10), mi = parseInt(m[2], 10), sec = m[3] ? parseInt(m[3], 10) : 0;
  if (h > 23 || mi > 59 || sec > 59) return "";
  const p = (n) => String(n).padStart(2, "0");
  return `${p(h)}:${p(mi)}:${p(sec)}`;
}

/** 取当前生效的预约时段：预设返回 slot key，自定义返回 ("", start, end) */
function currentSlot() {
  const v = $("slot").value;
  if (v !== CUSTOM) return { slot: v, start: "", end: "" };
  return {
    slot: "",
    start: normalizeTimeInput($("slot_start").value),
    end: normalizeTimeInput($("slot_end").value),
  };
}

/** 自定义时段的即时提示（时长 / 超范围） */
function updateSlotHint() {
  const hint = $("slot_custom_hint");
  const custom = $("slot").value === CUSTOM;
  if (!custom) { hint.textContent = ""; hint.classList.add("hidden"); return; }
  hint.classList.remove("hidden");
  const { start, end } = currentSlot();
  const rawS = $("slot_start").value, rawE = $("slot_end").value;
  $("slot_start").style.borderColor = (rawS && !start) ? "var(--err)" : "";
  $("slot_end").style.borderColor = (rawE && !end) ? "var(--err)" : "";
  if (!start || !end) {
    hint.innerHTML = '<span class="err">请填写开始和结束时间</span>';
    return;
  }
  const toMin = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
  const dur = toMin(end) - toMin(start);
  if (dur <= 0) {
    hint.innerHTML = '<span class="err">结束时间必须晚于开始时间</span>';
    return;
  }
  const h = Math.floor(dur / 60), m = dur % 60;
  hint.innerHTML = `时段 <b>${escapeHtml(start.slice(0,5))} – ${escapeHtml(end.slice(0,5))}</b>，`
    + `时长 <b>${h} 小时${m ? ` ${m} 分钟` : ""}</b>（${dur} 分钟）`
    + `<br><span class="dim">图书馆规则：时长为 60 ~ 300 分钟，且须在开放时段 07:00–22:00 内，`
    + `否则服务器会拒绝</span>`;
}

/** 失去焦点时规范化预约时段的自定义起止时间 */
function commitCustomSlot() {
  if ($("slot").value !== CUSTOM) return;
  for (const id of ("slot_start", "slot_end")) {
    const inp = $(id);
    const ok = normalizeTimeInput(inp.value);
    if (ok && inp.value !== ok) inp.value = ok;    // 9:30 -> 09:30:00
    inp.style.borderColor = (inp.value && !ok) ? "var(--err)" : "";
  }
  updateSlotHint();
}

function syncCustomSlot() {
  const custom = $("slot").value === CUSTOM;
  $("slot_custom_wrap").classList.toggle("hidden", !custom);
  if (custom) {
    const el = $("slot_start");
    if (el.value && !normalizeTimeInput(el.value)) el.style.borderColor = "var(--err)";
  }
  updateSlotHint();
}

/** 取当前生效的定时时间（下拉值，或自定义输入） */
function currentFireTime() {
  const v = $("fire_time").value;
  if (v !== CUSTOM) return v;
  return normalizeTimeInput($("fire_time_custom").value);
}

function syncCustomTime() {
  const custom = $("fire_time").value === CUSTOM;
  $("fire_time_custom_wrap").classList.toggle("hidden", !custom);
  if (!custom) return;
  const inp = $("fire_time_custom");
  // ★ 只做校验提示，**绝不改写输入框内容**。
  //   （以前在这里回写规范化结果，导致用户打 "13:30" 时刚输入 "13:3"
  //     就被改写成 "13:03:00"，后面的字符再也接不上。）
  const ok = normalizeTimeInput(inp.value);
  inp.style.borderColor = (inp.value && !ok) ? "var(--err)" : "";
}

/** 失去焦点时才规范化定时时间输入框内容 */
function commitCustomTime() {
  const inp = $("fire_time_custom");
  if ($("fire_time").value !== CUSTOM) return;
  const ok = normalizeTimeInput(inp.value);
  if (ok && inp.value !== ok) inp.value = ok;      // 13:30 -> 13:30:00
  inp.style.borderColor = (inp.value && !ok) ? "var(--err)" : "";
}

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

  setVal("base_url", c.base_url || "");
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
  // 预约时段：预设 或 自定义
  const slotVal = c.target.slot || "";
  if (slotVal && [...$("slot").options].some((o) => o.value === slotVal)) {
    setVal("slot", slotVal);
    $("slot_start").value = "";
    $("slot_end").value = "";
  } else {
    // 配置里是自定义时段（slot 为空 + start/end 有值）
    setVal("slot", CUSTOM);
    $("slot_start").value = (c.target.start_time || "").slice(0, 5);
    $("slot_end").value = (c.target.end_time || "").slice(0, 5);
  }
  syncCustomSlot();

  // 要求 4：定时日期默认当天，定时时间默认 13:30
  setVal("fire_date", c.schedule.fire_date || ymd(new Date()));
  const ft = (c.schedule.fire_time || "13:30").trim();
  const preset = [...$("fire_time").options].some((o) => o.value === ft);
  if (preset) {
    setVal("fire_time", ft);
    $("fire_time_custom").value = "";
  } else {
    // 配置里是自定义时间，回填到输入框
    setVal("fire_time", CUSTOM);
    $("fire_time_custom").value = ft;
  }
  syncCustomTime();
  updateSummary();
}

function collect() {
  // 只提交界面上存在的字段，其余后端默认值/既有值保持不变
  const patch = {
    base_url: getVal("base_url").replace(/\/+$/, ""),
    account: { username: getVal("username") },
    target: {
      space_id: $("space_select").value || "",
      seat_id: getVal("seat_id"),
      // 自定义时段时 slot 传空字符串，让后端回退用 start_time / end_time
      slot: currentSlot().slot,
      start_time: getVal("slot_start"),
      end_time: getVal("slot_end"),
      reserve_date: getVal("reserve_date"),
    },
    schedule: {
      mode: "at_time",
      fire_date: getVal("fire_date"),
      fire_time: currentFireTime() || getVal("fire_time"),
      retry: true,
      retry_max: null,            // 不限次数，靠 retry_timeout_s 兜底
      retry_interval_ms: 2000,    // 每 2 秒重试一次
      retry_timeout_s: 1800,      // 最多持续 30 分钟
      prewarm: false,
      stop_on_success: true,
      keep_running_after_success: false,
    },
  };
  if (getVal("password")) patch.account.password = $("password").value;
  return patch;
}

async function saveConfig(silent) {
  // 服务器地址归一化：自动补 http://，去掉末尾斜杠
  const el = $("base_url");
  if (el) {
    let v = (el.value || "").trim();
    if (v && !/^https?:\/\//i.test(v)) v = "http://" + v.replace(/^\/+/, "");
    v = v.replace(/\/+$/, "");
    if (v !== el.value) el.value = v;
  }
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
  const cs = currentSlot();
  const slotText = cs.slot
    ? (SLOT_LABEL[cs.slot] || cs.slot)
    : (cs.start && cs.end ? `${cs.start.slice(0,5)} – ${cs.end.slice(0,5)}（自定义）`
                          : "（请填写自定义时段）");
  const seat = getVal("seat_id") || "（未选座位）";
  const opt = $("space_select").selectedOptions[0];
  const roomName = opt && opt.value ? opt.textContent.replace(/\s*\(\d+\)\s*$/, "") : "（未选自习室）";
  const day = getVal("fire_date") || "今天";
  const at = currentFireTime();
  const atText = at ? escapeHtml(at) : '<span class="err">（请填写定时时间）</span>';
  $("summary").innerHTML = `将于 <b>${escapeHtml(day)} ${atText}</b> 自动预约 `
    + `<b>${escapeHtml(roomName)}</b> 的 <b>${escapeHtml(seat)}</b>，时段 `
    + `<b>${escapeHtml(slotText)}</b>。`;
}

/* ---------------- 动作 ---------------- */
async function actLogin() {
  if (!getVal("base_url")) return tip("login-tip", "请先填写服务器地址", true);
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
  if (!getVal("base_url")) return tip("login-tip", "请先填写服务器地址", true);
  if (!getVal("username")) return tip("login-tip", "请先填写学号", true);
  if (!$("space_select").value) return tip("seat-tip", "请先加载并选择自习室", true);
  if (!getVal("seat_id")) return tip("seat-tip", "请先查询并选择座位", true);
  await saveConfig(true);
  const r = await post("/api/test-delay", { delay_s: 60 });
  tip("verify-tip", r.message || "已启动 1 分钟测试", !r.ok);
}

async function actStart() {
  if (!getVal("base_url")) return tip("login-tip", "请先填写服务器地址", true);
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
  $("slot").addEventListener("change", () => { syncCustomSlot(); saveConfig(true); updateSummary(); });
  $("slot_start").addEventListener("input", () => { updateSlotHint(); updateSummary(); });
  $("slot_end").addEventListener("input", () => { updateSlotHint(); updateSummary(); });
  $("slot_start").addEventListener("blur", () => { commitCustomSlot(); saveConfig(true); });
  $("slot_end").addEventListener("blur", () => { commitCustomSlot(); saveConfig(true); });
  $("fire_time").addEventListener("change", () => { syncCustomTime(); saveConfig(true); updateSummary(); });
  $("fire_time_custom").addEventListener("input", () => { syncCustomTime(); updateSummary(); });
  $("fire_time_custom").addEventListener("blur", () => { commitCustomTime(); saveConfig(true); });
  $("fire_date").addEventListener("change", () => { saveConfig(true); updateSummary(); });

  setInterval(pollLogs, 1000);
  setInterval(pollState, 500);
  setInterval(() => { $("clock").textContent = new Date().toLocaleString("zh-CN", { hour12: false }); }, 1000);
  pollLogs(); pollState();
}

document.addEventListener("DOMContentLoaded", init);
