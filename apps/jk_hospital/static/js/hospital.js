const API = '/hospital/api';
let currentUser = null;
let currentPatient = null;
let currentVisit = null;
let mediaRecorder = null;
let voiceTarget = null;
let audioChunks = [];

// ---------- Role-based tab visibility ----------
const ROLE_TABS = {
    receptionist: ['dashboard','patients','opd','visit','lab','ipd','ot','ivf','admin','ai'],
    doctor:       ['dashboard','patients','opd','visit','lab','ipd','ot','ivf','ai'],
    nurse:        ['dashboard','patients','opd','visit','lab','ipd','ot','ai'],
    lab_technician: ['dashboard','lab','ai'],
    ot_coordinator: ['dashboard','ot','ipd','ai'],
    pharmacist:   ['dashboard','visit','ai'],
    ivf_specialist: ['dashboard','ivf','patients','ai'],
    admin:        ['dashboard','patients','opd','visit','lab','ipd','ot','ivf','admin','ai'],
};

function setLoginError(msg) {
    const el = document.getElementById('login-error');
    if (el) el.textContent = msg;
}

async function doLogin() {
    setLoginError('');
    const username = document.getElementById('login-username').value.trim();
    const password = document.getElementById('login-password').value;
    try {
        const res = await fetch(API + '/auth/login', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({username, password})
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
            setLoginError(data.detail || 'Invalid credentials'); return;
        }
        currentUser = data.user;
        sessionStorage.setItem('hospital_user', JSON.stringify(currentUser));
        document.getElementById('login-overlay').style.display = 'none';
        applyRoleUI();
        loadDashboard();
    } catch (e) {
        setLoginError('Network error: ' + e.message);
    }
}

function logout() {
    sessionStorage.removeItem('hospital_user');
    currentUser = null; currentPatient = null; currentVisit = null;
    location.reload();
}

function applyRoleUI() {
    if (!currentUser) return;
    document.getElementById('role-badge').textContent = currentUser.role;
    document.getElementById('user-name').textContent = currentUser.full_name || currentUser.username;
    document.getElementById('logout-btn').style.display = 'inline-block';
    const allowed = ROLE_TABS[currentUser.role] || ROLE_TABS.receptionist;
    document.querySelectorAll('.main-nav button').forEach(btn => {
        const tab = btn.dataset.tab;
        btn.classList.toggle('nav-hidden', !allowed.includes(tab));
    });
    // default to first allowed tab
    const first = document.querySelector('.main-nav button:not(.nav-hidden)');
    if (first) first.click();
}

function initApp() {
    const saved = sessionStorage.getItem('hospital_user');
    if (saved) {
        try { currentUser = JSON.parse(saved); } catch(e){}
    }
    if (currentUser) {
        document.getElementById('login-overlay').style.display = 'none';
        applyRoleUI();
        loadDashboard();
    }
}

document.addEventListener('DOMContentLoaded', initApp);

// ---------- Tabs ----------
document.querySelectorAll('.main-nav button').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.main-nav button').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
        if (btn.dataset.tab === 'dashboard') loadDashboard();
        if (btn.dataset.tab === 'patients') searchPatients();
        if (btn.dataset.tab === 'opd') refreshOPD();
        if (btn.dataset.tab === 'lab') refreshLab();
        if (btn.dataset.tab === 'ipd') refreshBeds();
        if (btn.dataset.tab === 'ot') refreshOT();
        if (btn.dataset.tab === 'ivf') refreshIVF();
        if (btn.dataset.tab === 'admin') {};

    });
});

function setClock() {
    const now = new Date();
    document.getElementById('clock').textContent = now.toLocaleTimeString('en-IN', {hour:'2-digit', minute:'2-digit'});
}
setInterval(setClock, 1000); setClock();

// ---------- API helper ----------
async function api(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(API + path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Request failed');
    return data;
}

// ---------- Dashboard ----------
async function loadDashboard() {
    const data = await api('GET', '/status');
    const c = data.counts;
    document.getElementById('stat-patients').textContent = c.patients || 0;
    document.getElementById('stat-active').textContent = c.active_visits || 0;
    document.getElementById('stat-opd').textContent = c.opd_active || 0;
    document.getElementById('stat-ipd').textContent = c.ipd_active || 0;
    document.getElementById('stat-ivf').textContent = c.ivf_active || 0;
    document.getElementById('stat-ot').textContent = c.ot_pending || 0;
}
loadDashboard();

// ---------- Patients ----------
async function searchPatients() {
    const q = document.getElementById('patient-search').value.trim();
    const data = await api('GET', '/patients/search?q=' + encodeURIComponent(q) + '&limit=50');
    const tbody = document.getElementById('patient-list');
    tbody.innerHTML = data.patients.map(p => `
        <tr>
            <td>${p.hospital_id || '-'}</td>
            <td>${p.name}</td>
            <td>${p.phone}</td>
            <td>${p.age || ''} / ${p.gender || ''}</td>
            <td>${p.city || ''}</td>
            <td>
                <button class="btn" onclick="selectPatient(${p.id})">Open</button>
                <button class="btn" onclick="newVisit(${p.id})">OPD</button>
            </td>
        </tr>
    `).join('') || '<tr><td colspan="6" class="empty-state">No patients found</td></tr>';
}

function openRegister() {
    document.getElementById('modal-title').textContent = 'Register New Patient';
    document.getElementById('modal-body').innerHTML = registerForm();
    document.getElementById('modal-overlay').classList.remove('hidden');
    document.getElementById('modal-save').onclick = submitRegister;
}

function registerForm() {
    return `
        <div class="modal-form">
            <div class="full"><label>Name *</label><input id="p-name" required></div>
            <div><label>Phone *</label><input id="p-phone" required></div>
            <div><label>Email</label><input id="p-email"></div>
            <div><label>Gender</label><select id="p-gender"><option value="">-</option><option>Male</option><option>Female</option><option>Other</option></select></div>
            <div><label>DOB</label><input id="p-dob" type="date"></div>
            <div><label>Age</label><input id="p-age" type="number"></div>
            <div><label>Blood Group</label><input id="p-blood_group" placeholder="e.g. B+"></div>
            <div class="full"><label>Address</label><textarea id="p-address"></textarea></div>
            <div><label>City</label><input id="p-city"></div>
            <div><label>State</label><input id="p-state"></div>
            <div><label>Pincode</label><input id="p-pincode"></div>
            <div><label>Emergency Contact Name</label><input id="p-emergency_name"></div>
            <div><label>Emergency Phone</label><input id="p-emergency_phone"></div>
            <div><label>Insurance Provider</label><input id="p-insurance_provider"></div>
            <div><label>Insurance ID</label><input id="p-insurance_id"></div>
            <div class="full"><label>Allergies</label><textarea id="p-allergies"></textarea></div>
            <div class="full"><label>Medical History</label><textarea id="p-medical_history"></textarea></div>
            <div class="full"><label>Current Medications</label><textarea id="p-current_medications"></textarea></div>
        </div>
    `;
}

async function submitRegister() {
    const body = {
        name: document.getElementById('p-name').value,
        phone: document.getElementById('p-phone').value,
        email: document.getElementById('p-email').value || undefined,
        gender: document.getElementById('p-gender').value || undefined,
        dob: document.getElementById('p-dob').value || undefined,
        age: document.getElementById('p-age').value ? parseInt(document.getElementById('p-age').value) : undefined,
        blood_group: document.getElementById('p-blood_group').value || undefined,
        address: document.getElementById('p-address').value || undefined,
        city: document.getElementById('p-city').value || undefined,
        state: document.getElementById('p-state').value || undefined,
        pincode: document.getElementById('p-pincode').value || undefined,
        emergency_name: document.getElementById('p-emergency_name').value || undefined,
        emergency_phone: document.getElementById('p-emergency_phone').value || undefined,
        insurance_provider: document.getElementById('p-insurance_provider').value || undefined,
        insurance_id: document.getElementById('p-insurance_id').value || undefined,
        allergies: document.getElementById('p-allergies').value || undefined,
        medical_history: document.getElementById('p-medical_history').value || undefined,
        current_medications: document.getElementById('p-current_medications').value || undefined,
    };
    const data = await api('POST', '/patients', body);
    closeModal();
    selectPatient(data.patient.id);
    searchPatients();
}

async function selectPatient(id) {
    const data = await api('GET', '/patients/' + id);
    currentPatient = data.patient;
    // show latest visit or create new
    const visits = await api('GET', '/visits?patient_id=' + id + '&status=active&limit=1');
    if (visits.visits.length) {
        await openVisit(visits.visits[0].id);
    } else {
        await newVisit(id);
    }
}

async function newVisit(patientId) {
    const data = await api('POST', '/visits', { patient_id: patientId, visit_type: 'opd', chief_complaint: '' });
    await openVisit(data.visit.id);
}

async function openVisit(visitId) {
    const data = await api('GET', '/visits/' + visitId);
    currentVisit = data.visit;
    currentPatient = data.patient;
    document.getElementById('visit-empty').classList.add('hidden');
    document.getElementById('visit-workspace').classList.remove('hidden');
    document.getElementById('enc-patient-name').textContent = data.patient.name;
    document.getElementById('enc-uhid').textContent = data.patient.hospital_id;
    document.getElementById('enc-visit-type').textContent = data.visit.visit_type.toUpperCase();
    renderHistory('vitals-history', data.vitals, v => `<b>${v.temperature||'-'}°F</b> P:${v.pulse||'-'} BP:${v.bp_systolic||'-'}/${v.bp_diastolic||'-'} SpO2:${v.spo2||'-'}`);
    renderHistory('complaints-history', data.complaints, c => `<b>${c.complaint}</b> ${c.duration||''}`);
    renderHistory('diagnoses-history', data.diagnoses, d => `<b>${d.diagnosis}</b> <span class="badge">${d.type}</span>`);
    renderHistory('prescriptions-history', data.prescriptions, p => `<b>${p.medication}</b> ${p.dosage||''} ${p.frequency||''}`);
    switchTab('visit');
}

function renderHistory(id, items, formatter) {
    document.getElementById(id).innerHTML = items.length ? items.map(i => `
        <div class="item">
            ${formatter(i)}
            <div class="meta">${i.created_at || i.recorded_at || ''}</div>
        </div>
    `).join('') : '<div class="meta">No entries yet</div>';
}

function switchTab(name) {
    document.querySelectorAll('.main-nav button').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.id === 'tab-' + name));
}

async function saveVitals() {
    const form = document.getElementById('vitals-form');
    const body = Object.fromEntries(new FormData(form).entries());
    for (const k of Object.keys(body)) body[k] = body[k] ? parseFloat(body[k]) || body[k] : undefined;
    await api('POST', '/visits/' + currentVisit.id + '/vitals', body);
    openVisit(currentVisit.id);
}

async function saveComplaint() {
    const form = document.getElementById('complaint-form');
    const body = Object.fromEntries(new FormData(form).entries());
    await api('POST', '/visits/' + currentVisit.id + '/complaints', body);
    openVisit(currentVisit.id);
    form.reset();
}

async function saveDiagnosis() {
    const form = document.getElementById('diagnosis-form');
    const body = Object.fromEntries(new FormData(form).entries());
    await api('POST', '/visits/' + currentVisit.id + '/diagnoses', body);
    openVisit(currentVisit.id);
    form.reset();
}

async function savePrescription() {
    const form = document.getElementById('prescription-form');
    const body = Object.fromEntries(new FormData(form).entries());
    await api('POST', '/visits/' + currentVisit.id + '/prescriptions', body);
    openVisit(currentVisit.id);
    form.reset();
}

// ---------- OPD Queue ----------
async function refreshOPD() {
    const data = await api('GET', '/visits?visit_type=opd&status=active&limit=50');
    const el = document.getElementById('opd-queue');
    el.innerHTML = data.visits.length ? data.visits.map(v => `
        <div class="queue-card">
            <div class="info">
                <b>Visit #${v.id}</b> — ${v.chief_complaint || 'No complaint'}
                <div class="meta">Dept: ${v.department || '-'} | ${v.created_at}</div>
            </div>
            <div class="actions">
                <button class="btn primary" onclick="openVisit(${v.id})">Open</button>
            </div>
        </div>
    `).join('') : '<div class="empty-state">No active OPD visits</div>';
}

function startOPD() {
    switchTab('patients');
    searchPatients();
}

// ---------- AI ----------
function formatAIResult(res) {
    let html = '';
    if (res.result) {
        if (Array.isArray(res.result)) {
            html += '<ul>' + res.result.map(i => `<li>${i}</li>`).join('') + '</ul>';
        } else if (typeof res.result === 'object') {
            html += '<dl>' + Object.entries(res.result).map(([k,v]) => `<dt><b>${k}</b></dt><dd>${Array.isArray(v)?v.join(', '):v}</dd>`).join('') + '</dl>';
        } else {
            html += `<p>${res.result}</p>`;
        }
    } else if (res.raw) {
        html += `<p>${res.raw}</p>`;
    } else {
        html += '<p>No output</p>';
    }
    return html;
}

async function aiDifferential() {
    if (!currentVisit) return alert('Open a visit first');
    const summary = await api('GET', '/visits/' + currentVisit.id);
    const complaints = summary.complaints.map(c => c.complaint);
    const vitals = summary.vitals[0] || {};
    const age_gender = `${summary.patient.age || ''} ${summary.patient.gender || ''}`;
    const output = document.getElementById('ai-output');
    output.innerHTML = 'Thinking...';
    const res = await api('POST', '/ai/differential', {
        visit_id: currentVisit.id,
        complaints, vitals,
        history: summary.patient.medical_history,
        age_gender,
    });
    output.innerHTML = formatAIResult(res);
}

async function aiTreatment() {
    if (!currentVisit) return alert('Open a visit first');
    const summary = await api('GET', '/visits/' + currentVisit.id);
    const diagnosis = summary.diagnoses[0]?.diagnosis || 'unknown';
    const complaints = summary.complaints.map(c => c.complaint);
    const vitals = summary.vitals[0] || {};
    const age_gender = `${summary.patient.age || ''} ${summary.patient.gender || ''}`;
    const output = document.getElementById('ai-output');
    output.innerHTML = 'Thinking...';
    const res = await api('POST', '/ai/treatment', {
        visit_id: currentVisit.id,
        diagnosis, complaints, vitals,
        history: summary.patient.medical_history,
        age_gender,
    });
    output.innerHTML = formatAIResult(res);
}

async function askMentor() {
    const q = document.getElementById('mentor-question').value.trim();
    if (!q) return;
    const ctx = currentPatient ? `Patient ${currentPatient.name}, ${currentPatient.medical_history || 'no history'}` : '';
    document.getElementById('mentor-answer').textContent = 'Thinking...';
    const res = await api('POST', '/ai/mentor', { question: q, context: ctx });
    document.getElementById('mentor-answer').textContent = res.answer || res.raw || 'No response';
}
function aiMentor() { switchTab('ai'); document.getElementById('mentor-question').focus(); }

async function printPrescription() {
    if (!currentVisit) return alert('Open a visit first');
    const summary = await api('GET', '/visits/' + currentVisit.id);
    const w = window.open('', '_blank');
    const rx = summary.prescriptions || [];
    const patient = summary.patient || currentPatient;
    const rows = rx.length ? rx.map(p => `<tr><td>${p.medication}</td><td>${p.dosage||'-'}</td><td>${p.frequency||'-'}</td><td>${p.duration||'-'}</td><td>${p.route||'-'}</td><td>${p.instructions||'-'}</td></tr>`).join('') : '<tr><td colspan="6">No prescriptions</td></tr>';
    w.document.write(`
        <html><head><title>Prescription</title><style>body{font-family:Arial;padding:2rem;} table{width:100%;border-collapse:collapse;margin-top:1rem} th,td{border:1px solid #ccc;padding:8px;text-align:left}</style></head>
        <body><h2>J K Hospital — Prescription</h2><p><b>Patient:</b> ${patient.name} | <b>UHID:</b> ${patient.hospital_id}</p>
        <table><thead><tr><th>Medicine</th><th>Dose</th><th>Frequency</th><th>Duration</th><th>Route</th><th>Instructions</th></tr></thead><tbody>${rows}</tbody></table>
        <p style="margin-top:2rem">Doctor Signature: ____________________</p></body></html>`);
    w.document.close(); w.print();
}

async function printVisitSummary() {
    if (!currentVisit) return alert('Open a visit first');
    const summary = await api('GET', '/visits/' + currentVisit.id);
    const w = window.open('', '_blank');
    const vitals = (summary.vitals && summary.vitals[0]) ? summary.vitals[0] : {};
    const patient = summary.patient || currentPatient;
    const complaints = (summary.complaints || []).map(c => `<li>${c.complaint} ${c.duration?'('+c.duration+')':''}</li>`).join('') || '<li>None</li>';
    const diagnoses = (summary.diagnoses || []).map(d => `<li>${d.diagnosis} <i>(${d.type})</i></li>`).join('') || '<li>None</li>';
    w.document.write(`
        <html><head><title>Visit Summary</title><style>body{font-family:Arial;padding:2rem;}</style></head>
        <body><h2>J K Hospital — Visit Summary</h2>
        <p><b>Patient:</b> ${patient.name} | <b>UHID:</b> ${patient.hospital_id} | <b>Visit #</b>${summary.visit.id}</p>
        <h3>Vitals</h3><p>Temp ${vitals.temperature||'-'}°F, Pulse ${vitals.pulse||'-'}, BP ${vitals.bp_systolic||'-'}/${vitals.bp_diastolic||'-'}, SpO2 ${vitals.spo2||'-'}, Wt ${vitals.weight_kg||'-'}kg</p>
        <h3>Complaints</h3><ul>${complaints}</ul>
        <h3>Diagnoses</h3><ul>${diagnoses}</ul>
        <p style="margin-top:2rem">Doctor Signature: ____________________</p></body></html>`);
    w.document.close(); w.print();
}

// ---------- Voice ----------
async function startVoice(target) {
    voiceTarget = target;
    if (!navigator.mediaDevices) return alert('Microphone not available');
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    audioChunks = [];
    mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
    mediaRecorder.onstop = uploadVoice;
    mediaRecorder.start();
    document.getElementById('voice-overlay').classList.remove('hidden');
}

function stopVoice() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
    document.getElementById('voice-overlay').classList.add('hidden');
}

async function uploadVoice() {
    const blob = new Blob(audioChunks, { type: 'audio/webm' });
    const fd = new FormData();
    fd.append('file', blob, 'voice.webm');
    const res = await fetch(API + '/voice/transcribe', { method: 'POST', body: fd });
    const data = await res.json();
    const text = data.ok ? data.corrected || data.raw : '';
    if (voiceTarget === 'complaint') document.getElementById('input-complaint').value = text;
    if (voiceTarget === 'diagnosis') document.getElementById('input-diagnosis').value = text;
    if (voiceTarget === 'notes') {
        const el = document.querySelector('#tab-visit textarea[name="notes"]');
        if (el) el.value = text;
    }
    if (!data.ok) alert('Voice failed: ' + (data.error || 'unknown'));
}

// ---------- Modal ----------
function closeModal() { document.getElementById('modal-overlay').classList.add('hidden'); }

// ---------- Demo ----------
async function loadDemo() {
    alert('Demo data generator will be added in Phase 5.');
}

// ---------- Lab ----------
async function refreshLab() {
    const data = await api('GET', '/lab-orders?status=&limit=100');
    const tbody = document.getElementById('lab-list');
    tbody.innerHTML = data.orders.length ? data.orders.map(o => `
        <tr>
            <td>#${o.visit_id}</td>
            <td>${o.test_name}</td>
            <td>${o.category || '-'}</td>
            <td><span class="badge">${o.status}</span></td>
            <td>${o.ordered_at}</td>
            <td>
                ${o.status !== 'reported' ? `<button class="btn" onclick="reportLab(${o.id})">Report</button>` : ''}
            </td>
        </tr>
    `).join('') : '<tr><td colspan="6" class="empty-state">No lab orders</td></tr>';
}

async function reportLab(orderId) {
    const params = prompt('Enter parameter, value, unit, status (comma separated):', 'Result, normal, , normal');
    if (!params) return;
    const parts = params.split(',').map(s => s.trim());
    await api('POST', '/lab-orders/' + orderId + '/results', {
        parameter: parts[0] || 'Result',
        value: parts[1] || '',
        unit: parts[2] || '',
        status: parts[3] || 'normal',
    });
    await api('PATCH', '/lab-orders/' + orderId, { status: 'reported' });
    refreshLab();
}

// ---------- Beds ----------
async function refreshBeds() {
    const data = await api('GET', '/rooms');
    const board = document.getElementById('bed-board');
    const grouped = {};
    data.rooms.forEach(r => { (grouped[r.ward] = grouped[r.ward] || []).push(r); });
    board.innerHTML = Object.entries(grouped).map(([ward, beds]) => `
        <div class="panel">
            <h3>${ward}</h3>
            ${beds.map(b => `<div class="badge" style="background:${b.status==='available'?'#d1fae5':'#fee2e2'};color:#111">${b.room_number}-${b.bed_number} ${b.bed_type}</div>`).join(' ')}
        </div>
    `).join('');
}

// ---------- OT ----------
async function refreshOT() {
    const data = await api('GET', '/ot-schedules?limit=50');
    const el = document.getElementById('ot-list');
    el.innerHTML = data.schedules.length ? data.schedules.map(s => `
        <div class="queue-card">
            <div class="info">
                <b>${s.procedure}</b> — ${s.ot_room || 'TBD'}
                <div class="meta">${s.scheduled_at} | Status: ${s.status}</div>
            </div>
            <div class="actions">
                <button class="btn" onclick="updateOT(${s.id},'in_progress')">Start</button>
                <button class="btn" onclick="updateOT(${s.id},'completed')">Complete</button>
                <button class="btn" onclick="updateOT(${s.id},'cancelled')">Cancel</button>
            </div>
        </div>
    `).join('') : '<div class="empty-state">No OT schedules</div>';
}

async function updateOT(id, status) {
    await api('PATCH', '/ot-schedules/' + id, { status });
    refreshOT();
}

// ---------- IVF ----------
async function refreshIVF() {
    const data = await api('GET', '/ivf/couples?limit=50');
    const el = document.getElementById('ivf-couples');
    el.innerHTML = data.couples.length ? data.couples.map(c => `
        <div class="queue-card">
            <div class="info">
                <b>Couple #${c.id}</b>
                <div class="meta">TTC ${c.trying_to_conceive_years || '-'} yrs | Prior IVF ${c.prior_ivf_cycles || 0} | ${c.known_causes || 'no known cause'}</div>
            </div>
            <div class="actions">
                <button class="btn" onclick="viewCouple(${c.id})">View</button>
                <button class="btn primary" onclick="newCycle(${c.id})">+ Cycle</button>
            </div>
        </div>
    `).join('') : '<div class="empty-state">No IVF couples yet</div>';
}

function openIVFCouple() {
    document.getElementById('modal-title').textContent = 'Register IVF Couple';
    document.getElementById('modal-body').innerHTML = `
        <div class="modal-form">
            <div><label>Female Patient ID</label><input id="ivf-female" type="number"></div>
            <div><label>Male Patient ID</label><input id="ivf-male" type="number"></div>
            <div><label>TTC (years)</label><input id="ivf-ttc" type="number"></div>
            <div><label>Prior IVF cycles</label><input id="ivf-prior" type="number" value="0"></div>
            <div class="full"><label>Known causes</label><textarea id="ivf-causes"></textarea></div>
        </div>
    `;
    document.getElementById('modal-overlay').classList.remove('hidden');
    document.getElementById('modal-save').onclick = submitIVFCouple;
}

async function submitIVFCouple() {
    const body = {
        female_patient_id: parseInt(document.getElementById('ivf-female').value),
        male_patient_id: parseInt(document.getElementById('ivf-male').value) || null,
        trying_to_conceive_years: parseInt(document.getElementById('ivf-ttc').value) || 0,
        prior_ivf_cycles: parseInt(document.getElementById('ivf-prior').value) || 0,
        known_causes: document.getElementById('ivf-causes').value,
    };
    await api('POST', '/ivf/couples', body);
    closeModal();
    refreshIVF();
}

async function newCycle(coupleId) {
    const protocol = prompt('Protocol (antagonist/agonist/mild/natural):', 'antagonist');
    if (!protocol) return;
    await api('POST', '/ivf/couples/' + coupleId + '/cycles', { protocol, start_date: new Date().toISOString().split('T')[0] });
    refreshIVF();
}

async function viewCouple(coupleId) {
    const data = await api('GET', '/ivf/couples/' + coupleId);
    const cycles = data.cycles || [];
    document.getElementById('modal-title').textContent = 'IVF Couple #' + coupleId;
    document.getElementById('modal-body').innerHTML = `
        <p><b>Known causes:</b> ${data.couple.known_causes || '-'}</p>
        <h4>Cycles</h4>
        ${cycles.map(c => `<div class="queue-card"><div class="info"><b>Cycle #${c.cycle_number}</b> ${c.protocol} <span class="badge">${c.status}</span></div></div>`).join('') || '<p>No cycles</p>'}
    `;
    document.getElementById('modal-overlay').classList.remove('hidden');
    document.getElementById('modal-save').onclick = closeModal;
}

// ---------- Admin ----------
async function generateDemo() {
    const count = parseInt(document.getElementById('demo-count').value) || 30;
    document.getElementById('demo-result').textContent = 'Generating...';
    const res = await api('POST', '/demo/generate', { count });
    document.getElementById('demo-result').textContent = `Generated ${res.generated} demo records.`;
    loadDashboard();
}

async function estimateStaff() {
    const body = {
        patient_load: parseInt(document.getElementById('est-patients').value) || 0,
        opd_per_day: parseInt(document.getElementById('est-opd').value) || 0,
        ipd_beds: parseInt(document.getElementById('est-beds').value) || 0,
        ivf_cycles_per_month: parseInt(document.getElementById('est-ivf').value) || 0,
    };
    const res = await api('POST', '/estimator/staff', body);
    document.getElementById('est-result').textContent = JSON.stringify(res.estimate, null, 2);
}

function loadDemo() { switchTab('admin'); }
