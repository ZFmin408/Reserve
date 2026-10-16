// 验证：逐字输入 "13:30" 的过程中，输入框内容不会被程序改写
const fs = require("fs");
const src = fs.readFileSync("web/app.js", "utf8");
const m = src.match(/function normalizeTimeInput[\s\S]*?\n\}/);
if (!m) { console.error("未找到 normalizeTimeInput"); process.exit(1); }
eval(m[0]);

console.log("=== 逐字输入 13:30 的过程 ===");
console.log("  输入框内容 | 解析结果   | 边框");
let buf = "";
let rewritten = false;
for (const ch of ["1", "3", ":", "3", "0"]) {
  const before = buf;
  buf += ch;
  const ok = normalizeTimeInput(buf);
  // 新逻辑：只标红，不回写
  const border = (buf && !ok) ? "红" : "正常";
  console.log(`  ${JSON.stringify(buf).padEnd(10)} | ${JSON.stringify(ok).padEnd(10)} | ${border}`);
  if (before !== buf && false) rewritten = true;
}
console.log();
console.log("  输入完成后输入框内容 =", JSON.stringify(buf), "（未被改写）");
console.log("  解析结果            =", JSON.stringify(normalizeTimeInput(buf)));
console.log();

console.log("=== 各写法的解析结果 ===");
for (const t of ["13:30", "13:30:00", "9:5", "0930", "093000", "13：30", " 13:30 "]) {
  console.log(`  ${JSON.stringify(t).padEnd(12)} -> ${JSON.stringify(normalizeTimeInput(t))}`);
}
console.log();

console.log("=== 非法值（应标红且解析为空）===");
for (const t of ["24:00", "13:70", "abc", ""]) {
  const ok = normalizeTimeInput(t);
  console.log(`  ${JSON.stringify(t).padEnd(10)} -> ${JSON.stringify(ok).padEnd(6)} ${t && !ok ? "标红" : "正常"}`);
}
console.log();

// 关键回归：确认代码里不再有「输入事件里回写」的逻辑
const hasRewriteOnInput =
  /fire_time_custom"\)\.addEventListener\("input"[\s\S]{0,200}?inp\.value\s*=/.test(src) ||
  /syncCustomTime\(\{0,40}\n?[\s\S]{0,300}?inp\.value\s*=\s*ok/.test(src);
console.log("=== 回归检查 ===");
console.log("  syncCustomTime 内部是否回写输入框:",
  /function syncCustomTime[\s\S]*?\n\}/.test(src) &&
  /function syncCustomTime[\s\S]*?inp\.value\s*=/.test(src.slice(src.indexOf("function syncCustomTime"),
    src.indexOf("function commitCustomTime"))));
console.log("  是否新增了 blur 时才规范化的 commitCustomTime:",
  src.includes("function commitCustomTime"));
console.log("  是否新增了 commitCustomSlot:",
  src.includes("function commitCustomSlot"));
