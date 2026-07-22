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

## AI 想"看到"图片画面，就这两步

图片在本地是**加密缓存**：`get_chat_history` 给的 `image_path` 是加密的 `.dat`，**直接读它只会是乱码，别读**。
真正看图必须先解密导出成 `.jpg`，再用你的读图工具读那个 `.jpg`：

```bash
# 1) 解密导出（图片/视频都会落盘，附 manifest.json）
python3 engine/vault.py export-media "群名或联系人" --out ~/Desktop/媒体 --start 2026-07-01 --end 2026-07-31

# 2) 用读图工具（Claude Code 的 Read、或任意能看图的工具）读导出目录里的 .jpg —— 你就能看见画面
```

也可在代码里调 `vault.export_media(username, out_dir, start_ts, end_ts)`，返回 `saved` 统计。

**别说成"图片一律不可见"，更别编造画面。** 缩略图（约 180px、十几 KB）**是真图、能正常打开**，只是小——看得清就描述，看不清就明说"这个分辨率看不清"，不要猜细节写死。

**只拿到缩略图时，原因看这两个细分计数，不许自己猜：**
- `saved.thumb_hd_is_wxgf` → 高清**已经下载了**，但是 wxgf 腾讯私有编码，本地无法解码（硬限制，**点开也没用，别建议用户去点**）。
- `saved.thumb_hd_missing` → 本地确实没有高清文件（可建议：去软件里点开那张图，稍等片刻后重新导出）。
- `saved.undecodable` → 连缩略图都无法解码。

> 🚫 **严禁由"只有缩略图"反推"用户没点开过"**——实测证伪：用户点开了、软件也确实把高清下下来了，但那份高清是 wxgf 编码解不开，于是仍然只有缩略图可用。照这条错推断，会给出既冤枉人又无效的建议。

## 分析时的注意点（防踩坑）

见 `docs/03-分析防踩坑.md`：说话人以标注为准别自由复述、引用/@ 是确定信息别臆断指向、
`群友-xxxx` 当固定匿名者、图片有 wxgf 私有编码本地无解（占比在涨）、朋友圈图片能解密但无法自动归属。
