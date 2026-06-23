#!/data/data/com.termux/files/usr/bin/bash
set -e
MODEL=${1:-$HOME/shims_mobile_runtime/models/model.gguf}
if [ ! -f "$MODEL" ]; then
  echo "Model not found: $MODEL"
  echo "Download a small GGUF model to ~/shims_mobile_runtime/models/model.gguf first."
  exit 1
fi
cd ~/llama.cpp
./build/bin/llama-server -m "$MODEL" -c 4096 --host 127.0.0.1 --port 8080
