"""Unit tests for Phase 1.2 coder_v2.py fixes.

Covers:
- JSON-literal sanitizer regex word-boundary fix
- list_files() recursive mode
- upload_folder() accepting list[int] from JS
"""
from __future__ import annotations

from shared import coder_v2 as coder


def test_sanitize_python_replaces_json_literals():
    src = "x = null\ny = true\nz = false\n"
    expected = "x = None\ny = True\nz = False\n"
    assert coder._sanitize_python(src) == expected


def test_sanitize_python_skips_inside_strings():
    src = 'a = "true null false"\nb = \"also false\"\nc = true\n'
    out = coder._sanitize_python(src)
    assert 'a = "true null false"' in out
    assert 'b = "also false"' in out
    assert 'c = True' in out


def test_sanitize_python_word_boundary():
    """JSON literals embedded in identifiers must not be replaced."""
    src = "nullify = true_value or false_flag\nflag = true\n"
    out = coder._sanitize_python(src)
    assert "nullify" in out
    assert "true_value" in out
    assert "false_flag" in out
    assert "flag = True" in out


def test_list_files_recursive(tmp_path) -> None:
    """Recursive mode returns nested children arrays for directories."""
    project_id = coder.create_project("recursive-test")["project_id"]
    try:
        base = coder._project_path(project_id)
        (base / "src").mkdir()
        (base / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
        (base / "src" / "utils").mkdir()
        (base / "src" / "utils" / "helper.py").write_text("x = 1", encoding="utf-8")
        (base / "README.md").write_text("# test", encoding="utf-8")

        flat = coder.list_files(project_id)
        assert len(flat) == 3  # default main.py + README.md + src/
        assert all("children" not in entry for entry in flat)

        recursive = coder.list_files(project_id, recursive=True)
        by_path = {entry["path"]: entry for entry in recursive}
        assert "README.md" in by_path
        assert "main.py" in by_path
        assert "src" in by_path
        src_entry = by_path["src"]
        assert src_entry["is_dir"] is True
        assert "children" in src_entry
        children_by_path = {child["path"]: child for child in src_entry["children"]}
        assert "src/main.py" in children_by_path
        assert "src/utils" in children_by_path
        utils = children_by_path["src/utils"]
        assert utils["is_dir"] is True
        assert len(utils["children"]) == 1
        assert utils["children"][0]["path"] == "src/utils/helper.py"
    finally:
        coder.delete_file(project_id, "")


def test_upload_folder_accepts_bytes_and_int_lists(tmp_path) -> None:
    """upload_folder must accept both raw bytes and JS-serialized list[int]."""
    project_id = coder.create_project("upload-test")["project_id"]
    try:
        files = {
            "bytes_file.txt": b"hello bytes",
            "nested/list_file.txt": [104, 105, 32, 108, 105, 115, 116],  # "hi list"
        }
        result = coder.upload_folder(project_id, files)
        assert result["ok"] is True
        assert result["written"] == 2
        assert result["errors"] == []

        base = coder._project_path(project_id)
        assert (base / "bytes_file.txt").read_bytes() == b"hello bytes"
        assert (base / "nested" / "list_file.txt").read_bytes() == b"hi list"
    finally:
        coder.delete_file(project_id, "")
