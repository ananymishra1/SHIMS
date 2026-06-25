(function(){
  const API='/api/duobot';
  let convId=null;
  let auto=false;
  let autoTimer=null;
  let currentMode='free';

  const PERSONA_NAMES={user:'You', primary:'Omni', local:'Factory', gemini:'Gemini', anthropic:'Claude', openai:'OpenAI', chair:'Chair'};
  const ROLE_COLOR={
    user:'var(--user)', primary:'var(--omni)', local:'var(--local)',
    gemini:'var(--gemini)', anthropic:'var(--anthropic)', openai:'var(--openai)', chair:'var(--chair)'
  };
  const DEFAULT_MEMBER_PROMPTS={
    primary:"You are SHIMS Omni — the primary orchestrator and long-term memory keeper. You are pragmatic, concise, and deeply familiar with the SHIMS source tree. Your job in the Council is to summarize context, ground decisions in existing code, and only invoke tools when the council collectively agrees.",
    gemini:"You are Gemini, a fast multimodal thinker. In the Council, you specialize in big-picture architecture, risk scanning, and creative options. Challenge hidden assumptions and propose alternatives.",
    anthropic:"You are Claude, a careful, safety-oriented thinker. In the Council, you question risky changes, verify edge cases, and ensure any self-modification follows the SHIMS safety model. Favor minimal, reversible edits.",
    openai:"You are OpenAI, an execution-focused engineer. In the Council, you evaluate feasibility, estimate effort, and draft concrete implementation steps with file paths and tool calls.",
    local:"You are Factory, the on-premise local model. In the Council, you represent offline-first constraints, cost awareness, and local-tool availability. Flag when a cloud-only plan would break air-gapped deployments."
  };

  // Council seats (member roster) + avatar glyphs for every speaker.
  const SEATS=[
    {role:'primary', name:'Omni', emoji:'✦', color:'var(--omni)', local:true},
    {role:'local', name:'Factory', emoji:'🏭', color:'var(--local)', pid:'ollama', local:true},
    {role:'gemini', name:'Gemini', emoji:'🔷', color:'var(--gemini)', pid:'gemini'},
    {role:'anthropic', name:'Claude', emoji:'🟣', color:'var(--anthropic)', pid:'anthropic'},
    {role:'openai', name:'OpenAI', emoji:'🟢', color:'var(--openai)', pid:'openai'},
  ];
  const AVATAR={user:'🧑', primary:'✦', local:'🏭', gemini:'🔷', anthropic:'🟣', openai:'🟢', chair:'⚖️', system:'⚙️', context:'📚'};
  let providerStatus={};   // pid -> 'ready' | 'missing key' | 'offline'
  let chairRole='primary';

  async function refreshProviderStatus(){
    try{ const d=await sysApi('GET','/system/providers');
      (d.providers||[]).forEach(p=>{ providerStatus[p.id]=p.status; }); }catch(e){}
  }

  function seatEnabled(s){
    if(s.role==='primary') return true;
    if(s.role==='local') return providerStatus['ollama']==='ready';
    return providerStatus[s.pid]==='ready';
  }

  function renderRoster(){
    const box=document.getElementById('roster'); if(!box) return;
    let html=SEATS.map(s=>{
      const en=seatEnabled(s);
      const isChair=(s.role===chairRole);
      return `<div class="seat ${en?'enabled ready':''} ${isChair?'chair-seat':''}" data-role="${s.role}" style="--seat:${s.color}" title="${esc(s.name)}${isChair?' · Chair':''}${en?'':' · not connected'}">
        <div class="av" style="color:${s.color}">${s.emoji}</div>
        <div class="nm">${esc(s.name)}${isChair?' ⚖️':''}</div>
        <div class="dot"></div>
      </div>`;
    }).join('');
    const connected=SEATS.filter(seatEnabled).length;
    if(connected<2){
      html+=`<div class="chair-sep"></div><a class="seat" href="/setup" style="text-decoration:none" title="Connect more minds">
        <div class="av" style="color:var(--accent);border-style:dashed">＋</div><div class="nm" style="color:var(--accent)">Add minds</div></a>`;
    }
    box.innerHTML=html;
  }

  function setRosterThinking(on){
    document.querySelectorAll('#roster .seat[data-role]').forEach(el=>{
      const role=el.dataset.role;
      const s=SEATS.find(x=>x.role===role);
      if(on && s && seatEnabled(s)) el.classList.add('thinking');
      else el.classList.remove('thinking');
    });
  }
  function markSpoke(roles){
    const set=new Set(roles);
    document.querySelectorAll('#roster .seat[data-role]').forEach(el=>{
      el.classList.remove('thinking');
      if(set.has(el.dataset.role)){
        el.classList.add('spoke');
        setTimeout(()=>el.classList.remove('spoke'), 2600);
      }
    });
  }

  function connectedCount(){ return SEATS.filter(seatEnabled).length; }

  // Inline nudge: convening a council with <2 minds is underwhelming, so offer
  // a one-click path to /setup instead of silently letting it run thin.
  async function maybeNudgeSetup(force){
    await refreshProviderStatus(); renderRoster();
    if(!force && connectedCount()>=2) return false;
    if(document.getElementById('nudge-overlay')) return true;
    const ov=document.createElement('div');
    ov.id='nudge-overlay';
    ov.style.cssText='position:fixed;inset:0;z-index:1200;display:flex;align-items:center;justify-content:center;background:rgba(3,7,20,.74);backdrop-filter:blur(8px)';
    const connected=connectedCount();
    ov.innerHTML=`<div class="glass" style="width:460px;max-width:92%;padding:26px;text-align:center;border-radius:18px">
      <div style="font-size:34px;margin-bottom:6px">⚖️</div>
      <h2 style="margin:0 0 8px;font-size:20px">A council needs more than one mind</h2>
      <p style="color:var(--muted);font-size:13.5px;margin:0 0 18px">
        You have <b style="color:var(--text)">${connected}</b> connected. The Council of the Wise shines when
        several AIs debate — connect Gemini, Claude or OpenAI in one click (local minds stay free and private).</p>
      <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap">
        <a class="btn primary" href="/setup" style="text-decoration:none;padding:11px 18px">🔑 Connect minds</a>
        <button class="btn" style="padding:11px 18px" onclick="duobot.dismissNudge()">Continue anyway</button>
      </div>
    </div>`;
    ov.addEventListener('click',e=>{ if(e.target===ov) dismissNudge(); });
    document.body.appendChild(ov);
    return true;
  }
  function dismissNudge(){ const ov=document.getElementById('nudge-overlay'); if(ov) ov.remove(); }

  const CURATED_MODELS={
    anthropic:["claude-opus-4","claude-sonnet-4-6","claude-3-5-sonnet-latest","claude-3-haiku-20240307"],
    openai:["gpt-4o","gpt-4o-mini","o3-mini","o1-mini","gpt-4.1","gpt-4.1-mini"],
    gemini:["gemini-2.5-pro","gemini-2.5-flash","gemini-2.0-flash","gemini-1.5-pro"],
    primary:["kimi-k2.6","moonshot-v1-8k","gpt-4o","claude-sonnet-4-6","gemini-2.5-pro","deepseek-chat","qwen-max"]
  };

  async function api(method, path, body){
    const opts={method, headers:{'Content-Type':'application/json'}};
    if(body) opts.body=JSON.stringify(body);
    const r=await fetch(API+path, opts);
    return r.json();
  }
  async function sysApi(method, path, body){
    const opts={method, headers:{'Content-Type':'application/json'}};
    if(body) opts.body=JSON.stringify(body);
    const r=await fetch(path, opts);
    return r.json();
  }

  function fmt(ts){ return new Date(ts*1000).toLocaleTimeString(); }
  function esc(t){ const d=document.createElement('div'); d.textContent=t; return d.innerHTML; }

  function renderStars(){
    const box=document.getElementById('stars');
    if(!box) return;
    box.innerHTML='';
    for(let i=0;i<80;i++){
      const s=document.createElement('div'); s.className='star';
      s.style.left=Math.random()*100+'%'; s.style.top=Math.random()*100+'%';
      const size=Math.random()*2+1; s.style.width=size+'px'; s.style.height=size+'px';
      s.style.animationDelay=Math.random()*4+'s'; s.style.animationDuration=(3+Math.random()*4)+'s';
      box.appendChild(s);
    }
  }

  function renderMsg(m){
    const div=document.createElement('div');
    const role=m.role||'system';
    div.className='msg '+role;
    const name=PERSONA_NAMES[role]||role;
    const meta=m.metadata||{};
    const isChair=(role==='chair');
    const badge=role==='context'?'RAG Context':(name+(isChair?' · Chair':''));
    const av=AVATAR[role]||'•';
    const body=role==='context'?formatContext(m.content):esc(m.content);
    div.innerHTML=`<div class="msg-head"><span class="av">${av}</span><span class="badge">${badge}</span></div>`+
      `<div class="body">${body}</div><div class="ts">${fmt(m.ts)}${meta.rag?' · '+meta.hits+' source chunks':''}</div>`;
    return div;
  }

  function formatContext(text){
    if(!text) return '';
    const lines=text.split('\n');
    return '<div style="font-size:12px;opacity:.85;white-space:pre-wrap">'+esc(text).slice(0,1200)+'</div>';
  }

  let _lastCount=0, _lastConv=null;

  async function loadConversation(id){
    const data=await api('GET','/conversations/'+id);
    if(!data.ok) return alert(data.error||'load failed');
    convId=id;
    const chat=document.getElementById('chat');
    chat.innerHTML='<div class="thinking" id="thinking"><div class="orb-lg"></div><div>Council is deliberating…</div></div>';
    const conv=data.conversation;
    document.getElementById('conv-subtitle').textContent=(conv.topic||'SHIMS continuous improvement')+' · Mode: '+(conv.mode||'free');
    currentMode=conv.mode||'free';
    setModeTab(currentMode);
    const msgs=conv.messages||[];
    // Stagger-reveal only the messages that are new since the last render of
    // this same conversation, so a fresh turn feels like members speaking.
    const sameConv=(id===_lastConv);
    const revealFrom=sameConv?_lastCount:msgs.length;
    const newRoles=[];
    msgs.forEach((m,i)=>{
      const el=renderMsg(m);
      if(i>=revealFrom){
        el.classList.add('reveal');
        el.style.animationDelay=((i-revealFrom)*0.45)+'s';
        if(m.role && m.role!=='user' && m.role!=='system' && m.role!=='context') newRoles.push(m.role);
      }
      chat.appendChild(el);
    });
    _lastCount=msgs.length; _lastConv=id;
    chat.scrollTop=chat.scrollHeight;
    renderRoster();
    if(newRoles.length) markSpoke(newRoles);
    await Promise.all([renderConvList(), loadProposals(), loadCouncilActions(), loadTasks()]);
  }

  async function renderConvList(){
    const data=await api('GET','/conversations?limit=20');
    const box=document.getElementById('conv-list');
    box.innerHTML='';
    (data.conversations||[]).forEach(c=>{
      const el=document.createElement('div');
      el.className='conv-item'+(c.id===convId?' active':'');
      el.innerHTML=`<div class="topic">${esc(c.topic||'Untitled')}</div><div class="meta">${new Date(c.created_at*1000).toLocaleString()}</div>`;
      el.onclick=()=>loadConversation(c.id);
      box.appendChild(el);
    });
  }

  async function newConversation(){
    const topic=prompt('Conversation topic or task:','');
    if(topic===null) return;
    const modeChoice=prompt('Mode: council, improvement, or free?','council');
    const mode=(modeChoice||'free').trim().toLowerCase();
    const data=await api('POST','/conversations',{topic, mode});
    if(data.ok) await loadConversation(data.conversation.id);
  }

  async function sendUser(){
    const input=document.getElementById('user-input');
    const text=input.value.trim();
    if(!text) return;
    if(!convId) await newConversation();
    await api('POST','/conversations/'+convId+'/message',{content:text});
    input.value='';
    await loadConversation(convId);
  }

  function setThinking(on, label){
    const th=document.getElementById('thinking');
    if(th) th.classList.toggle('active', on);
    const delib=document.getElementById('delib');
    if(delib){
      delib.classList.toggle('active', on);
      const t=document.getElementById('delib-text');
      if(t && label) t.textContent=label;
    }
    if(on){ const chat=document.getElementById('chat'); if(chat) chat.scrollTop=chat.scrollHeight; }
    setRosterThinking(on);
  }

  async function runTurn(){
    if(!convId) await newConversation();
    if(currentMode==='council') return runCouncilStream();
    setThinking(true, 'Thinking…');
    const chat=document.getElementById('chat'); chat.scrollTop=chat.scrollHeight;
    const data=await api('POST','/conversations/'+convId+'/turn',{});
    setThinking(false);
    if(!data.ok){
      if(auto) autoRunToggle();
      alert('Turn stopped: '+(data.error||'unknown'));
    }
    await loadConversation(convId);
  }

  let _streamedAny=false;
  function appendStreamMsg(m){
    const chat=document.getElementById('chat');
    const th=document.getElementById('thinking'); if(th) th.classList.remove('active');
    const el=renderMsg(m); el.classList.add('reveal');
    chat.appendChild(el); chat.scrollTop=chat.scrollHeight;
    _lastCount++; _streamedAny=true;
    if(m.role && m.role!=='user' && m.role!=='system' && m.role!=='context') markSpoke([m.role]);
  }

  function handleStreamEvent(ev){
    if(!ev||!ev.type) return;
    if(ev.type==='council_start'){ setRosterThinking(true); return; }
    if(ev.type==='chair_start'){
      const t=document.getElementById('delib-text'); if(t) t.textContent='The Chair is synthesising the verdict…';
      return;
    }
    if(ev.type==='message' && ev.message){ appendStreamMsg(ev.message); return; }
    if(ev.type==='member_error'){ appendStreamMsg({role:'system', content:'A member could not respond: '+(ev.error||''), ts:Date.now()/1000}); return; }
    if(ev.type==='error'){ appendStreamMsg({role:'system', content:'Turn stopped: '+(ev.error||'unknown'), ts:Date.now()/1000}); }
  }

  // Stream a council turn so each member surfaces live as they finish.
  async function runCouncilStream(){
    setThinking(true, 'The Council is deliberating…');
    const chat=document.getElementById('chat'); chat.scrollTop=chat.scrollHeight;
    let failed=null, sawDone=false; _streamedAny=false;
    try{
      const resp=await fetch(API+'/conversations/'+convId+'/turn/stream',
        {method:'POST', headers:{'Content-Type':'application/json'}});
      if(!resp.ok || !resp.body) throw new Error('stream unavailable');
      const reader=resp.body.getReader(); const dec=new TextDecoder(); let buf='';
      while(true){
        const {value,done}=await reader.read(); if(done) break;
        buf+=dec.decode(value,{stream:true});
        let nl;
        while((nl=buf.indexOf('\n'))>=0){
          const line=buf.slice(0,nl).trim(); buf=buf.slice(nl+1);
          if(!line) continue;
          try{ const ev=JSON.parse(line); if(ev.type==='done') sawDone=true; else handleStreamEvent(ev); }catch(e){}
        }
      }
      if(buf.trim()){ try{ const ev=JSON.parse(buf.trim()); if(ev.type!=='done') handleStreamEvent(ev); }catch(e){} }
    }catch(e){ failed=e.message; }
    setThinking(false);
    if(failed && !_streamedAny){
      // Nothing streamed — safe to run the non-streaming path so a turn still completes.
      const data=await api('POST','/conversations/'+convId+'/turn',{});
      if(!data.ok && auto) autoRunToggle();
      await loadConversation(convId);
      return;
    }
    if(failed || !sawDone){
      // Stream broke mid-way after some output — re-sync from the server rather
      // than re-running the turn (which would double-add members).
      await loadConversation(convId);
      return;
    }
    // Clean finish: sync side panels, keep the streamed bubbles in place.
    await Promise.all([renderConvList(), loadProposals(), loadCouncilActions(), loadTasks()]);
  }

  async function setMode(mode){
    currentMode=mode;
    if(mode==='council') maybeNudgeSetup(false);
    if(!convId){ setModeTab(mode); return; }
    await api('POST','/conversations/'+convId+'/mode',{mode});
    await loadConversation(convId);
  }
  function setModeTab(mode){
    document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
    const btn=document.getElementById('tab-'+(mode||'free'));
    if(btn) btn.classList.add('active');
    const council=document.getElementById('council-panel');
    if(council) council.style.display=(mode==='council')?'flex':'none';
  }

  async function finalize(){
    if(!convId) return;
    setThinking(true, 'The Chair is synthesising the verdict…');
    await api('POST','/conversations/'+convId+'/finalize',{});
    setThinking(false);
    await loadConversation(convId);
  }

  async function loadProposals(){
    const data=await api('GET','/proposals?limit=50');
    const box=document.getElementById('prop-list');
    box.innerHTML='';
    const votes=data.votes||{};
    const proposals=data.proposals||[];
    if(!proposals.length){ box.innerHTML='<div class="empty">No pending proposals.<br>Run a turn or improvement cycle to generate some.</div>'; return; }
    proposals.forEach(p=>{
      const card=document.createElement('div'); card.className='card';
      const vote=votes[p.id];
      if(vote) card.style.borderLeft='3px solid '+(vote==='approve'?'var(--success)':'var(--danger)');
      const detail=p.proposal||{};
      const meta=detail.meta||{};
      const title=meta.title||detail.title||p.type;
      const target=meta.target_instance||(p.source==='local'?'Local Factory':'Primary Omni');
      const why=meta.why_proposal||'';
      const problem=meta.problem_statement||'';
      const solution=meta.solution_proposed||'';
      const options=Array.isArray(meta.options_considered)?meta.options_considered:[];
      const files=Array.isArray(meta.files_to_change)?meta.files_to_change:[];
      const risk=meta.risk||'unknown';
      const benefit=meta.expected_benefit||'';
      const purpose=meta.purpose||'';
      card.innerHTML=`<div class="src">${esc(p.source||'unknown')} · ${esc(target)} · ${esc(p.type)}</div>
        <div class="title">${esc(title)}</div>
        ${purpose?`<div class="field"><b>Purpose:</b> ${esc(purpose)}</div>`:''}
        ${why?`<div class="field"><b>Why this proposal:</b> ${esc(why)}</div>`:''}
        ${problem?`<div class="field"><b>Problem statement:</b> ${esc(problem)}</div>`:''}
        ${solution?`<div class="field"><b>Solution proposed:</b> ${esc(solution)}</div>`:''}
        ${options.length?`<div class="field"><b>Options considered:</b><ul>${options.map(o=>`<li>${esc(typeof o==='string'?o:JSON.stringify(o))}</li>`).join('')}</ul></div>`:''}
        ${files.length?`<div class="field"><b>Files to change:</b> ${files.map(f=>`<code>${esc(f)}</code>`).join(', ')}</div>`:''}
        ${benefit?`<div class="field"><b>Expected benefit:</b> ${esc(benefit)}</div>`:''}
        <div class="field"><b>Risk:</b> ${esc(risk)}</div>
        <div class="btns">
          <button class="success" onclick="duobot.vote('${p.id}','approve')">Approve</button>
          <button class="danger" onclick="duobot.vote('${p.id}','reject')">Reject</button>
          <button onclick="duobot.rethink('${p.id}')">Rethink</button>
          <button class="danger" onclick="duobot.delProposal('${p.id}')">Delete</button>
        </div>`;
      box.appendChild(card);
    });
  }

  async function vote(pid, action){
    await api('POST','/proposals/'+pid+'/vote',{action});
    if(action==='approve'){
      if(confirm('Apply this approved proposal now?')){
        const res=await api('POST','/proposals/'+pid+'/apply',{});
        alert(res.ok?'Applied.':'Apply failed: '+(res.error||''));
      }
    }
    loadProposals();
  }

  async function delProposal(pid){
    if(!confirm('Permanently delete this proposal?')) return;
    const res=await api('POST','/proposals/'+pid+'/delete',{});
    if(!res.ok) alert(res.error||'delete failed');
    loadProposals();
  }

  async function rethink(pid){
    const feedback=prompt('Why should this be rethought? What is missing or wrong?','');
    if(feedback===null) return;
    const res=await api('POST','/proposals/'+pid+'/rethink',{feedback});
    if(!res.ok) alert(res.error||'rethink failed');
    loadProposals();
  }

  async function loadCouncilActions(){
    const box=document.getElementById('council-actions');
    if(!convId||!box){ if(box) box.innerHTML=''; return; }
    const data=await api('GET','/conversations/'+convId);
    if(!data.ok){ box.innerHTML=''; return; }
    const conv=data.conversation||{};
    const pending=conv.pending_council_actions||[];
    if(conv.mode!=='council' || !pending.length){ box.innerHTML='<div class="empty">No gated actions waiting for approval.</div>'; return; }
    box.innerHTML='';
    pending.forEach(a=>{
      const el=document.createElement('div'); el.className='card';
      el.innerHTML=`<div class="field"><b>${esc(a.tool)}</b> — ${esc(a.reason||'No reason given')}</div>
        <div class="field" style="opacity:.7;font-size:11px">Args: ${esc(JSON.stringify(a.args||{}))}</div>
        <div class="btns">
          <button class="success" onclick="duobot.councilApprove('${a.approval_id}')">Approve</button>
          <button class="danger" onclick="duobot.councilReject('${a.approval_id}')">Reject</button>
        </div>`;
      box.appendChild(el);
    });
  }

  async function councilApprove(approvalId){
    const res=await api('POST','/conversations/'+convId+'/council/approve',{approval_id:approvalId});
    if(!res.ok) alert(res.error||'approve failed');
    await loadConversation(convId);
  }
  async function councilReject(approvalId){
    const res=await api('POST','/conversations/'+convId+'/council/reject',{approval_id:approvalId});
    if(!res.ok) alert(res.error||'reject failed');
    await loadConversation(convId);
  }

  async function checkCapabilities(){
    const chat=document.getElementById('chat');
    const card=document.createElement('div'); card.className='msg chair';
    card.innerHTML=`<div class="badge">Capabilities</div><div class="body">checking…</div><div class="ts">${fmt(Date.now()/1000)}</div>`;
    chat.appendChild(card); chat.scrollTop=chat.scrollHeight;
    const data=convId? await api('POST','/conversations/'+convId+'/capabilities',{}) : await api('GET','/capabilities');
    const local=data.local||{}; const primary=data.primary||{};
    const caps=local.capabilities||{};
    const activeCaps=Object.keys(caps).filter(k=>caps[k]).join(', ') || 'unknown';
    card.querySelector('.body').textContent=[
      'Primary: '+(primary.provider||'auto')+'/'+(primary.model||'auto'),
      'Local: '+(local.ok?'online':'offline')+' '+(local.default_model||''),
      'Local capabilities: '+activeCaps,
      'Probe latency: '+(data.latency_ms||local.roundtrip_ms||0)+' ms'
    ].join('\n');
  }

  function autoRunToggle(){
    auto=!auto;
    document.getElementById('auto-btn').textContent='Auto: '+(auto?'ON':'OFF');
    if(autoTimer) clearInterval(autoTimer);
    if(auto){ runTurn(); autoTimer=setInterval(runTurn, 30000); }
  }

  async function startTask(){
    const title=document.getElementById('task-title').value.trim();
    const desc=document.getElementById('task-desc').value.trim();
    if(!title||!desc){ alert('Enter a title and description.'); return; }
    if(!convId) await newConversation();
    const data=await api('POST','/tasks',{conv_id:convId,title,description:desc});
    if(!data.ok) return alert(data.error||'task create failed');
    document.getElementById('task-title').value='';
    document.getElementById('task-desc').value='';
    await api('POST','/conversations/'+convId+'/message',{content:`[Started collaborative task: ${title}]`});
    await Promise.all([loadTasks(), loadConversation(convId)]);
  }

  async function loadTasks(){
    const data=await api('GET','/tasks'+(convId?'?conv_id='+convId:''));
    const box=document.getElementById('task-list');
    box.innerHTML='';
    (data.tasks||[]).forEach(t=>{
      const last=t.last_test||{};
      const el=document.createElement('div'); el.className='card';
      el.innerHTML=`<div class="title">${esc(t.title)}</div>
        <div class="status">${t.status}${last.ok?' ✅':last.ok===false?' ❌':''}</div>
        <div class="btns">
          <button onclick="duobot.taskRound('${t.id}')">Round</button>
          <button onclick="duobot.taskRun('${t.id}')">Run</button>
        </div>`;
      box.appendChild(el);
    });
  }

  async function taskRound(taskId){
    const data=await api('POST','/tasks/'+taskId+'/round',{});
    if(!data.ok) alert(data.error||'round failed');
    await loadTasks();
  }
  async function taskRun(taskId){
    const data=await api('POST','/tasks/'+taskId+'/run?max_rounds=10',{});
    if(!data.ok) alert(data.error||'run failed');
    await loadTasks();
  }

  function settingsTab(name){
    document.querySelectorAll('.tab2').forEach(t=>t.classList.remove('active'));
    document.getElementById('stab-'+name).classList.add('active');
    document.getElementById('settings-general').style.display=(name==='general')?'block':'none';
    document.getElementById('settings-council').style.display=(name==='council')?'block':'none';
    document.getElementById('settings-keys').style.display=(name==='keys')?'block':'none';
  }

  let currentSettings={};
  let currentProviderKeys={};
  async function openSettings(){
    const modal=document.getElementById('settings-modal');
    modal.classList.add('active');
    settingsTab('general');
    const [settingsData, modelsData, keysData] = await Promise.all([
      api('GET','/settings/ai'),
      api('GET','/settings/ollama-models'),
      sysApi('GET','/system/provider-keys')
    ]);
    const s=settingsData.settings||{}; currentSettings=s;
    currentProviderKeys=keysData.providers||{};
    document.getElementById('set-primary-provider').value=s.primary_provider||'kimi';
    document.getElementById('set-primary-model').value=s.primary_model||'';
    document.getElementById('set-council-auto').checked=!!s.council_auto_execute;
    document.getElementById('set-council-chair').value=s.council_chair||'primary';
    document.getElementById('set-council-rag').checked=!!(s.council_rag_enabled??true);

    const localSel=document.getElementById('set-local-model');
    localSel.innerHTML='';
    const models=(modelsData.models||[]).filter(m=>m);
    if(!models.length) models.push('qwen2.5:3b','qwen2.5:7b','qwen2.5-coder:14b');
    models.forEach(m=>{
      const opt=document.createElement('option'); opt.value=m; opt.textContent=m;
      if(m===s.local_model) opt.selected=true;
      localSel.appendChild(opt);
    });

    renderMemberSettings(s.council_personas||{});
    renderKeySettings(currentProviderKeys);
  }

  function renderMemberSettings(personas){
    const box=document.getElementById('member-settings');
    box.innerHTML='';
    const order=['primary','gemini','anthropic','openai','local'];
    order.forEach(mid=>{
      const p=personas[mid]||{};
      const name=PERSONA_NAMES[mid]||mid;
      let dlId='';
      if(mid==='primary') dlId='dl-models-primary';
      else if(p.provider==='anthropic'||(!p.provider&&mid==='anthropic')) dlId='dl-models-anthropic';
      else if(p.provider==='openai'||(!p.provider&&mid==='openai')) dlId='dl-models-openai';
      else if(p.provider==='google'||(!p.provider&&mid==='gemini')) dlId='dl-models-gemini';
      const listAttr=dlId?` list="${dlId}"`:'';
      const el=document.createElement('div'); el.className='member-card';
      el.innerHTML=`<h4>${name}</h4>
        <div class="form-row">
          <label>Enabled</label>
          <input type="checkbox" id="mem-${mid}-enabled" ${(p.enabled??true)?'checked':''}>
        </div>
        <div class="form-row">
          <label>Provider override</label>
          <select id="mem-${mid}-provider" onchange="duobot.memberProviderChanged('${mid}')">
            <option value="" ${!p.provider?'selected':''}>default</option>
            <option value="anthropic" ${p.provider==='anthropic'?'selected':''}>Anthropic</option>
            <option value="openai" ${p.provider==='openai'?'selected':''}>OpenAI</option>
            <option value="google" ${p.provider==='google'?'selected':''}>Google</option>
            <option value="ollama" ${p.provider==='ollama'?'selected':''}>Ollama</option>
            <option value="primary" ${p.provider==='primary'?'selected':''}>primary</option>
          </select>
        </div>
        <div class="form-row">
          <label>Model override</label>
          <input id="mem-${mid}-model" value="${esc(p.model||'')}" placeholder="e.g. gpt-4o"${listAttr}>
        </div>
        <div class="form-row">
          <label>Temperature</label>
          <input type="number" step="0.1" min="0" max="1" id="mem-${mid}-temp" value="${p.temperature??0.6}">
        </div>
        <div class="form-row">
          <label>System prompt</label>
          <textarea id="mem-${mid}-prompt" rows="4">${esc(p.system_prompt||DEFAULT_MEMBER_PROMPTS[mid]||'')}</textarea>
        </div>`;
      box.appendChild(el);
    });
  }

  function memberProviderChanged(mid){
    const provider=document.getElementById('mem-'+mid+'-provider').value;
    const input=document.getElementById('mem-'+mid+'-model');
    const listId=(provider==='google'?'gemini':provider==='anthropic'?'anthropic':provider==='openai'?'openai':'primary');
    const dl=document.getElementById('dl-models-'+listId);
    if(dl) input.setAttribute('list','dl-models-'+listId);
    else input.removeAttribute('list');
  }

  function renderKeySettings(providers){
    const box=document.getElementById('key-settings');
    box.innerHTML='';
    const list=[
      {id:'anthropic', label:'Anthropic / Claude', env:'ANTHROPIC_API_KEY', models:CURATED_MODELS.anthropic},
      {id:'openai', label:'OpenAI', env:'OPENAI_API_KEY', models:CURATED_MODELS.openai},
      {id:'gemini', label:'Google Gemini', env:'GEMINI_API_KEY', models:CURATED_MODELS.gemini},
    ];
    list.forEach(item=>{
      const info=providers[item.id]||{};
      const masked=info.masked||'';
      const configured=info.configured?'ok':'bad';
      const model=info.model||'';
      const el=document.createElement('div');
      el.innerHTML=`<div class="key-row">
        <div class="form-row">
          <label>${esc(item.label)} key <span style="opacity:.6">(${esc(item.env)})</span></label>
          <input type="password" id="key-${item.id}" value="${esc(masked)}" placeholder="sk-... or paste key">
        </div>
        <div class="form-row narrow">
          <label>Model</label>
          <input id="key-model-${item.id}" list="dl-models-${item.id}" value="${esc(model)}" placeholder="model">
        </div>
        <button class="btn" style="margin-bottom:0" onclick="duobot.testKey('${item.id}')">Test</button>
      </div>
      <div id="key-status-${item.id}" class="key-status ${configured}">${configured==='ok'?'Configured':'No key saved'}</div>`;
      box.appendChild(el);
    });
  }

  async function testKey(pid){
    const status=document.getElementById('key-status-'+pid);
    status.className='key-status'; status.textContent='Testing…';
    const key=document.getElementById('key-'+pid).value.trim();
    const model=document.getElementById('key-model-'+pid).value.trim();
    const masked=(currentProviderKeys[pid]||{}).masked||'';
    const sendKey=key && key!==masked ? key : '';
    const res=await sysApi('POST','/system/providers/'+pid+'/test',{api_key:sendKey, model:model});
    status.className='key-status '+(res.ok?'ok':'bad');
    status.textContent=(res.ok?'OK':'Failed')+': '+(res.reply||res.error||'unknown');
  }

  function closeSettings(){ document.getElementById('settings-modal').classList.remove('active'); }

  async function saveSettings(){
    const personas={};
    ['primary','gemini','anthropic','openai','local'].forEach(mid=>{
      personas[mid]={
        enabled: document.getElementById('mem-'+mid+'-enabled').checked,
        provider: document.getElementById('mem-'+mid+'-provider').value,
        model: document.getElementById('mem-'+mid+'-model').value,
        temperature: parseFloat(document.getElementById('mem-'+mid+'-temp').value),
        system_prompt: document.getElementById('mem-'+mid+'-prompt').value,
      };
    });
    const body={
      primary_provider: document.getElementById('set-primary-provider').value,
      primary_model: document.getElementById('set-primary-model').value,
      local_model: document.getElementById('set-local-model').value,
      council_auto_execute: document.getElementById('set-council-auto').checked,
      council_chair: document.getElementById('set-council-chair').value,
      council_rag_enabled: document.getElementById('set-council-rag').checked,
      council_personas: personas,
    };
    await api('POST','/settings/ai',body);

    // Save provider keys/models if changed.
    const keyUpdates=[
      {id:'anthropic', keyId:'key-anthropic', modelId:'key-model-anthropic'},
      {id:'openai', keyId:'key-openai', modelId:'key-model-openai'},
      {id:'gemini', keyId:'key-gemini', modelId:'key-model-gemini'},
    ];
    for(const u of keyUpdates){
      const keyInput=document.getElementById(u.keyId);
      const modelInput=document.getElementById(u.modelId);
      if(!keyInput) continue;
      const keyVal=keyInput.value.trim();
      const masked=(currentProviderKeys[u.id]||{}).masked||'';
      const body={provider:u.id, model:modelInput?modelInput.value.trim():''};
      if(keyVal && keyVal!==masked) body.api_key=keyVal;
      if(body.api_key || body.model) await sysApi('POST','/system/provider-keys',body);
    }

    closeSettings();
    alert('AI settings saved.');
  }

  window.duobot={
    newConversation, sendUser, runTurn, setMode, finalize, checkCapabilities,
    vote, delProposal, rethink, councilApprove, councilReject,
    autoRunToggle, openSettings, closeSettings, saveSettings, settingsTab,
    memberProviderChanged, testKey, dismissNudge,
    startTask, taskRound, taskRun,
  };

  function getUrlParams(){
    const params=new URLSearchParams(window.location.search);
    return {
      mode:(params.get('mode')||'').trim().toLowerCase(),
      topic:(params.get('topic')||'').trim(),
    };
  }

  (async function init(){
    renderStars();
    await refreshProviderStatus();
    renderRoster();
    await renderConvList();
    const {mode,topic}=getUrlParams();
    if(mode==='council'){ currentMode='council'; setModeTab('council'); maybeNudgeSetup(false); }
    if((mode==='council'||mode==='improvement'||mode==='free') && topic){
      const data=await api('POST','/conversations',{topic, mode});
      if(data.ok){
        convId=data.conversation.id;
        await api('POST','/conversations/'+convId+'/message',{content:topic});
        if(window.history && window.history.replaceState) window.history.replaceState({},'',window.location.pathname);
        await loadConversation(convId);
        if(mode==='council') await runTurn();
        return;
      }
    }
    const data=await api('GET','/conversations?limit=1');
    if(data.conversations&&data.conversations.length) await loadConversation(data.conversations[0].id);
    else await newConversation();
  })();
})();
