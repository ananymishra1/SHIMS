#!/data/data/com.termux/files/usr/bin/bash
set -e
pkg install -y git cmake clang make curl
cd ~
if [ ! -d llama.cpp ]; then git clone https://github.com/ggml-org/llama.cpp.git; fi
cd llama.cpp
cmake -B build -DGGML_OPENMP=OFF
cmake --build build --config Release -j$(nproc)
echo "llama.cpp built. Put a GGUF model in ~/shims_mobile_runtime/models and run start_llama_server.sh"
