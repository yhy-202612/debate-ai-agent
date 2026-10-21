/* 训练版 ↔ 大屏 一致性：用大屏真实的 RULES 逐环节广播，核对两版 AI 的 aiOnStage
   规则：本环节是否「轮到二辩（AI）」＝立论/质询/总结/人人对辩 一律 false；
        人机对辩按 target 判断；自由辩论 true。 */
const fs=require("fs"); const {JSDOM}=require("jsdom");
const DISPLAY="/sandbox/workspace/output/辩论大屏/time/display.html";
let pass=0,fail=0;
const ok=(n,c,e)=>{ if(c){pass++;console.log("  ✓ "+n);} else {fail++;console.log("  ✗ "+n+"  实际="+JSON.stringify(e));} };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

/* 从大屏源码里取出 RULES（保证与大屏同一份数据，不手抄） */
const dhtml=fs.readFileSync(DISPLAY,"utf8");
const start=dhtml.indexOf("const RULES = [");
const end=dhtml.indexOf("];", start);
const RULES=eval(dhtml.slice(start+("const RULES = ").length, end+1));
console.log("从大屏读到环节数:", RULES.length);

function expectFor(rule, mySide){
  const k=rule.kind;
  if(k==="free") return true;
  if(k==="cross") return !!(rule.target && rule.target.side===(mySide==="反方"?"con":"pro") && Number(rule.target.seat)===2);
  return false;   // forced / quiz / duel → 不是二辩的场
}
async function boot(f){
  const dom=new JSDOM(fs.readFileSync("/sandbox/workspace/debate-agent/web/local/"+f,"utf8"),
    {runScripts:"dangerously",pretendToBeVisual:true,url:"https://120.48.43.211/debate/local/"+f,
     beforeParse(win){
       win.fetch=async(u)=>{const s=String(u);let b={};
         if(s.includes("/api/voices")) b={voices:["alex"],default:"alex",models:[{id:"qwen-audio-3.0-tts-plus",name:"p",voices:[["longanlufeng","男"]],supports_emotion:true,emotions:["严肃"]}],default_model:"qwen-audio-3.0-tts-plus",emotions:["严肃"]};
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
(async()=>{
for(const f of ["debate-local.html","debate-local-con.html"]){
  const dom=await boot(f); const w=dom.window; const ours=w.eval("cfg.side");
  console.log("\n【"+f+"  我方="+ours+"】");
  const es=()=>(w.__es||[]).find(e=>e.url.includes("buzzer/stream"));
  for(let i=0;i<RULES.length;i++){
    const r=RULES[i];
    es().onmessage({data:JSON.stringify({action:"stage",kind:r.kind,stage:i+1,side:r.side,seat:r.seat,
                                         target:r.target||null,floor:"hall",name:r.name})});
    await sleep(40);
    const got=w.eval("aiOnStage"), want=expectFor(r,ours);
    ok(`${String(i+1).padStart(2)} ${r.name.slice(0,26).padEnd(26)} → ${want?"AI 上场":"AI 旁观"}`, got===want, {got,want,kind:r.kind});
  }
  dom.window.close();
}
console.log(`\n结果：${pass} 通过 / ${fail} 失败`); process.exit(fail?1:0);
})();
