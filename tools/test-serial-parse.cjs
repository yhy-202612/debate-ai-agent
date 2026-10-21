/* 针对性冒烟：验证大屏页面对「任意固件输出格式」的解析
   用法：node /sandbox/workspace/debate-agent/tools/test-serial-parse.cjs */
const fs = require("fs"), path = require("path");
const { JSDOM } = require(path.join(__dirname, "node_modules", "jsdom"));
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
    win.EventSource = class { constructor(u){ this.url=u; this.onmessage=null; } addEventListener(){} close(){} };
    win.AudioContext = class {
      constructor(){ this.currentTime=0; this.destination={}; }
      createOscillator(){ return { type:"", frequency:{value:0}, connect(){}, start(){}, stop(){} }; }
      createGain(){ return { gain:{ setValueAtTime(){}, exponentialRampToValueAtTime(){} }, connect(){} }; }
    };
  }
});
const win = dom.window, $ = id => win.document.getElementById(id);
const sleep = ms => new Promise(r => setTimeout(r, ms));
let pass = 0, fail = 0;
const ok = (n, c, e = "") => { c ? (pass++, console.log("  ✓ " + n)) : (fail++, console.log("  ✗ " + n + "  " + e)); };
const on = id => win.document.getElementById(id).classList.contains("on");

(async () => {
  await sleep(400);
  console.log("== 1. 基本结构 ==");
  ok("函数 onSerialLine 存在", typeof win.onSerialLine === "function");
  ok("灯数 = 6", win.document.querySelectorAll(".seat").length === 6, win.document.querySelectorAll(".seat").length);

  console.log("== 2. 同一份固件 + 连接顺序决定正反 ==");
  const L1 = { side: "pro", board: "" };
  const L2 = { side: "con", board: "" };
  win.eval("S.idx=0; applyStage();"); await sleep(120);

  win.onSerialLine("A1:D", L1); await sleep(120);
  ok("link=pro + A1:D → 正方一辩亮", on("seat-pro-1"));
  ok("未误亮反方", !on("seat-con-1"));

  win.eval("clearLights(); S.running=false;"); await sleep(60);
  win.onSerialLine("A1:D", L2); await sleep(120);
  ok("link=con + A1:D（同一份程序）→ 反方一辩亮", on("seat-con-1"));
  ok("未误亮正方", !on("seat-pro-1"));

  win.eval("clearLights(); S.running=false;"); await sleep(60);
  win.onSerialLine("A2:D", L1); await sleep(120);
  ok("A2:D → 正方三辩亮", on("seat-pro-3"), win.document.getElementById("seat-pro-3").className);

  console.log("== 3. 其它写法兼容 ==");
  win.eval("clearLights(); S.running=false;"); await sleep(60);
  win.onSerialLine("1:D", L1); await sleep(100);
  ok("1:D（无板号）→ 正方一辩亮", on("seat-pro-1"));

  win.eval("clearLights(); S.running=false;"); await sleep(60);
  win.onSerialLine("A1：按下", L2); await sleep(100);
  ok("A1：按下（中文+全角冒号）→ 反方一辩亮", on("seat-con-1"));

  console.log("== 4. 噪声与松开 ==");
  win.__pub.length = 0;
  win.eval("clearLights(); S.running=false;"); await sleep(60);
  win.onSerialLine("A1:U", L1); await sleep(80);
  ok("A1:U 松开不亮灯", !on("seat-pro-1"));
  win.onSerialLine("=== 5键测试就绪 ===", L1); await sleep(50);
  ok("测试横幅不抛异常", true);

  console.log("== 5. 状态栏与按钮 ==");
  win.onSerialLine("READY A", L1); await sleep(50);
  ok("READY 后状态栏含「正方板」", /正方板/.test($("serialState").textContent), $("serialState").textContent);
  ok("存在「交换正/反」按钮", !!$("btnSwap"));

  console.log("== 6. 事件推送（给智能体） ==");
  const pubs = win.__pub.filter(p => p.action === "down");
  ok("按下时向服务器发过 down 事件", pubs.length > 0, JSON.stringify(pubs));

  console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail ? 1 : 0);
})();
