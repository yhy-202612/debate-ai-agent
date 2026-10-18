/* 流程驱动发言人（floor）实测
   目标：有大屏 + 实体按钮时，不再靠 ASR 猜「谁在说」，而由流程/按钮直接给出。
   跑法：cd tools && node test-floor.cjs [页面文件]                     */
const fs = require("fs");
const path = require("path");
let JSDOM;
try { ({ JSDOM } = require("jsdom")); }
catch (e) { ({ JSDOM } = require(path.join(process.cwd(), "node_modules", "jsdom"))); }

const FILE = process.argv[2] || path.join(__dirname, "..", "web", "local", "debate-local.html");
const HTML = fs.readFileSync(FILE, "utf8");
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const mkRes = (b, ok = true) => ({ ok, status: ok ? 200 : 500, json: async () => b,
  text: async () => JSON.stringify(b), headers: { get: () => null }, body: null });

const dom = new JSDOM(HTML, {
  runScripts: "dangerously", pretendToBeVisual: true,
  url: "http://localhost/debate/local/debate-local.html",
  beforeParse(win) {
    win.__attrHits = 0;
    class Gain { constructor() { this.gain = { value: 1, setValueAtTime() {}, linearRampToValueAtTime() {} }; } connect() { return this; } disconnect() {} }
    win.AudioContext = class {
      constructor() { this.state = "running"; this.currentTime = 0; this.sampleRate = 48000; this.destination = {}; }
      createGain() { return new Gain(); } createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
      createMediaStreamDestination() { return { stream: { getTracks: () => [] } }; }
      createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; }
      createBufferSource() { return { buffer: null, connect() {}, start() { win.__spk = (win.__spk || 0) + 1; }, stop() {}, onended: null }; }
      createBuffer(c, l) { return { duration: l / 48000, getChannelData: () => new Float32Array(l) }; }
      decodeAudioData() { return Promise.resolve({ duration: 1, getChannelData: () => new Float32Array(1) }); }
      resume() { return Promise.resolve(); } close() { return Promise.resolve(); }
    };
    win.HTMLMediaElement.prototype.play = function () { win.__plays = (win.__plays || 0) + 1; return Promise.resolve(); };
    win.HTMLMediaElement.prototype.pause = function () {};
    win.MediaStream = class { getTracks() { return []; } };
    Object.defineProperty(win.navigator, "mediaDevices", { value: { getUserMedia: async () => new win.MediaStream() }, configurable: true });
    win.URL.createObjectURL = () => "blob:stub"; win.URL.revokeObjectURL = () => {};
    win.EventSource = class { constructor(u) { this.url = u; this.onmessage = null; win.__es = this; } close() {} };
    win.fetch = async (url, opt = {}) => {
      const u = String(url);
      if (u.includes("/api/attribute")) { win.__attrHits++; return mkRes({ items: [{ idx: 0, who: "opp", sent: ["opp"] }] }); }
      if (u.includes("/api/buzzer")) return mkRes({ ok: true });
      if (u.includes("/api/transcript-save")) return mkRes({ ok: true });
      if (u.includes("/api/should-speak")) return mkRes({ speak: false });
      if (u.includes("/api/rebut")) return mkRes({ error: "x" }, false);
      if (u.includes("/api/tts")) return { ok: true, status: 200, blob: async () => ({ size: 1 }), headers: { get: () => null }, text: async () => "", body: null };
      return mkRes({ ok: true });
    };
    win.alert = () => {}; win.confirm = () => true;
  },
});

const win = dom.window;
const R = []; const ok = (n, c, e = "") => { R.push(!!c); console.log((c ? "✓" : "✗") + " " + n + (e ? "  " + e : "")); };

function reset() {
  win.eval(`
    cfg.mode="auto"; cfg.side="正方"; cfg.autoSave=false; cfg.diarize=false;
    cfg.floorMode=true; cfg.attr=false; cfg.streamMode=false; cfg.omni=false;
    cfg.speakCooldown=0; cfg.freeClaimDelay=700;
    transcript=[]; queue.length=0; lastSentences=[]; preHold=false; genBusy=false; playing=false;
    lastSpeakAt=0; lastIngestAt=Date.now(); lastPlayEndAt=0; freeMode=false; freeOwner=""; floorWho="";
    window.__attrHits=0; window.__genCalled=0;
    genRebut=async()=>{ genBusy=true; window.__genCalled=(window.__genCalled||0)+1; };
  `);
}
const fire = (o) => { if (win.__es && win.__es.onmessage) win.__es.onmessage({ data: JSON.stringify(o) }); };
const seg = async (text) => { await win.eval(`ingest(${JSON.stringify(text)}, [])`); await sleep(30); };
const whos = () => win.eval("transcript.map(s=>s.who)");
const floor = () => win.eval("floorWho");

(async () => {
  /* ---------- 1 sideWho 归一化 ---------- */
  console.log("\n======== 1 side 归一化（大屏发 pro/con，AI 侧兼容各种写法） ========");
  reset();
  const cases = [["pro", "ours"], ["con", "opp"], ["正方", "ours"], ["反方", "opp"], ["A", "ours"], ["B", "opp"]];
  let allOk = true;
  for (const [inp, want] of cases) {
    const got = win.eval(`sideWho(${JSON.stringify(inp)})`);
    if (got !== want) { allOk = false; console.log(`   ${inp} → ${got}（期望 ${want}）`); }
  }
  ok("pro/con/正方/反方/A/B 全部正确映射（我方=正方视角）", allOk);

  /* ---------- 2 环节切换 → 主席 ---------- */
  console.log("\n======== 2 环节切换的间隙 = 主席 ========");
  reset();
  fire({ action: "stage", kind: "forced", stage: 0, side: "pro", seat: 1, floor: "hall", source: "display" });
  await sleep(10);
  ok("切环节后 floor = hall（过渡期主席）", floor() === "hall", floor());
  await seg("感谢正方一辩的精彩立论。");
  ok("过渡期转写归会场", whos()[0] === "hall", JSON.stringify(whos()));

  /* ---------- 3 计时开始 → 本环节发言方 ---------- */
  console.log("\n======== 3 计时开始 = 本环节发言开始 ========");
  reset();
  fire({ action: "stage", kind: "forced", stage: 1, side: "con", seat: 1, floor: "hall", source: "display" });
  fire({ action: "floor", who: "con", seat: 1, stage: 1, reason: "stage-start", source: "display" });
  await sleep(10);
  ok("floor = opp（反方一辩发言）", floor() === "opp", floor());
  await seg("我方认为人工智能会消解人的价值。");
  ok("转写直接归对方（零 AI 调用）", whos()[0] === "opp", JSON.stringify(whos()));
  ok("关闭识别开关时不请求 /api/attribute", win.__attrHits === 0, `hits=${win.__attrHits}`);

  /* ---------- 4 主席串场语即使落在辩手时段也要记会场 ---------- */
  console.log("\n======== 4 主席串场兜底（纯正则，零调用） ========");
  reset();
  fire({ action: "floor", who: "con", stage: 1, reason: "stage-start", source: "display" });
  await seg("感谢反方一辩。下面有请正方二辩进行驳论。");
  ok("「感谢…下面有请…」→ 会场", whos()[0] === "hall", JSON.stringify(whos()));
  await seg("对方把效率当成价值，这是两个概念。");
  ok("接下去的辩手发言仍归对方", whos()[1] === "opp", JSON.stringify(whos()));

  /* ---------- 5 按钮回传 → 实时切换 ---------- */
  console.log("\n======== 5 实体按钮回传 = 该侧真人开口 ========");
  reset();
  fire({ action: "stage", kind: "cross", stage: 5, side: "con", seat: 1, target: { side: "pro", seat: 2 }, floor: "hall", source: "display" });
  fire({ action: "down", side: "con", action2: "", stage: 5, kind: "cross", source: "button" });
  await sleep(10);
  ok("对方按下按钮 → floor = opp", floor() === "opp", floor());
  await seg("请问对方，你们如何解释这个反例？");
  ok("该段归对方", whos()[0] === "opp", JSON.stringify(whos()));

  /* ---------- 6 自由辩论仲裁 ---------- */
  console.log("\n======== 6 自由辩论：抢话仲裁直接给出发言人 ========");
  reset();
  fire({ action: "stage", kind: "free", stage: 7, side: "both", floor: "hall", source: "display" });
  ok("自由辩论初始 floor = hall", floor() === "hall", floor());
  fire({ action: "free_grant", side: "con", source: "display" });
  ok("对方抢到 → floor = opp", floor() === "opp", floor());
  await seg("对方刚才说效率提升就是价值提升。");
  ok("抢话期间转写归对方", whos()[0] === "opp", JSON.stringify(whos()));
  fire({ action: "free_grant", side: "pro", source: "display" });
  ok("我方抢到 → floor = ours", floor() === "ours", floor());

  /* ---------- 7 我方说完 → 交回 ---------- */
  console.log("\n======== 7 我方说完（ai_done）→ 发言权交回 ========");
  reset();
  fire({ action: "stage", kind: "cross", stage: 5, side: "con", floor: "hall", source: "display" });
  win.eval("freeMode=false; buzzAiDoneAt=0;");
  win.eval("buzzAiDone()");
  await sleep(10);
  ok("非自由辩论 ai_done 后 floor = opp（回到对方）", floor() === "opp", floor());

  /* ---------- 8 识别开关打开 → 回退到 AI 判定 ---------- */
  console.log("\n======== 8 识别发言方开关（可选）开启后走 /api/attribute ========");
  reset();
  win.eval("cfg.attr = true;");
  await seg("我方认为这个判断站不住脚。");
  ok("开启后确实请求 /api/attribute", win.__attrHits > 0, `hits=${win.__attrHits}`);
  ok("归属采用模型结果", whos()[0] === "opp", JSON.stringify(whos()));

  /* ---------- 9 总开关关闭 → 回退到「全会场」 ---------- */
  console.log("\n======== 9 流程判定总开关关闭 → 不再臆测发言方 ========");
  reset();
  win.eval("cfg.floorMode = false; cfg.attr = false;");
  fire({ action: "floor", who: "con", stage: 1, source: "display" });
  await seg("我方认为人工智能会消解人的价值。");
  ok("关闭后不按 floor 归类（默认会场）", whos()[0] === "hall", JSON.stringify(whos()));

  /* ---------- 10 单机/未连大屏 → 自动回退 AI 识别（安全阀） ---------- */
  console.log("\n======== 10 未连大屏时自动回退（否则会一片都归会场、永不接话） ========");
  reset();
  win.eval("cfg.floorMode=true; cfg.attr=false; curStage=-1; curStageSide=\"\";");
  await seg("我方认为人工智能会消解人的价值。");
  ok("没有任何流程信息时自动走 /api/attribute", win.__attrHits > 0, `hits=${win.__attrHits}`);
  ok("归属不为空（不会卡在会场）", whos()[0] === "opp" || whos()[0] === "ours", JSON.stringify(whos()));

  const pass = R.filter(Boolean).length;
  console.log(`\n结果：${pass} 通过 / ${R.length - pass} 失败`);
  process.exit(pass === R.length ? 0 : 1);
})();
