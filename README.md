# E-ink Dashboard

A home dashboard for a Waveshare 7.5" e-ink display (800×480), running on a Raspberry Pi. Displays weather, calendar events, news headlines, school and daycare schedules, public transit departures, and AI-extracted weekly school letters.

Developed on macOS and Windows (PNG simulation), deployed on a Raspberry Pi Zero 2 W.

## Layout

The grid is row-based and fully configurable in `config.yaml`.
Each row can be either 3 columns (classic dashboard row) or 1 full-width module row.
The news strip at the bottom is always full-width.

```
┌──────────────────┬──────────────────┬──────────────────┐
│  [module, module, module]          (3-column row)      │
├─────────────────────────────────────────────────────────┤
│  [module]                          (full-width row)     │
├─────────────────────────────────────────────────────────┤
│  UUTISET  (full width)                                  │
└─────────────────────────────────────────────────────────┘
```

Use `~` (null) to leave a cell blank. An unconfigured module shows a placeholder. Row height adjusts automatically — a 2-row grid gives 170 px per row, a 3-row grid gives 113 px.

Extra calendar-focused modules:
- `calendar_full_upcoming`: dense upcoming list for full-width calendar views
- `calendar_full_week`: compact 7-day weekly view for full-width calendar views

### Single layout

```yaml
layout:
  grid:
    - [wilma_letter, calendar, weather]
    - [wilma,        evaka_letter, hsl]
```

### Named profiles

Define multiple layouts and switch between them with `--layout <name>`:

```yaml
layout:
  active: school                  # used when --layout is omitted
  profiles:
    school:
      grid:
        - [wilma_letter, calendar, weather]
        - [wilma,        evaka_letter, hsl]
    weekend:
      grid:
        - [evaka_letter, calendar, weather]
        - [evaka,        ~,        hsl    ]
    calendar_upcoming:
      grid:
        - [calendar_full_upcoming]
    calendar_week:
      grid:
        - [calendar_full_week]
```

Switch manually or drive it from cron:

```bash
# weekdays — uses layout.active ("school")
python main.py

# weekends — override to weekend profile
python main.py --layout weekend

# cron: automatic weekday/weekend switching
*/10 * * * 1-5  python main.py
*/10 * * * 6,7  python main.py --layout weekend
```

The resolution order is: `--layout` flag → `layout.active` → `layout.grid` → built-in default. Configs without profiles continue to work unchanged.

## Hardware

| Part | Model | Notes |
|---|---|---|
| Display | Waveshare 7.5" e-Paper HAT V2 (800×480) | Black/white |
| Computer | Raspberry Pi Zero 2 W (or any Pi with 40-pin GPIO) | Needs pre-soldered headers |
| Power | 5V micro-USB charger, ≥1A | Standard phone charger works |

> **Important:** The Raspberry Pi Zero 2 W is sold both with and without GPIO headers.
> The Waveshare HAT has a female connector and requires **male pins** on the Pi.
> Make sure to buy the **"with headers" (WH) version**, or solder a 2×20 male header yourself.

## Data sources

| Module | Source | Auth |
|---|---|---|
| `weather` | [Open-Meteo](https://open-meteo.com/) | None |
| `calendar` | Google Calendar iCal | Secret URL token |
| `news` | YLE Uutiset RSS | None |
| `waste` | Manual schedule in config | None |
| `evaka` | Espoo eVaka (`/api/citizen/auth/weak-login`) | Username + password |
| `hsl` | [HSL Digitransit v2 GraphQL](https://portal-api.digitransit.fi/) | API key |
| `wilma` | Wilma school calendar iCal (inschool.fi) | Username + password |
| `wilma_letter` | Wilma messages + Claude Haiku summarisation | Username + password + Anthropic API key |
| `electricity` | Caruna via pycaruna | Username + password |

The `wilma` and `wilma_letter` modules target Finnish schools on the inschool.fi platform. `wilma` replaces or supplements `evaka` once a child moves to primary school. `wilma_letter` fetches the latest weekly teacher letter ("viikkokirje") and uses Claude Haiku to extract 4–6 concise bullet points — upcoming events, reminders, things to bring.

## Development setup

### 1. Clone and create virtualenv

**macOS / Linux**
```bash
git clone <repo>
cd eInk
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Windows**
```powershell
git clone <repo>
cd eInk
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

### 2. Configure

```bash
cp config.example.yaml config.yaml   # macOS/Linux
# or: copy config.example.yaml config.yaml  (Windows)
```

Edit `config.yaml` with your credentials and location. The file contains passwords and API keys — do not commit it.

Key settings:

```yaml
location:
  latitude: 60.1699
  longitude: 24.9384
  name: "Helsinki"

hsl:
  api_key: "your-key-from-portal-api.digitransit.fi"
  to_name: "Pasila"
  to_lat: 60.1985
  to_lon: 24.9323
  min_walk_bus: 3       # minutes to nearest bus stop
  min_walk_rail: 15     # minutes to nearest train station

waste:
  collections:
    - type: "Sekajäte"
      interval_weeks: 2
      next_date: "2026-03-25"

wilma:
  username: "firstname.lastname@school.fi"
  password: "your-password"
  base_url: "https://espoo.inschool.fi"
  ical_url: "https://espoo.inschool.fi/!TOKEN/preferences/token?tag=schedule/export/students/ID/Wilma.ics"

claude:
  api_key: "sk-ant-..."   # required only for wilma_letter
```

### 3. Google Calendar iCal link

In Google Calendar: *Calendar settings → "Private address in iCal format"*. Add the URL to `config.yaml`:

```yaml
calendars:
  - name: "Oma"
    ical_url: "https://calendar.google.com/calendar/ical/.../basic.ics"
```

### 4. HSL API key

Register at [portal-api.digitransit.fi](https://portal-api.digitransit.fi/) and create a subscription for the Routing API.

### 5. Wilma iCal URL

In Wilma: *Preferences → Calendar → Calendar export → copy the .ics link*. The URL contains a token — treat it like a password.

### 6. Anthropic API key (wilma_letter only)

Create a key at [console.anthropic.com](https://console.anthropic.com) and add it to `config.yaml` under `claude.api_key`. The module uses Claude Haiku — a single letter extraction costs a fraction of a cent.

### 7. Run

**macOS / Linux**
```bash
source venv/bin/activate
python main.py --preview
```

**Windows**
```powershell
venv\Scripts\python main.py --preview
```

Common flags:

```bash
# Full run, open preview
python main.py --preview

# Force data refresh (skip cache)
python main.py --no-cache --preview

# Use a named layout profile
python main.py --layout weekend --preview
python main.py --layout calendar_upcoming --preview
python main.py --layout calendar_week --preview

# Test a single module
python main.py --only weather
python main.py --only wilma_letter --no-cache

# Partial-refresh just the cells listed in config.partial_updates (no API calls)
python main.py --partial-only

# Force a true full refresh instead of the default fast refresh
python main.py --full-refresh
```

> **Windows note:** Terminals using the default cp1252 encoding show `--- Logging error ---` noise for the ✓/✗ log characters. This is cosmetic — the PNG is still generated correctly. Set `PYTHONIOENCODING=utf-8` to suppress it.

## Raspberry Pi deployment

### 1. Flash SD card

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/):
- OS: Raspberry Pi OS Lite (64-bit)
- Enable SSH, set username/password, configure WiFi (country: FI)

### 2. Connect display

Attach the Waveshare HAT to the 40-pin GPIO header with the Pi powered off.

### 3. Enable SPI

```bash
ssh -t USER@HOSTNAME "sudo raspi-config nonint do_spi 0"
```

### 4. Install system dependencies

```bash
ssh -t USER@HOSTNAME "sudo apt install -y git python3-venv python3-pip swig liblgpio-dev"
```

### 5. Sync project files

```bash
./sync.sh
```

Or manually:
```bash
rsync -av --exclude venv --exclude cache --exclude output --exclude .git \
  /path/to/eInk/ USER@HOSTNAME:~/eInk/
```

### 6. Set up virtualenv and install Python dependencies

```bash
ssh USER@HOSTNAME "cd ~/eInk && python3 -m venv venv && venv/bin/pip install -r requirements.txt"
```

The e-paper driver is `betterepd7in5` (on PyPI, pulled in by `requirements.txt`). It fixes partial-update corruption present in the stock Waveshare driver and is significantly faster on the Pi Zero 2 W.

### 7. Create required directories

```bash
ssh USER@HOSTNAME "mkdir -p ~/eInk/cache ~/eInk/output"
```

### 8. Copy config

```bash
scp config.yaml USER@HOSTNAME:~/eInk/config.yaml
```

### 9. Test

```bash
ssh USER@HOSTNAME "cd ~/eInk && venv/bin/python main.py --no-cache"
```

### 10. Set up cron

The crontab is checked in at `cron/eink.crontab` and installed via `sync_cron.sh`. Three rhythms run on `flock`-protected mutually-exclusive minute slots so they can't collide on the SPI bus:

| Slot | Command | Purpose |
|---|---|---|
| Minutes 1–9, 11–19, …, 51–59 | `main.py --partial-only` | Partial-refresh cells in `config.partial_updates` (no API calls) |
| Minutes 10, 20, 30, 40, 50 | `main.py` | Fast refresh of the full dashboard |
| Minute 0 (every hour) + `@reboot` | `main.py --full-refresh` | True full refresh — clears ghosting, bootstraps partial baseline |

Edit `cron/eink.crontab` (update the username and path placeholders marked with `# TODO`) then push:

```bash
./sync_cron.sh
```

The script preserves any other crontab entries on the Pi — it only replaces the block between `# >>> eink-managed >>>` markers.

### Sync changes

```bash
./sync.sh        # rsync project files
./sync_cron.sh   # only when cron/eink.crontab changed
```

## Project structure

```
eInk/
├── main.py              # Entry point, CLI args, module orchestration
├── render.py            # Pillow-based image renderer (800×480, grayscale)
├── sync.sh              # rsync project files to Pi
├── sync_cron.sh         # Push cron/eink.crontab to Pi
├── config.yaml          # Your config (not committed)
├── config.example.yaml  # Template
├── cron/
│   └── eink.crontab     # Managed crontab block (installed via sync_cron.sh)
├── data/
│   ├── weather.py       # Open-Meteo
│   ├── calendar.py      # iCal / Google Calendar
│   ├── news.py          # YLE RSS feed
│   ├── electricity.py   # Caruna / pycaruna
│   ├── waste.py         # Manual waste schedule
│   ├── evaka.py         # Espoo daycare (eVaka)
│   ├── hsl.py           # HSL Digitransit transit
│   ├── wilma.py         # Wilma school calendar (iCal)
│   └── wilma_letter.py  # Wilma weekly letter → Claude Haiku bullet points
├── display/
│   ├── simulator.py     # PNG output for local development (macOS + Windows)
│   └── epaper.py        # Waveshare 7.5" V2 driver via betterepd7in5
├── fonts/               # Optional: place Inter-Regular.ttf + Inter-Bold.ttf here
├── cache/               # JSON cache files + cur_display.png partial baseline (auto-generated)
└── output/              # Output PNG (auto-generated, local dev only)
```

## Caching

Each module writes a JSON cache file under `cache/`. TTLs are configurable per module in `config.yaml`. Stale cache is used as a fallback when an API call fails — the dashboard always shows something even when offline.

```yaml
cache:
  ttl_minutes: 55               # weather, calendar
  hsl_ttl_minutes: 10           # real-time transit
  hsl_active_hours: [6, 22]     # no HSL fetches outside these hours
  evaka_ttl_minutes: 1440       # daycare: once per day
  electricity_ttl_minutes: 720  # electricity: twice per day
  wilma_letter_ttl_minutes: 240 # weekly letter: 4 hours
```

## Partial updates

The per-minute cron slot runs `main.py --partial-only`, which re-renders the dashboard from cache only (no API calls) and partial-refreshes the cells listed in config:

```yaml
partial_updates:
  clock: true   # ticks every minute
  hsl: true     # drops connections whose departure has passed
```

Available cells are defined in `render.py` as `PARTIAL_CELLS`. Each entry carries `{region, data_key, filter}` where `filter` is an optional `module:function` that mutates the cell's data before render — the HSL cell uses `data.hsl:drop_past_departures` to remove elapsed connections. New partial-eligible cells need an `x`-aligned region (multiples of 8) and an entry in the registry.

If `partial_updates` is missing or empty, `--partial-only` is a no-op.

## Adding a new module

1. Create `data/mymodule.py` with a `fetch(config, use_cache=True) -> dict` function following the same cache pattern as existing modules.
2. Add a `_draw_mymodule` function in `render.py` and register it in `_DRAW_FUNCS`.
3. Add `"mymodule"` to the `fetch_module` dispatch and `--only` choices in `main.py`.
4. If the module requires credentials, add it to `_requires_config` in `main.py`.
5. Add any config keys to `config.example.yaml`.

Pull requests are welcome — especially for new data sources, display improvements, and support for other school or transit systems.
