from shared.memory_store import save_memory, list_memories, forget_memory


def test_memory_save_list_forget(tmp_path, monkeypatch):
    import shared.memory_store as ms
    monkeypatch.setattr(ms, "MEMORY_DB", tmp_path / "memory.sqlite3")
    item = save_memory("preference", "voice", "Use Indian English", ["test"], True)
    assert item["id"]
    items = list_memories(q="Indian")
    assert len(items) == 1
    assert forget_memory(item["id"]) is True
    assert list_memories(q="Indian") == []
