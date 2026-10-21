/* 验证「混合模式统一 TTS」：端到端(Omni)只提供字幕文本，音频统一由同一个 TTS 播报
   ① omniTts=true（默认）：Omni 的音频被丢弃，文本按句入 TTS 队列
   ② omniTts=false：回到模型自带嗓音（音频直接进扬声器缓冲）
   跑法：cd tools && npm i jsdom && node test-omni-tts.cjs
*/
const fs = require("fs");
const { JSDOM } = require("jsdom");

const HTML = fs.readFileSync("/sandbox/workspace/debate-agent/web/local/debate-local.html", "utf8");
let pass = 0, fail = 0;
const ok = (n, c, e) => { if (c) { pass++; console.log("  ✓ " + n); } else { fail++; console.log("  ✗ " + n + (e !== undefined ? "  " + JSON.stringify(e) : "")); } };
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* 构造一段 Omni SSE：两句完整话 + 一句半句 + 一段音频 */
function sseChunks() {
  const ev = [
    { type: "text", delta: "我方认为，" },
    { type: "text", delta: "对方把工具和能力混为一谈了。" },
    { type: "audio", delta: Buffer.alloc(4096).toString("base64") },
    { type: "text", delta: "请问对方，计算器问世当年人的计算能力消失了吗？" },
    { type: "audio", delta: Buffer.alloc(4096).toString("base64") },
    { type: "text", delta: "所以这不是消解" },
    { type: "done", text: "我方认为，对方把工具和能力混为一谈了。请问对方，计算器问世当年人的计算能力消失了吗？所以这不是消解", chars: 52, voice: "Ethan" },
  ];
  return ev.map(e => "data: " + JSON.stringify(e) + "\n\n");
}

(async () => {
  for (const omniTts of [true, false]) {
    const dom = new JSDOM(HTML, {
      runScripts: "dangerously", pretendToBeVisual: true,
      url: "https://120.48.43.211/debate/local/debate-local.html",
      beforeParse(win) {
        const chunks = sseChunks();
        win.__ttsCalls = [];
        win.fetch = async (url) => {
          const u = String(url);
          if (u.includes("/api/rebut-omni")) {
            let i = 0;
            const enc = new win.TextEncoder();
            return { ok: true, body: { getReader: () => ({ read: async () => i < chunks.length ? { done: false, value: enc.encode(chunks[i++]) } : { done: true } }) } };
          }
          if (u.includes("/api/tts")) { win.__ttsCalls.push(u); throw new Error("测试桩：不真合成"); }
          return { ok: true, json: async () => ({ models: [], voices: [], emotions: [] }), text: async () => "", body: null };
        };
        win.EventSource = class { constructor() { this.onmessage = null; } addEventListener() { } close() { } };
        win.matchMedia = () => ({ matches: false, addListener() { }, removeListener() { } });
        Object.defineProperty(win.navigator, "mediaDevices", { value: { getUserMedia: async () => { throw new Error("no"); } }, configurable: true });
        win.AudioContext = class {
          constructor() { this.currentTime = 0; this.state = "running"; this.destination = {}; this.sampleRate = 24000; }
          resume() { }
          createBuffer(ch, len, sr) { return { duration: len / sr, getChannelData: () => new Float32Array(len), copyToChannel() { } }; }
          createBufferSource() { return { connect() { }, start() { }, stop() { }, buffer: null, onended: null, playbackRate: { value: 1 } }; }
          createGain() { return { connect() { }, gain: { value: 0 } }; }
          createMediaStreamSource() { return { connect() { }, disconnect() { } }; }
          createScriptProcessor() { return { connect() { }, disconnect() { }, onaudioprocess: null }; }
          createOscillator() { return { connect() { }, start() { }, stop() { }, frequency: {} }; }
        };
        win.MediaRecorder = class { start() { } stop() { } };
        win.URL.createObjectURL = () => "blob:x";
      },
    });
    const win = dom.window;
    await sleep(1200);
    win.eval(`cfg.omniTts = ${omniTts}; cfg.autoSpeak = true;`);
    win.eval(`transcript.length = 0; transcript.push({who:"opp", text:"AI 会取代大部分岗位，让人的技能失去价值。", ts:Date.now()});`);
    win.eval("genBusy = false; preHold = false; queue.length = 0; lastSentences = []; resetSpokenChars();");

    let err = null;
    try { await win.eval("genRebutOmni(true, {})"); } catch (e) { err = e; }
    await sleep(600);

    console.log(`\n【omniTts = ${omniTts}】`);
    ok("无异常", !err, err && String(err).slice(0, 120));
    const q = win.eval("lastSentences.map(x=>x.text)");   // queue 会被 drain 立刻消费，改看已产出的句子
    const sp = win.eval("spkPendLen");
    const ttsCalls = win.__ttsCalls.length;
    if (omniTts) {
      ok("文本按句进入 TTS 队列（≥3 句）", q.length >= 3, q);
      ok("末句半句也被补播", q.some(t => t.includes("这不是消解")), q);
      ok("TTS 被真正调用（走同一个 TTS）", ttsCalls >= 1, ttsCalls);
      ok("★ Omni 自带音频被丢弃（未进扬声器缓冲）", sp === 0, sp);
    } else {
      ok("音频直接进扬声器缓冲（模型自带嗓音）", sp > 0, sp);
      ok("★ 未调用 TTS（用模型自带嗓音）", ttsCalls === 0, ttsCalls);
    }
    dom.window.close();
  }
  console.log(`\n结果：${pass} 通过 / ${fail} 失败\n`);
  process.exit(fail ? 1 : 0);
})();
