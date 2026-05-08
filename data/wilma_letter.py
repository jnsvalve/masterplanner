"""
wilma_letter.py – Fetches the latest weekly letter (viikkokirje) from Wilma
and uses Claude Haiku to extract 4-6 key bullet points.

Auth: same form-based Wilma login as wilma.py, session cookie cached to
cache/wilma_letter_session.json. Re-login on 401/403.

The Anthropic API key is read from config (claude.api_key) or falls back to
the ANTHROPIC_API_KEY environment variable.

Config:
  wilma:
    username: "..."
    password: "..."
    base_url: "https://espoo.inschool.fi"
  claude:
    api_key: "sk-ant-..."   # or set ANTHROPIC_API_KEY env var
"""

import json
import os
from datetime import datetime
from pathlib import Path

import anthropic
import requests
from bs4 import BeautifulSoup

CACHE_FILE   = Path("cache/wilma_letter.json")
SESSION_FILE = Path("cache/wilma_letter_session.json")
DEFAULT_BASE = "https://espoo.inschool.fi"
DEFAULT_TTL  = 240   # 4 hours — new letter arrives once a week


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


def _save_session(cookies: dict, role_prefix: str):
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(
        json.dumps({"cookies": cookies, "role_prefix": role_prefix}), encoding="utf-8"
    )


def _make_session(cookies: dict | None = None) -> requests.Session:
    s = requests.Session()
    s.headers["user-agent"] = "Mozilla/5.0"
    if cookies:
        for name, value in cookies.items():
            s.cookies.set(name, value)
    return s


def _login(base_url: str, username: str, password: str) -> tuple[requests.Session, str]:
    from urllib.parse import urlparse
    s = _make_session()
    try:
        s.get(f"{base_url}/", timeout=15)
        login_id = s.get(f"{base_url}/token", timeout=15).json()["Wilma2LoginID"]
        resp = s.post(
            f"{base_url}/login",
            data={"Login": username, "Password": password, "SESSIONID": login_id},
            headers={"Referer": f"{base_url}/", "Origin": base_url},
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except (requests.RequestException, KeyError, ValueError) as e:
        raise DataFetchError(f"Wilma letter: login failed: {e}") from e

    if "/login" in resp.url:
        raise DataFetchError("Wilma letter: login failed — check username/password")

    role_prefix = urlparse(resp.url).path.rstrip("/")
    if not role_prefix.startswith("/!"):
        raise DataFetchError(f"Wilma letter: unexpected post-login redirect: {resp.url}")

    _save_session(dict(s.cookies), role_prefix)
    return s, role_prefix


# ── Wilma messages ────────────────────────────────────────────────────────────

def _fetch_message_list(s: requests.Session, base_url: str, role_prefix: str) -> list[dict]:
    r = s.get(f"{base_url}{role_prefix}/messages/list", timeout=15)
    if r.status_code in (401, 403):
        raise DataFetchError("_relogin")
    r.raise_for_status()
    return r.json().get("Messages", [])


def _find_latest_viikkokirje(messages: list[dict]) -> dict | None:
    """Returns the most recent message whose subject starts with 'Viikko'."""
    for msg in messages:   # list is newest-first
        if msg.get("Subject", "").strip().startswith("Viikko"):
            return msg
    return None


def _fetch_message_body(s: requests.Session, base_url: str, role_prefix: str, msg_id: int) -> str:
    r = s.get(f"{base_url}{role_prefix}/messages/{msg_id}", timeout=15)
    if r.status_code in (401, 403):
        raise DataFetchError("_relogin")
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    div  = soup.find("div", class_="ckeditor")
    if not div:
        raise DataFetchError("Wilma letter: message body not found in HTML")
    # Convert to plain text, preserving paragraph breaks
    return "\n\n".join(p.get_text(" ", strip=True) for p in div.find_all("p") if p.get_text(strip=True))


# ── Claude extraction ─────────────────────────────────────────────────────────

_PROMPT = """\
Lue seuraava opettajan viikkokirje ja poimi 4–6 tärkeintä muistettavaa asiaa.

Säännöt:
- Mainitse viikonpäivä lyhenteenä (Ma/Ti/Ke/To/Pe) jos mainitaan
- Mainitse kellonaika jos mainitaan
- Pidä kukin bullet max 7 sanaa
- Listaa vain asiat jotka vaativat toimintaa tai ovat tärkeitä tapahtumia
- Älä toista samaa asiaa kahdesti
- Vastaa VAIN listalla, yksi asia per rivi, ei luettelomerkkejä

Viikkokirje:
{body}"""


def _extract_bullets(body: str, api_key: str) -> list[str]:
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[{"role": "user", "content": _PROMPT.format(body=body)}],
    )
    lines = resp.content[0].text.strip().splitlines()
    return [l.strip("–-•*0123456789). ").strip() for l in lines if l.strip()]


# ── Public interface ──────────────────────────────────────────────────────────

def fetch(config: dict, use_cache: bool = True) -> dict:
    ttl = config.get("cache", {}).get("wilma_letter_ttl_minutes", DEFAULT_TTL)

    if use_cache and _cache_is_fresh(ttl):
        cached = _load_cache()
        if cached:
            return cached

    wilma_cfg = config.get("wilma", {})
    username  = wilma_cfg.get("username", "")
    password  = wilma_cfg.get("password", "")
    base_url  = wilma_cfg.get("base_url", DEFAULT_BASE).rstrip("/")
    api_key   = config.get("claude", {}).get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")

    if not username or not password:
        raise DataFetchError("Wilma letter: credentials missing (wilma.username / wilma.password)")
    if not api_key:
        raise DataFetchError("Wilma letter: Anthropic API key missing (claude.api_key or ANTHROPIC_API_KEY env var)")

    def _do_fetch(s: requests.Session, role_prefix: str) -> dict:
        messages = _fetch_message_list(s, base_url, role_prefix)
        latest   = _find_latest_viikkokirje(messages)
        if not latest:
            return {"bullets": [], "subject": "", "fetched_at": datetime.now().isoformat(timespec="seconds")}
        body    = _fetch_message_body(s, base_url, role_prefix, latest["Id"])
        bullets = _extract_bullets(body, api_key)
        return {
            "bullets":    bullets,
            "subject":    latest["Subject"],
            "sender":     latest.get("Sender", ""),
            "timestamp":  latest.get("TimeStamp", ""),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }

    # Try saved session first
    result = None
    saved  = _load_session()
    if saved.get("cookies") and saved.get("role_prefix"):
        s = _make_session(saved["cookies"])
        try:
            result = _do_fetch(s, saved["role_prefix"])
        except DataFetchError as e:
            if "_relogin" not in str(e):
                raise

    if result is None:
        s, role_prefix = _login(base_url, username, password)
        result = _do_fetch(s, role_prefix)

    _save_cache(result)
    return result
