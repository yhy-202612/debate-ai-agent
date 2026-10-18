/* 质询环节自动识别发言人（quiz → attribute）实测
   目标：质询是一问一答双方交替开口，按钮/流程无法表达「现在谁在说」，
        本环节必须无条件走识别（不再用 floor）。
   跑法：cd tools && node test-quiz-attr.cjs [页面文件]                 */
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
    curStage=-1; curKind=""; curTarget=null; curStageName=""; aiOnStage=true;
    window.__attrHits=0; window.__genCalled=0;
    genRebut=async()=>{ genBusy=true; window.__genCalled=(window.__genCalled||0)+1; };
  `);
}
const fire = (o) => { if (win.__es && win.__es.onmessage) win.__es.onmessage({ data: JSON.stringify(o) }); };
const seg = async (text) => { await win.eval(`ingest(${JSON.stringify(text)}, [])`); await sleep(30); };
const whos = () => win.eval("transcript.map(s=>s.who)");
const floor = () => win.eval("floorWho");


(async () => {
  /* ---------- 1 质询环节：流程信息齐全也不走 floor，强制识别 ---------- */
  console.log("\n======== 1 质询环节强制走识别（cfg.attr=false 也要识别） ========");
  reset();
  fire({ action: "stage", kind: "quiz", stage: 3, side: "pro", seat: 3, target: { side: "con", seat: 1 }, floor: "hall", source: "display" });
  /* floor 故意设成「我方」；attribute 桩固定返回 opp。
     若仍按流程判定 → ours；若按识别 → opp。二者可区分。 */
  fire({ action: "floor", who: "pro", seat: 3, stage: 3, reason: "button", source: "display" });
  await sleep(10);
  ok("质询环节已识别为 quiz", win.eval("curKind") === "quiz", win.eval("curKind"));
  ok("floor 被设成 ours（模拟按钮误报成我方）", floor() === "ours", floor());
  win.__attrHits = 0;
  await seg("请问对方，你们所谓价值的主体究竟是谁？");
  ok("★质询环节不采信 floor，改用识别结果（opp）", whos()[0] === "opp", JSON.stringify(whos()));
  ok("★质询环节确实调用了 /api/attribute", win.__attrHits > 0, `hits=${win.__attrHits}`);

  /* ---------- 2 关闭 cfg.quizAttr → 退回流程判定 ---------- */
  console.log("\n======== 2 cfg.quizAttr=false → 退回流程判定 ========");
  reset();
  win.eval("cfg.quizAttr=false");
  fire({ action: "stage", kind: "quiz", stage: 3, side: "pro", seat: 3, target: { side: "con", seat: 1 }, floor: "hall", source: "display" });
  fire({ action: "floor", who: "pro", seat: 3, stage: 3, reason: "button", source: "display" });
  await sleep(10);
  win.__attrHits = 0;
  await seg("我方认为这项技术并未触及价值的内核。");
  ok("关闭后不调用 /api/attribute", win.__attrHits === 0, `hits=${win.__attrHits}`);
  ok("关闭后按 floor 归我方（与识别结果相反，证明确实退了回去）", whos()[0] === "ours", JSON.stringify(whos()));

  /* ---------- 3 质询环节 AI 不在场：不自动发言 / 不抢跑 ---------- */
  console.log("\n======== 3 质询环节我方二辩不在场 → 不接话 ========");
  reset();
  win.eval("cfg.streamMode=true; cfg.speakGap=0; cfg.streamMinChars=1");
  fire({ action: "stage", kind: "quiz", stage: 3, side: "pro", seat: 3, target: { side: "con", seat: 1 }, floor: "hall", source: "display" });
  await sleep(10);
  ok("aiOnStage=false", win.eval("aiOnStage") === false, String(win.eval("aiOnStage")));
  win.__genCalled = 0;
  await seg("对方刚才说的效率，恰恰是价值被压缩的证据，请问你怎么解释这一轮裁员潮？");
  await sleep(120);
  win.eval("runSpeakCheck()"); await sleep(120);
  ok("★质询环节不触发出稿", win.__genCalled === 0, `gen=${win.__genCalled}`);

  /* ---------- 4 人机对辩且我方是 AI 方 → 允许开口 ---------- */
  console.log("\n======== 4 人机对辩 ② 我方二辩在场 ========");
  reset();
  fire({ action: "stage", kind: "cross", stage: 6, side: "con", seat: 1, target: { side: "pro", seat: 2 }, floor: "hall", source: "display" });
  await sleep(10);
  ok("★target 指我方二辩 → aiOnStage=true", win.eval("aiOnStage") === true, String(win.eval("aiOnStage")));

  /* ---------- 5 人机对辩 ① 我方是「被 AI 对」的一方 → 不允许 ---------- */
  console.log("\n======== 5 人机对辩 ① 我方二辩不在场 ========");
  reset();
  fire({ action: "stage", kind: "cross", stage: 5, side: "pro", seat: 1, target: { side: "con", seat: 2 }, floor: "hall", source: "display" });
  await sleep(10);
  ok("★target 指对方二辩 → aiOnStage=false", win.eval("aiOnStage") === false, String(win.eval("aiOnStage")));

  /* ---------- 6 自由辩论 / 未连大屏 → 不受限 ---------- */
  console.log("\n======== 6 自由辩论与单机模式不受限 ========");
  reset();
  ok("未连大屏 aiOnStage=true", win.eval("aiOnStage") === true, String(win.eval("aiOnStage")));
  fire({ action: "stage", kind: "free", stage: 7, side: "both", floor: "hall", source: "display" });
  await sleep(10);
  ok("自由辩论 aiOnStage=true", win.eval("aiOnStage") === true, String(win.eval("aiOnStage")));

  /* ---------- 7 target 缺失时保守放行 ---------- */
  console.log("\n======== 7 大屏没带 target → 保守放行（不误伤） ========");
  reset();
  fire({ action: "stage", kind: "cross", stage: 5, side: "pro", seat: 1, floor: "hall", source: "display" });
  await sleep(10);
  ok("cross 且无 target → aiOnStage=true", win.eval("aiOnStage") === true, String(win.eval("aiOnStage")));

  /* ---------- 8 停止倾听后复位 ---------- */
  console.log("\n======== 8 停止倾听复位环节状态 ========");
  reset();
  fire({ action: "stage", kind: "forced", stage: 0, side: "pro", seat: 1, floor: "hall", source: "display" });
  await sleep(10);
  ok("立论环节 aiOnStage=false", win.eval("aiOnStage") === false, String(win.eval("aiOnStage")));
  win.eval("curTarget=null; curStageName=''; aiOnStage=true;");
  ok("复位后 aiOnStage=true", win.eval("aiOnStage") === true, String(win.eval("aiOnStage")));

  const pass = R.filter(Boolean).length;
  console.log(`\n结果：${pass} 通过 / ${R.length - pass} 失败`);
  process.exit(pass === R.length ? 0 : 1);
})();
