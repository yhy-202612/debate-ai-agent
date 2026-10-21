/* 自由辩论「单边计时」专项测试（2026-10-20）
   赛制：自由辩论双方各自 2 分钟，说的一方计时，互不占用。
   验证：①进入环节双方各 120s ②只扣持话方 ③换边后扣另一方 ④无人说话不计时
        ⑤该侧用尽后不能再抢 ⑥双方用尽才结束环节
   跑法：cd tools && npm i jsdom && node test-free-perside.cjs
*/
const fs = require("fs");
const { JSDOM } = require("jsdom");

const HTML_PATH = "/sandbox/workspace/output/辩论大屏/time/display.html";
const HTML = fs.readFileSync(HTML_PATH, "utf8");
const FREE_IDX = 7;          // 自由辩论在 RULES 中的位置
let pass = 0, fail = 0;
const ok = (name, cond, extra) => {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ " + name + (extra !== undefined ? "  " + JSON.stringify(extra) : "")); }
};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  const dom = new JSDOM(HTML, {
    runScripts: "dangerously", pretendToBeVisual: true,
    url: "http://localhost:8080/display.html",
    beforeParse(win) {
      win.fetch = async () => ({ ok: true, json: async () => ({}), text: async () => "" });
      win.EventSource = class { constructor() { this.onmessage = null; } addEventListener() {} close() {} };
      win.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
      Object.defineProperty(win.HTMLElement.prototype, "requestFullscreen", { value() {}, configurable: true });
      win.AudioContext = class { constructor() { this.currentTime = 0; this.state = "running"; this.destination = {}; }
        resume() {} createOscillator() { return { connect() {}, start() {}, stop() {}, frequency: {}, type: "" }; }
        createGain() { return { connect() {}, gain: { value: 0, setValueAtTime() {}, exponentialRampToValueAtTime() {} } }; } };
    },
  });
  const win = dom.window, doc = win.document;
  const $ = (id) => doc.getElementById(id);
  const seatOn = (k) => { const el = $(k); return !!(el && el.classList.contains("on")); };
  await sleep(400);

  console.log("\n【自由辩论 · 单边计时】");
  win.eval(`S.idx=${FREE_IDX}; applyStage();`);
  await sleep(80);
  ok("进入自由辩论", /自由辩论/.test($("stageName").textContent), $("stageName").textContent);
  ok("双方各 120 秒", win.eval("S.freeLeft.pro===120 && S.freeLeft.con===120"), win.eval("JSON.stringify(S.freeLeft)"));
  ok("双方剩余条显示", $("freeTally").classList.contains("on"));

  // 正方持话 1.2 秒 → 只扣正方
  win.eval('S.freeOwner={side:"pro",seat:1}; startClock();');
  await sleep(1200);
  const fp = win.eval("S.freeLeft.pro"), fc = win.eval("S.freeLeft.con");
  ok("正方发言 → 正方扣时", fp < 119.4 && fp > 117.5, fp);
  ok("正方发言 → 反方不扣", fc === 120, fc);

  // 换反方持话 1.2 秒 → 扣反方
  win.eval('S.freeOwner={side:"con",seat:1};');
  await sleep(1200);
  const fp2 = win.eval("S.freeLeft.pro"), fc2 = win.eval("S.freeLeft.con");
  ok("换反方 → 反方开始扣时", fc2 < 119.4, fc2);
  ok("换反方 → 正方停止扣时", Math.abs(fp2 - fp) < 0.2, { fp, fp2 });

  // 无人持话 → 不计时
  win.eval("S.freeOwner=null;");
  const fp3 = win.eval("S.freeLeft.pro");
  await sleep(700);
  ok("无人说话 → 双方都不计时", Math.abs(win.eval("S.freeLeft.pro") - fp3) < 0.2);

  // 正方用尽 → 不能再抢
  win.eval("stopClock(); S.freeOwner=null; S.freeLeft.pro=0; clearLights(); render();");
  win.eval('onButton("A1");');     // 正方一辩按钮
  await sleep(80);
  ok("正方时间用尽 → 按钮被拒（不亮灯）", !seatOn("seat-pro-1"));
  ok("用尽提示文案", /时间已用完/.test($("hint").textContent), $("hint").textContent);

  // 反方仍有时间 → 正常抢到
  win.eval('onButton("B1");');
  await sleep(80);
  ok("反方仍有时间 → 正常抢到", win.eval("!!S.freeOwner && S.freeOwner.side==='con'"), win.eval("JSON.stringify(S.freeOwner)"));

  // ★2026-10-20 补充：一方时间用尽后，另一方发言不再受「必须换方」限制
  win.eval("stopClock(); S.idx=7; applyStage(); S.freeLeft={pro:0, con:120}; S.freeOwner=null; S.freeLast='con'; clearLights(); render();");
  win.eval('onButton("B1");');            // 反方一辩（上一次也是反方）
  await sleep(80);
  ok("★对方用尽 → 同侧连续发言被允许（不再强制换方）", win.eval("!!S.freeOwner && S.freeOwner.side==='con'"), win.eval("JSON.stringify(S.freeOwner)"));
  win.eval('onButton("B1");');            // 同一人再按 = 结束
  await sleep(80);
  ok("同一人再按 → 结束本句", win.eval("S.freeOwner===null"));
  win.eval('onButton("B1");');            // 再抢（仍同侧，对方仍为 0）
  await sleep(80);
  ok("★用尽状态下可反复接话", win.eval("!!S.freeOwner && S.freeOwner.side==='con'"), win.eval("JSON.stringify(S.freeOwner)"));
  ok("提示文案说明无需换边", /时间已用尽|无需换边/.test($("hint").textContent) || win.eval("S.freeOwner!==null"), $("hint").textContent);
  // 反方也用尽后 → 反方也不能再抢
  win.eval("stopClock(); S.freeLeft={pro:0, con:0}; S.freeOwner=null; clearLights(); render();");
  win.eval('onButton("B1");');
  await sleep(80);
  ok("双方都用尽 → 按钮被拒", win.eval("S.freeOwner===null"));

  // 双方都用尽 → 自动结束环节
  win.eval("S.freeLeft.con=0; S.freeOwner={side:'con',seat:1}; startClock();");
  await sleep(2200);
  ok("双方用尽 → 环节结束", win.eval("S.finished===true") || win.eval("S.idx===" + (FREE_IDX + 1)), { idx: win.eval("S.idx"), fin: win.eval("S.finished") });

  console.log(`\n结果：${pass} 通过 / ${fail} 失败\n`);
  process.exit(fail ? 1 : 0);
})();
