/* SHIMS Omni — Agentic UI layer.
 * Renders the agent's live "hands" inline in chat (tool calls, background coder
 * jobs, self-patch diffs), plus a command palette (Ctrl/Cmd-K), a capability
 * panel, a jobs tray, @file mentions and a Stop button. Loaded AFTER
 * shims_omni.js; everything here is additive — nothing existing is removed.
 *
 * The chat stream consumer in shims_omni.js calls window.renderToolCard /
 * renderToolResult / renderJobCard / renderDiffCard for the new event types. */
(function(){
  'use strict';
  const qs = (s, r) => (r||document).querySelector(s);
  const esc = (s) => (window.escapeHtml ? window.escapeHtml(s) : String(s==null?'':s));
  const toastMsg = (m, t) => { try{ window.toast && window.toast(m, t); }catch(e){} };
  window.shimsAgentMode = (localStorage.getItem('shims_agent_mode') !== '0');

  // ---------------- inline tool cards ----------------
  const TOOL_ICON = {
    'shell.run':'▶', 'fs.read':'📄', 'fs.list':'🗂', 'fs.glob':'🔍', 'fs.search':'🔎',
    'fs.write':'✎', 'fs.edit':'✎', 'fs.mkdir':'📁', 'fs.move':'↪', 'fs.delete':'🗑',
    'code.run':'λ', 'web.search':'🌐', 'web.fetch':'🌐', 'coder.spawn':'🤖',
    'coder.status':'🤖', 'skill.learn':'★', 'self.patch':'🧬',
    'coder.create_project':'📦', 'coder.read_file':'📄', 'coder.write_file':'✎',
    'coder.run_shell':'▶', 'coder.run_project':'▶', 'coder.search':'🔎',
    'coder.install':'📦', 'coder.git_commit':'🌿',
    'neural.generate_proposal':'🧬', 'neural.test_proposal':'🧪',
    'neural.apply_proposal':'🚀', 'neural.reflect':'🔮',
    'desktop.run_python':'🐍', 'desktop.interpreter':'📊',
    'browser.visit':'🌐', 'browser.search':'🔍', 'browser.click':'👆',
    'browser.extract':'📋', 'browser.fill_form':'📝', 'browser.screenshot':'📸',
    'browser.scroll':'📜', 'task.check_status':'📊', 'task.list':'📋'
  };

  function argPreview(tool, args){
    args = args || {};
    if(tool === 'shell.run') return esc(args.command || '');
    if(tool && tool.indexOf('fs.') === 0) return esc(args.path || args.pattern || args.query || (args.src||'')+(args.dst?(' → '+args.dst):''));
    if(tool === 'web.search') return esc(args.query || '');
    if(tool === 'web.fetch') return esc(args.url || '');
    if(tool === 'code.run') return esc((args.language||'python'));
    if(tool === 'coder.spawn') return esc(args.goal || args.name || '');
    if(tool === 'self.patch') return esc(args.path || '');
    if(tool && tool.indexOf('coder.') === 0) return esc(args.project_id ? (args.project_id + ': ' + (args.file_path || args.command || args.query || args.message || '')) : (args.name || ''));
    if(tool && tool.indexOf('neural.') === 0) return esc(args.intent || args.proposal_id || '');
    if(tool === 'desktop.run_python') return esc((args.code||'').slice(0,60) + '...');
    if(tool === 'desktop.interpreter') return esc((args.code||'').slice(0,60) + '...');
    if(tool && tool.indexOf('browser.') === 0) return esc(args.url || args.query || args.selector || '');
    if(tool && tool.indexOf('task.') === 0) return esc(args.task_id || args.status || '');
    try{ return esc(JSON.stringify(args).slice(0,140)); }catch(e){ return ''; }
  }

  window.renderToolCard = function(ch, bubble){
    const target = bubble ? bubble.querySelector('.content') : qs('#transcript');
    if(!target) return null;
    const card = document.createElement('div');
    card.className = 'agent-tool-card running';
    const icon = TOOL_ICON[ch.tool] || '⚙';
    card.innerHTML =
      '<div class="atc-head">'+
        '<span class="atc-icon">'+icon+'</span>'+
        '<span class="atc-name">'+esc(ch.tool||'tool')+'</span>'+
        '<code class="atc-arg">'+argPreview(ch.tool, ch.args)+'</code>'+
        '<span class="atc-chip running">running…</span>'+
        '<span class="atc-toggle">▾</span>'+
      '</div>'+
      '<pre class="atc-out" style="display:none"></pre>';
    const head = card.querySelector('.atc-head');
    head.addEventListener('click', () => {
      const out = card.querySelector('.atc-out');
      out.style.display = out.style.display === 'none' ? 'block' : 'none';
    });
    target.appendChild(card);
    target.parentElement && (target.parentElement.scrollTop = target.parentElement.scrollHeight);
    return card;
  };

  function resultText(r){
    if(!r) return '';
    if(r.error) return 'error: '+r.error;
    const out = [];
    if(typeof r.stdout === 'string' && r.stdout.trim()) out.push(r.stdout.trim());
    if(typeof r.stderr === 'string' && r.stderr.trim()) out.push('[stderr]\n'+r.stderr.trim());
    if(typeof r.text === 'string' && r.text.trim()) out.push(r.text.trim());
    if(typeof r.content === 'string' && r.content.trim()) out.push(r.content.trim());
    if(Array.isArray(r.entries)) out.push(r.entries.map(e=>(e.is_dir?'📁 ':'   ')+e.name).join('\n'));
    if(Array.isArray(r.matches)) out.push(r.matches.join('\n'));
    if(Array.isArray(r.name_matches) && r.name_matches.length) out.push('names:\n'+r.name_matches.join('\n'));
    if(Array.isArray(r.content_matches) && r.content_matches.length) out.push('content:\n'+r.content_matches.map(m=>m.path+':'+m.line+'  '+m.text).join('\n'));
    if(Array.isArray(r.results)) out.push(r.results.map((x,i)=>(i+1)+'. '+(x.title||'')+'\n   '+(x.url||'')).join('\n'));
    if(r.project_id) out.push('project: '+r.project_id+(r.name ? ' ('+r.name+')' : ''));
    if(r.proposal_id) out.push('proposal: '+r.proposal_id+(r.title ? ' — '+r.title : ''));
    if(r.diff) out.push('diff:\n'+r.diff.slice(0,4000));
    if(r.intent) out.push('intent: '+r.intent);
    if(r.thought) out.push('thought: '+r.thought);
    if(r.note) out.push(r.note);
    if(!out.length){ try{ return JSON.stringify(r, null, 2).slice(0,4000); }catch(e){ return ''; } }
    return out.join('\n').slice(0, 8000);
  }

  function renderInterpreterFigures(card, r){
    if(!r) return;
    const out = card && card.querySelector('.atc-out');
    if(!out) return;
    let html = '';
    if(Array.isArray(r.figures) && r.figures.length){
      html += '<div class="interp-figures">';
      r.figures.forEach((b64, i) => {
        html += '<img class="interp-figure" src="data:image/png;base64,'+esc(b64)+'" alt="figure '+(i+1)+'" />';
      });
      html += '</div>';
    }
    if(Array.isArray(r.artifacts) && r.artifacts.length){
      html += '<div class="interp-artifacts"><b>Generated files:</b><ul>';
      r.artifacts.forEach(a => {
        html += '<li><code>'+esc(a.path)+'</code> ('+esc(a.mime||'')+', '+Math.round((a.size||0)/1024)+' KB)</li>';
      });
      html += '</ul></div>';
    }
    if(html){
      const wrap = document.createElement('div');
      wrap.className = 'atc-out-interp';
      wrap.innerHTML = html;
      card.appendChild(wrap);
    }
  }

  window.renderToolResult = function(card, ch, bubble){
    if(!card){ card = window.renderToolCard(ch, bubble); }
    if(!card) return;
    const ok = ch.ok !== false;
    card.classList.remove('running'); card.classList.add(ok ? 'ok' : 'fail');
    const chip = card.querySelector('.atc-chip');
    if(chip){ chip.className = 'atc-chip ' + (ok?'ok':'fail'); chip.textContent = ok ? 'done' : 'failed'; }
    const out = card.querySelector('.atc-out');
    if(out){ out.textContent = resultText(ch.result); if(out.textContent.trim()) out.style.display='block'; }
    if(ch.tool === 'desktop.interpreter' && ch.result) renderInterpreterFigures(card, ch.result);
  };

  // ---------------- self-patch diff cards ----------------
  window.renderDiffCard = function(ch, bubble){
    const target = bubble ? bubble.querySelector('.content') : qs('#transcript');
    if(!target) return;
    const v = ch.validation || {};
    const ok = (v.status === 'validated');
    const card = document.createElement('div');
    card.className = 'agent-diff-card';
    const diffHtml = String(ch.diff||'').split('\n').map(l=>{
      let cls = '';
      if(l.startsWith('+') && !l.startsWith('+++')) cls='add';
      else if(l.startsWith('-') && !l.startsWith('---')) cls='del';
      else if(l.startsWith('@@')) cls='hunk';
      return '<span class="dl '+cls+'">'+esc(l)+'</span>';
    }).join('\n');
    card.innerHTML =
      '<div class="adc-head"><span class="atc-icon">🧬</span> Self-patch · <code>'+esc(ch.path||'')+'</code>'+
        '<span class="atc-chip '+(ok?'ok':'fail')+'">'+esc(v.status||'?')+'</span></div>'+
      '<pre class="adc-diff">'+diffHtml+'</pre>'+
      '<div class="adc-actions"></div>';
    const actions = card.querySelector('.adc-actions');
    const approvalId = ch.approval && ch.approval.approval_id;
    const ap = document.createElement('button'); ap.className='v9-btn ok'; ap.textContent='Approve & apply';
    const no = document.createElement('button'); no.className='v9-btn no'; no.textContent='Discard';
    ap.onclick = () => { window.decideApproval && window.decideApproval(approvalId, true); ap.disabled=true; ap.textContent='Applied'; };
    no.onclick = () => { window.decideApproval && window.decideApproval(approvalId, false); card.style.opacity=.5; };
    if(!approvalId){ ap.disabled = true; }
    actions.appendChild(ap); actions.appendChild(no);
    target.appendChild(card);
  };

  // ---------------- proposal card with Yes/No dialog ----------------
  window.renderProposalCard = function(prop, bubble){
    const target = bubble ? bubble.querySelector('.content') : qs('#transcript');
    if(!target) return;
    const card = document.createElement('div');
    card.className = 'agent-proposal-card';
    card.dataset.proposalId = prop.proposal_id || '';
    const diffHtml = String(prop.diff||'').split('\n').map(l=>{
      let cls = '';
      if(l.startsWith('+') && !l.startsWith('+++')) cls='add';
      else if(l.startsWith('-') && !l.startsWith('---')) cls='del';
      else if(l.startsWith('@@')) cls='hunk';
      return '<span class="dl '+cls+'">'+esc(l)+'</span>';
    }).join('\n');
    const hasDiff = (prop.diff||'').trim().length > 0;
    card.innerHTML =
      '<div class="apc-head">'+
        '<span class="atc-icon">🧬</span> <b>Evolution Proposal</b>'+
        '<span class="apc-badge" id="apc-badge-'+esc(prop.proposal_id||'')+'">🔬 Sandbox: pending</span>'+
      '</div>'+
      '<div class="apc-body">'+
        '<div class="apc-meta"><b>Intent:</b> '+esc(prop.intent||'')+'</div>'+
        '<div class="apc-meta"><b>File:</b> <code>'+esc(prop.file_path||'')+'</code></div>'+
        (prop.thought ? '<div class="apc-meta"><b>Reasoning:</b> '+esc(prop.thought)+'</div>' : '')+
        (prop.model_used ? '<div class="apc-meta" style="font-size:10px;color:var(--text-dim)">Model: '+esc(prop.model_used)+'</div>' : '')+
        (hasDiff ? '<details class="apc-diff-wrap"><summary>View diff ('+(String(prop.diff||'').split("\n").length)+' lines)</summary><pre class="adc-diff">'+diffHtml+'</pre></details>' : '')+
      '</div>'+
      '<div class="apc-actions">'+
        '<button class="v9-btn ok" id="apc-yes-'+esc(prop.proposal_id||'')+'">✓ Yes, Apply</button>'+
        '<button class="v9-btn no" id="apc-no-'+esc(prop.proposal_id||'')+'">✗ No, Discard</button>'+
      '</div>';
    target.appendChild(card);

    // Bind Yes/No buttons
    const yesBtn = card.querySelector('#apc-yes-'+esc(prop.proposal_id||''));
    const noBtn = card.querySelector('#apc-no-'+esc(prop.proposal_id||''));
    if(yesBtn) yesBtn.onclick = async () => {
      yesBtn.disabled = true; noBtn.disabled = true;
      yesBtn.textContent = 'Applying...';
      try{
        const r = await fetch('/api/neural-agent/proposals/'+encodeURIComponent(prop.proposal_id||'')+'/apply', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({approved_by:'ui-human'})});
        const d = await r.json();
        if(d.ok || d.status==='applied'){
          yesBtn.textContent = '✓ Applied';
          card.querySelector('.apc-badge').textContent = '✓ Applied';
          card.querySelector('.apc-badge').className = 'apc-badge ok';
        } else {
          yesBtn.textContent = 'Failed';
          yesBtn.className = 'v9-btn no';
        }
      }catch(e){ yesBtn.textContent = 'Error'; yesBtn.className='v9-btn no'; }
    };
    if(noBtn) noBtn.onclick = async () => {
      yesBtn.disabled = true; noBtn.disabled = true;
      noBtn.textContent = 'Discarding...';
      try{
        const r = await fetch('/api/neural-agent/proposals/'+encodeURIComponent(prop.proposal_id||'')+'/reject', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({reviewer:'ui-human'})});
        card.style.opacity = .5;
        noBtn.textContent = '✗ Discarded';
        card.querySelector('.apc-badge').textContent = '✗ Discarded';
      }catch(e){ noBtn.textContent = 'Error'; }
    };

    // Start sandbox validation in background
    startSandboxValidation(prop.proposal_id, card);
  };

  async function startSandboxValidation(proposalId, card){
    if(!proposalId || !card) return;
    const badge = card.querySelector('.apc-badge');
    if(!badge) return;
    badge.textContent = '🔬 Sandbox: running...';
    try{
      const r = await fetch('/api/neural-agent/proposals/'+encodeURIComponent(proposalId)+'/test', {method:'POST'});
      const d = await r.json();
      if(d.ok && d.status==='validated'){
        badge.textContent = '✓ Sandbox: passed';
        badge.className = 'apc-badge ok';
      } else if(d.ok && d.status==='failed'){
        badge.textContent = '✗ Sandbox: failed — '+esc(d.message||'');
        badge.className = 'apc-badge fail';
      } else {
        badge.textContent = '✗ Sandbox: '+esc(d.message||'error');
        badge.className = 'apc-badge fail';
      }
    }catch(e){ badge.textContent = '✗ Sandbox: unreachable'; badge.className = 'apc-badge fail'; }
  }

  // ---------------- background coder jobs ----------------
  const jobStreams = {};
  window.renderJobCard = function(job, bubble){
    if(!job || !job.job_id) return;
    const target = bubble ? bubble.querySelector('.content') : qs('#transcript');
    if(!target) return;
    const card = document.createElement('div');
    card.className = 'agent-job-card';
    card.dataset.jobId = job.job_id;
    card.innerHTML =
      '<div class="ajc-head"><span class="atc-icon">🤖</span><b>Codex job</b> '+
        '<span class="ajc-goal">'+esc(job.goal||job.name||'')+'</span>'+
        '<span class="atc-chip running" data-status>queued</span></div>'+
      '<pre class="ajc-log"></pre>'+
      '<div class="ajc-actions">'+
        '<button class="v9-btn" data-open>Open in Coder</button>'+
        '<button class="v9-btn no" data-cancel>Cancel</button></div>';
    const log = card.querySelector('.ajc-log');
    const statusEl = card.querySelector('[data-status]');
    card.querySelector('[data-open]').onclick = () => { try{ window.openView ? window.openView('coder') : (qs('.nav-row[data-view="coder"]')||{}).click(); }catch(e){} };
    card.querySelector('[data-cancel]').onclick = () => { fetch('/agent/jobs/'+job.job_id+'/cancel',{method:'POST'}); statusEl.textContent='cancelling'; };
    target.appendChild(card);
    addToTray(job, card);
    startJobStream(job.job_id, log, statusEl, card);
    return card;
  };

  function startJobStream(jobId, logEl, statusEl, card){
    if(jobStreams[jobId]) return;
    let es;
    try{ es = new EventSource('/agent/jobs/'+jobId+'/stream'); }
    catch(e){ logEl.textContent = 'stream unavailable'; return; }
    jobStreams[jobId] = es;
    const append = (line) => { logEl.textContent += (logEl.textContent?'\n':'') + line; logEl.scrollTop = logEl.scrollHeight; };
    es.onmessage = (ev) => {
      let d; try{ d = JSON.parse(ev.data); }catch(_e){ return; }
      if(d.stage === 'start'){ statusEl.textContent='planning'; statusEl.className='atc-chip running'; append('▸ goal: '+(d.goal||'')); }
      else if(d.stage === 'project'){ append('▸ project '+(d.project_id||'')); }
      else if(d.stage === 'step'){
        statusEl.textContent='step '+(d.step||'');
        append('• step '+(d.step||'')+': '+(d.explanation||'').slice(0,200));
        if(d.files_changed && d.files_changed.length) append('   files: '+d.files_changed.join(', '));
        if(d.run_ok === false && d.stderr) append('   ✗ '+String(d.stderr).split('\n').slice(-3).join(' ').slice(0,200));
        if(d.run_ok === true) append('   ✓ ran ok');
      } else if(d.stage === 'done'){
        const ok = !!d.ok;
        statusEl.textContent = ok ? 'done ✓' : 'failed'; statusEl.className = 'atc-chip ' + (ok?'ok':'fail');
        append(ok ? ('✓ finished — '+((d.files||[]).length)+' files, entry '+(d.entry||'')) : ('✗ '+(d.error||'failed')));
        updateTray(jobId, ok?'done':'failed');
        es.close(); delete jobStreams[jobId];
      }
    };
    es.addEventListener('end', () => { try{ es.close(); }catch(e){} delete jobStreams[jobId]; });
    es.onerror = () => { /* keep card; stream may have ended */ };
  }

  // ---------------- jobs tray ----------------
  function tray(){
    let t = qs('#jobs-tray');
    if(!t){
      t = document.createElement('div'); t.id='jobs-tray';
      t.innerHTML = '<div class="jt-head">Background jobs <span class="jt-min">—</span></div><div class="jt-body"></div>';
      document.body.appendChild(t);
      t.querySelector('.jt-head').onclick = () => t.classList.toggle('collapsed');
    }
    return t;
  }
  function addToTray(job, card){
    const body = tray().querySelector('.jt-body');
    let row = body.querySelector('[data-tray="'+job.job_id+'"]');
    if(!row){
      row = document.createElement('div'); row.className='jt-row'; row.dataset.tray = job.job_id;
      row.innerHTML = '<span class="jt-dot running"></span><span class="jt-name"></span><span class="jt-status">queued</span>';
      row.querySelector('.jt-name').textContent = (job.name||job.goal||('job '+job.job_id)).slice(0,40);
      row.onclick = () => { card.scrollIntoView({behavior:'smooth', block:'center'}); card.classList.add('flash'); setTimeout(()=>card.classList.remove('flash'),900); };
      body.appendChild(row);
    }
    tray().classList.remove('collapsed');
  }
  function updateTray(jobId, status){
    const row = qs('#jobs-tray [data-tray="'+jobId+'"]'); if(!row) return;
    row.querySelector('.jt-status').textContent = status;
    const dot = row.querySelector('.jt-dot'); dot.className = 'jt-dot ' + (status==='done'?'ok':(status==='failed'?'fail':'running'));
  }

  async function refreshJobsTray(){
    try{
      const d = await (await fetch('/agent/jobs?limit=12')).json();
      (d.jobs||[]).forEach(j => {
        const exists = qs('#jobs-tray [data-tray="'+j.id+'"]');
        if(!exists && (j.status==='running'||j.status==='queued')){
          // best-effort: surface running jobs that started elsewhere
        }
      });
    }catch(e){}
  }

  // ---------------- command palette (Ctrl/Cmd-K) ----------------
  const SLASH = [
    {k:'/run <cmd>', d:'Run a shell command', t:'slash'},
    {k:'/build <goal>', d:'Background Codex build', t:'slash'},
    {k:'/edit <path>', d:'Edit a file', t:'slash'},
    {k:'/patch <what>', d:'Patch SHIMS itself', t:'slash'},
    {k:'/web <query>', d:'Search the web', t:'slash'},
    {k:'/image, /doc, /ppt, /audio', d:'Generate media', t:'slash'}
  ];
  function palette(){
    let p = qs('#cmd-palette');
    if(!p){
      p = document.createElement('div'); p.id='cmd-palette'; p.className='hidden';
      p.innerHTML = '<div class="cp-box"><input id="cp-input" placeholder="Type a command, module, or tool…  (Esc to close)"><div id="cp-list"></div></div>';
      document.body.appendChild(p);
      p.addEventListener('click', e => { if(e.target === p) hidePalette(); });
      qs('#cp-input', p).addEventListener('input', renderPalette);
      qs('#cp-input', p).addEventListener('keydown', e => {
        if(e.key === 'Escape') hidePalette();
        if(e.key === 'Enter'){ const first = qs('#cp-list .cp-item'); if(first) first.click(); }
      });
    }
    return p;
  }
  function paletteItems(){
    const items = [];
    document.querySelectorAll('.nav-row[data-view]').forEach(r => {
      items.push({label: r.textContent.replace(/\d+$/,'').trim(), kind:'module', view: r.getAttribute('data-view')});
    });
    SLASH.forEach(s => items.push({label: s.k, sub: s.d, kind:'slash'}));
    (window.AGENT_TOOLS||[]).forEach(t => items.push({label: t, sub:'agent tool', kind:'tool'}));
    return items;
  }
  function renderPalette(){
    const q = (qs('#cp-input').value||'').toLowerCase().trim();
    const list = qs('#cp-list'); list.innerHTML = '';
    paletteItems().filter(it => !q || (it.label+' '+(it.sub||'')).toLowerCase().includes(q)).slice(0,40).forEach(it => {
      const el = document.createElement('div'); el.className='cp-item';
      el.innerHTML = '<span class="cp-kind '+it.kind+'">'+it.kind+'</span><span class="cp-label">'+esc(it.label)+'</span>'+(it.sub?'<span class="cp-sub">'+esc(it.sub)+'</span>':'');
      el.onclick = () => {
        if(it.kind === 'module'){ const row = qs('.nav-row[data-view="'+it.view+'"]'); row && row.click(); }
        else { const inp = qs('#input'); if(inp){ inp.value = (it.kind==='slash' ? it.label.split(' ')[0]+' ' : it.label+' '); inp.focus(); } }
        hidePalette();
      };
      list.appendChild(el);
    });
  }
  function showPalette(){ const p = palette(); p.classList.remove('hidden'); const i = qs('#cp-input'); i.value=''; renderPalette(); setTimeout(()=>i.focus(),10); }
  function hidePalette(){ const p = qs('#cmd-palette'); if(p) p.classList.add('hidden'); }
  window.shimsPalette = showPalette;

  // ---------------- capability panel ----------------
  async function showCapabilities(){
    let m = qs('#cap-panel');
    if(!m){ m = document.createElement('div'); m.id='cap-panel'; m.className='hidden'; document.body.appendChild(m);
      m.addEventListener('click', e => { if(e.target === m) m.classList.add('hidden'); }); }
    m.innerHTML = '<div class="cap-box"><div class="cap-load">Loading capabilities…</div></div>';
    m.classList.remove('hidden');
    try{
      const d = await (await fetch('/agent/capabilities')).json();
      window.AGENT_TOOLS = (d.tools||[]).map(t=>t.name);
      const tools = (d.tools||[]).map(t => '<div class="cap-tool"><b>'+esc(t.name)+'</b> <span class="cap-risk '+esc(t.risk_default)+'">'+esc(t.risk_default)+'</span><div>'+esc(t.description)+'</div></div>').join('');
      const roots = (d.allowed_roots||[]).map(r=>'<code>'+esc(r)+'</code>').join(' ') || '<i>none yet</i>';
      qs('.cap-box', m).innerHTML =
        '<div class="cap-title">⚡ What SHIMS Omni can do <button class="v9-btn" id="cap-close">Close</button></div>'+
        '<p class="cap-intro">I can run commands, read/write files anywhere, run code, browse the web, build projects in the background, and even <b>rewrite my own code</b>. Safe actions run instantly; risky ones ask you first.</p>'+
        '<div class="cap-roots"><b>Repo (full power):</b> <code>'+esc(d.repo_root||'')+'</code><br><b>Extra allowed folders:</b> '+roots+
          ' <span class="cap-addroot"><input id="cap-root-input" placeholder="C:\\path\\to\\folder"><button class="v9-btn ok" id="cap-add-root">Add</button></span></div>'+
        '<div class="cap-tools">'+tools+'</div>';
      qs('#cap-close', m).onclick = () => m.classList.add('hidden');
      qs('#cap-add-root', m).onclick = async () => {
        const v = (qs('#cap-root-input', m).value||'').trim(); if(!v) return;
        await fetch('/agent/roots',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:v})});
        toastMsg('Added allowed folder','info'); showCapabilities();
      };
    }catch(e){ qs('.cap-box', m).innerHTML = '<div class="cap-load">Could not load capabilities: '+esc(e.message)+'</div>'; }
  }
  window.shimsCapabilities = showCapabilities;

  // ---------------- @file mentions ----------------
  let mentionBox, mentionTimer;
  function ensureMentionBox(){
    if(!mentionBox){ mentionBox = document.createElement('div'); mentionBox.id='mention-box'; mentionBox.className='hidden'; document.body.appendChild(mentionBox); }
    return mentionBox;
  }
  function currentMention(input){
    const v = input.value, pos = input.selectionStart;
    const before = v.slice(0, pos);
    const m = before.match(/@([\w./\\-]{0,60})$/);
    return m ? {term: m[1], start: pos - m[0].length, end: pos} : null;
  }
  async function onInputMention(e){
    const input = e.target; const mention = currentMention(input);
    const box = ensureMentionBox();
    if(!mention){ box.classList.add('hidden'); return; }
    clearTimeout(mentionTimer);
    mentionTimer = setTimeout(async () => {
      try{
        const d = await (await fetch('/agent/tool',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tool:'fs.search', args:{query: mention.term || '.py', root: '.'}})})).json();
        const hits = (d.name_matches||[]).slice(0,8);
        if(!hits.length){ box.classList.add('hidden'); return; }
        box.innerHTML = hits.map(h=>'<div class="mb-item" data-path="'+esc(h)+'">'+esc(h)+'</div>').join('');
        const r = input.getBoundingClientRect();
        box.style.left = r.left+'px'; box.style.top = (r.top - Math.min(220, hits.length*30) - 6)+'px'; box.style.width = Math.min(560, r.width)+'px';
        box.classList.remove('hidden');
        box.querySelectorAll('.mb-item').forEach(it => it.onclick = () => {
          const p = it.getAttribute('data-path');
          input.value = input.value.slice(0, mention.start) + p + ' ' + input.value.slice(mention.end);
          box.classList.add('hidden'); input.focus();
        });
      }catch(_e){ box.classList.add('hidden'); }
    }, 220);
  }

  // ---------------- composer agent toolbar ----------------
  function injectToolbar(){
    const composer = qs('.composer'); if(!composer || qs('#agent-toolbar')) return;
    const bar = document.createElement('div'); bar.id='agent-toolbar';
    bar.innerHTML =
      '<button type="button" id="atb-stop" title="Stop the agent">■ Stop</button>'+
      '<button type="button" id="atb-cap" title="What SHIMS can do">⚡ Capabilities</button>'+
      '<button type="button" id="atb-palette" title="Command palette (Ctrl/Cmd-K)">⌘K</button>'+
      '<label id="atb-agent" title="Let SHIMS use tools (run commands, edit files, build, self-patch)"><input type="checkbox" id="atb-agent-cb"> Agent</label>';
    composer.parentElement.insertBefore(bar, composer);
    qs('#atb-stop', bar).onclick = () => { window.shimsAbort && window.shimsAbort(); toastMsg('Stopped','warn'); };
    qs('#atb-cap', bar).onclick = showCapabilities;
    qs('#atb-palette', bar).onclick = showPalette;
    const cb = qs('#atb-agent-cb', bar); cb.checked = window.shimsAgentMode;
    cb.onchange = () => { window.shimsAgentMode = cb.checked; localStorage.setItem('shims_agent_mode', cb.checked?'1':'0'); };
  }

  // ---------------- plan graph renderer (v2) ----------------
  window.renderPlanGraph = function(steps, bubble){
    if(!steps || !steps.length) return;
    const target = bubble ? bubble.querySelector('.content') : qs('#transcript');
    if(!target) return;
    const card = document.createElement('div');
    card.className = 'plan-graph-card';
    let html = '<div class="plan-graph-title">📋 Plan</div><div class="plan-graph-steps">';
    steps.forEach((step, i) => {
      const tool = step.tool || 'respond';
      const icon = TOOL_ICON[tool] || '⚙';
      const purpose = esc(step.purpose || tool);
      const args = argPreview(tool, step.args);
      html += '<div class="plan-step" data-step="'+i+'">'+
        '<span class="plan-step-num">'+(i+1)+'</span>'+
        '<span class="plan-step-icon">'+icon+'</span>'+
        '<span class="plan-step-name">'+esc(tool)+'</span>'+
        '<span class="plan-step-purpose">'+purpose+'</span>'+
        (args ? '<code class="plan-step-args">'+args+'</code>' : '')+
        '</div>';
      if(i < steps.length - 1) html += '<div class="plan-step-arrow">↓</div>';
    });
    html += '</div>';
    card.innerHTML = html;
    target.appendChild(card);
    const scroller = bubble ? qs('#transcript') : target;
    if(scroller) scroller.scrollTop = scroller.scrollHeight;
  };

  // ---------------- init ----------------
  function init(){
    injectToolbar();
    tray().classList.add('collapsed');
    const input = qs('#input'); if(input && !input.dataset.mentionBound){ input.addEventListener('input', onInputMention); input.dataset.mentionBound='1'; }
    document.addEventListener('keydown', (e) => {
      if((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')){ e.preventDefault(); const p = qs('#cmd-palette'); (p && !p.classList.contains('hidden')) ? hidePalette() : showPalette(); }
    });
    // warm the tool list for the palette
    fetch('/agent/capabilities').then(r=>r.json()).then(d=>{ window.AGENT_TOOLS = (d.tools||[]).map(t=>t.name); }).catch(()=>{});
    refreshJobsTray();
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else setTimeout(init, 0);
})();
