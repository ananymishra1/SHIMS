"""Coverage for the Phase-1 agentic core: tool registry, risk gating, the
whole-machine fs/shell/code tools, the JSON-action fallback parser, the
/agent/* endpoints, self.patch proposal, and the background coder_job handler.
None of these require Ollama/an LLM to run.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app
from shared import agent_tools as A
from shared import agent_loop as L

client = TestClient(app)

REPO = A.REPO_ROOT
SCRATCH = REPO / "storage" / "_agent_test"


# ── registry + specs ────────────────────────────────────────────────────────
def test_registry_and_specs():
    assert len(A.TOOLS) >= 15
    specs = A.tool_specs()
    assert all(s["type"] == "function" and "name" in s["function"] for s in specs)
    caps = A.capabilities()
    assert caps["ok"] and caps["repo_root"] and len(caps["tools"]) == len(A.TOOLS)


# ── risk classification ─────────────────────────────────────────────────────
def test_shell_risk():
    assert A._shell_risk({"command": "git status"}) == "safe"
    assert A._shell_risk({"command": "dir"}) == "safe"
    assert A._shell_risk({"command": "rm -rf foo"}) == "gated"
    assert A._shell_risk({"command": "echo hi > out.txt"}) == "gated"   # redirection
    assert A._shell_risk({"command": "pip install requests"}) == "gated"
    assert A._shell_risk({"command": "frobnicate --all"}) == "gated"    # unknown → caution


def test_path_classification():
    assert A.path_class(REPO / "backend" / "app" / "main.py") == "repo_source"
    assert A.path_class(REPO / "storage" / "x.txt") == "repo_scratch"
    assert A.path_class("C:/Windows/notepad.exe") in {"outside", "allowed_root"}
    assert A._write_risk(REPO / "shared" / "x.py") == "gated"          # own source → gated
    assert A._write_risk(REPO / "storage" / "x.txt") == "safe"
    assert A._delete_risk(REPO / "storage" / "x.txt") == "safe"
    assert A._delete_risk("C:/Windows/system32/x") == "gated"


# ── safe fs/code/shell tools actually run ───────────────────────────────────
def test_fs_roundtrip_in_scratch():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    f = SCRATCH / "hello.txt"
    w = A.run_tool("fs.write", {"path": str(f), "content": "hello agent"})
    assert w["ok"] and Path(w["path"]).is_file()
    r = A.run_tool("fs.read", {"path": str(f)})
    assert r["ok"] and r["text"] == "hello agent"
    e = A.run_tool("fs.edit", {"path": str(f), "find": "agent", "replace": "world"})
    assert e["ok"] and e["replacements"] == 1
    assert A.run_tool("fs.read", {"path": str(f)})["text"] == "hello world"
    # undo restores the pre-edit content
    A.undo_edit(e["undo_id"])
    assert "agent" in A.run_tool("fs.read", {"path": str(f)})["text"]
    d = A.run_tool("fs.delete", {"path": str(f)})
    assert d["ok"] and not f.is_file()


def test_fs_write_own_source_refused():
    out = A.run_tool("fs.write", {"path": str(REPO / "shared" / "agent_tools.py"), "content": "x"},
                     allow_gated=True)
    assert out["ok"] is False and "self.patch" in out["error"]


def test_code_run_python():
    out = A.run_tool("code.run", {"language": "python", "source": "print(6*7)"})
    assert out["ok"] and out["stdout"].strip() == "42"


def test_shell_run_safe():
    out = A.run_tool("shell.run", {"command": "echo shims-agent-ok"})
    assert out["ok"] and "shims-agent-ok" in out["stdout"]


# ── gate: risky tools need approval, not silent execution ───────────────────
def test_gated_tool_needs_approval():
    out = A.run_tool("fs.delete", {"path": "C:/Windows/system32/drivers/etc/hosts"})
    assert out.get("needs_approval") is True and out["tool"] == "fs.delete"
    sh = A.run_tool("shell.run", {"command": "Remove-Item -Recurse C:/important"})
    assert sh.get("needs_approval") is True


# ── JSON-action fallback parsing (models without native tool-calling) ───────
def test_json_action_fallback():
    msg = {"content": '{"tool": "shell.run", "args": {"command": "ls"}}'}
    calls = L._normalize_tool_calls(msg)
    assert calls and calls[0]["name"] == "shell.run" and calls[0]["args"]["command"] == "ls"
    assert L._final_from_text({"content": '{"final": "all done"}'}) == "all done"
    assert L._final_from_text({"content": "plain answer"}) == "plain answer"
    # native tool_calls path
    native = {"tool_calls": [{"function": {"name": "fs.list", "arguments": {"path": "."}}}]}
    nc = L._normalize_tool_calls(native)
    assert nc[0]["name"] == "fs.list"


# ── /agent/* endpoints ───────────────────────────────────────────────────────
def test_agent_endpoints():
    caps = client.get("/agent/capabilities").json()
    assert caps["ok"] and caps["tools"]
    assert client.get("/agent/tools").json()["specs"]
    roots = client.get("/agent/roots").json()
    assert roots["ok"] and "repo_root" in roots
    # direct tool run: safe executes, gated returns needs_approval
    safe = client.post("/agent/tool", json={"tool": "fs.list", "args": {"path": str(REPO / "shared")}}).json()
    assert safe["ok"] and safe["count"] >= 1
    gated = client.post("/agent/tool", json={"tool": "fs.delete", "args": {"path": "C:/Windows/x"}}).json()
    assert gated.get("needs_approval") is True


# ── self.patch proposes + validates in the sandbox (no live change) ─────────
def test_self_patch_proposes_and_validates():
    out = A.propose_self_patch("docs/_agent_selfpatch_test.md",
                               new_content="# Agent self-patch test\nhello\n",
                               reason="unit test")
    assert out["ok"] and out["needs_approval"] and out["proposal_id"]
    assert isinstance(out["diff"], str)
    assert out["validation"]["status"] in {"validated", "rejected", "failed"}


# ── coder_job background handler runs and writes a live event log ───────────
def test_coder_job_handler_writes_events():
    from shared import omni_brain as ob
    res = ob._task_coder_job({"_task_id": "pytest1", "goal": "print hello", "name": "t"})
    assert "ok" in res
    ev = ob.STORAGE_DIR / "coder_jobs" / "pytest1" / "events.jsonl"
    assert ev.exists()
    stages = [l for l in ev.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any('"stage": "start"' in s or '"stage":"start"' in s for s in stages)
    assert any('"stage": "done"' in s or '"stage":"done"' in s for s in stages)
