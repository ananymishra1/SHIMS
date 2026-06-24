// SHIMS Desktop — Electron shell.
//
// Wraps the local SHIMS backend (FastAPI on 127.0.0.1:8010) in a native,
// frameless window with a custom titlebar. The backend can either be started
// separately (`python -m backend.app.main`) or auto-spawned here if a Python
// runtime is present. Everything stays local — no external servers.

const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');

const SHIMS_URL = process.env.SHIMS_URL || 'http://127.0.0.1:8010';
const REPO_ROOT = path.resolve(__dirname, '..', '..');
const AUTO_SPAWN = process.env.SHIMS_NO_SPAWN !== '1';

let mainWindow = null;
let backendProc = null;

function waitForBackend(url, timeoutMs = 30000) {
  const started = Date.now();
  return new Promise((resolve) => {
    const ping = () => {
      http.get(url + '/health', (res) => { res.resume(); resolve(true); })
        .on('error', () => {
          if (Date.now() - started > timeoutMs) return resolve(false);
          setTimeout(ping, 500);
        });
    };
    ping();
  });
}

function startBackend() {
  if (!AUTO_SPAWN) return;
  const py = process.env.SHIMS_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  try {
    backendProc = spawn(py, ['-m', 'backend.app.main'], {
      cwd: REPO_ROOT,
      env: { ...process.env, SHIMS_HOST: '127.0.0.1', SHIMS_OMNI_PORT: '8010' },
      stdio: 'ignore',
      detached: false,
    });
    backendProc.on('error', (e) => console.error('SHIMS backend spawn failed:', e.message));
  } catch (e) {
    console.error('Could not start SHIMS backend:', e.message);
  }
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 880,
    minHeight: 560,
    frame: false,                 // custom titlebar (window.shimsDesktop)
    backgroundColor: '#020616',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Splash while the backend warms up.
  mainWindow.loadFile(path.join(__dirname, 'splash.html'));
  mainWindow.once('ready-to-show', () => mainWindow.show());

  startBackend();
  const ok = await waitForBackend(SHIMS_URL);
  if (ok) {
    mainWindow.loadURL(SHIMS_URL);
  } else {
    mainWindow.loadURL(
      'data:text/html,' + encodeURIComponent(
        '<body style="background:#020616;color:#dce6ff;font-family:sans-serif;display:grid;place-items:center;height:100vh;text-align:center">' +
        '<div><h2>SHIMS backend not reachable</h2><p>Start it with <code>python -m backend.app.main</code> and reopen.</p></div></body>'
      )
    );
  }

  // External links open in the system browser, not inside the app.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http') && !url.startsWith(SHIMS_URL)) {
      shell.openExternal(url);
      return { action: 'deny' };
    }
    return { action: 'allow' };
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

// Titlebar IPC (consumed by preload → window.shimsDesktop).
ipcMain.on('win:minimize', () => mainWindow && mainWindow.minimize());
ipcMain.on('win:maximize', () => {
  if (!mainWindow) return;
  mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
});
ipcMain.on('win:close', () => mainWindow && mainWindow.close());

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (backendProc) { try { backendProc.kill(); } catch (e) {} }
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.on('before-quit', () => {
  if (backendProc) { try { backendProc.kill(); } catch (e) {} }
});
