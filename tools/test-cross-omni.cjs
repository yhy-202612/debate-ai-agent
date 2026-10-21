/* 人机对辩：抢跑未出稿 → 必须转端到端（而不是干等级联）
   ①级联稿未落地 → 调 /api/rebut-omni
   ②级联已出句 → 不转（照常 drain 播报） */
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
         if(s.includes("/api/voices")) b={voices:["alex"],default:"alex",models:[{id:"qwen-audio-3.0-tts-plus",name:"p",voices:[["longanlufeng","男"]],supports_emotion:true,emotions:["严肃"]}],default_model:"qwen-audio-3.0-tts-plus",emotions:["严肃"]};
         else if(s.includes("/api/should-speak")) b={speak:false};
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
(async()=>{
for(const f of ["debate-local.html","debate-local-con.html"]){
  const dom=await boot(f); const w=dom.window; const ours=w.eval("cfg.side");
  w.eval("cfg.mode='auto'");
  console.log("\n【"+f+"】");
  const tgt = ours==="反方" ? {side:"con",seat:2} : {side:"pro",seat:2};
  push(dom,{action:"stage",kind:"cross",stage:ours==="反方"?5:6,
            side:ours==="反方"?"pro":"con",seat:1,target:tgt});
  await sleep(60);
  await w.eval("ingest('我方认为人工智能会消解人的价值，这是不容置疑的。', null)");
  await sleep(150);
  /* —— 情形一：抢跑稿未落地 —— */
  w.eval("preHold = true; queue.length = 0; lastSentences = []; preAudioBuf = []; genBusy = false; lastIngestAt = Date.now()-3000; lastSpeakAt = Date.now()-5000; lastPlayEndAt = 0;");
  const n0=(w.__calls||[]).length;
  await w.eval("runSpeakCheck()");
  await sleep(600);
  const c0=(w.__calls||[]).slice(n0);
  ok("★抢跑未出稿 → 调 /api/rebut-omni", c0.some(u=>u.includes("/api/rebut-omni")), c0);
  ok("同时不再走级联 /api/rebut", !c0.some(u=>u.trim().endsWith("/api/rebut")), c0);
  ok("preHold 已解除", w.eval("preHold")===false, w.eval("preHold"));
  /* —— 情形二：抢跑稿已在队列里 → 不转端到端 —— */
  w.eval("genBusy=false; preHold = true; lastSentences = ['第一句']; queue.length = 0; lastSpeakAt = Date.now()-5000; lastIngestAt = Date.now()-3000;");
  const n1=(w.__calls||[]).length;
  await w.eval("runSpeakCheck()");
  await sleep(500);
  const c1=(w.__calls||[]).slice(n1);
  ok("已有稿 → 不转端到端", !c1.some(u=>u.includes("/api/rebut-omni")), c1);
  dom.window.close();
}
console.log(`\n结果：${pass} 通过 / ${fail} 失败`); process.exit(fail?1:0);
})();
