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
    quoted = strip_group_prefix(quoted)
    if "<appmsg" in quoted or "&lt;appmsg" in quoted:
        inner = html.unescape(quoted) if "&lt;" in quoted else quoted
        quoted = format_content(49, inner)
    elif rtype is not None and rtype != 1:
        quoted = type_label(rtype)
    quoted = xml_to_clean(quoted)
    if quoted.strip().startswith(("<", "&lt;")):
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
    text = xml_to_clean(msg.get("content") or "")
    r = msg.get("reply_to")
    if r:
        q = xml_to_clean(r.get("quoted") or "")
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
# 只匹配真卡片强信号（不含裸 <msg>，避免误吃用户打的 <msg> 文本）
_XML_MSG_RE = re.compile(r"<\?xml|<appmsg[>\s/]|<sysmsg[>\s/]|<voipmsg|<voicemsg|<emoji[>\s/]|<img\s|<recordinfo")


def strip_group_prefix(s):
    """剥掉群消息里 `wxid_xxx:\\n` 发送人前缀（引用原文/被嵌套内容常带，会让 XML 解析失败）。"""
    return re.sub(r"^[\w\-@.]{1,64}:\n", "", s or "", count=1)


def xml_to_clean(text):
    """把残留的结构化消息 XML（appmsg/图文/系统/语音卡片）收敛成干净文本，绝不吐原始 XML。

    否则 `history --format text` 会吐一整段 XML，逼下游按行 grep/head 清噪音、进而截断消息。
    能抽 <title> 就抽，抽不到给干净标签；只处理明确结构化标签，不误伤正文里偶发的 `<`。"""
    if not text or ("<" not in text and "&lt;" not in text):
        return text
    raw = html.unescape(text) if "&lt;" in text else text
    if not _XML_MSG_RE.search(raw):
        return text
    m = re.search(r"<title>(.*?)</title>", raw, re.S)
    if m and m.group(1).strip():
        return normalize_emoji(m.group(1).strip())
    if "<voicemsg" in raw or "<voipmsg" in raw:
        return "[语音/通话]"
    return "[卡片消息]"


def expand_merged_forward(content, max_items=40):
    """展开合并转发的聊天记录（appmsg type 19）：嵌套 datalist 逐条抽成可读文本。
    完整内容（发言人+时间+每条正文）就在本地库 recordinfo/datalist 里，不展开只剩一个标签、
    整段对话白白丢掉。少数没有 datalist 的（内容在单独文件、未下载）才真展不开，如实标注。"""
    raw = html.unescape(content) if "&lt;" in content else content
    tm = re.search(r"<title>(.*?)</title>", raw, re.S)
    title = tm.group(1).strip() if tm else "聊天记录"
    items = re.findall(r"<dataitem\b.*?</dataitem>", raw, re.S)
    if not items:
        return f"[合并转发·{title}]（本地无展开内容，可能未下载）"
    tmap = {"1": "", "2": "[图片]", "4": "[视频]", "5": "[链接]", "6": "[位置]", "8": "[文件]", "34": "[语音]"}
    lines = [f"[合并转发·{len(items)}条·{title}]"]
    for it in items[:max_items]:
        sn = re.search(r"<sourcename>(.*?)</sourcename>", it, re.S)
        st = re.search(r"<sourcetime>(.*?)</sourcetime>", it, re.S)
        dd = re.search(r"<datadesc>(.*?)</datadesc>", it, re.S) or re.search(r"<datatitle>(.*?)</datatitle>", it, re.S)
        dtype = re.search(r'datatype="?(\d+)', it) or re.search(r"<datatype>(\d+)", it)
        who = html.unescape(sn.group(1).strip()) if sn else "?"
        when = html.unescape(st.group(1).strip()) if st else ""
        if dd and dd.group(1).strip():
            body = normalize_emoji(html.unescape(dd.group(1).strip())).replace("\n", " ")
        else:
            body = tmap.get(dtype.group(1) if dtype else "", "[非文本]")
        lines.append(f"  · {when} {who}: {body}".rstrip())
    if len(items) > max_items:
        lines.append(f"  …还有 {len(items) - max_items} 条（超出展开上限）")
    return "\n".join(lines)


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
        content = strip_group_prefix(content)  # 群 appmsg 常带发送人前缀，会让解析失败
        try:
            root = ET.fromstring(content)
            app_type = int(root.findtext(".//type") or 0)
            title = (root.findtext(".//title") or "").strip()
            des = (root.findtext(".//des") or "").strip()
            if app_type == 57:
                return normalize_emoji((title or des).strip()) or "[引用回复]"
            if app_type == 19 or "<recordinfo>" in content or "<datalist" in content:
                return expand_merged_forward(content)
            if app_type == 5:
                return f"[链接] {title or des}".strip()
            if app_type in (33, 36, 44):
                return f"[小程序] {title or des}".strip()
            if sub == 6 or app_type == 6:
                return f"[文件] {title or des}".strip()
            return f"[链接/文件] {title or des}".strip()
        except Exception:
            cleaned = xml_to_clean(content)
            if not _XML_MSG_RE.search(cleaned):
                return cleaned or "[链接/文件]"
            t = re.search(r"<title>(.*?)</title>", html.unescape(content), re.S)
            return (normalize_emoji(t.group(1).strip()) if t and t.group(1).strip() else "[链接/文件]")
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


_DEFAULT_LIMIT = object()  # 区分"没传 limit"和"显式传了数字"


def _has_message_table(username, decrypted_dir=None):
    """该 username 在任一 message 库里是否真的有会话表。"""
    d = decrypted_dir or DECRYPTED_DIR
    target = message_table(username)
    for db in message_dbs(d):
        try:
            with connect(db) as con:
                if table_exists(con, target):
                    return True
        except sqlite3.Error:
            continue
    return False


def resolve_chat_username(query, decrypted_dir=None):
    """把"群名/备注名/昵称"解析成真实 username（wxid_xxx / xxx@chatroom）。

    为什么必须有这一步：会话表名是 md5(username)。若直接把人类可读的名字传进去，
    算出的表名根本不存在，于是**静默返回 0 条**——用户会以为"这个群没有消息"，
    实际是没找到会话。这类静默空结果比报错危险得多，所以这里找不到就明确抛错。
    """
    if not query or not str(query).strip():
        raise ValueError("会话名不能为空")
    q = str(query).strip()
    # 原始 id 直接用
    if q.endswith("@chatroom") or q.startswith(("wxid_", "gh_")):
        return q
    # 已经是有效 username 就直接用，不要当"名字"去解析——否则合法的系统账号
    # （notifymessage/qqtech 等无标准前缀的 username）会被误判成"找不到"而报错。
    # ① 能直接定位到会话表 → 一定是有效 username；
    # ② 或它本身就是联系人表里的 username（哪怕没聊过，返回空才是诚实答案，不该报错）。
    if _has_message_table(q, decrypted_dir):
        return q
    contacts, _ = load_contacts(decrypted_dir or DECRYPTED_DIR)
    if q in contacts:
        return q
    cands = search_contacts(q, decrypted_dir)
    if not cands:
        raise ValueError(f"找不到会话: {q}（请用群名/备注名/昵称，或直接用 username）")
    # 优先：显示名完全相同；其次：确实存在会话表的候选
    exact = [c for c in cands if c["display"].strip().lower() == q.lower()]
    pool = exact or cands
    with_table = [c for c in pool if _has_message_table(c["username"], decrypted_dir)]
    if len(with_table) == 1:
        return with_table[0]["username"]
    if not with_table:
        raise ValueError(
            f"找到联系人「{pool[0]['display']}」但本地没有它的聊天记录表"
            f"（可能从未聊过，或该库未解密）")
    names = "、".join(f"{c['display']}({c['username']})" for c in with_table[:5])
    raise ValueError(f"「{q}」匹配到多个会话，请写得更精确或直接用 username：{names}")


def get_chat_history(username, start_ts=None, end_ts=None, limit=_DEFAULT_LIMIT,
                     resolve_media=True):
    """读取某会话某时间段的消息，含我们原创的引用/@/花名册/表情加工。

    limit 语义（踩过坑，别改回去）：**给了时间范围就默认取全量**——否则用户拉一整月
    却被默认条数悄悄截断，会误以为"这天数据没了"。只有完全不给时间范围时才兜底截断，
    且截**最近的**而不是最老的。显式传数字则以显式为准。
    """
    if limit is _DEFAULT_LIMIT:
        limit = None if (start_ts is not None or end_ts is not None) else 2000
        _tail = limit is not None  # 无时间范围的兜底：要最近的
    else:
        _tail = False
    # 允许传"群名/备注名/昵称"：不解析成真实 username 就会算出不存在的表名、
    # 静默返回 0 条（SKILL.md 里承诺的就是"联系人或群名/username"都能用）。
    username = resolve_chat_username(username)
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
    if limit:
        rows = rows[-limit:] if _tail else rows[:limit]
    if resolve_media:
        enrich_media(username, rows)
    return rows


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


# 惰性初始化：定位聊天软件容器、派生图片密钥都要读它的容器目录，而 macOS 只要有 App
# 去读另一个 App 的容器就会弹隐私授权。纯文字查询读的是我们自己解密好的 vault，
# 本就不需要碰容器——写在模块顶层会让任何一条命令一启动就摸容器、白白触发一次弹窗。
_LAZY = {}


def wechat_data_dir():
    if "dir" not in _LAZY:
        _LAZY["dir"] = _find_wechat_data_dir()
    return _LAZY["dir"]


def image_keys():
    if "keys" not in _LAZY:
        _LAZY["keys"] = _derive_keys(wechat_data_dir())
    return _LAZY["keys"]


def __getattr__(name):
    """兼容 `vault.WECHAT_DATA_DIR` 这类模块属性访问（面板在用），且保持惰性。

    模块级 __getattr__ 只对外部属性访问生效；本模块内部必须直接调用
    wechat_data_dir() / image_keys()。
    """
    if name == "WECHAT_DATA_DIR":
        return wechat_data_dir()
    if name == "IMAGE_AES_KEY":
        return image_keys()[0]
    if name == "IMAGE_XOR_KEY":
        return image_keys()[1]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def image_ready():
    return bool(AES and image_keys()[0] and wechat_data_dir())


def decrypt_v2_dat(data):
    """AES 区实际字节 = aes_size//16*16+16（PKCS#7 多补一整块），解密后取前 aes_size 丢填充。"""
    aes_key, xor_key = image_keys()
    if not AES or not aes_key or len(data) < 15 or data[:6] != V2_MAGIC:
        return None
    aes_size = struct.unpack_from("<I", data, 6)[0]
    xor_size = struct.unpack_from("<I", data, 10)[0]
    body = data[15:]
    enc = min((aes_size // 16) * 16 + 16, len(body))
    res = bytearray(AES.new(aes_key.encode("ascii"), AES.MODE_ECB).decrypt(body[:enc])[:aes_size])
    mid = min(max(len(body) - xor_size, enc), len(body))
    res += body[enc:mid]
    res += bytes(b ^ xor_key for b in body[mid:])
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
    aes_key = image_keys()[0]
    if not AES or not aes_key or len(head) < 31:
        return None
    dec = AES.new(aes_key.encode("ascii"), AES.MODE_ECB).decrypt(head[15:31])
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
    wdir = wechat_data_dir()
    if not wdir:
        return None, "none"
    ch = hashlib.md5(username.encode()).hexdigest()
    month = datetime.fromtimestamp(ts).strftime("%Y-%m")
    d = wdir / "msg" / "attach" / ch / month / "Img"
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


def hd_absence_reason(thumb_path):
    """只拿到缩略图时，说明高清究竟为什么没用上：wxgf / missing / other。

    切勿由“只有缩略图”反推“用户没点开过”——实测不成立：用户点开后微信确实会下载高清，
    但新版下载的高清可能是 wxgf 私有编码、本地无法解码，于是仍然只能退回缩略图。
      wxgf    -> 高清已下载，但私有编码本地无解（点开也没用）
      missing -> 本地根本没有高清文件（可能未点开，或尚未下载完）
    """
    name = str(thumb_path)
    if not name.endswith("_t.dat"):
        return "unknown"
    stem = name[: -len("_t.dat")]
    for suf in ("_h.dat", ".dat"):
        c = Path(stem + suf)
        if c.exists():
            return "wxgf" if _dat_kind(c) == "wxgf" else "other"
    return "missing"


def find_video_file(username, ts, file_hash=None):
    wdir = wechat_data_dir()
    if not wdir or not file_hash:
        return None, None
    month = datetime.fromtimestamp(ts).strftime("%Y-%m")
    v = wdir / "msg" / "video" / month
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
    # 这批消息里压根没有图片/视频时，直接返回——避免为纯文字对话去摸聊天软件容器
    # （macOS 上那一摸就会弹“想访问其他应用程序的数据”）。
    if not any(split_msg_type(m.get("local_type"))[0] in (3, 43) for m in messages):
        return
    if not wechat_data_dir():
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



# ---------------------------------------------------------------------------
# 媒体导出：把加密的 .dat 解密成真实 .jpg/.png/.mp4 落盘。
# 这是"让 AI 真正看到图片"的唯一通路——图片在本地是加密缓存，
# 直接读 image_path 只会拿到乱码；必须先导出，再用读图工具读导出的 .jpg。
# ---------------------------------------------------------------------------

def _safe_name(text, limit=20):
    return re.sub(r"[^\w一-鿿-]", "_", str(text or "unknown"))[:limit]


def export_media(username, out_dir, start_ts=None, end_ts=None, limit=None):
    """导出某会话某时间段的图片/视频到目录，返回统计与清单。

    返回 dict：{"output_dir", "saved": {...}, "manifest": [...]}
    saved.image_thumb       = 只导出到 180px 缩略图（缩略图是真图、能打开，只是小）
    saved.thumb_hd_is_wxgf  = 高清**已下载**但是 wxgf 私有编码，本地无法解码（点开也没用）
    saved.thumb_hd_missing  = 本地确实没有高清文件（可建议去软件里点开后重跑）
    saved.undecodable       = 连缩略图都无法解码

    ⚠️ 严禁由"只有缩略图"反推"用户没点开过"——实测证伪：点开了、高清也下载了，
    但那份高清是 wxgf 编码解不开，于是仍然只能退回缩略图。原因只看上面两个细分计数。
    """
    import json

    if not image_ready():
        missing = []
        if not AES:
            missing.append("pycryptodome 未安装（pip3 install pycryptodome）")
        if not wechat_data_dir():
            missing.append("找不到本机聊天软件的附件目录")
        if not image_keys()[0]:
            missing.append("无法推导图片密钥")
        raise RuntimeError("媒体导出不可用: " + "; ".join(missing))

    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    rows = get_chat_history(username, start_ts, end_ts, limit=limit, resolve_media=True)

    saved = {"image_hd": 0, "image_thumb": 0, "image_none": 0,
             "video": 0, "video_none": 0, "undecodable": 0,
             "thumb_hd_is_wxgf": 0, "thumb_hd_missing": 0}
    manifest = []
    for msg in rows:
        base, _ = split_msg_type(msg.get("local_type"))
        stamp = datetime.fromtimestamp(msg["timestamp"]).strftime("%Y%m%d-%H%M%S")
        who = _safe_name(msg.get("sender"))
        if base == 3:
            path = msg.get("image_path")
            if not path:
                saved["image_none"] += 1
                continue
            data, ext = image_bytes(path)
            if not data:
                saved["undecodable"] += 1
                continue
            name = f"{stamp}_{who}_{msg.get('local_id')}.{ext}"
            (out / name).write_bytes(data)
            reason = ""
            if msg.get("image_quality") == "hd":
                saved["image_hd"] += 1
            else:
                saved["image_thumb"] += 1
                reason = hd_absence_reason(path)
                if reason == "wxgf":
                    saved["thumb_hd_is_wxgf"] += 1
                elif reason == "missing":
                    saved["thumb_hd_missing"] += 1
            manifest.append({"file": name, "time": msg.get("time"), "type": "image",
                             "sender": msg.get("sender"), "quality": msg.get("image_quality"),
                             "hd_absence": reason})
        elif base == 43:
            mp4 = msg.get("video_path")
            if not mp4:
                saved["video_none"] += 1
                continue
            name = f"{stamp}_{who}_{msg.get('local_id')}.mp4"
            (out / name).write_bytes(Path(mp4).read_bytes())
            saved["video"] += 1
            manifest.append({"file": name, "time": msg.get("time"),
                             "type": "video", "sender": msg.get("sender")})

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output_dir": str(out), "saved": saved, "manifest": manifest}


def _parse_day(text, end_of_day=False):
    if not text:
        return None
    d = datetime.strptime(text[:10], "%Y-%m-%d")
    if end_of_day:
        d = d.replace(hour=23, minute=59, second=59)
    return int(d.timestamp())


if __name__ == "__main__":
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(description="v-local-chat 引擎命令行")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ph = sub.add_parser("history", help="导出会话消息（JSON）")
    ph.add_argument("chat", help="联系人/群名 或 username")
    ph.add_argument("--start", help="开始日期 YYYY-MM-DD")
    ph.add_argument("--end", help="结束日期 YYYY-MM-DD")
    ph.add_argument("--limit", type=int, default=None, help="条数上限，默认不限")

    pm = sub.add_parser("export-media", help="把图片/视频解密导出成真实文件")
    pm.add_argument("chat", help="联系人/群名 或 username")
    pm.add_argument("--out", required=True, help="导出目录")
    pm.add_argument("--start", help="开始日期 YYYY-MM-DD")
    pm.add_argument("--end", help="结束日期 YYYY-MM-DD")
    pm.add_argument("--limit", type=int, default=None)

    a = ap.parse_args()
    s, e = _parse_day(getattr(a, "start", None)), _parse_day(getattr(a, "end", None), True)
    if a.cmd == "history":
        print(_json.dumps(get_chat_history(a.chat, s, e, limit=a.limit),
                          ensure_ascii=False, indent=2))
    else:
        r = export_media(a.chat, a.out, s, e, a.limit)
        v = r["saved"]
        out_lines = [f"导出到: {r['output_dir']}",
                     f"高清图 {v['image_hd']} | 缩略图 {v['image_thumb']} | 视频 {v['video']} | "
                     f"无本地文件 {v['image_none'] + v['video_none']} | 完全无法解码 {v['undecodable']}"]
        # 只拿到缩略图时，说清是哪种原因——否则调用方会瞎猜成"你没点开过"（实测该推断不成立）
        if v.get("thumb_hd_is_wxgf"):
            out_lines.append(f"  其中 {v['thumb_hd_is_wxgf']} 张的高清已下载、但是 wxgf 私有编码，"
                             f"本地无法解码（点开也没用，这是软件的编码机制）")
        if v.get("thumb_hd_missing"):
            out_lines.append(f"  其中 {v['thumb_hd_missing']} 张本地没有高清文件"
                             f"（在软件里点开看一下、稍等片刻后重跑本命令可能补上）")
        out_lines.append("提示：要“看”图，用读图工具直接读导出目录里的 .jpg 文件。")
        print("\n".join(out_lines))
