# Weather Forecast Announcer — Context

## What this does

Fetches UK inshore waters forecasts from the Met Office, optionally adds land temperature (Channel Islands only, from gov.gg), generates a TTS announcement with Piper, and plays it on ASL3.

GB3GU (node 43172) uses `--region channel-islands` and includes Guernsey temperatures. The script supports all 19 Met Office inshore waters areas and can auto-select a region from GPS coordinates.

Two announcement types, each run by a cron job:

| Type | Cron | Audio file | Content |
|------|------|------------|---------|
| `forecast` | 07:30 daily | `/var/lib/asterisk/sounds/custom/forecast.wav` | 24-hour maritime forecast + today's temperature (Channel Islands only) |
| `outlook` | 19:30 daily | `/var/lib/asterisk/sounds/custom/outlook.wav` | Outlook for following 24 hours + tomorrow's temperature (Channel Islands only) |

## Key files

| Path | Purpose |
|------|---------|
| `/usr/local/bin/weather-forecast.py` | Main script |
| `/etc/cron.d/weather-forecast` | Cron jobs (07:30 forecast, 19:30 outlook) |
| `/usr/local/bin/piper-speak` | Piper TTS wrapper (sets LD_LIBRARY_PATH, ESPEAK_DATA_PATH) |
| `/usr/local/lib/piper/` | Piper shared libs (isolated to avoid clash with system espeak-ng 1.51) |
| `/usr/local/share/piper-voices/en_GB-jenny_dioco-medium.onnx` | Voice model (61 MB, female British English) |
| `/var/log/weather-forecast.log` | Log output from cron runs |
| `tests/test_parsers.py` | Unit tests for parsers and region resolution |
| `tests/fixtures/` | Offline HTML fixtures for tests |

## Region selection

Priority order in `resolve_region_config()`:

1. `--region` CLI flag
2. `--lat` / `--lon` CLI flags (both required)
3. `--gps` (reads fix from gpsd at `127.0.0.1:2947`)
4. `WEATHER_LAT` / `WEATHER_LON` environment variables
5. `WEATHER_REGION` environment variable
6. Default: `channel-islands`

`REGIONS` dict maps region keys to `section_id`, `name`, `title_match`, and `bbox` (for GPS lookup). Smallest matching bounding box wins when coordinates overlap multiple areas.

Utility flags: `--list-regions`, `--list-sections`, `--section` (override section id), `-v` / `--verbose`.

## Data sources

### Met Office — maritime forecast
- URL: `https://weather.metoffice.gov.uk/specialist-forecasts/coast-and-sea/print/inshore-waters-forecast`
- Section: `<section id="inshore-waters-N">` → `<div class="forecast-block">` or `<div class="outlook-block">`
- Region title read from `<h3>` inside the section
- Parse class: `MetOfficeParser` (stdlib HTMLParser, no BS4)
- Section id fallback: `resolve_section_id()` matches on `title_match` if configured id is missing
- Discovery: `discover_sections()` regex-scans the print page for live section ids

### gov.gg — land temperature (Channel Islands only, fail-safe)
- URL: `https://www.gov.gg/weather`
- Enabled when region config has `"temp_source": "gov.gg"` (currently only `channel-islands`)
- Structure: `<div id="weatherToday">` (today) and same div with `class="weatherTomorrow"` (tomorrow)
- Inside: `<span id="wsummary">` for summary, `<span id="wtemp">` ×2 for High/Low
- Parse class: `GovGGParser`
- **Fail-safe**: if fetch fails, raises, or data is incomplete, `fetch_temperature()` returns `None` and the announcement continues without the temperature line — never fatal.

## Audio pipeline

```
announcement text → piper (--output-raw, 22050 Hz signed 16-bit mono)
                  → sox (resample to 8000 Hz mono WAV)
                  → /var/lib/asterisk/sounds/custom/{forecast,outlook}.wav
                  → asterisk -rx "rpt localplay 43172 <path>"
```

Intro uses region title from Met Office: *"Good morning, here is the {region} 24 hour maritime forecast."*

Atomic write: sox writes to `{output}.tmp.wav`, replaced with `os.replace()` only on success.

## Testing

```bash
# Offline unit tests
python3 -m unittest discover -s tests -v

# Test morning announcement
sudo python3 /usr/local/bin/weather-forecast.py --type forecast --region channel-islands

# Test evening announcement
sudo python3 /usr/local/bin/weather-forecast.py --type outlook --region channel-islands

# List regions / live Met Office sections
python3 weather-forecast.py --list-regions
python3 weather-forecast.py --list-sections

# Check last cron run
tail -50 /var/log/weather-forecast.log
```

## Node context

Node 43172 also has hourly time announcements via `/etc/asterisk/local/hellotime.sh` using `rpt cmd 43172 status 12 xxx`. No root crontab; all jobs in `/etc/cron.d/`.

GB3GU cron should pass `--region channel-islands` explicitly (or set `WEATHER_REGION=channel-islands`).

## GitHub repo and web player

Repo: `G1LRO/gb3gu-maritime-forecast`

GitHub Pages site at `docs/index.html` provides a web-based player for the sample WAV files.

| Repo path | Purpose |
|-----------|---------|
| `weather-forecast.py` | Main script (keep in sync with `/usr/local/bin/weather-forecast.py`) |
| `CLAUDE.md` | This context file |
| `tests/test_parsers.py` | Parser and region unit tests |
| `tests/fixtures/` | HTML fixtures for offline testing |
| `samples/forecast-sample.wav` | Latest forecast audio (played by the web player) |
| `samples/outlook-sample.wav` | Latest outlook audio (played by the web player) |
| `docs/index.html` | GitHub Pages web player — references `../samples/*.wav` |

**Important**: WAV samples live in `samples/` — do not put them anywhere else or the web player breaks. After any script change that affects the announcement text, regenerate both WAVs and push all three files (`weather-forecast.py`, `samples/forecast-sample.wav`, `samples/outlook-sample.wav`).

## Existing ASL3 repo reference

Similar project (for reference, not used here): `Saytime-Weather-TimeFormat-ASL3`
