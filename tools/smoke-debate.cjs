/* 前端冒烟（我方=正方）：
   ①片内拆分 ②主席点名触发发言 ③未映射不再全 hall ④判定失败跟随上一片 ⑤分块显示
   跑法：cd /sandbox/workspace/debate-agent/tools && npm i jsdom && node smoke-debate.cjs   */
const fs = require("fs");
const path = require("path");
let JSDOM;
try { ({ JSDOM } = require("jsdom")); }
catch (e) { ({ JSDOM } = require(path.join(process.cwd(), "node_modules", "jsdom"))); }
const HTML_PATH = process.argv[2] || path.join(__dirname, "..", "web", "local", "debate-local.html");
const HTML = fs.readFileSync(HTML_PATH, "utf8");

function mkRes(body, ok = true) {
  return { ok, status: ok ? 200 : 500, json: async () => body, text: async () => JSON.stringify(body),
           headers: { get: () => null }, body: null };
}
const PARTS = [
  { who: "hall", text: "感谢正方一辩，下面有请反方一辩进行。立论陈词，时间同样是三分钟。" },
  { who: "ours", text: "谢谢主席。我方认为，人工智能的发展对人。" },
];
const dom = new JSDOM(HTML, {
  runScripts: "dangerously", pretendToBeVisual: true,
  url: "http://localhost/debate/local/debate-local.html",
  beforeParse(win) {
    class Gain { constructor() { this.gain = { value: 1, setValueAtTime() {}, linearRampToValueAtTime() {} }; } connect() { return this; } disconnect() {} }
    win.AudioContext = class {
      constructor() { this.state = "running"; this.currentTime = 0; this.sampleRate = 48000; this.destination = {}; }
      createGain() { return new Gain(); }
      createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
      createMediaStreamDestination() { return { stream: { getTracks: () => [] } }; }
      createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; }
      createBufferSource() { return { buffer: null, connect() {}, start() {}, stop() {}, onended: null }; }
      createBuffer(c, l, r) { return { duration: l / (r || 48000), getChannelData: () => new Float32Array(l) }; }
      decodeAudioData() { return Promise.resolve({ duration: 10, getChannelData: () => new Float32Array(10) }); }
      resume() { return Promise.resolve(); } close() { return Promise.resolve(); }
    };
    win.MediaStream = class { getTracks() { return []; } };
    Object.defineProperty(win.navigator, "mediaDevices", { value: { getUserMedia: async () => new win.MediaStream() }, configurable: true });
    win.URL.createObjectURL = () => "blob:stub"; win.URL.revokeObjectURL = () => {};
    win.fetch = async (url, opt = {}) => {
      const u = String(url); win.__lastUrl = u; win.__urls = (win.__urls||[]).concat([u]);
      // 端到端出稿通道（必须排在 /api/rebut 之前，避免被 includes 误命中）
      if (u.includes("/api/rebut-omni")) {
        const audioB64 = Buffer.from(new Uint8Array(4800)).toString("base64");
        const chunks = [
          'data: {"type":"text","delta":"对方把替代岗位当成了消解价值。"}\n\n',
          `data: {"type":"audio","delta":"${audioB64}"}\n\n`,
          'data: {"type":"noise"}\n\n',
          'data: {"type":"done","text":"对方把替代岗位当成了消解价值。","first_text_ms":260,"first_audio_ms":700,"ms":2000,"chars":18,"voice":"Tina"}\n\n',
        ];
        const enc = new TextEncoder();
        let i = 0;
        return {
          ok: true, status: 200, json: async () => ({}),
          body: { getReader: () => ({ read: async () => i < chunks.length
            ? { done: false, value: enc.encode(chunks[i++]) } : { done: true } }) },
        };
      }
      if (u.includes("/api/omni-status")) return mkRes({ ok: true, voices: [{ id: "Tina", label: "Tina（女）" }], models: [{ id: "qwen3.5-omni-plus-realtime", label: "Plus" }], default_voice: "Tina", default_model: "qwen3.5-omni-plus-realtime", sample_rate: 24000 });
      if (u.includes("/api/models")) return mkRes({ models: [{ id: "", name: "默认", ready: true }] });
      if (u.includes("/api/attribute")) {
        if (win.__failAttribute) return mkRes({ error: "boom" }, false);
        const body = JSON.parse(opt.body || "{}");
        return mkRes({ items: (body.texts || []).map((t, i) => ({ idx: i + 1, parts: PARTS, who: "ours", text: t })) });
      }
      if (u.includes("/api/should-speak")) return mkRes({ speak: false, reason: "stub" });
      if (u.includes("/api/summarize")) return mkRes({ summary: {} });
      if (u.includes("/healthz")) return mkRes({ engine: { strong: { key_set: true } } });
      return mkRes({ ok: true });
    };
    win.__origFetch = win.fetch;
    win.alert = () => {}; win.confirm = () => true;
  },
});
const win = dom.window;
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
let pass = 0, fail = 0;
function ok(n, c, extra = "") { if (c) { pass++; console.log("  ✓ " + n); } else { fail++; console.log("  ✗ " + n + "  " + extra); } }

(async () => {
  await sleep(400);
  win.eval("cfg.mode='auto'; cfg.diarize=false; cfg.side='正方'; cfg.topic='人工智能'; cfg.autoSave=false; genRebut=async()=>{ genBusy=true; window.__genCalled=(window.__genCalled||0)+1; };");

  console.log("\n【A. 片内拆分 → 发言人区分明确】");
  win.eval("transcript=[];");
  await win.eval(`ingest("感谢正方一辩，下面有请反方一辩进行。立论陈词，时间同样是三分钟。谢谢主席。我方认为，人工智能的发展对人。", [])`);
  await sleep(600);
  const s1 = win.eval("transcript.map(s=>({who:s.who,text:s.text}))");
  ok("混合片拆成 2 条", s1.length === 2, JSON.stringify(s1.map(x => x.who)));
  ok("第 1 条=会场", s1[0] && s1[0].who === "hall");
  ok("第 2 条=我方", s1[1] && s1[1].who === "ours");
  ok("主席词未挂到辩手名下", s1[1] && !/有请反方一辩/.test(s1[1].text));

  console.log("\n【B. 主席点名我方（正方二辩）→ 立即发言】");
  win.eval("transcript=[]; window.__genCalled=0; lastSpeakAt=0; lastIngestAt=Date.now();");
  win.eval(`pushSeg({who:"hall", raw:"感谢双方一辩。下面进入驳论环节。有请正方二辩，针对反方立论展开反驳。", text:"感谢双方一辩。下面进入驳论环节。有请正方二辩，针对反方立论展开反驳。", t:Date.now(), time:"20:39:30"});`);
  ok("命中「有请正方二辩」", win.eval(`chairCallsUs("感谢双方一辩。下面进入驳论环节。有请正方二辩，针对反方立论展开反驳。")`) === true);
  ok("不误判「感谢正方二辩。下面有请反方二辩」", win.eval(`chairCallsUs("感谢正方二辩。下面有请反方二辩，针对正方立论展开反驳。")`) === false);
  ok("不误判「有请正方一辩」（我方队友）", win.eval(`chairCallsUs("首先进入开篇立论环节。有请正方一辩进行立论陈词。")`) === false);
  ok("不误判「有请反方三辩」", win.eval(`chairCallsUs("下面有请反方三辩，向对方进行提问。")`) === false);
  win.eval("runSpeakCheck()"); await sleep(300);
  ok("立刻触发出稿", win.eval("window.__genCalled||0") === 1);
  ok("出稿中即置 genBusy（模拟源 hold）", win.eval("genBusy") === true);

  console.log("\n【C. 主席引导对方 → 不发言】");
  win.eval("transcript=[]; window.__genCalled=0; lastSpeakAt=0;");
  win.eval(`pushSeg({who:"hall", raw:"感谢正方二辩。下面有请反方二辩，针对正方立论展开反驳。", text:"感谢正方二辩。下面有请反方二辩，针对正方立论展开反驳。", t:Date.now(), time:"20:40:00"});`);
  win.eval("runSpeakCheck()"); await sleep(300);
  ok("主席引导对方时不发言", win.eval("window.__genCalled||0") === 0);

  console.log("\n【D. 说话人分离未映射 → 不再全部会场】");
  win.eval("transcript=[]; cfg.diarize=true; speakerMap={};");
  await win.eval(`ingest("感谢正方一辩，下面有请反方一辩进行。立论陈词，时间同样是三分钟。谢谢主席。我方认为，人工智能的发展对人。", [{speaker:"S1"}])`);
  await sleep(700);
  const w1 = win.eval("transcript.map(s=>s.who)");
  ok("未映射时仍做归属判定（不再全 hall）", w1.indexOf("ours") >= 0 || w1.indexOf("opp") >= 0, JSON.stringify(w1));
  ok("同时挂起待标注供人工确认", win.eval("pendingSpeakers.size") === 1);

  console.log("\n【E. 归属判定失败 → 跟随上一片】");
  win.eval("transcript=[]; cfg.diarize=false; window.__failAttribute=true;");
  win.eval(`pushSeg({who:"opp", raw:"我方认为消极大于积极。", text:"我方认为消极大于积极。", t:Date.now()-5000, time:"1"});`);
  await win.eval(`ingest("这是新的一片对方发言，还没有结束。", [])`);
  await sleep(400);
  const w2 = win.eval("transcript.map(s=>s.who)");
  ok("判定失败时跟随上一片归属", w2[w2.length - 1] === "opp", JSON.stringify(w2));
  win.eval("window.__failAttribute=false");

  console.log("\n【F. 分块显示】");
  win.eval(`transcript=[
    {who:"opp", text:"我方认为消极大于积极。", t:1000, time:"1"},
    {who:"hall", text:"感谢正方一辩。下面有请反方一辩。", t:2000, time:"2"},
    {who:"ours", text:"我方认为积极大于消极。", t:3000, time:"3"}];`);
  const g = win.eval("groupSegments(transcript).map(x=>x.who)");
  ok("主席串场独立成块", g.join(",") === "opp,hall,ours", JSON.stringify(g));

  console.log("\n【G. 我方播报中收到主席点名 → 播报结束后必须补发言】");
  win.eval("transcript=[]; window.__genCalled=0; lastSpeakAt=0; lastIngestAt=Date.now(); cfg.diarize=false; cfg.side='正方'; genBusy=false; playing=false;");
  win.eval("playing=true;");                 // 模拟：我方正在播报，此时主席点名到达
  win.eval(`pushSeg({who:"hall", raw:"感谢双方一辩。下面进入驳论环节。有请正方二辩，针对反方立论展开反驳。", text:"感谢双方一辩。下面进入驳论环节。有请正方二辩，针对反方立论展开反驳。", t:Date.now(), time:"20:39:30"});`);
  await sleep(1400);                          // 超过 900ms 排期
  ok("播报中不抢话（暂不发言）", win.eval("window.__genCalled||0") === 0);
  ok("播报中已排入复查（未被丢弃）", win.eval("!!speakTimer"));
  win.eval("playing=false; scheduleSpeakCheck();");   // 播报结束
  await sleep(1400);
  ok("★播报结束后补上发言（此前会被永久吞掉）", win.eval("window.__genCalled||0") === 1);

  console.log("\n【H. 流式抢跑：对方说话时先拟稿，说完立即接话】");
  win.eval("transcript=[]; window.__genCalled=0; window.__drainCalled=0; lastSpeakAt=0; lastIngestAt=Date.now();"
    + " cfg.diarize=false; cfg.side='正方'; genBusy=false; playing=false; preHold=false; lastStreamAt=0; streamAtOppChars=0;"
    + " cfg.streamMode=true; cfg.streamMinChars=20; cfg.streamGapMs=10000;"
    + " drain=async()=>{ window.__drainCalled=(window.__drainCalled||0)+1; };");
  win.eval(`pushSeg({who:"opp", raw:"我方认为人工智能正在侵蚀人的核心能力，这非常危险", text:"我方认为人工智能正在侵蚀人的核心能力，这非常危险", t:Date.now(), time:"20:40:01"});`);
  win.eval("maybeStreamPregenerate();");
  ok("对方说够字数即触发抢跑出稿", win.eval("window.__genCalled||0") === 1);
  ok("抢跑稿只攒不播（preHold 置位）", win.eval("preHold") === true);
  win.eval("maybeStreamPregenerate();");
  ok("抢跑中不重复触发", win.eval("window.__genCalled||0") === 1);

  win.eval("genBusy=false; lastIngestAt=Date.now()-5000;");   // 模拟出稿结束 + 已静默 5s
  win.eval("runSpeakCheck()"); await sleep(300);
  ok("★对方说完立即放稿（不等再次出稿）", win.eval("window.__drainCalled||0") >= 1);
  ok("放稿后 preHold 解除", win.eval("preHold") === false);

  // 安全阀：主席正在请对方 → 抢跑稿必须作废
  win.eval("transcript=[]; window.__drainCalled=0; window.__genCalled=0; preHold=true; streamAtOppChars=0;"
    + " lastSpeakAt=0; genBusy=false; playing=false;"
    + " transcript.push({who:'hall',text:'感谢正方二辩。下面有请反方二辩，针对正方立论展开反驳。',raw:'',t:Date.now()}); lastIngestAt=Date.now()-5000;");
  win.eval("runSpeakCheck()"); await sleep(300);
  ok("主席请对方时抢跑稿作废（不接话）", win.eval("window.__drainCalled||0") === 0 && win.eval("preHold") === false);

  // 抢跑稿过时（对方大幅续说，超出 streamAtOppChars+40）→ 作废重出
  win.eval("transcript=[]; window.__drainCalled=0; window.__genCalled=0; preHold=true; lastSpeakAt=0; genBusy=false; playing=false;"
    + " cfg.streamMode=true; cfg.streamMinChars=20; lastStreamAt=Date.now();"
    + " transcript.push({who:'opp',text:'我方认为人工智能正在侵蚀人的核心能力这非常危险而且会带来更多社会问题并让人逐渐丧失独立判断与创造的能力最终被工具所支配',raw:'',t:Date.now()}); lastIngestAt=Date.now()-5000;"
    + " streamAtOppChars=10;");
  win.eval("runSpeakCheck()"); await sleep(400);
  ok("对方大幅续说后旧稿作废（不放出）", win.eval("window.__drainCalled||0") === 0 && win.eval("preHold") === false);

  console.log("\n【I. 语义判停 EOU：话说完整即接、未说完不抢（借鉴豆包实时对话）】");
  // 覆盖 fetch：可控 should-speak 响应 + 调用计数
  win.eval(`window.__speakCalled=0; window.__speakResp={speak:true,reason:"stub"};
    fetch=async(url,opt={})=>{ const u=String(url);
      if(u.includes("/api/should-speak")){ window.__speakCalled++; return {ok:true,json:async()=>window.__speakResp}; }
      return {ok:true,json:async()=>({}),text:async()=>""}; };`);

  // I-1 末句已收束（句号结尾）+ 静默仅 1.0s → 应已可接话（旧逻辑无脑等 2.2s）
  win.eval("transcript=[]; window.__genCalled=0; window.__speakCalled=0; preHold=false; genBusy=false; playing=false;"
    + " cfg.streamMode=false; cfg.mode='auto'; cfg.side='正方'; cfg.speakCooldown=0; lastSpeakAt=0;"
    + " genRebut=async()=>{ genBusy=true; window.__genCalled=(window.__genCalled||0)+1; };"
    + " transcript.push({who:'opp',text:'我方认为人工智能的发展正在消解人的价值。',raw:'',t:Date.now()});"
    + " lastIngestAt=Date.now()-1000;");
  win.eval("runSpeakCheck()"); await sleep(300);
  ok("★末句收束→静默 1.0s 即可接话（旧逻辑要等 2.2s）", win.eval("window.__genCalled||0") === 1);
  ok("确实发起了时机判定请求", win.eval("window.__speakCalled||0") === 1);

  // I-2 末句未收束（逗号结尾）+ 静默 1.0s → 仍等待、不抢话
  win.eval("transcript=[]; window.__genCalled=0; window.__speakCalled=0; genBusy=false; playing=false;"
    + " transcript.push({who:'opp',text:'我方认为人工智能的发展正在消解人的价值，',raw:'',t:Date.now()});"
    + " lastIngestAt=Date.now()-1000;");
  win.eval("runSpeakCheck()"); await sleep(300);
  ok("末句未收束（逗号结尾）→ 继续倾听、不抢话",
     win.eval("window.__genCalled||0") === 0 && win.eval("window.__speakCalled||0") === 0);

  // I-3 末句未收束但静默已超强阈值 → 允许判定
  win.eval("transcript=[]; window.__genCalled=0; window.__speakCalled=0; genBusy=false; playing=false;"
    + " transcript.push({who:'opp',text:'我方认为人工智能的发展正在消解人的价值，',raw:'',t:Date.now()});"
    + " lastIngestAt=Date.now()-5000;");
  win.eval("runSpeakCheck()"); await sleep(300);
  ok("末句未收束但静默已超强阈值 → 允许判定", win.eval("window.__speakCalled||0") === 1);

  console.log("\n【J. 语义启动抢跑：讲完一句完整话即可起稿（不必等满 20 字）】");
  win.eval("transcript=[]; window.__genCalled=0; preHold=false; genBusy=false; playing=false;"
    + " cfg.streamMode=true; cfg.streamMinChars=20; cfg.streamGapMs=10000; lastStreamAt=0; streamAtOppChars=0;"
    + " transcript.push({who:'opp',text:'人工智能正在消解人的价值。',raw:'',t:Date.now()});");   // 13 字·收束
  win.eval("maybeStreamPregenerate();");
  ok("★末句收束→13 字即启动抢跑（原门槛 20 字）", win.eval("window.__genCalled||0") === 1);

  win.eval("transcript=[]; window.__genCalled=0; preHold=false; genBusy=false; playing=false; lastStreamAt=0; streamAtOppChars=0;"
    + " transcript.push({who:'opp',text:'人工智能正在消解人的价值而且它',raw:'',t:Date.now()});"); // 16 字·未收束
  win.eval("maybeStreamPregenerate();");
  ok("末句未收束→不足 20 字不抢跑（防半句起稿）", win.eval("window.__genCalled||0") === 0);

  console.log("\n【K. 端到端出稿通道（Omni，可选）：字幕 + PCM 音频一次到位】");
  win.eval("window.fetch = window.__origFetch; fetch = window.__origFetch;");
  win.eval("cfg.omni=true; transcript=[{who:'opp',text:'人工智能会替代人的工作，所以人的价值被消解。'}];"
    + " genBusy=false; playing=false; $('speech').innerHTML=''; $('flags').innerHTML=''; lastSentences=[];");
  win.__urls = []; win.eval("window.__err=null; genRebutOmni(true).catch(e=>{window.__err=e.stack||String(e)})");
  await sleep(900);
  ok("字幕已渲染到发言区", /对方把替代岗位当成了消解价值/.test(win.eval("$('speech').textContent")),
     win.eval("$('speech').textContent").slice(0, 60));
  ok("done 事件回填了耗时统计", /端到端通道/.test(win.eval("$('flags').textContent")),
     win.eval("$('flags').textContent").slice(0, 80));
  ok("done 后切分出句子（供「播报全文」复用）", win.eval("lastSentences.length") >= 1,
     String(win.eval("lastSentences.length")));
  ok("genBusy 已复位（不会卡死后续出稿）", win.eval("genBusy") === false);
  win.eval("cfg.omni=false;");

  console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail ? 1 : 0);
})();
