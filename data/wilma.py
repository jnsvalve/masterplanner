"""
wilma.py – Fetches school schedule from Wilma (inschool.fi).

Two-step auth: log in with form credentials to get a session, then hit
the token endpoint to generate a persistent export token. The export token
URL works without any session cookie, so it's cached to disk and reused on
every subsequent fetch. Re-login only happens when the token has expired.

Config:
  wilma:
    username: "firstname.lastname@school.fi"
    password: "your-password"
    base_url: "https://espoo.inschool.fi"
    ical_url: "https://espoo.inschool.fi/!TOKEN/preferences/token?tag=schedule/export/students/ID/Wilma.ics"
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import recurring_ical_events
import requests
from icalendar import Calendar

CACHE_FILE = Path("cache/wilma.json")
TOKEN_FILE = Path("cache/wilma_token.json")  # stores the sessionless export URL
DEFAULT_BASE = "https://espoo.inschool.fi"
WINDOW_DAYS  = 14


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


def _load_token_url() -> str:
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8")).get("url", "")
    except Exception:
        return ""


def _save_token_url(url: str):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"url": url}), encoding="utf-8")


# ── Auth ──────────────────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers["user-agent"] = "Mozilla/5.0"
    return s


def _login_and_get_export_url(base_url: str, username: str, password: str,
                               token_endpoint: str) -> str:
    """Logs in, hits the token endpoint, returns a sessionless iCal download URL."""
    s = _make_session()

    # Step 1: get Wilma2LoginID from /token (requires loading the login page first)
    try:
        s.get(f"{base_url}/", timeout=15)
        login_id = s.get(f"{base_url}/token", timeout=15).json()["Wilma2LoginID"]
    except (requests.RequestException, KeyError, ValueError) as e:
        raise DataFetchError(f"Wilma: could not fetch login token: {e}") from e

    # Step 2: POST login form
    try:
        resp = s.post(
            f"{base_url}/login",
            data={"Login": username, "Password": password, "SESSIONID": login_id},
            headers={"Referer": f"{base_url}/", "Origin": base_url},
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise DataFetchError(f"Wilma: login POST failed: {e}") from e

    if "/login" in resp.url:
        raise DataFetchError("Wilma: login failed — check username/password")

    # Step 3: hit the token endpoint with the session to get the export token
    try:
        tok_resp = s.get(token_endpoint, timeout=15)
        tok_resp.raise_for_status()
        export_token = tok_resp.json()["Token"]
    except (requests.RequestException, KeyError, ValueError) as e:
        raise DataFetchError(f"Wilma: could not get export token: {e}") from e

    # Step 4: construct the sessionless download URL from the tag param
    tag = parse_qs(urlparse(token_endpoint).query).get("tag", [""])[0]
    if not tag:
        raise DataFetchError("Wilma: 'tag' param missing from ical_url")

    return f"{base_url}/{tag}?token={export_token}"


# ── iCal fetch and parse ──────────────────────────────────────────────────────

def _fetch_ical(url: str) -> str:
    """Fetches the iCal file from a sessionless export URL."""
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code in (401, 403, 404):
            raise DataFetchError("_retoken")   # token expired — signal re-login
        resp.raise_for_status()
        if not resp.text.strip().startswith("BEGIN:VCALENDAR"):
            raise DataFetchError("_retoken")   # got HTML (login page) instead of iCal
        return resp.text
    except DataFetchError:
        raise
    except requests.RequestException as e:
        raise DataFetchError(f"Wilma: iCal fetch failed: {e}") from e


def _parse_ical(ical_text: str, today: date, end: date) -> list[dict]:
    try:
        cal = Calendar.from_ical(ical_text)
    except Exception as e:
        raise DataFetchError(f"Wilma: iCal parse failed: {e}") from e

    try:
        # recurring_ical_events needs datetime (not date) bounds; use local midnight
        # with timezone so it aligns with the Helsinki-zoned events in the feed
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Europe/Helsinki")
        dt_start = datetime(today.year, today.month, today.day, 0,  0,  0,  tzinfo=tz)
        dt_end   = datetime(end.year,   end.month,   end.day,   23, 59, 59, tzinfo=tz)
        occurrences = recurring_ical_events.of(cal).between(dt_start, dt_end)
    except Exception as e:
        raise DataFetchError(f"Wilma: recurring event expansion failed: {e}") from e

    events = []
    for component in occurrences:
        dtstart = component.get("DTSTART")
        if dtstart is None:
            continue

        dt = dtstart.dt
        if isinstance(dt, datetime):
            ev_date = dt.astimezone().date()
            ev_time = dt.astimezone().strftime("%H:%M")
        else:
            ev_date = dt
            ev_time = None

        title = str(component.get("SUMMARY", "")).strip()
        # Use time range as description — the raw Wilma description contains
        # redundant full names already abbreviated in the title
        desc = ""
        if ev_time:
            dtend = component.get("DTEND")
            if dtend and isinstance(dtend.dt, datetime):
                end_time = dtend.dt.astimezone().strftime("%H:%M")
                desc = f"{ev_time}–{end_time}"

        events.append({
            "title":       title,
            "description": desc,
            "date":        ev_date.isoformat(),
            "time":        ev_time,
            "all_day":     not isinstance(dtstart.dt, datetime),
            "calendar":    "Koulu",
        })

    events.sort(key=lambda e: (e["date"], e["time"] or ""))
    return events


# ── Public interface ──────────────────────────────────────────────────────────

def fetch(config: dict, use_cache: bool = True) -> dict:
    ttl = config.get("cache", {}).get("wilma_ttl_minutes",
          config.get("cache", {}).get("ttl_minutes", 1440))

    if use_cache and _cache_is_fresh(ttl):
        cached = _load_cache()
        if cached:
            return cached

    wilma_cfg      = config.get("wilma", {})
    username       = wilma_cfg.get("username", "")
    password       = wilma_cfg.get("password", "")
    base_url       = wilma_cfg.get("base_url", DEFAULT_BASE).rstrip("/")
    token_endpoint = wilma_cfg.get("ical_url", "")

    if not username or not password:
        raise DataFetchError("Wilma: credentials missing (wilma.username, wilma.password)")
    if not token_endpoint:
        raise DataFetchError("Wilma: ical_url missing from config")

    today = date.today()
    end   = today + timedelta(days=WINDOW_DAYS)

    # Try cached export URL first (no login needed)
    ical_text  = None
    export_url = _load_token_url()
    if export_url:
        try:
            ical_text = _fetch_ical(export_url)
        except DataFetchError as e:
            if "_retoken" not in str(e):
                raise

    # Login and generate a fresh export token if needed
    if ical_text is None:
        export_url = _login_and_get_export_url(base_url, username, password, token_endpoint)
        _save_token_url(export_url)
        ical_text  = _fetch_ical(export_url)

    events = _parse_ical(ical_text, today, end)
    data   = {
        "events":     events,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_cache(data)
    return data
