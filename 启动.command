#!/bin/bash
# 双击此文件即可启动本地面板（macOS 会在终端里运行它）。
# 前提：你已按 docs/01-获取密钥-指引.md 拿到密钥、并把聊天库解密到本地。

cd "$(dirname "$0")"

PORT="${VLC_PORT:-5678}"

# 依赖自检（首次会自动装）
python3 - <<'PY' 2>/dev/null || pip3 install flask pycryptodome zstandard
import flask, Crypto, zstandard  # noqa
PY

# 先杀掉占用端口的旧实例，否则新进程会因端口被占静默退出、你看到的还是旧页面
OLD=$(lsof -ti:"$PORT" 2>/dev/null)
if [ -n "$OLD" ]; then
    echo "发现旧实例(PID $OLD)，先停掉…"
    kill $OLD 2>/dev/null
    sleep 1
fi

echo "面板地址: http://127.0.0.1:$PORT"
open "http://127.0.0.1:$PORT" 2>/dev/null &
python3 panel/server.py
