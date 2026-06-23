#!/data/data/com.termux/files/usr/bin/bash
set -e
pkg update -y
pkg upgrade -y
pkg install -y python git cmake clang make ffmpeg curl unzip
python -m pip install --upgrade pip wheel setuptools
python -m pip install -r requirements-mobile.txt
mkdir -p ~/shims_mobile_runtime/models ~/shims_mobile_runtime/media/{images,pdf,ppt,audio,video}
echo "SHIMS Termux runtime installed."
echo "Optional local LLM: build llama.cpp and run llama-server, or connect app to your Predator SHIMS host."
