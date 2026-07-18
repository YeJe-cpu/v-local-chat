#!/usr/bin/env python3
"""
v-local-chat 读取/分析引擎（原创，clean-room 实现）。

职责：读取"已经解密到本地的"聊天库，产出结构化消息，并做我们原创的加工——
引用回复/@提及还原、群成员昵称花名册反解、表情归一化、图片/视频解密提取。

不含也不再分发任何第三方项目的源码。密钥获取与库解密属一次性环境准备，
其技术方法见 docs/key-extraction-technique.md，本引擎只消费已解密的明文库。
"""

from __future__ import annotations

import hashlib
import html
import os
import re
import sqlite3
import struct
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    import zstandard as zstd
    _ZSTD = zstd.ZstdDecompressor()
except Exception:
    _ZSTD = None

try:
    from Crypto.Cipher import AES
except Exception:
    AES = None

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
V2_MAGIC = bytes.fromhex("070856320807")

# 已解密明文库目录（可用环境变量覆盖）
DECRYPTED_DIR = Path(
    os.environ.get("VLC_DECRYPTED_DIR")
    or "~/Library/Application Support/wechat-local-vault/decrypted/current"
).expanduser()
# 附件（图片/视频原文件）所在的微信容器目录，可用环境变量覆盖
WECHAT_CONTAINER = Path("~/Library/Containers/com.tencent.xinWeChat/Data/Documents").expanduser()

TYPE_LABELS = {
    1: "文本", 3: "图片", 34: "语音", 42: "名片", 43: "视频",
    47: "表情", 48: "位置", 49: "链接/文件", 50: "通话", 10000: "系统",
}

# ---- 微信内置表情 → Unicode（严格白名单；名单外的方括号原样保留）----
WECHAT_EMOJI = {
    "强": "👍", "弱": "👎", "玫瑰": "🌹", "呲牙": "😁", "捂脸": "🤦", "偷笑": "🤭",
    "旺柴": "🐶", "破涕为笑": "😂", "哇": "😮", "爱心": "❤️", "抱拳": "🙏", "庆祝": "🎉",
    "拥抱": "🤗", "OK": "👌", "拳头": "✊", "吃瓜": "🍉", "流泪": "😭", "色": "😍",
    "苦涩": "😣", "惊恐": "😱", "鼓掌": "👏", "太阳": "☀️", "机智": "🤓", "恐惧": "😨",
    "皱眉": "😟", "好的": "👌", "微笑": "😊", "666": "🙌", "月亮": "🌙", "可怜": "🥺",
    "发呆": "😳", "礼物": "🎁", "奸笑": "😏", "坏笑": "😏", "裂开": "🫠", "嘿哈": "😆",
    "抓狂": "😫", "红包": "🧧", "合十": "🙏", "让我看看": "👀", "胜利": "✌️", "烟花": "🎆",
    "跳跳": "😝", "害羞": "☺️", "憨笑": "😄", "耶": "✌️", "愉快": "☺️", "加油": "💪",
    "得意": "😎", "嘴唇": "💋", "咖啡": "☕", "尴尬": "😰", "亲亲": "😘", "汗": "😓",
    "撇嘴": "😖", "发抖": "🥶", "惊讶": "😲", "转圈": "💫", "调皮": "😜", "脸红": "😊",
    "衰": "😩", "握手": "🤝", "擦汗": "😥", "翻白眼": "🙄", "大哭": "😩", "白眼": "🙄",
    "笑脸": "😄", "天啊": "😱", "阴险": "😏", "福": "🧧", "困": "😪", "疑问": "❓",
    "囧": "😖", "难过": "🙁", "蛋糕": "🎂", "晕": "😵", "敲打": "👊", "爆竹": "🧨",
    "快哭了": "😢", "无语": "😑", "委屈": "🥺", "发怒": "😡", "心碎": "💔", "嘘": "🤫",
    "菜刀": "🔪", "闪电": "⚡", "睡": "😴", "咒骂": "🤬", "失望": "😞", "凋谢": "🥀",
    "吐": "🤮", "傲慢": "😤", "再见": "👋", "啤酒": "🍺", "炸弹": "💣", "怄火": "😡",
    "闭嘴": "🤐", "猪头": "🐷", "鄙视": "😒", "生病": "🤒", "便便": "💩", "骷髅": "💀",
    "流汗": "😓", "疯了": "🤪", "掩面": "🙈", "奋斗": "💪", "飞吻": "😘", "西瓜": "🍉",
    "碰拳": "👊", "无辜笑": "😇", "不看": "🙈", "叉号": "❌", "勾号": "✅", "点击": "👆",
    "100分": "💯", "火": "🔥", "泣不成声": "😭", "吐血": "🤮", "爱你": "🥰", "爱情": "💕",
    "笑哭R": "😂", "赞R": "👍", "偷笑R": "🤭", "红色心形R": "❤️", "给心心": "💗",
    "破涕為笑": "😂", "擁抱": "🤗", "難受": "😣", "尷尬": "😰", "愛心": "❤️",
    "慶祝": "🎉", "發呆": "😳", "親親": "😘",
    "Heart": "❤️", "Rose": "🌹", "Hug": "🤗", "Salute": "🙏", "ThumbsUp": "👍",
    "Chuckle": "🤭", "Grin": "😁", "NO": "🙅", "No": "🙅", "Yes": "🙆",
    "Speechless": "😑", "Concerned": "😟", "Whimper": "🥺", "Wilt": "🥀",
}
_EMOJI_RE = re.compile(r"\[([^\[\]]{1,10})\]")


def normalize_emoji(text: str) -> str:
    if not text or "[" not in text:
        return text
    return _EMOJI_RE.sub(lambda m: WECHAT_EMOJI.get(m.group(1), m.group(0)), text)


# ---------------- 低层辅助 ----------------
def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def table_exists(con, table) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def table_columns(con, table) -> set:
    try:
        return {r["name"] for r in con.execute(f"PRAGMA table_info([{table}])")}
    except sqlite3.Error:
        return set()


def decode_value(value, flag=None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    data = bytes(value)
    if (data.startswith(ZSTD_MAGIC) or flag == 4) and _ZSTD:
        try:
            data = _ZSTD.decompress(data, max_output_size=1_000_000)
        except Exception:
            return "[压缩消息解码失败]"
    return data.decode("utf-8", "replace")


def split_msg_type(local_type):
    try:
        v = int(local_type or 0)
    except (TypeError, ValueError):
        return 0, 0
    return (v & 0xFFFFFFFF, v >> 32) if v > 0xFFFFFFFF else (v, 0)


def type_label(local_type) -> str:
    base, _ = split_msg_type(local_type)
    return TYPE_LABELS.get(base, f"type={local_type}")


def message_table(username: str) -> str:
    return "Msg_" + hashlib.md5(username.encode()).hexdigest()


def message_dbs(decrypted_dir: Path):
    d = decrypted_dir / "message"
    return sorted(d.glob("message_[0-9]*.db")) if d.is_dir() else []


def load_contacts(decrypted_dir: Path):
    """返回 {username: display_name}，并把微信号别名也并入以便 @ 解析。"""
    db = decrypted_dir / "contact/contact.db"
    contacts, aliases = {}, {}
    if not db.exists():
        return contacts, aliases
    with connect(db) as con:
        if not table_exists(con, "contact"):
            return contacts, aliases
        cols = table_columns(con, "contact")
        want = [c for c in ("username", "remark", "nick_name", "alias") if c in cols]
        for r in con.execute(f"SELECT {','.join(want)} FROM contact"):
            u = str(r["username"] or "")
            if not u:
                continue
            disp = str(r["remark"] or "") or str(r["nick_name"] or "") or u
            contacts[u] = disp
            al = str(r["alias"] or "")
            if al and disp != u:
                aliases[al] = disp
    return contacts, aliases


def load_name2id(con):
    m = {}
    if not table_exists(con, "Name2Id"):
        return m
    try:
        for r in con.execute("SELECT rowid, user_name FROM Name2Id"):
            if r["user_name"]:
                m[int(r["rowid"])] = decode_value(r["user_name"])
    except sqlite3.Error:
        return {}
    return m


# ---------------- 原创加工：昵称/引用/@ ----------------
def resolve_name(username, contacts, roster=None):
    if not username:
        return ""
    name = contacts.get(username) or username
    if name != username:
        return name
    if roster and roster.get(username):
        return roster[username]
    return name


def parse_sender_prefix(content):
    # 群消息内容常以 `发送人id:\n正文` 开头（id 可能是 wxid_/gh_/微信号别名）。
    # 只要前缀不是聊天室 id，就当作发送人前缀剥掉。
    if ":\n" not in content:
        return "", content
    head, text = content.split(":\n", 1)
    if head.startswith("wxid_") or "@chatroom" not in head:
        return head, text
    return "", content


def parse_mentions(source, contacts, roster=None):
    if not source or "<atuserlist>" not in source:
        return []
    m = re.search(r"<atuserlist>(.*?)</atuserlist>", source, re.S)
    if not m:
        return []
    raw = m.group(1).strip()
    cd = re.search(r"<!\[CDATA\[(.*?)\]\]>", raw, re.S)
    if cd:
        raw = cd.group(1)
    out = []
    for tok in re.split(r"[,、\s]+", raw):
        tok = tok.strip()
        if not tok:
            continue
        if tok == "notify@all":
            out.append("所有人")
            continue
        name = resolve_name(tok, contacts, roster)
        if name and name != tok:
            out.append(name)
    return out


def parse_reply(local_type, content, contacts, roster=None):
    base, _ = split_msg_type(local_type)
    if base != 49 or "<refermsg>" not in content:
        return None
    to_name = quoted = from_usr = ""
    refer_type = None
    try:
        root = ET.fromstring(content)
        r = root.find(".//refermsg")
        if r is not None:
            to_name = (r.findtext("displayname") or "").strip()
            quoted = (r.findtext("content") or "").strip()
            refer_type = r.findtext("type")
            from_usr = (r.findtext("fromusr") or "").strip()
    except Exception:
        nm = re.search(r"<refermsg>.*?<displayname>(.*?)</displayname>", content, re.S)
        qt = re.search(r"<refermsg>.*?<content>(.*?)</content>", content, re.S)
        tp = re.search(r"<refermsg>\s*<type>(\d+)</type>", content, re.S)
        fu = re.search(r"<refermsg>.*?<fromusr>(.*?)</fromusr>", content, re.S)
        to_name = nm.group(1).strip() if nm else ""
        quoted = qt.group(1).strip() if qt else ""
        refer_type = tp.group(1) if tp else None
        from_usr = fu.group(1).strip() if fu else ""
    if not to_name and from_usr and "@chatroom" not in from_usr:
        rn = resolve_name(from_usr, contacts, roster)
        if rn and rn != from_usr:
            to_name = rn
    if not to_name and not quoted:
        return None
    # 被引用的本身是 appmsg（引用回复/链接/文件，refermsg.type=49）时，content 里嵌着它的
    # XML，取真实文本（引用回复取 title），而不是笼统标成"链接/文件"；其它媒体用类型标签。
    try:
        rtype = int(refer_type) if refer_type is not None else None
    except (TypeError, ValueError):
        rtype = None
    if "<appmsg" in quoted or "&lt;appmsg" in quoted:
        inner = html.unescape(quoted) if "&lt;" in quoted else quoted
        quoted = format_content(49, inner)
    elif rtype is not None and rtype != 1:
        quoted = type_label(rtype)
    if quoted.startswith("<"):
        quoted = "[非文本内容]"
    return {"to_name": to_name, "quoted": normalize_emoji(quoted)}


def build_group_roster(decrypted_dir, username):
    """扫全群引用回复的 fromusr(wxid)+displayname 配对，确定性建 wxid→群昵称。"""
    roster = {}
    target = message_table(username)
    for db in message_dbs(decrypted_dir):
        with connect(db) as con:
            if not table_exists(con, target):
                continue
            cols = table_columns(con, target)
            mc = "message_content" if "message_content" in cols else None
            if not mc:
                continue
            cc = "compress_content" if "compress_content" in cols else "NULL"
            flag = "WCDB_CT_message_content" if "WCDB_CT_message_content" in cols else "NULL"
            try:
                for r in con.execute(f"SELECT {mc} AS mc, {cc} AS cc, {flag} AS flag FROM [{target}]"):
                    s = decode_value(r["mc"], r["flag"]) or decode_value(r["cc"], r["flag"])
                    if "<refermsg>" not in s:
                        continue
                    fu = re.search(r"<refermsg>.*?<fromusr>(.*?)</fromusr>", s, re.S)
                    dn = re.search(r"<refermsg>.*?<displayname>(.*?)</displayname>", s, re.S)
                    if fu and dn:
                        w, name = fu.group(1).strip(), dn.group(1).strip()
                        if w.startswith("wxid_") and name:
                            roster.setdefault(w, name)
            except sqlite3.Error:
                continue
    return roster


def annotate_content(msg):
    """给扁平文本/Markdown 场景加回应锚点（面板用结构化字段自渲染，不用这个）。"""
    text = msg.get("content") or ""
    r = msg.get("reply_to")
    if r:
        q = r.get("quoted") or ""
        if len(q) > 40:
            q = q[:40] + "…"
        text = f"[回复 {r.get('to_name') or '某人'}「{q}」] {text}".strip()
    ment = msg.get("mentions") or []
    if ment:
        j = "、".join(ment)
        if f"@{j}" not in text:
            text = f"[@{j}] {text}".strip()
    return text


# ---------------- 内容格式化 + 消息读取 ----------------
def format_content(local_type, content):
    base, sub = split_msg_type(local_type)
    if base == 3:
        return "[图片]"
    if base == 34:
        m = re.search(r'voicelength="(\d+)"', content or "")
        return f"[语音，约 {int(m.group(1))/1000:.1f} 秒]" if m else "[语音]"
    if base == 43:
        return "[视频]"
    if base == 47:
        return "[表情]"
    if base == 48:
        return "[位置]"
    if base == 42:
        return "[名片]"
    if base == 50:
        return "[通话]"
    if base == 49:
        try:
            root = ET.fromstring(content)
            app_type = int(root.findtext(".//type") or 0)
            title = (root.findtext(".//title") or "").strip()
            des = (root.findtext(".//des") or "").strip()
            if app_type == 57:
                return normalize_emoji((title or des).strip()) or "[引用回复]"
            if app_type == 5:
                return f"[链接] {title or des}".strip()
            if app_type in (33, 36, 44):
                return f"[小程序] {title or des}".strip()
            if sub == 6 or app_type == 6:
                return f"[文件] {title or des}".strip()
            return f"[链接/文件] {title or des}".strip()
        except Exception:
            return content.strip() or "[链接/文件]"
    if base == 10000:
        # 系统消息：带 XML 标签的（群公告 ChatRoomTopMsgResponse / 撤回 / 拍一拍等）给干净标签，
        # 别把原始 XML 吐到界面；纯文本的（"X邀请Y加入了群聊"）保留原文。
        if re.search(r"<[a-zA-Z/!]", content):
            return "[系统消息]"
        return normalize_emoji(content.strip())
    if base != 1:
        return f"[{type_label(local_type)}] {normalize_emoji(content)}".strip()
    return normalize_emoji(content.strip())


def _row_to_message(row, chat, contacts, name2id, roster):
    lt = row["local_type"]
    ts = int(row["create_time"] or 0)
    content = decode_value(row["message_content"], row["flag"]) or decode_value(row["compress_content"], row["flag"])
    prefix_sender, content = parse_sender_prefix(content)
    sid = row["real_sender_id"]
    sender_username = ""
    try:
        sender_username = name2id.get(int(sid), "")
    except (TypeError, ValueError):
        sender_username = ""
    if not sender_username:
        sender_username = prefix_sender
    base_type, _ = split_msg_type(lt)
    is_system = base_type == 10000
    if is_system:
        # 系统消息（邀请入群 / "非朋友关系"提示 / 群公告 / 撤回等）不是任何人的发言，
        # 统一归为"系统"，绝不按 real_sender_id 挂到某个人头上（否则新群会被入群通知霸榜）。
        sender = "系统"
    elif chat["is_group"]:
        sender = resolve_name(sender_username, contacts, roster) if sender_username else ""
        if sender_username and (not sender or sender == sender_username):
            sender = f"群友-{sender_username[5:11]}" if sender_username.startswith("wxid_") else sender_username
    elif sender_username and sender_username != chat["username"]:
        sender = contacts.get(sender_username, sender_username)
    elif str(sid) == "2":
        sender = "我"
    else:
        sender = chat["display_name"]
    src = decode_value(row["source"]) if "source" in row.keys() else ""
    return {
        "local_id": row["local_id"], "local_type": lt, "type": type_label(lt),
        "sender": sender, "sender_username": sender_username, "is_system": is_system,
        "timestamp": ts, "time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "",
        "content": format_content(lt, content),
        "reply_to": parse_reply(lt, content, contacts, roster),
        "mentions": parse_mentions(src, contacts, roster),
    }


def get_chat_history(username, start_ts=None, end_ts=None, limit=2000, resolve_media=True):
    """读取某会话某时间段的消息，含我们原创的引用/@/花名册/表情加工。"""
    contacts, aliases = load_contacts(DECRYPTED_DIR)
    chat = {"username": username, "display_name": contacts.get(username, username),
            "is_group": "@chatroom" in username}
    target = message_table(username)
    roster = build_group_roster(DECRYPTED_DIR, username) if chat["is_group"] else {}
    roster.update({a: n for a, n in aliases.items() if a not in roster})
    rows = []
    for db in message_dbs(DECRYPTED_DIR):
        with connect(db) as con:
            if not table_exists(con, target):
                continue
            cols = table_columns(con, target)
            sel = ["local_id", "local_type", "real_sender_id", "create_time", "message_content"]
            sel.append("compress_content" if "compress_content" in cols else "NULL AS compress_content")
            sel.append("WCDB_CT_message_content AS flag" if "WCDB_CT_message_content" in cols else "NULL AS flag")
            sel.append("source" if "source" in cols else "NULL AS source")
            where, params = [], []
            if start_ts is not None:
                where.append("create_time >= ?"); params.append(start_ts)
            if end_ts is not None:
                where.append("create_time <= ?"); params.append(end_ts)
            sql = f"SELECT {', '.join(sel)} FROM [{target}]"
            if where:
                sql += " WHERE " + " AND ".join(where)
            name2id = load_name2id(con)  # 每库加载一次，别放进行循环
            for r in con.execute(sql, params):
                rows.append(_row_to_message(r, chat, contacts, name2id, roster))
    rows.sort(key=lambda m: (m["timestamp"], str(m["local_id"])))
    if resolve_media:
        enrich_media(username, rows[:limit] if limit else rows)
    return rows[:limit] if limit else rows


def search_contacts(query, decrypted_dir=None):
    """给面板的 /api/search 用：按关键词搜联系人/群聊。"""
    decrypted_dir = decrypted_dir or DECRYPTED_DIR
    db = decrypted_dir / "contact/contact.db"
    if not db.exists():
        return []
    q = query.lower()
    out = []
    with connect(db) as con:
        cols = table_columns(con, "contact")
        want = [c for c in ("username", "remark", "nick_name", "alias") if c in cols]
        for r in con.execute(f"SELECT {','.join(want)} FROM contact"):
            u = str(r["username"] or "")
            if not u:
                continue
            disp = str(r["remark"] or "") or str(r["nick_name"] or "") or u
            hay = " ".join(str(r[c] or "") for c in want).lower()
            if q in hay:
                out.append({"username": u, "display": disp, "type": "group" if "@chatroom" in u else "contact"})
    out.sort(key=lambda x: (x["type"] != "group", x["display"]))
    return out[:20]


# ---------------- 图片/视频提取（原创：含 AES 1040 字节色差修复）----------------
def _find_wechat_data_dir():
    if os.environ.get("VLC_WECHAT_DATA_DIR"):
        return Path(os.environ["VLC_WECHAT_DATA_DIR"]).expanduser()
    xw = WECHAT_CONTAINER / "xwechat_files"
    if not xw.is_dir():
        return None
    for d in sorted(xw.iterdir()):
        if d.is_dir() and (d / "msg" / "attach").is_dir():
            return d
    return None


def _derive_keys(data_dir):
    if not data_dir:
        return None, None
    m = re.match(r"^(wxid_[^_]+)", data_dir.name, re.IGNORECASE)
    wxid = m.group(1) if m else data_dir.name
    kv = WECHAT_CONTAINER / "app_data" / "net" / "kvcomm"
    if not kv.is_dir():
        return None, None
    for f in kv.iterdir():
        km = re.match(r"^key_(\d+)_.+\.statistic$", f.name, re.IGNORECASE)
        if km:
            uin = int(km.group(1))
            return hashlib.md5(f"{uin}{wxid}".encode()).hexdigest()[:16], uin & 0xFF
    return None, None


WECHAT_DATA_DIR = _find_wechat_data_dir()
IMAGE_AES_KEY, IMAGE_XOR_KEY = _derive_keys(WECHAT_DATA_DIR)


def image_ready():
    return bool(AES and IMAGE_AES_KEY and WECHAT_DATA_DIR)


def decrypt_v2_dat(data):
    """AES 区实际字节 = aes_size//16*16+16（PKCS#7 多补一整块），解密后取前 aes_size 丢填充。"""
    if not AES or not IMAGE_AES_KEY or len(data) < 15 or data[:6] != V2_MAGIC:
        return None
    aes_size = struct.unpack_from("<I", data, 6)[0]
    xor_size = struct.unpack_from("<I", data, 10)[0]
    body = data[15:]
    enc = min((aes_size // 16) * 16 + 16, len(body))
    res = bytearray(AES.new(IMAGE_AES_KEY.encode("ascii"), AES.MODE_ECB).decrypt(body[:enc])[:aes_size])
    mid = min(max(len(body) - xor_size, enc), len(body))
    res += body[enc:mid]
    res += bytes(b ^ IMAGE_XOR_KEY for b in body[mid:])
    return bytes(res)


def _dat_kind(path):
    try:
        with open(path, "rb") as f:
            head = f.read(31)
    except OSError:
        return None
    if len(head) < 4:
        return None
    if head[:6] != V2_MAGIC:
        if head[:3] == b"\xff\xd8\xff":
            return "jpeg"
        if head[:4] == b"\x89PNG":
            return "png"
        if head[:4] == b"RIFF":
            return "webp"
        return "other"
    if not AES or not IMAGE_AES_KEY or len(head) < 31:
        return None
    dec = AES.new(IMAGE_AES_KEY.encode("ascii"), AES.MODE_ECB).decrypt(head[15:31])
    if dec[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if dec[:4] == b"\x89PNG":
        return "png"
    if dec[:4] == b"RIFF":
        return "webp"
    if dec[:4] == b"wxgf":
        return "wxgf"
    return "other"


def _media_hash_map(username):
    result = {}
    target = message_table(username)
    for db in message_dbs(DECRYPTED_DIR):
        with connect(db) as con:
            if not table_exists(con, target) or "packed_info_data" not in table_columns(con, target):
                continue
            try:
                for r in con.execute(f"SELECT create_time, packed_info_data FROM [{target}] "
                                     "WHERE (local_type & 4294967295) IN (3, 43)"):
                    pk = r["packed_info_data"]
                    if pk:
                        m = re.search(rb"([0-9a-f]{32})", bytes(pk))
                        if m:
                            result[int(r["create_time"] or 0)] = m.group(1).decode()
            except sqlite3.Error:
                continue
            break
    return result


def find_image_file(username, ts, file_hash=None, prefer_thumb=False):
    if not WECHAT_DATA_DIR:
        return None, "none"
    ch = hashlib.md5(username.encode()).hexdigest()
    month = datetime.fromtimestamp(ts).strftime("%Y-%m")
    d = WECHAT_DATA_DIR / "msg" / "attach" / ch / month / "Img"
    if not d.is_dir():
        return None, "none"
    base = None
    if file_hash and any((d / f"{file_hash}{s}").exists() for s in ("_t.dat", ".dat", "_h.dat")):
        base = file_hash
    if base is None:
        best, bd = None, float("inf")
        for f in d.iterdir():
            if not f.name.endswith("_t.dat"):
                continue
            try:
                delta = abs(f.stat().st_mtime - ts)
            except OSError:
                continue
            if delta < bd:
                bd, best = delta, f
        if not best or bd >= 15:
            return None, "none"
        base = best.name.replace("_t.dat", "")
    thumb = d / f"{base}_t.dat"
    if prefer_thumb:
        return (thumb, "thumb") if thumb.exists() else (None, "none")
    for name in (f"{base}_h.dat", f"{base}.dat"):
        c = d / name
        if c.exists() and _dat_kind(c) in ("jpeg", "png", "webp"):
            return c, "hd"
    return (thumb, "thumb") if thumb.exists() else (None, "none")


def find_video_file(username, ts, file_hash=None):
    if not WECHAT_DATA_DIR or not file_hash:
        return None, None
    month = datetime.fromtimestamp(ts).strftime("%Y-%m")
    v = WECHAT_DATA_DIR / "msg" / "video" / month
    mp4, cover = v / f"{file_hash}.mp4", v / f"{file_hash}.jpg"
    return (mp4 if mp4.exists() else None), (cover if cover.exists() else None)


def image_bytes(path):
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None, ""
    data = decrypt_v2_dat(raw) if raw[:6] == V2_MAGIC else raw
    if not data:
        return None, ""
    if data[:3] == b"\xff\xd8\xff":
        return data, "jpg"
    if data[:4] == b"\x89PNG":
        return data, "png"
    if data[:4] == b"RIFF":
        return data, "webp"
    return None, ""


def enrich_media(username, messages):
    if not WECHAT_DATA_DIR:
        return
    hmap = _media_hash_map(username)
    for m in messages:
        base, _ = split_msg_type(m.get("local_type"))
        h = hmap.get(m.get("timestamp"))
        if base == 3:
            p, q = find_image_file(username, m["timestamp"], file_hash=h)
            m["image_path"], m["image_quality"] = (str(p) if p else None), q
        elif base == 43:
            mp4, cover = find_video_file(username, m["timestamp"], file_hash=h)
            m["video_path"], m["video_cover"] = (str(mp4) if mp4 else None), (str(cover) if cover else None)

