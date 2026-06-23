# SHIMS Desktop Bridge

The Desktop Bridge gives SHIMS real control over your local Windows/Mac/Linux machine — not just screenshots, but shell commands, file access, and system info.

## What it can do

- **Shell commands** — run CMD/PowerShell/Bash commands on your machine and stream output back
- **Screenshots** — capture your desktop via the bridge (works headlessly on Windows/Mac/Linux)
- **File system** — read files, write files, list directories
- **Find files** — search drives for executables like `webui.bat`
- **System info** — platform, hostname, current directory

## Quick start on Windows

1. Open CMD as Administrator in `desktop_bridge\`
2. Run the installer:
   ```bat
   install_windows.bat
   ```
3. Start the bridge:
   ```bat
   start_bridge.bat
   ```
4. The bridge now listens on `ws://localhost:9876/bridge` with a secure token.

## Configure SHIMS to use it

Set these environment variables before starting SHIMS:

```bat
set SHIMS_DESKTOP_BRIDGE_URI=ws://localhost:9876/bridge
set SHIMS_DESKTOP_BRIDGE_TOKEN=YOUR_TOKEN_FROM_start_bridge.bat
```

Or in `.env`:

```
SHIMS_DESKTOP_BRIDGE_URI=ws://localhost:9876/bridge
SHIMS_DESKTOP_BRIDGE_TOKEN=YOUR_TOKEN_HERE
```

## API endpoints exposed by SHIMS

- `GET /api/desktop/bridge/status` — check if bridge is online
- `POST /api/desktop/bridge/command` — send a command

Example payload:

```json
{
  "type": "shell",
  "command": "dir C:\\stable-diffusion-webui",
  "timeout": 60
}
```

Other types: `screenshot`, `system_info`, `find_file`, `read_file`, `write_file`.

## Security

- Token authentication is required for every command.
- The bridge binds to `0.0.0.0` by default; use a firewall to restrict access.
- Only run the bridge on trusted networks.

## Troubleshooting

| Problem | Fix |
|---|---|
| `websockets not found` | Run `.venv\Scripts\python -m pip install websockets Pillow` |
| `Bridge token not configured` | Set `SHIMS_DESKTOP_BRIDGE_TOKEN` |
| `Bridge connection failed` | Make sure `start_bridge.bat` is running |
| Screenshot fails on Linux | Install `gnome-screenshot` or ImageMagick `import` |
