/* 大屏广播 → 智能体：环节类型/环节号/target 是否正确生效
   核心红线：人机对辩 ②（正方机器人上场）时，反方 AI 的 aiOnStage 必须为 false（不许插话） */
const fs=require("fs"); const {JSDOM}=require("jsdom");
let pass=0,fail=0;
const ok=(n,c,e)=>{ if(c){pass++;console.log("  ✓ "+n);} else {fail++;console.log("  ✗ "+n+"  实际="+JSON.stringify(e));} };
async function boot(f){
  const dom=new JSDOM(fs.readFileSync("/sandbox/workspace/debate-agent/web/local/"+f,"utf8"),
    {runScripts:"dangerously",pretendToBeVisual:true,url:"https://120.48.43.211/debate/local/"+f,
     beforeParse(win){
       win.fetch=async(u)=>{const s=String(u);
         const b=s.includes("/api/voices")?{voices:["alex"],default:"alex",models:[{id:"qwen-audio-3.0-tts-plus",name:"p",voices:[["longanlufeng","男"],["longanlingxin","女"]],supports_emotion:true,emotions:["严肃"]}],default_model:"qwen-audio-3.0-tts-plus",emotions:["严肃"]}:
                  (s.includes("/api/omni-status")?{ok:false}:{});
         return {ok:true,status:200,json:async()=>b,text:async()=>"",body:null};};
       win.EventSource=class{constructor(u){this.url=String(u);(win.__es=win.__es||[]).push(this);}addEventListener(){}close(){}};
       win.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});
       Object.defineProperty(win.navigator,"mediaDevices",{value:{getUserMedia:async()=>{throw new Error("no")}},configurable:true});
       win.AudioContext=class{constructor(){this.currentTime=0;this.state="running";this.destination={};this.sampleRate=16000;}
         resume(){}createMediaStreamSource(){return{connect(){},disconnect(){}}}createScriptProcessor(){return{connect(){},disconnect(){},onaudioprocess:null}}createGain(){return{connect(){},gain:{value:0}}}createOscillator(){return{connect(){},start(){},stop(){},frequency:{}}}};
       win.MediaRecorder=class{start(){}stop(){}}; win.URL.createObjectURL=()=>"blob:x";
     }});
  await new Promise(r=>setTimeout(r,1500));
  return dom;
}
function push(dom, ev){
  const es=(dom.window.__es||[]).find(e=>e.url.includes("buzzer/stream"));
  if(!es||!es.onmessage) throw new Error("未找到 buzzer 订阅");
  es.onmessage({data:JSON.stringify(ev)});
}
(async()=>{
for(const f of ["debate-local.html","debate-local-con.html"]){
  const dom=await boot(f); const w=dom.window;
  const cfg=w.eval("cfg"); const ours=cfg.side;           // 本页所属方
  console.log("\n【"+f+"  我方="+ours+"】");
  // 1 自由辩论
  push(dom,{action:"stage",kind:"free",stage:8,side:"both",seat:0});
  ok("free → freeMode=true", w.eval("freeMode")===true, w.eval("freeMode"));
  ok("free → 环节号=8（数字）", w.eval("curStage")===8, w.eval("curStage"));
  // 2 人机对辩①：反方机器人上场（target = 反方二辩）
  push(dom,{action:"stage",kind:"cross",stage:5,side:"pro",seat:1,target:{side:"con",seat:2}});
  ok("cross①→环节号=5", w.eval("curStage")===5, w.eval("curStage"));
  const onA=w.eval("aiOnStage");
  ok("cross① 我方=反方 → 可发言", ours==="反方" ? onA===true : onA===false, onA);
  // 3 人机对辩②：正方机器人上场 ★红线
  push(dom,{action:"stage",kind:"cross",stage:6,side:"con",seat:1,target:{side:"pro",seat:2}});
  const onB=w.eval("aiOnStage");
  ok("★cross② 我方=反方 → 禁止插话", ours==="反方" ? onB===false : onB===true, onB);
  ok("cross②→环节号=6", w.eval("curStage")===6, w.eval("curStage"));
  // 4 立论（非我方上场）
  push(dom,{action:"stage",kind:"forced",stage:1,side:"pro",seat:1});
  ok("forced → 不许抢", w.eval("aiOnStage")===false, w.eval("aiOnStage"));
  ok("forced → freeMode 复位", w.eval("freeMode")===false, w.eval("freeMode"));
  // 5 兼容字符串 stage（旧固件/旧大屏）
  push(dom,{action:"stage",kind:"duel",stage:"7",side:"both",seat:3});
  ok("数字字符串 stage 也能识别", w.eval("curStage")===7, w.eval("curStage"));
  dom.window.close();
}
console.log(`\n结果：${pass} 通过 / ${fail} 失败`); process.exit(fail?1:0);
})();
