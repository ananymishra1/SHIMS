"""Run the SHIMS self-indexer with force=true."""
from __future__ import annotations

from shared.self_indexer import index_shims_source

if __name__ == "__main__":
    result = index_shims_source(force=True)
    print(result)
