/* TTS 三件套「拉取清单后不被覆盖」验证：喂真实的 /api/voices 结构（含 default_model=CosyVoice2）
   断言初始化完成后 cfg.ttsModel / cfg.voice / cfg.emotion 仍是配置里的值 */
const fs=require("fs"); const {JSDOM}=require("jsdom");
const VOICES=(()=>{
  const c2=[["alex","alex · 男声（沉稳有力）"],["benjamin","benjamin · 男声"],["anna","anna · 女声"],["bella","bella · 女声"]];
  const plus=[["longanlingxin","龙安灵心 · 女声（知心温暖·旗舰）"],["longanlufeng","龙安鲁风 · 男声（明亮开朗）"]];
  return {voices:c2.map(x=>x[0]), default:"FunAudioLLM/CosyVoice2-0.5B:alex", rate:"+8%",
    models:[{id:"FunAudioLLM/CosyVoice2-0.5B",name:"CosyVoice 2",note:"",voices:c2,supports_emotion:false,emotions:[]},
            {id:"qwen-audio-3.0-tts-plus",name:"Qwen Audio 3.0 Plus",note:"",voices:plus,supports_emotion:true,
             emotions:["严肃","激动","愤怒","悲伤","大喊","耳语","轻蔑","好奇","大笑","叹息","疲惫","恐慌"]}],
    default_model:"FunAudioLLM/CosyVoice2-0.5B",
    model_voices:plus.map(x=>({id:x[0],name:x[1]})), model_default_voice:"longanlingxin",
    emotions:["严肃","激动","愤怒","悲伤"]};
})();
const cases=[
 {f:"debate-local.html", want:{ttsModel:"qwen-audio-3.0-tts-plus", voice:"longanlufeng", emotion:"严肃"}},
 {f:"debate-local-con.html", want:{ttsModel:"qwen-audio-3.0-tts-plus", voice:"longanlingxin", emotion:"严肃"}},
];
let pass=0,fail=0;
const ok=(n,c,e)=>{ if(c){pass++;console.log("  ✓ "+n);} else {fail++;console.log("  ✗ "+n+"  实际="+JSON.stringify(e));} };
(async()=>{
for(const c of cases){
  const p="/sandbox/workspace/debate-agent/web/local/"+c.f;
  const dom=new JSDOM(fs.readFileSync(p,"utf8"),{runScripts:"dangerously",pretendToBeVisual:true,
    url:"https://120.48.43.211/debate/local/"+c.f,
    beforeParse(win){
      win.fetch=async(u)=>{
        const s=String(u);
        const body=s.includes("/api/voices")?VOICES:(s.includes("/api/asr-models")?{models:[],default:""}:
                   s.includes("/api/omni-status")?{ok:false}:{});
        return {ok:true,status:200,json:async()=>body,text:async()=>"",body:null};
      };
      win.EventSource=class{constructor(){this.onmessage=null;}addEventListener(){}close(){}};
      win.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});
      Object.defineProperty(win.navigator,"mediaDevices",{value:{getUserMedia:async()=>{throw new Error("no")}},configurable:true});
      win.AudioContext=class{constructor(){this.currentTime=0;this.state="running";this.destination={};this.sampleRate=16000;}
        resume(){}createMediaStreamSource(){return{connect(){},disconnect(){}}}createScriptProcessor(){return{connect(){},disconnect(){},onaudioprocess:null}}createGain(){return{connect(){},gain:{value:0}}}createOscillator(){return{connect(){},start(){},stop(){},frequency:{}}}};
      win.MediaRecorder=class{start(){}stop(){}}; win.URL.createObjectURL=()=>"blob:x";
    }});
  await new Promise(r=>setTimeout(r,1800));
  console.log("\n【"+c.f+"】");
  let cfg=null; try{cfg=dom.window.eval("cfg");}catch(e){}
  if(!cfg){ fail++; console.log("  ✗ 读不到 cfg"); dom.window.close(); continue; }
  for(const [k,v] of Object.entries(c.want)) ok(`${k} 保持 ${v}`, String(cfg[k])===String(v), cfg[k]);
  const msel=dom.window.document.getElementById("cfgTtsModel"), vsel=dom.window.document.getElementById("cfgVoice");
  ok("下拉·模型 = 千问 plus", msel && msel.value==="qwen-audio-3.0-tts-plus", msel&&msel.value);
  ok("下拉·音色 = "+c.want.voice, vsel && vsel.value===c.want.voice, vsel&&vsel.value);
  const emo=dom.window.document.getElementById("cfgEmotion");
  ok("下拉·语气 = 严肃", emo && emo.value==="严肃", emo&&emo.value);
  ok("音色选项含龙安鲁风", vsel && [...vsel.options].some(o=>o.value==="longanlufeng"), vsel&&[...vsel.options].map(o=>o.value));
  dom.window.close();
}
console.log(`\n结果：${pass} 通过 / ${fail} 失败`); process.exit(fail?1:0);
})();
