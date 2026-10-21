/* 重要词慢速重读 · 逻辑测试
   用法：node tools/test-emph.cjs
   覆盖：标记拆分、未闭合容错、空段过滤、慢读段不被合并、语速取值 */
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "..", "web", "local", "debate-local.html"), "utf8");

function grab(name, kind) {
  const re = new RegExp("^" + (kind || "function") + "\\s+" + name + "\\b", "m");
  const i = html.search(re);
  if (i < 0) throw new Error("missing " + name);
  let depth = 0, started = false;
  for (let k = i; k < html.length; k++) {
    const c = html[k];
    if (c === "{") { depth++; started = true; }
    else if (c === "}") { depth--; if (started && depth === 0) return html.slice(i, k + 1); }
  }
  throw new Error("unbalanced " + name);
}

const srcVar = (name) => {
  const m = new RegExp("(?:\\bvar\\s+|,\\s*)" + name + "\\s*=\\s*([^,;]+)").exec(html);
  if (!m) throw new Error("missing var " + name);
  return "var " + name + " = " + m[1] + ";";
};

const code = [
  srcVar("EMPH_OPEN"),
  srcVar("EMPH_CLOSE"),
  "var cfg = { rate:'+8%', emphRate:'-30%' };",
  grab("emphRate"),
  grab("segRate"),
  grab("stripEmphMarks"),
  grab("splitEmphParts"),
  "module.exports = { splitEmphParts, stripEmphMarks, segRate, EMPH_OPEN, EMPH_CLOSE };",
].join("\n");

const m = { exports: {} };
new Function("module", code)(m);
const { splitEmphParts, stripEmphMarks, segRate } = m.exports;

let pass = 0, fail = 0;
function eq(label, got, want) {
  const a = JSON.stringify(got), b = JSON.stringify(want);
  if (a === b) { pass++; console.log("  ✓ " + label); }
  else { fail++; console.log("  ✗ " + label + "\n      got  " + a + "\n      want " + b); }
}

console.log("【1】标记拆分");
eq("无标记 → 单段普通",
  splitEmphParts("这个论点根本站不住。"),
  [{ text: "这个论点根本站不住。", emph: false }]);

eq("句中标记 → 前/强调/后 三段",
  splitEmphParts("我方认为，【慢】从根本上【/慢】就是错的。"),
  [{ text: "我方认为，", emph: false }, { text: "从根本上", emph: true }, { text: "就是错的。", emph: false }]);

eq("整句标记 → 单段慢读",
  splitEmphParts("【慢】就是错的【/慢】"),
  [{ text: "就是错的", emph: true }]);

eq("句首标记 → 两段",
  splitEmphParts("【慢】我方认为【/慢】，这个论点站不住。"),
  [{ text: "我方认为", emph: true }, { text: "，这个论点站不住。", emph: false }]);

console.log("\n【2】容错");
eq("未闭合标记 → 其后全算慢读",
  splitEmphParts("我方认为，【慢】这个论点站不住。"),
  [{ text: "我方认为，", emph: false }, { text: "这个论点站不住。", emph: true }]);

eq("空标记 → 被过滤，不产生空段",
  splitEmphParts("前面【慢】【/慢】后面"),
  [{ text: "前面", emph: false }, { text: "后面", emph: false }]);

eq("残留标记剥除",
  stripEmphMarks("前【慢】中【/慢】后"),
  "前中后");

console.log("\n【3】语速取值");
eq("慢读段用 -30%", segRate(true), "-30%");
eq("普通段用 +8%", segRate(false), "+8%");

console.log("\n【4】合并边界（慢读段不得与普通段并成一段）");
const q = [
  { text: "我方认为，", emph: false },
  { text: "从根本上", emph: true },
  { text: "就是错的。", emph: false },
];
// 复刻 takeSeg 的合并判定
function mergeGroup(queue, max) {
  const first = queue.shift();
  let text = first.text;
  while (queue.length && text.length < max) {
    const nxt = queue[0];
    if ((nxt.emph === true) !== (first.emph === true)) break;
    if (text.length + nxt.text.length > max + 20) break;
    queue.shift(); text += nxt.text;
  }
  return { text, emph: first.emph };
}
const g1 = mergeGroup([...q], 120);
eq("第一组只含普通首段（遇慢读段即断）", g1, { text: "我方认为，", emph: false });

console.log("\n【5】三段拆出的顺序与拼接可还原原文");
const parts = splitEmphParts("我方认为，【慢】从根本上【/慢】就是错的。");
eq("拼接还原", parts.map(p => p.text).join(""),
  "我方认为，从根本上就是错的。");

console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
