/* 验证两版页面的运行时默认配置（真解析 HTML + 执行脚本） */
const fs=require("fs");
const {JSDOM}=require("jsdom");
const cases=[
  {f:"debate-local.html", want:{side:"反方",stance:"拓展",voice:"longanlufeng",duration:15,outMode:"hybrid",ttsModel:"qwen-audio-3.0-tts-plus",emotion:"严肃",omniVoice:"Ethan",streamMode:true}},
  {f:"debate-local-con.html", want:{side:"正方",stance:"消解",voice:"longanlingxin",duration:15,outMode:"hybrid",ttsModel:"qwen-audio-3.0-tts-plus",emotion:"严肃",omniVoice:"Serena",streamMode:true}},
];
let pass=0,fail=0;
const ok=(n,c,e)=>{ if(c){pass++;console.log("  ✓ "+n);} else {fail++;console.log("  ✗ "+n+"  "+JSON.stringify(e));} };
(async()=>{
for(const c of cases){
  const p="/sandbox/workspace/debate-agent/web/local/"+c.f;
  const html=fs.readFileSync(p,"utf8");
  const dom=new JSDOM(html,{runScripts:"dangerously",pretendToBeVisual:true,url:"https://120.48.43.211/debate/local/"+c.f,
    beforeParse(win){
      win.fetch=async()=>({ok:true,json:async()=>({models:[],voices:[],emotions:[]}),text:async()=>"",body:null});
      win.EventSource=class{constructor(){this.onmessage=null;}addEventListener(){}close(){}};
      win.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});
      Object.defineProperty(win.navigator,"mediaDevices",{value:{getUserMedia:async()=>{throw new Error("no")}},configurable:true});
      win.AudioContext=class{constructor(){this.currentTime=0;this.state="running";this.destination={};this.sampleRate=16000;}
        resume(){}createMediaStreamSource(){return{connect(){},disconnect(){}}}createScriptProcessor(){return{connect(){},disconnect(){},onaudioprocess:null}}createGain(){return{connect(){},gain:{value:0}}}createOscillator(){return{connect(){},start(){},stop(){},frequency:{}}}};
      win.MediaRecorder=class{start(){}stop(){}}; win.URL.createObjectURL=()=>"blob:x";
    }});
  await new Promise(r=>setTimeout(r,1500));
  let cfg=null; try{ cfg=dom.window.eval("cfg"); }catch(e){}
  console.log("\n【"+c.f+"】");
  if(!cfg){ fail++; console.log("  ✗ 未能读取 cfg"); continue; }
  for(const [k,v] of Object.entries(c.want)) ok(`${k} = ${v}`, String(cfg[k])===String(v), cfg[k]);
  dom.window.close();
}
console.log(`\n结果：${pass} 通过 / ${fail} 失败\n`); process.exit(fail?1:0);
})();
