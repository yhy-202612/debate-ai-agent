/* 播报流水线专项测试：整段合并 / 段间无缝续接 / 无丢段、无死循环
 * 用 jsdom 加载真实页面，stub 掉 fetch 与 Web Audio，跑真实 drain 逻辑。
 * 跑法：NODE_PATH=/tmp/node_modules node tools/test_pipeline.cjs
 */
const fs = require("fs"), path = require("path");
const { JSDOM } = require("jsdom");

const html = fs.readFileSync(path.join(__dirname, "../web/local/debate-local.html"), "utf8");
const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true, url: "http://localhost/" });
const w = dom.window;
let pass = 0, fail = 0;
function ok(c, m){ if (c) { pass++; console.log("  ✓ " + m); } else { fail++; console.log("  ✗ " + m); } }
function sleep(ms){ return new Promise(r => setTimeout(r, ms)); }

/* ---- WAV 构造（24kHz/16bit/单声道） ---- */
function wav(nSamples, sr = 24000){
  const dataLen = nSamples * 2, b = Buffer.alloc(44 + dataLen);
  b.write("RIFF", 0); b.writeUInt32LE(36 + dataLen, 4); b.write("WAVE", 8);
  b.write("fmt ", 12); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22);
  b.writeUInt32LE(sr, 24); b.writeUInt32LE(sr * 2, 28); b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34);
  b.write("data", 36); b.writeUInt32LE(dataLen, 40);
  for (let i = 0; i < nSamples; i++) b.writeInt16LE(Math.round(20000 * Math.sin(i * 0.05)), 44 + i * 2);
  return b;
}
function resp(buf, chunk = 4096){
  const u8 = new Uint8Array(buf); let off = 0;
  const rs = new ReadableStream({ pull(c){ if (off >= u8.length) { c.close(); return; } const e = Math.min(off + chunk, u8.length); c.enqueue(u8.subarray(off, e)); off = e; } });
  return { ok: true, status: 200, body: rs };
}

const t0 = Date.now();
class FakeCtx{
  constructor(){ this.state = "running"; this.destination = {}; }
  get currentTime(){ return (Date.now() - t0) / 1000; }
  resume(){ return Promise.resolve(); }
  createBuffer(ch, len, sr){ const d = new Float32Array(len); return { duration: len / sr, copyToChannel(a){ d.set(a); }, getChannelData(){ return d; } }; }
  createBufferSource(){
    const s = { buffer: null, onended: null, connect(){}, stop(){}, start(){ const d = s.buffer ? s.buffer.duration * 1000 : 0; setTimeout(() => { try { s.onended && s.onended(); } catch (e) {} }, d + 30); } };
    return s;
  }
}
w.AudioContext = FakeCtx; w.webkitAudioContext = FakeCtx;

let calls = 0, errs = [];
w.addEventListener("error", e => errs.push(String(e.message || e)));
w.fetch = (url) => {
  if (String(url).includes("/api/tts-stream")) { calls++; return Promise.resolve(resp(wav(12000))); }  // 每段 0.5s
  return Promise.resolve({ ok: false, status: 404, body: null });
};

async function runCase(name, texts, expectMaxCalls){
  calls = 0;
  const st = w.document.getElementById("speakState");
  st.textContent = "（测试中）";
  for (const t of texts) w.enqueue(t, null);
  const t = Date.now();
  while ((calls === 0 || st.textContent !== "播报完成") && Date.now() - t < 15000) await sleep(60);
  console.log(`\n== ${name} ==`);
  ok(st.textContent === "播报完成", `正常播完（状态：${st.textContent}）`);
  ok(calls >= 1 && calls <= expectMaxCalls, `合成段数 ${calls} ≤ ${expectMaxCalls}（整段合并生效，${texts.length} 句）`);
  ok(errs.length === 0, `无脚本错误（${errs.slice(0,2).join(" | ")}）`);
}

(async () => {
  // 6 句 × 8 字 = 48 字 < 120 → 应合并为 1 段
  await runCase("短句合并为一段", Array.from({ length: 6 }, (_, i) => `这是第${i}句测试文本。`), 1);
  // 12 句 × 16 字 ≈ 192 字 → 应切成 2 段（≤120 / 余量）
  await runCase("长稿切为两段并无缝续接", Array.from({ length: 12 }, (_, i) => `这是第${i}句用于测试较长稿件的测试文本内容。`), 3);
  console.log(`\n${pass}/${pass + fail} 通过`);
  process.exit(fail ? 1 : 0);
})();
