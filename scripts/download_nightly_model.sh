#!/usr/bin/env bash
# Parallel, resumable download of Qwen3-235B-A22B Q4_K_M (4 parts, ~142GB).
set -u
set -a; source /c/d/SHIMS/.env; set +a
DEST="$HOME/.lmstudio/models/shims/Qwen3-235B-A22B-Q4_K_M"
BASE="https://huggingface.co/lmstudio-community/Qwen3-235B-A22B-GGUF/resolve/main"
FILES=(
  "Qwen3-235B-A22B-Q4_K_M-00001-of-00004.gguf"
  "Qwen3-235B-A22B-Q4_K_M-00002-of-00004.gguf"
  "Qwen3-235B-A22B-Q4_K_M-00003-of-00004.gguf"
  "Qwen3-235B-A22B-Q4_K_M-00004-of-00004.gguf"
)
mkdir -p "$DEST"

dl() {
  local f="$1"
  local url="$BASE/$f"
  local out="$DEST/$f"
  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    echo "[$(date +%H:%M:%S)] $f attempt $attempt"
    curl -sL -C - --retry 3 --retry-delay 5 --connect-timeout 30 \
         --speed-limit 10240 --speed-time 30 \
         -H "Authorization: Bearer $HF_TOKEN" -o "$out" "$url" && {
      echo "[$(date +%H:%M:%S)] $f COMPLETE"
      return 0
    }
    echo "[$(date +%H:%M:%S)] $f failed, retrying in 20s"
    sleep 20
  done
  echo "[$(date +%H:%M:%S)] $f FAILED after 20 attempts"
  return 1
}

pids=()
for f in "${FILES[@]}"; do
  dl "$f" &
  pids+=($!)
done
rc=0
for p in "${pids[@]}"; do
  wait "$p" || rc=1
done
echo "[$(date +%H:%M:%S)] ALL DONE rc=$rc"
exit $rc
