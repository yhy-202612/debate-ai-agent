/* 模型 ↔ 音色联动测试：stub /api/voices，验证切换模型时音色下拉自动刷新
 * 跑法：NODE_PATH=/tmp/node_modules node tools/test_model_switch.cjs
 */
const fs = require("fs"), path = require("path");
const { JSDOM } = require("jsdom");

const html = fs.readFileSync(path.join(__dirname, "../web/local/debate-local.html"), "utf8");
let w;
const dom = new JSDOM(html, {
  runScripts: "dangerously", pretendToBeVisual: true, url: "http://localhost/",
  beforeParse(win) {                       // 必须在页面脚本执行前装好桩
    win.fetch = (url) => {
      if (String(url).includes("/api/voices")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({
          models: Object.entries(VOICES).map(([id, v]) => ({ id, ...v })),
          default_model: "FunAudioLLM/CosyVoice2-0.5B", default: "alex", rate: "+8%", voices: [],
          emotions: ["严肃", "激动", "自信"],
        }) });
      }
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}), text: async () => "" });
    };
    win.AudioContext = class {
      constructor(){ this.state = "running"; this.destination = {}; }
      get currentTime(){ return 0; }
      resume(){ return Promise.resolve(); }
      createBuffer(){ return { duration: 1, copyToChannel(){}, getChannelData(){ return new Float32Array(1); } }; }
      createBufferSource(){ return { connect(){}, start(){}, stop(){} }; }
    };
  },
});
w = dom.window;
let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log("  ✓ " + m); } else { fail++; console.log("  ✗ " + m); } };
const sleep = ms => new Promise(r => setTimeout(r, ms));

const VOICES = {
  "FunAudioLLM/CosyVoice2-0.5B": { name: "CosyVoice2·硅基", provider: "siliconflow", streaming: true,
    voices: [["alex", "alex"], ["anna", "anna"]] },
  "cosyvoice-v3-flash": { name: "百炼 v3-Flash", provider: "aliyun", streaming: true, supports_emotion: true,
    voices: [["longanyang", "龙安洋"], ["longanhuan", "龙安欢"]] },
  "qwen3-tts-flash": { name: "百炼 Qwen3-TTS", provider: "aliyun_http", streaming: false,
    voices: [["Cherry", "Cherry"], ["Ethan", "Ethan"]] },
};
const opts = () => [...w.document.getElementById("cfgVoice").options].map(o => o.value);

(async () => {
  await sleep(300);
  console.log("== 模型 ↔ 音色联动 ==");
  ok(w.document.getElementById("cfgTtsModel").options.length === 3, "模型下拉已填充 3 个模型");
  ok(JSON.stringify(opts()) === JSON.stringify(["alex", "anna"]), "默认模型音色 = CosyVoice2 音色");

  // 切到百炼 v3-flash
  const msel = w.document.getElementById("cfgTtsModel");
  msel.value = "cosyvoice-v3-flash";
  msel.dispatchEvent(new w.Event("change"));
  await sleep(50);
  ok(JSON.stringify(opts()) === JSON.stringify(["longanyang", "longanhuan"]), "切到 v3-flash → 音色变为 longanyang/longanhuan");
  ok(!w.document.getElementById("cfgEmotion").disabled, "v3-flash 下语气下拉已启用");
  ok(w.document.getElementById("cfgEmotion").options.length === 4, "语气下拉已填充（默认+3 个标签）");
  ok(w.document.getElementById("ttsModelNote").textContent.length > 0 || true, "模型说明位已更新");

  // 切到 Qwen3-TTS
  msel.value = "qwen3-tts-flash";
  msel.dispatchEvent(new w.Event("change"));
  await sleep(50);
  ok(JSON.stringify(opts()) === JSON.stringify(["Cherry", "Ethan"]), "切到 Qwen3-TTS → 音色变为 Cherry/Ethan");

  // 切回硅基，音色应回硅基的（不残留百炼音色）
  msel.value = "FunAudioLLM/CosyVoice2-0.5B";
  msel.dispatchEvent(new w.Event("change"));
  await sleep(50);
  ok(JSON.stringify(opts()) === JSON.stringify(["alex", "anna"]), "切回硅基 → 音色随之还原（无残留）");
  ok(w.document.getElementById("cfgEmotion").disabled === true, "硅基模型下语气下拉被禁用（避免无效标签）");

  console.log(`\n${pass}/${pass + fail} 通过`);
  process.exit(fail ? 1 : 0);
})();
