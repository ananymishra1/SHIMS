// Stanford International School — single-page UI
const API_BASE = '/school';
const LS_TOKEN = 'school_token';
const LS_USER = 'school_user';
const LS_ROLE = 'school_role';

let currentRole = null;
let currentUser = null;

const ROLE_TABS = {
  admin:     ['dashboard','students','staff','classes','attendance','exams','results','fees','reports'],
  principal: ['dashboard','students','staff','classes','attendance','exams','results','fees','reports'],
  teacher:   ['dashboard','students','classes','attendance','exams','results','reports'],
  student:   ['dashboard','attendance','exams','results','fees','reports'],
};

const ROLE_CAN_CREATE = {
  admin:     { students:true, staff:true, classes:true, attendance:true, exams:true, results:true, fees:true },
  principal: { students:true, staff:true, classes:true, attendance:true, exams:true, results:true, fees:true },
  teacher:   { students:false, staff:false, classes:false, attendance:true, exams:true, results:true, fees:false },
  student:   { students:false, staff:false, classes:false, attendance:false, exams:false, results:false, fees:false },
};

function token() {
  return localStorage.getItem(LS_TOKEN) || '';
}

function authHeaders() {
  return {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token()}`
  };
}

async function apiFetch(path, options = {}) {
  const url = API_BASE + path;
  const res = await fetch(url, {
    ...options,
    headers: { ...authHeaders(), ...(options.headers || {}) }
  });
  if (res.status === 401) {
    logout();
    return null;
  }
  return res;
}

function showToast(message, type = 'success') {
  const toast = document.getElementById('toast');
  if (!toast) return;
  toast.textContent = message;
  toast.className = `toast show toast-${type}`;
  toast.style.display = 'block';
  setTimeout(() => {
    toast.classList.remove('show');
    toast.style.display = 'none';
  }, 3000);
}

function setLoginError(msg) {
  const el = document.getElementById('loginError');
  if (!el) return;
  el.textContent = msg;
  el.style.display = msg ? 'block' : 'none';
}

function showLogin() {
  const loginPage = document.getElementById('loginPage');
  const appShell = document.getElementById('appShell');
  if (loginPage) loginPage.classList.remove('hidden');
  if (appShell) appShell.classList.add('hidden');
}

function showApp() {
  const loginPage = document.getElementById('loginPage');
  const appShell = document.getElementById('appShell');
  if (loginPage) loginPage.classList.add('hidden');
  if (appShell) appShell.classList.remove('hidden');

  const userName = document.getElementById('userName');
  const userRole = document.getElementById('userRole');
  const userAvatar = document.getElementById('userAvatar');
  if (userName) userName.textContent = currentUser || 'User';
  if (userRole) userRole.textContent = (currentRole || 'user').replace('_', ' ');
  if (userAvatar) userAvatar.textContent = (currentUser || 'U').charAt(0).toUpperCase();

  const welcomeText = document.getElementById('welcomeText');
  if (welcomeText) welcomeText.textContent = `Welcome, ${currentUser || 'User'}`;

  applyRoleUI();
  switchTab('dashboard');
}

function applyRoleUI() {
  const allowed = ROLE_TABS[currentRole] || ROLE_TABS.student;
  document.querySelectorAll('.nav-item[data-tab]').forEach(btn => {
    const tab = btn.dataset.tab;
    const allowedTab = allowed.includes(tab);
    btn.classList.toggle('nav-hidden', !allowedTab);
    if (!allowedTab && btn.classList.contains('active')) {
      btn.classList.remove('active');
    }
  });

  const canCreate = ROLE_CAN_CREATE[currentRole] || {};
  const buttonMap = {
    addStudentBtn: canCreate.students,
    addStaffBtn: canCreate.staff,
    addClassBtn: canCreate.classes,
    addAttendanceBtn: canCreate.attendance,
    addExamBtn: canCreate.exams,
    addResultBtn: canCreate.results,
    addFeeBtn: canCreate.fees,
  };
  Object.entries(buttonMap).forEach(([id, visible]) => {
    const btn = document.getElementById(id);
    if (btn) btn.style.display = visible ? '' : 'none';
  });
}

async function doLogin() {
  setLoginError('');
  const usernameEl = document.getElementById('loginUsername');
  const passwordEl = document.getElementById('loginPassword');
  if (!usernameEl || !passwordEl) return;
  const username = usernameEl.value.trim();
  const password = passwordEl.value.trim();
  if (!username || !password) {
    setLoginError('Please enter username and password.');
    return;
  }
  try {
    const body = new URLSearchParams();
    body.append('username', username);
    body.append('password', password);
    const res = await fetch(`${API_BASE}/auth/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body
    });
    if (!res || !res.ok) {
      const data = await res.json().catch(() => ({}));
      setLoginError(data.detail || 'Login failed. Check credentials.');
      return;
    }
    const data = await res.json();
    localStorage.setItem(LS_TOKEN, data.access_token);
    localStorage.setItem(LS_USER, username);
    localStorage.setItem(LS_ROLE, data.role || 'student');
    currentUser = username;
    currentRole = data.role || 'student';
    showApp();
    showToast(`Welcome, ${currentUser}!`);
  } catch (err) {
    setLoginError('Network error. Is the SHIMS backend running?');
  }
}

function logout() {
  localStorage.removeItem(LS_TOKEN);
  localStorage.removeItem(LS_USER);
  localStorage.removeItem(LS_ROLE);
  currentUser = null;
  currentRole = null;
  showLogin();
}

function switchTab(tabName) {
  const allowed = ROLE_TABS[currentRole] || ROLE_TABS.student;
  if (!allowed.includes(tabName)) tabName = 'dashboard';

  document.querySelectorAll('.tab-content').forEach(el => {
    el.classList.toggle('active', el.id === `tab-${tabName}`);
  });
  document.querySelectorAll('.nav-item[data-tab]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });

  if (tabName === 'dashboard') loadDashboard();
  if (tabName === 'students') loadStudents();
  if (tabName === 'staff') loadStaff();
  if (tabName === 'classes') loadClasses();
  if (tabName === 'attendance') loadAttendance();
  if (tabName === 'exams') loadExams();
  if (tabName === 'results') loadResults();
  if (tabName === 'fees') loadFees();
  if (tabName === 'reports') loadReportStudents();
}

async function loadDashboard() {
  const ids = ['statStudents','statStaff','statClasses','statExams','statFees','statAttendance'];
  ids.forEach(id => { const el = document.getElementById(id); if (el) el.textContent = '—'; });

  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

  const counts = [
    ['statStudents','/api/students'],
    ['statStaff','/api/staff'],
    ['statClasses','/api/classes'],
    ['statExams','/api/exams'],
    ['statFees','/api/fees'],
    ['statAttendance','/api/attendance'],
  ];
  for (const [id, path] of counts) {
    const res = await apiFetch(path);
    if (!res || !res.ok) continue;
    const data = await res.json();
    const arr = Array.isArray(data) ? data : (data.items || []);
    set(id, arr.length);
  }

  const recent = document.getElementById('recentStudents');
  if (recent) {
    const res = await apiFetch('/api/students');
    if (res && res.ok) {
      const data = await res.json();
      const rows = Array.isArray(data) ? data : (data.items || []);
      recent.innerHTML = makeTableHtml(
        [{label:'ID',key:'id'},{label:'Admission No',key:'admission_number'},{label:'Name',key:'full_name'},{label:'Grade',key:'grade'},{label:'Section',key:'section'}],
        rows.slice(0, 5)
      );
    } else {
      recent.innerHTML = '<p class="table-empty">Could not load recent students.</p>';
    }
  }
}

function formatAIResult(data) {
  if (!data) return '<p>No suggestions available.</p>';
  let html = '';
  if (data.summary) {
    html += `<div class="ai-summary"><strong>Summary:</strong> ${data.summary.total_at_risk || 0} student(s) at risk `;
    html += `(academic: ${data.summary.academic_risk || 0}, attendance: ${data.summary.attendance_risk || 0}, fees: ${data.summary.fee_default_risk || 0})</div>`;
  }
  const interventions = data.interventions || [];
  if (interventions.length === 0) {
    html += '<p>No interventions needed. Great job!</p>';
  } else {
    html += '<div class="ai-interventions">';
    interventions.forEach(item => {
      const badgeClass = item.risk_type === 'academic' ? 'badge-danger' : item.risk_type === 'attendance' ? 'badge-warning' : 'badge-info';
      html += `<div class="ai-intervention-item">
        <div class="ai-intervention-header">
          <strong>${escapeHtml(item.full_name)}</strong> <span class="badge ${badgeClass}">${item.risk_type}</span>
        </div>
        <div class="ai-intervention-detail">${escapeHtml(item.detail)}</div>
        <div class="ai-intervention-suggestion">💡 ${escapeHtml(item.suggestion)}</div>
      </div>`;
    });
    html += '</div>';
  }
  return html;
}

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function getAiSuggestions() {
  const resultEl = document.getElementById('aiResult');
  const loadingEl = document.getElementById('aiLoading');
  const gradeInput = document.getElementById('aiContext');
  const grade = gradeInput ? gradeInput.value.trim() || null : null;
  if (resultEl) {
    resultEl.innerHTML = '';
    resultEl.classList.remove('visible');
  }
  if (loadingEl) loadingEl.classList.add('visible');
  const res = await apiFetch('/api/ai/suggest', {
    method: 'POST',
    body: JSON.stringify({ grade })
  });
  if (loadingEl) loadingEl.classList.remove('visible');
  if (!res || !res.ok) {
    if (resultEl) {
      resultEl.innerHTML = '<p class="ai-error">Failed to fetch AI suggestions.</p>';
      resultEl.classList.add('visible');
    }
    return;
  }
  const data = await res.json();
  if (resultEl) {
    resultEl.innerHTML = formatAIResult(data);
    resultEl.classList.add('visible');
  }
}

function makeTableHtml(columns, rows) {
  if (!rows || rows.length === 0) return '<p class="table-empty">No records found.</p>';
  let html = '<div class="table-wrapper"><table><thead><tr>';
  columns.forEach(c => html += `<th>${c.label}</th>`);
  html += '</tr></thead><tbody>';
  rows.forEach(row => {
    html += '<tr>';
    columns.forEach(c => {
      let val = row[c.key] ?? '';
      if (c.key === 'status') val = `<span class="status-${String(val).toLowerCase()}">${val}</span>`;
      html += `<td>${val}</td>`;
    });
    html += '</tr>';
  });
  html += '</tbody></table></div>';
  return html;
}

function filterTable(tableId, value) {
  const table = document.getElementById(tableId);
  if (!table) return;
  const term = value.toLowerCase();
  table.querySelectorAll('tbody tr').forEach(tr => {
    tr.style.display = tr.textContent.toLowerCase().includes(term) ? '' : 'none';
  });
}

async function admitStudent() {
  const data = {
    admission_number: document.getElementById('s_admission_number').value.trim(),
    full_name: document.getElementById('s_full_name').value.trim(),
    grade: document.getElementById('s_grade').value.trim(),
    section: document.getElementById('s_section').value.trim(),
    parent_phone: document.getElementById('s_parent_phone').value.trim() || null,
  };
  const res = await apiFetch('/api/students', { method: 'POST', body: JSON.stringify(data) });
  if (res && res.ok) { showToast('Student admitted'); closeModal('studentModal'); clearModalInputs('studentModal'); loadStudents(); }
  else { const d = await res?.json().catch(() => ({})); showToast(d.detail || 'Error admitting student', 'error'); }
}

async function loadStudents() {
  const res = await apiFetch('/api/students');
  const el = document.getElementById('studentsBody');
  if (!el) return;
  if (!res || !res.ok) { el.innerHTML = '<tr><td colspan="6" class="table-empty">Failed to load.</td></tr>'; return; }
  const data = await res.json();
  const rows = Array.isArray(data) ? data : (data.items || []);
  el.innerHTML = rows.map(r => `<tr><td>${r.id}</td><td>${escapeHtml(r.admission_number)}</td><td>${escapeHtml(r.full_name)}</td><td>${escapeHtml(r.grade)}</td><td>${escapeHtml(r.section)}</td><td>${escapeHtml(r.parent_phone) || ''}</td></tr>`).join('') || '<tr><td colspan="6" class="table-empty">No students yet.</td></tr>';
}

async function addStaff() {
  const data = {
    employee_id: document.getElementById('st_employee_id').value.trim(),
    full_name: document.getElementById('st_full_name').value.trim(),
    role: document.getElementById('st_role').value.trim(),
    subject: document.getElementById('st_subject').value.trim() || null,
  };
  const res = await apiFetch('/api/staff', { method: 'POST', body: JSON.stringify(data) });
  if (res && res.ok) { showToast('Staff added'); closeModal('staffModal'); clearModalInputs('staffModal'); loadStaff(); }
  else { const d = await res?.json().catch(() => ({})); showToast(d.detail || 'Error adding staff', 'error'); }
}

async function loadStaff() {
  const res = await apiFetch('/api/staff');
  const el = document.getElementById('staffBody');
  if (!el) return;
  if (!res || !res.ok) { el.innerHTML = '<tr><td colspan="5" class="table-empty">Failed to load.</td></tr>'; return; }
  const data = await res.json();
  const rows = Array.isArray(data) ? data : (data.items || []);
  el.innerHTML = rows.map(r => `<tr><td>${r.id}</td><td>${escapeHtml(r.employee_id)}</td><td>${escapeHtml(r.full_name)}</td><td>${escapeHtml(r.role)}</td><td>${escapeHtml(r.subject) || ''}</td></tr>`).join('') || '<tr><td colspan="5" class="table-empty">No staff yet.</td></tr>';
}

async function createClass() {
  const data = {
    grade: document.getElementById('c_grade').value.trim(),
    section: document.getElementById('c_section').value.trim(),
    class_teacher_id: document.getElementById('c_class_teacher_id').value ? Number(document.getElementById('c_class_teacher_id').value) : null,
  };
  const res = await apiFetch('/api/classes', { method: 'POST', body: JSON.stringify(data) });
  if (res && res.ok) { showToast('Class created'); closeModal('classModal'); clearModalInputs('classModal'); loadClasses(); }
  else { const d = await res?.json().catch(() => ({})); showToast(d.detail || 'Error creating class', 'error'); }
}

async function loadClasses() {
  const res = await apiFetch('/api/classes');
  const el = document.getElementById('classesBody');
  if (!el) return;
  if (!res || !res.ok) { el.innerHTML = '<tr><td colspan="4" class="table-empty">Failed to load.</td></tr>'; return; }
  const data = await res.json();
  const rows = Array.isArray(data) ? data : (data.items || []);
  el.innerHTML = rows.map(r => `<tr><td>${r.id}</td><td>${escapeHtml(r.grade)}</td><td>${escapeHtml(r.section)}</td><td>${r.class_teacher_id || ''}</td></tr>`).join('') || '<tr><td colspan="4" class="table-empty">No classes yet.</td></tr>';
}

async function markAttendance() {
  const data = {
    student_id: Number(document.getElementById('a_student_id').value),
    date: document.getElementById('a_date').value,
    status: document.getElementById('a_status').value,
  };
  const res = await apiFetch('/api/attendance', { method: 'POST', body: JSON.stringify(data) });
  if (res && res.ok) { showToast('Attendance marked'); closeModal('attendanceModal'); clearModalInputs('attendanceModal'); loadAttendance(); }
  else { const d = await res?.json().catch(() => ({})); showToast(d.detail || 'Error marking attendance', 'error'); }
}

async function loadAttendance() {
  const res = await apiFetch('/api/attendance');
  const el = document.getElementById('attendanceBody');
  if (!el) return;
  if (!res || !res.ok) { el.innerHTML = '<tr><td colspan="4" class="table-empty">Failed to load.</td></tr>'; return; }
  const data = await res.json();
  const rows = Array.isArray(data) ? data : (data.items || []);
  el.innerHTML = rows.map(r => {
    const statusClass = `status-${String(r.status).toLowerCase()}`;
    return `<tr><td>${r.id}</td><td>${r.student_id}</td><td>${r.date}</td><td><span class="${statusClass}">${escapeHtml(r.status)}</span></td></tr>`;
  }).join('') || '<tr><td colspan="4" class="table-empty">No attendance yet.</td></tr>';
}

async function createExam() {
  const data = {
    name: document.getElementById('e_name').value.trim(),
    subject: document.getElementById('e_subject').value.trim(),
    grade: document.getElementById('e_grade').value.trim(),
    max_marks: Number(document.getElementById('e_max_marks').value),
  };
  const res = await apiFetch('/api/exams', { method: 'POST', body: JSON.stringify(data) });
  if (res && res.ok) { showToast('Exam created'); closeModal('examModal'); clearModalInputs('examModal'); loadExams(); }
  else { const d = await res?.json().catch(() => ({})); showToast(d.detail || 'Error creating exam', 'error'); }
}

async function loadExams() {
  const res = await apiFetch('/api/exams');
  const el = document.getElementById('examsBody');
  if (!el) return;
  if (!res || !res.ok) { el.innerHTML = '<tr><td colspan="5" class="table-empty">Failed to load.</td></tr>'; return; }
  const data = await res.json();
  const rows = Array.isArray(data) ? data : (data.items || []);
  el.innerHTML = rows.map(r => `<tr><td>${r.id}</td><td>${escapeHtml(r.name)}</td><td>${escapeHtml(r.subject)}</td><td>${escapeHtml(r.grade)}</td><td>${r.max_marks}</td></tr>`).join('') || '<tr><td colspan="5" class="table-empty">No exams yet.</td></tr>';
}

async function recordResult() {
  const data = {
    student_id: Number(document.getElementById('r_student_id').value),
    exam_id: Number(document.getElementById('r_exam_id').value),
    marks: Number(document.getElementById('r_marks').value),
  };
  const res = await apiFetch('/api/results', { method: 'POST', body: JSON.stringify(data) });
  if (res && res.ok) { showToast('Result recorded'); closeModal('resultModal'); clearModalInputs('resultModal'); loadResults(); }
  else { const d = await res?.json().catch(() => ({})); showToast(d.detail || 'Error recording result', 'error'); }
}

async function loadResults() {
  const res = await apiFetch('/api/results');
  const el = document.getElementById('resultsBody');
  if (!el) return;
  if (!res || !res.ok) { el.innerHTML = '<tr><td colspan="4" class="table-empty">Failed to load.</td></tr>'; return; }
  const data = await res.json();
  const rows = Array.isArray(data) ? data : (data.items || []);
  el.innerHTML = rows.map(r => `<tr><td>${r.id}</td><td>${r.student_id}</td><td>${r.exam_id}</td><td>${r.marks}</td></tr>`).join('') || '<tr><td colspan="4" class="table-empty">No results yet.</td></tr>';
}

async function addFee() {
  const data = {
    student_id: Number(document.getElementById('f_student_id').value),
    amount: Number(document.getElementById('f_amount').value),
    term: document.getElementById('f_term').value.trim(),
    paid: document.getElementById('f_paid').value ? Number(document.getElementById('f_paid').value) : 0,
  };
  const res = await apiFetch('/api/fees', { method: 'POST', body: JSON.stringify(data) });
  if (res && res.ok) { showToast('Fee record added'); closeModal('feeModal'); clearModalInputs('feeModal'); loadFees(); }
  else { const d = await res?.json().catch(() => ({})); showToast(d.detail || 'Error adding fee record', 'error'); }
}

async function loadFees() {
  const res = await apiFetch('/api/fees');
  const el = document.getElementById('feesBody');
  if (!el) return;
  if (!res || !res.ok) { el.innerHTML = '<tr><td colspan="6" class="table-empty">Failed to load.</td></tr>'; return; }
  const data = await res.json();
  const rows = Array.isArray(data) ? data : (data.items || []);
  el.innerHTML = rows.map(r => {
    const balance = (r.amount || 0) - (r.paid || 0);
    return `<tr><td>${r.id}</td><td>${r.student_id}</td><td>${r.amount}</td><td>${escapeHtml(r.term)}</td><td>${r.paid || 0}</td><td>${balance}</td></tr>`;
  }).join('') || '<tr><td colspan="6" class="table-empty">No fee records yet.</td></tr>';
}

async function loadReportStudents() {
  const select = document.getElementById('reportStudentSelect');
  if (!select) return;
  select.innerHTML = '<option value="">-- choose student --</option>';
  const res = await apiFetch('/api/students');
  if (!res || !res.ok) return;
  const data = await res.json();
  const rows = Array.isArray(data) ? data : (data.items || []);
  rows.forEach(s => {
    const opt = document.createElement('option');
    opt.value = s.id;
    opt.textContent = `${s.admission_number} — ${s.full_name} (Grade ${s.grade}${s.section})`;
    select.appendChild(opt);
  });
}

async function generateReportCard() {
  const select = document.getElementById('reportStudentSelect');
  const container = document.getElementById('reportCardContainer');
  if (!select || !container) return;
  const studentId = Number(select.value);
  if (!studentId) { showToast('Please select a student', 'error'); return; }

  const [studentsRes, resultsRes, attendanceRes, feesRes] = await Promise.all([
    apiFetch('/api/students'),
    apiFetch('/api/results'),
    apiFetch('/api/attendance'),
    apiFetch('/api/fees'),
  ]);

  if (!studentsRes?.ok || !resultsRes?.ok || !attendanceRes?.ok || !feesRes?.ok) {
    showToast('Failed to load report data', 'error'); return;
  }

  const students = await studentsRes.json();
  const student = (Array.isArray(students) ? students : (students.items || [])).find(s => s.id === studentId);
  if (!student) { showToast('Student not found', 'error'); return; }

  const results = await resultsRes.json();
  const resultRows = (Array.isArray(results) ? results : (results.items || [])).filter(r => r.student_id === studentId);

  const attendance = await attendanceRes.json();
  const attRows = (Array.isArray(attendance) ? attendance : (attendance.items || [])).filter(a => a.student_id === studentId);
  const attCounts = { Present:0, Absent:0, Late:0, Excused:0 };
  attRows.forEach(a => { attCounts[a.status] = (attCounts[a.status] || 0) + 1; });

  const fees = await feesRes.json();
  const feeRows = (Array.isArray(fees) ? fees : (fees.items || [])).filter(f => f.student_id === studentId);
  const totalFees = feeRows.reduce((sum, f) => sum + (f.amount || 0), 0);
  const totalPaid = feeRows.reduce((sum, f) => sum + (f.paid || 0), 0);

  document.getElementById('reportStudentInfo').innerHTML = `
    <div class="report-student-info">
      <p><strong>Name:</strong> ${escapeHtml(student.full_name)}</p>
      <p><strong>Admission No:</strong> ${escapeHtml(student.admission_number)}</p>
      <p><strong>Grade / Section:</strong> ${escapeHtml(student.grade)} / ${escapeHtml(student.section)}</p>
      <p><strong>Parent Phone:</strong> ${escapeHtml(student.parent_phone) || '—'}</p>
    </div>`;

  document.getElementById('reportResults').innerHTML = resultRows.length
    ? makeTableHtml([{label:'Exam ID',key:'exam_id'},{label:'Marks',key:'marks'}], resultRows)
    : '<p class="table-empty">No results recorded.</p>';

  document.getElementById('reportAttendance').innerHTML = `
    <div class="report-stats">
      <div class="report-stat"><span>Present</span><strong>${attCounts.Present}</strong></div>
      <div class="report-stat"><span>Absent</span><strong>${attCounts.Absent}</strong></div>
      <div class="report-stat"><span>Late</span><strong>${attCounts.Late}</strong></div>
      <div class="report-stat"><span>Excused</span><strong>${attCounts.Excused}</strong></div>
    </div>
    ${attRows.length ? makeTableHtml([{label:'Date',key:'date'},{label:'Status',key:'status'}], attRows) : '<p class="table-empty">No attendance records.</p>'}`;

  document.getElementById('reportFees').innerHTML = feeRows.length
    ? `<div class="report-stats">
         <div class="report-stat"><span>Total Fees</span><strong>${totalFees}</strong></div>
         <div class="report-stat"><span>Paid</span><strong>${totalPaid}</strong></div>
         <div class="report-stat"><span>Balance</span><strong>${totalFees - totalPaid}</strong></div>
       </div>
       ${makeTableHtml([{label:'Term',key:'term'},{label:'Amount',key:'amount'},{label:'Paid',key:'paid'}], feeRows)}`
    : '<p class="table-empty">No fee records.</p>';

  document.getElementById('reportDate').textContent = new Date().toLocaleDateString();
  container.style.display = 'block';
}

function printReportCard() {
  const reportCard = document.getElementById('reportCard');
  if (!reportCard) return;
  const cssPath = '/stanford_school-static/css/stanford_school.css';
  const printWindow = window.open('', '_blank');
  printWindow.document.write(`
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8">
      <title>Report Card</title>
      <link rel="stylesheet" href="${cssPath}">
      <style>
        body { background: #fff; padding: 2rem; }
        .report-card { box-shadow: none; border: 2px solid var(--primary); }
      </style>
    </head>
    <body>
      ${reportCard.outerHTML}
      <script>window.onload = function(){ setTimeout(() => { window.print(); window.close(); }, 300); };</script>
    </body>
    </html>
  `);
  printWindow.document.close();
}

function clearModalInputs(modalId) {
  const modal = document.getElementById(modalId);
  if (!modal) return;
  modal.querySelectorAll('input, select, textarea').forEach(el => {
    if (el.tagName === 'SELECT') el.selectedIndex = 0;
    else if (el.type === 'number') el.value = '';
    else el.value = '';
  });
}

function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('hidden');
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('hidden');
}

async function restoreSession() {
  const storedToken = token();
  if (!storedToken) { showLogin(); return; }
  currentUser = localStorage.getItem(LS_USER) || 'User';
  currentRole = localStorage.getItem(LS_ROLE) || 'student';
  showApp();
  try {
    const res = await fetch(`${API_BASE}/auth/me`, { headers: { 'Authorization': `Bearer ${storedToken}` } });
    if (res.ok) {
      const data = await res.json();
      currentUser = data.username || currentUser;
      currentRole = data.role || currentRole;
      localStorage.setItem(LS_USER, currentUser);
      localStorage.setItem(LS_ROLE, currentRole);
      applyRoleUI();
      const userName = document.getElementById('userName');
      const userRole = document.getElementById('userRole');
      const userAvatar = document.getElementById('userAvatar');
      const welcomeText = document.getElementById('welcomeText');
      if (userName) userName.textContent = currentUser;
      if (userRole) userRole.textContent = currentRole.replace('_', ' ');
      if (userAvatar) userAvatar.textContent = currentUser.charAt(0).toUpperCase();
      if (welcomeText) welcomeText.textContent = `Welcome, ${currentUser}`;
    }
  } catch (e) { /* offline, local values are enough */ }
}

document.addEventListener('DOMContentLoaded', () => {
  const pwdInput = document.getElementById('loginPassword');
  if (pwdInput) pwdInput.addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });

  if (token()) restoreSession();
  else showLogin();
});
