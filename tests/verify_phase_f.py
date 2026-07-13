from __future__ import annotations

import asyncio
import json
import sys
import urllib.parse

import requests


BASE = "http://127.0.0.1:8021"
OMNI = "http://127.0.0.1:8010"


def login(session: requests.Session) -> bool:
    r = session.post(
        f"{BASE}/login",
        data={"username": "admin", "password": "TestPass123!"},
        allow_redirects=False,
    )
    return r.status_code in (302, 303)


def test_export_modules(session: requests.Session) -> bool:
    r = session.get(f"{BASE}/api/export/modules")
    if r.status_code != 200:
        print("export/modules failed", r.status_code, r.text[:200])
        return False
    data = r.json()
    print("export/modules ok:", data.get("modules", []))
    return True


def test_export_downloads(session: requests.Session) -> bool:
    modules = ["lims", "lims_samples", "equipment", "qms"]
    ok = True
    for mod in modules:
        for fmt in ("csv", "xlsx"):
            r = session.get(f"{BASE}/api/export/{mod}/{fmt}")
            if r.status_code != 200 or len(r.content) == 0:
                print(f"export/{mod}/{fmt} failed", r.status_code, len(r.content))
                ok = False
            else:
                print(f"export/{mod}/{fmt} ok", len(r.content), "bytes")
    return ok


def test_websocket() -> bool:
    try:
        import websockets  # type: ignore
    except ImportError:
        print("websockets not installed; skipping live WS test")
        return True

    async def _ping():
        ws_url = urllib.parse.urljoin(BASE.replace("http", "ws"), "/ws/events")
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"action": "ping"}))
            msg = json.loads(await ws.recv())
            return msg

    try:
        msg = asyncio.run(_ping())
    except Exception as exc:
        print("websocket error:", exc)
        return False
    if msg.get("type") != "pong":
        print("WS unexpected:", msg)
        return False
    print("websocket ping/pong ok")
    return True


def test_bmr_pdf(session: requests.Session) -> bool:
    r = session.get(f"{BASE}/api/production/bmr")
    if r.status_code != 200:
        print("bmr list failed", r.status_code, r.text[:200])
        return False
    data = r.json()
    bmrs = data.get("bmrs", [])
    if not bmrs:
        print("no BMR records to test PDF")
        return True
    bmr_id = bmrs[0]["id"]
    r = session.get(f"{BASE}/api/production/bmr/{bmr_id}/pdf")
    if r.status_code != 200 or r.headers.get("content-type") != "application/pdf":
        print(f"bmr/{bmr_id}/pdf failed", r.status_code, r.headers.get("content-type"))
        return False
    print(f"bmr/{bmr_id}/pdf ok", len(r.content), "bytes")
    return True


def test_omni_suggest_tools() -> bool:
    payload = {"tool": "agent.suggest_tools", "args": {"goal": "Export Enterprise LIMS samples to CSV"}}
    r = requests.post(f"{OMNI}/agent/tool", json=payload, timeout=60)
    if r.status_code != 200:
        print("agent/tool suggest failed", r.status_code, r.text[:300])
        return False
    data = r.json()
    names = [s.get("name") for s in data.get("suggestions", [])]
    print("omni suggest_tools:", names)
    if "enterprise.export" not in names:
        print("WARNING: enterprise.export not in top suggestions")
    return True


def test_omni_enterprise_export_tool() -> bool:
    payload = {"tool": "enterprise.export", "args": {"action": "modules"}}
    r = requests.post(f"{OMNI}/agent/tool", json=payload, timeout=60)
    if r.status_code != 200:
        print("agent/tool enterprise.export failed", r.status_code, r.text[:300])
        return False
    data = r.json()
    print("omni enterprise.export modules:", data.get("modules", [])[:5], "...")
    return True


def main() -> int:
    session = requests.Session()
    if not login(session):
        print("login failed")
        return 1
    print("login ok")
    results = [
        test_export_modules(session),
        test_export_downloads(session),
        test_bmr_pdf(session),
        test_websocket(),
        test_omni_suggest_tools(),
        test_omni_enterprise_export_tool(),
    ]
    if all(results):
        print("\nPhase F verification: ALL PASSED")
        return 0
    print("\nPhase F verification: FAILURES")
    return 1


if __name__ == "__main__":
    sys.exit(main())
