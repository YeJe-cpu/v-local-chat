# v-local-chat

**Turn your local chat history into readable daily digests, highlights, and material — with one click.** Runs fully locally: no upload, no network, no third-party API.

> For macOS WeChat (verified on the current version). A **local chat-data analysis panel + Claude Code Skill**:
> it doesn't do the AI analysis for you — it reorganizes chats **you own** into clean, relationship-anchored
> structured text / HTML that you then feed to any LLM (Claude / Codex / anything) to summarize.

中文版 → [README.md](README.md)

## Why

Group chats pile up hundreds of messages a day; keeping a context backup of a DM or reviewing a
relationship has no handy tool. Most similar tools only support old WeChat versions, or bake "analysis"
into the tool. This project does one thing well: **deterministically reorganize raw messages into a form
both humans and LLMs can read.**

## Highlights (original work in this project)

- **Deterministic speaker attribution** — each message's sender comes from database facts, not LLM
  guessing; non-friend group members are resolved to real names via a **group roster** built from
  quote-reply pairings.
- **Response-relationship recovery** — quote-replies (who replied to which line) and @mentions are
  extracted as structured fields, telling you exactly "who is responding to whom" — the thing LLMs most
  often get wrong becomes hard data.
- **Emoji normalization** — WeChat built-in emoji `[破涕为笑]` → 😂 (whitelist; never corrupts brackets
  in marketing copy / prompt templates).
- **Image color fix** — fixes the AES block-padding bug in macOS WeChat image `.dat` files (which caused
  large JPEG color shift); decodable images display and export with correct color. Tencent's private
  wxgf encoding cannot be decoded locally (a hard limit, honestly labeled).
- **Data / presentation separation** — the extraction layer emits clean text + structured fields; the
  panel renders replies as subtle quote lines, while LLM material carries text anchors separately.

## Scenarios

| Scenario | Input | Output |
|----------|-------|--------|
| **Group highlights** | group + date range | aggregated by speaker, ranked by contribution; highlights + link list |
| **Material extract** | group/contact + date range | images/links/files grouped by time, exportable |
| **Info export** | contact | conversation timeline + context backup, Markdown export |

## Install & use

The panel reads a locally **already-decrypted** chat database. Key acquisition and decryption are a
one-time setup; the technique is described in
[`docs/key-extraction-technique.md`](docs/key-extraction-technique.md) (this repo does **not** ship a
turnkey grabber — prepare your own, on **data you are authorized to access**).

```bash
pip install flask pycryptodome zstandard
python3 panel/server.py        # open http://127.0.0.1:5678
# optional: VLC_PORT to change port, VLC_DECRYPTED_DIR for the decrypted DB dir
```

## Limits & disclaimer

- **macOS only**; key extraction needs frida.
- **Time-sensitive**: WeChat updates may require re-setup or break the method. **Works on the current
  version; no guarantee after future updates.**
- Some images use Tencent's private wxgf encoding and cannot be decoded locally.
- Use only on data **you own and are authorized to access**. Follow local law and platform terms. The
  author is not responsible for misuse.

## License & credits

- License: non-commercial, source-available — see [`LICENSE`](LICENSE) (view / learn / personal use;
  **no commercial use or resale**).
- Credits & sources: see [`NOTICES.md`](NOTICES.md). The key/decryption technique references several
  public projects; this repo contains none of their source code.
