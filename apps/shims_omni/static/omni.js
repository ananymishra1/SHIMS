const $ = (id)=>document.getElementById(id);
const chat = $('chat');
let lastText = '';
let voiceEnabled = false;
let recognition = null;
let listening = false;

function add(role, text, meta=''){
  const div=document.createElement('div'); div.className='msg '+role;
  const m=document.createElement('div'); m.className='meta'; m.textContent=meta || (role==='user'?'You':'SHIMS Omni');
  const body=document.createElement('div'); body.textContent=text;
  div.appendChild(m); div.appendChild(body); chat.appendChild(div); chat.scrollTop=chat.scrollHeight;
  if(role==='assistant'){ lastText=text; if(voiceEnabled) speak(text); }
}
async function post(url, data){ const res=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); if(!res.ok) throw new Error(await res.text()); return await res.json(); }
function currentContext(){ return {provider:$('provider').value, model:$('modelSelect').value, temperature:0.7, voice_mode:voiceEnabled}; }

async function refreshModels(){
  const sel=$('modelSelect'); const current=sel.value;
  try{
    const r=await fetch('/api/models').then(r=>r.json());
    sel.innerHTML='';
    const names = (r.models && r.models.length) ? r.models : ['llama3.2:latest'];
    for(const name of names){ const o=document.createElement('option'); o.value=name; o.textContent=name; sel.appendChild(o); }
    if(names.includes(current)) sel.value=current;
    $('modelHint').textContent = r.ok ? `Found ${names.length} local Ollama model(s).` : `Ollama not reachable: ${r.error || 'unknown'}`;
    routeBadge.textContent = r.ok ? 'Ollama ready' : 'Ollama offline';
  }catch(e){ $('modelHint').textContent='Model refresh failed: '+e.message; }
}

async function sendMessage(text){
  const msg=text || $('message').value; if(!msg.trim()) return;
  add('user', msg, 'You'); $('message').value='';
  const meta=`${$('provider').value} · ${$('modelSelect').value || 'default'}`;
  try{
    const r=await post('/api/chat',{message:msg, provider:$('provider').value, model:$('modelSelect').value, voice_mode:voiceEnabled, context:currentContext()});
    add('assistant', r.content || JSON.stringify(r), `${r.provider || 'assistant'} · ${r.model || $('modelSelect').value || 'default'}`);
  }catch(e){ add('assistant','Chat failed: '+e.message,'error'); }
}

function loadVoices(){
  const voices=speechSynthesis.getVoices(); const sel=$('voiceSelect'); const old=sel.value; sel.innerHTML='';
  voices.forEach((v,i)=>{ const o=document.createElement('option'); o.value=String(i); o.textContent=`${v.name} (${v.lang})${v.default?' default':''}`; sel.appendChild(o); });
  const preferred=voices.findIndex(v=>/en-IN|en-US|en-GB/i.test(v.lang) && /female|natural|neural|google|microsoft|zira|aria/i.test(v.name));
  if(old) sel.value=old; else if(preferred>=0) sel.value=String(preferred);
}
function speak(text){
  if(!('speechSynthesis' in window)) return;
  speechSynthesis.cancel();
  const u=new SpeechSynthesisUtterance(text.replace(/```[\s\S]*?```/g,'code block omitted for speech'));
  const voices=speechSynthesis.getVoices(); const idx=parseInt($('voiceSelect').value||'-1',10);
  if(voices[idx]) u.voice=voices[idx];
  u.rate=parseFloat($('rate').value); u.pitch=parseFloat($('pitch').value); u.volume=parseFloat($('volume').value);
  u.onstart=()=>voiceBadge.textContent='Speaking'; u.onend=()=>voiceBadge.textContent=voiceEnabled?'Voice on':'Voice idle';
  speechSynthesis.speak(u);
}
function setupMic(){
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(!SR){ alert('Speech recognition is not available in this browser. Use Chrome or Edge on localhost.'); return null; }
  const r=new SR(); r.lang='en-IN'; r.continuous=false; r.interimResults=true;
  let finalText='';
  r.onstart=()=>{ listening=true; micToggle.classList.add('active'); voiceBadge.textContent='Listening'; };
  r.onresult=(e)=>{ let interim=''; for(let i=e.resultIndex;i<e.results.length;i++){ const t=e.results[i][0].transcript; if(e.results[i].isFinal) finalText += t; else interim += t; } $('message').value=(finalText || interim).trim(); };
  r.onend=()=>{ listening=false; micToggle.classList.remove('active'); voiceBadge.textContent=voiceEnabled?'Voice on':'Voice idle'; if(finalText.trim()) sendMessage(finalText.trim()); };
  r.onerror=(e)=>{ listening=false; voiceBadge.textContent='Mic error: '+e.error; };
  return r;
}

$('chatForm').addEventListener('submit', e=>{ e.preventDefault(); sendMessage(); });
$('message').addEventListener('keydown', e=>{ if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); sendMessage(); }});
$('refreshModels').onclick=refreshModels;
$('warmModel').onclick=()=>sendMessage('Warm this model with a one sentence greeting and confirm you are ready.');
$('voiceToggle').onclick=()=>{ voiceEnabled=!voiceEnabled; $('voiceToggle').classList.toggle('active', voiceEnabled); $('voiceToggle').textContent=voiceEnabled?'Voice On':'Voice Off'; voiceBadge.textContent=voiceEnabled?'Voice on':'Voice idle'; };
$('micToggle').onclick=()=>{ if(!recognition) recognition=setupMic(); if(!recognition) return; if(listening){ recognition.stop(); } else { finalText=''; recognition.start(); }};
$('speakLast').onclick=()=>{ if(lastText) speak(lastText); };
$('stopSpeak').onclick=()=>speechSynthesis.cancel();
['rate','pitch','volume'].forEach(id=>$(id).oninput=()=>$(id+'Val').textContent=$(id).value);
if('speechSynthesis' in window){ speechSynthesis.onvoiceschanged=loadVoices; loadVoices(); }
$('pair').onclick=async()=>{ pairOut.textContent=JSON.stringify(await post('/api/pair',{enterprise_url:enterpriseUrl.value,bridge_token:bridgeToken.value}),null,2); };
$('unpair').onclick=async()=>{ pairOut.textContent=JSON.stringify(await post('/api/unpair',{}),null,2); };
$('overview').onclick=async()=>{ pairOut.textContent=JSON.stringify(await fetch('/api/enterprise/overview').then(r=>r.json()),null,2); };
document.querySelectorAll('[data-tool]').forEach(btn=>btn.onclick=async()=>{ const prompt=$('message').value || 'Create a SHIMS sample output'; let r; try{ if(btn.dataset.tool==='doc') r=await post('/api/document',{title:'SHIMS Document',body:prompt,output_type:'docx'}); if(btn.dataset.tool==='image') r=await post('/api/image',{prompt}); if(btn.dataset.tool==='video') r=await post('/api/video',{prompt}); if(btn.dataset.tool==='code') r=await post('/api/code',{task:prompt}); if(btn.dataset.tool==='evolve') r=await post('/api/self-evolve',{goal:prompt,apply:false}); toolOut.textContent=JSON.stringify(r,null,2); }catch(e){ toolOut.textContent=e.message; }});
refreshModels();
add('assistant','Omni voice/model hotfix loaded. Refresh Models, choose llama3.2:latest, turn Voice On, then use Mic or typed chat.','system');
