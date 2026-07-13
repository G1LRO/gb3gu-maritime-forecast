# Weather Forecast Announcer — Context

## What this does

Fetches the Channel Islands inshore waters forecast from the Met Office and the Guernsey land temperature from gov.gg, generates a TTS announcement with Piper, and plays it on ASL3 node 43172.

Three announcement types, each run by a cron job:

| Type | Cron | Audio file | Content |
|------|------|------------|---------|
| `forecast` | 07:30 daily | `/var/lib/asterisk/sounds/custom/forecast.wav` | 24-hour maritime forecast + today's temperature |
| `midday` | 12:30 daily | `/var/lib/asterisk/sounds/custom/midday.wav` | Same 24-hour maritime forecast + today's temperature, "Good afternoon" intro |
| `outlook` | 19:30 daily | `/var/lib/asterisk/sounds/custom/outlook.wav` | Outlook for following 24 hours + tomorrow's temperature |

## Key files

| Path | Purpose |
|------|---------|
| `/usr/local/bin/weather-forecast.py` | Main script |
| `/etc/cron.d/weather-forecast` | Cron jobs (07:30 forecast, 12:30 midday, 19:30 outlook) |
| `/usr/local/bin/piper-speak` | Piper TTS wrapper (sets LD_LIBRARY_PATH, ESPEAK_DATA_PATH) |
| `/usr/local/lib/piper/` | Piper shared libs (isolated to avoid clash with system espeak-ng 1.51) |
| `/usr/local/share/piper-voices/en_GB-jenny_dioco-medium.onnx` | Voice model (61 MB, female British English) |
| `/var/log/weather-forecast.log` | Log output from cron runs |

## Data sources

### Met Office — maritime forecast
- URL: `https://weather.metoffice.gov.uk/specialist-forecasts/coast-and-sea/print/inshore-waters-forecast`
- Section: `<section id="inshore-waters-19">` → `<div class="forecast-block">` or `<div class="outlook-block">`
- Parse class: `MetOfficeParser` (stdlib HTMLParser, no BS4)

### gov.gg — land temperature (fail-safe)
- URL: `https://www.gov.gg/weather`
- Structure: `<div id="weatherToday">` (today) and same div with `class="weatherTomorrow"` (tomorrow)
- Inside: `<span id="wsummary">` for summary, `<span id="wtemp">` ×2 for High/Low
- Parse class: `GovGGParser`
- **Fail-safe**: if fetch fails, raises, or data is incomplete, `fetch_temperature()` returns `None` and the announcement continues without the temperature line — never fatal.

## Audio pipeline

```
announcement text → piper (--output-raw, 22050 Hz signed 16-bit mono)
                  → sox (resample to 8000 Hz mono WAV)
                  → /var/lib/asterisk/sounds/custom/{forecast,midday,outlook}.wav
                  → asterisk -rx "rpt localplay 43172 <path>"
```

Atomic write: sox writes to `{output}.tmp.wav`, replaced with `os.replace()` only on success.

## Testing

```bash
# Test morning announcement
sudo python3 /usr/local/bin/weather-forecast.py --type forecast

# Test midday announcement
sudo python3 /usr/local/bin/weather-forecast.py --type midday

# Test evening announcement
sudo python3 /usr/local/bin/weather-forecast.py --type outlook

# Check last cron run
tail -50 /var/log/weather-forecast.log
```

## Gotchas

- **`asterisk` binary and cron `PATH`**: `asterisk` lives in `/usr/sbin`, which is on an interactive shell's `PATH` but not on cron's default (`/usr/bin:/bin` — `cron.d` files do *not* inherit `/etc/crontab`'s `PATH=` line, that only applies to entries literally inside `/etc/crontab`). A manual test always "works" even when the cron job silently fails with `[Errno 2] No such file or directory: 'asterisk'`. Fixed by calling `asterisk` via an absolute path (`ASTERISK = "/usr/sbin/asterisk"` in `weather-forecast.py`) and by setting `PATH=` explicitly in `/etc/cron.d/weather-forecast` (belt and braces). `sox` doesn't need this — it lives in `/usr/bin`, which cron's default PATH does cover.

- **Piper TTS is a real CPU load on a Raspberry Pi 3B**: on the GB3GU node (Pi 3B, 906MB RAM), piper inference for one announcement took ~30 seconds of sustained near-100% CPU, and `vcgencmd get_throttled` flipped from `0x20000` (historical capping only) to `0x20002` (bit 1 — ARM frequency capping *currently active*) during that run, clearing again once it finished. Memory stayed flat throughout (not an OOM issue). This is suspected as the cause of at least one full host crash/reboot around a scheduled cron firing — on a hot day or with a marginal PSU, the same CPU/thermal spike could plausibly tip into an actual brownout/reset rather than just frequency capping. `weather-forecast.py` now logs `MEM`/`THROTTLE` snapshots and captures piper/sox stderr (previously sent to `DEVNULL`, silently discarding piper's own log lines) specifically to catch this kind of failure. If this keeps happening, look at the power supply (official spec: 5.1V/2.5A) before suspecting software.

## Node context

Node 43172 also has hourly time announcements via `/etc/asterisk/local/hellotime.sh` using `rpt cmd 43172 status 12 xxx`. No root crontab; all jobs in `/etc/cron.d/`.

## GitHub repo and web player

Repo: `G1LRO/gb3gu-maritime-forecast`

GitHub Pages site at `docs/index.html` provides a web-based player for the sample WAV files.

| Repo path | Purpose |
|-----------|---------|
| `weather-forecast.py` | Main script (keep in sync with `/usr/local/bin/weather-forecast.py`) |
| `install.sh` | Automated installer: `sudo ./install.sh <NODE_NUMBER>`. Copies `weather-forecast.py` and patches its `NODE` line via `sed` to match the argument — keep this in sync with any changes to the `NODE = "..."` line format in `weather-forecast.py`. |
| `CLAUDE.md` | This context file |
| `samples/forecast-sample.wav` | Latest forecast audio (played by the web player) |
| `samples/midday-sample.wav` | Latest midday audio (played by the web player) |
| `samples/outlook-sample.wav` | Latest outlook audio (played by the web player) |
| `docs/index.html` | GitHub Pages web player — each `<audio>` `src` is an absolute `raw.githubusercontent.com/.../main/samples/...` URL (not a relative path — the site is served with `docs/` as its root, so a relative `../samples/...` would resolve outside the site and 404) |

**Important**: WAV samples live in `samples/` — do not put them anywhere else or the web player breaks. After any script change that affects the announcement text, regenerate all three WAVs and push all four files (`weather-forecast.py`, `samples/forecast-sample.wav`, `samples/midday-sample.wav`, `samples/outlook-sample.wav`).

## Existing ASL3 repo reference

Similar project (for reference, not used here): `Saytime-Weather-TimeFormat-ASL3`
