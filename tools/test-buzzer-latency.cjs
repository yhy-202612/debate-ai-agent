/* 实体发令按钮 → AI 开口 的延迟实测（级联通道 / 非端到端）
   现场口径：同学发言结束 → 按本侧抢答器按钮 → 大屏 POST /api/buzzer → 智能体订阅到 → 立刻开口。
   本脚本测「信号到达前端」到「音频开始播放」这一段（网络与串口在线上另测）。
   跑法：cd tools && node test-buzzer-latency.cjs                                  */
const fs = require("fs");
const path = require("path");
let JSDOM;
try { ({ JSDOM } = require("jsdom")); }
catch (e) { ({ JSDOM } = require(path.join(process.cwd(), "node_modules", "jsdom"))); }

const FILE = process.argv[2] || path.join(__dirname, "..", "web", "local", "debate-local.html");
const HTML = fs.readFileSync(FILE, "utf8");
const TTS_TTFB = Number(process.argv[3] || 120);      // 模拟 TTS 首包/整段合成耗时（线上实测 100~120ms）
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const mkRes = (b, ok = true) => ({ ok, status: ok ? 200 : 500, json: async () => b,
  text: async () => JSON.stringify(b), headers: { get: () => null }, body: null });

const SPEECH = ["对方把效率提升当成价值提升，这两件事并不等价。",
  "效率解决的是做得快，价值问的是做得对不对。", "当判断被工具接管，人让出的是主权。"];

function sseRebutt() {
  const c = [`data: ${JSON.stringify({ type: "stage", stage: "draft", status: "start" })}\n\n`];
  SPEECH.forEach((t, i) => c.push(`data: ${JSON.stringify({ type: "sentence", idx: i + 1, text: t, first: i === 0 })}\n\n`));
  c.push(`data: ${JSON.stringify({ type: "done", meta: { ms: 8000 } })}\n\n`);
  const enc = new TextEncoder(); let i = 0;
  return { ok: true, status: 200, headers: { get: () => null },
    body: { getReader: () => ({ read: async () => (i < c.length ? { value: enc.encode(c[i++]), done: false } : { done: true }) }) },
    text: async () => "", json: async () => ({}) };
}

const dom = new JSDOM(HTML, {
  runScripts: "dangerously", pretendToBeVisual: true,
  url: "http://localhost/debate/local/debate-local.html",
  beforeParse(win) {
    win.__playAt = 0; win.__playCount = 0; win.__buzzPosts = []; win.__spkCalls = 0;
    class Gain { constructor() { this.gain = { value: 1, setValueAtTime() {}, linearRampToValueAtTime() {} }; } connect() { return this; } disconnect() {} }
    win.AudioContext = class {
      constructor() { this.state = "running"; this.currentTime = 0; this.sampleRate = 48000; this.destination = {}; }
      createGain() { return new Gain(); } createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
      createMediaStreamDestination() { return { stream: { getTracks: () => [] } }; }
      createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; }
      createBufferSource() { return { buffer: null, connect() {}, start() { win.__spkCalls++; }, stop() {}, onended: null }; }
      createBuffer(c, l, r) { return { duration: l / 48000, getChannelData: () => new Float32Array(l) }; }
      decodeAudioData() { return Promise.resolve({ duration: 10, getChannelData: () => new Float32Array(10) }); }
      resume() { return Promise.resolve(); } close() { return Promise.resolve(); }
    };
    /* 用真实 <audio> 的 play() 作为「出声」时刻的代理 */
    win.HTMLMediaElement.prototype.play = function () { win.__playAt = Date.now(); win.__playCount++; return Promise.resolve(); };
    win.MediaStream = class { getTracks() { return []; } };
    Object.defineProperty(win.navigator, "mediaDevices", { value: { getUserMedia: async () => new win.MediaStream() }, configurable: true });
    win.URL.createObjectURL = () => "blob:stub"; win.URL.revokeObjectURL = () => {};
    /* 订阅通道：保留实例，测试里手动派发事件 */
    win.EventSource = class { constructor(u) { this.url = u; this.onmessage = null; win.__es = this; } close() { this.closed = true; } };
    win.fetch = async (url, opt = {}) => {
      const u = String(url);
      if (u.includes("/api/tts-stream")) {
        const enc = new TextEncoder();
        const SAMPLE = 24000, LEN = SAMPLE * 2 * 0.2;
        const dv = new DataView(new ArrayBuffer(44));
        const wr = (o, s) => { for (let k = 0; k < s.length; k++) dv.setUint8(o + k, s.charCodeAt(k)); };
        wr(0, "RIFF"); dv.setUint32(4, 36 + LEN, true); wr(8, "WAVE"); wr(12, "fmt ");
        dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
        dv.setUint32(24, SAMPLE, true); dv.setUint32(28, SAMPLE * 2, true);
        dv.setUint16(32, 2, true); dv.setUint16(34, 16, true); wr(36, "data"); dv.setUint32(40, LEN, true);
        const head = new Uint8Array(dv.buffer), pcm = new Uint8Array(LEN);
        for (let i = 0; i < LEN; i += 2) { const v = Math.round(Math.sin(i / 20) * 8000); pcm[i] = v & 255; pcm[i + 1] = (v >> 8) & 255; }
        return { ok: true, status: 200, headers: { get: () => null }, body: { getReader: () => {
          let s = 0; return { read: async () => { if (s === 0) { s++; return { value: head, done: false }; }
            await sleep(TTS_TTFB); if (s === 1) { s++; return { value: pcm, done: false }; } return { done: true }; } };
        } }, text: async () => "", json: async () => ({}) };
      }
      if (u.includes("/api/tts")) {           /* 整段合成：模拟 TTS_TTFB 后返回 */
        await sleep(TTS_TTFB);
        return { ok: true, status: 200, blob: async () => ({ size: 2048, type: "audio/mpeg" }), headers: { get: () => null }, text: async () => "" };
      }
      if (u.includes("/api/rebut")) { win.__rebutCalls = (win.__rebutCalls || 0) + 1; return sseRebutt(); }
      if (u.includes("/api/attribute")) return mkRes({ items: [{ idx: 0, who: "opp", sent: ["opp"] }] });
      if (u.includes("/api/buzzer")) { win.__buzzPosts.push(JSON.parse(opt.body || "{}")); return mkRes({ ok: true }); }
      if (u.includes("/api/should-speak")) return mkRes({ speak: false });
      return mkRes({ ok: true });
    };
    win.alert = () => {}; win.confirm = () => true;
  },
});

const win = dom.window;
const R = []; const ok = (n, c, e = "") => { R.push(!!c); console.log((c ? "✓" : "✗") + " " + n + (e ? "  " + e : "")); };

function reset(side = "正方") {
  win.eval(`
    cfg.mode="auto"; cfg.streamMode=true; cfg.diarize=false; cfg.attr=true; cfg.floorMode=false; cfg.omni=false; cfg.side="${side}";
    cfg.streamMinChars=20; cfg.streamGapMs=7000; cfg.streamTts=true; cfg.speakCooldown=0;
    transcript=[]; queue.length=0; lastSentences=[]; preHold=false; genBusy=false; playing=false;
    lastSpeakAt=0; lastStreamAt=0; streamAtOppChars=0; lastPlayEndAt=0; stopped=false; speakGen=0;
    ttsCache.clear(); window.__playAt=0; window.__playCount=0; window.__genCount=0; window.__rebutCalls=0; window.__spkCalls=0;
  `);
}
async function presetDraft() {        // 造出「抢跑稿已入队、TTS 已预合成」
  await win.eval(`ingest("对方辩友说人工智能可以提高效率，解放人的重复劳动。", [])`);
  for (let i = 0; i < 40 && win.eval("queue.length") < 3; i++) await sleep(25);
  await sleep(TTS_TTFB + 150);        // 等预取 TTS 完成
}
async function finishPlayback() {       // jsdom 不触发 onended → 手动收尾，让 drain 跑完 finally
  for (let i = 0; i < 15; i++) {
    if (!win.eval("playing")) break;
    win.eval("if(typeof curAudio!=='undefined' && curAudio && curAudio.onended) curAudio.onended();");
    await sleep(40);
  }
}
function fireBuzz(side, action = "down", source = "button") {
  const es = win.__es;
  if (!es || !es.onmessage) return false;
  es.onmessage({ data: JSON.stringify({ side, action, source }) });
  return true;
}

(async () => {
  console.log(`TTS 模拟耗时 ${TTS_TTFB}ms\n`);

  /* ---------- 场景 1：抢跑稿已备好，对方侧按钮按下 ---------- */
  console.log("======== 1 对方按按钮 → AI 立刻开口（抢跑稿+音频已就绪） ========");
  reset();
  await presetDraft();
  ok("订阅通道已建立", !!win.__es, win.__es ? win.__es.url : "");
  const q = win.eval("queue.length"), cached = win.eval("ttsCache.size");
  ok("抢跑稿已入队", q >= 3, `队列 ${q} 句`);
  ok("抢跑期已预合成音频", cached >= 3, `缓存 ${cached} 条`);

  const T0 = Date.now();
  const fired = fireBuzz("反方");
  console.log("   [调试] fired=", fired, "preHold=", win.eval("preHold"), "queue=", win.eval("queue.length"),
              "playing=", win.eval("playing"), "genBusy=", win.eval("genBusy"), "stopped=", win.eval("stopped"));
  await sleep(60);
  console.log("   [调试] 60ms后 playing=", win.eval("playing"), "playCount=", win.eval("window.__playCount"),
              "state=", win.eval('$("speakState").textContent'));                       // 我方=正方，反方侧按钮 = 对方说完了
  let T1 = -1;
  for (let i = 0; i < 300; i++) { await sleep(5); if (win.eval("window.__playCount") > 0) { T1 = win.eval("window.__playAt"); break; } }
  ok("按钮 → 开始出声", T1 > 0, T1 > 0 ? `${T1 - T0} ms` : "未出声");
  ok("没有重新出稿（直接用抢跑稿）", win.eval("window.__genCount || 0") === 0, `出稿 ${win.eval("window.__genCount || 0")} 次`);

  await finishPlayback();                 // 让第 1 次播报正常收尾（触发 ai_done 回发）
  await sleep(150);

  /* ---------- 场景 2：我方侧按钮 = 我方说完了，不该开口 ---------- */
  console.log("\n======== 2 我方按按钮（我方发言结束）→ 不应开口 ========");
  reset(); await presetDraft();
  fireBuzz("正方");
  await sleep(300);
  ok("我方侧按钮不触发开口", win.eval("window.__playCount") === 0);

  /* ---------- 场景 3：AI 发言结束 → 回发 ai_done ---------- */
  console.log("\n======== 3 AI 说完 → 向总线回发 ai_done ========");
  ok("已回发 ai_done", win.eval("window.__buzzPosts").some(p => p.action === "ai_done" && p.source === "ai"),
     JSON.stringify(win.eval("window.__buzzPosts")));

  /* ---------- 场景 4：没有抢跑稿时按按钮（最坏情况） ---------- */
  console.log("\n======== 4 没有抢跑稿时按按钮（最坏情况：要等出稿） ========");
  await finishPlayback();
  reset();
  win.eval("transcript=[]; queue.length=0; lastSentences=[];");
  await win.eval(`ingest("对方最后一句。", [])`);
  await sleep(100);
  win.eval("preHold=false; queue.length=0; lastSentences=[];");
  const B0 = Date.now();
  const bf = fireBuzz("反方");
  await sleep(80);
  console.log("   [调试4] fired=", bf, "playing=", win.eval("playing"), "genBusy=", win.eval("genBusy"),
              "queue=", win.eval("queue.length"), "playCount=", win.eval("window.__playCount"),
              "state=", win.eval('$("speakState").textContent'));
  let B1 = -1;
  for (let i = 0; i < 400; i++) {
    await sleep(10);
    if (win.eval("window.__playCount") > 0) { B1 = Date.now(); break; }
    if (win.eval("window.__spkCalls") > 0) { B1 = Date.now(); break; }   // 无稿时走流式播放
  }
  const rebutCalls = win.eval("window.__rebutCalls || 0");
  ok("无稿时按钮会立刻启动出稿（兜底链路）", rebutCalls >= 1 || B1 > 0,
     rebutCalls >= 1 ? `已发起 /api/rebut ×${rebutCalls}（此路径需等完整出稿 7~18s）` : "未发起");

  const pass = R.filter(Boolean).length;
  console.log(`\n结果：${pass} 通过 / ${R.length - pass} 失败`);
  process.exit(pass === R.length ? 0 : 1);
})();
