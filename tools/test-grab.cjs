/* 大屏「谁抢到 / AI 发言 / 人打断 AI」行为验证
   用法：mkdir -p /tmp/sertest2 && cd /tmp/sertest2 && npm i jsdom && cp /sandbox/workspace/debate-agent/tools/test-grab.cjs . && node test-grab.cjs
*/
const fs = require("fs");
const { JSDOM } = require("jsdom");
const HTML_PATH = "/sandbox/workspace/debate-agent/web/time/display.html";
const HTML = fs.readFileSync(HTML_PATH, "utf8");

const dom = new JSDOM(HTML, {
  runScripts: "dangerously",
  pretendToBeVisual: true,
  url: "https://120.48.43.211/debate/time/display.html",
  beforeParse(win) {
    win.__pub = [];
    win.fetch = async (u, opt) => {
      try { if (String(u).includes("/api/") && opt && opt.body) win.__pub.push(JSON.parse(opt.body)); } catch (e) {}
      return { ok: true, status: 200, json: async () => ({ ok: true }), text: async () => "{}", headers: { get: () => null } };
    };
    win.EventSource = class {
      constructor(u){ this.url = u; this.onmessage = null; win.__es = this; }
      addEventListener(){} close(){}
    };
    win.AudioContext = class {
      constructor(){ this.currentTime = 0; this.destination = {}; }
      createOscillator(){ return { type:"", frequency:{value:0}, connect(){}, start(){}, stop(){} }; }
      createGain(){ return { gain:{ setValueAtTime(){}, exponentialRampToValueAtTime(){} }, connect(){} }; }
    };
  }
});
const win = dom.window, $ = id => win.document.getElementById(id);
const sleep = ms => new Promise(r => setTimeout(r, ms));
let pass = 0, fail = 0;
const ok = (n, c, e = "") => { c ? (pass++, console.log("  ✓ " + n)) : (fail++, console.log("  ✗ " + n + "   → " + e)); };
const on = id => { const el = win.document.getElementById(id); return !!el && el.classList.contains("on"); };
const hint = () => ($("hint") ? $("hint").textContent : win.eval("hintEl.textContent"));
const lastPub = () => win.__pub[win.__pub.length - 1] || {};
const pubSince = n => win.__pub.slice(n);
function pushEv(obj){ win.__es.onmessage({ data: JSON.stringify(obj) }); }

(async () => {
  await sleep(400);
  console.log("== 准备：进入自由辩论 ==");
  win.eval("S.idx=7; applyStage();"); await sleep(150);
  ok("第 8 环节是自由辩论", win.eval("RULES[S.idx].kind") === "free");
  ok("初始无灯", !on("seat-pro-1") && !on("seat-con-1"));

  console.log("\n== 场景 1：人按按钮抢到（正方一辩） ==");
  win.onSeat("pro", 1); await sleep(150);
  ok("正方一辩灯亮", on("seat-pro-1"), $("seat-pro-1").className);
  ok("反方不误亮", !on("seat-con-1"));
  ok("提示「正方一辩 抢到发言」", /正方.*一辩.*抢到/.test(hint()), hint());
  ok("广播 free_grant(pro)", lastPub().action === "free_grant" && (lastPub().side === "pro" || lastPub().side === "正方"), JSON.stringify(lastPub()));

  console.log("\n== 场景 2：同侧另一位按 → 拒绝（不得同侧接力） ==");
  win.onSeat("pro", 3); await sleep(150);
  ok("正方三辩灯不亮", !on("seat-pro-3"));
  ok("一辩保持亮", on("seat-pro-1"));
  ok("提示含「接力」", /接力/.test(hint()), hint());
  ok("广播 free_deny(same-side)", lastPub().action === "free_deny" && lastPub().reason === "same-side", JSON.stringify(lastPub()));

  console.log("\n== 场景 3：人持话时 AI 来抢 → 拒绝（人阻断 AI） ==");
  pushEv({ action: "claim", side: "反方", source: "ai" }); await sleep(200);
  ok("广播 free_deny(claim)", lastPub().action === "free_deny" && lastPub().reason === "claim", JSON.stringify(lastPub()));
  ok("AI 灯未亮", !on("seat-con-2"));
  ok("正方一辩仍在发言", on("seat-pro-1"));

  console.log("\n== 场景 4：同一人再按 → 结束，发言权空出 ==");
  win.onSeat("pro", 1); await sleep(150);
  ok("灯全灭", !on("seat-pro-1"));
  ok("广播 free_idle(last=pro)", lastPub().action === "free_idle" && (lastPub().last === "pro" || lastPub().last === "正方"), JSON.stringify(lastPub()));
  ok("提示「正方说完，等反方接话」", /说完.*等.*反方/.test(hint()), hint());

  console.log("\n== 场景 5：反方接话（文案应为「接话」） ==");
  win.onSeat("con", 1); await sleep(150);
  ok("反方一辩灯亮", on("seat-con-1"));
  ok("正方灯灭", !on("seat-pro-1"));
  ok("提示含「接话」而非「抢到」", /接话/.test(hint()) && !/抢到/.test(hint()), hint());
  ok("广播 free_grant(con)", lastPub().action === "free_grant", JSON.stringify(lastPub()));

  console.log("\n== 场景 6：空档时 AI 抢到 ==");
  win.onSeat("con", 1); await sleep(120);
  ok("发言权已空出", lastPub().action === "free_idle");
  pushEv({ action: "claim", side: "正方", source: "ai" }); await sleep(250);
  ok("AI 抢到 → 正方二辩灯亮", on("seat-pro-2"), $("seat-pro-2").className);
  ok("提示「正方二辩（AI）抢到发言」", /二辩/.test(hint()) && /AI/.test(hint()), hint());
  ok("广播 free_grant(pro)", lastPub().action === "free_grant");

  console.log("\n== 场景 7：AI 说话中，同侧真人按按钮 → 人接管、AI 闭嘴 ==");
  win.onSeat("pro", 1); await sleep(150);
  const g = lastPub();
  ok("灯转给正方一辩、二辩灭", on("seat-pro-1") && !on("seat-pro-2"), `pro-1=${on("seat-pro-1")} pro-2=${on("seat-pro-2")}`);
  ok("提示「接管发言（AI 已停止）」", /接管/.test(hint()), hint());
  ok("广播 free_deny(human-takeover)", g.action === "free_deny" && g.reason === "human-takeover", JSON.stringify(g));

  console.log("\n== 场景 8：AI 说话中，对方真人按按钮 → 发言权转给对方 ==");
  win.eval("S.freeOwner={side:'pro',seat:2}; setSpeaker('pro',2);"); await sleep(80);
  win.onSeat("con", 1); await sleep(150);
  ok("反方一辩灯亮、正方二辩灭", on("seat-con-1") && !on("seat-pro-2"), `con-1=${on("seat-con-1")} pro-2=${on("seat-pro-2")}`);
  ok("广播 free_grant(con)（AI 侧据此闭嘴）", lastPub().action === "free_grant", JSON.stringify(lastPub()));

  console.log("\n== 场景 9：非自由辩论，人按对方按钮（只发一条 floor，带 human 标记） ==");
  win.eval("S.idx=0; applyStage();"); await sleep(150);
  const n0 = win.__pub.length;
  win.onSeat("con", 1); await sleep(200);
  const floors = pubSince(n0).filter(x => x.action === "floor");
  ok("只发一条 floor（无 stage-start 抢报）", floors.length === 1, JSON.stringify(floors));
  ok("floor = 反方 · reason=button · human=true",
     floors[0] && (floors[0].who === "con" || floors[0].who === "反方") && floors[0].reason === "button" && floors[0].human === true,
     JSON.stringify(floors[0] || {}));
  ok("反方一辩灯亮", on("seat-con-1"));

  console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail ? 1 : 0);
})();
