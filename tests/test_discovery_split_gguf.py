"""Multi-part GGUF discovery: only part 1 is a model; size sums all parts."""
from __future__ import annotations

import shared.native_engine.discovery as discovery


def _touch(path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.truncate(size)


def test_split_gguf_parts_filtered_and_size_aggregated(tmp_path, monkeypatch):
    root = tmp_path / "models"
    _touch(root / "BigModel-Q4_K_M-00001-of-00003.gguf", 100)
    _touch(root / "BigModel-Q4_K_M-00002-of-00003.gguf", 90)
    _touch(root / "BigModel-Q4_K_M-00003-of-00003.gguf", 80)
    _touch(root / "SmallModel.gguf", 50)

    models = discovery.discover_models([root])
    ids = {m["id"] for m in models}
    assert "BigModel-Q4_K_M-00001-of-00003" in ids
    assert "SmallModel" in ids
    assert not any("-00002-of-" in i or "-00003-of-" in i for i in ids)

    big = next(m for m in models if m["id"].startswith("BigModel"))
    assert big["size_bytes"] == 270


def test_is_split_part():
    assert discovery.is_split_part("X-00002-of-00004.gguf")
    assert discovery.is_split_part("X-00010-of-00012.gguf")
    assert not discovery.is_split_part("X-00001-of-00004.gguf")
    assert not discovery.is_split_part("plain-model.gguf")
