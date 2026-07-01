(() => {
'use strict';
const API = window.location.origin;
const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => Array.from(root.querySelectorAll(s));
const auth = () => ({'X-Shims-Token': localStorage.shimsAccessToken || ''});

const state = {
  sessionId: localStorage.shimsSessionId || null,
  provider: localStorage.shimsProvider || 'ollama',
  selectedModel: localStorage.shimsModel || '',
  streaming: false,
  voiceOn: localStorage.shimsVoice === 'true',
  converseMode: localStorage.shimsConverse !== 'false',
  webMode: localStorage.shimsWeb === 'true',
  peersMode: localStorage.shimsPeers === 'true',
  voiceLang: localStorage.shimsVoiceLang || 'en-IN',
  wakeArmed: localStorage.shimsWakeArmed === 'true',
  privacyMode: localStorage.shimsPrivacy || 'balanced',
  recognition: null,
  speakingAudio: null,
  speakBusy: false,
  lastVoiceSentText: '',
  lastVoiceSentAt: 0,
  lastTypedSentText: '',
  lastTypedSentAt: 0,
  listeningSuppressedUntil: 0,
  models: {installed:[], recommended:[], cloud:[]},
  voiceConfig: null,
  abort: null,
  crashContext: null
};
state.serverVoiceStream = null;
state.serverVoiceLoop = false;
state.serverVoiceShouldResume = false;
state.browserSttFailed = false;  // set when browser (Google) STT errors with network/service issues
state.lastVoiceStatusAt = 0;
state.wakeLatchUntil = 0;
state.wakeAckTimer = null;
state.lastWakeAckAt = 0;
state.serverSttBackoffUntil = 0;

// Frozen voice provider mode: 'cloud' (fastest, requires keys), 'fast' (browser + local fallback), 'local' (local only), 'offline' (no cloud ever).
const VOICE_MODE = (localStorage.shimsVoiceMode || 'fast').toLowerCase();
const SERVER_STT_CHUNK_MS = Math.max(600, Math.min(1200, Number(localStorage.shimsServerSttChunkMs || (VOICE_MODE==='local'||VOICE_MODE==='offline'?1000:900)) || 900));
const VOICE_CORRECTION_ENABLED = localStorage.shimsVoiceCorrection === 'true' || VOICE_MODE === 'cloud';

window.SHIMS_V13_STATE = state; window.SHIMS_V11_STATE = state;

/* ========================================================================
   Audio Wake Word Engine — runs before STT to detect custom wake words
   ======================================================================== */
class WakeWordEngine {
  constructor() {
    this.ctx = null;
    this.source = null;
    this.processor = null;
    this.stream = null;
    this.buffer = [];
    this.sampleRate = 16000;
    this.chunkSize = 24000; // 1.5 seconds @ 16kHz
    this.detecting = false;
    this.cooldownUntil = 0;
    this.onDetected = null;
    this._inFlight = false;
    this._lastSentAt = 0;
    this._minIntervalMs = 3200;
    this._backoffUntil = 0;
  }

  async start(onDetected) {
    if (this.detecting) return;
    this.onDetected = onDetected;
    this.detecting = true;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      this.ctx = new AudioCtx({ sampleRate: this.sampleRate });
      let workletReady = false;
      if (this.ctx.audioWorklet && window.AudioWorkletNode) {
        try {
          await this.ctx.audioWorklet.addModule('data:application/javascript;base64,' + btoa(`
            class WWProcessor extends AudioWorkletProcessor {
              process(inputs) {
                const ch = inputs[0] && inputs[0][0];
                if (ch) this.port.postMessage({ samples: ch.slice() });
                return true;
              }
            }
            registerProcessor('ww-processor', WWProcessor);
          `));
          workletReady = true;
        } catch (e) {
          console.warn('AudioWorklet unavailable, using ScriptProcessor fallback:', e);
        }
      }
      this.source = this.ctx.createMediaStreamSource(this.stream);
      // Prefer AudioWorklet, fall back to ScriptProcessorNode
      if (workletReady) {
        this.processor = new AudioWorkletNode(this.ctx, 'ww-processor', { numberOfInputs: 1, numberOfOutputs: 0 });
        this.processor.port.onmessage = (ev) => this._pushSamples(ev.data.samples);
      } else {
        const sp = this.ctx.createScriptProcessor(4096, 1, 1);
        sp.onaudioprocess = (e) => this._pushSamples(new Float32Array(e.inputBuffer.getChannelData(0)));
        this.processor = sp;
      }
      this.source.connect(this.processor);
      if (!this.ctx.audioWorklet) this.processor.connect(this.ctx.destination);
    } catch (e) {
      console.warn('WakeWordEngine start failed:', e);
      this.stop();
    }
  }

  _pushSamples(samples) {
    if (!this.detecting) return;
    this.buffer.push(...samples);
    // Cap buffer to avoid memory growth when sending is back-pressured
    if (this.buffer.length > this.chunkSize * 3) {
      this.buffer = this.buffer.slice(-this.chunkSize * 2);
    }
    if (this.buffer.length >= this.chunkSize) {
      const chunk = this.buffer.splice(0, this.chunkSize);
      this._sendChunk(new Float32Array(chunk));
    }
  }

  async _sendChunk(floatSamples) {
    if (this._inFlight) return;
    const now = Date.now();
    if (now < this.cooldownUntil) return;
    if (now < this._backoffUntil) return;
    if (now - this._lastSentAt < this._minIntervalMs) return;
    this._inFlight = true;
    this._lastSentAt = now;
    // Convert float [-1,1] to 16-bit PCM WAV
    const pcm = new Int16Array(floatSamples.length);
    for (let i = 0; i < floatSamples.length; i++) {
      pcm[i] = Math.max(-32768, Math.min(32767, floatSamples[i] * 32767));
    }
    const wav = this._pcmToWav(pcm, this.sampleRate);
    try {
      const form = new FormData();
      form.append('file', new Blob([wav], { type: 'audio/wav' }), 'chunk.wav');
      const r = await fetch('/voice/wakeword/detect', { method: 'POST', body: form });
      if (r.status === 429) {
        this._backoffUntil = Date.now() + 10000;
        return;
      }
      if (!r.ok) return;
      const d = await r.json();
      if (d && d.ok && d.detected) {
        this.cooldownUntil = Date.now() + 4500;
        if (typeof this.onDetected === 'function') this.onDetected(d);
      }
    } catch (e) {
      // Network or server errors are silent in wake-word loop
    } finally {
      this._inFlight = false;
    }
  }

  _pcmToWav(pcm, sampleRate) {
    const buf = new ArrayBuffer(44 + pcm.length * 2);
    const view = new DataView(buf);
    const writeStr = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
    writeStr(0, 'RIFF'); view.setUint32(4, 36 + pcm.length * 2, true);
    writeStr(8, 'WAVE'); writeStr(12, 'fmt '); view.setUint32(16, 16, true);
    view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    writeStr(36, 'data'); view.setUint32(40, pcm.length * 2, true);
    for (let i = 0; i < pcm.length; i++) view.setInt16(44 + i * 2, pcm[i], true);
    return new Uint8Array(buf);
  }

  stop() {
    this.detecting = false;
    this._inFlight = false;
    try { this.processor?.disconnect?.(); } catch (e) {}
    try { this.source?.disconnect?.(); } catch (e) {}
    try { this.ctx?.close?.(); } catch (e) {}
    try { this.stream?.getTracks?.().forEach(t => t.stop()); } catch (e) {}
    this.processor = null; this.source = null; this.ctx = null; this.stream = null; this.buffer = [];
  }
}
state.wakeEngine = new WakeWordEngine();

function persist(){
  if (state.sessionId) localStorage.shimsSessionId = state.sessionId;
  localStorage.shimsProvider = state.provider || 'ollama';
  localStorage.shimsModel = state.selectedModel || '';
  localStorage.shimsVoiceLang = state.voiceLang || 'en-IN';
  localStorage.shimsVoice = state.voiceOn ? 'true' : 'false';
  localStorage.shimsConverse = state.converseMode ? 'true' : 'false';
  localStorage.shimsWeb = state.webMode ? 'true' : 'false';
  localStorage.shimsPeers = state.peersMode ? 'true' : 'false';
  localStorage.shimsWakeArmed = state.wakeArmed ? 'true' : 'false';
  localStorage.shimsPrivacy = state.privacyMode || 'balanced';
}
function escapeHtml(s){return String(s||'').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function md(s){
  return escapeHtml(s||'')
    .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/\n/g, '<br>');
}
function toast(msg, type='info'){
  const host = $('#toast-host'); if(!host) return;
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(()=>el.remove(), 4200);
}
function feed(msg, type='info'){
  const box = $('#event-feed'); if(!box) return;
  const row = document.createElement('div');
  row.className = 'feed-row ' + type;
  row.innerHTML = `<span>${new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'})}</span> ${escapeHtml(msg)}`;
  box.prepend(row);
  while(box.children.length>80) box.lastChild.remove();
}
function setText(id, val){ const el = $(id); if(el) el.textContent = val; }
function setOrbState(s){ setText('#orb-state', String(s||'standby').toUpperCase()); const orb=$('#orb'); if(orb){ orb.classList.remove('thinking','speaking','listening'); if(['thinking','speaking','listening'].includes(s)) orb.classList.add(s); } }
function setStage(s){ setText('#g-stage-val', s||'idle'); }
function showStatus(s){ const line=$('#status-line'); if(!line) return; const t=line.querySelector('.sl-text'); if(t) t.textContent=s||''; line.classList.add('show'); }
function hideStatus(){ const line=$('#status-line'); if(line) line.classList.remove('show'); }
function updateModeButtons(){
  const map = [['#mode-converse',state.converseMode],['#mode-web',state.webMode],['#mode-peers',state.peersMode]];
  for(const [sel,on] of map){ const b=$(sel); if(b) b.classList.toggle('on', !!on); }
  const ps=$('#provider-select'); if(ps) ps.value = state.provider || 'ollama';
  // Update provider pills
  $$('.provider-pill').forEach(p=>{
    const on = p.dataset.provider === (state.provider||'ollama');
    p.classList.toggle('on', on);
    if(on && (p.dataset.provider==='ollama' || p.dataset.provider==='lmstudio' || p.dataset.provider==='huggingface')){
      const modelSpan = p.querySelector('.pill-model');
      if(modelSpan) modelSpan.textContent = state.selectedModel ? ' · '+state.selectedModel.slice(0,18) : '';
    }
  });
  // Update privacy bar
  $$('.privacy-btn').forEach(b => b.classList.toggle('on', b.dataset.mode === (state.privacyMode || 'balanced')));
}
window.setProviderPill = function setProviderPill(btn){
  const provider = btn.dataset.provider || 'ollama';
  const model = btn.dataset.model || '';
  state.provider = provider;
  if(provider === 'ollama'){
    state.selectedModel = chooseDefaultLocal(state.models);
  } else if(provider === 'lmstudio'){
    state.selectedModel = chooseDefaultLmstudio(state.models);
  } else {
    state.selectedModel = model;
  }
  persist(); updateModeButtons();
  toast('Provider: ' + provider + (state.selectedModel ? ' / ' + state.selectedModel : ''), 'info');
};

function setPrivacyMode(mode){
  state.privacyMode = mode || 'balanced';
  persist();
  updateModeButtons();
  toast('Privacy mode: ' + mode, 'info');
}
window.setPrivacyMode = setPrivacyMode;

function isCloudModelName(name){ return /claude|sonnet|haiku|opus|gpt-|gpt_|gemini|moonshot|kimi/i.test(String(name||'')); }
function isLocalModelName(name){ return /^(llama|qwen|mistral|codellama|phi|gemma|deepseek-r1|mixtral)/i.test(String(name||'')) || (String(name||'').includes(':') && !isCloudModelName(name)); }
function chooseDefaultLocal(data=state.models){
  const names=(data.installed||[]).filter(m=>!m.provider||m.provider==='ollama').map(m=>m.name);
  for(const x of ['qwen2.5:7b','llama3.2:latest','llama3.2','qwen2.5:14b','mistral-small:latest']) if(names.includes(x)) return x;
  return names[0] || data.default || 'llama3.2:latest';
}
function chooseDefaultLmstudio(data=state.models){
  const models=(data.installed||[]).filter(m=>m.provider==='lmstudio');
  const loaded=models.filter(m=>m.loaded);
  const pickFrom = loaded.length ? loaded : models;
  if(!pickFrom.length) return '';
  // Prefer the smallest (fastest to cold-load / already-loaded) model.
  return pickFrom.slice().sort((a,b)=>(a.size||Infinity)-(b.size||Infinity))[0].name;
}
function syncProviderModel(data=state.models){
  const installed=(data.installed||[]).map(m=>m.name);
  // If user has chosen Ollama, never keep a stale Claude/GPT/HF model in telemetry or payload.
  if((state.provider||'ollama') === 'ollama'){
    if(!state.selectedModel || isCloudModelName(state.selectedModel) || (state.selectedModel||'').includes('/') || (installed.length && !installed.includes(state.selectedModel) && !isLocalModelName(state.selectedModel))){
      state.selectedModel = chooseDefaultLocal(data);
    }
  } else if(state.provider === 'lmstudio'){
    const lmNames=(data.installed||[]).filter(m=>m.provider==='lmstudio').map(m=>m.name);
    if(!state.selectedModel || !lmNames.includes(state.selectedModel)){
      state.selectedModel = chooseDefaultLmstudio(data);
    }
  } else {
    // User explicitly chose a provider — keep it, but pick a model from that provider if none is set
    // or if the current selection belongs to a different provider (stale localStorage).
    const candidates=(data.installed||[]).concat(data.cloud||[]).filter(m=>m.provider===state.provider);
    const candidateNames = candidates.map(m=>m.name);
    if(!state.selectedModel || ((state.provider||'')!=='huggingface' && isLocalModelName(state.selectedModel)) || (state.provider!=='huggingface' && installed.includes(state.selectedModel)) || (candidateNames.length && !candidateNames.includes(state.selectedModel))){
      state.selectedModel = candidates.length ? candidates[0].name : '';
    }
  }
  persist(); updateModeButtons();
  setText('#t-model', (state.selectedModel || 'auto').slice(0,22)); setText('#t-route', state.provider || 'ollama');
}
async function forceLocalMode(model){
  state.provider='ollama'; state.selectedModel=model || chooseDefaultLocal(state.models); persist(); updateModeButtons();
  setText('#t-model', state.selectedModel.slice(0,22)); setText('#t-route','ollama');
  try{ await fetch('/system/reset-local',{method:'POST'}); }catch(e){}
  toast('Forced local Ollama mode: '+state.selectedModel);
}
window.forceLocalMode = forceLocalMode;

function clearEmpty(){ const e=$('.empty-state'); if(e) e.remove(); }
function pushBubble(role, text='', opts={}){
  clearEmpty();
  const t = $('#transcript');
  const b = document.createElement('div');
  b.className = 'bubble ' + (role==='user'?'user':'assistant') + (opts.side?' side':'');
  b.innerHTML = `<div class="avatar">${role==='user'?'YOU':'SHIMS'}</div><div class="bubble-main"><div class="content"></div><div class="meta"></div></div>`;
  t.appendChild(b);
  setBubble(b, text);
  t.scrollTop = t.scrollHeight;
  return b;
}
function setBubble(b, text){ const c=b && b.querySelector('.content'); if(c) c.innerHTML=md(text||''); }
function appendBubble(b, text){ const c=b && b.querySelector('.content'); if(c) c.innerHTML += md(text||''); }
function setBubbleMetaLegacy(b, meta){ const m=b && b.querySelector('.meta'); if(m) m.textContent = [meta.provider, meta.model, meta.route].filter(Boolean).join(' / '); }

function setBubbleMeta(b, meta){
  const m=b && b.querySelector('.meta'); if(!m) return;
  const parts = [meta.provider, meta.model, meta.route].filter(Boolean);
  if(meta.trust_level) parts.push('trust '+meta.trust_level);
  let badge = '';
  const provider = (meta.provider || '').toLowerCase();
  const route = (meta.route || '').toLowerCase();
  if(route.includes('privacy-guard')){
    badge = '<span class="provider-badge privacy" title="Sensitive data detected — forced local processing">🔒 Privacy Guard</span>';
  } else if(provider === 'ollama' || provider === 'local' || provider === 'lmstudio' || provider === 'huggingface'){
    badge = '<span class="provider-badge local" title="Data stays on this machine">🏠 Local</span>';
  } else if(provider && provider !== 'tool' && provider !== 'web'){
    badge = '<span class="provider-badge cloud" title="Data sent to cloud provider">☁️ ' + escapeHtml(provider.toUpperCase()) + '</span>';
  }
  m.innerHTML = escapeHtml(parts.join(' / ')) + (badge ? '<br>' + badge : '');
}

function renderMediaCard(result, bubble){
  if(!result) return;
  const target = bubble ? bubble.querySelector('.content') : $('#mf-gallery');
  if(!target) return;
  const kind = result.type || result.kind || 'file';
  const url = result.url || result.file_url || result.download_url;
  const card = document.createElement('div');
  card.className = 'media-card';
  let inner = `<div><b>${escapeHtml((kind||'file').toUpperCase())}</b> ${escapeHtml(result.title || result.filename || 'Generated file')}</div>`;
  if(url){
    if(kind === 'image') inner += `<a href="${url}" target="_blank"><img src="${url}" alt="generated image"></a>`;
    else if(kind === 'video') inner += `<video controls src="${url}"></video>`;
    else if(kind === 'audio') inner += `<audio controls src="${url}"></audio>`;
    else inner += `<p><a href="${url}" target="_blank">Open / download generated ${escapeHtml(kind)}</a></p>`;
  }
  if(result.note) inner += `<small>${escapeHtml(result.note)}</small>`;
  if(result.sha256) inner += `<small>SHA-256: ${escapeHtml(result.sha256.slice(0,18))}... · verified ledger</small>`;
  card.innerHTML = inner;
  target.appendChild(card);
}

renderMediaCard = function(result, bubble){
  if(!result) return;
  const target = bubble ? bubble.querySelector('.content') : $('#mf-gallery');
  if(!target) return;
  if(!bubble && target.classList && target.classList.contains('mf-gallery')){
    const empty = target.querySelector('.empty-pane');
    if(empty) empty.remove();
  }
  const rawKind = String(result.type || result.kind || 'file').toLowerCase();
  const kind = rawKind === 'photo' || rawKind === 'picture' ? 'image' : rawKind;
  const url = result.url || result.file_url || result.download_url || '';
  const safeUrl = escapeHtml(url);
  const provider = result.provider || result.backend || result.engine || result.model || 'auto';
  const fallback = result.fallback_reason || result.reason || result.note || '';
  const verified = result.verified === true || !!result.sha256 || !!result.ledger_id || !!result.trust;
  const status = result.ok === false ? 'failed' : (url ? 'ready' : (result.job_id ? 'queued' : 'created'));
  const title = result.title || result.filename || result.file || (kind + ' output');
  const chips = [
    'status: ' + status,
    'provider: ' + provider,
    verified ? 'ledger proof: yes' : 'ledger proof: pending'
  ];
  if(result.file_url) chips.push('file URL: yes');
  let media = '';
  if(url){
    if(kind === 'image') media = `<a href="${safeUrl}" target="_blank" rel="noopener"><img src="${safeUrl}" alt="generated image"></a>`;
    else if(kind === 'video') media = `<video controls playsinline src="${safeUrl}"></video>`;
    else if(kind === 'audio' || kind === 'podcast') media = `<audio controls src="${safeUrl}"></audio>`;
    else media = `<p><a class="mc-link" href="${safeUrl}" target="_blank" rel="noopener">Open generated ${escapeHtml(kind)}</a></p>`;
  } else {
    media = '<small>No file URL returned yet. If this is a queued job, check Recent Outputs.</small>';
  }
  const proof = [
    result.sha256 ? 'SHA-256: ' + String(result.sha256).slice(0, 24) + '...' : '',
    result.ledger_id ? 'Ledger: ' + result.ledger_id : '',
    fallback ? 'Fallback: ' + fallback : ''
  ].filter(Boolean).map(x => `<small>${escapeHtml(x)}</small>`).join('');
  const card = document.createElement('div');
  card.className = 'media-card';
  card.innerHTML =
    `<div class="mc-info"><span class="mc-kind">${escapeHtml(kind.toUpperCase())}</span><b>${escapeHtml(title)}</b></div>` +
    media +
    `<div class="mc-info">${chips.map(c=>`<small>${escapeHtml(c)}</small>`).join('')}</div>` +
    proof +
    (url ? `<p><a class="mc-link" href="${safeUrl}" target="_blank" rel="noopener">${escapeHtml(url)}</a></p>` : '');
  target.appendChild(card);
  const scroller = bubble ? $('#transcript') : target;
  if(scroller) scroller.scrollTop = scroller.scrollHeight;
};

function renderSearchCard(result, bubble){
  if(!result) return;
  const target = bubble ? bubble.querySelector('.content') : $('#event-feed');
  if(!target) return;
  const card=document.createElement('div');
  card.className='media-card search-card';
  const items=(result.results||[]).slice(0,6).map((r,i)=>`<div style="margin-top:8px"><b>${i+1}. ${escapeHtml(r.title||'Untitled')}</b><br><a href="${escapeHtml(r.url||'#')}" target="_blank">${escapeHtml(r.url||'')}</a><small>${escapeHtml(r.snippet||'')}</small></div>`).join('');
  card.innerHTML=`<div><b>WEB SEARCH</b> ${escapeHtml(result.query||'')}</div><small>provider: ${escapeHtml(result.provider||'none')}</small>${items || '<small>No results returned. Configure search provider in Settings.</small>'}`;
  target.appendChild(card);
}

function renderTrustCard(source, bubble){
  const trust = source && (source.trust || source);
  if(!trust || !trust.trust_level) return;
  const target = bubble ? bubble.querySelector('.content') : $('#event-feed');
  if(!target) return;
  const card=document.createElement('div');
  card.className='trust-card trust-'+String(trust.trust_level||'unverified').replace(/[^a-z-]/gi,'');
  const conf=trust.confidence||{};
  const evidence=(trust.evidence||[]).slice(0,5).map((e,i)=>`<li><b>${i+1}. ${escapeHtml(e.title||e.kind||'Evidence')}</b>${e.source_uri?` <a href="${escapeHtml(e.source_uri)}" target="_blank">source</a>`:''}<small>${escapeHtml(e.excerpt||'')}</small></li>`).join('');
  const missing=(trust.missing_evidence||[]).slice(0,3).map(x=>`<li>${escapeHtml(x)}</li>`).join('');
  const proof=trust.action_id ? `<div class="trust-proof"><button class="v9-btn" onclick="verifyAction('${escapeHtml(trust.action_id)}')">Verify action</button><span>${escapeHtml((trust.ledger_hash||'').slice(0,18))}</span></div>` : '';
  card.innerHTML=`<div class="trust-head"><b>${escapeHtml(String(trust.trust_level||'unverified').toUpperCase())}</b><span>${escapeHtml(String(conf.score ?? ''))}</span></div><small>${escapeHtml(conf.reason||trust.policy||'Verification metadata attached.')}</small>${evidence?`<ul class="evidence-list">${evidence}</ul>`:''}${missing?`<div class="missing-evidence"><b>Missing</b><ul>${missing}</ul></div>`:''}${proof}`;
  target.appendChild(card);
}
window.renderTrustCard=renderTrustCard;

function renderApprovalCard(source, bubble){
  const approval = source && (source.approval || source);
  if(!approval || !approval.approval_id) return;
  const target = bubble ? bubble.querySelector('.content') : $('#sandbox-sidebar');
  if(!target) return;
  const card=document.createElement('div');
  card.className='approval-card';
  const status=approval.status || 'pending';
  const payload=approval.payload || {};
  const preview=payload.relative_path || payload.proposal_id || payload.name || approval.action_type || '';
  card.innerHTML=`<div class="approval-head"><b>${escapeHtml(approval.title || 'Approval request')}</b><span>${escapeHtml(status)}</span></div><small>${escapeHtml(approval.summary || approval.yes_no_prompt || '')}</small>${preview?`<code>${escapeHtml(preview)}</code>`:''}<div class="approval-actions"></div>`;
  const actions=card.querySelector('.approval-actions');
  const yes=document.createElement('button'); yes.className='v9-btn ok'; yes.textContent='Yes';
  const no=document.createElement('button'); no.className='v9-btn no'; no.textContent='No';
  yes.onclick=()=>decideApproval(approval.approval_id, true);
  no.onclick=()=>decideApproval(approval.approval_id, false);
  if(actions){ actions.appendChild(yes); actions.appendChild(no); }
  target.appendChild(card);
}
window.renderApprovalCard=renderApprovalCard;

async function decideApproval(approvalId, decision){
  if(!approvalId) return toast('Missing approval id','warn');
  try{
    const d=await (await fetch('/approvals/decide',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({approval_id:approvalId,decision:!!decision,approved_by:'ui-human'})})).json();
    toast(d.ok ? (decision ? 'Approved and executed' : 'Cancelled') : ('Approval failed: '+(d.message||d.status)), d.ok?'info':'err');
    if(d.result && d.result.app_url) window.open(d.result.app_url, '_blank');
    await loadSandboxSidebar();
    if($('#self-body')) loadSelfPane();
  }catch(e){ toast('Approval failed: '+e.message,'err'); }
}
window.decideApproval=decideApproval;

async function loadSandboxSidebar(){
  const box=$('#sandbox-sidebar'); if(!box) return;
  try{
    const d=await (await fetch('/coder/playground/status')).json();
    const pending=(d.pending||[]).slice(0,3);
    const apps=(d.apps||[]).slice(0,3);
    const proposals=(d.proposals||[]).slice(0,3);
    let html=`<div class="sandbox-mini-actions"><button class="v9-btn" onclick="openPane('self')">Patches</button></div>`;
    html+='<div class="sandbox-section">Pending</div>';
    html+=pending.length ? pending.map(p=>`<div class="sandbox-item"><b>${escapeHtml(p.title||p.action_type)}</b><small>${escapeHtml(p.summary||'')}</small><div class="sandbox-actions"><button onclick="decideApproval('${escapeHtml(p.approval_id)}',true)">Yes</button><button onclick="decideApproval('${escapeHtml(p.approval_id)}',false)">No</button></div></div>`).join('') : '<div class="empty-pane compact">No pending approvals.</div>';
    html+='<div class="sandbox-section">Apps</div>';
    html+=apps.length ? apps.map(a=>`<div class="sandbox-item"><b>${escapeHtml(a.name)}</b><small>${escapeHtml(a.relative_path||'')}</small><a href="${escapeHtml(a.url)}" target="_blank">Open</a></div>`).join('') : '<div class="empty-pane compact">No generated apps yet.</div>';
    html+='<div class="sandbox-section">Patch Queue</div>';
    html+=proposals.length ? proposals.map(p=>`<div class="sandbox-item"><b>${escapeHtml(p.status)} · ${escapeHtml(p.relative_path)}</b><small>${escapeHtml((p.proposal_id||p.id||'').slice(0,28))}</small></div>`).join('') : '<div class="empty-pane compact">No proposals.</div>';
    box.innerHTML=html;
  }catch(e){ box.innerHTML='<div class="empty-pane compact">Sandbox unavailable.</div>'; }
}
window.loadSandboxSidebar=loadSandboxSidebar;

async function streamNDJSON(endpoint, body, onChunk){
  if(state.abort){try{state.abort.abort();}catch(e){}}
  const ctrl = new AbortController(); state.abort = ctrl;
  const IDLE_TIMEOUT_MS = 10 * 60 * 1000; // 10 min idle window for slow local model loads
  let idleTimedOut = false;
  let timer = null;
  const bumpTimer = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      idleTimedOut = true;
      try{ ctrl.abort(); }catch(e){}
    }, IDLE_TIMEOUT_MS);
  };
  try{
    bumpTimer();
    const resp = await fetch(API + endpoint, {method:'POST', headers:{'Content-Type':'application/json', ...auth()}, body:JSON.stringify(body), signal:ctrl.signal});
    if(!resp.ok){ throw new Error('HTTP '+resp.status+': '+(await resp.text()).slice(0,300)); }
    if(!resp.body){ throw new Error('Streaming response body was empty'); }
    const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf='';
    while(true){
      let done, value;
      try{
        ({done,value} = await reader.read());
      }catch(e){
        if(idleTimedOut) throw new Error('Stream idle for '+Math.round(IDLE_TIMEOUT_MS/1000)+'s — the model may be overloaded or unreachable. Try a faster model or retry.');
        throw e;
      }
      if(done) break;
      bumpTimer();
      buf += dec.decode(value, {stream:true});
      let lines = buf.split(/\r?\n/); buf = lines.pop() || '';
      for(const line of lines){ if(!line.trim()) continue; try{ onChunk(JSON.parse(line)); }catch(e){ console.warn('bad ndjson', line); } }
    }
    if(buf.trim()){ try{ onChunk(JSON.parse(buf)); }catch(e){} }
  }finally{
    clearTimeout(timer);
    state.abort = null;
  }
}

async function sendText(text, source='typed'){
  text = String(text || '').trim();
  if(state.pendingInject){
    text = (state.pendingInject + '\n\n' + text).trim();
    state.pendingInject = null;
  }
  if(!text || state.streaming) return;
  const now = Date.now();
  const lastText = source === 'voice' ? state.lastVoiceSentText : state.lastTypedSentText;
  const lastAt = source === 'voice' ? state.lastVoiceSentAt : state.lastTypedSentAt;
  if(text.toLowerCase() === String(lastText||'').toLowerCase() && now - lastAt < 4500){ feed('duplicate turn blocked: '+text.slice(0,40), 'warn'); return; }
  if(source === 'voice'){ state.lastVoiceSentText = text; state.lastVoiceSentAt = now; } else { state.lastTypedSentText = text; state.lastTypedSentAt = now; }

  state.streaming = true; persist();
  const input=$('#input'); if(input) input.value='';
  const sendBtn=$('#send-btn'); if(sendBtn) sendBtn.disabled = true;
  setOrbState('thinking'); setStage('brain'); hideStatus(); stopSpeaking(false);
  pushBubble('user', text);
  const bubble = pushBubble('assistant', '');
  bubble.classList.add('thinking');
  clearThoughts();
  startThinkingBlock(bubble);   // inline collapsible "Reasoning" above this answer
  let answer = ''; let mediaResult = null; let lastTrust = null; let meta = {provider:state.provider, model:state.selectedModel, route:'brain'}; const started=performance.now(); let toolCards = {};
  saveCrashContext(text);
  try{
    const payload = {
      message:text,
      session_id:state.sessionId,
      provider:state.provider || 'ollama',
      model:state.selectedModel || undefined,
      conversation_mode:state.converseMode,
      web_mode:state.webMode,
      auto_peer_consultation:state.peersMode,
      privacy_mode:state.privacyMode || 'balanced',
      locale:state.voiceLang || 'en-IN',
      agent_mode:true,
      source,
      realtime: source === 'voice',
      max_tokens: source === 'voice' ? (Number(localStorage.shimsVoiceMaxTokens || 220) || 220) : undefined
    };
    if(source === 'voice' && state.pendingVoiceCorrectionId){
      payload.voice_correction_id = state.pendingVoiceCorrectionId;
    }
    await streamNDJSON('/brain/turn', payload, ch => {
      if(ch.type !== 'status') hideStatus();
      if(ch.type === 'meta'){
        state.sessionId = ch.session_id || state.sessionId; persist();
        if(ch.trust) lastTrust = ch.trust;
        meta = {provider:ch.provider, model:ch.model, route:ch.route, trust_level: ch.trust && ch.trust.trust_level};
        setText('#t-model', (ch.model || 'auto').slice(0,22)); setText('#t-route', (ch.route || '—').slice(0,18)); setBubbleMeta(bubble, meta);
        setText('#tb-model', (ch.model || 'auto').slice(0,20));
      } else if(ch.type === 'thought'){
        addThoughtLine(ch.stage, ch.content);
      } else if(ch.type === 'status'){
        showStatus(ch.content || ch.status || 'Working...'); setText('#g-inf-val', (ch.content || 'working').slice(0,18)); setStage('working');
        addThoughtLine(ch.stage || 'status', ch.content || ch.status || 'Working...');
      } else if(ch.type === 'token'){
        if(!answer) collapseThinkingBlock();   // answer started → fold reasoning
        answer += ch.content || ''; setBubble(bubble, answer); setText('#t-tokens', String(Math.ceil(answer.length/4))); setOrbState('speaking');
      } else if(ch.type === 'media'){
        mediaResult = ch.media_result; if(ch.trust) lastTrust = ch.trust; renderMediaCard(mediaResult, bubble);
      } else if(ch.type === 'search'){
        if(ch.trust) lastTrust = ch.trust; renderSearchCard(ch.search_result, bubble);
      } else if(ch.type === 'plan'){
        if(window.renderPlanGraph) renderPlanGraph(ch.steps || [], bubble);
      } else if(ch.type === 'tool_call'){
        const key=(ch.tool||'tool')+'#'+(ch.step||0)+'.'+(ch.index||0);
        toolCards[key] = (window.renderToolCard ? renderToolCard(ch, bubble) : null);
      } else if(ch.type === 'tool_result'){
        const key=(ch.tool||'tool')+'#'+(ch.step||0)+'.'+(ch.index||0);
        if(window.renderToolResult) renderToolResult(toolCards[key], ch, bubble);
      } else if(ch.type === 'job'){
        if(window.renderJobCard) renderJobCard(ch.job, bubble);
      } else if(ch.type === 'background_task'){
        const task = ch.task || {};
        pushBubble('assistant', '🔄 **Background task started**\n- **'+escapeHtml(task.title||'Task')+'**\n- Type: '+escapeHtml(task.task_type||'')+'\n- ID: `'+task.task_id+'`\n- Status: '+escapeHtml(task.status||'queued')+'\n\nYou can continue chatting. Ask me "how is task '+task.task_id+' going?" anytime.');
        loadBackgroundTasks();
      } else if(ch.type === 'patch_proposal'){
        if(window.renderDiffCard) renderDiffCard(ch, bubble);
      } else if(ch.type === 'approval_request'){
        if(ch.trust) lastTrust = ch.trust;
        renderApprovalCard(ch, bubble);
        loadSandboxSidebar();
      } else if(ch.type === 'approval'){
        if(ch.trust) lastTrust = ch.trust;
        renderApprovalCard(ch, bubble);
        loadSandboxSidebar();
      } else if(ch.type === 'ignored'){
        bubble.remove(); feed('ignored duplicate voice phrase', 'warn');
      } else if(ch.type === 'error'){
        const emsg = ch.message || 'The AI engine failed.';
        addThoughtLine('agent', emsg);
        if(!answer.trim()) setBubble(bubble, '⚠ ' + emsg);
        if(ch.retryable !== false) showCrashRecovery(emsg);
        feed(emsg, 'err');
        refreshOmniAiHealth();
      } else if(ch.type === 'done'){
        if(ch.trust) lastTrust = ch.trust;
        if(ch.media_result && !mediaResult){ mediaResult = ch.media_result; renderMediaCard(mediaResult, bubble); }
        if(ch.approval) loadSandboxSidebar();
        meta.route = ch.route || meta.route; meta.trust_level = lastTrust && lastTrust.trust_level; setBubbleMeta(bubble, meta);
        if(lastTrust) renderTrustCard(lastTrust, bubble);
        if(answer.trim()) addOmniFeedback(bubble, text, answer);
      }
    });
    const latency = Math.round(performance.now()-started); $('#t-latency') && ($('#t-latency').innerHTML = latency+'<span class="unit">ms</span>'); setText('#tb-latency', latency+'ms');
    if(answer && (state.voiceOn || source === 'voice')) await speakText(answer.replace(/https?:\/\/\S+/g,''));
  }catch(e){
    const raw = e && e.message ? e.message : String(e || 'Unknown stream error');
    const aborted = /BodyStreamBuffer|abort|aborted|AbortError/i.test(raw);
    const msg = aborted
      ? 'Connection dropped while streaming. I kept any partial reply above; retry or pick a faster installed model in Settings.'
      : 'Connection error: '+raw;
    if(answer.trim()){
      setBubble(bubble, answer + '\n\n[' + msg + ']');
    }else{
      setBubble(bubble, msg);
    }
    // Always show retry card on stream errors
    showCrashRecovery(msg);
    feed(msg, 'err'); toast(msg, 'err');
    if(source === 'voice' || state.voiceOn){
      try{ await speakText('I heard you, but my brain connection failed. Please try again.'); }catch(_e){}
    }
  }finally{
    bubble.classList.remove('thinking'); state.streaming=false; if(sendBtn) sendBtn.disabled=false; hideStatus(); setOrbState(state.voiceOn?'listening':'standby'); setStage('idle');
    setThinkStatus('Idle'); setThinkDot(null);
    finishThinkingBlock();
    loadSessionsPane();
  }
}
window.sendText = sendText;
window.shimsAbort = function(){ try{ if(state.abort) state.abort.abort(); }catch(e){} try{ state.streaming=false; }catch(e){} stopSpeaking(true); finishSpeaking(); };

/* ==================== FEEDBACK + AI HEALTH (learning loop) ==================== */
function addOmniFeedback(bubble, question, answer){
  if(!bubble || bubble.querySelector('.om-fb')) return;
  const bar = document.createElement('div');
  bar.className = 'om-fb';
  bar.style.cssText = 'display:flex;gap:6px;align-items:center;margin-top:6px;flex-wrap:wrap;';
  bar.innerHTML = '<button class="om-fb-up" title="Good answer" style="border:1px solid var(--border,#334);background:transparent;border-radius:6px;padding:2px 8px;cursor:pointer;opacity:.6;">👍</button>'+
                  '<button class="om-fb-down" title="Bad answer" style="border:1px solid var(--border,#334);background:transparent;border-radius:6px;padding:2px 8px;cursor:pointer;opacity:.6;">👎</button>'+
                  '<span class="om-fb-note" style="font-size:11px;opacity:.7;"></span>';
  const note = bar.querySelector('.om-fb-note');
  async function send(rating, comment){
    try{
      await fetch('/api/feedback', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({rating, message:(question||'').slice(0,300), answer:(answer||'').slice(0,300), comment:comment||''})});
      bar.querySelectorAll('button').forEach(b=>b.disabled=true);
      note.textContent = rating > 0 ? "Thanks — I'll remember what works." : "Noted — I'll avoid this next time.";
    }catch(e){ note.textContent = 'Could not save feedback.'; }
  }
  bar.querySelector('.om-fb-up').onclick = ()=>send(1,'');
  bar.querySelector('.om-fb-down').onclick = ()=>{
    if(bar.querySelector('input')) return;
    const inp = document.createElement('input');
    inp.placeholder = 'What was wrong? (optional, Enter to send)';
    inp.style.cssText = 'flex-basis:100%;border:1px solid var(--border,#334);border-radius:6px;padding:4px 8px;font-size:12px;background:transparent;color:inherit;';
    inp.addEventListener('keydown', e=>{ if(e.key==='Enter') send(-1, inp.value.trim()); });
    bar.appendChild(inp); inp.focus();
  };
  bubble.appendChild(bar);
}

async function refreshOmniAiHealth(){
  try{
    const h = await (await fetch('/api/ai/health')).json();
    let dot = document.getElementById('ai-health-dot');
    if(!dot){
      const anchor = document.querySelector('#t-model');
      if(!anchor || !anchor.parentElement) return;
      dot = document.createElement('span');
      dot.id = 'ai-health-dot';
      dot.style.cssText = 'display:inline-block;width:8px;height:8px;border-radius:50%;margin-left:6px;vertical-align:middle;';
      anchor.parentElement.appendChild(dot);
    }
    const ollama = (h.providers||{}).ollama || {};
    dot.style.background = h.ok ? '#059669' : '#dc2626';
    dot.title = h.ok
      ? ('AI online — Ollama: ' + (ollama.ok ? (ollama.models + ' models') : 'down, cloud fallback available'))
      : 'AI offline — Ollama unreachable and no cloud provider configured';
  }catch(e){}
}
document.addEventListener('DOMContentLoaded', ()=>{ refreshOmniAiHealth(); setInterval(refreshOmniAiHealth, 120000); });

/* ==================== THINKING SIDEBOX ==================== */
let thoughtQueue = [];
let autoExpandedThinking = false;
function clearThoughts(){
  thoughtQueue = [];
  autoExpandedThinking = false;
  const box = $('#think-lines'); if(box) box.innerHTML = '';
  setThinkStatus('Thinking…');
  setThinkDot(null);
}
function setThinkStatus(s){
  const el = $('#think-status'); if(el) el.textContent = s || 'Idle';
}
function setThinkDot(stage){
  ['plan','context','tool','generate'].forEach(s => {
    const d = $('#think-dot-'+s); if(d) d.classList.toggle('on', s === stage);
    const cd = $('#rp-dot-'+s); if(cd) cd.classList.toggle('on', s === stage);
  });
}
/* ==================== INLINE THINKING (Claude-Code style) ====================
   Each assistant turn gets a collapsible "Reasoning" accordion rendered as a
   SIBLING just above its answer bubble (setBubble() replaces the bubble's
   .content on every token, so thinking cannot live inside it). */
let _activeThinking = null;     // the current turn's .turn-thinking element
let _thinkingCount = 0;

function startThinkingBlock(beforeBubble){
  const t = $('#transcript'); if(!t) return null;
  const wrap = document.createElement('div');
  wrap.className = 'turn-thinking live';
  wrap.innerHTML =
    '<div class="tt-head"><span class="tt-chevron">▾</span><span class="tt-spark"></span>'+
    '<span class="tt-title">Reasoning</span><span class="tt-count"></span></div>'+
    '<div class="tt-body"></div>';
  wrap.querySelector('.tt-head').onclick = ()=>wrap.classList.toggle('collapsed');
  if(beforeBubble && beforeBubble.parentNode === t) t.insertBefore(wrap, beforeBubble);
  else t.appendChild(wrap);
  _activeThinking = wrap; _thinkingCount = 0;
  return wrap;
}
function collapseThinkingBlock(){
  if(_activeThinking){ _activeThinking.classList.add('collapsed'); _activeThinking.classList.remove('live'); }
}
function finishThinkingBlock(){
  if(_activeThinking){
    _activeThinking.classList.remove('live');
    if(_thinkingCount === 0) _activeThinking.remove();   // no reasoning → drop the empty block
  }
  _activeThinking = null;
}
function addThoughtLine(stage, content){
  if(!stage || !content) return;
  setThinkDot(stage);
  if(!_activeThinking) startThinkingBlock(null);
  const body = _activeThinking && _activeThinking.querySelector('.tt-body');
  if(!body) return;
  const stageLabel = ({plan:'PLAN',conversation:'CONV',context:'CTX',tool:'TOOL',generate:'GEN',status:'·',agent:'AGENT'})[stage] || String(stage).toUpperCase();
  const line = document.createElement('div');
  line.className = 'tt-line' + (stage==='status'?' tt-status':'');
  line.innerHTML = `<span class="tt-stage">${escapeHtml(stageLabel)}</span><span class="tt-text">${escapeHtml(content)}</span>`;
  body.appendChild(line);
  _thinkingCount++;
  const cnt = _activeThinking.querySelector('.tt-count'); if(cnt) cnt.textContent = _thinkingCount + (_thinkingCount===1?' step':' steps');
  body.scrollTop = body.scrollHeight;
  const t = $('#transcript'); if(t) t.scrollTop = t.scrollHeight;
}
function stopThinking(){
  if(state.abort){ try{ state.abort.abort(); }catch(e){} }
  state.streaming = false;
  stopSpeaking(true); // also stop any TTS/audio playback
  const sendBtn = $('#send-btn'); if(sendBtn) sendBtn.disabled = false;
  setThinkStatus('Stopped by user');
  setOrbState(state.voiceOn?'listening':'standby');
  feed('User stopped thinking stream','warn');
}
window.stopThinking = stopThinking;

/* ==================== CRASH RECOVERY ==================== */
function saveCrashContext(text){
  state.crashContext = {
    text: text,
    provider: state.provider,
    model: state.selectedModel,
    timestamp: Date.now(),
    sessionId: state.sessionId
  };
}
function showCrashRecovery(errorMsg){
  const box = $('#transcript'); if(!box) return;
  const div = document.createElement('div');
  div.className = 'bubble assistant';
  div.innerHTML = '<div class="content" style="color:var(--amber)">⚠️ <b>Something went wrong</b><br>'+escapeHtml(errorMsg.slice(0,300))+'</div>'+
    '<div style="margin-top:8px;display:flex;gap:8px">'+
    '<button class="mode-pill" onclick="retryLastMessage()" style="border-color:var(--cyan);color:var(--cyan)">🔄 Try Again</button>'+
    '<button class="mode-pill" onclick="dismissCrashRecovery(this)" style="border-color:var(--line);color:var(--text-dim)">Dismiss</button>'+
    '</div>';
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}
window.retryLastMessage = function retryLastMessage(){
  if(!state.crashContext){ toast('Nothing to retry','warn'); return; }
  const ctx = state.crashContext;
  state.provider = ctx.provider || state.provider;
  state.selectedModel = ctx.model || state.selectedModel;
  if(ctx.sessionId) state.sessionId = ctx.sessionId;
  updateModeButtons();
  toast('Retrying last message...','info');
  sendText(ctx.text);
};
window.dismissCrashRecovery = function dismissCrashRecovery(btn){
  const bubble = btn && btn.closest('.bubble');
  if(bubble) bubble.remove();
};
function injectThinking(){
  const input = $('#think-inject-input');
  const text = input ? String(input.value||'').trim() : '';
  if(!text) return;
  addThoughtLine('inject', 'User injected: ' + text.slice(0,120));
  // Store as pending follow-up message
  state.pendingInject = text;
  if(input) input.value = '';
  toast('Context injected. Will append to next turn.','info');
}
window.injectThinking = injectThinking;
function send(){
  const input=$('#input'); const text = input ? String(input.value||'').trim() : '';
  // Slash commands route straight to tools — the chat absorbs every module's power.
  if(text.startsWith('/')){ if(input) input.value=''; pushBubble('user', text); handleSlashCommand(text); return; }
  // Natural-language routing for the cowork tools the backend LLM doesn't own
  // (chem/coder/files/ocr). Media + general chat still flow to /brain/turn.
  const nl = text ? nlRouteTool(text) : null;
  if(nl){ if(input) input.value=''; pushBubble('user', text); handleSlashCommand(nl); return; }
  sendText(text, 'typed');
}
window.send = send;

// Conservative NL → tool router. Returns a slash command or null (null = let the
// normal LLM turn handle it). Only fires on high-confidence phrasings.
function nlRouteTool(text){
  const t = (text||'').toLowerCase();
  // Chemistry: explicit SMILES / molecule check.
  if(/\bsmiles\b/.test(t) || (/\bmolecul|compound\b/.test(t) && /\b(valid|check|verify|hazard|canonical)\b/.test(t))){
    const tok = (text.match(/[A-Za-z0-9@+\-\[\]()=#%.\/\\]{2,}/g)||[])
      .filter(s=>/[()=#\[\]1-9]/.test(s) && /[CNOPSFIBcnops]/.test(s))
      .sort((a,b)=>b.length-a.length)[0];
    if(tok) return '/chem '+tok;
  }
  // Files: organize / summarize the workspace.
  if(/\b(organi[sz]e|tidy|declutter|clean up|sort)\b.*\b(file|folder|download|document|workspace)\b/.test(t)) return '/files';
  if(/\b(what'?s in|summari[sz]e|list|show)\b.*\b(workspace|my files|my folder|downloads)\b/.test(t)) return '/files';
  // OCR: read text out of an image (opens the picker).
  if(/\bocr\b/.test(t) || (/\b(extract|read|get)\b.*\btext\b/.test(t) && /\b(image|screenshot|photo|picture|scan)\b/.test(t))) return '/ocr';
  return null;
}
window.nlRouteTool = nlRouteTool;

/* ============ CHAT-AS-EVERYTHING: slash commands + tool dispatch ============ */
const SLASH_HELP = [
  ['/image <prompt>', 'Generate an image'],
  ['/doc <prompt>', 'Generate a PDF document'],
  ['/ppt <prompt>', 'Generate a slide deck'],
  ['/audio <prompt>', 'Generate audio/narration'],
  ['/video <prompt>', 'Generate a video'],
  ['/create-project <name>', 'Create a new coding project'],
  ['/read-file <project> <path>', 'Read a file from a project'],
  ['/write-file <project> <path> <content>', 'Write a file in a project'],
  ['/run-shell <project> <command>', 'Run shell command in a project'],
  ['/run-project <project> [entry]', 'Run a coding project'],
  ['/search <project> <query>', 'Search code in a project'],
  ['/install <project> <package>', 'Install a package in a project'],
  ['/git-commit <project> [message]', 'Git commit a project'],
  ['/shell <command>', 'Run a shell / CMD / PowerShell command'],
  ['/python <code>', 'Run Python in a sandbox'],
  ['/propose <intent>', 'Generate a self-evolution patch proposal'],
  ['/test-proposal <id>', 'Test a proposal in sandbox'],
  ['/apply-proposal <id>', 'Apply a proposal to the codebase'],
  ['/proposals', 'List all pending proposals'],
  ['/reflect', 'Run AI reflection — generate improvement proposals'],
  ['/neural', 'Open Neural Agent dashboard'],
  ['/neural-status', 'Show neural agent model status'],
  ['/tasks [status]', 'List background tasks (queued/running/done/failed)'],
  ['/auto-evolve', 'Toggle automatic self-evolution (reflect + propose)'],
  ['/browser visit <url>', 'Visit a page with headless browser'],
  ['/browser search <query>', 'Search the web via DuckDuckGo'],
  ['/browser click <url> <text>', 'Click a link on a page'],
  ['/browser extract <url> <selector>', 'Extract data with CSS selector'],
  ['/browser screenshot <url>', 'Take a screenshot of a page'],
  ['/browser scroll <url> [direction]', 'Scroll a page up/down/bottom'],
  ['/swarm <task>', 'Run the meta-orchestrator swarm: analyze, plan, code, review, test, synthesize'],
  ['/self-index', 'Index the SHIMS source tree into the omni-brain'],
  ['/chem <SMILES>', 'Validate a molecule + hazards'],
  ['/chemdfm <query>', 'Ask ChemDFM chemistry model'],
  ['/mail [action]', 'Gmail: summarize, organize, or check status'],
  ['/enterprise <command>', 'Send command to SHIMS Enterprise bridge'],
  ['/bridge <command>', 'Run a shell command on the Desktop Bridge'],
  ['/plans [status]', 'List active plans (running/queued/done)'],
  ['/files [query]', 'Search / summarize your workspace'],
  ['/remember <note>', 'Save something to long-term memory'],
  ['/learned', 'Show what Shims has learned recently (skills, feedback, self-improvement)'],
  ['/self-check [tests|lint|file]', 'Inspect SHIMS code and create a patch proposal'],
  ['/council <task>', 'Open Council of the Wise on a task'],
  ['/agent', 'Show background agent activity'],
  ['/help', 'List commands'],
];
function toolBubble(title){ const b=pushBubble('assistant',''); setBubble(b, '**'+title+'**\n\n_working…_'); return b; }
function toolResult(b, md){ setBubble(b, md); }
async function postJSON(url, body){ const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}); return {status:r.status, data: await r.json().catch(()=>({}))}; }

async function handleSlashCommand(text){
  const sp = text.indexOf(' ');
  const cmd = (sp<0 ? text : text.slice(0,sp)).toLowerCase();
  const arg = (sp<0 ? '' : text.slice(sp+1)).trim();
  try{
    if(cmd==='/help'){ pushBubble('assistant', '### Chat commands\n'+SLASH_HELP.map(c=>'`'+c[0]+'` — '+c[1]).join('\n')+'\n\nYou can also just ask in plain language.'); return; }
    if(cmd==='/agent'){ const b=toolBubble('Agent activity'); toolResult(b, await agentActivityMarkdown()); return; }
    if(cmd==='/self-check'){
      const scope=(arg||'tests').split(' ')[0].toLowerCase();
      const b=toolBubble('Self-Check');
      const body={scope};
      if(scope==='file'){
        const parts=(arg||'').split(' ').slice(1);
        body.relative_path=parts[0]||'';
        body.goal=parts.slice(1).join(' ')||'Review and improve this file';
      }
      const d=await (await fetch('/evolution/self-check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
      if(d.ok && d.proposal && d.proposal.proposal_id){
        toolResult(b, '**Self-check created proposal** `'+escapeHtml(d.proposal.proposal_id)+'`\n\n'+(d.message||'')+'\n\nReview it in Self-Upgrade & Approvals.');
      }else if(d.ok){
        toolResult(b, '**Self-check result**\n\n'+(d.message||'No patch needed.'));
      }else{
        toolResult(b, '❌ Self-check failed: '+(d.error||d.message||JSON.stringify(d)));
      }
      return;
    }
    if(cmd==='/council'){
      const url='/omni-duobot?mode=council'+(arg?'&topic='+encodeURIComponent(arg):'');
      window.open(url,'_blank');
      pushBubble('assistant','Opened Council of the Wise in a new tab.');
      return;
    }
    if(cmd==='/image'||cmd==='/doc'||cmd==='/ppt'||cmd==='/audio'||cmd==='/video'){
      if(!arg){ pushBubble('assistant','Add a prompt, e.g. `'+cmd+' a blue logo`'); return; }
      const kind = cmd==='/doc'?'pdf':cmd.slice(1);
      const b=toolBubble(kind.toUpperCase()+' generation');
      const {data}=await postJSON('/media/generate',{kind, prompt:arg, type:kind, privacy_mode: state.privacyMode || 'balanced'});
      if(data && (data.url||data.file_url||data.ok!==false)){ setBubble(b,'Generated '+kind+':'); renderMediaCard(data, b); } else { toolResult(b,'Generation failed: '+JSON.stringify(data)); }
      return;
    }
    if(cmd==='/chem'){
      if(!arg){ pushBubble('assistant','Provide a SMILES, e.g. `/chem CCO`'); return; }
      const b=toolBubble('Chemistry verify'); const {data}=await postJSON('/chem/verify',{smiles:arg});
      const rep=(data.smiles&&data.smiles.data&&data.smiles.data.report)||{};
      toolResult(b, '**Chem · '+escapeHtml(arg)+'**\n\n- valid: '+(rep.valid!==undefined?rep.valid:'?')+'\n- canonical: `'+(rep.canonical_smiles||'—')+'`\n- formula: '+(rep.formula||'—')+'\n- MW: '+(rep.mol_weight||rep.molecular_weight||'—'));
      return;
    }
    if(cmd==='/swarm'){
      if(!arg){ pushBubble('assistant','Usage: `/swarm <task>` e.g. `/swarm design a Python logging utility`'); return; }
      const b=toolBubble('Swarm');
      // Phase 2+ — use the real meta-orchestrator. Set orchestrate=true, use_llm=true.
      const {data}=await postJSON('/agent/swarm',{prompt:arg, use_llm:true, orchestrate:true});
      if(!data.ok){ toolResult(b, 'Failed: '+JSON.stringify(data)); return; }
      let md = '**Swarm** — '+escapeHtml(arg)+'\n\n'+escapeHtml(data.synthesis||'(no synthesis)').replace(/\n/g,'<br>');
      const events = data.events || [];
      if(events.length){
        md += '\n\n<details><summary>Agent activity log ('+events.length+' events)</summary>\n\n';
        md += events.map(e => '- `'+escapeHtml(e.stage)+'` **'+escapeHtml(e.agent_id)+'**: '+escapeHtml(e.message)).join('\n');
        md += '\n\n</details>';
      }
      const agents = data.agents || (data.results || []);
      if(agents.length){
        md += '\n\n_Agents:_ '+agents.map(a => '`'+escapeHtml(a.agent_id||a.role||'?')+'`').join(' ');
      }
      toolResult(b, md);
      return;
    }
    if(cmd==='/self-index'){
      const b=toolBubble('Self-Index');
      const {data}=await postJSON('/api/brain/self-index?force='+(arg === 'force'));
      toolResult(b, data.ok ? '**Self-Index** ✓\n- files indexed: '+(data.files_indexed||0)+'\n- chunks indexed: '+(data.chunks_indexed||0)+'\n- elapsed: '+(data.elapsed_s||0)+'s'+(data.skipped ? '\n_skipped: '+escapeHtml(data.reason||'')+'_' : '') : 'Failed: '+JSON.stringify(data));
      return;
    }
    if(cmd==='/files'){
      const b=toolBubble('Files');
      if(arg){ const {data}=await postJSON('/files/search',{query:arg}); toolResult(b,'**Search "'+escapeHtml(arg)+'"**\n\nNames: '+((data.name_matches||[]).join(', ')||'—')+'\n\nIn content: '+((data.content_matches||[]).join(', ')||'—')); }
      else { const d=await (await fetch('/files/summary')).json(); toolResult(b,'**Workspace**: '+(d.files||0)+' files, '+(d.total_mb||0)+' MB\n\n'+Object.entries(d.by_category||{}).map(([k,v])=>'- '+k+': '+v).join('\n')); }
      return;
    }
    if(cmd==='/remember'){
      if(!arg){ pushBubble('assistant','What should I remember? `/remember <note>`'); return; }
      await postJSON('/memory/save',{namespace:'user', key:'note', value:arg, tags:['chat'], pinned:false});
      pushBubble('assistant','Saved to memory ✓'); return;
    }
    if(cmd==='/learned'){
      const b=toolBubble('What Shims has learned');
      try{
        const d = await (await fetch('/api/learning/recent?limit=10')).json();
        const fc = d.feedback_counts || {};
        let md = '### What I\'ve been learning\n';
        md += '- Autonomous self-improvement: '+(d.autonomous_improvement_enabled?'**on**':'off')+' · background learning: '+(d.background_learning_enabled?'**on**':'off')+'\n';
        md += '- Feedback signals: 👍 '+(fc.preferences||0)+' preferences · 👎 '+(fc.anti_patterns||0)+' things to avoid\n\n';
        if((d.skills||[]).length){
          md += '**Recent skills**\n'+d.skills.slice(0,8).map(s=>'- **'+escapeHtml(s.name||'skill')+'** — '+escapeHtml((s.summary||'').slice(0,120))+(s.source?' _('+s.source+')_':'')).join('\n')+'\n\n';
        }
        if((d.improvement_runs||[]).length){
          md += '**Self-improvement runs**\n'+d.improvement_runs.slice(0,3).map(r=>'- '+(r.run_id||'run')+': '+((r.proposals||[]).length)+' proposals, '+((r.reflection||{}).failed_cases ?? '?')+' eval failures').join('\n');
        }
        if(!(d.skills||[]).length && !(d.improvement_runs||[]).length){
          md += '_No skills distilled yet. Use 👍/👎 on my answers — I turn that into preferences and skills automatically._';
        }
        toolResult(b, md);
      }catch(e){ toolResult(b, 'Could not load learning status: '+(e.message||e)); }
      return;
    }
    if(cmd==='/ocr'){ pickChatFile('ocr'); return; }
    if(cmd==='/file'){ pickChatFile('ingest'); return; }
    if(cmd==='/create-project'){
      if(!arg){ pushBubble('assistant','Project name required, e.g. `/create-project beam_calculator`'); return; }
      const b=toolBubble('Create project');
      const {data}=await postJSON('/coder/v2/project',{name:arg});
      toolResult(b, data.ok ? '**Project created**\n- id: `'+data.project_id+'`\n- name: '+escapeHtml(data.name) : 'Failed: '+JSON.stringify(data));
      return;
    }
    if(cmd==='/read-file'){
      const parts=arg.split(' '); const proj=parts[0], fpath=parts.slice(1).join(' ');
      if(!proj||!fpath){ pushBubble('assistant','Usage: `/read-file <project_id> <file_path>`'); return; }
      const b=toolBubble('Read file');
      const data=await (await fetch('/coder/v3/project/'+encodeURIComponent(proj)+'/file?path='+encodeURIComponent(fpath))).json();
      toolResult(b, data.ok ? '**`'+escapeHtml(fpath)+'`**\n```\n'+escapeHtml((data.content||'').slice(0,3000))+'\n```' : 'Failed: '+JSON.stringify(data));
      return;
    }
    if(cmd==='/write-file'){
      const parts=arg.split(' '); const proj=parts[0], fpath=parts[1], content=parts.slice(2).join(' ');
      if(!proj||!fpath||!content){ pushBubble('assistant','Usage: `/write-file <project_id> <file_path> <content>`'); return; }
      const b=toolBubble('Write file');
      const {data}=await postJSON('/coder/v3/project/'+encodeURIComponent(proj)+'/file',{path:fpath, content:content});
      toolResult(b, data.ok ? '**Wrote `'+escapeHtml(fpath)+'`** ✓' : 'Failed: '+JSON.stringify(data));
      return;
    }
    if(cmd==='/run-shell'){
      const parts=arg.split(' '); const proj=parts[0], command=parts.slice(1).join(' ');
      if(!proj||!command){ pushBubble('assistant','Usage: `/run-shell <project_id> <command>`'); return; }
      const b=toolBubble('Run shell');
      const {data}=await postJSON('/coder/v3/project/'+encodeURIComponent(proj)+'/shell',{command:command});
      toolResult(b, '**Shell**\n```\n'+escapeHtml((data.stdout||data.stderr||data.output||JSON.stringify(data)).slice(0,2000))+'\n```');
      return;
    }
    if(cmd==='/run-project'){
      const parts=arg.split(' '); const proj=parts[0], entry=parts.slice(1).join(' ')||undefined;
      if(!proj){ pushBubble('assistant','Usage: `/run-project <project_id> [entry_file]`'); return; }
      const b=toolBubble('Run project');
      const body = entry ? {entry_file: entry} : {};
      const {data}=await postJSON('/coder/v3/project/'+encodeURIComponent(proj)+'/run', body);
      toolResult(b, '**Run**\n```\n'+escapeHtml((data.stdout||data.stderr||data.output||JSON.stringify(data)).slice(0,2000))+'\n```');
      return;
    }
    if(cmd==='/search'){
      const parts=arg.split(' '); const proj=parts[0], query=parts.slice(1).join(' ');
      if(!proj||!query){ pushBubble('assistant','Usage: `/search <project_id> <query>`'); return; }
      const b=toolBubble('Search code');
      const data=await (await fetch('/coder/v3/project/'+encodeURIComponent(proj)+'/search?query='+encodeURIComponent(query))).json();
      toolResult(b, data.ok ? '**Search results**\n'+((data.results||[]).map(m=>'- `'+escapeHtml(m.file)+'` line '+m.line).join('\n')||'No matches') : 'Failed: '+JSON.stringify(data));
      return;
    }
    if(cmd==='/install'){
      const parts=arg.split(' '); const proj=parts[0], pkg=parts.slice(1).join(' ');
      if(!proj||!pkg){ pushBubble('assistant','Usage: `/install <project_id> <package>`'); return; }
      const b=toolBubble('Install package');
      const {data}=await postJSON('/coder/v3/project/'+encodeURIComponent(proj)+'/install',{packages:pkg.split(',').map(s=>s.trim()).filter(Boolean)});
      toolResult(b, data.ok ? '**Installed `'+escapeHtml(pkg)+'`** ✓\n```\n'+escapeHtml((data.stdout||data.stderr||'').slice(0,1000))+'\n```' : 'Failed: '+JSON.stringify(data));
      return;
    }
    if(cmd==='/git-commit'){
      const parts=arg.split(' '); const proj=parts[0], message=parts.slice(1).join(' ')||'SHIMS auto-commit';
      if(!proj){ pushBubble('assistant','Usage: `/git-commit <project_id> [message]`'); return; }
      const b=toolBubble('Git commit');
      const {data}=await postJSON('/coder/v2/project/'+encodeURIComponent(proj)+'/git/commit',{message:message});
      toolResult(b, data.ok ? '**Committed** ✓\n```\n'+escapeHtml((data.stdout||data.stderr||'').slice(0,1000))+'\n```' : 'Failed: '+JSON.stringify(data));
      return;
    }
    if(cmd==='/shell'){
      if(!arg){ pushBubble('assistant','Command required, e.g. `/shell dir`'); return; }
      const b=toolBubble('Shell');
      const {data}=await postJSON('/api/chat',{message:'/run '+arg, provider:'ollama', agent_mode:true});
      toolResult(b, '```\n'+(data.stdout||data.stderr||JSON.stringify(data)).slice(0,2000)+'\n```');
      return;
    }
    if(cmd==='/python'){
      if(!arg){ pushBubble('assistant','Code required, e.g. `/python print(2+2)`'); return; }
      const b=toolBubble('Python');
      const {data}=await postJSON('/api/chat',{message:'/do run python: '+arg, provider:'ollama', agent_mode:true});
      toolResult(b, '```\n'+(data.stdout||data.stderr||JSON.stringify(data)).slice(0,2000)+'\n```');
      return;
    }
    if(cmd==='/propose'){
      if(!arg){ pushBubble('assistant','Intent required, e.g. `/propose add logging to backend`'); return; }
      const b=toolBubble('Neural Proposal');
      setBubble(b, '_Generating proposal..._\n\n🧠 Analyzing intent...\n🧠 Identifying target files...\n🧠 Generating patch...');
      const {data}=await postJSON('/api/neural-agent/generate',{intent:arg});
      if(data && data.ok){
        renderProposalCard({
          proposal_id: data.proposal_id,
          intent: data.intent || arg,
          thought: data.thought || '',
          file_path: data.file_path || '',
          diff: data.diff || '',
          model_used: data.model_used || 'local',
        }, b);
      } else {
        toolResult(b, '❌ Proposal failed: '+(data?.error||JSON.stringify(data)));
      }
      return;
    }
    if(cmd==='/test-proposal'){
      if(!arg){ pushBubble('assistant','Proposal ID required, e.g. `/test-proposal abc123`'); return; }
      const b=toolBubble('Test Proposal');
      const {data}=await postJSON('/api/neural-agent/proposals/'+arg+'/test',{});
      toolResult(b, data.ok ? '**Test results**\n- status: '+escapeHtml(data.status||'')+'\n- message: '+escapeHtml(data.message||'') : 'Failed: '+JSON.stringify(data));
      return;
    }
    if(cmd==='/apply-proposal'){
      if(!arg){ pushBubble('assistant','Proposal ID required, e.g. `/apply-proposal abc123`'); return; }
      const b=toolBubble('Apply Proposal');
      const {data}=await postJSON('/api/neural-agent/proposals/'+arg+'/apply',{});
      toolResult(b, data.ok ? '**Applied** ✓\n- status: '+escapeHtml(data.status||'')+'\n- message: '+escapeHtml(data.message||'') : 'Failed: '+JSON.stringify(data));
      return;
    }
    if(cmd==='/proposals'){ renderNeuralPanel(); return; }
    if(cmd==='/reflect'){
      const b=toolBubble('Neural Reflection');
      const {data}=await postJSON('/api/neural-agent/reflect',{});
      toolResult(b, data.ok ? '**Reflection complete**\n- proposals generated: '+(data.proposals_generated||0)+'\n- message: '+escapeHtml(data.message||'') : 'Failed: '+JSON.stringify(data));
      return;
    }
    if(cmd==='/neural'){ renderNeuralPanel(); return; }
    if(cmd==='/neural-status'){
      const b=toolBubble('Neural Status');
      const {data}=await (await fetch('/api/neural-agent/model-status')).json();
      toolResult(b, '**Neural Agent Status**\n- model: '+escapeHtml(data.model||'')+'\n- available: '+(data.available?'yes':'no')+'\n- note: '+escapeHtml(data.note||'')+'\n- GPU: '+escapeHtml(data.hardware?.gpu_name||'')+'\n- VRAM: '+(data.hardware?.vram_gb||0)+'GB');
      return;
    }
    if(cmd==='/tasks'){
      const b=toolBubble('Background Tasks');
      const status = arg || '';
      const url = status ? '/api/tasks?status='+encodeURIComponent(status)+'&limit=20' : '/api/tasks?limit=20';
      const {data}=await (await fetch(url)).json();
      const tasks = data.tasks || [];
      if(!tasks.length){ toolResult(b, '**Background Tasks**\n\nNo tasks found.'); return; }
      const rows = tasks.map(t => {
        const elapsed = t.updated_at && t.created_at ? Math.round((t.updated_at - t.created_at)/1000)+'s' : '—';
        return '- `'+t.id+'` · **'+escapeHtml(t.status)+'** · '+escapeHtml(t.task_type)+' · '+escapeHtml(t.title)+' · '+elapsed;
      }).join('\n');
      toolResult(b, '**Background Tasks** ('+tasks.length+')\n\n'+rows);
      return;
    }
    if(cmd==='/auto-evolve'){
      const b=toolBubble('Auto-Evolution');
      const {data}=await postJSON('/api/settings/auto-evolution',{enabled: arg !== 'off'});
      const enabled = data?.auto_evolution;
      toolResult(b, '**Auto-Evolution** '+(enabled?'🟢 ON':'🔴 OFF')+'\n\n'+(enabled?
        'SHIMS will now automatically reflect and propose improvements every few turns.\n\nA reflection task has been scheduled.' :
        'Auto-evolution disabled. SHIMS will only evolve when you explicitly ask.'));
      return;
    }
    if(cmd==='/browser'){
      const sp2 = arg.indexOf(' ');
      const subcmd = (sp2<0 ? arg : arg.slice(0,sp2)).toLowerCase();
      const subarg = (sp2<0 ? '' : arg.slice(sp2+1)).trim();
      if(subcmd==='visit'){
        if(!subarg){ pushBubble('assistant','URL required, e.g. `/browser visit https://example.com`'); return; }
        const b=toolBubble('Browser Visit');
        const {data}=await postJSON('/api/browser/visit',{url:subarg});
        if(data && data.ok){
          let md='**🌐 '+escapeHtml(data.title||'Page')+'**\n'+escapeHtml(data.url)+'\n\n';
          md+=escapeHtml(data.text?.slice(0,2000)||'');
          if(data.headings?.length){ md+='\n\n**Headings:**\n'+data.headings.map(h=>'- '+escapeHtml(h.level)+': '+escapeHtml(h.text)).join('\n'); }
          if(data.links?.length){ md+='\n\n**Links:**\n'+data.links.slice(0,15).map(l=>'- ['+escapeHtml(l.text.slice(0,60))+']('+l.href+')').join('\n'); }
          toolResult(b, md);
        } else { toolResult(b, '❌ Failed: '+(data?.error||JSON.stringify(data))); }
        return;
      }
      if(subcmd==='search'){
        if(!subarg){ pushBubble('assistant','Query required, e.g. `/browser search latest AI news`'); return; }
        const b=toolBubble('Browser Search');
        const {data}=await postJSON('/api/browser/search',{query:subarg});
        if(data && data.ok){
          let md='**🔍 Search: '+escapeHtml(subarg)+'** ('+data.count+' results)\n\n';
          md+=data.results.map(r=>'- **'+escapeHtml(r.title)+'**\n  '+escapeHtml(r.url)+'\n  '+escapeHtml(r.snippet?.slice(0,200)||'')).join('\n\n');
          toolResult(b, md);
        } else { toolResult(b, '❌ Failed: '+(data?.error||JSON.stringify(data))); }
        return;
      }
      if(subcmd==='click'){
        const parts=subarg.split(' '); const url=parts[0], text=parts.slice(1).join(' ');
        if(!url||!text){ pushBubble('assistant','Usage: `/browser click <url> <link text>`'); return; }
        const b=toolBubble('Browser Click');
        const {data}=await postJSON('/api/browser/click',{url:url, text:text});
        toolResult(b, data.ok ? '**Clicked → '+escapeHtml(data.title||'')+'**\n'+escapeHtml(data.url)+'\n\n'+escapeHtml((data.text||'').slice(0,2000)) : '❌ Failed: '+(data?.error||JSON.stringify(data)));
        return;
      }
      if(subcmd==='extract'){
        const parts=subarg.split(' '); const url=parts[0], selector=parts.slice(1).join(' ');
        if(!url||!selector){ pushBubble('assistant','Usage: `/browser extract <url> <css-selector>`'); return; }
        const b=toolBubble('Browser Extract');
        const {data}=await postJSON('/api/browser/extract',{url:url, selector:selector});
        toolResult(b, data.ok ? '**Extracted '+data.count+' elements** from `'+escapeHtml(selector)+'`\n\n'+data.elements.map((e,i)=>'**'+i+'**\n```\n'+escapeHtml(e.text)+'\n```').join('\n\n') : '❌ Failed: '+(data?.error||JSON.stringify(data)));
        return;
      }
      if(subcmd==='screenshot'){
        if(!subarg){ pushBubble('assistant','URL required, e.g. `/browser screenshot https://example.com`'); return; }
        const b=toolBubble('Browser Screenshot');
        const {data}=await postJSON('/api/browser/screenshot',{url:subarg, full_page:true});
        if(data && data.ok){
          let md='**📸 Screenshot**\n'+escapeHtml(data.url)+'\n\n';
          if(data.screenshot_url){ md+='!['+escapeHtml(data.filename)+']('+data.screenshot_url+')'; }
          toolResult(b, md);
        } else { toolResult(b, '❌ Failed: '+(data?.error||JSON.stringify(data))); }
        return;
      }
      if(subcmd==='scroll'){
        const parts=subarg.split(' '); const url=parts[0], direction=parts[1]||'down';
        if(!url){ pushBubble('assistant','Usage: `/browser scroll <url> [up|down|bottom]`'); return; }
        const b=toolBubble('Browser Scroll');
        const {data}=await postJSON('/api/browser/scroll',{url:url, direction:direction});
        toolResult(b, data.ok ? '**Scrolled '+escapeHtml(direction)+'** → '+escapeHtml(data.url)+'\n\n'+escapeHtml((data.text||'').slice(0,2000)) : '❌ Failed: '+(data?.error||JSON.stringify(data)));
        return;
      }
      pushBubble('assistant','Browser subcommands: visit, search, click, extract, screenshot, scroll. Type `/help` for examples.');
      return;
    }
    if(cmd==='/mail'){
      if(!arg || arg==='digest'){
        const b=toolBubble('Mailbox Digest');
        const {data}=await postJSON('/mailbox/gmail/sync',{});
        toolResult(b, data.ok ? '**Mailbox synced**\n\nUnread: '+(data.unread_count||0)+' · Total: '+(data.total_count||0) : '❌ Failed: '+(data?.error||'Gmail not connected'));
        return;
      }
      if(arg==='organize'){
        pushBubble('assistant','Usage: `/mail organize <criteria>` e.g. `/mail organize from:newsletter older_than:7d`');
        return;
      }
      pushBubble('assistant','Mail subcommands: `digest`, `organize <criteria>`. Gmail must be connected in Settings.');
      return;
    }
    if(cmd==='/enterprise'){
      if(state.enterpriseEnabled === false){ pushBubble('assistant','Enterprise integration is not configured.'); return; }
      if(!arg){ pushBubble('assistant','Usage: `/enterprise summary` or `/enterprise list_dashboard rd` or `/enterprise create_experiment {"title":"..."}`'); return; }
      const b=toolBubble('Enterprise Bridge');
      const {data}=await postJSON('/enterprise/command',{command:arg, payload:{}});
      toolResult(b, data.status==='ok' || data.ok ? '**Enterprise response**\n\n```json\n'+JSON.stringify(data, null, 2).slice(0,3000)+'\n```' : '❌ Failed: '+(data?.detail||JSON.stringify(data)));
      return;
    }
    if(cmd==='/bridge'){
      if(!arg){ pushBubble('assistant','Usage: `/bridge <shell-command>` e.g. `/bridge dir` or `/bridge screenshot`'); return; }
      const b=toolBubble('Desktop Bridge');
      let payload={type:'shell', command:arg, timeout:60};
      if(arg==='screenshot'||arg==='info'||arg==='ping'){
        payload={type:arg==='screenshot'?'screenshot':arg==='info'?'system_info':'ping'};
      }
      const {data}=await postJSON('/api/desktop/bridge/command',payload);
      const result=data.result||data;
      if(result.ok && result.format==='png'){
        toolResult(b, '**Screenshot**\n\n![screenshot](data:image/png;base64,'+result.data+')');
      }else if(result.ok){
        toolResult(b, '**Bridge result**\n\n```\n'+(result.stdout||result.stderr||JSON.stringify(result,null,2)).slice(0,3000)+'\n```');
      }else{
        toolResult(b, '❌ Bridge failed: '+(result.error||JSON.stringify(data)));
      }
      return;
    }
    if(cmd==='/plans'){
      const b=toolBubble('Plans');
      const status=arg||'';
      const url=status?'/api/plans?status='+encodeURIComponent(status)+'&limit=20':'/api/plans?limit=20';
      const {data}=await (await fetch(url)).json();
      const plans=data.plans||[];
      if(!plans.length){ toolResult(b, '**Plans**\n\nNo active plans.'); return; }
      const rows=plans.map(p=>'- `'+escapeHtml(p.plan_id||p.id||'?')+'` · **'+escapeHtml(p.status)+'** · '+escapeHtml(p.goal||p.title||'').slice(0,80)).join('\n');
      toolResult(b, '**Active Plans** ('+plans.length+')\n\n'+rows);
      return;
    }
    if(cmd==='/chemdfm'){
      if(!arg){ pushBubble('assistant','Usage: `/chemdfm What is the pKa of aspirin?`'); return; }
      const b=toolBubble('ChemDFM Query');
      const {data}=await postJSON('/chem/chemdfm/query',{query:arg, topic:'general'});
      toolResult(b, data.ok ? '**ChemDFM** ('+(data.source||'unknown')+')\n\n'+escapeHtml(data.answer||'')+'\n\n_<small>'+escapeHtml(data.disclaimer||'')+'</small>_' : '❌ Failed: '+(data?.error||JSON.stringify(data)));
      return;
    }
    pushBubble('assistant','Unknown command. Type `/help` for the list.');
  }catch(e){ pushBubble('assistant','Tool error: '+escapeHtml(e.message||String(e))); }
}

function pickChatFile(mode){
  let inp=document.getElementById('chat-file-input');
  if(!inp){ inp=document.createElement('input'); inp.type='file'; inp.id='chat-file-input'; inp.style.display='none'; document.body.appendChild(inp); }
  inp.accept = mode==='ocr' ? 'image/*' : '*/*';
  inp.onchange = async ()=>{
    const f=inp.files && inp.files[0]; inp.value=''; if(!f) return;
    const b=toolBubble(mode==='ocr'?'OCR':'Ingest file');
    const fd=new FormData(); fd.append('file', f, f.name);
    try{
      if(mode==='ocr'){ const r=await fetch('/ocr',{method:'POST',body:fd}); const d=await r.json(); toolResult(b, d.ok?('**Extracted text**\n\n'+escapeHtml((d.text||'').slice(0,3000))):('OCR unavailable: '+(d.hint||d.error||r.status))); }
      else { const r=await fetch('/api/v15/documents/ingest',{method:'POST',body:fd}); const d=await r.json(); toolResult(b, d.ok?('Ingested **'+escapeHtml(f.name)+'** into memory ✓'):('Ingest failed: '+JSON.stringify(d).slice(0,300))); }
    }catch(e){ toolResult(b,'Failed: '+escapeHtml(e.message||String(e))); }
  };
  inp.click();
}
window.handleSlashCommand = handleSlashCommand;

/* ============ Agent Activity (agentic work, made visible) ============ */
async function agentActivityMarkdown(){
  try{
    const tasks=(await (await fetch('/brain/tasks?limit=8')).json()).tasks||[];
    const skills=(await (await fetch('/skills')).json()).learned||[];
    const running=tasks.filter(t=>t.status==='running').length, queued=tasks.filter(t=>t.status==='queued').length, done=tasks.filter(t=>t.status==='done'||t.status==='completed').length;
    let md='**Background agent**\n\n- running: '+running+' · queued: '+queued+' · done: '+done+'\n';
    if(tasks.length) md+='\nRecent tasks:\n'+tasks.slice(0,6).map(t=>'- ['+t.status+'] '+escapeHtml(t.task_type||t.title||'task')).join('\n');
    if(skills.length) md+='\n\nLearned skills: '+skills.slice(0,6).map(s=>escapeHtml(s.name)).join(', ');
    return md;
  }catch(e){ return 'Agent activity unavailable: '+escapeHtml(e.message||String(e)); }
}
async function refreshAgentStrip(){
  const el=document.getElementById('agent-activity'); if(!el) return;
  try{
    const tasks=(await (await fetch('/brain/tasks?limit=20')).json()).tasks||[];
    const running=tasks.filter(t=>t.status==='running').length, queued=tasks.filter(t=>t.status==='queued').length;
    const recent=tasks.find(t=>t.status==='done'||t.status==='completed');
    el.innerHTML='<span class="aa-dot"></span>Agent: '+(running?running+' running':(queued?queued+' queued':'idle'))+(recent?' · last: '+escapeHtml((recent.task_type||'task')):'');
    el.title='Tap for details';
    el.onclick=()=>{ pushBubble('user','/agent'); handleSlashCommand('/agent'); };
  }catch(e){ el.textContent='Agent: —'; }
}
window.refreshAgentStrip = refreshAgentStrip;

function toolsPick(kind){
  const menu=document.getElementById('tools-menu'); if(menu) menu.classList.add('hidden');
  if(kind==='help'){ pushBubble('user','/help'); handleSlashCommand('/help'); return; }
  if(kind==='ocr'){ pickChatFile('ocr'); return; }
  if(kind==='file'){ pickChatFile('ingest'); return; }
  if(kind==='files'){ pushBubble('user','/files'); handleSlashCommand('/files'); return; }
  if(kind==='tasks'){ pushBubble('user','/tasks'); handleSlashCommand('/tasks'); return; }
  if(kind==='neural'){ renderNeuralPanel(); return; }          // inline neural proposals
  if(kind==='theme'){ cycleTheme(); return; }
  // Prompt-based tools: prefill the slash prefix so the user types the prompt, then Enter.
  const map={image:'/image ', doc:'/doc ', chem:'/chem ', remember:'/remember ', enterprise:'/enterprise ', bridge:'/bridge ', plans:'/plans ', browser:'/browser ', mail:'/mail '};
  const inp=document.getElementById('input'); if(inp){ inp.value=map[kind]||''; inp.focus(); }
}
window.toolsPick = toolsPick;

function toggleNavMore(){
  const m=document.getElementById('nav-more'); const c=document.getElementById('nav-more-caret');
  if(!m) return; const hidden=m.classList.toggle('hidden'); if(c) c.textContent = hidden ? '▾' : '▴';
}
window.toggleNavMore = toggleNavMore;

function finishSpeaking(){
  state.speakBusy=false;
  state.listeningSuppressedUntil = Date.now() + 650;
  state.speakingAudio=null;
  if(state.voiceOn){
    setTimeout(()=>{ try{ const r=getRecognition(); if(r && !state.speakBusy) r.start(); }catch(e){} }, 700);
    if(!state.recognition && state.serverVoiceShouldResume){
      state.serverVoiceShouldResume = false;
      setTimeout(()=>startServerVoiceFallback(), 800);
    }
  }
  setOrbState(state.voiceOn ? 'listening' : 'standby');
}
function stopSpeaking(cancelSynth=true){
  if(state.speakingAudio){ try{state.speakingAudio.pause(); state.speakingAudio.src='';}catch(e){} state.speakingAudio=null; }
  if(cancelSynth && window.speechSynthesis){ try{speechSynthesis.cancel();}catch(e){} }
  state.speakBusy=false;
  state.listeningSuppressedUntil = Date.now() + 500;
}
function estimateSpeechMs(text, minMs=2800, maxMs=90000){
  const words = String(text||'').trim().split(/\s+/).filter(Boolean).length;
  return Math.max(minMs, Math.min(maxMs, words * 480 + 1800));
}
async function speakViaBrowser(text){
  return new Promise((resolve, reject)=>{
    if(!window.speechSynthesis || !window.SpeechSynthesisUtterance) return reject(new Error('browser speech synthesis unavailable'));
    try{ speechSynthesis.cancel(); }catch(e){}
    const u = new SpeechSynthesisUtterance(text);
    u.lang = state.voiceLang || 'en-IN'; u.rate = 1.02; u.pitch = 1.0; u.volume = 1.0;
    const pickVoice = () => {
      const voices = speechSynthesis.getVoices ? speechSynthesis.getVoices() : [];
      const v = voices.find(v => /India|Hindi|hi-IN|en-IN|Ravi|Heera/i.test((v.name||'')+' '+(v.lang||''))) || voices.find(v => /English/i.test((v.name||'')+' '+(v.lang||'')));
      if(v) u.voice = v;
    };
    const chooseVoice = () => {
      return new Promise((res) => {
        const voices = speechSynthesis.getVoices ? speechSynthesis.getVoices() : [];
        if(voices && voices.length){ pickVoice(); res(); return; }
        const handler = () => { pickVoice(); res(); };
        if(speechSynthesis.addEventListener) speechSynthesis.addEventListener('voiceschanged', handler, {once:true});
        setTimeout(() => { pickVoice(); res(); }, 600);
      });
    };
    chooseVoice().then(() => {
      let settled = false;
      const settle = (ok, err) => {
        if(settled) return;
        settled = true;
        clearTimeout(startTimer); clearTimeout(doneTimer); clearInterval(resumeTimer);
        if(!ok){ try{ speechSynthesis.cancel(); }catch(e){} }
        finishSpeaking();
        ok ? resolve(true) : reject(err || new Error('speech synthesis failed'));
      };
      const startTimer = setTimeout(()=>{
        try{
          if(!speechSynthesis.speaking && !speechSynthesis.pending) settle(false, new Error('browser speech did not start'));
        }catch(e){}
      }, 800);
      const doneTimer = setTimeout(()=>settle(false, new Error('browser speech timed out')), estimateSpeechMs(text));
      const resumeTimer = setInterval(()=>{ try{ if(!settled && speechSynthesis.paused) speechSynthesis.resume(); }catch(e){} }, 2000);
      u.onend = () => settle(true);
      u.onerror = (ev) => settle(false, new Error(ev && ev.error ? ev.error : 'speech synthesis error'));
      try{
        speechSynthesis.speak(u);
        // Chrome can occasionally pause synthesis after starting; one gentle resume keeps it alive.
        setTimeout(()=>{ try{ if(!settled && speechSynthesis.paused) speechSynthesis.resume(); }catch(e){} }, 500);
      }catch(e){
        settle(false, e);
      }
    });
  });
}
async function speakViaServerFile(text){
  const r = await fetch('/voice/speak', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text, lang:state.voiceLang || 'en-IN', rate:172})});
  const raw = await r.text();
  let data = {};
  try{ data = raw ? JSON.parse(raw) : {}; }catch(e){ throw new Error('server TTS returned invalid JSON: '+raw.slice(0,120)); }
  if(!r.ok || (data && data.ok === false)) throw new Error((data && (data.detail || data.error || data.reason || data.tts_error)) || ('HTTP '+r.status));
  if(data && data.spoken === true && !data.file_url){ finishSpeaking(); return true; }
  if(!(data && data.file_url)) throw new Error('server TTS did not return audio');
  return new Promise((resolve, reject)=>{
    const a = new Audio(data.file_url); state.speakingAudio = a;
    let settled = false;
    let timer = setTimeout(()=>settle(false, new Error('server audio playback timed out')), estimateSpeechMs(text, 4000, 120000));
    const settle = (ok, err) => {
      if(settled) return;
      settled = true;
      clearTimeout(timer);
      finishSpeaking();
      ok ? resolve(true) : reject(err || new Error('audio playback failed'));
    };
    a.onloadedmetadata=()=>{
      if(Number.isFinite(a.duration) && a.duration > 0){
        clearTimeout(timer);
        timer = setTimeout(()=>settle(false, new Error('server audio playback timed out')), Math.min(120000, Math.max(4500, a.duration * 1000 + 3500)));
      }
    };
    a.onended=()=>settle(true);
    a.onerror=()=>settle(false, new Error('audio playback failed'));
    a.play().catch(err=>settle(false, err));
  });
}
function cleanTextForTTS(text){
  return String(text||'')
    .replace(/https?:\/\/\S+/g, '')
    .replace(/#{1,6}\s*/g, '')
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/\*(.*?)\*/g, '$1')
    .replace(/__(.*?)__/g, '$1')
    .replace(/_(.*?)_/g, '$1')
    .replace(/```[\w]*\n?([\s\S]*?)```/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/^[-*+]\s+/gm, '')
    .replace(/^\d+\.\s+/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}
async function speakText(text){
  text = cleanTextForTTS(text); if(!text) return false;
  stopSpeaking(true);
  state.speakBusy = true;
  state.listeningSuppressedUntil = Date.now() + 999999;
  try{ if(state.recognition) state.recognition.stop(); }catch(e){}
  state.serverVoiceShouldResume = Boolean(state.voiceOn && !state.recognition && state.serverVoiceLoop);
  stopServerVoiceFallback();
  setOrbState('speaking'); showStatus('Speaking...');
  // Try browser TTS first (fast, offline, matches selected language). If it fails,
  // fall back to server TTS so voice output still works in headless or restricted browsers.
  try{
    await speakViaBrowser(text);
    return true;
  }catch(e){
    feed('browser TTS failed, trying server fallback: '+e.message, 'warn');
    try{
      await speakViaServerFile(text);
      return true;
    }catch(e2){
      feed('server TTS fallback also failed: '+e2.message, 'err');
      finishSpeaking();
      showSttBanner('SHIMS heard you, but speech playback failed: '+e2.message);
      return false;
    }
  }
}
window.speakText = speakText;

function getWakeRegex(){
  const words = (state.voiceConfig && state.voiceConfig.wake_words) || ['hey shims','hi shims','hello shims','ok shims','okay shims','suno shims','sun rahe ho','arre shims','shims','excuse me shims','listen shims','yo shims','shims assistant','shims bot','shims ai','shims system'];
  const escaped = words.map(w => String(w).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
  return new RegExp('\\b(' + escaped + ')\\b', 'i');
}
function cleanWake(txt){ return String(txt||'').replace(getWakeRegex(), '').replace(/^\s*[,.:;\-]+\s*/, '').trim(); }
function armWakeLatch(ms=9000){
  state.wakeLatchUntil = Date.now() + ms;
  showStatus('Wake detected. Listening for command...');
}
function wakeLatchActive(){ return Date.now() < (state.wakeLatchUntil || 0); }
function cancelWakeAck(){
  if(state.wakeAckTimer){ clearTimeout(state.wakeAckTimer); state.wakeAckTimer = null; }
}
function speakWakeAck(){
  cancelWakeAck();
  const now = Date.now();
  if(now - (state.lastWakeAckAt || 0) < 3500) return;
  state.lastWakeAckAt = now;
  toast('Hey SHIMS activated');
  speakText('Yes, I am listening.').then(ok => {
    if(!ok) showStatus('Wake detected. Listening for command...');
  }).catch(()=>showStatus('Wake detected. Listening for command...'));
}
function queueWakeAck(delay=1400){
  if(!state.voiceOn || state.streaming) return;
  const now = Date.now();
  if(now - (state.lastWakeAckAt || 0) < 3500) return;
  cancelWakeAck();
  state.wakeAckTimer = setTimeout(()=>{
    state.wakeAckTimer = null;
    if(state.voiceOn && !state.streaming && !state.speakBusy) speakWakeAck();
  }, delay);
}
async function handleVoicePhrase(phrase){
  phrase = String(phrase||'').trim();
  if(!phrase) return;
  if(state.speakBusy || Date.now() < (state.listeningSuppressedUntil||0)) return;
  stopSpeaking(true);
  const wake = getWakeRegex();
  let command = phrase;
  if(state.wakeArmed){
    const hasWake = wake.test(phrase);
    if(!hasWake && !wakeLatchActive()){
      const now=Date.now();
      if(now - state.lastVoiceStatusAt > 6000){ showStatus('Say Hey SHIMS...'); state.lastVoiceStatusAt = now; }
      return;
    }
    if(hasWake){
      armWakeLatch();
      command = cleanWake(phrase);
      if(!command){ speakWakeAck(); return; }
    }
  }
  command = command.trim();
  if(!command) return;
  cancelWakeAck();
  state.wakeLatchUntil = 0;
  const now=Date.now();
  if(command.toLowerCase() === String(state.lastVoiceSentText||'').toLowerCase() && now-state.lastVoiceSentAt < 15000) return;
  const input=$('#input'); if(input) input.value = command;
  // Server STT can provide a correction id. Browser STT correction is opt-in
  // because it adds an extra backend round trip on every voice turn.
  if(VOICE_CORRECTION_ENABLED && !state.pendingVoiceCorrectionId){
    try{
      const r = await fetch('/voice/correct', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text: phrase, session_id: state.sessionId || undefined, language: state.voiceLang || 'auto'})});
      const d = await r.json();
      if(d && d.correction_id) state.pendingVoiceCorrectionId = d.correction_id;
    }catch(e){}
  }
  sendText(command, 'voice');
  state.pendingVoiceCorrectionId = null;
}
async function startServerVoiceFallback(){
  if(state.serverVoiceLoop) return;
  state.serverVoiceLoop = true;
  try{
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    state.serverVoiceStream = stream;
    setOrbState('listening');
    showStatus(state.wakeArmed ? 'Server STT armed. Say Hey SHIMS...' : 'Server STT conversation ready.');
    feed('server STT armed via faster-whisper fallback', 'info');
    const loop = async () => {
      if(!state.voiceOn || state.recognition || !state.serverVoiceLoop) return;
      if(Date.now() < (state.serverSttBackoffUntil || 0)){ setTimeout(loop, Math.max(700, state.serverSttBackoffUntil - Date.now())); return; }
      if(state.speakBusy || Date.now() < (state.listeningSuppressedUntil||0)){ setTimeout(loop, 700); return; }
      let rec;
      try{ rec = new MediaRecorder(stream); }catch(e){ showSttBanner('MediaRecorder unavailable: '+e.message); state.serverVoiceLoop=false; return; }
      const chunks=[];
      rec.ondataavailable = ev => { if(ev.data && ev.data.size) chunks.push(ev.data); };
      rec.onstop = async () => {
        try{
          const blob = new Blob(chunks, {type: rec.mimeType || 'audio/webm'});
          if(blob.size > 1800){
            const fd = new FormData();
            fd.append('file', blob, 'shims_voice.webm');
            fd.append('lang', state.voiceLang || 'auto');
            fd.append('correct', VOICE_CORRECTION_ENABLED ? 'true' : 'false');
            const r = await fetch('/voice/transcribe', {method:'POST', body:fd});
            if(r.status === 429){
              state.serverSttBackoffUntil = Date.now() + 5000;
              showStatus('Speech is cooling down for a moment...');
            } else if(!r.ok){
              feed('server STT HTTP '+r.status, 'warn');
            } else {
              const d = await r.json();
              if(d && d.ok && d.text && d.text.trim()){
                state.pendingVoiceCorrectionId = d.correction_id || null;
                await handleVoicePhrase(d.text.trim());
              } else if(d && d.ok === false && (d.hint || d.reason)){
              // Server STT could not run (e.g. speech model not downloaded). Surface it once.
              const now=Date.now();
              if(now - state.lastVoiceStatusAt > 15000){ showSttBanner('Server STT unavailable: ' + (d.hint || d.reason)); state.lastVoiceStatusAt = now; }
              } else {
                const now=Date.now();
                if(now - state.lastVoiceStatusAt > 12000){ showStatus('Listening...'); state.lastVoiceStatusAt = now; }
              }
            }
          }
        }catch(e){ feed('server STT error: '+e.message, 'warn'); }
        if(state.voiceOn && !state.recognition && state.serverVoiceLoop) setTimeout(loop, state.speakBusy ? 900 : 650);
      };
      try{ rec.start(); setTimeout(()=>{ try{ if(rec.state !== 'inactive') rec.stop(); }catch(e){} }, SERVER_STT_CHUNK_MS); }catch(e){ feed('server STT start failed: '+e.message, 'err'); state.serverVoiceLoop=false; }
    };
    loop();
  }catch(e){ state.serverVoiceLoop=false; showSttBanner('Microphone permission blocked or unavailable: '+e.message); }
}
function stopServerVoiceFallback(){
  state.serverVoiceLoop = false;
  if(state.serverVoiceStream){ try{ state.serverVoiceStream.getTracks().forEach(t=>t.stop()); }catch(e){} state.serverVoiceStream=null; }
}
function getRecognition(){
  if(state.browserSttFailed) return null;  // browser STT already failed this session -> use server STT
  if(state.recognition) return state.recognition;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!SR) return null;
  const r = new SR();
  r.continuous = true; r.interimResults = true; r.maxAlternatives = 3; r.lang = state.voiceLang || 'en-IN';
  r.onresult = ev => {
    if(state.speakBusy || Date.now() < (state.listeningSuppressedUntil||0)) return;
    let interim='', final='';
    for(let i=ev.resultIndex;i<ev.results.length;i++){
      const t = ev.results[i][0].transcript;
      if(ev.results[i].isFinal) final += t + ' '; else interim += t;
    }
    const shown = (final || interim || '').trim(); const partial=$('#partial-text'); if(partial) partial.textContent = shown;
    if(!final.trim()) return;
    handleVoicePhrase(final.trim());
  };
  r.onerror = ev => {
    if(ev.error === 'no-speech'){ setOrbState('listening'); return; }
    if(ev.error === 'aborted') return;
    // The browser Web Speech API relies on Google's cloud servers; 'network' and
    // 'service-not-allowed' mean that backend is unreachable. Don't loop on a dead
    // recognizer — switch to the local faster-whisper server STT instead.
    if(ev.error === 'network' || ev.error === 'service-not-allowed'){
      state.browserSttFailed = true;
      try{ r.abort ? r.abort() : r.stop(); }catch(e){}
      state.recognition = null;
      feed('Browser speech backend unreachable ('+ev.error+') — switching to local server STT.', 'warn');
      showSttBanner('Browser voice needs Google servers and failed ('+ev.error+'). Switched to local server STT (faster-whisper).');
      if(state.voiceOn) startServerVoiceFallback();
      return;
    }
    feed('voice error: '+ev.error, ev.error === 'not-allowed' ? 'err' : 'warn');
    if(ev.error === 'audio-capture' || ev.error === 'not-allowed') showSttBanner('Voice error: '+ev.error+'. Allow microphone, use Chrome/Edge, or use server STT fallback.');
  };
  r.onend = () => {
    if(state.browserSttFailed) return;  // never restart a recognizer that hit a network/service failure
    if(state.voiceOn && !state.speakBusy){ const delay=Math.max(500, (state.listeningSuppressedUntil||0)-Date.now()+50); setTimeout(()=>{ try{ if(!state.speakBusy){ r.lang = state.voiceLang || 'en-IN'; r.start(); } }catch(e){} }, delay); } };
  state.recognition = r; return r;
}
async function toggleVoice(){
  state.voiceOn = !state.voiceOn; persist();
  const orb=$('#voice-orb'); if(orb) orb.classList.toggle('on', state.voiceOn);
  if(state.voiceOn){
    if(state.wakeArmed){
    // Optional wake-word engine. Continuous conversation mode skips this loop.
    try{
      await state.wakeEngine.start((detection) => {
        toast('Wake word: ' + (detection.label || 'detected'), 'info');
        armWakeLatch();
        queueWakeAck(1400);
        // If browser STT available, ensure it's capturing
        if(state.recognition){
          try{ state.recognition.start(); }catch(e){}
        } else if(state.serverVoiceLoop){
          // Server STT is already looping; wake word just signals user intent
        } else {
          // No STT running yet — try browser first, then fallback
          const r=getRecognition();
          if(r){ try{ r.start(); }catch(e){} }
          else { startServerVoiceFallback(); }
        }
      });
    }catch(e){ console.warn('WakeWordEngine failed to start:', e); }
    }
    const r=getRecognition();
    if(!r){
      toast('Browser speech recognition unavailable. Trying server STT fallback.', 'warn');
      await startServerVoiceFallback();
      setOrbState('listening'); showStatus(state.wakeArmed ? 'Voice armed. Say Hey SHIMS...' : 'Voice conversation ready.'); feed('voice armed '+state.voiceLang, 'info');
      return;
    }
    try{ await navigator.mediaDevices.getUserMedia({audio:true}); }catch(e){ toast('Microphone permission blocked', 'err'); }
    if(r){ try{ r.start(); }catch(e){} }
    setOrbState('listening'); showStatus(state.wakeArmed ? 'Voice armed. Say Hey SHIMS...' : 'Voice conversation ready.'); feed('voice armed '+state.voiceLang, 'info');
  }else{
    state.wakeEngine.stop();
    try{ state.recognition && state.recognition.stop(); }catch(e){}
    stopServerVoiceFallback();
    stopSpeaking(true); setOrbState('standby'); hideStatus(); feed('voice off', 'info');
  }
}
window.toggleVoice = toggleVoice;

async function loadVoiceConfig(){ try{ const r=await fetch('/voice/config'); const d=await r.json(); state.voiceConfig=d.config; }catch(e){} }
async function checkSttHealth(){ try{ const d=await (await fetch('/stt/health')).json(); toast('STT: browser '+(d.browser_stt?'ok':'no')+', server '+(d.server_stt_installed?'installed':'not installed'), d.server_stt_installed?'info':'warn'); }catch(e){toast('STT check failed: '+e.message,'err');} }
window.checkSttHealth=checkSttHealth;
async function loadSttModels(){
  const sel=document.getElementById('set-stt-model'); if(!sel) return;
  try{
    const d=await (await fetch('/stt/models')).json();
    const models=d.models||[];
    if(!models.length){ sel.innerHTML='<option value="">No local model installed</option>'; return; }
    sel.innerHTML='';
    models.forEach(m=>{ const o=document.createElement('option'); o.value=m.id; o.textContent=m.label+(m.ready?'':' (not downloaded)'); if(m.id===d.active) o.selected=true; sel.appendChild(o); });
    const st=document.getElementById('stt-model-status'); if(st) st.textContent='Active: '+(String(d.active).split(/[\\\/]/).pop())+' · base = faster, small = more accurate (CPU).';
  }catch(e){ sel.innerHTML='<option value="">STT unavailable</option>'; }
}
window.loadSttModels=loadSttModels;
async function setSttModel(id){
  if(!id) return;
  const st=document.getElementById('stt-model-status');
  try{
    const d=await (await fetch('/stt/model',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:id})})).json();
    if(d && d.ok){ if(st) st.textContent='Active: '+String(id).split(/[\\\/]/).pop()+(d.ready?' (ready)':' (not downloaded)')+' · applies on your next spoken command.'; toast('Speech model switched to '+String(id).split(/[\\\/]/).pop()); }
    else { if(st) st.textContent=(d&&d.detail)||'Switch failed'; toast('Switch failed','err'); }
  }catch(e){ if(st) st.textContent='Switch failed: '+e.message; toast('Switch failed: '+e.message,'err'); }
}
window.setSttModel=setSttModel;
async function resetSttCache(){
  const st=document.getElementById('stt-model-status');
  try{
    const d=await (await fetch('/stt/reset',{method:'POST'})).json();
    if(d && d.ok){ toast('STT cache cleared — reloads on next speech (device: '+d.device_will_use+')'); if(st) st.textContent='Cache cleared. Next voice command reloads on '+d.device_will_use+'.'; }
    else { toast('Reset failed','err'); }
  }catch(e){ toast('Reset failed: '+e.message,'err'); }
}
window.resetSttCache=resetSttCache;
async function saveVoiceConfig(){
  const lang=$('#v9-voice-lang') ? $('#v9-voice-lang').value : state.voiceLang;
  const cooldown=parseFloat($('#v9-cooldown') ? $('#v9-cooldown').value : '2.2');
  state.voiceLang = lang; persist();
  try{ await fetch('/voice/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({primary_lang:lang,secondary_langs:['hi-IN','en-US'],command_cooldown_seconds:cooldown,silence_timeout_seconds:1.4,max_auto_replies_without_user:1})}); toast('Voice config saved'); if(state.recognition){try{state.recognition.stop()}catch(e){} state.recognition=null;} }
  catch(e){toast('Voice config failed: '+e.message,'err');}
}
window.saveVoiceConfig = saveVoiceConfig;

async function loadModelList(){
  const menu=$('#model-menu'); if(menu) menu.innerHTML='<div class="model-card"><div class="m-name">Loading models...</div></div>';
  try{
    const r=await fetch('/chat/models'); const data=await r.json(); state.models = data; syncProviderModel(data); renderModelMenu(data); renderModelManager(data); populateProviderModelSelects(data);
    const installed=(data.installed||[]).map(m=>m.name);
    setText('#chip-llm', installed.length ? 'LLM '+installed.length : 'LLM offline'); setText('#t-model', (state.selectedModel||data.default||'auto').slice(0,22));
  }catch(e){ if(menu) menu.innerHTML='<div class="model-card"><div class="m-name">Model list unavailable</div><div class="m-meta">'+escapeHtml(e.message)+'</div></div>'; feed('models unavailable: '+e.message, 'err'); }
}
window.loadModelList = loadModelList;
function renderModelMenu(data=state.models){
  const menu=$('#model-menu'); if(!menu) return;
  const installed=data.installed||[]; const rec=data.recommended||[]; const cloud=data.all_cloud||data.cloud||[];
  const toolBadge = (m) => m.tool_capable ? ' <span title="Tool-capable" style="color:#74ffb9;font-size:10px">🛠</span>' : '';
  let html='';
  // LM Studio models (GPU-accelerated local) — shown first since it's the fast path
  const lmModels = installed.filter(m=>m.provider==='lmstudio');
  if(lmModels.length){
    html += '<div style="font-size:10px;color:#9eb6c1;margin:4px 0 8px">⚡ LM Studio (GPU)</div>';
    html += lmModels.map(m=>{
      const loadedBadge = m.loaded ? ' <span title="Loaded — instant response" style="color:#74ffb9;font-size:10px">●</span>' : '';
      const tc = m.tool_capable ? toolBadge(m) : '';
      return `<button class="model-card" data-provider="lmstudio" data-model="${escapeHtml(m.name)}"><div class="m-name">${escapeHtml(m.name)}${tc}${loadedBadge}</div><div class="m-meta">${escapeHtml([m.parameters,m.family,m.quantization].filter(Boolean).join(' · ')||'local model')}</div></button>`;
    }).join('');
  }
  // Installed models
  html += '<div style="font-size:10px;color:#9eb6c1;margin:4px 0 8px">📦 Installed</div>';
  if(installed.length){
    html += installed.filter(m=>m.provider==='ollama'||!m.provider).map(m=>{
      const recMeta = rec.find(r=>r.name===m.name);
      const tc = recMeta ? toolBadge(recMeta) : '';
      return `<button class="model-card" data-provider="ollama" data-model="${escapeHtml(m.name)}"><div class="m-name">${escapeHtml(m.name)}${tc}</div><div class="m-meta">${escapeHtml([m.parameters,m.family,m.quantization].filter(Boolean).join(' · ')||'local model')}</div></button>`;
    }).join('');
  } else {
    html += '<div class="model-card"><div class="m-name">No local models</div><div class="m-meta">Start Ollama or pull a model</div></div>';
  }
  // Suggested models (not installed yet) - HIDDEN: user wants only installed models
  // const notInstalled = rec.filter(m=>m.provider==='ollama' && !m.installed);
  // if(notInstalled.length){
  //   html += '<div style="font-size:10px;color:#9eb6c1;margin:10px 0 8px">💡 Suggested for your machine</div>';
  //   html += notInstalled.slice(0,6).map(m=>`<button class="model-card" data-provider="ollama" data-model="${escapeHtml(m.name)}"><div class="m-name">${escapeHtml(m.name)} ↓${toolBadge(m)}</div><div class="m-meta">${escapeHtml(m.role||'')} · ${escapeHtml(m.notes||'')}</div></button>`).join('');
  // }
  // Quick cloud favorites (top 5 only)
  const topCloud = [
    {name:'gpt-4.5-preview',provider:'openai',role:'best reasoning'},
    {name:'claude-sonnet-4-6',provider:'anthropic',role:'balanced'},
    {name:'gemini-2.5-pro',provider:'gemini',role:'multimodal'},
    {name:'kimi-k2.7',provider:'kimi',role:'long context'},
    {name:'deepseek-chat',provider:'deepseek',role:'cheap & fast'}
  ];
  html += '<div style="font-size:10px;color:#9eb6c1;margin:10px 0 8px">☁️ Cloud quick picks</div>';
  html += topCloud.map(m=>`<button class="model-card" data-provider="${escapeHtml(m.provider)}" data-model="${escapeHtml(m.name)}"><div class="m-name">${escapeHtml(m.name)}</div><div class="m-meta">${escapeHtml(m.role)} · requires API key</div></button>`).join('');
  menu.innerHTML=html;
  $$('.model-card[data-model]', menu).forEach(btn=>btn.onclick=()=>{ state.provider=btn.dataset.provider||'ollama'; state.selectedModel=btn.dataset.model||''; persist(); updateModeButtons(); setText('#t-model', state.selectedModel.slice(0,22)); setText('#t-route', state.provider); menu.classList.remove('open'); const parent=document.getElementById('model-dropdown'); if(parent) parent.classList.remove('open'); toast('Model: '+state.provider+' / '+state.selectedModel); });
}
window.renderModelMenu = renderModelMenu;

function populateProviderModelSelects(data=state.models){
  // Pioneer (flagship) + free-tier-friendly fast model only. No deprecated noise.
  const bestModels = {
    openai: ['gpt-5.5-pro','gpt-4o-mini'],
    anthropic: ['claude-opus-4-6','claude-sonnet-4-6'],
    gemini: ['gemini-3.5-pro','gemini-2.5-flash'],
    kimi: ['kimi-k2.7'],
    deepseek: ['deepseek-chat','deepseek-reasoner'],
    qwen: ['qwen-max'],
    huggingface: ['meta-llama/Llama-3.1-8B-Instruct','Qwen/Qwen2.5-7B-Instruct']
  };
  for(const pid of ['openai','anthropic','gemini','kimi','deepseek','qwen','huggingface']){
    const sel=$('#model-'+pid); if(!sel) continue;
    const opts=bestModels[pid]||[];
    sel.innerHTML = '<option value="">Default / auto</option>' + opts.map(x=>`<option value="${escapeHtml(x)}">${escapeHtml(x)}</option>`).join('');
  }
  // LM Studio is whatever the user has actually downloaded — list real installed
  // models (loaded ones first) instead of a fixed/guessed catalog.
  const lmSel=$('#model-lmstudio');
  if(lmSel){
    const lmModels=(data.installed||[]).filter(m=>m.provider==='lmstudio').slice().sort((a,b)=>(b.loaded?1:0)-(a.loaded?1:0));
    lmSel.innerHTML = '<option value="">Default / auto</option>' + lmModels.map(m=>`<option value="${escapeHtml(m.name)}">${escapeHtml(m.name)}${m.loaded?' (loaded)':''}</option>`).join('');
  }
  populateAgentModelSelects(data);
}
function populateAgentModelSelects(data=state.models){
  const all=(data.installed||[]).concat(data.cloud||[]).filter(m=>m.tool_capable);
  const opts=all.map(m=>`<option value="${escapeHtml(m.name)}">${escapeHtml(m.name)} (${m.provider})</option>`).join('');
  $$('.agent-model-select').forEach(sel=>{
    const current=sel.value;
    sel.innerHTML='<option value="">Auto</option>'+opts;
    sel.value=current||'';
  });
}
window.populateAgentModelSelects=populateAgentModelSelects;
function renderModelManager(data=state.models){
  const box=$('#v9-model-list'); if(!box) return;
  const rec=data.recommended||[];
  let html = rec.map(m=>`<div class="v9-chip"><b>${escapeHtml(m.name)}</b> <span class="${m.installed?'v9-ok':'v9-warn'}">${m.installed?'installed':'not installed'}</span><small>${escapeHtml(m.role||'')} · ${escapeHtml(m.notes||'')}</small>${m.provider==='ollama'?`<button class="v9-btn" data-pull="${escapeHtml(m.name)}" style="margin-top:7px">Pull / Update</button>`:''}</div>`).join('');
  html += `<div class="v9-chip" style="opacity:.85"><b>ChemDFM</b> <span class="v9-warn">not Ollama</span><small>Chemistry model uses its own endpoint/HuggingFace. Use /chemdfm command, not Pull.</small></div>`;
  box.innerHTML = html;
  $$('[data-pull]', box).forEach(b=>b.onclick=()=>pullModel(b.dataset.pull));
}

async function saveMediaSettings(){
  const backend=$('#v11-image-backend')?.value || 'auto';
  const sd=$('#v11-sd-url')?.value || '';
  const diff=$('#v11-diffusers-enabled')?.checked || false;
  const model=$('#v11-diffusers-model')?.value || '';
  const audioBackend=$('#set-audio-backend')?.value || undefined;
  const videoBackend=$('#set-video-backend')?.value || undefined;
  const audioUrl=$('#set-audio-api-url')?.value || undefined;
  const audioKey=$('#set-audio-api-key')?.value || undefined;
  const videoUrl=$('#set-video-api-url')?.value || undefined;
  const videoKey=$('#set-video-api-key')?.value || undefined;
  const openaiTtsModel=$('#set-openai-tts-model')?.value || undefined;
  const openaiTtsVoice=$('#set-openai-tts-voice')?.value || undefined;
  const openaiVideoModel=$('#set-openai-video-model')?.value || undefined;
  const openaiVideoSize=$('#set-openai-video-size')?.value || undefined;
  const openaiVideoSeconds=parseInt($('#set-openai-video-seconds')?.value || '', 10);
  try{
    const payload={image_backend:backend,stable_diffusion_url:sd,diffusers_enabled:diff,diffusers_model:model};
    if(audioBackend) payload.audio_backend=audioBackend;
    if(videoBackend) payload.video_backend=videoBackend;
    if(audioUrl !== undefined) payload.audio_api_url=audioUrl;
    if(audioKey) payload.audio_api_key=audioKey;
    if(videoUrl !== undefined) payload.video_api_url=videoUrl;
    if(videoKey) payload.video_api_key=videoKey;
    if(openaiTtsModel !== undefined) payload.openai_tts_model=openaiTtsModel;
    if(openaiTtsVoice !== undefined) payload.openai_tts_voice=openaiTtsVoice;
    if(openaiVideoModel !== undefined) payload.openai_video_model=openaiVideoModel;
    if(openaiVideoSize !== undefined) payload.openai_video_size=openaiVideoSize;
    if(Number.isFinite(openaiVideoSeconds)) payload.openai_video_seconds=openaiVideoSeconds;
    const privacyMode=$('#set-privacy-mode')?.value;
    if(privacyMode){ state.privacyMode=privacyMode; persist(); updateModeButtons(); }
    const r=await fetch('/media/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    if($('#set-audio-api-key') && d.ok) $('#set-audio-api-key').value='';
    if($('#set-video-api-key') && d.ok) $('#set-video-api-key').value='';
    toast(d.ok?'Media settings saved':'Media settings failed', d.ok?'info':'warn');
  }catch(e){toast('Media settings failed: '+e.message,'err');}
}
window.saveMediaSettings = saveMediaSettings;
async function loadMediaSettings(){
  try{
    const d=await (await fetch('/media/settings')).json();
    const s=d.settings||{};
    if($('#set-audio-backend')) $('#set-audio-backend').value=s.audio_backend||'auto';
    if($('#set-video-backend')) $('#set-video-backend').value=s.video_backend||'auto';
    if($('#set-audio-api-url')) $('#set-audio-api-url').value=s.audio_api_url||'';
    if($('#set-video-api-url')) $('#set-video-api-url').value=s.video_api_url||'';
    if($('#set-openai-tts-model')) $('#set-openai-tts-model').value=s.openai_tts_model||'gpt-4o-mini-tts';
    if($('#set-openai-tts-voice')) $('#set-openai-tts-voice').value=s.openai_tts_voice||'alloy';
    if($('#set-openai-video-model')) $('#set-openai-video-model').value=s.openai_video_model||'sora-2';
    if($('#set-openai-video-size')) $('#set-openai-video-size').value=s.openai_video_size||'1280x720';
    if($('#set-openai-video-seconds')) $('#set-openai-video-seconds').value=s.openai_video_seconds||4;
    if($('#set-audio-api-key')) $('#set-audio-api-key').placeholder=s.audio_api_key?'configured':'optional audio API key';
    if($('#set-video-api-key')) $('#set-video-api-key').placeholder=s.video_api_key?'configured':'optional video API key';
    if($('#v11-image-backend')) $('#v11-image-backend').value=s.image_backend||'auto';
    if($('#v11-sd-url')) $('#v11-sd-url').value=s.stable_diffusion_url||'';
    if($('#v11-diffusers-enabled')) $('#v11-diffusers-enabled').checked=!!s.diffusers_enabled;
    if($('#v11-diffusers-model')) $('#v11-diffusers-model').value=s.diffusers_model||'';
    if($('#set-privacy-mode')) $('#set-privacy-mode').value=state.privacyMode||'balanced';
  }catch(e){ feed('media settings unavailable: '+e.message, 'warn'); }
}
window.loadMediaSettings = loadMediaSettings;
async function startOllama(){ try{ const r=await fetch('/ollama/start',{method:'POST'}); const d=await r.json(); toast(d.ok?'Ollama started/online':(d.detail||'Ollama not started'), d.ok?'info':'err'); await loadModelList(); }catch(e){toast(e.message,'err');} }
window.startOllama = startOllama;
async function pullModel(model){
  model = model || ($('#v9-pull-model') ? $('#v9-pull-model').value.trim() : ''); if(!model) return toast('Enter model name','warn');
  const log=$('#v9-pull-log'); if(log) log.textContent='Starting pull '+model+'...';
  let hadError = false;
  try{
    const r=await fetch('/ollama/pull',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model,stream:true})});
    const reader=r.body.getReader(); const dec=new TextDecoder(); let buf='';
    while(true){ const {done,value}=await reader.read(); if(done) break; buf += dec.decode(value,{stream:true}); const lines=buf.split(/\r?\n/); buf=lines.pop()||''; for(const line of lines){ if(!line.trim()) continue; let obj={}; try{obj=JSON.parse(line)}catch(e){} if(obj.type==='error' || obj.error || obj.detail){ hadError=true; } if(log) log.textContent = (obj.status || obj.detail || obj.error || JSON.stringify(obj)).slice(0,500); } }
    if(hadError){ toast('Pull failed for '+model+' — see log','err'); return; }
    state.provider='ollama'; state.selectedModel=model; persist(); toast('Pull finished and selected: '+model); await loadModelList();
  }catch(e){ if(log) log.textContent='Pull failed: '+e.message; toast('Pull failed: '+e.message,'err'); }
}
window.pullModel = pullModel;


async function saveWebSettings(){
  const searx=$('#v14-searxng-url')?.value || ''; const tav=$('#v14-tavily-key')?.value || ''; const brave=$('#v14-brave-key')?.value || '';
  try{ const d=await (await fetch('/web/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({searxng_url:searx,tavily_key:tav,brave_key:brave,duckduckgo_fallback:true})})).json(); toast(d.ok?'Search settings saved':'Search settings failed', d.ok?'info':'warn'); }catch(e){ toast('Search settings failed: '+e.message,'err'); }
}
window.saveWebSettings=saveWebSettings;
async function testWebSearch(){
  try{ const d=await (await fetch('/web/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:'SHIMS realtime voice agent test',max_results:3})})).json(); toast(d.ok?'Search works via '+d.provider:'Search needs configuration/internet', d.ok?'info':'warn'); feed('search '+(d.ok?'ok':'failed')+': '+(d.provider||d.message), d.ok?'info':'warn'); }catch(e){ toast('Search test failed: '+e.message,'err'); }
}
window.testWebSearch=testWebSearch;
async function loadVoiceProfiles(){
  const box=$('#v14-voice-profiles'); if(!box) return; try{ const d=await (await fetch('/voice/profiles')).json(); box.innerHTML=(d.profiles||[]).map(p=>`<div class="v9-chip"><b>${escapeHtml(p.name)} ${d.selected===p.id?'✓':''}</b><small>${escapeHtml((p.voiceprint_sha256||'').slice(0,20))}... · ${escapeHtml(p.engine||'profile-vault')}</small><button class="v9-btn" onclick="selectVoiceProfile('${escapeHtml(p.id)}')">Select</button></div>`).join('') || '<div class="empty-pane">No voice profiles yet. Enroll only voices you are authorized to use.</div>'; }catch(e){box.textContent=e.message;}
}
window.loadVoiceProfiles=loadVoiceProfiles;
async function selectVoiceProfile(id){ const d=await (await fetch('/voice/profiles/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile_id:id})})).json(); toast(d.ok?'Voice profile selected':'Profile select failed', d.ok?'info':'warn'); loadVoiceProfiles(); }
window.selectVoiceProfile=selectVoiceProfile;
async function enrollVoiceProfile(){
  const name=$('#v14-voice-name')?.value || 'owner'; const f=$('#v14-voice-file')?.files?.[0]; if(!f) return toast('Choose an authorized voice recording first','warn');
  const fd=new FormData(); fd.append('file',f); fd.append('name',name); fd.append('consent_phrase','I authorize SHIMS to use this voice profile');
  try{ const d=await (await fetch('/voice/profiles/enroll',{method:'POST',body:fd})).json(); toast(d.ok?'Voice profile enrolled':'Enrollment failed', d.ok?'info':'warn'); loadVoiceProfiles(); }catch(e){ toast('Enrollment failed: '+e.message,'err'); }
}
window.enrollVoiceProfile=enrollVoiceProfile;

function ensureSettingsEnhancements(){
  const body=$('#pane-settings .pane-body'); if(!body || $('#v9-settings-card')) return;
  const card=document.createElement('div'); card.id='v9-settings-card'; card.className='v9-setting-card';
  card.innerHTML=`<h3>Ollama Model Manager</h3><div class="v9-row"><button class="v9-btn" onclick="startOllama()">Start Ollama</button><button class="v9-btn" onclick="loadModelList()">Refresh Models</button><button class="v9-btn" onclick="forceLocalMode()">Force Local/Ollama</button><input id="v9-pull-model" placeholder="llama3.2:latest or qwen2.5:7b"><button class="v9-btn" onclick="pullModel()">Pull / Download</button></div><div id="v9-pull-log" style="font-size:11px;color:#9eb6c1;min-height:18px"></div><div class="v9-list" id="v9-model-list"></div><h3 style="margin-top:16px">Voice / Listening</h3><div class="v9-row"><select id="v9-voice-lang"><option value="en-IN">Indian English (en-IN)</option><option value="hi-IN">Hindi / Hinglish (hi-IN)</option><option value="en-US">English US (fallback)</option></select><input id="v9-cooldown" type="number" step="0.1" value="2.2" title="Cooldown seconds"><button class="v9-btn" onclick="saveVoiceConfig()">Save Voice</button><button class="v9-btn" onclick="speakText('Namaste, SHIMS voice is ready. Main sun raha hoon.')">Test Speak</button><button class="v9-btn" onclick="checkSttHealth()">Check STT</button></div><small>Voice is wake-gated. Say: Hey SHIMS, image banao ek panda relaxing.</small><h3 style="margin-top:16px">Media Generation</h3><div class="v9-row"><select id="v11-image-backend"><option value="auto">Auto</option><option value="pollinations">Pollinations.ai (free, no key)</option><option value="stable-diffusion">Stable Diffusion WebUI API</option><option value="diffusers">Diffusers Local</option><option value="openai">OpenAI Image</option><option value="qwen">Qwen / Alibaba Image</option></select><input id="v11-sd-url" placeholder="STABLE_DIFFUSION_URL e.g. http://127.0.0.1:7860"><label style="display:flex;gap:6px;align-items:center"><input id="v11-diffusers-enabled" type="checkbox"> Diffusers</label><input id="v11-diffusers-model" placeholder="runwayml/stable-diffusion-v1-5"><button class="v9-btn" onclick="saveMediaSettings()">Save Media</button></div><h3 style="margin-top:16px">Internet Search</h3><div class="v9-row"><input id="v14-searxng-url" placeholder="SearXNG URL e.g. http://127.0.0.1:8888"><input id="v14-tavily-key" type="password" placeholder="Tavily key optional"><input id="v14-brave-key" type="password" placeholder="Brave key optional"><button class="v9-btn" onclick="saveWebSettings()">Save Search</button><button class="v9-btn" onclick="testWebSearch()">Test Search</button></div><small>Use the WEB mode toggle or type “search the web for ...”.</small><h3 style="margin-top:16px">Voice Profile Vault</h3><div class="v9-row"><input id="v14-voice-name" placeholder="Profile name"><input id="v14-voice-file" type="file" accept="audio/*"><button class="v9-btn" onclick="enrollVoiceProfile()">Enroll authorized voice</button><button class="v9-btn" onclick="loadVoiceProfiles()">Refresh profiles</button></div><div id="v14-voice-profiles" class="v9-list"></div>`;
  body.insertBefore(card, body.firstChild);
  const lang=$('#v9-voice-lang'); if(lang) lang.value=state.voiceLang || 'en-IN';
}

function openPane(name){
  closePane(); const pane=$('#pane-'+name); if(pane) pane.classList.add('open');
  if(name==='memory') loadMemoryPane(); if(name==='skills') loadSkillsPane(); if(name==='behavior') loadBehaviorPane(); if(name==='cortex') loadCortexPane(); if(name==='agents') loadAgentsPane(); if(name==='docs') loadDocsPane(); if(name==='media') loadMediaPane(); if(name==='self'||name==='forge') loadSelfPane(); if(name==='files') loadFilesPane(); if(name==='mailbox') loadMailboxPane(); if(name==='operator') loadOperatorPane(); if(name==='settings'){ ensureSettingsEnhancements(); ensureMailboxSettingsCard(); ensureOmnipotentToggle(); loadModelList(); loadVoiceProfiles(); loadMailboxSettings(); loadSttModels(); loadMediaSettings(); } if(name==='rd') loadRdPane(); if(name==='scanner') loadScannerPane();
}
function closePane(){ $$('.pane-overlay').forEach(p=>p.classList.remove('open')); }
window.openPane=openPane; window.closePane=closePane;
async function loadMemoryPane(){ const box=$('#memory-body'); if(!box)return; try{ const d=await (await fetch('/memory')).json(); box.innerHTML=(d.memories||[]).map(m=>`<div class="v9-chip"><b>${escapeHtml(m.title)}</b><small>${escapeHtml(m.content)}</small></div>`).join('')||'<div class="empty-pane">No memories.</div>'; }catch(e){box.textContent=e.message;} }
async function loadSkillsPane(){ const box=$('#skills-body'); if(!box)return; try{ const d=await (await fetch('/skills')).json(); const learned=(d.learned||[]); const builtin=(d.builtin||d.skills||[]);
  box.innerHTML=
    skillEditorCardHtml()
    +'<div class="v9-setting-card"><h3>🛒 Skill Marketplace</h3><div style="font-size:11px;color:var(--text-dim);margin-bottom:6px">Install ready-made skills. They behave like ones SHIMS learns on its own.</div><div id="marketplace-body"><small>Loading…</small></div></div>'
    +'<div class="v9-setting-card"><h3>Learned skills & preferences</h3>'+(learned.length?learned.map(s=>`<div class="v9-chip"><b>${s.pinned?'📌 ':''}${escapeHtml(s.name)}</b><small>${escapeHtml(s.description||'')}</small> <button class="secondary" style="font-size:11px" onclick='editSkill(${JSON.stringify(JSON.stringify(s))})'>edit</button> <button class="secondary" style="font-size:11px" onclick="forgetSkill('${s.id}')">forget</button></div>`).join(''):'<small>None yet — SHIMS learns these from your chats, or create one above.</small>')+'</div>'
    +'<div class="v9-setting-card"><h3>Built-in capabilities</h3>'+builtin.map(s=>`<div class="v9-chip"><b>${escapeHtml(s.name)}</b><small>${escapeHtml(s.description||'')}</small></div>`).join('')+'</div>';
  loadMarketplace();
 }catch(e){box.textContent=e.message;} }

function skillEditorCardHtml(){
  return '<div class="v9-setting-card"><h3 id="skill-editor-title">✎ Create a skill</h3>'
    +'<input type="hidden" id="skill-edit-id">'
    +'<div class="setting-row"><label>Name</label><input id="skill-name" placeholder="e.g. Always cite sources"></div>'
    +'<div class="setting-row"><label>Summary</label><input id="skill-summary" placeholder="One line describing when to use it"></div>'
    +'<div class="setting-row" style="align-items:flex-start"><label>Instruction</label><textarea id="skill-body" rows="3" style="width:100%;resize:vertical" placeholder="What SHIMS should do…"></textarea></div>'
    +'<div class="setting-row"><label>Tags</label><input id="skill-tags" placeholder="comma,separated"></div>'
    +'<div class="v9-row" style="margin-top:6px"><button class="v9-btn" onclick="saveSkill()">Save skill</button><button class="secondary" onclick="resetSkillEditor()">Clear</button></div></div>';
}
function resetSkillEditor(){ ['skill-edit-id','skill-name','skill-summary','skill-body','skill-tags'].forEach(id=>{const el=$('#'+id); if(el) el.value='';}); const t=$('#skill-editor-title'); if(t)t.textContent='✎ Create a skill'; }
function editSkill(json){ try{ const s=JSON.parse(json); $('#skill-edit-id').value=s.id||''; $('#skill-name').value=s.name||''; $('#skill-summary').value=s.description||s.summary||''; $('#skill-body').value=s.body||''; $('#skill-tags').value=(s.tags||[]).join(', '); const t=$('#skill-editor-title'); if(t)t.textContent='✎ Edit skill'; $('#skill-name').scrollIntoView({behavior:'smooth',block:'center'}); }catch(e){ toast('Could not load skill','err'); } }
async function saveSkill(){
  const name=($('#skill-name').value||'').trim(); if(!name){ toast('Name is required','warn'); return; }
  const payload={ name, summary:($('#skill-summary').value||'').trim(), body:($('#skill-body').value||'').trim(),
    tags:($('#skill-tags').value||'').split(',').map(t=>t.trim()).filter(Boolean), skill_id:($('#skill-edit-id').value||null) };
  try{ const r=await fetch('/skills/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); const d=await r.json();
    if(d.ok){ toast('Skill saved'); resetSkillEditor(); loadSkillsPane(); } else { toast('Save failed','err'); } }
  catch(e){ toast('Save failed: '+e.message,'err'); }
}
async function loadMarketplace(){ const box=$('#marketplace-body'); if(!box)return; try{ const d=await (await fetch('/marketplace/skills')).json(); const items=d.skills||[];
  box.innerHTML = items.length? items.map(c=>`<div class="v9-chip"><b>${escapeHtml(c.name)}</b><small>${escapeHtml(c.summary||'')}</small> ${c.installed?'<span style="font-size:10px;color:var(--green)">installed</span>':`<button class="v9-btn" style="font-size:11px;padding:3px 8px" onclick="installSkill('${c.slug}')">install</button>`}</div>`).join('') : '<small>Marketplace unavailable.</small>';
 }catch(e){ box.innerHTML='<small>Marketplace unavailable.</small>'; } }
async function installSkill(slug){ try{ const r=await fetch('/marketplace/install',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slug})}); const d=await r.json(); if(d.ok){ toast('Skill installed'); loadSkillsPane(); } else { toast('Install failed','err'); } }catch(e){ toast('Install failed: '+e.message,'err'); } }
async function forgetSkill(id){ try{ await fetch('/skills/'+id,{method:'DELETE'}); loadSkillsPane(); toast('Skill forgotten'); }catch(e){ toast('Failed: '+e.message,'err'); } }
window.forgetSkill=forgetSkill; window.saveSkill=saveSkill; window.editSkill=editSkill; window.resetSkillEditor=resetSkillEditor; window.installSkill=installSkill;
async function loadFilesPane(){
  const box=$('#files-body'); if(!box)return;
  let ws='';
  try{ ws=(await (await fetch('/files/workspace')).json()).workspace||''; }catch(e){}
  box.innerHTML=`
    <div class="v9-setting-card"><h3>Workspace folder</h3>
      <div class="v9-row"><input id="ws-path" style="flex:1" value="${escapeHtml(ws)}" placeholder="C:\\\\Users\\\\you\\\\SHIMS-Workspace"><button class="v9-btn" onclick="saveWorkspace()">Set</button></div>
      <small>SHIMS only ever touches files inside this folder. Every move is reversible.</small>
    </div>
    <div class="v9-setting-card"><h3>Actions</h3>
      <div class="grid" style="grid-template-columns:1fr 1fr 1fr;gap:8px">
        <button class="v9-btn secondary" onclick="filesSummary()">Summary</button>
        <button class="v9-btn secondary" onclick="filesDuplicates()">Find duplicates</button>
        <button class="v9-btn" onclick="filesPlan()">Organize…</button>
      </div>
      <div class="v9-row" style="margin-top:8px"><input id="files-q" style="flex:1" placeholder="Search files…"><button class="v9-btn secondary" onclick="filesSearch()">Search</button></div>
    </div>
    <pre id="files-out" class="status">Set a workspace and pick an action.</pre>
    <div id="files-plan"></div>`;
}
function filesOut(o){ const el=$('#files-out'); if(el) el.textContent=typeof o==='string'?o:JSON.stringify(o,null,2); }
async function saveWorkspace(){ const p=$('#ws-path').value.trim(); if(!p)return; try{ const d=await (await fetch('/files/workspace',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:p})})).json(); filesOut(d); toast('Workspace set'); }catch(e){ filesOut('Error: '+e.message); } }
async function filesSummary(){ try{ filesOut(await (await fetch('/files/summary')).json()); }catch(e){ filesOut(e.message); } }
async function filesDuplicates(){ try{ filesOut(await (await fetch('/files/duplicates')).json()); }catch(e){ filesOut(e.message); } }
async function filesSearch(){ const q=$('#files-q').value.trim(); if(!q)return; try{ filesOut(await (await fetch('/files/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q})})).json()); }catch(e){ filesOut(e.message); } }
async function filesPlan(){
  try{
    const plan=await (await fetch('/files/organize/plan',{method:'POST'})).json();
    filesOut(plan);
    const host=$('#files-plan');
    if(plan.count>0){ host.innerHTML=`<button class="v9-btn" style="width:100%;margin-top:8px" onclick='filesApply(${JSON.stringify(plan.moves)})'>Confirm & move ${plan.count} files</button>`; }
    else { host.innerHTML='<small>Nothing to organize — folder is already tidy.</small>'; }
  }catch(e){ filesOut(e.message); }
}
async function filesApply(moves){
  try{ const d=await (await fetch('/files/organize/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({moves})})).json();
    filesOut(d); $('#files-plan').innerHTML = d.undo_id?`<button class="v9-btn secondary" style="width:100%;margin-top:8px" onclick="filesUndo('${d.undo_id}')">Undo this move</button>`:'';
    toast('Organized '+(d.applied||0)+' files');
  }catch(e){ filesOut(e.message); }
}
async function filesUndo(id){ try{ const d=await (await fetch('/files/organize/undo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({undo_id:id})})).json(); filesOut(d); $('#files-plan').innerHTML=''; toast('Restored '+(d.restored||0)+' files'); }catch(e){ filesOut(e.message); } }
window.loadFilesPane=loadFilesPane; window.saveWorkspace=saveWorkspace; window.filesSummary=filesSummary; window.filesDuplicates=filesDuplicates; window.filesSearch=filesSearch; window.filesPlan=filesPlan; window.filesApply=filesApply; window.filesUndo=filesUndo;
async function loadAgentsPane(){ const box=$('#agents-body'); if(!box)return; try{ const d=await (await fetch('/agents/list')).json(); box.innerHTML='<div class="v9-list">'+(d.agents||[]).map(a=>`<div class="v9-chip agent-pane-card"><b>${escapeHtml(a.name || a.id)}</b><small>${escapeHtml(a.role || '')}</small><small>status: ${escapeHtml(a.status || 'ready')} / approval: ${escapeHtml(a.approval_level || 'normal')}</small></div>`).join('')+'</div>'; }catch(e){box.textContent=e.message;} }
async function loadDocsPane(){ const box=$('#docs-body'); if(!box)return; try{ const d=await (await fetch('/documents')).json(); box.innerHTML='<div class="v9-setting-card"><h3>Generate PDF</h3><div class="v9-row"><input id="doc-title" placeholder="Title"><button class="v9-btn" onclick="generateDoc()">Generate PDF</button></div><textarea id="doc-content" style="width:100%;min-height:120px;background:rgba(0,0,0,.25);color:#e9fbff;border:1px solid rgba(124,240,255,.2);border-radius:8px;padding:10px" placeholder="PDF content..."></textarea></div>'+((d.documents||[]).map(x=>`<div class="v9-chip">${escapeHtml(x)}</div>`).join('')||''); }catch(e){box.textContent=e.message;} }
async function generateDoc(){ const title=$('#doc-title')?.value||'SHIMS Document'; const content=$('#doc-content')?.value||title; const d=await (await fetch('/documents/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,content})})).json(); renderMediaCard(d, null); toast('PDF generated'); }
window.generateDoc=generateDoc;
async function loadMailboxPane(){
  const box=$('#mailbox-body'); if(!box)return;
  try{
    const d=await (await fetch('/mailbox/digest')).json();
    const status=(await (await fetch('/mailbox/status')).json());
    const actions=(d.action_candidates||[]).map(a=>`<div class="v9-chip"><b>${escapeHtml(a.title||'Action')}</b><small>${escapeHtml(a.from||a.url||a.id||'')}</small></div>`).join('') || '<div class="empty-pane">No action candidates yet.</div>';
    const caps=(d.captures||[]).map(c=>`<div class="v9-chip"><b>${escapeHtml(c.title)}</b><small>${escapeHtml(c.url||c.text||c.source||'')}</small></div>`).join('') || '<div class="empty-pane">No captures yet.</div>';
    const msgs=(d.messages||[]).map(m=>`<div class="v9-chip"><b>${escapeHtml(m.subject)}</b><small>${escapeHtml(m.sender||'')}<br>${escapeHtml(m.snippet||'')}</small></div>`).join('') || '<div class="empty-pane">No messages yet.</div>';
    box.innerHTML=`<div class="v9-setting-card"><h3>Mailbox Brain</h3><div class="v9-list"><div class="v9-chip"><b>${status.counts.messages} messages</b><small>${status.counts.new_messages} new</small></div><div class="v9-chip"><b>${status.counts.captures} captures</b><small>${status.counts.new_captures} new</small></div><div class="v9-chip"><b>Gmail ${status.gmail.access_token_configured?'token configured':'consent required'}</b><small>Default scope: gmail.metadata</small></div></div></div><div class="v9-setting-card"><h3>Capture Link / Note</h3><div class="v9-row"><input id="cap-title" placeholder="Title" style="flex:1"><input id="cap-url" placeholder="URL" style="flex:1"></div><textarea id="cap-text" placeholder="Why this matters..." style="width:100%;min-height:90px;background:rgba(0,0,0,.25);color:#e9fbff;border:1px solid rgba(124,240,255,.2);border-radius:8px;padding:10px"></textarea><div class="v9-row"><button class="v9-btn" onclick="saveMailboxCapture()">Save Capture</button><button class="v9-btn" onclick="startGmailOAuth()">Gmail OAuth</button><button class="v9-btn" onclick="syncGmailMetadata()">Sync Gmail Metadata</button></div></div><div class="v9-setting-card"><h3>Import Mail Item</h3><div class="v9-row"><input id="mail-from" placeholder="From"><input id="mail-subject" placeholder="Subject"></div><textarea id="mail-snippet" placeholder="Snippet or useful body..." style="width:100%;min-height:90px;background:rgba(0,0,0,.25);color:#e9fbff;border:1px solid rgba(124,240,255,.2);border-radius:8px;padding:10px"></textarea><div class="v9-row"><button class="v9-btn" onclick="importMailboxMessage()">Import Mail</button></div></div><div class="pane-grid"><div class="card"><h3>Action Candidates</h3>${actions}</div><div class="card"><h3>Recent Captures</h3>${caps}</div><div class="card"><h3>Recent Messages</h3>${msgs}</div></div>`;
  }catch(e){box.textContent=e.message;}
}
window.loadMailboxPane=loadMailboxPane;
async function saveMailboxCapture(){
  const title=$('#cap-title')?.value||''; const url=$('#cap-url')?.value||''; const text=$('#cap-text')?.value||'';
  if(!title && !url && !text) return toast('Add a title, URL, or note first','warn');
  const d=await (await fetch('/capture/share',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,text,url,kind:url?'link':'note',source:'omni_ui'})})).json();
  toast(d.ok?'Capture saved to brain':'Capture failed', d.ok?'info':'err'); loadMailboxPane();
}
window.saveMailboxCapture=saveMailboxCapture;
async function importMailboxMessage(){
  const sender=$('#mail-from')?.value||''; const subject=$('#mail-subject')?.value||''; const snippet=$('#mail-snippet')?.value||'';
  if(!subject && !snippet) return toast('Add subject or snippet first','warn');
  const d=await (await fetch('/mailbox/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:'local',sender,subject,snippet})})).json();
  toast(d.ok?'Mail imported to brain':'Import failed', d.ok?'info':'err'); loadMailboxPane();
}
window.importMailboxMessage=importMailboxMessage;
async function startGmailOAuth(){
  const d=await (await fetch('/mailbox/oauth/start')).json();
  if(d.auth_url){ window.open(d.auth_url,'_blank'); toast('Opening Gmail OAuth consent'); }
  else toast(d.message || 'Gmail OAuth is not configured yet', 'warn');
}
window.startGmailOAuth=startGmailOAuth;
async function syncGmailMetadata(){
  const d=await (await fetch('/mailbox/gmail/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({max_results:10})})).json();
  toast(d.ok?('Synced '+d.stored+' Gmail messages'):(d.message||'Gmail OAuth needed'), d.ok?'info':'warn'); loadMailboxPane();
}
window.syncGmailMetadata=syncGmailMetadata;
function ensureMailboxSettingsCard(){
  const body=$('#pane-settings .pane-body'); if(!body || $('#mailbox-settings-card')) return;
  const card=document.createElement('div'); card.id='mailbox-settings-card'; card.className='v9-setting-card';
  card.innerHTML=`<h3>Mailbox / Gmail</h3><div class="v9-row"><button class="v9-btn" onclick="startGmailOAuth()">Open Gmail OAuth</button><button class="v9-btn" onclick="loadMailboxSettings()">Check Mailbox</button><button class="v9-btn" onclick="openPane('mailbox')">Open Mailbox</button></div><div id="mailbox-settings-status" class="v9-list"></div><small>Gmail requires explicit OAuth consent and Play Store disclosure. Shared links and pasted mail snippets work without Gmail account access.</small>`;
  body.insertBefore(card, body.firstChild);
}
window.ensureMailboxSettingsCard=ensureMailboxSettingsCard;
async function loadMailboxSettings(){
  const box=$('#mailbox-settings-status'); if(!box) return;
  try{ const d=await (await fetch('/mailbox/status')).json(); box.innerHTML=`<div class="v9-chip"><b>${d.version}</b><small>${d.counts.messages} messages Â· ${d.counts.captures} captures</small></div><div class="v9-chip"><b>Gmail ${d.gmail.client_id_configured?'client configured':'client missing'}</b><small>${escapeHtml((d.gmail.scopes||[]).join(', '))}</small></div>`; }catch(e){ box.textContent=e.message; }
}
window.loadMailboxSettings=loadMailboxSettings;
async function loadOperatorPane(){
  const box=$('#operator-body'); if(!box) return;
  try{
    const [digest, actions] = await Promise.all([
      fetch('/operator/digest?limit=20').then(r=>r.json()),
      fetch('/actions?limit=20').then(r=>r.json()).catch(()=>({actions:[]}))
    ]);
    const recs=(digest.recommendations||[]).map(r=>`<div class="v9-chip"><b>${escapeHtml(r.title||'Recommendation')}</b><small>${escapeHtml(r.reason||'')}<br>${escapeHtml(r.type||'')}</small></div>`).join('') || '<div class="empty-pane">No recommendations yet.</div>';
    const blockers=(digest.blockers||[]).map(b=>`<div class="v9-chip action-proof"><b>${escapeHtml(b.title||b.type||'Blocker')}</b><small>${escapeHtml(b.reason||b.status||'')}<br>${escapeHtml(b.action_id||'')}</small>${b.action_id?`<button class="v9-btn" onclick="verifyAction('${escapeHtml(b.action_id)}')">Verify</button>`:''}</div>`).join('') || '<div class="empty-pane">No blockers.</div>';
    const actionRows=(actions.actions||[]).slice(0,8).map(a=>`<div class="v9-chip action-proof"><b>${escapeHtml(a.title||a.action_type)}</b><small>${escapeHtml(a.status||'')} / ${escapeHtml(a.action_type||'')}<br>${escapeHtml((a.ledger_hash||a.record_hash||'').slice(0,22))}</small><button class="v9-btn" onclick="verifyAction('${escapeHtml(a.id)}')">Verify</button></div>`).join('') || '<div class="empty-pane">No actions recorded yet.</div>';
    box.innerHTML=`<div class="v9-setting-card"><h3>Operator Digest</h3><div class="v9-list"><div class="v9-chip"><b>${(digest.recommendations||[]).length} recommendations</b><small>${(digest.blockers||[]).length} blockers</small></div><div class="v9-chip"><b>Trust ${escapeHtml(digest.trust?.trust_level||'draft')}</b><small>${escapeHtml(digest.confidence?.reason||'')}</small></div><div class="v9-chip"><b>Telemetry</b><small>${digest.telemetry?.error_count||0} recent errors</small></div></div><div class="v9-row"><button class="v9-btn" onclick="refreshOperatorDigest()">Refresh + Record</button><button class="v9-btn" onclick="runReliabilityEvals()">Run Evals</button></div></div><div class="pane-grid"><div class="card"><h3>Next Actions</h3>${recs}</div><div class="card"><h3>Blockers</h3>${blockers}</div><div class="card"><h3>Action Ledger</h3>${actionRows}</div></div><div class="v9-setting-card"><h3>Campaign Draft</h3><div class="v9-row"><input id="campaign-objective" placeholder="Objective" value="Sell SHIMS as a daily AI operator"><input id="campaign-audience" placeholder="Audience" value="business owners and operators"></div><div class="v9-row"><input id="campaign-offer" placeholder="Offer" value="verification-first AI operator demo"><button class="v9-btn" onclick="planCampaignDraft()">Plan Campaign</button></div><div id="campaign-output" class="draft-output"></div></div><div class="v9-setting-card"><h3>Calendar ICS</h3><div class="v9-row"><input id="ics-title" placeholder="Meeting title" value="SHIMS follow-up"><input id="ics-start" placeholder="2026-06-01T10:00:00+05:30"><input id="ics-minutes" type="number" value="30"></div><div class="v9-row"><input id="ics-desc" placeholder="Notes"><button class="v9-btn" onclick="createIcsDraft()">Create ICS</button></div><div id="ics-output" class="draft-output"></div></div>`;
  }catch(e){ box.textContent=e.message; }
}
window.loadOperatorPane=loadOperatorPane;
async function refreshOperatorDigest(){ try{ const d=await (await fetch('/operator/digest?record=true')).json(); toast(d.ok?'Operator digest recorded':'Operator digest failed', d.ok?'info':'warn'); loadOperatorPane(); }catch(e){ toast('Operator refresh failed: '+e.message,'err'); } }
window.refreshOperatorDigest=refreshOperatorDigest;
async function verifyAction(id){
  try{
    const d=await (await fetch('/actions/'+encodeURIComponent(id)+'/verify')).json();
    toast(d.ok?'Action verified: '+String(d.ledger_hash||'').slice(0,12):'Action verification failed', d.ok?'info':'err');
    feed('verify '+id+': '+(d.ok?'ok':'failed'), d.ok?'info':'err');
  }catch(e){ toast('Verify failed: '+e.message,'err'); }
}
window.verifyAction=verifyAction;
async function runReliabilityEvals(){
  try{
    const d=await (await fetch('/evals/run',{method:'POST'})).json();
    toast(`Reliability evals: ${d.passed}/${d.total}`, d.ok?'info':'err');
    loadOperatorPane();
  }catch(e){ toast('Evals failed: '+e.message,'err'); }
}
window.runReliabilityEvals=runReliabilityEvals;
async function planCampaignDraft(){
  const payload={objective:$('#campaign-objective')?.value||'', audience:$('#campaign-audience')?.value||'', offer:$('#campaign-offer')?.value||''};
  const box=$('#campaign-output'); if(box) box.textContent='Planning...';
  try{
    const d=await (await fetch('/campaigns/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})).json();
    if(box) box.innerHTML=`<div class="v9-chip"><b>${escapeHtml(d.brief?.positioning||'Campaign draft')}</b><small>${escapeHtml(d.drafts?.email_body||'')}</small></div><div class="v9-chip"><b>Approval gate</b><small>${escapeHtml(d.policy||'External actions require approval.')}</small></div>`;
    toast('Campaign draft ready');
  }catch(e){ if(box) box.textContent=e.message; toast('Campaign failed: '+e.message,'err'); }
}
window.planCampaignDraft=planCampaignDraft;
async function createIcsDraft(){
  const payload={title:$('#ics-title')?.value||'SHIMS follow-up', start:$('#ics-start')?.value||undefined, duration_minutes:parseInt($('#ics-minutes')?.value||'30',10), description:$('#ics-desc')?.value||''};
  const box=$('#ics-output'); if(box) box.textContent='Creating ICS...';
  try{
    const d=await (await fetch('/calendar/ics',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})).json();
    if(box) box.innerHTML=`<div class="v9-chip"><b>${escapeHtml(d.title||'ICS created')}</b><small>${escapeHtml(d.start||'')} to ${escapeHtml(d.end||'')}<br>${escapeHtml(d.policy||'')}</small>${d.file_url?`<a class="v9-btn" href="${escapeHtml(d.file_url)}" target="_blank">Download ICS</a>`:''}</div>`;
    toast('Calendar ICS ready');
  }catch(e){ if(box) box.textContent=e.message; toast('ICS failed: '+e.message,'err'); }
}
window.createIcsDraft=createIcsDraft;
async function loadMediaPane(){ await refreshMediaLibrary(); }
async function refreshMediaLibrary(){ const box=$('#mf-gallery'); if(!box)return; try{ const d=await (await fetch('/media/library')).json(); box.innerHTML=''; (d.items||[]).forEach(it=>renderMediaCard(it,null)); }catch(e){box.textContent=e.message;} }
async function generateMedia(){ const kind=document.querySelector('.mf-pill.on')?.dataset.type || document.querySelector('.mf-pill.active')?.dataset.type || 'image'; const prompt=$('#mf-prompt')?.value || 'SHIMS generated media'; const theme=$('#mf-theme')?.value||'auto'; const quality=$('#mf-quality')?.value||'standard'; const provider=$('#mf-provider')?.value||undefined; const prog=$('#mf-progress'); if(prog) prog.textContent='Generating '+kind+'...'; const d=await (await fetch('/media/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,prompt,theme,quality,provider})})).json(); if(prog) prog.textContent=d.job_id&&!(d.file_url||d.url)?'Job started' : 'Done'; renderMediaCard(d,null); }
window.generateMedia=generateMedia;
async function loadSelfPane(){
  const box=$('#self-body')||$('#forge-body'); if(!box)return;
  try{
    const [tasks,evo,props] = await Promise.all([
      fetch('/tasks').then(r=>r.json()).catch(()=>({tasks:[]})),
      fetch('/evolution/status').then(r=>r.json()).catch(()=>null),
      fetch('/evolution/proposals').then(r=>r.json()).catch(()=>({proposals:[]}))
    ]);
    let html = '<div class="v9-setting-card"><h3>Self-Evolution Lab v13 — Real Patch Pipeline</h3><p style="color:#9eb6c1;font-size:12px">Generated patches are now real proposal files: propose -> sandbox validate -> human approve -> apply -> rollback on failure. The safety harness itself is immutable.</p><div class="v9-row"><button class="v9-btn" onclick="runReflection()">Run Reflection Now</button></div><div id="evo-status"></div></div>';
    html += '<div class="v9-setting-card"><h3>Create Patch Proposal</h3><input id="evo-path" placeholder="tests/test_my_fix.py or frontend/shims_interface.html" style="width:100%;margin-bottom:8px"><textarea id="evo-content" placeholder="Paste complete new file content here. SHIMS will diff, sandbox-test, then wait for approval." style="width:100%;min-height:130px;background:rgba(0,0,0,.25);color:#e9fbff;border:1px solid rgba(124,240,255,.2);border-radius:8px;padding:10px"></textarea><div class="v9-row"><input id="evo-reason" placeholder="Reason for patch"><select id="evo-scope"><option value="code">code</option><option value="prompt">prompt</option><option value="skill">skill</option><option value="ui_text">ui_text</option></select><button class="v9-btn" onclick="createEvolutionProposal()">Propose Patch</button></div></div>';
    if(evo && evo.daily_lessons){
      const l=evo.daily_lessons;
      html += `<div class="v9-chip"><b>Daily Lessons</b><small>events: ${l.event_count||0} · errors: ${l.error_count||0} · docs: ${l.document_ledger_count||0}<br>p95: ${(l.latency||{}).p95_ms||0}ms · p99: ${(l.latency||{}).p99_ms||0}ms</small></div>`;
      (l.prompt_injection||[]).slice(0,5).forEach(x=>{ html += `<div class="v9-chip"><b>Lesson</b><small>${escapeHtml(x)}</small></div>`; });
    }
    html += '<h3 style="margin:16px 0 8px;color:#7cf0ff">Patch Queue</h3>';
    if((props.proposals||[]).length){
      html += (props.proposals||[]).map(p=>`<div class="v9-chip"><b>${escapeHtml(p.proposal_id||p.id)} · ${escapeHtml(p.status)} · ${escapeHtml(p.relative_path)}</b><small>${escapeHtml(p.reason||'no reason')}<br>old ${escapeHtml((p.old_sha256||'').slice(0,12))} -> new ${escapeHtml((p.new_sha256||'').slice(0,12))}</small><div class="v9-row"><button class="v9-btn" onclick="validateProposal('${escapeHtml(p.proposal_id||p.id)}')">Sandbox Test</button><button class="v9-btn" onclick="approveProposal('${escapeHtml(p.proposal_id||p.id)}')">Approve</button><button class="v9-btn" onclick="applyProposal('${escapeHtml(p.proposal_id||p.id)}')">Apply</button></div></div>`).join('');
    } else html += '<div class="empty-pane">No patch proposals yet.</div>';
    html += (tasks.tasks||[]).map(t=>`<div class="v9-chip"><b>${escapeHtml(t.title)}</b><small>${escapeHtml(t.status)}<br>${escapeHtml(t.diff||'')}</small></div>`).join('');
    box.innerHTML=html;
    const evoRow = box.querySelector('#evo-status')?.previousElementSibling;
    if(evoRow && !evoRow.querySelector('[data-evo-capability]')){
      const selfTests=document.createElement('button'); selfTests.className='v9-btn'; selfTests.dataset.evoSelfCheck='tests'; selfTests.textContent='Self-Check Tests'; selfTests.onclick=()=>runEvolutionSelfCheck('tests'); evoRow.appendChild(selfTests);
      const selfLint=document.createElement('button'); selfLint.className='v9-btn'; selfLint.dataset.evoSelfCheck='lint'; selfLint.textContent='Self-Check Lint'; selfLint.onclick=()=>runEvolutionSelfCheck('lint'); evoRow.appendChild(selfLint);
      const dry=document.createElement('button'); dry.className='v9-btn'; dry.dataset.evoCapability='dry'; dry.textContent='Pipeline Dry-Run'; dry.onclick=()=>runEvolutionCapabilityCheck(false); evoRow.appendChild(dry);
    }
  }catch(e){box.textContent=e.message;}
}
async function runReflection(){ try{ const d=await (await fetch('/evolution/reflect',{method:'POST'})).json(); toast(d.ok?'Reflection complete':'Reflection failed', d.ok?'info':'warn'); loadSelfPane(); }catch(e){ toast('Reflection failed: '+e.message,'err'); } }
window.runReflection=runReflection;

async function runEvolutionCapabilityCheck(apply){
  if(apply){ toast('Live apply from capability check is disabled. Use the patch queue to approve and apply proposals.','warn'); return; }
  try{
    const d=await (await fetch('/evolution/capability-check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({apply:false,approved_by:'ui-human',revision:'ui-'+Date.now()})})).json();
    const ok=(d.targets||[]).filter(x=>x.ok).length; const total=(d.targets||[]).length;
    toast(d.ok?`Pipeline dry-run passed (${ok}/${total})`:'Pipeline dry-run failed: '+(d.message||d.status), d.ok?'info':'err');
    loadSelfPane();
  }catch(e){ toast('Capability check failed: '+e.message,'err'); }
}
window.runEvolutionCapabilityCheck=runEvolutionCapabilityCheck;

async function runEvolutionSelfCheck(scope){
  const info=$('#desktop-info') || $('#self-body');
  try{
    toast(`Running self-check (${scope})…`,'info');
    const d=await (await fetch('/evolution/self-check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope})})).json();
    if(d.ok && d.proposal && d.proposal.proposal_id){
      toast(`Self-check created proposal ${d.proposal.proposal_id}. Review in Self-Evolution pane.`,'info');
    }else if(d.ok){
      toast(d.message || `Self-check (${scope}) complete — no patch needed.`,'info');
    }else{
      toast('Self-check failed: '+(d.error||d.message||d.status),'err');
    }
    loadSelfPane();
  }catch(e){ toast('Self-check error: '+e.message,'err'); }
}
window.runEvolutionSelfCheck=runEvolutionSelfCheck;

async function createEvolutionProposal(){
  const relative_path=$('#evo-path')?.value.trim(); const new_content=$('#evo-content')?.value||''; const reason=$('#evo-reason')?.value||'manual patch'; const scope=$('#evo-scope')?.value||'code';
  if(!relative_path||!new_content.trim()) return toast('Patch path and content required','warn');
  const r=await fetch('/evolution/propose',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({relative_path,new_content,reason,scope,author:'ui-human'})});
  const d=await r.json(); toast(d.ok?'Patch proposed: '+(d.proposal_id||d.id):'Patch blocked: '+(d.message||d.status), d.ok?'info':'err'); loadSelfPane();
}
async function validateProposal(id){ const d=await (await fetch('/evolution/validate/'+encodeURIComponent(id),{method:'POST'})).json(); toast(d.ok?'Sandbox tests passed':'Validation failed: '+d.status, d.ok?'info':'err'); loadSelfPane(); }
async function approveProposal(id){ const d=await (await fetch('/evolution/approve/'+encodeURIComponent(id),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({approved_by:'ui-human',note:'approved from SHIMS cockpit'})})).json(); toast(d.ok?'Approved':'Approval failed: '+d.status, d.ok?'info':'err'); loadSelfPane(); }
async function applyProposal(id){ if(!confirm('Apply this validated patch to the live SHIMS files now?')) return toast('Patch apply cancelled','warn'); const d=await (await fetch('/evolution/apply/'+encodeURIComponent(id),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({approved_by:'ui-human',approval_phrase:'I_APPROVE_SHIMS_PATCH'})})).json(); toast(d.ok?'Patch applied':'Apply failed: '+(d.message||d.status), d.ok?'info':'err'); loadSelfPane(); loadSandboxSidebar(); }
window.createEvolutionProposal=createEvolutionProposal; window.validateProposal=validateProposal; window.approveProposal=approveProposal; window.applyProposal=applyProposal;

/* =======================================================================
   R&D Brain Pane
   ======================================================================== */
function loadRdPane(){ const box=$('#rd-body'); if(!box) return; switchRdTab('patents'); }
window.loadRdPane=loadRdPane;

async function loadEnterpriseDashboard(){
  const box=$('#enterprise-dashboard');
  if(!box) return;
  try{
    const d=await (await fetch('/enterprise/dashboard')).json();
    if(!d.ok || !d.enterprise){
      box.innerHTML='<div style="color:var(--text-dim)">Dashboard unavailable.</div>'; return;
    }
    const e=d.enterprise;
    let html='';
    if(e.departments){
      html+='<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:6px;margin-top:4px">';
      for(const [dept, info] of Object.entries(e.departments)){
        const count=(info&&info.count!==undefined)?info.count:(Array.isArray(info)?info.length:'—');
        const status=(info&&info.status)?info.status:'';
        html+=`<div style="background:rgba(0,0,0,.2);padding:5px 7px;border-radius:6px"><div style="color:var(--cyan)">${escapeHtml(dept.toUpperCase())}</div><div style="font-size:12px">${count} ${escapeHtml(status)}</div></div>`;
      }
      html+='</div>';
    }else{
      html='<pre style="font-size:10px;white-space:pre-wrap;margin:0">'+escapeHtml(JSON.stringify(e,null,2).slice(0,800))+'</pre>';
    }
    box.innerHTML=html;
  }catch(exc){ if(box) box.innerHTML='<div style="color:var(--red)">Dashboard error</div>'; }
}
window.loadEnterpriseDashboard=loadEnterpriseDashboard;

function togglePanelSection(id){
  const body=$('#body-'+id); const chevron=$('#chevron-'+id); if(!body || !chevron) return;
  body.classList.toggle('hidden');
  chevron.textContent = body.classList.contains('hidden') ? '▸' : '▾';
  if(id==='plans' && !body.classList.contains('hidden')) loadSchedulerPane();
  if(id==='enterprise' && !body.classList.contains('hidden')) loadEnterpriseDashboard();
  if(id==='feed' && !body.classList.contains('hidden')){ /* feed auto-updates */ }
}
window.togglePanelSection=togglePanelSection;

async function desktopAction(action){
  const info=$('#desktop-info'); if(info) info.textContent='Running…';
  try{
    let result={ok:true, note:''};
    if(action==='screenshot'){
      const d=await (await fetch('/api/desktop/screenshot',{method:'POST',headers:{'Content-Type':'application/json'}})).json();
      result={ok:d.ok, note:d.url?('Screenshot saved: '+d.url):('Screenshot failed: '+(d.error||''))};
    }else if(action==='clipboard'){
      const text=await navigator.clipboard.readText().catch(()=>'');
      result={ok:!!text, note:text?'Clipboard: '+text.slice(0,120):'Clipboard empty or denied'};
    }else if(action==='volume'){
      result={ok:true, note:'Use system volume keys or run a shell command via chat'};
    }else if(action==='notify'){
      if('Notification' in window && Notification.permission==='granted'){
        new Notification('SHIMS', {body:'Desktop notification test'});
        result={ok:true, note:'Notification sent'};
      }else if('Notification' in window){
        const perm=await Notification.requestPermission();
        result={ok:perm==='granted', note:perm==='granted'?'Notifications enabled':'Notifications denied'};
      }else{
        result={ok:false, note:'Notifications not supported'};
      }
    }
    if(info) info.textContent = result.ok ? result.note : ('Error: '+result.note);
  }catch(e){ if(info) info.textContent='Error: '+e.message; }
}
window.desktopAction=desktopAction;

async function checkBridgeStatus(){
  const info=$('#desktop-info'); if(info) info.textContent='Checking bridge…';
  try{
    const d=await (await fetch('/api/desktop/bridge/status')).json();
    info.textContent = d.connected ? 'Bridge: connected ✅' : ('Bridge: offline ❌ '+(d.error||''));
  }catch(e){ if(info) info.textContent='Bridge check error: '+e.message; }
}
window.checkBridgeStatus=checkBridgeStatus;

async function launchAndConnectBridge(){
  const info=$('#desktop-info'); if(info) info.textContent='Launching bridge…';
  try{
    const d=await (await fetch('/api/desktop/bridge/launch',{method:'POST'})).json();
    if(d.ok && d.connected){
      info.textContent = (d.started ? 'Bridge launched ✅' : 'Bridge already running ✅') +
        ' | token: ' + (d.token||'').slice(0,8)+'…';
    }else{
      info.textContent = 'Bridge launch failed ❌ ' + (d.error||'Unknown error');
    }
  }catch(e){ if(info) info.textContent='Bridge launch error: '+e.message; }
}
window.launchAndConnectBridge=launchAndConnectBridge;

async function runBridgeCommand(){
  const input=$('#bridgeCommand'); const info=$('#desktop-info');
  if(!input || !input.value.trim()) return;
  if(info) info.textContent='Running on desktop…';
  try{
    const d=await (await fetch('/api/desktop/bridge/command',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({type:'shell', command:input.value.trim(), timeout:60})
    })).json();
    const result = d.result || d;
    if(result.ok){
      const out=(result.stdout||'')+'\n'+(result.stderr||'');
      info.textContent = out.trim() || ('Exit code: '+result.returncode);
    }else{
      info.textContent = 'Error: '+(result.error||'Unknown');
    }
  }catch(e){ if(info) info.textContent='Error: '+e.message; }
}
window.runBridgeCommand=runBridgeCommand;

async function findBridgeFile(name){
  const info=$('#desktop-info'); if(info) info.textContent='Searching desktop for '+name+'…';
  try{
    const d=await (await fetch('/api/desktop/bridge/command',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({type:'find_file', name:name})
    })).json();
    const result = d.result || d;
    if(result.ok){
      const matches=result.matches||[];
      info.textContent = matches.length ? 'Found:\n'+matches.join('\n') : 'No matches';
    }else{
      info.textContent = 'Error: '+(result.error||'Unknown');
    }
  }catch(e){ if(info) info.textContent='Error: '+e.message; }
}
window.findBridgeFile=findBridgeFile;


async function loadEnterpriseStatus(){
  const el=$('#enterprise-status'); if(!el) return;
  try{
    const d=await (await fetch('/enterprise/status')).json();
    state.enterpriseEnabled = d.enabled !== false;
    if(d.enabled===false){
      // Enterprise integration is not configured; hide the bridge UI entirely.
      const hdr=$('#header-enterprise'); if(hdr) hdr.style.display='none';
      const body=$('#body-enterprise'); if(body) body.style.display='none';
      const link=$('#link-enterprise'); if(link) link.style.display='none';
      const strip=$('#enterprise-status'); if(strip) strip.style.display='none';
      return;
    }
    if(d.ok){
      el.innerHTML='<span style="color:#74ffb9">●</span> Enterprise connected · '+escapeHtml(d.url||'');
      connectEnterpriseEvents();
    }else{
      el.innerHTML='<span style="color:#ff5a7a">●</span> Enterprise offline · '+escapeHtml(d.detail||'not reachable');
    }
  }catch(e){ el.innerHTML='<span style="color:#ff5a7a">●</span> Enterprise unreachable'; }
}
window.loadEnterpriseStatus=loadEnterpriseStatus;

let _enterpriseWs = null;
function connectEnterpriseEvents(){
  if(_enterpriseWs && (_enterpriseWs.readyState===WebSocket.OPEN || _enterpriseWs.readyState===WebSocket.CONNECTING)) return;
  const proto = location.protocol==='https:'?'wss:':'ws:';
  const url = proto+'//'+location.host+'/ws/enterprise';
  const status=$('#enterprise-ws-status');
  try{
    _enterpriseWs = new WebSocket(url);
    _enterpriseWs.onopen = ()=>{ if(status) status.textContent='live'; };
    _enterpriseWs.onmessage = (ev)=>{
      let msg={}; try{ msg=JSON.parse(ev.data); }catch(e){}
      if(msg.type==='pong' || msg.type==='ack') return;
      const box=$('#enterprise-events'); if(!box) return;
      const cat = msg.category || 'info';
      const color = cat==='danger'||cat==='error'?'#ff5a7a':cat==='warning'?'#ffb86c':cat==='success'?'#74ffb9':'#6ecfff';
      const tile = document.createElement('div');
      tile.style.cssText='margin:3px 0;padding:4px 6px;background:rgba(0,0,0,.2);border-radius:6px;border-left:3px solid '+color;
      tile.innerHTML='<div style="font-weight:600;color:'+color+'">'+escapeHtml(msg.title||msg.type||'Event')+'</div><div>'+escapeHtml(msg.message||'')+'</div><div style="font-size:9px;color:var(--text-dim);margin-top:2px">'+(msg.entity_type||'')+' '+(msg.entity_id||'')+'</div>';
      box.insertBefore(tile, box.firstChild);
      while(box.children.length>20) box.removeChild(box.lastChild);
    };
    _enterpriseWs.onclose = ()=>{ if(status) status.textContent='reconnecting…'; setTimeout(connectEnterpriseEvents, 5000); };
    _enterpriseWs.onerror = ()=>{ if(status) status.textContent='error'; };
  }catch(e){ if(status) status.textContent='unsupported'; }
}
window.connectEnterpriseEvents=connectEnterpriseEvents;

async function enterpriseAction(cmd){
  const b=pushBubble('user','/enterprise '+cmd);
  const bubble=pushBubble('assistant','🏭 **Enterprise** · '+escapeHtml(cmd)+'\n\n_working…_');
  try{
    const d=await (await fetch('/enterprise/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:cmd,payload:{}})})).json();
    setBubble(bubble, d.status==='ok'||d.ok ? '🏭 **Enterprise response**\n\n```json\n'+JSON.stringify(d,null,2).slice(0,3000)+'\n```' : '❌ **Enterprise error**\n\n'+escapeHtml(d.detail||JSON.stringify(d)));
  }catch(e){ setBubble(bubble, '❌ Enterprise request failed: '+escapeHtml(e.message)); }
}
window.enterpriseAction=enterpriseAction;

async function loadSchedulerPane(){
  const statusEl=$('#scheduler-status');
  const listEl=$('#scheduler-list');
  if(!statusEl || !listEl) return;
  try{
    const plans=await (await fetch('/api/plans?limit=10')).json();
    const sched=await (await fetch('/api/schedule?enabled_only=true&limit=20')).json();
    const activePlans=(plans.plans||[]).filter(p=>p.status==='active');
    const upcoming=(sched.tasks||[]).filter(t=>t.enabled!==false);
    let html='';
    if(activePlans.length){
      html+='<div style="margin-bottom:6px"><b>Active plans:</b></div>';
      html+=activePlans.map(p=>`<div style="margin:3px 0;padding:4px 6px;background:rgba(0,0,0,.2);border-radius:6px"><div style="color:var(--cyan)">${escapeHtml(p.goal.slice(0,60))}</div><div style="font-size:10px;color:var(--text-dim)">${p.steps.filter(s=>s.status==='done').length}/${p.steps.length} done · <a href="#" style="color:var(--amber)" onclick="runPlanWave('${p.plan_id}');return false">run wave</a> · <a href="#" style="color:var(--red)" onclick="cancelPlan('${p.plan_id}');return false">cancel</a></div></div>`).join('');
    }
    if(upcoming.length){
      html+='<div style="margin:8px 0 6px"><b>Scheduled:</b></div>';
      html+=upcoming.map(t=>`<div style="margin:3px 0;padding:4px 6px;background:rgba(0,0,0,.2);border-radius:6px"><div>${escapeHtml(t.title)}</div><div style="font-size:10px;color:var(--text-dim)">${t.schedule_type} · ${new Date((t.next_run||0)*1000).toLocaleString()} · <a href="#" style="color:var(--red)" onclick="cancelSchedule('${t.task_id}');return false">cancel</a></div></div>`).join('');
    }
    listEl.innerHTML=html || '<div style="color:var(--text-dim)">No active plans or upcoming tasks.</div>';
    statusEl.textContent = activePlans.length ? `${activePlans.length} active plan(s)` : 'No active plans';
  }catch(e){ statusEl.textContent='Scheduler unavailable'; listEl.innerHTML=''; }
}
window.loadSchedulerPane=loadSchedulerPane;

async function runPlanWave(planId){
  try{
    const d=await (await fetch('/api/plans/run-wave',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plan_id:planId})})).json();
    toast(d.ok ? 'Plan wave executed' : ('Plan wave failed: '+escapeHtml(d.error||'')), d.ok?'info':'err');
    loadSchedulerPane();
  }catch(e){ toast('Plan wave error: '+e.message,'err'); }
}
window.runPlanWave=runPlanWave;

async function cancelPlan(planId){
  try{
    await fetch('/api/plans/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plan_id:planId})});
    toast('Plan cancelled','warn'); loadSchedulerPane();
  }catch(e){ toast('Cancel failed: '+e.message,'err'); }
}
window.cancelPlan=cancelPlan;

async function cancelSchedule(taskId){
  try{
    await fetch('/api/schedule/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_id:taskId})});
    toast('Schedule cancelled','warn'); loadSchedulerPane();
  }catch(e){ toast('Cancel failed: '+e.message,'err'); }
}
window.cancelSchedule=cancelSchedule;

function showScheduleModal(){
  const title=prompt('Task title:'); if(!title) return;
  const scheduleType=prompt('Schedule type (once / interval / cron):','once'); if(!scheduleType) return;
  const when=prompt('When?\n• once: ISO datetime\n• interval: seconds\n• cron: "M H * * *"','2026-12-31T23:59:00'); if(!when) return;
  const actionType=prompt('Action type (tool / message):','message'); if(!actionType) return;
  const payloadRaw=prompt('Payload JSON:\n• message: {"message":"reminder text"}\n• tool: {"tool":"shell.run","args":{"command":"echo hi"}}','{"message":"Reminder from SHIMS"}'); if(!payloadRaw) return;
  try{
    const payload=JSON.parse(payloadRaw);
    fetch('/api/schedule',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,schedule_type:scheduleType,when,action_type:actionType,payload})}).then(r=>r.json()).then(d=>{
      toast(d.ok?'Scheduled':'Schedule failed: '+escapeHtml(d.error||''), d.ok?'info':'err'); loadSchedulerPane();
    });
  }catch(e){ toast('Invalid JSON payload','err'); }
}
window.showScheduleModal=showScheduleModal;

function switchRdTab(tab){
  $$('.rd-tab').forEach(t=>t.classList.toggle('on', t.dataset.rdTab===tab));
  $$('.rd-panel').forEach(p=>p.classList.toggle('on', p.id==='rd-'+tab));
}
window.switchRdTab=switchRdTab;

async function searchPatents(){
  const query=$('#rd-patent-query')?.value?.trim(); if(!query) return toast('Enter a patent query','warn');
  const box=$('#rd-patent-results'); box.innerHTML='<div class="empty-pane">Searching patents...</div>';
  try{
    const d=await (await fetch('/api/rd/patents',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,top_k:10})})).json();
    if(!d.ok || !d.results || !d.results.length){ box.innerHTML='<div class="empty-pane">No patents found.</div>'; return; }
    box.innerHTML=d.results.map(p=>`<div class="patent-card"><b>${escapeHtml(p.patent_number||'N/A')}</b> <span style="color:var(--cyan)">${escapeHtml((p.relevance_score||0).toString())}</span><div style="font-size:12px;color:#e9fbff;margin-top:4px">${escapeHtml(p.title||'')}</div><small>Assignee: ${escapeHtml(p.assignee||'—')} · Filed: ${escapeHtml(p.filing_date||'—')}</small><small style="display:block;margin-top:4px">${escapeHtml((p.abstract||'').slice(0,280))}</small></div>`).join('');
  }catch(e){ box.innerHTML='<div class="empty-pane">Search failed: '+escapeHtml(e.message)+'</div>'; }
}
window.searchPatents=searchPatents;

async function synthesizeProcess(){
  const target=$('#rd-synth-target')?.value?.trim(); const mats=$('#rd-synth-materials')?.value?.trim().split(',').map(s=>s.trim()).filter(Boolean); const cons=$('#rd-synth-constraints')?.value?.trim();
  if(!target||!mats.length) return toast('Target product and raw materials required','warn');
  const box=$('#rd-synth-results'); box.innerHTML='<div class="empty-pane">Synthesizing process...</div>';
  try{
    const d=await (await fetch('/api/rd/synthesize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target_product:target,raw_materials:mats,constraints:cons})})).json();
    if(!d.ok){ box.innerHTML='<div class="empty-pane">Synthesis failed.</div>'; return; }
    const p=d.process; let html=`<div class="patent-card"><b>Process for ${escapeHtml(p.target_product)}</b><div>Overall yield: ${escapeHtml((p.overall_yield_pct||0).toString())}%</div><div>Safety: ${escapeHtml(p.safety_notes||'—')}</div><div>Env: ${escapeHtml(p.environmental_notes||'—')}</div>`;
    html+='<div style="margin-top:8px"><b>Steps:</b></div>'+(p.steps||[]).map((s,i)=>`<div class="v9-chip" style="margin:4px 0"><b>Step ${i+1}</b><small>${escapeHtml(s.description||'')} · T=${escapeHtml((s.temperature_c||0).toString())}°C · P=${escapeHtml((s.pressure_bar||0).toString())}bar · ${escapeHtml((s.time_hours||0).toString())}h · Yield=${escapeHtml((s.expected_yield_pct||0).toString())}%</small></div>`).join('');
    html+='</div>'; box.innerHTML=html;
  }catch(e){ box.innerHTML='<div class="empty-pane">Synthesis failed: '+escapeHtml(e.message)+'</div>'; }
}
window.synthesizeProcess=synthesizeProcess;

async function priceMaterials(){
  const mats=$('#rd-price-materials')?.value?.trim().split(',').map(s=>s.trim()).filter(Boolean); if(!mats.length) return toast('Enter at least one material','warn');
  const box=$('#rd-price-results'); box.innerHTML='<div class="empty-pane">Fetching pricing...</div>';
  try{
    const d=await (await fetch('/api/rd/pricing',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({materials:mats})})).json();
    if(!d.ok || !d.pricing || !d.pricing.length){ box.innerHTML='<div class="empty-pane">No pricing data found.</div>'; return; }
    box.innerHTML=d.pricing.map(p=>`<div class="patent-card"><b>${escapeHtml(p.material)}</b> <span style="color:var(--cyan)">₹${escapeHtml((p.price_per_kg_inr||0).toFixed(2))}</span><small>Region: ${escapeHtml(p.supplier_region||'—')} · Date: ${escapeHtml(p.price_date||'—')} · Trend: ${escapeHtml(p.trend||'—')}</small></div>`).join('');
  }catch(e){ box.innerHTML='<div class="empty-pane">Pricing failed: '+escapeHtml(e.message)+'</div>'; }
}
window.priceMaterials=priceMaterials;

async function predictYield(){
  const desc=$('#rd-yield-desc')?.value?.trim(); if(!desc) return toast('Enter a process description','warn');
  const box=$('#rd-yield-results'); box.innerHTML='<div class="empty-pane">Predicting yield...</div>';
  try{
    const d=await (await fetch('/api/rd/yield',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({process_description:desc})})).json();
    if(!d.ok){ box.innerHTML='<div class="empty-pane">Prediction failed.</div>'; return; }
    const p=d.prediction; box.innerHTML=`<div class="patent-card"><b>Predicted Yield: ${escapeHtml((p.predicted_yield_pct||0).toString())}%</b> <span style="color:var(--cyan)">Confidence: ${escapeHtml(p.confidence||'—')}</span><small>Key variables: ${escapeHtml((p.key_variables||[]).join(', ')||'—')}</small><small style="display:block;margin-top:4px">Suggestions: ${escapeHtml((p.optimization_suggestions||[]).join('; ')||'—')}</small></div>`;
  }catch(e){ box.innerHTML='<div class="empty-pane">Prediction failed: '+escapeHtml(e.message)+'</div>'; }
}
window.predictYield=predictYield;

async function suggestPurity(){
  const prod=$('#rd-purity-product')?.value?.trim(); const form=$('#rd-purity-form')?.value?.trim()||'API'; if(!prod) return toast('Enter a product name','warn');
  const box=$('#rd-purity-results'); box.innerHTML='<div class="empty-pane">Suggesting methods...</div>';
  try{
    const d=await (await fetch('/api/rd/purity',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product:prod,dosage_form:form})})).json();
    if(!d.ok || !d.methods || !d.methods.length){ box.innerHTML='<div class="empty-pane">No methods found.</div>'; return; }
    box.innerHTML=d.methods.map(m=>`<div class="patent-card"><b>${escapeHtml(m.test_name||'—')}</b> <span style="color:var(--cyan)">${escapeHtml(m.method||'—')}</span><small>Spec: ${escapeHtml(m.specification||'—')} · Ref: ${escapeHtml(m.reference_standard||'—')}</small></div>`).join('');
  }catch(e){ box.innerHTML='<div class="empty-pane">Suggestion failed: '+escapeHtml(e.message)+'</div>'; }
}
window.suggestPurity=suggestPurity;

function showSttBanner(msg){ const b=$('#stt-banner'); const m=$('#stt-banner-msg'); if(m)m.textContent=msg; if(b)b.classList.add('show'); }
window.showSttBanner=showSttBanner;

async function testConnection(pid){
  try{
    const input=$('#key-'+pid); const sel=$('#model-'+pid);
    const payload={api_key: input && input.value.trim() ? input.value.trim() : undefined, model: sel && sel.value && !/Auto-populate|Default/.test(sel.value) ? sel.value : undefined};
    const r=await fetch('/system/providers/'+pid+'/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    toast(pid+': '+(d.reply||d.detail||d.ok), d.ok?'info':'warn');
    if(input && d.ok) input.placeholder='•••••• configured';
  }catch(e){toast(pid+': '+e.message,'err');}
}
window.testConnection=testConnection;
async function saveProviderKeys(){
  const saved=[];
  for(const pid of ['openai','anthropic','gemini','kimi','deepseek','qwen','huggingface']){
    const input=$('#key-'+pid); const sel=$('#model-'+pid);
    const body={provider:pid, action:'set'};
    if(input && input.value.trim()) body.api_key=input.value.trim();
    if(sel && sel.value && !/Auto-populate|Default/.test(sel.value)) body.model=sel.value;
    if(body.api_key || body.model){
      const r=await fetch('/system/provider-keys',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const d=await r.json(); if(d.ok){ saved.push(pid); if(input){ input.value=''; if(d.configured) input.placeholder='•••••• configured'; } }
    }
  }
  toast(saved.length ? ('Provider settings saved: '+saved.join(', ')) : 'No provider key changes');
}
window.saveProviderKeys=saveProviderKeys;

async function saveLmstudioSettings(){
  const url=$('#lmstudio-url'); const model=$('#model-lmstudio');
  if(!url && !model) return;
  const payload={};
  if(url && url.value.trim()) payload.lmstudio_base_url=url.value.trim();
  if(model && model.value.trim() && !/Auto-populate|Default/.test(model.value)) payload.lmstudio_model=model.value.trim();
  if(!Object.keys(payload).length) return;
  try{
    const r=await fetch('/system/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    if(d.ok) toast('LM Studio settings saved');
    else toast('LM Studio settings failed','warn');
  }catch(e){ toast('LM Studio settings failed: '+e.message,'err'); }
}

async function saveOllamaSettings(){
  const url=$('#ollama-url');
  if(!url || !url.value.trim()) return;
  try{
    const r=await fetch('/system/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ollama_base_url:url.value.trim()})});
    const d=await r.json();
    if(d.ok) toast('Ollama settings saved');
    else toast('Ollama settings failed','warn');
  }catch(e){ toast('Ollama settings failed: '+e.message,'err'); }
}

async function pullLmstudioModel(){
  const input=$('#lmstudio-pull-name');
  const model = input ? input.value.trim() : '';
  if(!model) return toast('Enter a model name or HuggingFace URL','warn');
  const log=$('#lmstudio-pull-log'); if(log) log.textContent='Resolving '+model+'...';
  let hadError = false;
  try{
    const r=await fetch('/lmstudio/pull',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model})});
    const reader=r.body.getReader(); const dec=new TextDecoder(); let buf='';
    while(true){ const {done,value}=await reader.read(); if(done) break; buf += dec.decode(value,{stream:true}); const lines=buf.split(/\r?\n/); buf=lines.pop()||''; for(const line of lines){ if(!line.trim()) continue; let obj={}; try{obj=JSON.parse(line)}catch(e){} if(obj.type==='error' || obj.error || obj.detail && obj.type==='error'){ hadError=true; } if(log) log.textContent = (obj.status || obj.detail || obj.error || JSON.stringify(obj)).slice(0,500); } }
    if(hadError){ toast('Download failed for '+model+' — see log','err'); return; }
    state.provider='lmstudio'; state.selectedModel=model; persist(); toast('Download finished and selected: '+model); await loadModelList();
  }catch(e){ if(log) log.textContent='Download failed: '+e.message; toast('Download failed: '+e.message,'err'); }
}
window.pullLmstudioModel = pullLmstudioModel;

async function saveHfSettings(){
  const url=$('#hf-url'); const key=$('#key-huggingface'); const model=$('#model-huggingface');
  if(!url && !key && !model) return;
  const payload={};
  if(url && url.value.trim()) payload.huggingface_base_url=url.value.trim();
  if(key && key.value.trim()) payload.huggingface_api_key=key.value.trim();
  if(model && model.value.trim() && !/Auto-populate|Default/.test(model.value)) payload.huggingface_model=model.value.trim();
  if(!Object.keys(payload).length) return;
  try{
    const r=await fetch('/system/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    if(d.ok){ if(key) key.value=''; toast('HuggingFace settings saved'); }
    else toast('HuggingFace settings failed','warn');
  }catch(e){ toast('HuggingFace settings failed: '+e.message,'err'); }
}

async function saveAgentModels(){
  const payload={};
  $$('.agent-model-select').forEach(sel=>{
    if(!sel.value) return;
    const keyMap={'agent-model-router':'router_model','agent-model-fast':'fast_model','agent-model-smart':'smart_model','agent-model-coder':'coder_model','agent-model-creative':'creative_model','agent-model-chemistry':'chemistry_model','agent-model-research':'research_model'};
    const key=keyMap[sel.id]; if(key) payload[key]=sel.value;
  });
  if(!Object.keys(payload).length) return;
  try{
    const r=await fetch('/api/v15/settings/agent-models',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    if(d.ok) toast('Agent models saved');
    else toast('Agent models failed','warn');
  }catch(e){ toast('Agent models failed: '+e.message,'err'); }
}

async function saveSettings(){
  const providerSel=$('#set-provider');
  const modelSel=$('#set-model');
  const voiceSel=$('#set-voice-lang');
  const privacyMode=$('#set-privacy-mode');
  const privacySimple=$('#set-privacy-simple');
  if(providerSel) state.provider = providerSel.value || 'ollama';
  if(modelSel) state.selectedModel = modelSel.value === 'auto' ? '' : (modelSel.value || '');
  if(voiceSel) state.voiceLang = voiceSel.value || 'en-IN';
  const privacyValue = (privacyMode && privacyMode.value) || (privacySimple && privacySimple.value) || '';
  if(privacyValue) state.privacyMode = privacyValue;
  const v9VoiceLang = $('#v9-voice-lang');
  if(v9VoiceLang) v9VoiceLang.value = state.voiceLang;
  persist();
  updateModeButtons();
  await saveProviderKeys();
  await saveHfSettings();
  await saveLmstudioSettings();
  await saveOllamaSettings();
  await saveAgentModels();
  await saveMediaSettings();
  const token=$('#key-token'); if(token && token.value.trim()) localStorage.shimsAccessToken = token.value.trim();
  const peer=$('#key-peer'); if(peer && peer.value.trim()) localStorage.shimsPeerUrl = peer.value.trim();
  const st=$('#settings-status'); if(st) st.textContent='Settings saved. API keys are also written to .env by the backend for persistence.';
  toast('Settings saved');
}
window.saveSettings=saveSettings;
function resetSettings(){
  state.provider='ollama'; state.selectedModel='llama3.2:latest'; state.voiceLang='en-IN'; state.converseMode=true; state.webMode=false; state.peersMode=false; persist(); updateModeButtons(); toast('Settings reset to local defaults');
}
window.resetSettings=resetSettings;

function setThemePreview(theme){ document.body.dataset.theme=theme; $$('.theme-chip').forEach(c=>c.classList.toggle('on', c.dataset.theme===theme)); }
window.setThemePreview=setThemePreview;

function bindUI(){
  $('#send-btn') && ($('#send-btn').onclick = send);
  $('#tools-btn') && ($('#tools-btn').onclick = (e)=>{ e.stopPropagation(); const m=document.getElementById('tools-menu'); if(m) m.classList.toggle('hidden'); });
  document.addEventListener('click', (e)=>{ const m=document.getElementById('tools-menu'); if(m && !m.classList.contains('hidden') && !m.contains(e.target) && e.target.id!=='tools-btn'){ m.classList.add('hidden'); } });
  $('#input') && $('#input').addEventListener('keydown', e=>{ if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); send(); } });
  $('#voice-orb') && ($('#voice-orb').onclick = toggleVoice);
  const mobileMenu=$('#mobile-menu-btn'), drawer=$('.panel-left'), drawerBackdrop=$('#drawer-backdrop');
  const setDrawerOpen=(open)=>{ if(drawer) drawer.classList.toggle('drawer-open', !!open); if(drawerBackdrop) drawerBackdrop.classList.toggle('open', !!open); };
  mobileMenu && (mobileMenu.onclick = e=>{ e.stopPropagation(); setDrawerOpen(!(drawer && drawer.classList.contains('drawer-open'))); });
  drawerBackdrop && (drawerBackdrop.onclick = ()=>setDrawerOpen(false));
  // Command Center is now a slide-in drawer (former right panel).
  const cmdDock=$('#cmd-dock');
  const cmdToggle=$('#btn-thinking');
  if(cmdToggle && cmdDock){ cmdToggle.onclick=()=>cmdDock.classList.toggle('open'); }
  // Theme switcher (Aurora ⇄ Classic), persisted.
  const themeBtn=$('#btn-theme');
  if(themeBtn){ themeBtn.onclick=()=>{ themeBtn.classList.remove('flash'); void themeBtn.offsetWidth; themeBtn.classList.add('flash'); cycleTheme(); }; }
  $('#btn-model') && ($('#btn-model').onclick = ()=>{ const m=$('#model-menu'); if(m){ const parent=document.getElementById('model-dropdown'); if(parent) parent.classList.toggle('open'); m.classList.toggle('open'); loadModelList(); } });
  $('#provider-select') && ($('#provider-select').onchange = e=>{ state.provider=e.target.value||'ollama'; if(state.provider==='ollama'){ state.selectedModel=chooseDefaultLocal(state.models); } else { const cloud=(state.models.cloud||[]).find(m=>m.provider===state.provider); state.selectedModel=cloud ? cloud.name : ''; } syncProviderModel(state.models); toast('Provider: '+state.provider+' / '+state.selectedModel); });
  $('#set-provider') && ($('#set-provider').onchange = e=>{ state.provider=e.target.value||'auto'; state.selectedModel=''; loadSettings(); });
  $('#mode-converse') && ($('#mode-converse').onclick=()=>{state.converseMode=!state.converseMode;persist();updateModeButtons();toast(state.converseMode?'Conversation history on':'Conversation history off');});
  $('#mode-web') && ($('#mode-web').onclick=()=>{state.webMode=!state.webMode;persist();updateModeButtons();});
  $('#mode-peers') && ($('#mode-peers').onclick=()=>{state.peersMode=!state.peersMode;persist();updateModeButtons();});
  $$('.nav-row').forEach(row=>row.onclick=()=>{ const v=row.dataset.view; if(v==='chat') closePane(); else if(v==='sessions') loadSessionsPane(); else openPane(v); $$('.nav-row').forEach(r=>r.classList.remove('active')); row.classList.add('active'); setDrawerOpen(false); });
  $('#mf-gen') && ($('#mf-gen').onclick = generateMedia);
  $$('#mf-types .mf-pill').forEach(b=>b.onclick=()=>{$$('#mf-types .mf-pill').forEach(x=>x.classList.remove('on','active')); b.classList.add('on');});
  $('#stt-banner-close') && ($('#stt-banner-close').onclick=()=>$('#stt-banner').classList.remove('show'));
  $('#stt-type-btn') && ($('#stt-type-btn').onclick=()=>{$('#stt-banner').classList.remove('show'); $('#input')?.focus();});
  $('#stt-retry-btn') && ($('#stt-retry-btn').onclick=()=>{ if(!state.voiceOn) toggleVoice(); });
  document.addEventListener('keydown', e=>{ if(e.key==='Tab' && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)){ e.preventDefault(); toggleVoice(); } });
}
function renderTranscript(messages=[]){
  const t=$('#transcript'); if(!t) return;
  t.innerHTML='';
  if(!messages.length){
    t.innerHTML='<div class="empty-state"><h2>SHIMS // ONLINE</h2><div>Speak or type a command. Voice mode runs the converse pipeline.</div><div class="hint">Conversation history is ready.</div></div>';
    return;
  }
  messages.forEach(m=>pushBubble(m.role === 'user' ? 'user' : 'assistant', m.content || ''));
}
async function newChat(){
  try{
    const d=await (await fetch('/sessions/new',{method:'POST'})).json();
    if(!d.ok) throw new Error(d.detail || 'Could not create session');
    state.sessionId=d.session_id; persist(); renderTranscript([]);
    await loadSessionsPane(); toast('New chat ready');
  }catch(e){ toast('New chat failed: '+e.message,'err'); }
}
window.newChat=newChat;
async function loadSession(sessionId){
  if(!sessionId) return;
  try{
    const d=await (await fetch('/sessions/'+encodeURIComponent(sessionId))).json();
    if(!d.ok) throw new Error(d.detail || 'Session not found');
    state.sessionId=d.session_id; persist(); renderTranscript(d.messages || []);
    await loadSessionsPane(); feed('loaded chat history: '+(d.title || d.session_id).slice(0,40), 'info');
  }catch(e){ toast('Could not load chat: '+e.message,'err'); }
}
window.loadSession=loadSession;
async function loadSessionsPane(){
  const box=$('#sessions-list'); if(!box) return;
  try{
    const d=await (await fetch('/sessions')).json();
    const sessions=Array.isArray(d) ? d : (d.sessions || []);
    const newButton='<button type="button" class="v9-btn session-new" data-new-chat="1">New Chat</button>';
    const rows=sessions.map(s=>`<div class="v9-chip session-chip ${s.id===state.sessionId?'active':''}" role="button" tabindex="0" data-session-id="${escapeHtml(s.id)}"><b>${escapeHtml(s.title || 'New chat')}</b><small>${Number(s.message_count||0)} messages</small></div>`).join('');
    box.innerHTML=newButton+(rows || '<div class="empty-pane compact">No saved chats yet.</div>');
    const nb=box.querySelector('[data-new-chat]'); if(nb) nb.onclick=newChat;
    box.querySelectorAll('[data-session-id]').forEach(el=>{
      const open=()=>loadSession(el.dataset.sessionId);
      el.onclick=open;
      el.onkeydown=e=>{ if(e.key==='Enter' || e.key===' '){ e.preventDefault(); open(); } };
    });
  }catch(e){
    box.innerHTML='<div class="empty-pane compact">Sessions unavailable.</div>';
  }
}
window.loadSessionsPane=loadSessionsPane;
async function loadAgents(){
  const box=$('#agent-roster'); if(!box) return;
  try{
    const d=await (await fetch('/agents/list')).json();
    const agents=(d.agents || []);
    box.innerHTML=agents.map(a=>{
      const status=String(a.status || 'ready').toLowerCase();
      const cls=status.includes('busy')?'busy':(status.includes('ready')?'active':'offline');
      const role=String(a.approval_level || a.role || 'agent').replace(/_/g,' ');
      return `<div class="agent-row ${cls}" title="${escapeHtml(a.role || '')}"><span class="ag-dot"></span><span class="ag-name">${escapeHtml(a.name || a.id)}</span><span class="ag-role">${escapeHtml(role)}</span></div>`;
    }).join('') || '<div class="empty-pane compact">No agents online.</div>';
  }catch(e){
    box.innerHTML='<div class="feed-row err">Agent roster unavailable.</div>';
  }
}
window.loadAgents=loadAgents;

// ───────── Onboarding / first-run experience ─────────
const ONBOARD_STEPS = [
  { icon:'✦', title:'Welcome to SHIMS',
    body:'Your private, local-first AI operating shell. It can read and write files, run code and shell commands, search the web, and remember how you work — on your machine.' },
  { icon:'🌊', title:'It does the work',
    body:'Switch on <b>Agent mode</b> and SHIMS plans a wave of tools, runs them in parallel, and reports back. Anything risky pauses for your one-tap approval.' },
  { icon:'🧠', title:'It learns your way',
    body:'Give answers a 👍 or 👎 and SHIMS distills them into reusable skills and preferences. Teach it once; it remembers.' },
  { icon:'🔒', title:'Private by default',
    body:'Runs on local models out of the box. Nothing leaves your machine unless you pick a cloud provider. You stay in control.' },
];
const ONBOARD_SAMPLES = [
  'Summarise the files in this folder and suggest a cleanup.',
  'Search the web for the latest on a topic and brief me.',
  'Write a Python script to rename these files by date.',
];

function buildOnboarding(){
  if (document.getElementById('onboard-overlay')) return;
  const style = document.createElement('style');
  style.textContent = `
  #onboard-overlay{position:fixed;inset:0;z-index:9000;display:flex;align-items:center;justify-content:center;
    background:rgba(2,6,22,.72);backdrop-filter:blur(10px);opacity:1}
  /* No entrance animation on the overlay itself: a CSS animation's timeline can
     stall while the window is backgrounded/minimized (e.g. Electron window not
     yet focused during boot), which would freeze this full-screen blocker at
     its opacity:0 start frame — invisible but still swallowing every click.
     Card pops in instead, which is purely cosmetic and safe to lose a frame of. */
  #onboard-card{animation:obcardin .3s ease}
  @keyframes obfade{from{opacity:0}to{opacity:1}}
  @keyframes obcardin{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
  #onboard-card{width:min(560px,92vw);border:1px solid rgba(120,160,255,.22);border-radius:20px;
    background:linear-gradient(180deg,rgba(12,22,52,.96),rgba(6,12,32,.96));box-shadow:0 30px 90px rgba(0,0,0,.6);
    padding:34px 32px 26px;color:#dce6ff;font-family:inherit;position:relative;overflow:hidden}
  #onboard-card .ob-orb{width:62px;height:62px;border-radius:50%;margin:0 auto 16px;
    background:radial-gradient(circle at 35% 30%,#9fe9ff,#5563ff 45%,#1a1740 100%);
    box-shadow:0 0 44px rgba(89,140,255,.6);display:grid;place-items:center;font-size:26px}
  #onboard-card h2{margin:0 0 8px;text-align:center;font-size:23px;font-weight:800}
  #onboard-card p.ob-body{margin:0 auto 20px;text-align:center;color:#9fb3df;max-width:42ch;line-height:1.55;font-size:15px}
  #onboard-card .ob-samples{display:grid;gap:8px;margin:0 0 18px}
  #onboard-card .ob-sample{text-align:left;border:1px solid rgba(120,160,255,.18);background:rgba(10,20,46,.5);
    border-radius:11px;padding:11px 14px;cursor:pointer;color:#cfe;font-size:13.5px;transition:border-color .15s,transform .15s}
  #onboard-card .ob-sample:hover{border-color:rgba(120,160,255,.5);transform:translateY(-1px)}
  #onboard-card .ob-dots{display:flex;gap:7px;justify-content:center;margin:4px 0 18px}
  #onboard-card .ob-dot{width:8px;height:8px;border-radius:50%;background:rgba(120,160,255,.25);transition:.2s}
  #onboard-card .ob-dot.on{background:linear-gradient(120deg,#43e7ff,#8b7bff);width:22px;border-radius:5px}
  #onboard-card .ob-row{display:flex;gap:10px;justify-content:space-between;align-items:center}
  #onboard-card button.ob-btn{font:inherit;font-weight:650;font-size:14px;border-radius:11px;padding:11px 20px;cursor:pointer;border:1px solid transparent}
  #onboard-card .ob-next{background:linear-gradient(120deg,#43e7ff,#8b7bff);color:#05101f;box-shadow:0 8px 24px rgba(89,140,255,.34)}
  #onboard-card .ob-next:hover{transform:translateY(-1px)}
  #onboard-card .ob-skip{background:transparent;color:#8aa0d6;border-color:rgba(120,160,255,.2)}
  `;
  document.head.appendChild(style);

  const ov = document.createElement('div');
  ov.id = 'onboard-overlay';
  ov.innerHTML = `<div id="onboard-card" role="dialog" aria-modal="true" aria-label="Welcome to SHIMS">
    <div class="ob-orb" id="ob-icon">✦</div>
    <h2 id="ob-title"></h2>
    <p class="ob-body" id="ob-body"></p>
    <div class="ob-samples" id="ob-samples" style="display:none"></div>
    <div class="ob-dots" id="ob-dots"></div>
    <div class="ob-row">
      <button class="ob-btn ob-skip" id="ob-skip">Skip</button>
      <button class="ob-btn ob-next" id="ob-next">Next →</button>
    </div>
  </div>`;
  document.body.appendChild(ov);

  let step = 0;
  const render = () => {
    const s = ONBOARD_STEPS[step];
    const last = step === ONBOARD_STEPS.length - 1;
    document.getElementById('ob-icon').textContent = s.icon;
    document.getElementById('ob-title').textContent = s.title;
    document.getElementById('ob-body').innerHTML = s.body;
    document.getElementById('ob-dots').innerHTML =
      ONBOARD_STEPS.map((_, i) => `<span class="ob-dot ${i===step?'on':''}"></span>`).join('');
    document.getElementById('ob-next').textContent = last ? 'Start using SHIMS →' : 'Next →';
    const samplesBox = document.getElementById('ob-samples');
    if (last) {
      samplesBox.style.display = 'grid';
      samplesBox.innerHTML = '<div style="font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:#5f74a8;text-align:center;margin-bottom:2px">Try one to get started</div>' +
        ONBOARD_SAMPLES.map((t,i)=>`<div class="ob-sample" data-s="${i}">${t}</div>`).join('');
      samplesBox.querySelectorAll('.ob-sample').forEach(el=>{
        el.onclick = () => { prefillCommand(ONBOARD_SAMPLES[+el.dataset.s]); finishOnboarding(); };
      });
    } else {
      samplesBox.style.display = 'none';
    }
  };
  document.getElementById('ob-next').onclick = () => {
    if (step < ONBOARD_STEPS.length - 1) { step++; render(); }
    else finishOnboarding();
  };
  document.getElementById('ob-skip').onclick = () => finishOnboarding();
  ov.addEventListener('keydown', e => { if (e.key === 'Escape') finishOnboarding(); });
  render();
}

function prefillCommand(text){
  const input = document.getElementById('input');
  if (input) { input.value = text; input.focus(); }
}

function finishOnboarding(){
  localStorage.shimsOnboardingDone = 'true';
  const ov = document.getElementById('onboard-overlay');
  if (ov) { ov.style.animation = 'obfade .25s ease reverse'; setTimeout(()=>ov.remove(), 230); }
  try { toast('Welcome aboard — type a command or press TAB for voice.'); } catch(e){}
}

// ───────── Launch gate ─────────
function checkOnboarding(){
  if (localStorage.shimsOnboardingDone === 'true') return;
  // Defer slightly so the boot overlay clears first.
  setTimeout(() => {
    try { buildOnboarding(); }
    catch(e){
      localStorage.shimsOnboardingDone='true';
      // Don't leave a half-built overlay sitting on top of the app, blocking every click.
      const ov = document.getElementById('onboard-overlay'); if(ov) ov.remove();
    }
  }, 600);
}
function dismissOnboarding(){ finishOnboarding(); }
function startOnboarding(){ localStorage.removeItem('shimsOnboardingDone'); buildOnboarding(); }
window.dismissOnboarding=dismissOnboarding;
window.startOnboarding=startOnboarding;

// ───────── Settings ─────────
async function loadSettings(){
  try{
    const r=await fetch('/api/v15/settings/models');
    const d=await r.json();
    if(!d.ok) return;
    const providerSel=$('#set-provider');
    const modelSel=$('#set-model');
    const privSel=$('#set-privacy-mode') || $('#set-privacy-simple'); if(privSel) privSel.value=state.privacyMode||'balanced';
    if(providerSel) providerSel.value=state.provider||'auto';
    const providerKey = providerSel ? (providerSel.value || 'auto') : (state.provider || 'auto');
    if(modelSel){
      modelSel.innerHTML='<option value="auto">Auto</option>';
      const models=d.models[providerKey]||d.models.auto||d.models.ollama||[];
      models.forEach(m=>{ const o=document.createElement('option'); o.value=m.id||m.name||m; o.textContent=m.name||m.id||m; modelSel.appendChild(o); });
      modelSel.value=state.selectedModel||'auto';
    }
    const voiceSel=$('#set-voice-lang');
    if(voiceSel) voiceSel.value=state.voiceLang||'en-IN';
  }catch(e){ console.warn('loadSettings failed',e); }
}
// ───────── Analytics (privacy-respecting, opt-in) ─────────
function trackEvent(eventName){
  if(!localStorage.shimsAnalyticsEnabled && localStorage.shimsAnalyticsEnabled!=='false') localStorage.shimsAnalyticsEnabled='true';
  if(localStorage.shimsAnalyticsEnabled!=='true') return;
  try{
    fetch('/api/v15/analytics/event',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({event_name:eventName, platform:'web', app_version:'14.4', session_id:state.sessionId||'web'}),
      keepalive:true
    });
  }catch(e){}
}

// ───────── Abuse Reporting ─────────
function reportAbuse(){
  const cat=prompt('Category: harmful, spam, harassment, other');
  if(!cat) return;
  const desc=prompt('Describe the issue (min 10 chars):');
  if(!desc || desc.length<10){ toast('Description too short','warn'); return; }
  fetch('/api/v15/support/abuse-report',{
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({category:cat, description:desc, platform:'web', app_version:'14.4'})
  }).then(r=>r.json()).then(d=>{ toast(d.message||'Report submitted','info'); }).catch(e=>{ toast('Failed to submit report','err'); });
}
window.reportAbuse=reportAbuse;

// ───────── Support ─────────
function openSupport(){ window.open('mailto:support@jklifecare.com?subject=SHIMS%20Support','_blank'); }
window.openSupport=openSupport;

/* ==================== THEME SWITCHER ==================== */
const THEMES=['aurora','classic'];
function applyTheme(name){
  if(!THEMES.includes(name)) name='aurora';
  document.documentElement.setAttribute('data-theme', name);
  try{ localStorage.setItem('shims_theme', name); }catch(e){}
  const meta=document.querySelector('meta[name="theme-color"]'); if(meta) meta.setAttribute('content', name==='classic'?'#020616':'#0a0d15');
}
function currentTheme(){ return document.documentElement.getAttribute('data-theme')||'aurora'; }
function cycleTheme(){ const next=currentTheme()==='aurora'?'classic':'aurora'; applyTheme(next); toast('Theme · '+(next==='aurora'?'Aurora (default)':'Classic space/neon')); }
window.applyTheme=applyTheme; window.cycleTheme=cycleTheme;

/* ==================== NEURAL AGENT (inline, folded from its old tab) ==================== */
async function renderNeuralPanel(){
  const b=pushBubble('assistant','');
  const content=b.querySelector('.content'); if(!content) return;
  const card=document.createElement('div'); card.className='neural-card';
  card.innerHTML='<h4>🧠 Neural Self-Evolution<span style="margin-left:auto;font-size:10px;color:var(--text-dim)" id="np-model">checking…</span></h4>'+
    '<div class="neural-stats" id="np-stats"></div>'+
    '<div id="np-list"><div style="color:var(--text-dim);font-size:12px">Loading proposals…</div></div>'+
    '<div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap"><button class="v9-btn" onclick="neuralReflect()">🔍 Reflect (find gaps)</button><button class="v9-btn secondary" onclick="renderNeuralPanel()">↻ Refresh</button></div>';
  content.innerHTML=''; content.appendChild(card);
  try{ const ms=await (await fetch('/api/neural-agent/model-status')).json(); card.querySelector('#np-model').textContent=((ms.model||ms.evolution_model||'local')+' · '+(ms.provider||'ollama')+(ms.online===false?' (offline)':'')); }catch(e){ card.querySelector('#np-model').textContent='model status n/a'; }
  try{
    const d=await (await fetch('/api/neural-agent/proposals?limit=50')).json();
    const props=(d.proposals||d.items||[]);
    const c={total:props.length,pending:0,approved:0,applied:0};
    props.forEach(p=>{ const s=(p.status||'').toLowerCase(); if(s==='applied')c.applied++; else if(s==='approved'||s==='accepted')c.approved++; else if(['pending','validated','proposed'].includes(s))c.pending++; });
    card.querySelector('#np-stats').innerHTML=[['Total',c.total],['Pending',c.pending],['Approved',c.approved],['Applied',c.applied]].map(([k,v])=>`<div class="neural-stat"><b>${v}</b><span>${k}</span></div>`).join('');
    const list=card.querySelector('#np-list');
    if(!props.length){ list.innerHTML='<div style="color:var(--text-dim);font-size:12px">No proposals yet. Click “Reflect” to let Shims scan itself for improvements, or use <code>/propose &lt;intent&gt;</code>.</div>'; }
    else { list.innerHTML=props.slice(0,12).map(p=>`<div class="neural-prop"><div class="np-title">${escapeHtml(p.title||p.intent||('Proposal '+p.id))}</div><div class="np-meta"><span class="np-status">${escapeHtml(p.status||'pending')}</span> ${escapeHtml(((p.affected_files||p.files||[]).join(', '))||p.description||'').slice(0,120)}</div><div class="np-acts"><button onclick="neuralAct('${p.id}','test')">Test</button><button onclick="neuralAct('${p.id}','accept')">Accept</button><button onclick="neuralAct('${p.id}','apply')">Apply</button></div></div>`).join(''); }
  }catch(e){ card.querySelector('#np-list').innerHTML='<div style="color:var(--red)">Could not load proposals: '+escapeHtml(e.message)+'</div>'; }
  $('#transcript').scrollTop=$('#transcript').scrollHeight;
}
async function neuralAct(id,action){ try{ const d=await (await fetch('/api/neural-agent/proposals/'+id+'/'+action,{method:'POST'})).json(); toast('Proposal '+action+': '+(d.ok?'ok':(d.error||'failed')), d.ok?'info':'err'); if(d.ok) renderNeuralPanel(); }catch(e){ toast('Failed: '+e.message,'err'); } }
async function neuralReflect(){ toast('Reflecting — scanning Shims for gaps…'); try{ const d=await (await fetch('/api/neural-agent/reflect',{method:'POST'})).json(); toast('Reflection · '+(d.proposals_generated!=null?d.proposals_generated:0)+' new proposals, '+(d.gaps_found!=null?d.gaps_found:0)+' gaps'); renderNeuralPanel(); }catch(e){ toast('Reflect failed: '+e.message,'err'); } }
window.renderNeuralPanel=renderNeuralPanel; window.neuralAct=neuralAct; window.neuralReflect=neuralReflect;

function boot(){
  applyTheme(localStorage.getItem('shims_theme')||currentTheme());
  const clock=()=>setText('#clock', new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})); clock(); setInterval(clock,30000);
  const boot=$('#boot'); const enter=$('#boot-enter'); if(enter) enter.onclick=()=>boot&&boot.remove(); setTimeout(()=>{ if(boot) boot.classList.add('ready'); }, 500); setTimeout(()=>{ if(boot) boot.remove(); }, 1600);
  try{ const c=$('#bg'); if(c){ const ctx=c.getContext('2d'); function resize(){ c.width=innerWidth*devicePixelRatio; c.height=innerHeight*devicePixelRatio; c.style.width=innerWidth+'px'; c.style.height=innerHeight+'px'; } resize(); window.addEventListener('resize',resize); let t=0; (function draw(){ t+=0.01; ctx.clearRect(0,0,c.width,c.height); ctx.strokeStyle='rgba(124,240,255,.08)'; for(let x=0;x<c.width;x+=70){ ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x+Math.sin(t+x)*80,c.height); ctx.stroke(); } requestAnimationFrame(draw); })(); } }catch(e){}
  bindUI(); updateModeButtons(); ensureSettingsEnhancements(); loadVoiceConfig(); loadModelList(); loadSessionsPane(); loadAgents(); setInterval(loadAgents,30000); refreshAgentStrip(); setInterval(refreshAgentStrip,20000); loadSettings(); loadEnterpriseStatus(); setInterval(loadEnterpriseStatus,30000); checkOnboarding(); feed('SHIMS v16 Omni core online', 'info');
  if(state.voiceOn){ state.voiceOn=false; setTimeout(()=>toggleVoice(), 300); }
  trackEvent('app_boot');
}
if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();

/* ==================== SCANNER ==================== */
let scanImageData = null;
let scanStream = null;

function loadScannerPane() {
  const box = $('#scanner-body');
  if (!box) return;
  $('#scan-fields-card').style.display = 'none';
  $('#scan-company-card').style.display = 'none';
  $('#scan-saved-card').style.display = 'block';
  loadSavedContacts();
}

function handleScanFile(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    scanImageData = e.target.result;
    $('#scan-preview').src = scanImageData;
    $('#scan-preview-wrap').style.display = 'block';
    $('#scan-extract-btn').style.display = 'inline-block';
    $('#scan-status').textContent = 'Image loaded. Click Extract Text.';
    $('#scan-fields-card').style.display = 'none';
    $('#scan-company-card').style.display = 'none';
  };
  reader.readAsDataURL(file);
}

async function startScanCamera() {
  try {
    scanStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
    $('#scan-video').srcObject = scanStream;
    $('#scan-camera-wrap').style.display = 'block';
  } catch (err) {
    $('#scan-status').textContent = 'Camera error: ' + err.message;
  }
}

function stopScanCamera() {
  if (scanStream) { scanStream.getTracks().forEach(t => t.stop()); scanStream = null; }
  $('#scan-camera-wrap').style.display = 'none';
}

function captureScanPhoto() {
  const video = $('#scan-video');
  const canvas = $('#scan-canvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video, 0, 0);
  scanImageData = canvas.toDataURL('image/jpeg');
  $('#scan-preview').src = scanImageData;
  $('#scan-preview-wrap').style.display = 'block';
  $('#scan-extract-btn').style.display = 'inline-block';
  stopScanCamera();
  $('#scan-status').textContent = 'Photo captured. Click Extract Text.';
}

async function _ocrServer(dataUrl){
  // Offline server OCR (RapidOCR). dataUrl -> Blob -> /ocr.
  const blob = await (await fetch(dataUrl)).blob();
  const fd = new FormData(); fd.append('file', blob, 'scan.png');
  const r = await fetch('/ocr', { method:'POST', body: fd });
  if (!r.ok) throw new Error('server OCR unavailable ('+r.status+')');
  const d = await r.json();
  if (!d.ok) throw new Error(d.hint || d.error || 'server OCR failed');
  return d.text || '';
}
async function runScanOCR() {
  if (!scanImageData) return;
  $('#scan-status').textContent = 'Running OCR (offline engine)...';
  try {
    let text = '';
    // Prefer the offline server engine; fall back to browser Tesseract if present.
    try {
      text = await _ocrServer(scanImageData);
    } catch (serverErr) {
      if (typeof Tesseract === 'undefined') throw serverErr;
      $('#scan-status').textContent = 'Server OCR unavailable; using browser OCR...';
      const result = await Tesseract.recognize(scanImageData, 'eng', {
        logger: m => { if (m.status === 'recognizing text') $('#scan-status').textContent = 'OCR: ' + Math.round(m.progress*100) + '%'; }
      });
      text = result.data.text;
    }
    $('#scan-status').textContent = 'OCR complete. Parsing...';
    try {
      const r = await fetch('/api/v15/scan/parse', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ocr_text: text}) });
      const d = await r.json();
      if (d.ok && d.parsed) {
        $('#scan-name').value = d.parsed.name || '';
        $('#scan-title').value = d.parsed.title || '';
        $('#scan-company').value = d.parsed.company || '';
        $('#scan-phone').value = d.parsed.phone || '';
        $('#scan-email').value = d.parsed.email || '';
        $('#scan-website').value = d.parsed.website || '';
        $('#scan-address').value = d.parsed.address || '';
        $('#scan-social').value = d.parsed.social || '';
      } else { basicScanParse(text); }
    } catch (e) { basicScanParse(text); }
    $('#scan-fields-card').style.display = 'block';
    $('#scan-status').textContent = 'Review extracted fields and click Lookup Company or Skip.';
  } catch (err) {
    $('#scan-status').textContent = 'OCR failed: ' + err.message;
  }
}

function basicScanParse(text) {
  const email = text.match(/[\w.-]+@[\w.-]+\.\w+/);
  const phone = text.match(/(\+?\d[\d\s\-().]{7,}\d)/);
  const website = text.match(/(https?:\/\/[^\s]+|www\.[^\s]+)/i);
  $('#scan-email').value = email ? email[0] : '';
  $('#scan-phone').value = phone ? phone[0].trim() : '';
  $('#scan-website').value = website ? website[0] : '';
  const lines = text.split('\n').map(l=>l.trim()).filter(l=>l.length>1);
  if (lines.length>0) $('#scan-name').value = lines[0];
  if (lines.length>1) $('#scan-company').value = lines[1];
}

async function scanLookupCompany() {
  const company = $('#scan-company').value.trim();
  if (!company) { $('#scan-status').textContent = 'Enter a company name first.'; return; }
  $('#scan-status').textContent = 'Looking up company...';
  try {
    const r = await fetch('/api/v15/scan/company', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({company_name: company}) });
    const d = await r.json();
    if (d.ok && d.company) {
      const c = d.company;
      $('#scan-company-info').innerHTML = `<b>${escapeHtml(c.name)}</b><br>${escapeHtml(c.description)}<br><i>Industry:</i> ${escapeHtml(c.industry)} · <i>Size:</i> ${escapeHtml(c.size)}<br><i>Products:</i> ${(c.products||[]).map(p=>escapeHtml(p)).join(', ')}`;
      $('#scan-auto-notes').value = `${c.name} - ${c.industry} (${c.size}). Products: ${(c.products||[]).join(', ')}. ${c.description}`;
    } else {
      $('#scan-company-info').innerHTML = 'Company lookup failed. You can still add notes manually.';
    }
  } catch (e) {
    $('#scan-company-info').innerHTML = 'Company lookup error: ' + escapeHtml(e.message);
  }
  $('#scan-company-card').style.display = 'block';
  $('#scan-status').textContent = '';
}

function scanSkipLookup() {
  $('#scan-company-card').style.display = 'block';
  $('#scan-company-info').innerHTML = 'No company lookup performed.';
}

function scanBuildVCF() {
  const contact = {
    name: $('#scan-name').value,
    title: $('#scan-title').value,
    company: $('#scan-company').value,
    phone: $('#scan-phone').value,
    email: $('#scan-email').value,
    website: $('#scan-website').value,
    address: $('#scan-address').value,
    social: $('#scan-social').value,
    notes: ($('#scan-auto-notes').value + '\n' + $('#scan-manual-notes').value).trim()
  };
  const lines = ['BEGIN:VCARD','VERSION:3.0'];
  if (contact.name) { lines.push('FN:'+contact.name); const p=contact.name.trim().split(' '); lines.push(p.length>=2?'N:'+p.slice(1).join(' ')+';'+p[0]+';;;':'N:;'+contact.name+';;;'); }
  if (contact.title) lines.push('TITLE:'+contact.title);
  if (contact.company) lines.push('ORG:'+contact.company);
  if (contact.phone) lines.push('TEL;TYPE=CELL:'+contact.phone);
  if (contact.email) lines.push('EMAIL;TYPE=WORK:'+contact.email);
  if (contact.website) lines.push('URL:'+contact.website);
  if (contact.address) lines.push('ADR;TYPE=WORK:;;'+contact.address+';;;;');
  if (contact.social) lines.push('X-SOCIALPROFILE:'+contact.social);
  if (contact.notes) lines.push('NOTE:'+contact.notes);
  lines.push('END:VCARD');
  const blob = new Blob([lines.join('\r\n')+'\r\n'], {type:'text/vcard'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (contact.name||'contact')+'_shims.vcf';
  a.click();
  $('#scan-status').textContent = 'VCF downloaded.';
}

async function scanSaveContact() {
  const contact = {
    name: $('#scan-name').value,
    title: $('#scan-title').value,
    company: $('#scan-company').value,
    phone: $('#scan-phone').value,
    email: $('#scan-email').value,
    website: $('#scan-website').value,
    address: $('#scan-address').value,
    social: $('#scan-social').value,
    notes: ($('#scan-auto-notes').value + '\n' + $('#scan-manual-notes').value).trim(),
    saved_at: new Date().toISOString()
  };
  try {
    const r = await fetch('/api/v15/scan/save', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({contact}) });
    const d = await r.json();
    if (d.ok) { $('#scan-status').textContent = 'Contact saved.'; loadSavedContacts(); }
    else { $('#scan-status').textContent = 'Save failed: ' + (d.error||''); }
  } catch (e) {
    $('#scan-status').textContent = 'Save error: ' + e.message;
  }
}

async function loadSavedContacts() {
  const box = $('#scan-saved-list');
  if (!box) return;
  try {
    const r = await fetch('/api/v15/scan/contacts');
    const d = await r.json();
    if (d.ok && d.contacts && d.contacts.length) {
      box.innerHTML = d.contacts.map(c => `<div class="v9-chip"><b>${escapeHtml(c.name||'Unnamed')}</b> · ${escapeHtml(c.company||'')} · ${escapeHtml(c.phone||'')} · ${escapeHtml(c.email||'')}</div>`).join('');
    } else {
      box.innerHTML = '<div class="empty-pane">No saved contacts yet.</div>';
    }
  } catch (e) {
    box.innerHTML = '<div class="empty-pane">Could not load contacts.</div>';
  }
}



/* ==================== DOCUMENT INGESTION ==================== */
async function handleDocIngest(input) {
  const file = input.files[0];
  if (!file) return;
  const status = $("#scan-doc-status");
  status.textContent = "Uploading and analyzing " + file.name + "...";
  try {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch("/api/v15/documents/ingest", { method: "POST", body: form });
    const d = await r.json();
    if (d.ok) {
      status.innerHTML = `✅ Ingested: <b>${escapeHtml(file.name)}</b> — ${d.chunks} chunks stored.<br>Category: ${escapeHtml(d.ai?.category || "?")} · Sentiment: ${escapeHtml(d.ai?.sentiment || "?")}<br>Summary: ${escapeHtml((d.ai?.summary || "").slice(0, 180))}...`;
      loadSavedContacts();
    } else {
      status.textContent = "Ingest failed: " + (d.error || "unknown");
    }
  } catch (e) {
    status.textContent = "Ingest error: " + e.message;
  }
}
window.handleDocIngest = handleDocIngest;

/* ==================== BEHAVIOR & CORTEX PANES + OMNIPOTENT TOGGLE ==================== */

async function loadBehaviorPane(){
  const box = $('#behavior-body'); if(!box) return;
  try{
    const d = await (await fetch('/behavior/suggestions')).json();
    const stats = d.stats || {};
    const top = stats.top || [];
    const suggestion = d.suggestion;
    let html = '<div class="v9-setting-card"><h3>📈 Behavior Predictions</h3>';
    if(suggestion){
      html += '<div class="v9-chip" style="border-left:3px solid var(--amber)"><b>Top prediction:</b> ' + escapeHtml(suggestion.action) + ' <small>(' + (suggestion.confidence*100).toFixed(0) + '%)</small></div>';
    }
    if(top.length){
      html += '<div style="margin-top:8px"><b>Learned patterns</b></div>';
      top.forEach(p => {
        html += '<div class="v9-chip"><b>' + escapeHtml(p.action) + '</b> <small>' + (p.confidence*100).toFixed(0) + '% — ' + escapeHtml(p.tier) + '</small></div>';
      });
    } else {
      html += '<small>No behavior patterns learned yet. Chat more and SHIMS will detect your habits.</small>';
    }
    html += '</div><div class="v9-setting-card"><h3>Engine Stats</h3><div class="v9-list"><div class="v9-chip"><b>' + (stats.events || 0) + ' events</b><small>observed</small></div><div class="v9-chip"><b>' + (stats.actions_known || 0) + ' actions</b><small>learned</small></div></div></div>';
    box.innerHTML = html;
  }catch(e){ box.innerHTML = '<div class="empty-pane">Error loading behavior: ' + escapeHtml(e.message) + '</div>'; }
}
window.loadBehaviorPane = loadBehaviorPane;

async function loadCortexPane(){
  const box = $('#cortex-body'); if(!box) return;
  try{
    const d = await (await fetch('/cortex/status')).json();
    const kernel = d.kernel || {};
    const cortex = d.cortex || {};
    const gates = d.gates || {};
    let html = '<div class="v9-setting-card"><h3>⚡ Cortex Status</h3>';
    html += '<div class="v9-list"><div class="v9-chip"><b>Architecture</b><small>' + escapeHtml(d.architecture || '—') + '</small></div>';
    html += '<div class="v9-chip"><b>Kernel frozen</b><small>' + (kernel.frozen ? 'Yes' : 'No') + '</small></div>';
    html += '<div class="v9-chip"><b>Engine available</b><small>' + (kernel.engine_available ? 'Yes' : 'No') + '</small></div>';
    html += '<div class="v9-chip"><b>Hot-reloadable</b><small>' + escapeHtml((cortex.hot_reloadable || []).join(', ')) + '</small></div>';
    html += '<div class="v9-chip"><b>Skills</b><small>' + (cortex.skill_count || 0) + '</small></div>';
    html += '</div></div>';
    html += '<div class="v9-setting-card"><h3>Prompt Overlay</h3><div id="cortex-overlay-display" style="font-size:11px;color:var(--text-dim);white-space:pre-wrap"></div><div class="v9-row" style="margin-top:8px"><textarea id="cortex-overlay-input" style="width:100%;min-height:80px;background:rgba(0,0,0,.25);color:#e9fbff;border:1px solid rgba(124,240,255,.2);border-radius:8px;padding:10px" placeholder="Add a prompt overlay (injected into every system prompt)..."></textarea><button class="v9-btn" onclick="saveCortexOverlay()">Save Overlay</button></div></div>';
    html += '<div class="v9-setting-card"><h3>Approval Gates</h3><div class="v9-list"><div class="v9-chip"><b>Code changes</b><small>' + escapeHtml(gates.code_changes || '—') + '</small></div><div class="v9-chip"><b>Auto-apply confidence</b><small>' + (gates.cortex_auto_apply_confidence || '—') + '</small></div></div></div>';
    box.innerHTML = html;
    // fetch current overlay text
    try{
      const od = await (await fetch('/cortex/status')).json();
      const disp = $('#cortex-overlay-display');
      if(disp) disp.textContent = (od.cortex && od.cortex.prompt_overlay_active) ? 'Active overlay set.' : 'No overlay active.';
    }catch(_){}
  }catch(e){ box.innerHTML = '<div class="empty-pane">Error loading cortex: ' + escapeHtml(e.message) + '</div>'; }
}
window.loadCortexPane = loadCortexPane;

async function saveCortexOverlay(){
  const text = $('#cortex-overlay-input'); if(!text) return;
  try{
    const r = await fetch('/cortex/prompt-overlay', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:text.value, reason:'ui'})});
    const d = await r.json();
    toast(d.ok ? 'Overlay saved' : 'Failed: ' + (d.error || ''), d.ok ? 'ok' : 'err');
    loadCortexPane();
  }catch(e){ toast('Save failed: ' + e.message, 'err'); }
}
window.saveCortexOverlay = saveCortexOverlay;

/* Full Access / Omnipotent Toggle */
let _omnipotentState = false;
async function ensureOmnipotentToggle(){
  const body = $('#pane-settings .pane-body'); if(!body || $('#omnipotent-toggle-card')) return;
  const card = document.createElement('div'); card.id = 'omnipotent-toggle-card'; card.className = 'v9-setting-card';
  card.innerHTML = '<h3>🔓 Full Access Toggle</h3><p style="font-size:11px;color:var(--text-dim);margin:0 0 8px">When ON, SHIMS acts without asking for approval. Use with caution.</p><div class="v9-row"><button id="btn-omnipotent" class="v9-btn" onclick="toggleOmnipotent()">Loading...</button><span id="omnipotent-status" style="font-size:11px"></span></div>';
  body.insertBefore(card, body.firstChild);
  await refreshOmnipotentState();
}
window.ensureOmnipotentToggle = ensureOmnipotentToggle;

async function refreshOmnipotentState(){
  try{
    const d = await (await fetch('/api/settings/omnipotent')).json();
    _omnipotentState = d.omnipotent_mode || false;
    const btn = $('#btn-omnipotent');
    const st = $('#omnipotent-status');
    if(btn){ btn.textContent = _omnipotentState ? 'Disable Full Access' : 'Enable Full Access'; btn.style.background = _omnipotentState ? '#ef4444' : 'var(--accent)'; }
    if(st){ st.textContent = _omnipotentState ? 'Full Access is ON — SHIMS acts without approval' : 'Full Access is OFF — approval gates active'; st.style.color = _omnipotentState ? '#ef4444' : 'var(--text-dim)'; }
  }catch(e){ console.log('omnipotent state error', e); }
}
window.refreshOmnipotentState = refreshOmnipotentState;

async function toggleOmnipotent(){
  const next = !_omnipotentState;
  try{
    const r = await fetch('/api/settings/omnipotent', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled:next})});
    const d = await r.json();
    if(d.ok){ toast(d.note || ('Full Access ' + (next ? 'enabled' : 'disabled')), next ? 'warn' : 'ok'); }
    else { toast(d.error || 'Toggle failed', 'err'); }
    await refreshOmnipotentState();
  }catch(e){ toast('Toggle failed: ' + e.message, 'err'); }
}
window.toggleOmnipotent = toggleOmnipotent;

/* ==================== BACKGROUND TASKS SIDEBAR ==================== */
async function loadBackgroundTasks(){
  const box = $('#bg-tasks-list');
  if(!box) return;
  try{
    const d = await (await fetch('/api/tasks?limit=8')).json();
    const tasks = d.tasks || [];
    if(!tasks.length){ box.innerHTML = '<div class="empty-pane compact" style="padding:8px 0">No background tasks.</div>'; return; }
    box.innerHTML = tasks.map(t => {
      const statusColor = t.status==='done' ? '#4ade80' : t.status==='failed' ? '#f87171' : t.status==='running' ? 'var(--cyan)' : 'var(--text-dim)';
      return '<div class="bg-task-item" style="padding:4px 0;border-bottom:1px solid rgba(124,240,255,.06)">'+
        '<div style="font-size:10px;color:'+statusColor+'">● '+escapeHtml(t.status)+'</div>'+
        '<div style="font-size:11px">'+escapeHtml(t.title)+'</div>'+
        '<div style="font-size:9px;color:var(--text-dim)">'+escapeHtml(t.task_type)+' · #'+t.id+'</div>'+
      '</div>';
    }).join('');
  }catch(e){ box.innerHTML = '<div class="empty-pane compact" style="padding:8px 0">Error loading tasks.</div>'; }
}
window.loadBackgroundTasks = loadBackgroundTasks;
setInterval(loadBackgroundTasks, 15000);
loadBackgroundTasks();

})();
