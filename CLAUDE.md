# E-ink Dashboard – CLAUDE.md

## Project overview

Home dashboard for Waveshare 7.5" e-ink display (800×480px, grayscale).
Development on macOS or Windows (PNG simulation), deployed on Raspberry Pi Zero 2 W.

Primary hardware: Waveshare 7.5" e-Paper HAT V2 + Raspberry Pi Zero 2 W (wired, wall-mounted).

Alternative deployment: Waveshare ESP32-S3-Touch-LCD-7 (800×480 color LCD) via ESPHome + Home Assistant.
See README for two options: image server (AppDaemon runs render.py, ESPHome fetches PNG) or LVGL (HA sensors → ESPHome native widgets).

## Running

### macOS
```bash
cd /Users/sihvojuh/Personal/Projects/eInk
source venv/bin/activate

python main.py --preview           # full run, open PNG
python main.py --no-cache --preview
python main.py --only hsl --no-cache   # test single module
```

### Windows
```bash
# First-time setup
python -m venv venv
venv/Scripts/pip install -r requirements.txt
cp config.example.yaml config.yaml   # then edit config.yaml with your credentials

# Running (in bash/Git Bash terminal)
venv/Scripts/python main.py --preview           # full run, opens PNG in default viewer
venv/Scripts/python main.py --no-cache --preview
venv/Scripts/python main.py --only weather --no-cache   # test single module

# For clean log output (suppresses Unicode encoding noise in cp1252 terminals)
PYTHONIOENCODING=utf-8 venv/Scripts/python main.py --no-cache
```

Note: Windows terminals using cp1252 encoding will show `--- Logging error ---` noise for
the ✓/✗ log characters — this is cosmetic only. The PNG is still generated correctly.
`PYTHONIOENCODING=utf-8` suppresses it.

### Raspberry Pi (deploy)
```bash
./sync.sh   # rsync to pi@eink.local:~/eInk/ (excludes venv, cache, output, .git)
ssh pi@eink.local "cd ~/eInk && venv/bin/python main.py"
ssh pi@eink.local "cd ~/eInk && venv/bin/python main.py --no-cache"
```

Python: 3.13, venv at `venv/`

## File structure

```
main.py              – entry point, CLI, module orchestration
render.py            – Pillow renderer, 800×480 grayscale (mode L)
sync.sh              – rsync helper script (Mac → Pi)
config.yaml          – credentials + settings (NOT committed)
config.example.yaml  – template

fonts/
  Inter-Regular.ttf  – preferred font (both Mac and Pi)
  Inter-Bold.ttf

data/
  weather.py         – Open-Meteo REST (no auth)
  calendar.py        – iCal / Google Calendar (secret URL token)
  electricity.py     – Caruna via pycaruna (username/password)
  waste.py           – manual schedule from config
  evaka.py           – Espoo eVaka weak-login session API
  hsl.py             – HSL Digitransit v2 GraphQL
  news.py            – YLE RSS feed (no auth)

display/
  simulator.py       – saves output/dashboard.png (macOS)
  epaper.py          – Waveshare 7.5" V2 driver (Raspi only)
```

## Layout (mixed rows + full-width news strip)

`layout.grid` is row-based in `config.yaml`. Each row may contain:
- 3 modules (classic three-column row)
- 1 module (full-width row)

The news strip is fixed at the bottom (always full-width).

```
┌──────────────────┬──────────────────┬──────────────────┐
│ [module, module, module]  (3-column row)               │
├─────────────────────────────────────────────────────────┤
│ [module]                  (full-width row)             │
├─────────────────────────────────────────────────────────┤  NEWS_H = 140px
│ UUTISET (full width, 2 items stacked)                  │
└─────────────────────────────────────────────────────────┘
```

Default layout (matches original hardcoded order):
```yaml
layout:
  grid:
    - [evaka, calendar, weather]
    - [electricity, hsl, waste]
```

Calendar-focused full-width modules:
- `calendar_full_upcoming` (dense upcoming list)
- `calendar_full_week` (compact 7-day view)

Available modules for grid cells: `weather`, `calendar`, `calendar_full_upcoming`, `calendar_full_week`, `electricity`, `waste`, `hsl`, `evaka`, `wilma`, `wilma_letter`, `evaka_letter`, `keep`
Use `~` (null) to leave a cell blank. An unknown or unconfigured-but-required module shows a placeholder.

### Configurable layout internals (render.py)

- `DEFAULT_LAYOUT` constant defines the fallback grid
- `_DRAW_FUNCS` dict maps module name → draw function (e.g. `"evaka"` → `_draw_daycare`)
- `render(data, layout, news, width, height)` iterates rows and dispatches cell draw functions
- 3-cell rows get vertical dividers, 1-cell rows render full-width
- `_draw_placeholder()` is called for blank/unknown cells
- `main.py` reads `layout.grid`, validates rows as length 1 or 3, collects unique module names, and only fetches those

## Rendering conventions (render.py)

- **Fonts**: Inter (fonts/ dir) → Futura (macOS) → Helvetica (macOS) → DejaVu (Linux)
- **Colors**: inverted — BG=0 (black), FG=255 (white), GRAY=255, DIVIDER=80
- FONT_HUGE=52bold, FONT_VLARGE=42bold, FONT_LARGE=28bold, FONT_MED=18bold, FONT_SMALL=17bold,
  FONT_TINY=14bold, FONT_LABEL=12, FONT_HEADER=22bold
- Section labels: FONT_LABEL gray at top of each cell (`_label()` helper)
- No dividers between list items (removed for cleaner look)
- Weather icons: geometric Pillow drawing (no emoji/unicode symbols)
- HSL mode icons: removed — lines ("165 -> U") are self-explanatory
- Arrows: use `->` not `→` (unicode arrows unreliable across fonts)
- Text wrapping: `_wrap_text(draw, text, font, max_width)` — pixel-based, not char-based
- Badge: `_badge(draw, x, y, text)` — black pill with white text (used in HSL)
- `render()` signature: `render(data, layout, news, width, height)` — data is a dict keyed by module name

## Data module patterns

Each module follows the same pattern:
```python
def fetch(config: dict, use_cache: bool = True) -> dict:
    ttl = config.get("cache", {}).get("MODULE_ttl_minutes", DEFAULT)
    if use_cache and _cache_is_fresh(ttl): return _load_cache()
    # ... fetch from API ...
    _save_cache(data)
    return data
```

Stale cache fallback on network errors: `data["_stale"] = True`

## Cache TTLs (config.yaml)

```yaml
cache:
  ttl_minutes: 55           # weather, calendar (default)
  hsl_ttl_minutes: 10       # real-time transit
  hsl_active_hours: [6, 22] # no HSL fetches outside these hours
  evaka_ttl_minutes: 1440   # daycare: once per day
  electricity_ttl_minutes: 720  # electricity: twice per day
```

Cron runs `main.py` every 10 minutes + `@reboot`; each module decides independently.

## Electricity module specifics (data/electricity.py)

- Fetches from Caruna Plus API via pycaruna (username/password in config)
- Uses `TimeSpan.MONTHLY` to get day-by-day data; data may lag 1–2 days behind today
- Returns last 7 days of consumption as `daily_kwh`: `[{"date": "YYYY-MM-DD", "kwh": float}, ...]`
- Cross-month fetch: if 7-day window spans two months, makes a second API call for the prior month
- `yesterday_kwh` / `yesterday_date` are derived from the most recent entry in `daily_kwh`
- Rendered with FONT_VLARGE (42px) value + bar chart (7 bars anchored to cell bottom)

## HSL module specifics (data/hsl.py)

- Digitransit v2 GraphQL: `https://api.digitransit.fi/routing/v2/hsl/gtfs/v1`
- Variable types: `CoordinateValue!` for lat/lon, `OffsetDateTime!` for time
- startTime/endTime from API are **epoch milliseconds** (not ISO strings)
- Mode-specific walk time filtering:
  - BUS/TRAM/FERRY: `min_walk_bus` (default 3 min)
  - RAIL/SUBWAY: `min_walk_rail` (default 15 min)
- Each connection returns: departure, arrival, minutes_until, lines ("165 -> U"),
  walk_minutes, first_mode, first_stop, first_depart
- Outside `hsl_active_hours`: returns stale cache or empty result (no API call)
- Render: first connection shown with FONT_MED + badge, rest compact

## eVaka module specifics (data/evaka.py)

- POST `/api/citizen/auth/weak-login` → session cookie saved to `cache/evaka_session.json`
- GET `/api/citizen/calendar-events?start=...&end=...`
- Auto re-login on 401/403
- Events have separate `title` and `description` fields
- 14-day window
- `_apply_cutoff()`: called on both fresh and cached data — hides today's events
  after `_AFTERNOON_CUTOFF_HOUR = 18`

## Calendar module specifics (data/calendar.py)

- iCal links from config (no OAuth needed)
- 30-day window
- Date uses local timezone (`.astimezone().date()`), not UTC
- Filters out timed events that have already ended (uses DTEND)
- Returns up to 8 events, renderer shows ~3 that fit in the cell

## News module specifics (data/news.py)

- YLE RSS feed: `https://feeds.yle.fi/uutiset/v1/majorHeadlines/YLE_UUTISET.rss`
- URL configurable via `news.url` in config.yaml
- Returns top 3 items with title + description
- Rendered full-width at bottom: 2 items stacked, description in FONT_LABEL

## Known issues / TODO

- [x] Caruna returning null kWh — resolved; now fetches 7-day daily history (`daily_kwh` list)
- [x] Layout hardcoded — resolved; now configurable via `layout.grid` in config.yaml
- [ ] Consider adding forecast strip to weather cell
- [ ] Pi Zero 2 W with headers (WH version) would avoid needing to solder headers

## Git

Branch: main
Repo: https://github.com/jaatuli/masterplan
Upstream: https://github.com/JuhaniS/eInk
