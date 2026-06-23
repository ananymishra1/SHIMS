#!/usr/bin/env python3
"""
SHIMS Desktop Tray App
======================
Always-on desktop coworker: system tray icon, global hotkey (Ctrl+Space),
floating chat window, clipboard monitor, and OS toast notifications.

Requirements (install once):
    pip install pystray pillow keyboard pyperclip plyer requests

Usage:
    python desktop_bridge/tray_app.py
    python desktop_bridge/tray_app.py --omni-url http://127.0.0.1:8010
    python desktop_bridge/tray_app.py --hotkey ctrl+shift+s
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import scrolledtext
from typing import Any

# ---------------------------------------------------------------------------
# Optional dependency guards — missing libs degrade gracefully
# ---------------------------------------------------------------------------
try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

try:
    import keyboard
    HAS_HOTKEY = True
except ImportError:
    HAS_HOTKEY = False

try:
    import pyperclip
    HAS_CLIPBOARD = True
except ImportError:
    HAS_CLIPBOARD = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from plyer import notification as _plyer_notification
    HAS_NOTIFY = True
except ImportError:
    HAS_NOTIFY = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_OMNI_URL = "http://127.0.0.1:8010"
DEFAULT_HOTKEY   = "ctrl+space"
ICON_SIZE        = 64
WINDOW_W, WINDOW_H = 440, 520
CLIP_MIN_LEN     = 80    # chars before clipboard offer fires
CLIP_DEBOUNCE    = 8.0   # seconds between clipboard nudges
CLIP_POLL        = 2.0   # seconds between clipboard checks

# Dark palette
BG        = "#0d0d1a"
BG2       = "#1a1a2e"
ACCENT    = "#7c5cbf"
FG        = "#e0e0e0"
FG_USER   = "#a0c4ff"
FG_SHIMS  = "#c0ffc0"
FG_META   = "#666"


# ---------------------------------------------------------------------------
# Icon
# ---------------------------------------------------------------------------

def _make_icon(size: int = ICON_SIZE) -> "Image.Image":
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, size - 2, size - 2], fill="#1a1a2e")
    r  = size // 4
    cx = cy = size // 2
    w  = max(2, size // 12)
    draw.arc([cx - r, cy - r, cx + r, cy],      start=200, end=360, fill=ACCENT, width=w)
    draw.arc([cx - r, cy,     cx + r, cy + r],  start=0,   end=160, fill=ACCENT, width=w)
    return img


# ---------------------------------------------------------------------------
# OS notifications
# ---------------------------------------------------------------------------

def _notify_windows(title: str, message: str) -> None:
    """Windows 10+ toast via PowerShell — no extra deps required."""
    safe_title   = title.replace("'", "").replace('"', "")
    safe_message = message.replace("'", "").replace('"', "")
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager,"
        " Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
        "$t = [Windows.UI.Notifications.ToastTemplateType]::ToastText02;"
        "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($t);"
        "$n = $x.GetElementsByTagName('text');"
        f"$n[0].AppendChild($x.CreateTextNode('{safe_title}')) | Out-Null;"
        f"$n[1].AppendChild($x.CreateTextNode('{safe_message}')) | Out-Null;"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($x);"
        "$mgr = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('SHIMS');"
        "$mgr.Show($toast)"
    )
    try:
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-NonInteractive", "-Command", ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def notify(title: str, message: str, timeout: int = 4) -> None:
    if HAS_NOTIFY:
        try:
            _plyer_notification.notify(
                title=title, message=message, app_name="SHIMS", timeout=timeout
            )
            return
        except Exception:
            pass
    if platform.system() == "Windows":
        _notify_windows(title, message)


# ---------------------------------------------------------------------------
# Floating chat window (tkinter — must run on main thread)
# ---------------------------------------------------------------------------

class ChatWindow:
    """Slim floating chat widget.

    Thread-safe: external threads push events to `_q` and the tkinter `after`
    loop drains it, so the GUI never blocks.
    """

    def __init__(self, omni_url: str) -> None:
        self.omni_url = omni_url
        self.root: tk.Tk | None = None
        self._visible = False
        self._q: queue.Queue[tuple[str, str]] = queue.Queue()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self.root = r = tk.Tk()
        r.title("SHIMS")
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        r.geometry(f"{WINDOW_W}x{WINDOW_H}+{sw - WINDOW_W - 20}+{sh - WINDOW_H - 60}")
        r.configure(bg=BG)
        r.attributes("-topmost", True)
        r.resizable(True, True)

        # Title bar
        bar = tk.Frame(r, bg=BG2, height=34)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)
        tk.Label(bar, text="⬡  SHIMS", bg=BG2, fg=ACCENT,
                 font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT, padx=10)
        tk.Button(bar, text="⎯", bg=BG2, fg=FG_META, relief="flat",
                  command=self.hide, font=("Segoe UI", 10),
                  activebackground=BG2, activeforeground=FG).pack(side=tk.RIGHT, padx=2)
        tk.Button(bar, text="⤢", bg=BG2, fg=FG_META, relief="flat",
                  command=self._open_browser, font=("Segoe UI", 10),
                  activebackground=BG2, activeforeground=FG).pack(side=tk.RIGHT)

        # Chat display
        self._chat = scrolledtext.ScrolledText(
            r, wrap=tk.WORD, bg=BG, fg=FG, font=("Segoe UI", 10),
            relief="flat", padx=8, pady=6, state="disabled",
            insertbackground=ACCENT,
        )
        self._chat.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 0))
        self._chat.tag_config("user",  foreground=FG_USER,  font=("Segoe UI", 10, "bold"))
        self._chat.tag_config("shims", foreground=FG_SHIMS)
        self._chat.tag_config("meta",  foreground=FG_META,  font=("Segoe UI", 9))
        self._chat.tag_config("err",   foreground="#ff8888")

        # Input row
        inp_frame = tk.Frame(r, bg=BG2)
        inp_frame.pack(fill=tk.X, padx=4, pady=4)
        self._input_var = tk.StringVar()
        self._entry = tk.Entry(
            inp_frame, textvariable=self._input_var,
            bg=BG2, fg=FG, insertbackground=ACCENT,
            relief="flat", font=("Segoe UI", 10),
        )
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0), ipady=7)
        self._entry.bind("<Return>", self._on_send)
        self._entry.bind("<Escape>", lambda _: self.hide())
        tk.Button(
            inp_frame, text="→", command=self._on_send,
            bg=ACCENT, fg="white", relief="flat", width=3,
            font=("Segoe UI", 11, "bold"),
            activebackground="#9a7fd0",
        ).pack(side=tk.RIGHT, padx=4, ipady=5)

        # Status
        self._status = tk.StringVar(value="Ready · Ctrl+Space to toggle")
        tk.Label(r, textvariable=self._status, bg=BG, fg=FG_META,
                 font=("Segoe UI", 8), anchor="w").pack(fill=tk.X, padx=8, pady=(0, 2))

        r.protocol("WM_DELETE_WINDOW", self.hide)
        r.bind("<Escape>", lambda _: self.hide())
        r.after(80, self._drain_queue)

    def _append(self, text: str, tag: str = "") -> None:
        self._chat.config(state="normal")
        self._chat.insert(tk.END, text, tag)
        self._chat.see(tk.END)
        self._chat.config(state="disabled")

    def _open_browser(self) -> None:
        import webbrowser
        webbrowser.open(self.omni_url)

    # ------------------------------------------------------------------
    # Queue drain (main thread)
    # ------------------------------------------------------------------

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "chunk":
                    self._append(payload, "shims")
                elif kind == "done":
                    self._append("\n")
                    self._status.set("Ready · Ctrl+Space to toggle")
                elif kind == "err":
                    self._append(f"[Error: {payload}]\n", "err")
                    self._status.set("Ready · Ctrl+Space to toggle")
                elif kind == "prefill":
                    self._input_var.set(payload)
                    self._entry.icursor(tk.END)
                elif kind == "show":
                    self._do_show()
                elif kind == "hide":
                    self._do_hide()
        except queue.Empty:
            pass
        if self.root:
            self.root.after(80, self._drain_queue)

    # ------------------------------------------------------------------
    # Send / receive
    # ------------------------------------------------------------------

    def _on_send(self, _evt=None) -> None:
        msg = self._input_var.get().strip()
        if not msg:
            return
        self._input_var.set("")
        self._append(f"\nYou: {msg}\n", "user")
        self._append("SHIMS: ", "meta")
        self._status.set("Thinking …")
        threading.Thread(target=self._stream, args=(msg,), daemon=True).start()

    def _stream(self, message: str) -> None:
        if not HAS_REQUESTS:
            self._q.put(("err", "requests not installed — pip install requests"))
            return
        try:
            resp = requests.post(
                f"{self.omni_url}/chat/stream",
                json={"message": message, "conversation_mode": True},
                stream=True,
                timeout=(10, 120),
            )
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                try:
                    obj = json.loads(raw_line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                t = obj.get("type", "")
                if t == "token":
                    token = obj.get("content") or obj.get("text") or ""
                    if token:
                        self._q.put(("chunk", token))
                elif t == "done":
                    break
            self._q.put(("done", ""))
        except requests.exceptions.ConnectionError:
            self._q.put(("err", f"Cannot reach {self.omni_url} — is SHIMS Omni running?"))
        except Exception as exc:
            self._q.put(("err", str(exc)))

    # ------------------------------------------------------------------
    # Visibility (thread-safe via queue)
    # ------------------------------------------------------------------

    def _do_show(self) -> None:
        if self.root:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self._entry.focus_set()
            self._visible = True

    def _do_hide(self) -> None:
        if self.root:
            self.root.withdraw()
            self._visible = False

    def show(self, prefill: str = "") -> None:
        if prefill:
            self._q.put(("prefill", prefill))
        self._q.put(("show", ""))

    def hide(self) -> None:
        self._q.put(("hide", ""))

    def toggle(self, prefill: str = "") -> None:
        if self._visible:
            self.hide()
        else:
            self.show(prefill=prefill)

    def run(self) -> None:
        """Build and start the tkinter event loop (blocks — call from main thread)."""
        self._build()
        if self.root:
            self.root.withdraw()   # start hidden
            self.root.mainloop()


# ---------------------------------------------------------------------------
# Clipboard monitor
# ---------------------------------------------------------------------------

class ClipboardMonitor:
    def __init__(self, chat: ChatWindow) -> None:
        self._chat = chat
        self._last: str = ""
        self._last_offer: float = 0.0
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="clip-monitor").start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        if not HAS_CLIPBOARD:
            return
        try:
            self._last = pyperclip.paste() or ""
        except Exception:
            return

        while not self._stop.is_set():
            time.sleep(CLIP_POLL)
            try:
                clip = pyperclip.paste() or ""
            except Exception:
                continue

            if clip == self._last:
                continue
            self._last = clip

            if (
                len(clip) >= CLIP_MIN_LEN
                and time.time() - self._last_offer > CLIP_DEBOUNCE
            ):
                self._last_offer = time.time()
                preview = clip[:55].replace("\n", " ")
                notify(
                    "SHIMS",
                    f'"{preview}…"  — press Ctrl+Space to ask SHIMS about it',
                    timeout=4,
                )


# ---------------------------------------------------------------------------
# Approval / task poller (polls /agent/jobs for pending approvals)
# ---------------------------------------------------------------------------

class ApprovalPoller:
    """Polls SHIMS for pending approval gates and fires notifications."""

    POLL_INTERVAL = 10.0

    def __init__(self, omni_url: str, chat: ChatWindow) -> None:
        self._omni_url = omni_url
        self._chat = chat
        self._seen: set[str] = set()
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="approval-poller").start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        if not HAS_REQUESTS:
            return
        while not self._stop.is_set():
            time.sleep(self.POLL_INTERVAL)
            try:
                r = requests.get(
                    f"{self._omni_url}/agent/jobs",
                    params={"status": "pending_approval", "limit": 10},
                    timeout=5,
                )
                if r.ok:
                    for job in r.json().get("jobs", []):
                        jid = job.get("id", "")
                        if jid and jid not in self._seen:
                            self._seen.add(jid)
                            action = job.get("action") or job.get("description") or "action"
                            notify(
                                "SHIMS needs approval",
                                f"{action}  — open SHIMS to approve or deny",
                                timeout=8,
                            )
                            self._chat.show(prefill=f"Review pending action: {action}")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

class SHIMSTrayApp:
    def __init__(self, omni_url: str = DEFAULT_OMNI_URL, hotkey: str = DEFAULT_HOTKEY) -> None:
        self.omni_url    = omni_url
        self.hotkey      = hotkey
        self.chat        = ChatWindow(omni_url)
        self.clip_mon    = ClipboardMonitor(self.chat)
        self.approvals   = ApprovalPoller(omni_url, self.chat)
        self._icon: Any  = None

    # ------------------------------------------------------------------
    # System tray
    # ------------------------------------------------------------------

    def _build_tray(self) -> "pystray.Icon | None":
        if not HAS_TRAY:
            print("[tray] pystray/Pillow not installed — tray icon disabled")
            print("       pip install pystray pillow")
            return None

        img = _make_icon()

        def open_chat(_icon, _item):    self.chat.show()
        def open_browser(_icon, _item):
            import webbrowser; webbrowser.open(self.omni_url)

        def on_restart(_icon, _item):
            """Re-exec from source — picks up any self.patch changes to tray_app.py."""
            import os
            self.clip_mon.stop()
            self.approvals.stop()
            _icon.stop()
            if self.chat.root:
                self.chat.root.after(0, self.chat.root.quit)
            # Replace this process with a fresh one running the same script
            os.execv(sys.executable, [sys.executable] + sys.argv)

        def on_quit(_icon, _item):
            self.clip_mon.stop()
            self.approvals.stop()
            _icon.stop()
            if self.chat.root:
                self.chat.root.after(0, self.chat.root.quit)

        menu = pystray.Menu(
            pystray.MenuItem(f"Open Chat  ({self.hotkey})", open_chat, default=True),
            pystray.MenuItem("Open in Browser", open_browser),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restart (reload source)", on_restart),
            pystray.MenuItem("Quit SHIMS", on_quit),
        )
        return pystray.Icon("SHIMS", img, "SHIMS", menu)

    # ------------------------------------------------------------------
    # Global hotkey
    # ------------------------------------------------------------------

    def _register_hotkey(self) -> None:
        if not HAS_HOTKEY:
            print("[tray] keyboard not installed — hotkey disabled")
            print("       pip install keyboard")
            return
        try:
            keyboard.add_hotkey(self.hotkey, self.chat.toggle, suppress=False)
            print(f"[tray] hotkey registered: {self.hotkey}")
        except Exception as exc:
            print(f"[tray] hotkey registration failed: {exc}")
            print("       On Windows, try running as administrator, or use --hotkey ctrl+shift+s")

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> None:
        print(f"[tray] SHIMS desktop coworker  —  Omni at {self.omni_url}")

        self.clip_mon.start()
        self.approvals.start()
        self._register_hotkey()

        self._icon = self._build_tray()
        if self._icon:
            # Run tray icon in a daemon thread so tkinter can own the main thread
            threading.Thread(target=self._icon.run, daemon=True, name="tray").start()
            print("[tray] system tray icon active  (right-click for menu)")

        notify("SHIMS", f"Desktop coworker active  —  press {self.hotkey} to chat", timeout=4)

        try:
            self.chat.run()   # blocks — must be on main thread (Windows requirement)
        except KeyboardInterrupt:
            pass
        finally:
            self.clip_mon.stop()
            self.approvals.stop()
            if self._icon:
                self._icon.stop()
            print("[tray] bye")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="SHIMS Desktop Tray App")
    parser.add_argument(
        "--omni-url", default=os.environ.get("SHIMS_OMNI_URL", DEFAULT_OMNI_URL),
        help=f"SHIMS Omni base URL (default: {DEFAULT_OMNI_URL})",
    )
    parser.add_argument(
        "--hotkey", default=os.environ.get("SHIMS_HOTKEY", DEFAULT_HOTKEY),
        help=f"Global hotkey to toggle the chat window (default: {DEFAULT_HOTKEY})",
    )
    args = parser.parse_args()
    SHIMSTrayApp(omni_url=args.omni_url, hotkey=args.hotkey).run()


if __name__ == "__main__":
    main()
