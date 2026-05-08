"""
evaka_letter.py – Fetches recent group messages from eVaka and uses Claude Haiku
to extract key bullet points.

Auth: same weak-login as evaka.py, session cookie shared via
cache/evaka_session.json. Re-login on 401/403.

Config:
  evaka:
    username: "..."
    password: "..."
    base_url: "https://espoonvarhaiskasvatus.fi"
    letter_sender: "Kengurut"   # substring match on sender name
    letter_count: 2             # how many recent messages to summarise
  claude:
    api_key: "sk-ant-..."       # or set ANTHROPIC_API_KEY env var
"""

import json
import os
from datetime import datetime
from pathlib import Path

import anthropic
import requests

CACHE_FILE   = Path("cache/evaka_letter.json")
SESSION_FILE = Path("cache/evaka_session.json")   # shared with evaka.py
BASE_URL     = "https://espoonvarhaiskasvatus.fi"
DEFAULT_TTL  = 240    # 4 hours
DEFAULT_COUNT = 2     # messages to summarise


class DataFetchError(Exception):
    pass


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_is_fresh(ttl_minutes: int) -> bool:
    if not CACHE_FILE.exists():
        return False
    age = datetime.now().timestamp() - CACHE_FILE.stat().st_mtime
    return age < ttl_minutes * 60


def _load_cache() -> dict | None:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cache(data: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Session ───────────────────────────────────────────────────────────────────

def _load_session() -> dict:
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_session(cookies: dict):
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")


def _make_session(cookies: dict | None = None) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "accept":       "application/json, text/plain, */*",
        "user-agent":   "Mozilla/5.0",
        "x-evaka-csrf": "1",
    })
    if cookies:
        for name, value in cookies.items():
            s.cookies.set(name, value)
    return s


def _login(base_url: str, username: str, password: str) -> requests.Session:
    s = _make_session()
    try:
        resp = s.post(
            f"{base_url}/api/citizen/auth/weak-login",
            json={"username": username, "password": password},
            headers={"content-type": "application/json",
                     "referer": f"{base_url}/login/form?next=%2F"},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise DataFetchError(f"eVaka letter: login failed: {e}") from e
    _save_session(dict(s.cookies))
    return s


# ── Fetch messages ────────────────────────────────────────────────────────────

def _fetch_group_messages(s: requests.Session, base_url: str,
                          sender_filter: str, count: int) -> list[dict]:
    """
    Pages through received messages and returns the `count` most recent
    threads whose sender name contains `sender_filter` (case-insensitive).
    """
    matched = []
    page = 1
    filter_lower = sender_filter.lower()

    while len(matched) < count:
        r = s.get(
            f"{base_url}/api/citizen/messages/received",
            params={"page": page, "pageSize": 50},
            timeout=15,
        )
        if r.status_code in (401, 403):
            raise DataFetchError("_relogin")
        r.raise_for_status()

        data = r.json()
        threads = data.get("data", [])
        if not threads:
            break

        for thread in threads:
            for msg in thread.get("messages", []):
                name = msg.get("sender", {}).get("name", "")
                if filter_lower in name.lower():
                    matched.append({
                        "title":   thread.get("title", ""),
                        "sender":  name,
                        "sent_at": msg.get("sentAt", ""),
                        "content": msg.get("content", "").strip(),
                    })
                    if len(matched) >= count:
                        break
            if len(matched) >= count:
                break

        if page >= data.get("pages", 1):
            break
        page += 1

    # newest first (API already returns newest-first, but be explicit)
    matched.sort(key=lambda m: m["sent_at"], reverse=True)
    return matched[:count]


# ── Claude extraction ─────────────────────────────────────────────────────────

_PROMPT = """\
Lue seuraavat viestit päiväkodin henkilökunnalta ja poimi 4–6 tärkeintä muistettavaa asiaa.

Säännöt:
- Mainitse viikonpäivä lyhenteenä (Ma/Ti/Ke/To/Pe) ja päivämäärä jos mainitaan
- Mainitse kellonaika jos mainitaan
- Pidä kukin bullet max 8 sanaa
- Listaa vain asiat jotka vaativat toimintaa tai ovat tärkeitä tapahtumia
- Älä toista samaa asiaa kahdesti
- Vastaa VAIN listalla, yksi asia per rivi, ei luettelomerkkejä

Viestit:
{messages}"""


def _format_messages(messages: list[dict]) -> str:
    parts = []
    for m in messages:
        try:
            dt = datetime.fromisoformat(m["sent_at"])
            date_str = dt.strftime("%-d.%-m.%Y")
        except Exception:
            date_str = m["sent_at"][:10]
        parts.append(f"[{date_str}] {m['title']}\n{m['content']}")
    return "\n\n---\n\n".join(parts)


def _extract_bullets(messages: list[dict], api_key: str) -> list[str]:
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[{"role": "user", "content": _PROMPT.format(
            messages=_format_messages(messages)
        )}],
    )
    lines = resp.content[0].text.strip().splitlines()
    return [l.strip("–-•*0123456789). ").strip() for l in lines if l.strip()]


# ── Public interface ──────────────────────────────────────────────────────────

def fetch(config: dict, use_cache: bool = True) -> dict:
    ttl = config.get("cache", {}).get("evaka_letter_ttl_minutes", DEFAULT_TTL)

    if use_cache and _cache_is_fresh(ttl):
        cached = _load_cache()
        if cached:
            return cached

    evaka_cfg     = config.get("evaka", {})
    username      = evaka_cfg.get("username", "")
    password      = evaka_cfg.get("password", "")
    base_url      = evaka_cfg.get("base_url", BASE_URL).rstrip("/")
    sender_filter = evaka_cfg.get("letter_sender", "")
    count         = int(evaka_cfg.get("letter_count", DEFAULT_COUNT))
    api_key       = config.get("claude", {}).get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")

    if not username or not password:
        raise DataFetchError("eVaka letter: credentials missing (evaka.username / evaka.password)")
    if not sender_filter:
        raise DataFetchError("eVaka letter: evaka.letter_sender not set in config")
    if not api_key:
        raise DataFetchError("eVaka letter: Anthropic API key missing (claude.api_key or ANTHROPIC_API_KEY env var)")

    def _do_fetch(s: requests.Session) -> dict:
        messages = _fetch_group_messages(s, base_url, sender_filter, count)
        if not messages:
            return {
                "bullets":   [],
                "sources":   [],
                "newest_at": "",
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
        bullets = _extract_bullets(messages, api_key)
        return {
            "bullets":    bullets,
            "sources":    [m["title"] for m in messages],
            "newest_at":  messages[0]["sent_at"],
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }

    result = None
    saved  = _load_session()
    if saved:
        s = _make_session(saved)
        try:
            result = _do_fetch(s)
        except DataFetchError as e:
            if "_relogin" not in str(e):
                raise

    if result is None:
        s      = _login(base_url, username, password)
        result = _do_fetch(s)

    _save_cache(result)
    return result
