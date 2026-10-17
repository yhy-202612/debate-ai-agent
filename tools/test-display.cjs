/* 大屏辩论程序冒烟（jsdom）：node test_display.cjs */
const fs=require("fs"), path=require("path");
const {JSDOM}=require(path.join(__dirname,"node_modules","jsdom"));
const HTML_PATH="/sandbox/workspace/output/辩论大屏/time/display.html";
const HTML=fs.readFileSync(HTML_PATH,"utf8");
const dom=new JSDOM(HTML,{runScripts:"dangerously",pretendToBeVisual:true,url:"http://localhost:8080/display.html",
  beforeParse(win){
    win.__es = [];
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
  ok("下一环节=反方一辩立论", /反方一辩/.test($("stageName").textContent), $("stageName").textContent);
  ok("反方一辩灯亮", on("seat-con-1"));
  ok("正方灯已灭", !on("seat-pro-1"));

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
  ok("换人按键 2 → 交接给正方三辩", on("seat-pro-3") && !on("seat-pro-1"));

  // 质询环节双灯
  win.eval("S.idx=2; applyStage();"); await sleep(120);
  ok("质询：质询方三辩亮", on("seat-pro-3"));
  ok("质询：被质询方一辩也亮", on("seat-con-1"));

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

  console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail?1:0);
})();
