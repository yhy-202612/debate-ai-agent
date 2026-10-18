/* 大屏辩论程序冒烟（jsdom）：node test_display.cjs */
const fs=require("fs"), path=require("path");
const {JSDOM}=require(path.join(__dirname,"node_modules","jsdom"));
const HTML_PATH="/sandbox/workspace/output/辩论大屏/time/display.html";
const HTML=fs.readFileSync(HTML_PATH,"utf8");
const dom=new JSDOM(HTML,{runScripts:"dangerously",pretendToBeVisual:true,url:"http://localhost:8080/display.html",
  beforeParse(win){
    win.__es = []; win.__pub = [];
    win.fetch = async (u, opt) => {
      try{ if(String(u).includes("/api/buzzer") && opt && opt.body) win.__pub.push(JSON.parse(opt.body)); }catch(e){}
      return { ok:true, status:200, json:async()=>({ok:true}), text:async()=>"{}", headers:{get:()=>null} };
    };
    win.EventSource = class {
      constructor(u){ this.url = u; this.onmessage = null; win.__es.push(this); }
      addEventListener(){} close(){}
    };
    win.AudioContext=class{constructor(){this.currentTime=0;this.destination={};}
      createOscillator(){return{type:"",frequency:{value:0},connect(){},start(){},stop(){}};}
      createGain(){return{gain:{setValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){}};}};
  }});
const win=dom.window, $=id=>win.document.getElementById(id);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let pass=0,fail=0;
const ok=(n,c,e="")=>{c?(pass++,console.log("  ✓ "+n)):(fail++,console.log("  ✗ "+n+"  "+e));};
const on=id=>win.document.getElementById(id).classList.contains("on");
(async()=>{
  await sleep(300);
  ok("生成 6 盏灯（每方 3 位）", win.document.querySelectorAll(".seat").length===6,
     win.document.querySelectorAll(".seat").length);
  ok("环节总数 10", $("stageTotal").textContent==="10", $("stageTotal").textContent);
  ok("初始环节=正方一辩立论", /正方一辩/.test($("stageName").textContent), $("stageName").textContent);
  ok("初始计时 03:00", $("clock").textContent==="03:00", $("clock").textContent);
  ok("正方一辩灯亮", on("seat-pro-1"));
  ok("反方无灯亮", !on("seat-con-1"));

  $("btnNext").click(); await sleep(150);
  ok("★下一环节=反方三辩质询正方一辩（质询紧跟在本方一辩立论后）", /质询/.test($("stageName").textContent) && /反方三辩/.test($("stageName").textContent) && /正方一辩/.test($("stageName").textContent), $("stageName").textContent);
  ok("质询方反方三辩灯亮", on("seat-con-3"));
  ok("被质询方正方一辩灯亮", on("seat-pro-1"));
  ok("正方三辩未误亮", !on("seat-pro-3"));

  $("btnStart").click(); await sleep(1200);
  ok("开始后计时递减（<03:00）", $("clock").textContent!=="03:00", $("clock").textContent);
  $("btnStart").click();

  // 第 6 环节：人机对辩 ① —— 正方一辩 vs 反方二辩(AI)，双方同亮
  win.eval("S.idx=5; applyStage();"); await sleep(120);
  ok("人机对辩① 环节名含正方一辩", /正方一辩/.test($("stageName").textContent), $("stageName").textContent);
  ok("人机对辩① 正方一辩灯亮（人）", on("seat-pro-1"));
  ok("人机对辩① 反方二辩灯亮（AI）", on("seat-con-2"));
  ok("人机对辩① 未误亮三辩", !on("seat-pro-3") && !on("seat-con-3"));

  // 第 7 环节：反方一辩 vs 正方二辩
  win.eval("S.idx=6; applyStage();"); await sleep(120);
  ok("人机对辩② 反方一辩灯亮（人）", on("seat-con-1"));
  ok("人机对辩② 正方二辩灯亮（AI）", on("seat-pro-2"));
  ok("人机对辩② 未误亮一辩对方", !on("seat-pro-1"));

  // 自由辩论抢话
  win.eval("S.idx=7; applyStage();"); await sleep(120);
  ok("到达自由辩论", /自由辩论/.test($("stageName").textContent), $("stageName").textContent);
  ok("自由辩论初始无灯亮", win.document.querySelectorAll(".seat.on").length===0,
     win.document.querySelectorAll(".seat.on").length);
  win.document.dispatchEvent(new win.KeyboardEvent("keydown",{key:"1",bubbles:true})); await sleep(150);
  ok("按键 1 → 正方一辩抢到", on("seat-pro-1"));
  win.document.dispatchEvent(new win.KeyboardEvent("keydown",{key:"2",bubbles:true})); await sleep(150);
  ok("★同侧按键 2（正三）被拒 → 仍是正一在说", on("seat-pro-1") && !on("seat-pro-3"));
  win.document.dispatchEvent(new win.KeyboardEvent("keydown",{key:"3",bubbles:true})); await sleep(150);
  ok("★对方按键 3（反一）→ 接话", on("seat-con-1") && !on("seat-pro-1"));

  // ★质询环节：按钮只亮灯/计时，绝不发 floor（该环节双方交替发言，靠识别）
  win.eval("S.idx=3; applyStage();"); await sleep(120);
  ok("质询环节提示「自动识别谁在说」", /自动识别/.test($("hint").textContent), $("hint").textContent);
  win.__pub.length = 0;
  win.document.dispatchEvent(new win.KeyboardEvent("keydown",{key:"1",bubbles:true})); await sleep(150);
  ok("质询环节按按钮仍亮灯", on("seat-pro-1"));
  ok("★质询环节按钮不发 floor", !win.__pub.some(p=>p.action==="floor" && p.reason==="button"), JSON.stringify(win.__pub));
  ok("★质询环节按钮提示写明自动识别", /自动识别/.test($("hint").textContent), $("hint").textContent);

  // 对照：非质询环节按钮照常发 floor
  win.eval("S.idx=0; applyStage();"); await sleep(100);
  win.__pub.length = 0;
  win.document.dispatchEvent(new win.KeyboardEvent("keydown",{key:"1",bubbles:true})); await sleep(150);
  ok("对照：立论环节按钮照常发 floor", win.__pub.some(p=>p.action==="floor" && p.who==="pro"), JSON.stringify(win.__pub));

  // 键位提示开关
  ok("初始：键位提示开着（按钮含括号）", /（空格）/.test($("btnStart").textContent), $("btnStart").textContent);
  win.document.dispatchEvent(new win.KeyboardEvent("keydown",{key:"k",bubbles:true})); await sleep(80);
  ok("★按 K → body 带 hidekeys（键位括号隐藏）", win.document.body.classList.contains("hidekeys"), win.document.body.className);
  ok("★按钮文案：键位提示：关", /键位提示：关/.test($("btnKeys").textContent), $("btnKeys").textContent);
  win.eval("S.idx=0; applyStage();"); await sleep(60);
  ok("★隐藏时中央不再显示「按 空格」", !/按 空格/.test($("hint").textContent), $("hint").textContent);
  win.document.dispatchEvent(new win.KeyboardEvent("keydown",{key:"k",bubbles:true})); await sleep(80);
  ok("★再按 K → 恢复", !win.document.body.classList.contains("hidekeys") && /键位提示：开/.test($("btnKeys").textContent), $("btnKeys").textContent);

  // 质询环节双灯
  win.eval("S.idx=3; applyStage();"); await sleep(120);
  ok("质询②：正方三辩质询反方一辩", /正方三辩/.test($("stageName").textContent) && /反方一辩/.test($("stageName").textContent), $("stageName").textContent);
  ok("质询：质询方三辩亮", on("seat-pro-3"));
  ok("质询：被质询方一辩也亮", on("seat-con-1"));

  // ★赛制顺序：一辩立论 → 对方三辩质询，交替
  const order = win.eval("RULES.map(r=>r.name)");
  ok("★赛制顺序正确（立论/质询交替，质询紧跟本方立论）",
     /正方一辩/.test(order[0]) && /质询/.test(order[1]) && /反方三辩/.test(order[1]) && /反方一辩/.test(order[2]) && /质询/.test(order[3]) && /正方三辩/.test(order[3]),
     order.slice(0,4).join(" | "));
  ok("赛制共 10 环节", order.length===10, String(order.length));

  // 与智能体联动：模拟服务端 SSE 推送
  const es = win.__es[0];
  ok("已订阅大屏联动通道", !!es && /\/debate\/api\/stage\/stream/.test(es.url), es && es.url);
  es.onmessage({data: JSON.stringify({type:"speak", side:"pro", seat:2, on:true, who:"AI 二辩"})});
  await sleep(80);
  ok("AI 发言 → 正方二辩出现 speaking 脉冲", $("seat-pro-2").classList.contains("speaking"));
  ok("AI 发言 → 底部显示发言中", /AI 二辩/.test($("talkState").textContent), $("talkState").textContent);
  es.onmessage({data: JSON.stringify({type:"speak", side:"pro", seat:2, on:false, who:"AI 二辩"})});
  await sleep(80);
  ok("发言结束 → 脉冲消失", !$("seat-pro-2").classList.contains("speaking"));
  ok("发言结束 → 底部清空", $("talkState").textContent === "", $("talkState").textContent);

  // 双方 AI 同时接入（互不覆盖）
  es.onmessage({data: JSON.stringify({type:"speak", side:"pro", seat:2, on:true, who:"正方二辩（AI）"})});
  es.onmessage({data: JSON.stringify({type:"speak", side:"con", seat:2, on:true, who:"反方二辩（AI）"})});
  await sleep(80);
  ok("双方 AI 同时发言：正方二辩脉冲", $("seat-pro-2").classList.contains("speaking"));
  ok("双方 AI 同时发言：反方二辩脉冲", $("seat-con-2").classList.contains("speaking"));
  ok("底部同时列出两方", /正方二辩（AI）/.test($("talkState").textContent) && /反方二辩（AI）/.test($("talkState").textContent),
     $("talkState").textContent);
  es.onmessage({data: JSON.stringify({type:"speak", side:"pro", seat:2, on:false})});
  await sleep(60);
  ok("只结束正方 → 反方仍在发言", !$("seat-pro-2").classList.contains("speaking") && $("seat-con-2").classList.contains("speaking"));

  // 超时自愈：AI 端异常掉线（没发 on:false）时不得永久卡住
  win.eval("TALK['con-2'].until = Date.now()-1; refreshTalkBar();");
  await sleep(60);
  ok("★超时自愈：残留的「正在发言」自动收起", !$("seat-con-2").classList.contains("speaking"));
  ok("超时后底部清空", $("talkState").textContent === "", $("talkState").textContent);

  // ── WiFi 版按钮（不接电脑）：服务器转回的 down 事件 → 与大屏本地按钮同一套逻辑 ──
  const es2 = win.__es.find(e=>/buzzer\/stream/.test(e.url));
  ok("已订阅发令总线（WiFi 按钮用）", !!es2, es2 && es2.url);
  win.eval("S.idx=7; applyStage();"); await sleep(120);   // 自由辩论
  ok("自由辩论：初始无灯", !on("seat-pro-1") && !on("seat-con-1"));
  const press = async (side, seat) => { win.__pub.length=0;
    es2.onmessage({data: JSON.stringify({action:"down", side, seat:String(seat), source:"button", board: side==="正方"?"A":"B"})}); await sleep(120); };
  const pubHas = (fn) => win.__pub.some(fn);

  await press("正方", 1);
  ok("★WiFi 按钮 → 正方一辩亮灯（等同本地按键）", on("seat-pro-1"));
  ok("首次按 = 抢到发言", pubHas(p=>p.action==="free_grant"));

  /* ★交替规则 1：同侧另一位按 → 拒绝，灯不变 */
  await press("正方", 3);
  ok("★同侧接力被拒（正三按了不亮，正一仍亮）", on("seat-pro-1") && !on("seat-pro-3"));
  ok("★同侧接力回 free_deny(reason=same-side)", pubHas(p=>p.action==="free_deny" && p.reason==="same-side"), JSON.stringify(win.__pub));
  ok("提示写明「不得同侧接力」", /不得同侧接力/.test($("hint").textContent), $("hint").textContent);

  /* ★交替规则 2：对方按 → 接话（灯换人） */
  await press("反方", 1);
  ok("★对方按 = 接话（反一亮、正一灭）", on("seat-con-1") && !on("seat-pro-1"));
  ok("交接广播 free_grant", pubHas(p=>p.action==="free_grant"));

  /* 反方三辩想接力 → 也拒 */
  await press("反方", 3);
  ok("★反方三辩同侧接力同样被拒", on("seat-con-1") && !on("seat-con-3"));

  /* 同一人再按 = 结束 */
  await press("反方", 1);
  ok("同一人再按 → 结束（灯灭）", !on("seat-con-1"));
  ok("结束时广播 free_idle", pubHas(p=>p.action==="free_idle"));

  /* ★交替规则 3：空闲状态下也不能同侧连胜 */
  await press("反方", 3);
  ok("★反方刚说完，反三再按被拒（need-turn）", !on("seat-con-3"));
  ok("回 free_deny(reason=need-turn)", pubHas(p=>p.action==="free_deny" && p.reason==="need-turn"), JSON.stringify(win.__pub));
  await press("正方", 1);
  ok("换成正方 → 正常接话", on("seat-pro-1"));

  /* AI 说完也记「上一句是这一方」 */
  es2.onmessage({data: JSON.stringify({action:"ai_done", side:"正方", source:"ai"})});
  await sleep(150);
  ok("★AI 说完 → 释放发言权（灯灭）", !on("seat-pro-1"));
  await press("正方", 3);
  ok("★AI 说完后同侧再按被拒", !on("seat-pro-3"));
  await press("反方", 3);
  ok("★AI 说完后对方可接话", on("seat-con-3"));

  console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail?1:0);
})();
