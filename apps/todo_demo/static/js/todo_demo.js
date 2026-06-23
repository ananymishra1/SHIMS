const API_BASE = '/todo/api/tasks';

async function fetchTasks() {
  const res = await fetch(API_BASE);
  if (!res.ok) {
    console.error('Failed to fetch tasks', res.status);
    return [];
  }
  return await res.json();
}

async function createTask(title) {
  const res = await fetch(API_BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, done: false })
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to create task');
  }
  return await res.json();
}

async function updateTask(id, data) {
  const res = await fetch(`${API_BASE}/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to update task');
  }
  return await res.json();
}

async function deleteTask(id) {
  const res = await fetch(`${API_BASE}/${id}`, { method: 'DELETE' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to delete task');
  }
}

function renderTasks(tasks) {
  const list = document.getElementById('task-list');
  list.innerHTML = '';

  if (tasks.length === 0) {
    const empty = document.createElement('li');
    empty.className = 'task-empty';
    empty.textContent = 'No tasks yet. Add one above!';
    list.appendChild(empty);
    return;
  }

  tasks.forEach(task => {
    const li = document.createElement('li');
    li.className = 'task-item' + (task.done ? ' task-done' : '');
    li.dataset.id = task.id;

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = !!task.done;
    checkbox.className = 'task-checkbox';
    checkbox.addEventListener('change', async () => {
      try {
        await updateTask(task.id, { title: task.title, done: checkbox.checked });
        await loadAndRender();
      } catch (e) {
        showError(e.message);
        checkbox.checked = !checkbox.checked;
      }
    });

    const titleSpan = document.createElement('span');
    titleSpan.className = 'task-title';
    titleSpan.textContent = task.title;

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'task-delete-btn';
    deleteBtn.textContent = '✕';
    deleteBtn.title = 'Delete task';
    deleteBtn.addEventListener('click', async () => {
      if (!confirm(`Delete task "${task.title}"?`)) return;
      try {
        await deleteTask(task.id);
        await loadAndRender();
      } catch (e) {
        showError(e.message);
      }
    });

    li.appendChild(checkbox);
    li.appendChild(titleSpan);
    li.appendChild(deleteBtn);
    list.appendChild(li);
  });
}

function showError(msg) {
  const errDiv = document.getElementById('error-message');
  if (!errDiv) return;
  errDiv.textContent = msg;
  errDiv.style.display = 'block';
  setTimeout(() => { errDiv.style.display = 'none'; }, 4000);
}

function showSuccess(msg) {
  const okDiv = document.getElementById('success-message');
  if (!okDiv) return;
  okDiv.textContent = msg;
  okDiv.style.display = 'block';
  setTimeout(() => { okDiv.style.display = 'none'; }, 3000);
}

async function loadAndRender() {
  try {
    const tasks = await fetchTasks();
    renderTasks(tasks);
    updateStats(tasks);
  } catch (e) {
    showError('Could not load tasks: ' + e.message);
  }
}

function updateStats(tasks) {
  const statsEl = document.getElementById('task-stats');
  if (!statsEl) return;
  const total = tasks.length;
  const done = tasks.filter(t => t.done).length;
  statsEl.textContent = `${done} / ${total} completed`;
}

function initAddForm() {
  const form = document.getElementById('add-task-form');
  const input = document.getElementById('task-title-input');
  if (!form || !input) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const title = input.value.trim();
    if (!title) {
      showError('Task title cannot be empty.');
      return;
    }
    try {
      await createTask(title);
      input.value = '';
      showSuccess('Task added!');
      await loadAndRender();
    } catch (err) {
      showError(err.message);
    }
  });
}

function initTabs() {
  const tabs = document.querySelectorAll('.tab-btn');
  const panels = document.querySelectorAll('.tab-panel');

  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      tabs.forEach(t => t.classList.remove('active'));
      panels.forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      const target = tab.dataset.tab;
      const panel = document.getElementById('tab-' + target);
      if (panel) panel.classList.add('active');
    });
  });

  if (tabs.length > 0) {
    tabs[0].classList.add('active');
    const firstTarget = tabs[0].dataset.tab;
    const firstPanel = document.getElementById('tab-' + firstTarget);
    if (firstPanel) firstPanel.classList.add('active');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initAddForm();
  loadAndRender();
});