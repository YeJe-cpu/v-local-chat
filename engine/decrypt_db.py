#!/usr/bin/env python3
"""
第二步：用密钥把 SQLCipher 加密库解密成明文 SQLite（clean-room 实现）。

前提：你已按 docs/01-获取密钥-指引.md 拿到密钥（每个库一把 32 字节密钥，
存成 keys.json，形如 {"contact": "<64位hex>", "message_0": "...", ...}）。

用法：
  python3 engine/decrypt_db.py \
      --db-dir  "<微信容器>/db_storage" \
      --keys    ~/.config/wechat-keys.json \
      --out     "~/Library/Application Support/wechat-local-vault/decrypted/current"

算法（SQLCipher 4，公开规范）：每页 4096 字节，尾部 80 字节为 reserve(IV16+HMAC64)；
用该库的 32 字节密钥做 AES-256-CBC、IV 取自 reserve 前 16 字节逐页解密；
第 1 页前 16 字节是 salt，输出时替换成明文 SQLite 头。本脚本不含也不做密钥获取。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from Crypto.Cipher import AES
except Exception:
    raise SystemExit("需要 pycryptodome：pip3 install pycryptodome")

PAGE = 4096
RESERVE = 80  # IV(16) + HMAC-SHA512(64)
SQLITE_HEADER = b"SQLite format 3\x00"


def decrypt_bytes(data: bytes, key: bytes) -> bytes:
    """把一个加密库的字节解成明文 SQLite 字节。"""
    if data[:16] == SQLITE_HEADER:
        return data  # 已是明文
    out = bytearray()
    npages = len(data) // PAGE
    for i in range(npages):
        page = data[i * PAGE:(i + 1) * PAGE]
        offset = 16 if i == 0 else 0  # 第 1 页前 16 字节是 salt
        ciphertext = page[offset:PAGE - RESERVE]
        iv = page[PAGE - RESERVE:PAGE - RESERVE + 16]
        dec = AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext)
        if i == 0:
            out += SQLITE_HEADER
        out += dec
        out += page[PAGE - RESERVE:]  # 保留 reserve，维持 4096 页大小
    return bytes(out)


def key_for(rel_path: str, keys: dict) -> str | None:
    """keys.json 里键名可能是 'contact' 或 'contact/contact.db'，两种都兼容。"""
    name = rel_path
    if name in keys:
        return keys[name]
    stem = Path(rel_path).stem
    return keys.get(stem)


def main() -> None:
    ap = argparse.ArgumentParser(description="用密钥解密微信 SQLCipher 库为明文 SQLite")
    ap.add_argument("--db-dir", required=True, help="微信容器内 db_storage 目录（加密库所在）")
    ap.add_argument("--keys", required=True, help="keys.json：{库名: 32字节hex密钥}")
    ap.add_argument("--out", required=True, help="解密输出目录")
    args = ap.parse_args()

    db_dir = Path(args.db_dir).expanduser()
    out_dir = Path(args.out).expanduser()
    keys = json.loads(Path(args.keys).expanduser().read_text())

    ok = skipped = failed = 0
    for db in sorted(db_dir.rglob("*.db")):
        rel = db.relative_to(db_dir)
        khex = key_for(str(rel), keys) or key_for(rel.name, keys)
        if not khex:
            skipped += 1
            continue
        try:
            data = db.read_bytes()
            dec = decrypt_bytes(data, bytes.fromhex(khex))
            dst = out_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(dec)
            ok += 1
            print(f"  ✅ {rel}")
        except Exception as e:  # noqa
            failed += 1
            print(f"  ❌ {rel}: {e}")
    print(f"\n解密完成：成功 {ok}，无密钥跳过 {skipped}，失败 {failed}。输出：{out_dir}")


if __name__ == "__main__":
    main()
