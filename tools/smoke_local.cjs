/* 本地版页面 jsdom 冒烟：校验控件/默认值/纯函数行为 + 本轮新增（模式、弹窗关闭、全自动流程钩子）。
   运行： NODE_PATH=/tmp/node_modules node tools/smoke_local.cjs */
const fs = require("fs");
const { JSDOM } = require("jsdom");

const file = "/sandbox/workspace/debate-agent/web/local/debate-local.html";
const html = fs.readFileSync(file, "utf8");
const dom = new JSDOM(html, { runScripts: "dangerously", url: "http://localhost:8000/debate-local.html", pretendToBeVisual: true });
const w = dom.window;

let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; } else { fail++; console.log("FAIL:", m); } };

setTimeout(() => {
  const $ = id => w.document.getElementById(id);
  // —— 既有断言 ——
  ok($("cfgListen").value === "interval", "默认分片模式=常态化");
  ok($("cfgChunkMs").value === "3000", "分片间隔默认 3000（更密集）");
  ok($("cfgOverlapMs").value === "800", "重叠默认 800");
  ok(!!$("cfgBlockGap") && $("cfgBlockGap").value === "8000", "发言分块间隔默认 8000（更少分段）");
  ok(!!$("cfgModel") && $("cfgModel").tagName === "SELECT", "出稿模型下拉存在");
  ok(!!$("genModelShow"), "页头出稿模型显示位存在");
  ok(typeof w.reconcileSentences === "function", "reconcileSentences（最终稿校准）已定义");
  ok(typeof w.loadModels === "function", "loadModels 已定义");
  ok(typeof w.groupSegments === "function", "groupSegments（按含义分块）已定义");
  ok(typeof w.prefetchTTS === "function", "prefetchTTS（播报预取）已定义");
  { // 同一来源连续片合并为一块；换人另起块；间隔过大另起块
    const g = w.groupSegments([
      {who:"hall",text:"甲",t:1000},{who:"hall",text:"乙",t:2000},
      {who:"opp",text:"丙",t:3000},{who:"opp",text:"丁",t:13000}]);
    ok(g.length === 3 && g[0].items.length === 2, "连续同源合并、换人/停顿分块（块数=" + g.length + "）");
  }
  ok($("cfgAsrModel").options.length === 6, "ASR 模型下拉 6 项(fallback)=" + $("cfgAsrModel").options.length);
  ok(!!$("autoGen") && $("autoGen").checked === false, "自动生成复选框存在且默认关");
  ok($("autoSpeak").checked === true, "出句即播默认开");
  ok($("modeShow").textContent === "常态化", "页头模式显示 常态化");
  ok($("thrShow").textContent === "60", "自动阈值显示 60");
  ok(!!$("btnPlayAll"), "播报全文按钮存在");
  ok(!!$("stars"), "评分星控件存在");
  ok(typeof w.dedupAppend === "function", "dedupAppend 已定义");
  ok(w.dedupAppend("前面的话，对方同学说", "对方同学说人工智能会") === "人工智能会", "去重：去掉重叠前缀");
  ok(w.dedupAppend("甲", "乙") === "乙", "无重叠时原样返回");
  ok(typeof w.contStep === "function" && typeof w.vadStep === "function", "常态化/静音分片函数已定义");
  ok(typeof w.mergeChunks === "function" && typeof w.encodeWav === "function", "WAV/合帧工具已定义");
  ok($("cfgSide").tagName === "SELECT" && $("cfgSide").value === "正方", "立场下拉默认=正方");
  ok($("sideShow").textContent === "正方", "页头立场显示 正方");
  $("cfgSide").value = "__custom"; $("cfgSide").onchange();
  ok($("sideCustomWrap").style.display === "block", "选自定义后显示自定义输入");
  ok(!!$("cfgMic") && $("cfgMic").tagName === "SELECT", "麦克风设备下拉存在");
  ok(!!$("cfgAsrFix") && $("cfgAsrFix").checked === false, "自动修正复选框存在且默认关");
  ok(!!$("btnCheck"), "事实核查按钮存在");
  ok(w.dedupAppend("甲甲甲", "甲甲甲") === "", "完全重复尾部→去重为空");
  ok(w.dedupAppend("前面的话对方同学说", "对方同学说人工智能") === "人工智能", "拼接去重（3字起）");
  // 本轮修复：容忍 ASR 标点/断句差异的归一化去重
  ok(w.dedupAppend("……人工智能会产生深远影响。", "深远影响，我们要积极应对") === "我们要积极应对", "标点差异也能去重（归一化）");
  ok(w.dedupAppend("人工智能会带来失业。", "带来失业！我们要发展它") === "我们要发展它", "标点/语气差异去重");
  ok(w.dedupAppend("甲甲甲甲", "甲甲甲甲") === "", "整段重叠→丢弃");
  ok(typeof w.polishText === "function", "polishText（只修正新增片段）已定义");

  // 本轮修复：生成驳论按钮绑定
  ok(typeof $("btnGen").onclick === "function", "生成驳论按钮已绑定点击事件");

  // —— 本轮新增：模式区分 ——
  ok(!!$("modeSel") && $("modeSel").tagName === "SELECT", "页头模式下拉存在");
  ok($("modeSel").value === "test", "默认=测试模式");
  ok($("cfgMode").options.length === 2, "设置内运行模式 2 项");
  ok($("modeSel").value === $("cfgMode").value, "页头模式与设置内一致");
  ok($("runModeShow").textContent === "测试", "页头运行模式显示 测试");
  ok(($("runNote").textContent || "").includes("测试模式"), "runNote 提示测试模式");
  $("modeSel").value = "auto"; $("modeSel").onchange();
  ok($("runModeShow").textContent === "全自动", "切全自动后页头显示 全自动");
  ok(($("runNote").textContent || "").includes("全自动"), "runNote 提示全自动");
  ok($("modeSel").value === "auto", "切换后 select 值=auto");
  $("modeSel").value = "test"; $("modeSel").onchange();

  // —— 本轮新增：设置弹窗可关闭（核心修复）——
  ok(!!$("dlgSettings"), "设置弹窗存在");
  ok(!!$("btnSettingsClose") && $("btnSettingsClose").type === "button", "设置弹窗有 × 关闭按钮(type=button)");
  ok(!!$("btnSettingsCancel") && $("btnSettingsCancel").type === "button", "取消按钮改为 type=button（不再依赖表单提交）");
  ok($("btnSave").type === "button", "保存按钮改为 type=button（onclick 抛错也不会卡住弹窗）");
  ok(typeof w.closeSettings === "function", "closeSettings 已定义");
  ok(typeof w.saveSettings === "function", "saveSettings 已定义（与关闭解耦）");
  ok(!!$("btnHelpClose") && $("btnHelpClose").type === "button", "帮助弹窗有 × 关闭按钮");
  // 直接打开/关闭（不依赖 showModal：jsdom 未实现，用 open 属性验关闭路径）
  $("dlgSettings").setAttribute("open", "");
  try { $("btnSettingsClose").onclick(); ok(!$("dlgSettings").hasAttribute("open") && !$("dlgSettings").open, "点 × 后弹窗关闭"); }
  catch (e) { ok(false, "点 × 关闭异常：" + e.message); }
  $("dlgSettings").setAttribute("open", "");
  try { $("btnSettingsCancel").onclick(); ok(!$("dlgSettings").hasAttribute("open") && !$("dlgSettings").open, "点取消后弹窗关闭"); }
  catch (e) { ok(false, "取消关闭异常：" + e.message); }

  // —— 本轮新增：全自动流程钩子 ——
  ok(typeof w.ingest === "function", "ingest（识别→归属→保存）已定义");
  ok(typeof w.saveTranscript === "function", "saveTranscript 已定义");
  ok(typeof w.scheduleSpeakCheck === "function", "scheduleSpeakCheck（自主发言判定）已定义");
  ok(typeof w.ensureSession === "function", "ensureSession 已定义");
  ok(!!$("saveState"), "保存状态提示位存在");
  ok(!!$("cfgAutoSave") && $("cfgAutoSave").checked === true, "自动保存默认开");
  ok(!!$("cfgSpeakGap") && $("cfgSpeakGap").value === "2500", "自主发言停顿阈值默认 2500");
  ok(!!$("cfgSpeakCooldown") && $("cfgSpeakCooldown").value === "20000", "自主发言冷却默认 20000");
  ok(w.ensureSession() && $("saveState"), "ensureSession 可调用");

  // ---- 本轮新增：流式播报 / 说话人分离自动归属 / 录音降噪增强 ----
  ok(!!$("cfgStreamTts") && $("cfgStreamTts").checked === true, "流式播报开关默认开");
  ok(!!$("cfgDiarize") && $("cfgDiarize").checked === false, "说话人分离归属开关默认关");
  ok(!!$("cfgHpf") && $("cfgHpf").checked === true, "录音降噪增强默认开");
  ok(!!$("spkPrompt"), "说话人标注提示位存在");
  ok(typeof w.streamSpeak === "function" && typeof w.waitStreamEnd === "function", "流式播报函数已定义");
  ok(typeof w.stopStreamSpeak === "function" && typeof w.makeWavParser === "function", "流式叫停 / WAV 解析已定义");
  ok(typeof w.renderSpeakerPrompt === "function" && typeof w.notePendingSpeaker === "function", "说话人标注逻辑已定义");
  ok(typeof w.spkPushPcm === "function" && typeof w.spkFlush === "function", "Web Audio PCM 预缓冲排播已定义");
  ok(typeof w.startWatchdog === "function" && typeof w.restartMic === "function", "采集看门狗 / 自愈已定义");
  ok(!!$("vadShow"), "输入电平(说话/阈值)显示位存在");
  ok(w.SPEAK_MERGE_MAX === 120, "整句合成合并上限=120 字");
  ok(typeof w.vadStep === "function" && typeof w.contStep === "function", "VAD 分段函数已定义");
  // 播报连贯性改造：流水线预取 + 无缝续接 + 去静音
  ok(typeof w.fetchSegPcm === "function" && typeof w.pushSegPcm === "function", "整段预取 / 无缝排入已定义");
  ok(typeof w.waitLead === "function" && typeof w.waitDrained === "function", "衔接等待 / 排空等待已定义");
  ok(typeof w.streamSpeakInto === "function" && typeof w.streamSpeak === "function", "流式播报(续接版/重置版)已定义");
  ok(typeof w.trimSilence === "function" && typeof w.takeSeg === "function", "去静音 / 切段已定义");
  ok(!!$("cfgRateNum") && [...$("cfgRate").options].length >= 10, "语速：自定义倍率输入 + ≥10 档预设");
  ok(!!$("cfgTtsModel"), "播报模型下拉存在");
  ok(typeof w.applyModelVoices === "function", "模型↔音色联动函数已定义");
  ok(!!$("ttsModelNote") && !!$("cfgVoice"), "模型说明位 + 音色下拉存在");
  ok(!!$("cfgEmotion"), "语气（情感标签）下拉存在");
  ok(/清晰度/.test($("cfgRate").options[$("cfgRate").options.length-1].textContent), "语速档位已标注清晰度提示");
  ok(typeof w.spkResetPlayback === "function" && typeof w.stopWatchdog === "function", "排播复位 / 看门狗停止已定义");
  try {
    // 预缓冲行为：未满 0.3s 不排播；超过后排播且合并
    w.spkResetPlayback();
    w.spkPushPcm(new Uint8Array(200), 24000);   // 100 采样 ≈ 4ms
    ok(true, "spkPushPcm 可调用（小数据进入预缓冲）");
    w.spkResetPlayback();
  } catch (e) { ok(false, "预缓冲逻辑异常：" + e.message); }
  try {
    const p = w.makeWavParser();
    const bytes = new Uint8Array(44 + 4);
    const dv = new DataView(bytes.buffer);
    const ws = (o, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i)); };
    ws(0, "RIFF"); dv.setUint32(4, 36 + 4, true); ws(8, "WAVE"); ws(12, "fmt ");
    dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
    dv.setUint32(24, 24000, true); dv.setUint32(28, 48000, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
    ws(36, "data"); dv.setUint32(40, 4, true);
    dv.setInt16(44, 1234, true); dv.setInt16(46, -1234, true);
    let got = null, sr = 0;
    p.push(bytes, (pcm, s) => { got = pcm; sr = s; });
    ok(!!got && got.length === 4 && sr === 24000, "WAV 解析定位 data 块并输出 PCM(24k)=" + (got ? got.length : "null") + "B/" + sr);
  } catch (e) { ok(false, "WAV 解析器异常：" + e.message); }
  try {
    const p2 = w.makeWavParser();
    let first = null;
    // 头分段到达（模拟流式）：先 14 字节，再剩余
    const full = new Uint8Array(44 + 4);
    const dv2 = new DataView(full.buffer);
    const ws2 = (o, s) => { for (let i = 0; i < s.length; i++) dv2.setUint8(o + i, s.charCodeAt(i)); };
    ws2(0, "RIFF"); dv2.setUint32(4, 40, true); ws2(8, "WAVE"); ws2(12, "fmt ");
    dv2.setUint32(16, 16, true); dv2.setUint16(20, 1, true); dv2.setUint16(22, 1, true);
    dv2.setUint32(24, 24000, true); dv2.setUint32(28, 48000, true); dv2.setUint16(32, 2, true); dv2.setUint16(34, 16, true);
    ws2(36, "data"); dv2.setUint32(40, 4, true);
    p2.push(full.subarray(0, 14), (pcm) => { first = pcm; });
    ok(first === null, "头未到齐时不误报 PCM（分片安全）");
    let got2 = null;
    p2.push(full.subarray(14), (pcm) => { got2 = pcm; });
    ok(!!got2 && got2.length === 4, "跨分片到达后正确输出 PCM");
  } catch (e) { ok(false, "WAV 分片解析异常：" + e.message); }

  console.log(`\n${pass}/${pass + fail} 通过`);
  process.exit(fail ? 1 : 0);
}, 120);
