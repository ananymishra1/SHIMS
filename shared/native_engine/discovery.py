"""GGUF model discovery and pure-Python GGUF header parsing.

Only the GGUF header (magic + metadata key/value pairs) is read — the tensor
data is never touched, so parsing a multi-GB model costs a few KB of I/O.

Discovery roots (first match wins on duplicate ids):
- ``storage/models/`` (repo)
- ``data/models/`` (repo)
- ``~/.shims/models``
- the LM Studio models dir (read-only walk for ``*.gguf``)
- any dir listed in ``SHIMS_NATIVE_EXTRA_MODEL_DIRS`` (os.pathsep separated)
"""
from __future__ import annotations

import os
import re
import struct
from pathlib import Path
from typing import Any

_GGUF_MAGIC = b"GGUF"

# GGUF metadata value types (spec: ggml/gguf)
_VALUE_SIZES: dict[int, tuple[str, int]] = {
    0: ("<B", 1),   # UINT8
    1: ("<b", 1),   # INT8
    2: ("<H", 2),   # UINT16
    3: ("<h", 2),   # INT16
    4: ("<I", 4),   # UINT32
    5: ("<i", 4),   # INT32
    6: ("<f", 4),   # FLOAT32
    7: ("<?", 1),   # BOOL
    10: ("<Q", 8),  # UINT64
    11: ("<q", 8),  # INT64
    12: ("<d", 8),  # FLOAT64
}
_VALUE_STRING = 8
_VALUE_ARRAY = 9

# Safety caps so a corrupt/adversarial file can never make us read much.
_MAX_KV_PAIRS = 8192
_MAX_STRING_BYTES = 1 << 20       # 1 MiB per string value
_MAX_ARRAY_ELEMENTS = 4_000_000
_MAX_HEADER_BYTES = 256 << 20     # bail out if "metadata" runs past 256 MiB


class GGUFHeaderError(ValueError):
    """Raised when a file is not a parseable GGUF."""


def _read(fh, size: int) -> bytes:
    data = fh.read(size)
    if len(data) != size:
        raise GGUFHeaderError("unexpected end of file")
    return data


def _read_string(fh) -> str:
    (length,) = struct.unpack("<Q", _read(fh, 8))
    if length > _MAX_STRING_BYTES:
        raise GGUFHeaderError(f"string too large: {length}")
    return _read(fh, length).decode("utf-8", errors="replace")


def _skip_string(fh) -> None:
    (length,) = struct.unpack("<Q", _read(fh, 8))
    if length > _MAX_STRING_BYTES:
        raise GGUFHeaderError(f"string too large: {length}")
    fh.seek(length, os.SEEK_CUR)


def _read_value(fh, vtype: int) -> Any:
    if vtype == _VALUE_STRING:
        return _read_string(fh)
    if vtype == _VALUE_ARRAY:
        (elem_type,) = struct.unpack("<I", _read(fh, 4))
        (count,) = struct.unpack("<Q", _read(fh, 8))
        if count > _MAX_ARRAY_ELEMENTS:
            raise GGUFHeaderError(f"array too large: {count}")
        # Skip the payload; arrays (tokenizer tokens etc.) are not needed.
        if elem_type == _VALUE_STRING:
            for _ in range(count):
                _skip_string(fh)
        elif elem_type in _VALUE_SIZES:
            fh.seek(_VALUE_SIZES[elem_type][1] * count, os.SEEK_CUR)
        else:
            raise GGUFHeaderError(f"unknown array element type: {elem_type}")
        return None
    fmt_size = _VALUE_SIZES.get(vtype)
    if fmt_size is None:
        raise GGUFHeaderError(f"unknown value type: {vtype}")
    return struct.unpack(fmt_size[0], _read(fh, fmt_size[1]))[0]


def parse_gguf_header(path: str | Path) -> dict[str, Any]:
    """Parse GGUF metadata from the file header.

    Returns {arch, name, block_count, embedding_length, context_length,
    metadata} — all best-effort; missing keys yield zero/empty values.
    """
    meta: dict[str, Any] = {}
    with open(path, "rb") as fh:
        if _read(fh, 4) != _GGUF_MAGIC:
            raise GGUFHeaderError(f"not a GGUF file: {path}")
        (version,) = struct.unpack("<I", _read(fh, 4))
        if version < 2 or version > 3:
            raise GGUFHeaderError(f"unsupported GGUF version: {version}")
        _tensor_count, kv_count = struct.unpack("<QQ", _read(fh, 16))
        for _ in range(min(kv_count, _MAX_KV_PAIRS)):
            if fh.tell() > _MAX_HEADER_BYTES:
                break
            key = _read_string(fh)
            (vtype,) = struct.unpack("<I", _read(fh, 4))
            value = _read_value(fh, vtype)
            if value is not None:
                meta[key] = value

    arch = str(meta.get("general.architecture") or "")
    block_count = int(meta.get(f"{arch}.block_count") or meta.get("llama.block_count") or 0)
    return {
        "arch": arch,
        "name": str(meta.get("general.name") or ""),
        "block_count": block_count,
        "embedding_length": int(meta.get(f"{arch}.embedding_length") or 0),
        "context_length": int(meta.get(f"{arch}.context_length") or 0),
        "head_count": int(meta.get(f"{arch}.attention.head_count") or 0),
        "head_count_kv": int(meta.get(f"{arch}.attention.head_count_kv") or 0),
        "metadata": meta,
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_model_dirs() -> list[Path]:
    """Ordered discovery roots; earlier entries win on duplicate model ids."""
    dirs = [
        _repo_root() / "storage" / "models",
        _repo_root() / "data" / "models",
        Path.home() / ".shims" / "models",
        Path.home() / ".lmstudio" / "models",
    ]
    extra = (os.getenv("SHIMS_NATIVE_EXTRA_MODEL_DIRS") or "").strip()
    if extra:
        for part in extra.split(os.pathsep):
            part = part.strip()
            if part:
                dirs.append(Path(part))
    return dirs


def _iter_ggufs(root: Path, *, recursive: bool) -> list[Path]:
    if not root.is_dir():
        return []
    pattern = "**/*.gguf" if recursive else "*.gguf"
    try:
        return sorted(root.glob(pattern))
    except Exception:
        return []


def is_projector(path: str | Path) -> bool:
    """True for ``mmproj-*`` multimodal projector sidecars.

    These are GGUFs, but they are the vision tower that accompanies a
    multimodal model — not something the engine can load and chat with. They
    were being listed as ordinary models, so a user could select one (or
    ``pick_default_model`` could land on one) and get an unexplained load
    failure.
    """
    return Path(path).name.lower().startswith("mmproj")


_SPLIT_PART_RE = re.compile(r"-(\d{5})-of-\d+$", re.IGNORECASE)


def is_split_part(path: str | Path) -> bool:
    """True for non-first parts of a multi-part GGUF (``-00002-of-00004``
    etc.). Only part 1 is a loadable model entry — llama.cpp-family servers
    locate the remaining parts alongside it automatically, so listing them as
    models would pollute the picker and risk an unloadable selection."""
    m = _SPLIT_PART_RE.search(Path(path).stem)
    return bool(m) and int(m.group(1)) != 1


def discover_models(dirs: list[Path] | None = None, *,
                    include_projectors: bool = False) -> list[dict[str, Any]]:
    """Find GGUF models under the discovery roots.

    Returns a list of {id, path, size_bytes, arch, name, block_count,
    embedding_length, context_length, source_dir, projector}. ``id`` is the
    filename stem; duplicates keep the first (highest-priority root) hit. Files
    whose headers fail to parse are still listed, with empty metadata.

    Projector sidecars are excluded unless ``include_projectors`` is set — they
    are not loadable models. Pass it when you need the full on-disk inventory
    (e.g. pairing a projector with its multimodal parent).
    """
    roots = dirs if dirs is not None else default_model_dirs()
    seen: set[str] = set()
    models: list[dict[str, Any]] = []
    for root in roots:
        root = Path(root)
        # Always walk recursively: ``**/*.gguf`` also matches top-level files,
        # and LM Studio nests models as publisher/repo/model.gguf.
        candidates = _iter_ggufs(root, recursive=True)
        for path in candidates:
            model_id = path.stem
            if model_id in seen:
                continue
            if is_projector(path) and not include_projectors:
                continue
            if is_split_part(path):
                continue
            seen.add(model_id)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            # Multi-part GGUF: the memory footprint is the SUM of all parts,
            # not just part 1's bytes (launch planning depends on this).
            if re.search(r"-00001-of-\d+$", path.stem, re.IGNORECASE):
                prefix = path.stem.rsplit("-00001-of-", 1)[0]
                try:
                    size = sum(p.stat().st_size for p in path.parent.glob(prefix + "-0*-of-*.gguf"))
                except OSError:
                    pass
            try:
                header = parse_gguf_header(path)
            except (GGUFHeaderError, OSError):
                header = {"arch": "", "name": "", "block_count": 0,
                          "embedding_length": 0, "context_length": 0,
                          "head_count": 0, "head_count_kv": 0}
            models.append({
                "id": model_id,
                # Display label: strip the split-shard suffix so the UI shows
                # "Qwen3-235B-A22B-Q4_K_M" instead of "...-00001-of-00004".
                # The id itself keeps the real part-1 stem — the loader needs it.
                "display_name": re.sub(r"-00001-of-\d+$", "", model_id, flags=re.IGNORECASE),
                "path": str(path),
                "size_bytes": size,
                "arch": header["arch"],
                "name": header["name"],
                "block_count": header["block_count"],
                "embedding_length": header["embedding_length"],
                "context_length": header["context_length"],
                "head_count": header["head_count"],
                "head_count_kv": header["head_count_kv"],
                "source_dir": str(root),
                "projector": is_projector(path),
            })
    return models


def find_model(ref: str, dirs: list[Path] | None = None) -> dict[str, Any] | None:
    """Resolve a model reference (id, general name, or file path) to a discovery entry."""
    ref = (ref or "").strip()
    if not ref:
        return None
    models = discover_models(dirs)
    low = ref.lower()
    for m in models:
        if m["id"].lower() == low or (m.get("name") or "").lower() == low:
            return m
    for m in models:
        if m["path"].lower() == low or m["path"].lower().endswith(os.sep + low):
            return m
    p = Path(ref)
    if p.is_file() and p.suffix.lower() == ".gguf":
        return {"id": p.stem, "path": str(p), "size_bytes": p.stat().st_size,
                "arch": "", "name": "", "block_count": 0, "embedding_length": 0,
                "context_length": 0, "head_count": 0, "head_count_kv": 0,
                "source_dir": str(p.parent)}
    return None


def pick_default_model(dirs: list[Path] | None = None) -> dict[str, Any] | None:
    """Default model pick: largest GGUF in ``storage/models``, else largest found."""
    models = discover_models(dirs)
    if not models:
        return None
    storage = [m for m in models if Path(m["source_dir"]).name == "models"
               and "storage" in Path(m["source_dir"]).parts]
    pool = storage or models
    return max(pool, key=lambda m: m["size_bytes"])
