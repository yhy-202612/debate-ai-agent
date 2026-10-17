/* 自由辩论抢话规则实测（级联通道）
   规则一：AI 没抢到发言权 → 立刻阻断已在播/准备播的发言
   规则二：稿子还没生成 → AI 不参与抢答（不 check）
   规则三：抢到且稿已备好 → 立即开口
   跑法：cd tools && node test-free-grab.cjs                                        */
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
const SPEECH = ["对方把效率提升当成价值提升，这两件事并不等价。",
  "效率解决的是做得快，价值问的是做得对不对。", "当判断被工具接管，人让出的是主权。"];

function sseRebutt() {
  const c = [`data: ${JSON.stringify({ type: "stage", stage: "draft", status: "start" })}\n\n`];
  SPEECH.forEach((t, i) => c.push(`data: ${JSON.stringify({ type: "sentence", idx: i + 1, text: t, first: i === 0 })}\n\n`));
  c.push(`data: ${JSON.stringify({ type: "done", meta: { ms: 6000 } })}\n\n`);
  const enc = new TextEncoder(); let i = 0;
  return { ok: true, status: 200, headers: { get: () => null },
    body: { getReader: () => ({ read: async () => (i < c.length ? { value: enc.encode(c[i++]), done: false } : { done: true }) }) },
    text: async () => "", json: async () => ({}) };
}

const dom = new JSDOM(HTML, {
  runScripts: "dangerously", pretendToBeVisual: true,
  url: "http://localhost/debate/local/debate-local.html",
  beforeParse(win) {
    win.__posts = [];
    class Gain { constructor() { this.gain = { value: 1, setValueAtTime() {}, linearRampToValueAtTime() {} }; } connect() { return this; } disconnect() {} }
    win.AudioContext = class {
      constructor() { this.state = "running"; this.currentTime = 0; this.sampleRate = 48000; this.destination = {}; }
      createGain() { return new Gain(); } createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
      createMediaStreamDestination() { return { stream: { getTracks: () => [] } }; }
      createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; }
      createBufferSource() { return { buffer: null, connect() {}, start() { win.__spk = (win.__spk || 0) + 1; }, stop() {}, onended: null }; }
      createBuffer(c, l, r) { return { duration: l / 48000, getChannelData: () => new Float32Array(l) }; }
      decodeAudioData() { return Promise.resolve({ duration: 10, getChannelData: () => new Float32Array(10) }); }
      resume() { return Promise.resolve(); } close() { return Promise.resolve(); }
    };
    win.HTMLMediaElement.prototype.play = function () { win.__plays = (win.__plays || 0) + 1; return Promise.resolve(); };
    win.HTMLMediaElement.prototype.pause = function () { win.__paused = (win.__paused || 0) + 1; };
    win.MediaStream = class { getTracks() { return []; } };
    Object.defineProperty(win.navigator, "mediaDevices", { value: { getUserMedia: async () => new win.MediaStream() }, configurable: true });
    win.URL.createObjectURL = () => "blob:stub"; win.URL.revokeObjectURL = () => {};
    win.EventSource = class { constructor(u) { this.url = u; this.onmessage = null; win.__es = this; } close() {} };
    win.fetch = async (url, opt = {}) => {
      const u = String(url);
      if (u.includes("/api/buzzer")) { try { win.__posts.push(JSON.parse(opt.body || "{}")); } catch (e) {} return mkRes({ ok: true }); }
      if (u.includes("/api/rebut-omni")) return mkRes({ error: "x" }, false);
      if (u.includes("/api/rebut")) return sseRebutt();
      if (u.includes("/api/tts-stream")) {
        const enc = new TextEncoder();
        return { ok: true, status: 200, headers: { get: () => null }, body: { getReader: () => {
          let s = 0; return { read: async () => { if (s === 0) { s++; return { value: enc.encode("RIFF"), done: false }; } return { done: true }; } };
        } }, text: async () => "", json: async () => ({}) };
      }
      if (u.includes("/api/tts")) return { ok: true, status: 200, blob: async () => ({ size: 1024 }), headers: { get: () => null }, text: async () => "" };
      if (u.includes("/api/attribute")) return mkRes({ items: [{ idx: 0, who: "opp", sent: ["opp"] }] });
      if (u.includes("/api/should-speak")) return mkRes({ speak: false });
      return mkRes({ ok: true });
    };
    win.alert = () => {}; win.confirm = () => true;
  },
});

const win = dom.window;
const R = []; const ok = (n, c, e = "") => { R.push(!!c); console.log((c ? "✓" : "✗") + " " + n + (e ? "  " + e : "")); };
function reset() {
  win.eval(`
    cfg.mode="auto"; cfg.streamMode=true; cfg.diarize=false; cfg.omni=false; cfg.side="正方";
    cfg.streamMinChars=20; cfg.streamGapMs=7000; cfg.streamTts=true; cfg.speakCooldown=0;
    transcript=[]; queue.length=0; lastSentences=[]; preHold=false; genBusy=false; playing=false;
    lastSpeakAt=0; lastStreamAt=0; streamAtOppChars=0; lastPlayEndAt=0; stopped=false;
    freeMode=false; freeOwner=""; claimPending=false; ttsCache.clear();
    window.__plays=0; window.__spk=0; window.__paused=0; window.__posts=[];
  `);
}
function fire(ev) { win.__es.onmessage({ data: JSON.stringify(ev) }); }
async function makeDraft() {                        // 造出抢跑稿 + 预合成
  await win.eval(`ingest("对方辩友说人工智能可以提高效率，解放人的重复劳动。", [])`);
  for (let i = 0; i < 60 && win.eval("queue.length") < 3; i++) await sleep(25);
}

(async () => {
  /* ---------- 1 没稿 → 不参与抢答 ---------- */
  console.log("======== 1 稿子没生成 → AI 不参与抢答 ========");
  reset();
  fire({ action: "stage", kind: "free", source: "display" });
  ok("进入自由辩论模式", win.eval("freeMode") === true);
  fire({ action: "free_idle", source: "display" });      // 发言权空出
  await sleep(120);
  const claims = win.eval("window.__posts").filter(p => p.action === "claim");
  ok("无稿时不发 claim", claims.length === 0, `claims=${claims.length}`);

  /* ---------- 2 有稿 → 参与抢答 ---------- */
  console.log("\n======== 2 稿子已备好 → 参与抢答 ========");
  reset(); fire({ action: "stage", kind: "free", source: "display" });
  await makeDraft();
  ok("抢跑稿已入队", win.eval("queue.length") >= 3, `队列 ${win.eval("queue.length")} 句`);
  fire({ action: "free_idle", source: "display" });
  await sleep(150);
  const c2 = win.eval("window.__posts").filter(p => p.action === "claim");
  ok("有稿时才发 claim", c2.length >= 1, `claims=${c2.length} side=${c2[0] && c2[0].side}`);

  /* ---------- 3 抢到 + 稿在 → 开口 ---------- */
  console.log("\n======== 3 抢到发言权 → 立刻开口 ========");
  const t0 = Date.now();
  fire({ action: "free_grant", side: "正方", source: "display" });
  let spoke = false;
  for (let i = 0; i < 200; i++) { await sleep(5); if (win.eval("window.__plays") > 0 || win.eval("window.__spk") > 0) { spoke = true; break; } }
  ok("抢到后开口", spoke, spoke ? `${Date.now() - t0} ms` : "未出声");

  /* ---------- 4 AI 正在说，对方抢到 → 立刻阻断 ---------- */
  console.log("\n======== 4 AI 正在播报，对方抢到发言权 → 立刻阻断 ========");
  ok("阻断前处于播报中", win.eval("playing") === true);
  const t1 = Date.now();
  fire({ action: "free_grant", side: "反方", source: "display" });   // 对方抢到
  let aborted = false;
  for (let i = 0; i < 120; i++) { await sleep(5); if (win.eval("playing") === false) { aborted = true; break; } }
  ok("对方抢到 → 播报被中止", aborted, `${Date.now() - t1} ms`);
  ok("队列已清空（不会再续播）", win.eval("queue.length") === 0);
  ok("状态提示已阻断", /阻断/.test(win.eval('$("speakState").textContent')));

  /* ---------- 5 被 deny → 也无法开口 ---------- */
  console.log("\n======== 5 抢话被拒（deny）→ 不开口 ========");
  reset(); fire({ action: "stage", kind: "free", source: "display" });
  await makeDraft();
  win.eval("window.__plays=0; window.__spk=0;");
  fire({ action: "free_deny", side: "正方", source: "display" });
  await sleep(250);
  ok("deny 后不开口", win.eval("window.__plays") === 0 && win.eval("window.__spk") === 0);

  /* ---------- 6 非自由辩论环节：down 仍直接开口（不破坏人机对辩） ---------- */
  console.log("\n======== 6 人机对辩环节：对方按按钮 → 仍直接开口 ========");
  reset(); fire({ action: "stage", kind: "cross", source: "display" });
  await makeDraft();
  win.eval("window.__plays=0; window.__spk=0;");
  fire({ action: "down", side: "反方", source: "button" });
  let spoke2 = false;
  for (let i = 0; i < 200; i++) { await sleep(5); if (win.eval("window.__plays") > 0 || win.eval("window.__spk") > 0) { spoke2 = true; break; } }
  ok("非自由辩论 down 仍直接开口", spoke2);

  /* ---------- 7 自由辩论内普通 down 不直接开口 ---------- */
  console.log("\n======== 7 自由辩论内收到 down → 不直接开口（等仲裁） ========");
  reset(); fire({ action: "stage", kind: "free", source: "display" });
  await makeDraft();
  win.eval("window.__plays=0; window.__spk=0;");
  fire({ action: "down", side: "反方", source: "button" });
  await sleep(250);
  ok("自由辩论 down 不直接开口", win.eval("window.__plays") === 0 && win.eval("window.__spk") === 0);

  const pass = R.filter(Boolean).length;
  console.log(`\n结果：${pass} 通过 / ${R.length - pass} 失败`);
  process.exit(pass === R.length ? 0 : 1);
})();
