/* 自由辩论 · 抢跑模式时序实测（非端到端 / 级联通道）
   目标：测「对方最后一片转写到达」→「我方开始播报」的真实间隔，
   以及抢跑稿是否真的在对方说完时刻被放出（而不是再走一次 18 秒的出稿）。
   跑法：cd tools && node test-free-debate-timing.cjs                      */
const fs = require("fs");
const path = require("path");
let JSDOM;
try { ({ JSDOM } = require("jsdom")); }
catch (e) { ({ JSDOM } = require(path.join(process.cwd(), "node_modules", "jsdom"))); }

const HTML_PATH = process.argv[2] || path.join(__dirname, "..", "web", "local", "debate-local.html");
const HTML = fs.readFileSync(HTML_PATH, "utf8");
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
function mkRes(body, ok = true) {
  return { ok, status: ok ? 200 : 500, json: async () => body, text: async () => JSON.stringify(body),
           headers: { get: () => null }, body: null };
}

/* 抢跑期间出稿：模拟 LLM 已产出 3 句（首句 latency 记 0，重点测释放时刻） */
const SPEECH = {
  sentences: [
    "对方把效率提升当成价值提升，这两件事并不等价。",
    "效率解决的是做得快，价值问的是做得对不对、有没有意义。",
    "当判断被工具接管，人省下的是力气，让出的是主权。",
  ],
};
function sseRebutt() {
  const chunks = [];
  chunks.push(`data: ${JSON.stringify({ type: "stage", stage: "draft", status: "start" })}\n\n`);
  SPEECH.sentences.forEach((t, i) =>
    chunks.push(`data: ${JSON.stringify({ type: "sentence", idx: i + 1, text: t, first: i === 0 })}\n\n`));
  chunks.push(`data: ${JSON.stringify({ type: "done", meta: { mode: "one_shot", ms: 8000, chars: 90 } })}\n\n`);
  const enc = new TextEncoder();
  let i = 0;
  return {
    ok: true, status: 200, headers: { get: () => null },
    body: { getReader: () => ({ read: async () => (i < chunks.length ? { value: enc.encode(chunks[i++]), done: false } : { done: true }) }) },
    text: async () => "", json: async () => ({}),
  };
}

const dom = new JSDOM(HTML, {
  runScripts: "dangerously", pretendToBeVisual: true,
  url: "http://localhost/debate/local/debate-local.html",
  beforeParse(win) {
    class Gain { constructor() { this.gain = { value: 1, setValueAtTime() {}, linearRampToValueAtTime() {} }; } connect() { return this; } disconnect() {} }
    win.__spkCalls = 0;
    win.AudioContext = class {
      constructor() { this.state = "running"; this.currentTime = 0; this.sampleRate = 48000; this.destination = {}; }
      createGain() { return new Gain(); }
      createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
      createMediaStreamDestination() { return { stream: { getTracks: () => [] } }; }
      createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; }
      createBufferSource() { return { buffer: null, connect() {}, start() { win.__spkCalls++; }, stop() {}, onended: null }; }
      createBuffer(c, l, r) { return { duration: l / (r || 48000), getChannelData: () => new Float32Array(l) }; }
      decodeAudioData() { return Promise.resolve({ duration: 10, getChannelData: () => new Float32Array(10) }); }
      resume() { return Promise.resolve(); } close() { return Promise.resolve(); }
    };
    win.__mediaPlays = 0; win.__mediaAt = 0;
    win.HTMLMediaElement.prototype.play = function () { win.__mediaPlays++; win.__mediaAt = Date.now(); return Promise.resolve(); };
    win.MediaStream = class { getTracks() { return []; } };
    Object.defineProperty(win.navigator, "mediaDevices", { value: { getUserMedia: async () => new win.MediaStream() }, configurable: true });
    win.URL.createObjectURL = () => "blob:stub"; win.URL.revokeObjectURL = () => {};
    win.fetch = async (url) => {
      const u = String(url);
      if (u.includes("/api/rebut-omni")) return mkRes({ error: "未启用" }, false);
      if (u.includes("/api/rebut")) return sseRebutt();
      if (u.includes("/api/tts-stream")) {
        /* 流式 TTS：模拟首包 TTFB 80ms（线上实测 70~290ms），随后给 0.2s PCM */
        const SAMPLE_RATE = 24000, DATA_LEN = SAMPLE_RATE * 2 * 0.2;   // 0.2s
        const hdr = new DataView(new ArrayBuffer(44));
        const wr = (o, str) => { for (let i = 0; i < str.length; i++) hdr.setUint8(o + i, str.charCodeAt(i)); };
        wr(0, "RIFF"); hdr.setUint32(4, 36 + DATA_LEN, true); wr(8, "WAVE"); wr(12, "fmt ");
        hdr.setUint32(16, 16, true); hdr.setUint16(20, 1, true); hdr.setUint16(22, 1, true);
        hdr.setUint32(24, SAMPLE_RATE, true); hdr.setUint32(28, SAMPLE_RATE * 2, true);
        hdr.setUint16(32, 2, true); hdr.setUint16(34, 16, true); wr(36, "data");
        hdr.setUint32(40, DATA_LEN, true);
        const head = new Uint8Array(hdr.buffer);
        const pcm = new Uint8Array(DATA_LEN);
        for (let i = 0; i < DATA_LEN; i += 2) { const v = Math.round(Math.sin(i / 20) * 8000); pcm[i] = v & 255; pcm[i + 1] = (v >> 8) & 255; }
        return { ok: true, status: 200, headers: { get: () => null },
          body: { getReader: () => {
            let step = 0;
            return { read: async () => {
              if (step === 0) { step++; return { value: head, done: false }; }
              await sleep(80);
              if (step === 1) { step++; return { value: pcm, done: false }; }
              return { done: true };
            } };
          } },
          text: async () => "", json: async () => ({}) };
      }
      if (u.includes("/api/tts")) return { ok: true, status: 200, blob: async () => ({ size: 1000 }), headers: { get: () => null }, text: async () => "" };
      if (u.includes("/api/attribute")) {
        return mkRes({ items: [{ idx: 0, who: "opp", sent: ["opp"] }] });
      }
      if (u.includes("/api/should-speak")) return mkRes({ speak: true, reason: "stub" });
      if (u.includes("/api/summarize")) return mkRes({ summary: {} });
      if (u.includes("/api/voices") || u.includes("/api/models") || u.includes("/healthz")) return mkRes({});
      return mkRes({ ok: true });
    };
    win.alert = () => {}; win.confirm = () => true;
  },
});

const win = dom.window;
const results = [];
const ok = (name, cond, extra = "") => { results.push({ name, pass: !!cond, extra }); console.log((cond ? "✓" : "✗") + " " + name + (extra ? "  " + extra : "")); };

async function runCase(label, { streamTts, gapMs }) {
  console.log(`\n======== ${label} ========`);
  win.eval(`
    cfg.mode="auto"; cfg.streamMode=true; cfg.diarize=false; cfg.attr=true; cfg.floorMode=false; cfg.side="正方"; cfg.omni=false;
    cfg.streamMinChars=20; cfg.streamGapMs=${gapMs}; cfg.streamTts=${streamTts ? "true" : "false"};
    cfg.speakGap=2200; cfg.speakCooldown=0; cfg.summaryEnabled=false;
    transcript=[]; queue.length=0; lastSentences=[]; preAudioBuf=[]; preHold=false;
    genBusy=false; playing=false; stopped=false; lastSpeakAt=0; lastStreamAt=0;
    streamAtOppChars=0; lastPlayEndAt=0; window.__genCount=0; window.__spkCalls=0; window.__mediaPlays=0; window.__mediaAt=0; stopped=false;
  `);

  /* ① 对方正在发言（一个完整句子，≥12 字且末句收束）→ 应触发抢跑 */
  const tA = Date.now();
  await win.eval(`ingest("对方辩友说人工智能可以提高效率，解放人的重复劳动。", [])`);
  const tookA = Date.now() - tA;
  await sleep(400);   // 等 stub SSE 出完 3 句入队

  const preHold1 = win.eval("preHold");
  const q1 = win.eval("queue.length");
  const playing1 = win.eval("playing");
  ok("① 对方说到完整句 → 触发抢跑（preHold 置位）", preHold1 === true);
  ok("① 抢跑稿已入队但未播出（只攒不播）", q1 >= 3 && playing1 === false, `队列 ${q1} 句`);

  /* ② 对方最后一片到达（说完）→ 记 T0 */
  const T0 = Date.now();
  await win.eval(`ingest("所以效率提升就是人的价值的提升。", [])`);

  /* ③ 轮询：我方何时开始播报 */
  let T1 = -1, T2 = -1;
  for (let i = 0; i < 300; i++) {           // 最多等 6s
    await sleep(20);
    if (T1 < 0 && win.eval("playing") === true) T1 = Date.now();
    if (T1 >= 0 && T2 < 0 && (win.eval("window.__spkCalls") > 0 || win.eval("window.__mediaPlays") > 0)) { T2 = Date.now(); break; }
  }
  ok("② 抢跑稿被放出（进入播报）", T1 > 0, T1 > 0 ? `说完→开播 ${T1 - T0}ms` : "未放出");
  ok("③ 音频开始输出", T2 > 0, T2 > 0 ? `开播→出声 ${T2 - T1}ms｜说完→出声 ${T2 - T0}ms` : "无声");

  /* ④ 必须「不再走一次出稿」——出稿次数应只有抢跑那 1 次 */
  const genCount = win.eval("window.__genCount || 0");
  ok("④ 说出后没有重新出稿（用的就是抢跑稿）", genCount <= 1, `出稿 ${genCount} 次`);

  return { label, T0, T1, T2, tookA, queueLen: q1, total: T2 - T0 };
}

/* 风险场景：自由辩论里常见的「对方只说半句」「连续快速抢话」会不会让抢跑失效 */
async function runRisk(label, fn) {
  console.log(`\n======== ${label} ========`);
  win.eval(`
    cfg.mode="auto"; cfg.streamMode=true; cfg.diarize=false; cfg.attr=true; cfg.floorMode=false; cfg.side="正方"; cfg.omni=false;
    cfg.streamMinChars=20; cfg.streamGapMs=7000; cfg.streamTts=true;
    transcript=[]; queue.length=0; preHold=false; genBusy=false; playing=false;
    lastSpeakAt=0; lastStreamAt=0; streamAtOppChars=0; window.__genCount=0;
  `);
  await fn();
  return win.eval("preHold");
}

(async () => {
  const rows = [];
  rows.push(await runCase("A 默认（流式 TTS，逐句边收边播）", { streamTts: true, gapMs: 7000 }));
  rows.push(await runCase("B 整段合成（出句即预取 TTS）", { streamTts: false, gapMs: 7000 }));
  rows.push(await runCase("C 自由辩论快节奏（抢跑间隔 7s，对方短促反驳）", { streamTts: true, gapMs: 7000 }));

  /* ---- 风险场景 ---- */
  const shortOk = await runRisk("D 对方只说了半句（8 字、未成句）", async () => {
    await win.eval(`ingest("嗯，我觉得吧。", [])`);
    await sleep(300);
  });
  ok("D 半句话不触发抢跑（避免拿半截上下文出网稿）", shortOk === false);

  const fastOk = await runRisk("E 自由辩论快节奏：7 秒内连续两次抢话", async () => {
    await win.eval(`ingest("对方辩友说人工智能可以提高效率，解放人的重复劳动。", [])`);
    await sleep(300);
    const first = win.eval("preHold");
    win.eval("preHold=false; queue.length=0; genBusy=false;");
    await win.eval(`ingest("但是请问对方，效率提升就等于价值提升吗？这种推论是不是太快了。", [])`);
    await sleep(300);
    const second = win.eval("preHold");
    const gap = Date.now() - win.eval("lastStreamAt");
    win.eval("window.__seedFirst=" + (first ? 1 : 0) + "; window.__seedSecond=" + (second ? 1 : 0));
  });
  const seedFirst = win.eval("window.__seedFirst"), seedSecond = win.eval("window.__seedSecond");
  ok("E 第一次抢跑成功", seedFirst === 1);
  ok("E 第二次在 7s 门槛内被挡下（需等间隔）", seedSecond === 0,
     "→ 快节奏自由辩论中，第 2 次起会退回完整出稿链路");

  console.log("\n================ 汇总 ================");
  rows.forEach(r => console.log(
    `${r.label}\n   说完→开播 ${r.T1 - r.T0}ms │ 开播→出声 ${r.T2 - r.T1}ms │ 说完→出声 ${r.total}ms │ 抢跑稿 ${r.queueLen} 句`));
  const fail = results.filter(r => !r.pass).length;
  console.log(`\n结果：${results.length - fail} 通过 / ${fail} 失败`);
  process.exit(fail ? 1 : 0);
})();
