/* 人机对辩（cross）链路：
   ①我方 AI 上场 → 真人话直接判 opp、能接话（原故障：判成 hall 永不接话）
   ②主席串场仍 hall
   ③对方 AI 上场 → 不推定、走归属识别（有两路声音）
   ④非 cross 环节不强行推定 */
const fs=require("fs"); const {JSDOM}=require("jsdom");
let pass=0,fail=0;
const ok=(n,c,e)=>{ if(c){pass++;console.log("  ✓ "+n);} else {fail++;console.log("  ✗ "+n+"  实际="+JSON.stringify(e));} };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function boot(f){
  const dom=new JSDOM(fs.readFileSync("/sandbox/workspace/debate-agent/web/local/"+f,"utf8"),
    {runScripts:"dangerously",pretendToBeVisual:true,url:"https://120.48.43.211/debate/local/"+f,
     beforeParse(win){
       win.__calls=[];
       win.fetch=async(u)=>{ win.__calls.push(String(u)); const s=String(u); let b={};
         if(s.includes("/api/voices")) b={voices:["alex"],default:"alex",models:[{id:"qwen-audio-3.0-tts-plus",name:"p",voices:[["longanlufeng","男"],["longanlingxin","女"]],supports_emotion:true,emotions:["严肃"]}],default_model:"qwen-audio-3.0-tts-plus",emotions:["严肃"]};
         else if(s.includes("/api/should-speak")) b={speak:true,reason:"stub"};
         else if(s.includes("/api/attribute")) b={items:[{idx:1,who:"ours"}]};
         return {ok:true,status:200,json:async()=>b,text:async()=>"",body:null};};
       win.EventSource=class{constructor(u){this.url=String(u);(win.__es=win.__es||[]).push(this);}addEventListener(){}close(){}};
       win.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});
       Object.defineProperty(win.navigator,"mediaDevices",{value:{getUserMedia:async()=>{throw new Error("no")}},configurable:true});
       win.AudioContext=class{constructor(){this.currentTime=0;this.state="running";this.destination={};this.sampleRate=16000;}
         resume(){}createMediaStreamSource(){return{connect(){},disconnect(){}}}createScriptProcessor(){return{connect(){},disconnect(){},onaudioprocess:null}}createGain(){return{connect(){},gain:{value:0}}}createOscillator(){return{connect(){},start(){},stop(){},frequency:{}}}};
       win.MediaRecorder=class{start(){}stop(){}}; win.URL.createObjectURL=()=>"blob:x";
     }});
  await sleep(1500); return dom;
}
function push(dom,ev){ (dom.window.__es||[]).find(e=>e.url.includes("buzzer/stream")).onmessage({data:JSON.stringify(ev)}); }
const lastWho=w=>w.eval("transcript[transcript.length-1] && transcript[transcript.length-1].who");

(async()=>{
for(const f of ["debate-local.html","debate-local-con.html"]){
  const dom=await boot(f); const w=dom.window; const ours=w.eval("cfg.side");
  w.eval("cfg.mode='auto'");   // 现场为全自动模式（默认 test 时 ingest 不判归属）
  w.eval("cfg.mode='auto'");   // 现场就是全自动模式；默认值见 cfg.mode 的迁移说明
  console.log("\n【"+f+"  我方="+ours+"】");
  /* ===== 情形一：本页 AI 上场（target 指向我方二辩）===== */
  const tgtMine = ours==="反方" ? {side:"con",seat:2} : {side:"pro",seat:2};
  push(dom,{action:"stage",kind:"cross",stage:ours==="反方"?5:6,
            side:ours==="反方"?"pro":"con",seat:1,target:tgtMine});
  await sleep(60);
  ok("aiOnStage=true（轮到我方 AI）", w.eval("aiOnStage")===true, w.eval("aiOnStage"));
  ok("floor 仍是主席（原故障现场）", w.eval("floorWho")==="hall", w.eval("floorWho"));
  ok("streamOppTalking=true（抢跑可用）", w.eval("streamOppTalking()")===true, w.eval("streamOppTalking()"));
  const n0=(w.__calls||[]).length;
  await w.eval("ingest('我方认为，人工智能取代岗位就意味着人的价值被消解，这是不容置疑的。', null)");
  await sleep(150);
  ok("★真人一辩 → opp（此前判 hall 导致永不接话）", lastWho(w)==="opp", lastWho(w));
  ok("推定路径不调 attribute（零额外延迟）",
     !(w.__calls||[]).slice(n0).some(u=>u.includes("/api/attribute")), (w.__calls||[]).slice(n0));
  await w.eval("ingest('下面进行人机对辩环节，双方各两分半钟。', null)");
  await sleep(150);
  ok("主席串场 → hall（不会被当对方去抢）", lastWho(w)==="hall", lastWho(w));
  /* 真能接话：把静默推长再跑一次判定 */
  await w.eval("ingest('那么请问对方，你们判定价值的标准到底是什么？', null)");
  await sleep(150);
  w.eval("lastIngestAt = Date.now() - 6000; lastPlayEndAt = 0; preHold = false;");
  const n1=(w.__calls||[]).length;
  await w.eval("runSpeakCheck()");
  await sleep(900);
  const fired=(w.__calls||[]).slice(n1).some(u=>u.includes("should-speak")||u.includes("/api/rebut"));
  ok("★静默够了 → 触发判定/出稿（能接话）", fired, (w.__calls||[]).slice(n1));

  /* ===== 情形二：对方 AI 上场（target 指向对方二辩）===== */
  const tgtOther = ours==="反方" ? {side:"pro",seat:2} : {side:"con",seat:2};
  push(dom,{action:"stage",kind:"cross",stage:ours==="反方"?6:5,
            side:ours,seat:1,target:tgtOther});
  await sleep(60);
  ok("aiOnStage=false（不是我这轮）", w.eval("aiOnStage")===false, w.eval("aiOnStage"));
  ok("streamOppTalking=false（不抢跑）", w.eval("streamOppTalking()")===false, w.eval("streamOppTalking()"));
  ok("crossForcedWho=''（不强行推定）", w.eval("crossForcedWho('随便一句话')")==="", w.eval("crossForcedWho('随便一句话')"));
  const n2=(w.__calls||[]).length;
  await w.eval("ingest('我方一辩：技术的边界应该由人来划定。', null)");
  await sleep(200);
  ok("★对方上场时走归属识别（区分我方一辩/对方 AI）",
     (w.__calls||[]).slice(n2).some(u=>u.includes("/api/attribute")), (w.__calls||[]).slice(n2));

  /* ===== 情形三：非人机对辩环节 ===== */
  push(dom,{action:"stage",kind:"forced",stage:1,side:ours==="反方"?"pro":"con",seat:1});
  await sleep(60);
  ok("非 cross → 不强行推定", w.eval("crossForcedWho('我方认为人工智能会拓展人的价值')")==="", w.eval("crossForcedWho('我方认为人工智能会拓展人的价值')"));
  dom.window.close();
}
console.log(`\n结果：${pass} 通过 / ${fail} 失败`); process.exit(fail?1:0);
})();
