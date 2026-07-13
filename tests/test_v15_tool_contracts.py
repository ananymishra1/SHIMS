import time
from shared.tool_contracts import verify_artifact


def test_verify_artifact_success(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("hello", encoding="utf-8")
    r = verify_artifact("test.tool", time.time(), p, "ok")
    assert r.verified is True
    assert r.artifact_sha256


def test_verify_artifact_missing(tmp_path):
    p = tmp_path / "missing.txt"
    r = verify_artifact("test.tool", time.time(), p, "ok")
    assert r.verified is False
    assert r.status == "error"
