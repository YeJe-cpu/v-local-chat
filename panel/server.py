#!/usr/bin/env python3
"""
Local web panel for WeChat chat analysis.
Lightweight Flask server that reads from the decrypted vault.
"""

import hashlib
import io
import json
import os
import re
import sqlite3
import struct
import subprocess
import sys
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from threading import Timer

try:
    from flask import Flask, jsonify, render_template, request, send_file, Response
except ImportError:
    print("Flask not found. Installing...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "flask"])
    from flask import Flask, jsonify, render_template, request, send_file, Response

try:
    import zstandard as zstd
    ZSTD_DECODER = zstd.ZstdDecompressor()
except ImportError:
    zstd = None
    ZSTD_DECODER = None

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad as aes_unpad
except ImportError:
    AES = None
    aes_unpad = None

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
V2_MAGIC = bytes.fromhex("070856320807")

app = Flask(__name__, template_folder="templates", static_folder="static")

# 读取/分析引擎（本项目原创，clean-room 实现）。面板直接调用它，不再 shell 外部脚本。
sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))
import vault as engine

DEFAULT_DECRYPTED_DIR = engine.DECRYPTED_DIR
OUTPUT_DIR = Path(__file__).parent.parent / "output"
WECHAT_CONTAINER = engine.WECHAT_CONTAINER

WECHAT_DATA_DIR = engine.WECHAT_DATA_DIR
IMAGE_AES_KEY, IMAGE_XOR_KEY = engine.IMAGE_AES_KEY, engine.IMAGE_XOR_KEY
if IMAGE_AES_KEY:
    print(f"[+] Image decryption ready (aes_key={IMAGE_AES_KEY})")
else:
    print("[!] Image decryption keys not found — 请确认已按 docs/ 准备好解密库与附件目录")

decrypt_v2_dat = engine.decrypt_v2_dat
_dat_image_kind = engine._dat_kind
find_video_file = engine.find_video_file


def _build_media_hash_map(chat_username):
    return engine._media_hash_map(chat_username)


def find_image_file(chat_username, create_time, prefer_thumb=False,
                    with_quality=False, file_hash=None):
    path, quality = engine.find_image_file(chat_username, create_time,
                                           file_hash=file_hash, prefer_thumb=prefer_thumb)
    return (path, quality) if with_quality else path


WXGF_CACHE_DIR = OUTPUT_DIR / ".wxgf_cache"


def _convert_wxgf_to_jpeg(data):
    import tempfile
    cache_key = hashlib.md5(data[:256]).hexdigest()
    WXGF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = WXGF_CACHE_DIR / f"{cache_key}.jpg"
    if cached.exists() and cached.stat().st_size > 0:
        return cached.read_bytes()

    tmp_in = tempfile.NamedTemporaryFile(suffix=".hevc", delete=False)
    tmp_in.write(data)
    tmp_in.close()
    tmp_out = tmp_in.name + ".jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", tmp_in.name,
             "-frames:v", "1", "-q:v", "2", tmp_out],
            capture_output=True, timeout=10
        )
        if os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
            with open(tmp_out, "rb") as f:
                result = f.read()
            cached.write_bytes(result)
            return result
    except Exception:
        pass
    finally:
        try:
            os.unlink(tmp_in.name)
        except OSError:
            pass
        try:
            os.unlink(tmp_out)
        except OSError:
            pass
    return data


def get_decrypted_dir():
    return DEFAULT_DECRYPTED_DIR


def decompress_content(content):
    if isinstance(content, bytes) and content[:4] == ZSTD_MAGIC and ZSTD_DECODER:
        return ZSTD_DECODER.decompress(content).decode("utf-8", errors="replace")
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return content or ""


def search_contacts_and_groups(query):
    decrypted = get_decrypted_dir()
    contact_db = decrypted / "contact/contact.db"
    if not contact_db.exists():
        return []

    conn = sqlite3.connect(contact_db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    results = []
    try:
        like = f"%{query}%"
        cur.execute("""
            SELECT username, COALESCE(nick_name, '') as nick_name,
                   COALESCE(remark, '') as remark, COALESCE(alias, '') as alias
            FROM contact
            WHERE (nick_name LIKE ? OR remark LIKE ? OR alias LIKE ? OR username LIKE ?)
              AND username != ''
              AND username NOT LIKE 'gh_%'
            ORDER BY
              CASE WHEN username LIKE '%@chatroom' THEN 0 ELSE 1 END,
              CASE WHEN remark LIKE ? THEN 0 WHEN nick_name LIKE ? THEN 1 ELSE 2 END
            LIMIT 20
        """, (like, like, like, like, like, like))
        for row in cur.fetchall():
            username = row["username"]
            nick = row["nick_name"]
            remark = row["remark"]
            alias = row["alias"]
            display = remark or nick or alias or username
            is_group = username.endswith("@chatroom")
            results.append({
                "username": username,
                "display": display,
                "type": "group" if is_group else "contact",
            })
    except Exception as e:
        print(f"Search error: {e}")
    finally:
        conn.close()
    return results


def _to_ts(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except ValueError:
            continue
    return None


def get_chat_history(username, start_time, end_time, limit=2000):
    try:
        return engine.get_chat_history(username, _to_ts(start_time), _to_ts(end_time), limit=limit)
    except Exception as e:
        print(f"get_chat_history error: {e}")
        return []


def aggregate_by_person(messages):
    if not messages:
        return []
    persons = {}
    for msg in messages:
        sender = msg.get("sender", "unknown")
        content = msg.get("content", "")
        if sender not in persons:
            persons[sender] = {"sender": sender, "messages": [], "char_count": 0}
        persons[sender]["messages"].append(msg)
        persons[sender]["char_count"] += len(content)

    for p in persons.values():
        p["messages"].sort(key=lambda m: m.get("time", ""))
        p["msg_count"] = len(p["messages"])

    result = sorted(persons.values(), key=lambda p: -p["char_count"])
    return result


def group_materials_by_time(messages, gap_minutes=5):
    if not messages:
        return []
    sorted_msgs = sorted(messages, key=lambda m: m.get("time", ""))
    groups = []
    current_group = None

    for msg in sorted_msgs:
        sender = msg.get("sender", "unknown")
        time_str = msg.get("time", "")
        content = msg.get("content", "")

        try:
            msg_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            try:
                msg_time = datetime.strptime(time_str[:16], "%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                msg_time = None

        if (current_group and sender == current_group["sender"]
                and msg_time and current_group["_last_time"]
                and (msg_time - current_group["_last_time"]).total_seconds() <= gap_minutes * 60):
            current_group["messages"].append(msg)
            current_group["_last_time"] = msg_time
            current_group["end_time"] = time_str
        else:
            if current_group:
                del current_group["_last_time"]
                groups.append(current_group)
            current_group = {
                "sender": sender,
                "start_time": time_str,
                "end_time": time_str,
                "messages": [msg],
                "_last_time": msg_time,
            }

    if current_group:
        del current_group["_last_time"]
        groups.append(current_group)

    for g in groups:
        imgs = sum(1 for m in g["messages"] if "[图片]" in m.get("content", ""))
        vids = sum(1 for m in g["messages"] if "[视频]" in m.get("content", ""))
        texts = [m for m in g["messages"] if "[图片]" not in m.get("content", "") and "[视频]" not in m.get("content", "")]
        g["image_count"] = imgs
        g["video_count"] = vids
        g["text_count"] = len(texts)
        g["msg_count"] = len(g["messages"])

    return groups


def segment_chat_rounds(messages, gap_hours=2):
    if not messages:
        return [], []
    sorted_msgs = sorted(messages, key=lambda m: m.get("time", ""))
    rounds = []
    current_round = None
    unreplied = []

    for msg in sorted_msgs:
        time_str = msg.get("time", "")
        try:
            msg_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            try:
                msg_time = datetime.strptime(time_str[:16], "%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                msg_time = None

        if (current_round and msg_time and current_round["_last_time"]
                and (msg_time - current_round["_last_time"]).total_seconds() > gap_hours * 3600):
            del current_round["_last_time"]
            current_round["msg_count"] = len(current_round["messages"])
            rounds.append(current_round)
            current_round = None

        if current_round is None:
            current_round = {
                "start_time": time_str,
                "end_time": time_str,
                "messages": [msg],
                "_last_time": msg_time,
            }
        else:
            current_round["messages"].append(msg)
            current_round["end_time"] = time_str
            current_round["_last_time"] = msg_time

    if current_round:
        del current_round["_last_time"]
        current_round["msg_count"] = len(current_round["messages"])
        rounds.append(current_round)

    senders = set(m.get("sender", "") for m in sorted_msgs)
    if len(senders) == 2:
        sender_list = sorted(senders)
        for rnd in rounds:
            msgs = rnd["messages"]
            if len(msgs) > 0:
                last_msg = msgs[-1]
                last_sender = last_msg.get("sender", "")
                other_sender = [s for s in sender_list if s != last_sender]
                if other_sender:
                    rnd["last_sender"] = last_sender
                    rnd["awaiting_reply_from"] = other_sender[0]

    return rounds, unreplied


def extract_links_from_messages(messages):
    links = []
    seen_urls = set()
    url_pattern = re.compile(r'https?://\S+')

    for msg in messages:
        content = msg.get("content", "")
        urls = url_pattern.findall(content)
        for url in urls:
            clean_url = url.rstrip('.,;!?)）。，；')
            if clean_url not in seen_urls:
                seen_urls.add(clean_url)
                links.append({
                    "url": clean_url,
                    "sender": msg.get("sender", ""),
                    "time": msg.get("time", ""),
                    "context": content[:200],
                    "has_hongbao": "红包" in content or "hongbao" in content.lower(),
                })
    return links


def extract_file_messages(messages):
    files = []
    for msg in messages:
        content = msg.get("content", "")
        msg_type = msg.get("type", "")
        if any(tag in content for tag in ["[文件]", "[图片]", "[视频]", "[语音]", "[小程序]", "[链接/文件]"]):
            files.append({
                "sender": msg.get("sender", ""),
                "time": msg.get("time", ""),
                "content": content,
                "type": msg_type,
            })
    return files


def enrich_image_urls(messages, chat_username):
    hash_map = _build_media_hash_map(chat_username)
    for msg in messages:
        msg["chat_username"] = chat_username
        content = msg.get("content", "")
        ts = msg.get("timestamp")
        if not ts:
            continue
        file_hash = hash_map.get(int(ts))
        hq = f"&h={file_hash}" if file_hash else ""
        if "[图片]" in content:
            _, quality = find_image_file(chat_username, int(ts),
                                         with_quality=True, file_hash=file_hash)
            msg["image_quality"] = quality
            if quality == "hd":
                msg["image_url"] = f"/api/image?chat={chat_username}&ts={ts}&thumb=0{hq}&v=5"
            elif quality == "thumb":
                msg["image_url"] = None
        elif "[视频]" in content:
            mp4, cover = find_video_file(chat_username, int(ts), file_hash=file_hash)
            if mp4:
                msg["video_quality"] = "hd"
                msg["video_url"] = f"/api/video?chat={chat_username}&ts={ts}{hq}"
            elif cover:
                msg["video_quality"] = "cover"
            else:
                msg["video_quality"] = "none"
            if cover:
                msg["video_cover"] = f"/api/video-cover?chat={chat_username}&ts={ts}{hq}"


def fetch_messages_for_targets(usernames, start_date_str, end_date_str):
    start = datetime.strptime(start_date_str, "%Y-%m-%d")
    end = datetime.strptime(end_date_str, "%Y-%m-%d") + timedelta(days=1)

    all_messages = []
    for username in usernames:
        current = start
        while current < end:
            next_day = current + timedelta(days=1)
            day_start = current.strftime("%Y-%m-%d 00:00")
            day_end = next_day.strftime("%Y-%m-%d 00:00")
            msgs = get_chat_history(username, day_start, day_end, limit=1000)
            enrich_image_urls(msgs, username)
            all_messages.extend(msgs)
            current = next_day
    return all_messages


@app.route("/api/image")
def api_image():
    chat = request.args.get("chat", "")
    ts = request.args.get("ts", "")
    thumb = request.args.get("thumb", "1") == "1"
    file_hash = request.args.get("h") or None
    if not chat or not ts:
        return Response("missing params", status=400)
    try:
        create_time = int(ts)
    except ValueError:
        return Response("bad ts", status=400)
    dat_path = find_image_file(chat, create_time, prefer_thumb=thumb, file_hash=file_hash)
    if not dat_path:
        return Response("not found", status=404)
    with open(dat_path, "rb") as f:
        raw = f.read()
    decrypted = decrypt_v2_dat(raw)
    if not decrypted:
        return Response("decrypt failed", status=500)
    if decrypted[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif decrypted[:4] == b"\x89PNG":
        mime = "image/png"
    elif decrypted[:4] == b"RIFF":
        mime = "image/webp"
    else:
        mime = "application/octet-stream"
    return Response(decrypted, mimetype=mime,
                    headers={"Cache-Control": "public, max-age=3600"})


@app.route("/api/video-cover")
def api_video_cover():
    chat = request.args.get("chat", "")
    ts = request.args.get("ts", "")
    file_hash = request.args.get("h") or None
    if not chat or not ts:
        return Response("missing params", status=400)
    try:
        create_time = int(ts)
    except ValueError:
        return Response("bad ts", status=400)
    _, cover = find_video_file(chat, create_time, file_hash=file_hash)
    if not cover:
        return Response("not found", status=404)
    return send_file(str(cover), mimetype="image/jpeg",
                     max_age=3600)


@app.route("/api/video")
def api_video():
    chat = request.args.get("chat", "")
    ts = request.args.get("ts", "")
    file_hash = request.args.get("h") or None
    if not chat or not ts:
        return Response("missing params", status=400)
    try:
        create_time = int(ts)
    except ValueError:
        return Response("bad ts", status=400)
    mp4, _ = find_video_file(chat, create_time, file_hash=file_hash)
    if not mp4:
        return Response("not found", status=404)
    return send_file(str(mp4), mimetype="video/mp4", max_age=3600)


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    type_filter = request.args.get("type", "")
    if len(q) < 1:
        return jsonify([])
    results = search_contacts_and_groups(q)
    if type_filter == "group":
        results = [r for r in results if r["type"] == "group"]
    elif type_filter == "contact":
        results = [r for r in results if r["type"] == "contact"]
    return jsonify(results)


def reply_prefix(msg):
    """Markdown 导出用的紧凑引用标注。面板前端另有低调引用行渲染，二者互不影响。"""
    r = msg.get("reply_to")
    if not r:
        return ""
    quoted = (r.get("quoted") or "")
    if len(quoted) > 34:
        quoted = quoted[:34] + "…"
    return f"↩ 回复 {r.get('to_name') or '某人'}「{quoted}」\n> "


def _filter_display_messages(messages):
    result = []
    for msg in messages:
        content = msg.get("content", "")
        # 系统消息（邀请入群/非朋友提示/群公告等）按类型/标记排除，别靠内容文字匹配——
        # 否则纯文本的"X邀请Y加入了群聊"会溜进聚合、在新群里霸榜发言排行。
        if msg.get("is_system") or msg.get("sender") == "系统" \
                or (int(msg.get("local_type") or 0) & 0xFFFFFFFF) == 10000:
            continue
        if msg.get("image_url"):
            result.append(msg)
            continue
        if any(tag in content for tag in [
            "[文件]", "[视频]", "[表情]", "[系统]",
            "[链接/文件]", "[链接]", "[小程序]", "[语音]",
        ]):
            continue
        if "[图片]" in content:
            continue
        if content.startswith("<") and ("_wc_custom_link_" in content or "<msg>" in content or "<sysmsg" in content):
            continue
        if len(content.strip()) == 0:
            continue
        result.append(msg)
    return result


@app.route("/api/run/group-digest", methods=["POST"])
def api_group_digest():
    data = request.json
    usernames = data.get("usernames", [])
    start_date = data.get("start_date", "")
    end_date = data.get("end_date", "")
    names = data.get("names", {})

    if not usernames or not start_date or not end_date:
        return jsonify({"error": "缺少必填参数"}), 400

    sections = []
    total_all = 0
    total_text = 0

    for username in usernames:
        msgs = fetch_messages_for_targets([username], start_date, end_date)
        display_msgs = _filter_display_messages(msgs)
        links = extract_links_from_messages(msgs)
        person_blocks = aggregate_by_person(display_msgs)

        total_all += len(msgs)
        total_text += len(display_msgs)

        sections.append({
            "username": username,
            "display": names.get(username, username),
            "total_messages": len(msgs),
            "text_messages": len(display_msgs),
            "person_blocks": [{
                "sender": p["sender"],
                "msg_count": p["msg_count"],
                "char_count": p["char_count"],
                "messages": p["messages"],
            } for p in person_blocks],
            "links": links,
        })

    return jsonify({
        "total_messages": total_all,
        "text_messages": total_text,
        "date_range": f"{start_date} ~ {end_date}",
        "sections": sections,
    })


@app.route("/api/run/material-extract", methods=["POST"])
def api_material_extract():
    data = request.json
    usernames = data.get("usernames", [])
    start_date = data.get("start_date", "")
    end_date = data.get("end_date", "")

    if not usernames or not start_date or not end_date:
        return jsonify({"error": "缺少必填参数"}), 400

    names = data.get("names", {})
    all_messages = fetch_messages_for_targets(usernames, start_date, end_date)

    # 按群/联系人分区（多目标不混放），每个区内再按时间分组；导出仍全量合并
    by_chat = {}
    for msg in all_messages:
        by_chat.setdefault(msg.get("chat_username", ""), []).append(msg)
    sections = []
    for username in usernames:
        msgs = by_chat.get(username, [])
        sections.append({
            "chat_username": username,
            "display": (names.get(username, username)
                        if isinstance(names, dict) else username),
            "material_groups": group_materials_by_time(msgs, gap_minutes=2),
            "total_messages": len(msgs),
        })

    all_links = []
    url_pattern = re.compile(r'https?://\S+')
    seen = set()
    for msg in all_messages:
        content = msg.get("content", "")
        for url in url_pattern.findall(content):
            clean = url.rstrip('.,;!?)）。，；')
            if clean not in seen:
                seen.add(clean)
                all_links.append({
                    "url": clean,
                    "sender": msg.get("sender", ""),
                    "time": msg.get("time", ""),
                    "context": content[:200],
                })

    return jsonify({
        "total_messages": len(all_messages),
        "date_range": f"{start_date} ~ {end_date}",
        "sections": sections,
        "material_groups": group_materials_by_time(all_messages, gap_minutes=2),
        "links": all_links,
    })


@app.route("/api/run/info-export", methods=["POST"])
def api_info_export():
    data = request.json
    usernames = data.get("usernames", [])
    start_date = data.get("start_date", "")
    end_date = data.get("end_date", "")

    if not usernames or not start_date or not end_date:
        return jsonify({"error": "缺少必填参数"}), 400

    names = data.get("names", {})
    sections = []
    total = 0
    for username in usernames:
        msgs = fetch_messages_for_targets([username], start_date, end_date)
        total += len(msgs)
        sections.append({
            "username": username,
            "display": names.get(username, username),
            "total": len(msgs),
            "preview": msgs,          # 信息导出=对话完整备份，页面全量展示，不截断
            "has_more": False,
        })

    return jsonify({
        "total_messages": total,
        "date_range": f"{start_date} ~ {end_date}",
        "sections": sections,
    })


@app.route("/api/export/group-digest", methods=["POST"])
def api_export_group_digest():
    data = request.json
    usernames = data.get("usernames", [])
    start_date = data.get("start_date", "")
    end_date = data.get("end_date", "")
    names = data.get("names", {})

    if not usernames or not start_date or not end_date:
        return jsonify({"error": "缺少必填参数"}), 400

    md = f"# 群聊精华\n\n> 时间范围：{start_date} ~ {end_date}\n\n"

    for username in usernames:
        msgs = fetch_messages_for_targets([username], start_date, end_date)
        display_msgs = _filter_display_messages(msgs)
        links = extract_links_from_messages(msgs)
        person_blocks = aggregate_by_person(display_msgs)
        display = names.get(username, username)

        md += f"---\n\n# {display}\n\n"
        md += f"> 总消息：{len(msgs)} · 文本消息：{len(display_msgs)} · 发言人：{len(person_blocks)}\n\n"
        md += "## 按发言人聚合（字数降序）\n\n"

        for p in person_blocks:
            md += f"### ▍{p['sender']}（{p['msg_count']}条，{p['char_count']}字）\n\n"
            for m in p["messages"]:
                t = m.get("time", "")[5:16]
                md += f"[{t}] {m.get('content', '')}\n\n"

        if links:
            md += "## 链接列表\n\n| 发布人 | 链接 | 时间 |\n|--------|------|------|\n"
            for l in links:
                md += f"| {l['sender']} | {l['url']} | {l.get('time', '')[11:16]} |\n"
            md += "\n"

        md += "## 完整记录（时间顺序）\n\n"
        for m in sorted(msgs, key=lambda x: x.get("time", "")):
            md += f"**{m.get('sender', '')}** ({m.get('time', '')}): {reply_prefix(m)}{m.get('content', '')}\n\n"

    filename = f"group-digest-{datetime.now().strftime('%Y%m%d')}.md"
    output_path = OUTPUT_DIR / filename
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md, encoding="utf-8")
    return send_file(output_path, as_attachment=True, download_name=filename)


@app.route("/api/export/info-export", methods=["POST"])
def api_export_info_export():
    data = request.json
    usernames = data.get("usernames", [])
    start_date = data.get("start_date", "")
    end_date = data.get("end_date", "")
    names = data.get("names", {})

    if not usernames or not start_date or not end_date:
        return jsonify({"error": "缺少必填参数"}), 400

    md = f"# 信息导出\n\n> 时间范围：{start_date} ~ {end_date}\n\n"

    for username in usernames:
        msgs = fetch_messages_for_targets([username], start_date, end_date)
        display = names.get(username, username)
        md += f"## {display}（{len(msgs)}条）\n\n"
        last_day = ""
        _wk = "一二三四五六日"
        for m in msgs:
            sender = m.get("sender", "")
            full = m.get("time", "") or ""
            day, time_str = full[:10], full[11:16]
            if day and day != last_day:  # 跨天插入日期标题（带周几）
                last_day = day
                try:
                    wk = _wk[datetime.strptime(day, "%Y-%m-%d").weekday()]
                    md += f"\n### 📅 {day} 周{wk}\n\n"
                except ValueError:
                    md += f"\n### 📅 {day}\n\n"
            content = m.get("content", "")
            if "[图片]" in content:
                md += f"**{sender}** ({time_str}): 📷 [图片]\n\n"
            else:
                md += f"**{sender}** ({time_str}): {reply_prefix(m)}{content}\n\n"

    filename = f"info-export-{datetime.now().strftime('%Y%m%d')}.md"
    output_path = OUTPUT_DIR / filename
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md, encoding="utf-8")
    return send_file(output_path, as_attachment=True, download_name=filename)


@app.route("/api/export/material-extract", methods=["POST"])
def api_export_material_extract():
    data = request.json
    usernames = data.get("usernames", [])
    start_date = data.get("start_date", "")
    end_date = data.get("end_date", "")
    names = data.get("names", "")

    if not usernames or not start_date or not end_date:
        return jsonify({"error": "缺少必填参数"}), 400

    all_messages = fetch_messages_for_targets(usernames, start_date, end_date)
    material_groups = group_materials_by_time(all_messages, gap_minutes=2)

    url_pattern = re.compile(r'https?://\S+')
    all_links = []
    seen = set()
    for msg in all_messages:
        content = msg.get("content", "")
        for url in url_pattern.findall(content):
            clean = url.rstrip('.,;!?)）。，；')
            if clean not in seen:
                seen.add(clean)
                all_links.append({"url": clean, "sender": msg.get("sender", ""), "time": msg.get("time", "")})

    md = f"# 素材提取 — {names}\n\n"
    md += f"> 时间范围：{start_date} ~ {end_date}\n"
    md += f"> 总消息：{len(all_messages)} · 素材组：{len(material_groups)}\n\n"

    for idx, g in enumerate(material_groups):
        time_range = (g["start_time"] or "")[:16]
        if g["start_time"] != g["end_time"]:
            time_range += " ~ " + (g["end_time"] or "")[11:16]
        type_parts = []
        if g["image_count"] > 0:
            type_parts.append(f"{g['image_count']}图")
        if g["text_count"] > 0:
            type_parts.append(f"{g['text_count']}文")
        md += f"## 素材组 #{idx+1} ｜ {g['sender']} ｜ {time_range} ｜ {'+'.join(type_parts)}\n\n"
        for m in g["messages"]:
            content = m.get("content", "")
            if "[图片]" in content:
                md += "📷 [图片]\n\n"
            elif "[视频]" in content:
                md += "🎬 [视频]\n\n"
            else:
                md += content + "\n\n"

    if all_links:
        md += "## 链接\n\n| 发布人 | 链接 | 时间 |\n|--------|------|------|\n"
        for l in all_links:
            md += f"| {l['sender']} | {l['url']} | {(l['time'] or '')[11:16]} |\n"

    filename = f"material-extract-{datetime.now().strftime('%Y%m%d')}.md"
    output_path = OUTPUT_DIR / filename
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md, encoding="utf-8")
    return send_file(output_path, as_attachment=True, download_name=filename)


@app.route("/api/export/info-to-folder", methods=["POST"])
def api_export_info_to_folder():
    """信息导出 = 对话完整备份：导出成一个文件夹，含 media/ 里的图片视频
    + 一份引用它们的 Markdown（带跨天日期分隔）。与素材提取的文件夹导出对齐。"""
    import shutil
    data = request.json
    usernames = data.get("usernames", [])
    start_date = data.get("start_date", "")
    end_date = data.get("end_date", "")
    names = data.get("names", {})
    if not usernames or not start_date or not end_date:
        return jsonify({"error": "缺少必填参数"}), 400

    date_tag = f"{start_date.replace('-', '')}_{end_date.replace('-', '')}"
    label = re.sub(r'[^\w一-鿿]+', '_', "_".join(names.get(u, u) for u in usernames))[:40].strip("_") or "export"
    export_dir = OUTPUT_DIR / f"对话备份_{label}_{date_tag}"
    if export_dir.exists():
        shutil.rmtree(export_dir)
    media_dir = export_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    n_img = n_vid = n_miss = 0
    _wk = "一二三四五六日"
    md = f"# 对话备份\n\n> 时间范围：{start_date} ~ {end_date}\n\n"
    for username in usernames:
        msgs = fetch_messages_for_targets([username], start_date, end_date)
        display = names.get(username, username)
        md += f"\n## {display}（{len(msgs)}条）\n\n"
        last_day = ""
        hash_map = _build_media_hash_map(username)
        for i, m in enumerate(msgs):
            full = m.get("time", "") or ""
            day, tm = full[:10], full[11:16]
            if day and day != last_day:
                last_day = day
                try:
                    wk = _wk[datetime.strptime(day, "%Y-%m-%d").weekday()]
                    md += f"\n### 📅 {day} 周{wk}\n\n"
                except ValueError:
                    md += f"\n### 📅 {day}\n\n"
            sender = m.get("sender", "")
            content = m.get("content", "")
            ts = m.get("timestamp")
            fh = hash_map.get(int(ts)) if ts else None
            if ts and "[图片]" in content:
                path, quality = find_image_file(username, int(ts), with_quality=True, file_hash=fh)
                raw = path.read_bytes() if path else None
                dec = decrypt_v2_dat(raw) if raw and raw[:6] == V2_MAGIC else raw
                if dec and dec[:3] == b"\xff\xd8\xff":
                    ext = ".jpg"
                elif dec and dec[:4] == b"\x89PNG":
                    ext = ".png"
                elif dec and dec[:4] == b"RIFF":
                    ext = ".webp"
                else:
                    dec = None
                if dec:
                    n_img += 1
                    fn = f"img_{n_img:03d}{'_thumb' if quality == 'thumb' else ''}{ext}"
                    (media_dir / fn).write_bytes(dec)
                    note = "（仅缩略图）" if quality == "thumb" else ""
                    md += f"**{sender}** ({tm}): 📷{note}\n\n![]({'media/' + fn})\n\n"
                else:
                    n_miss += 1
                    md += f"**{sender}** ({tm}): 📷 [图片]（本地无可解码文件：未下载，或为微信私有 wxgf 编码）\n\n"
            elif ts and "[视频]" in content:
                mp4, cover = find_video_file(username, int(ts), file_hash=fh)
                if mp4:
                    n_vid += 1
                    fn = f"video_{n_vid:03d}.mp4"
                    shutil.copy2(str(mp4), str(media_dir / fn))
                    md += f"**{sender}** ({tm}): 🎬 [视频] → [{fn}]({'media/' + fn})\n\n"
                elif cover:
                    n_vid += 1
                    fn = f"video_{n_vid:03d}_封面.jpg"
                    shutil.copy2(str(cover), str(media_dir / fn))
                    md += f"**{sender}** ({tm}): 🎬 [视频]（仅封面，视频未下载）\n\n![]({'media/' + fn})\n\n"
                else:
                    n_miss += 1
                    md += f"**{sender}** ({tm}): 🎬 [视频]（本地无文件）\n\n"
            else:
                md += f"**{sender}** ({tm}): {reply_prefix(m)}{content}\n\n"

    (export_dir / "对话备份.md").write_text(md, encoding="utf-8")
    return jsonify({"ok": True, "path": str(export_dir), "images": n_img, "videos": n_vid, "missing": n_miss})


@app.route("/api/export/material-to-folder", methods=["POST"])
def api_export_material_to_folder():
    import shutil
    data = request.json
    usernames = data.get("usernames", [])
    start_date = data.get("start_date", "")
    end_date = data.get("end_date", "")
    names = data.get("names", "")

    if not usernames or not start_date or not end_date:
        return jsonify({"error": "缺少必填参数"}), 400

    all_messages = fetch_messages_for_targets(usernames, start_date, end_date)
    material_groups = group_materials_by_time(all_messages, gap_minutes=2)

    safe_name = re.sub(r'[^\w一-鿿]+', '_', names or "export").strip("_")
    date_tag = f"{start_date.replace('-', '')}_{end_date.replace('-', '')}"
    export_dir = OUTPUT_DIR / f"素材_{safe_name}_{date_tag}"
    if export_dir.exists():
        shutil.rmtree(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    total_images = 0
    total_videos = 0

    for idx, g in enumerate(material_groups):
        sender_clean = re.sub(r'[^\w一-鿿]+', '', g["sender"])[:10]
        time_tag = (g["start_time"] or "")[5:16].replace(":", "").replace("-", "").replace(" ", "_")
        group_dir = export_dir / f"{idx+1:02d}_{sender_clean}_{time_tag}"
        group_dir.mkdir(exist_ok=True)

        texts = []
        img_idx = 0
        vid_idx = 0
        for m in g["messages"]:
            content = m.get("content", "")
            chat_username = m.get("chat_username", "")
            ts = m.get("timestamp")
            file_hash = (_build_media_hash_map(chat_username).get(int(ts))
                         if ts and chat_username else None)

            if ts and "[图片]" in content and chat_username:
                img_idx += 1
                dat_path, quality = find_image_file(
                    chat_username, int(ts), prefer_thumb=False,
                    with_quality=True, file_hash=file_hash)
                if dat_path:
                    with open(dat_path, "rb") as f:
                        raw = f.read()
                    decrypted = decrypt_v2_dat(raw)
                    if decrypted:
                        if decrypted[:3] == b"\xff\xd8\xff":
                            ext = ".jpg"
                        elif decrypted[:4] == b"\x89PNG":
                            ext = ".png"
                        elif decrypted[:4] == b"RIFF":
                            ext = ".webp"
                        else:
                            ext = ".bin"
                        suffix = "_thumb" if quality == "thumb" else ""
                        img_path = group_dir / f"img_{img_idx:02d}{suffix}{ext}"
                        img_path.write_bytes(decrypted)
                        if quality == "thumb":
                            texts.append(f"📷 [图片] → {img_path.name} (仅缩略图，在微信中点开查看原图后重新导出可获得高清版)")
                        else:
                            texts.append(f"📷 [图片] → {img_path.name}")
                        total_images += 1
                    else:
                        texts.append("📷 [图片] (解密失败)")
                else:
                    texts.append("📷 [图片] (未找到文件)")
            elif ts and "[视频]" in content and chat_username:
                vid_idx += 1
                mp4, cover = find_video_file(chat_username, int(ts), file_hash=file_hash)
                if mp4:
                    vid_path = group_dir / f"video_{vid_idx:02d}.mp4"
                    shutil.copy2(str(mp4), str(vid_path))
                    texts.append(f"🎬 [视频] → {vid_path.name}")
                    total_videos += 1
                elif cover:
                    cov_path = group_dir / f"video_{vid_idx:02d}_封面.jpg"
                    shutil.copy2(str(cover), str(cov_path))
                    texts.append(f"🎬 [视频] → {cov_path.name} (仅封面，视频未下载。在微信中点开播放后重新导出可获得完整视频)")
                else:
                    texts.append("🎬 [视频] (微信本地无文件，未下载)")
            else:
                texts.append(content)

        copytext_path = group_dir / "文案.txt"
        time_range = (g["start_time"] or "")[:16]
        if g["start_time"] != g["end_time"]:
            time_range += " ~ " + (g["end_time"] or "")[11:16]
        header = f"素材组 #{idx+1} ｜ {g['sender']} ｜ {time_range}\n{'=' * 40}\n\n"
        copytext_path.write_text(header + "\n".join(texts), encoding="utf-8")

    summary = f"导出完成：{len(material_groups)} 组素材，{total_images} 张图片，{total_videos} 个视频\n路径：{export_dir}"
    (export_dir / "README.txt").write_text(summary, encoding="utf-8")

    return jsonify({
        "ok": True,
        "path": str(export_dir),
        "groups": len(material_groups),
        "images": total_images,
        "videos": total_videos,
    })


@app.route("/api/sync", methods=["POST"])
def api_sync():
    """增量同步（解密）。发布包不捆绑解密引擎（见 docs/key-extraction-technique.md）。
    如你已自备解密脚本，可用环境变量 VLC_DECRYPT_CMD 指定其命令，此按钮即会调用它；
    否则返回指引，请按文档自行准备/更新已解密库。"""
    cmd = os.environ.get("VLC_DECRYPT_CMD")
    if not cmd:
        return jsonify({
            "ok": False,
            "message": "本发布包不含解密引擎。请按 docs/key-extraction-technique.md 自行准备"
                       "已解密库；或设置环境变量 VLC_DECRYPT_CMD 指向你的解密命令后再点此。",
        })
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
        lines = (result.stdout + result.stderr).strip().split("\n")
        return jsonify({"ok": True, "message": "\n".join([l for l in lines if l.strip()][-5:])})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "解密超时（>5分钟）", "ok": False}), 504
    except Exception as e:
        return jsonify({"error": str(e), "ok": False}), 500


@app.route("/api/download/<report_type>", methods=["POST"])
def api_download(report_type):
    data = request.json
    content = data.get("content", "")
    filename = data.get("filename", f"{report_type}-{datetime.now().strftime('%Y%m%d')}.md")

    output_path = OUTPUT_DIR / filename
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")

    return send_file(output_path, as_attachment=True, download_name=filename)


@app.route("/api/open-folder", methods=["POST"])
def api_open_folder():
    folder = request.json.get("path", "")
    p = Path(folder)
    if not p.exists() or not p.is_dir():
        return jsonify({"error": "文件夹不存在"}), 404
    if not str(p).startswith(str(OUTPUT_DIR)):
        return jsonify({"error": "路径不允许"}), 403
    subprocess.Popen(["open", str(p)])
    return jsonify({"ok": True})


PORT = int(os.environ.get("VLC_PORT", "5678"))


def open_browser():
    webbrowser.open(f"http://127.0.0.1:{PORT}")


if __name__ == "__main__":
    print(f"Starting local panel at http://127.0.0.1:{PORT}")
    Timer(1.5, open_browser).start()
    app.run(host="127.0.0.1", port=PORT, debug=False)
