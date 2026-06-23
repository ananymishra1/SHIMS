#!/data/data/com.termux/files/usr/bin/bash
set -e
cd "$(dirname "$0")"
uvicorn mobile_server:app --host 127.0.0.1 --port 8010
