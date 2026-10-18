/* 分段续写（边播边生成）实测
   目标：抢跑只生成前一段；开口时把「已说内容 + 开口后对方新增发言」交给模型续写下一段，
        续写结果直接接进同一播放时间轴。
   跑法：cd tools && node test-continuation.cjs [页面文件]                           */
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

const PART1 = ["对方把效率提升当成价值提升，这两件事并不等价。",
               "效率解决的是做得快，价值问的是做得对不对。"];
const PART2 = ["对方还说伦理规范会随着技术一起进步，那么请问，规范是被谁写的呢？",
               "写规范的人自己也在用同一套工具，这就成了循环论证。"];

function sse(sentences) {
  const c = [`data: ${JSON.stringify({ type: "stage", stage: "draft", status: "start" })}\n\n`];
  sentences.forEach((t, i) => c.push(`data: ${JSON.stringify({ type: "sentence", idx: i + 1, text: t, first: i === 0 })}\n\n`));
  c.push(`data: ${JSON.stringify({ type: "done", elapsed: 3, sentences, meta: { chars: sentences.join("").length, first_sentence_ms: 800 } })}\n\n`);
  const enc = new TextEncoder(); let i = 0;
  return { ok: true, status: 200, headers: { get: () => null },
    body: { getReader: () => ({ read: async () => (i < c.length ? { value: enc.encode(c[i++]), done: false } : { done: true }) }) },
    text: async () => "", json: async () => ({}) };
}

const dom = new JSDOM(HTML, {
  runScripts: "dangerously", pretendToBeVisual: true,
  url: "http://localhost/debate/local/debate-local.html",
  beforeParse(win) {
    win.__reqs = []; win.__spk = 0; win.__plays = 0;
    class Gain { constructor() { this.gain = { value: 1, setValueAtTime() {}, linearRampToValueAtTime() {} }; } connect() { return this; } disconnect() {} }
    win.AudioContext = class {
      constructor() { this.state = "running"; this.currentTime = 0; this.sampleRate = 48000; this.destination = {}; }
      createGain() { return new Gain(); } createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
      createMediaStreamDestination() { return { stream: { getTracks: () => [] } }; }
      createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; }
      createBufferSource() { return { buffer: null, connect() {}, start() {}, stop() {}, onended: null }; }
      createBuffer(c, l) { return { duration: l / 48000, getChannelData: () => new Float32Array(l) }; }
      decodeAudioData() { return Promise.resolve({ duration: 1, getChannelData: () => new Float32Array(1) }); }
      resume() { return Promise.resolve(); } close() { return Promise.resolve(); }
    };
    win.HTMLMediaElement.prototype.play = function () { win.__plays++; return Promise.resolve(); };
    win.HTMLMediaElement.prototype.pause = function () {};
    win.MediaStream = class { getTracks() { return []; } };
    Object.defineProperty(win.navigator, "mediaDevices", { value: { getUserMedia: async () => new win.MediaStream() }, configurable: true });
    win.URL.createObjectURL = () => "blob:stub"; win.URL.revokeObjectURL = () => {};
    win.EventSource = class { constructor(u) { this.url = u; this.onmessage = null; win.__es = this; } close() {} };
    win.fetch = async (url, opt = {}) => {
      const u = String(url);
      let body = {}; try { body = JSON.parse(opt.body || "{}"); } catch (e) {}
      if (u.includes("/api/rebut-omni")) { win.__reqs.push({ kind: "omni", body }); return mkRes({ error: "x" }, false); }
      if (u.includes("/api/rebut")) {
        win.__reqs.push({ kind: "rebut", body });
        return sse(body.continuation ? PART2 : PART1);          // 第二次请求=续写
      }
      if (u.includes("/api/attribute")) return mkRes({ items: [{ idx: 0, who: "opp", sent: ["opp"] }] });
      if (u.includes("/api/should-speak")) return mkRes({ speak: false });
      if (u.includes("/api/buzzer")) return mkRes({ ok: true });
      if (u.includes("/api/transcript-save")) return mkRes({ ok: true });
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
    cfg.floorMode=true; cfg.attr=false; cfg.omni=false; cfg.streamMode=true;
    cfg.chunked=true; cfg.chunkRatio=0.45; cfg.duration=45;
    cfg.streamMinChars=12; cfg.streamGapMs=0; cfg.speakCooldown=0; cfg.freeClaimDelay=700;
    cfg.ttsModel=""; cfg.voice=""; cfg.rate="+8%"; cfg.emotion="";
    transcript=[]; queue.length=0; lastSentences=[]; preHold=false; genBusy=false; playing=false;
    lastSpeakAt=0; lastIngestAt=Date.now(); lastPlayEndAt=0; stopped=false;
    window.__reqs=[]; window.__spk=0; window.__plays=0;
    resetContinuation();
    genRebut=Object.getPrototypeOf(genRebut)===Function.prototype ? genRebut : genRebut;
  `);
}
const reqs = (kind) => win.eval("JSON.stringify(window.__reqs)") && win.__reqs.filter(r => !kind || r.kind === kind);

(async () => {
  /* ---------- 1 抢跑请求带分段时长 ---------- */
  console.log("\n======== 1 抢跑只写前一段（chunk_sec）========");
  reset();
  win.eval(`transcript.push({who:"opp", text:"效率提升就是价值提升。", raw:"效率提升就是价值提升。", t:Date.now()});`);
  await win.eval("genRebut(true, {pre:true})");
  await sleep(120);
  const r1 = reqs("rebut")[0];
  ok("抢跑请求带 chunk_sec", r1 && r1.body.chunk_sec === Math.round(45 * 0.45), r1 ? String(r1.body.chunk_sec) : "无请求");
  ok("首段稿已就绪（2 句）", win.eval("lastSentences.length") === 2, String(win.eval("lastSentences.length")));
  ok("已挂上续写状态（armed）", win.eval("!!contState && contState.started===false"));

  /* ---------- 2 开口 → 触发续写，且带已说内容 ---------- */
  console.log("\n======== 2 我方开口 → 结合对方新发言续写 ========");
  // 开口之后对方又补了一句（这是关键：续写必须看到它）
  win.eval(`transcript.push({who:"opp", text:"而且伦理规范会跟着技术进步。", raw:"而且伦理规范会跟着技术进步。", t:Date.now()});`);
  win.eval("speakNow()");
  for (let i = 0; i < 60 && reqs("rebut").length < 2; i++) await sleep(20);
  const r2 = reqs("rebut")[1];
  ok("开口后自动发起续写请求", !!r2);
  ok("续写带 continuation.part=2", r2 && r2.body.continuation && r2.body.continuation.part === 2);
  ok("续写带上「已说内容」（避免重复）",
    r2 && r2.body.continuation.said.includes("效率解决的是做得快"), (r2 && r2.body.continuation.said || "").slice(0, 20));
  ok("续写带上「我方开口后对方新增的发言」",
    r2 && r2.body.continuation.fresh.includes("伦理规范"), (r2 && r2.body.continuation.fresh || "").slice(0, 20));
  ok("续写时长 = 总时长 − 首段", r2 && r2.body.continuation.remain_sec > 0 && r2.body.continuation.remain_sec < 45,
    r2 ? String(r2.body.continuation.remain_sec) : "-");

  /* ---------- 3 续写结果接入同一时间轴 ---------- */
  console.log("\n======== 3 续写结果直接接进播放队列 ========");
  for (let i = 0; i < 100 && win.eval("lastSentences.length") < 4; i++) await sleep(20);
  ok("稿子从 2 句扩展到 4 句", win.eval("lastSentences.length") === 4, String(win.eval("lastSentences.length")));
  ok("新句子进入播放队列或被消费（不丢句）",
    win.eval("queue.length") + win.eval("lastSentences.length") >= 4,
    `queue=${win.eval("queue.length")}`);
  ok("第 3 句就是续写内容", win.eval("lastSentences[2].text").includes("伦理规范"),
    win.eval("lastSentences[2].text").slice(0, 18));

  /* ---------- 4 关闭分段续写 → 回到一次性出稿 ---------- */
  console.log("\n======== 4 关闭「分段续写」→ 一次写全稿 ========");
  reset();
  win.eval(`cfg.chunked=false;
    transcript.push({who:"opp", text:"效率提升就是价值提升。", raw:"效率提升就是价值提升。", t:Date.now()});`);
  await win.eval("genRebut(true, {pre:true})");
  await sleep(120);
  const r3 = reqs("rebut")[0];
  ok("不带 chunk_sec（整篇一次生成）", r3 && !r3.body.chunk_sec, r3 ? String(r3.body.chunk_sec) : "-");
  ok("不挂续写状态", win.eval("!contState"), String(win.eval("!!contState")));
  win.eval("speakNow()");
  await sleep(150);
  ok("不会再发第二次请求", reqs("rebut").length === 1, String(reqs("rebut").length));

  /* ---------- 5 首段占比可调 ---------- */
  console.log("\n======== 5 首段占比可调（0.30 / 0.60）========");
  reset();
  win.eval(`cfg.chunkRatio=0.30;
    transcript.push({who:"opp", text:"效率提升就是价值提升。", raw:"x", t:Date.now()});`);
  await win.eval("genRebut(true, {pre:true})");
  await sleep(100);
  const a = reqs("rebut")[0].body.chunk_sec;
  reset();
  win.eval(`cfg.chunkRatio=0.60;
    transcript.push({who:"opp", text:"效率提升就是价值提升。", raw:"x", t:Date.now()});`);
  await win.eval("genRebut(true, {pre:true})");
  await sleep(100);
  const b = reqs("rebut")[0].body.chunk_sec;
  ok("0.30 → 更短首段", a === Math.round(45 * 0.3), String(a));
  ok("0.60 → 更长首段", b === Math.round(45 * 0.6), String(b));

  const pass = R.filter(Boolean).length;
  console.log(`\n结果：${pass} 通过 / ${R.length - pass} 失败`);
  process.exit(pass === R.length ? 0 : 1);
})();
