# SHIMS Desktop (Electron)

A native, frameless desktop wrapper around the local SHIMS backend. It makes the
in-app custom titlebar work (`window.shimsDesktop`) and can auto-start the
FastAPI backend on `127.0.0.1:8010`.

## Develop
```bash
cd apps/desktop
npm install
npm start            # spawns the Python backend + opens the window
```
Set `SHIMS_NO_SPAWN=1` to attach to an already-running backend, or
`SHIMS_PYTHON=/path/to/python` to choose the interpreter.

## Package installers
```bash
npm run dist:win     # NSIS installer (.exe)
npm run dist:mac     # .dmg
npm run dist:linux   # AppImage
```

## Architecture
- `main.js` — window lifecycle, backend spawn, titlebar IPC, external-link handling.
- `preload.js` — context-isolated bridge exposing `window.shimsDesktop`.
- `splash.html` — shown while the local engine warms up.

The renderer simply loads the existing web UI from the backend, so the desktop
app and browser app stay in lockstep — no duplicated frontend.
