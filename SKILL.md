---
name: v-local-chat
description: |
  本地聊天数据分析：读取已解密到本地的聊天库，做群聊精华、素材提取、对话备份，
  并确定性还原说话人/引用回复/@提及、归一化表情、解密展示图片视频。
  触发词：本地聊天分析、群聊精华、素材提取、对话备份、聊天记录分析。
  前提：已按 docs/01-获取密钥-指引.md 拿到密钥并解密（decrypt_db.py）。
---

# v-local-chat · 本地聊天分析 Skill

面向已解密的本地聊天库。密钥获取见 `docs/01-获取密钥-指引.md`（仅指引，不含抓取脚本）。

## 一次性准备

```bash
# 1) 拿密钥：见 docs/01-获取密钥-指引.md（frida hook 系统密钥派生函数，一次即可长期复用）
# 2) 用密钥解密库为明文 SQLite：
python3 engine/decrypt_db.py --db-dir "<容器>/db_storage" --keys ~/.config/wechat-keys.json \
    --out "~/Library/Application Support/wechat-local-vault/decrypted/current"
```

解密目录可用环境变量 `VLC_DECRYPTED_DIR` 指定；默认即上面这个路径。

## 用法：面板（给人看）

```bash
pip3 install flask pycryptodome zstandard
python3 panel/server.py           # 浏览器打开 http://127.0.0.1:5678
# 或双击 启动.command（自动杀旧实例 + 起面板）
```

三个场景：**群聊精华**（按发言人聚合、按贡献度排序）、**素材提取**（图文/链接/文件/视频按时间分组、可导出）、**信息导出/对话备份**（完整时间线，可连图片视频导出到文件夹）。

## 用法：引擎（给 AI / 脚本调用）

`engine/vault.py` 是纯读取+分析库，直接 import：

```python
import sys; sys.path.insert(0, "engine")
import vault
msgs = vault.get_chat_history("联系人或群名/username", start_ts, end_ts, limit=None)
# 每条消息带：sender、content(表情已归一化)、reply_to{to_name,quoted}、mentions[]、image_path/video_path
```

## 分析时的注意点（防踩坑）

见 `docs/03-分析防踩坑.md`：说话人以标注为准别自由复述、引用/@ 是确定信息别臆断指向、
`群友-xxxx` 当固定匿名者、图片有 wxgf 私有编码本地无解（占比在涨）、朋友圈图片能解密但无法自动归属。
