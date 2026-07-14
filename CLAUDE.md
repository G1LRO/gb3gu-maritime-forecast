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

- **Piper TTS is a real CPU load on a Raspberry Pi 3B**: on the GB3GU node (Pi 3B, 906MB RAM), piper inference for one announcement took ~30 seconds of sustained near-100% CPU. During that window, temp has been observed ramping 55°C → 82°C and `MemAvailable` dipping as low as 240MB (only visible with sub-minute sampling — `weather-forecast.py`'s own before/after snapshots are too coarse to catch the mid-run trough). The kernel logged real USB transfer failures during that exact window: `Transfer to device N endpoint 0x2 frame ... failed - FIQ reported NYET. Data may have been lost.` — a known Pi 2/3 `dwc_otg` USB controller weakness where FIQ-based interrupt servicing gets starved under CPU saturation, hitting whichever USB device is active (here, the SimpleUSB radio interface itself, mid-broadcast). Suspected cause of at least one full host crash/reboot around a scheduled cron firing. `weather-forecast.py` logs `MEM`/`THROTTLE` snapshots and captures piper/sox stderr (previously `DEVNULL`, silently discarding piper's own log lines) to help catch this. If it keeps happening, check the power supply (official spec: 5.1V/2.5A) before suspecting software further.

- **Don't fight the CPU spike with `nice`/`taskset` — tried and reverted (2026-07-13)**: pinning piper off one core plus `nice -n 19` was meant to relieve the FIQ-starvation issue above. Instead, when a stuck manual test overran into the next scheduled cron firing, the two overlapping piper processes starved each other under `nice -19` so badly that both stalled for over an hour and a real announcement was missed entirely — worse than the bug it was meant to fix. Reverted. `timeout 120` on the cron entry (see below) is what actually saved it, by killing the stuck cron-triggered run rather than letting it hang indefinitely.

- **`timeout 120` and the hardware watchdog are cheap insurance, keep them**: `/etc/cron.d/weather-forecast` wraps each invocation in `timeout 120` so any hang (piper, sox, asterisk, network) gets killed rather than blocking the node forever — this is what limited the above incident to "one missed announcement" instead of an indefinite hang. Separately, `install.sh` step 7 configures the Pi's hardware watchdog (`/dev/watchdog`, 15s timeout, pet every 4s via `/etc/watchdog.conf`) so a genuine kernel/USB lockup self-recovers via reboot instead of needing someone to notice and power-cycle it. Deliberately no load-average or memory triggers in `watchdog.conf` — this node legitimately spikes CPU during piper synthesis, and a load-based trigger would treat normal operation as a hang. Gotcha when installing: Debian's `watchdog` package defaults to `run_wd_keepalive=1` in `/etc/default/watchdog`, which makes the service's stop hook deliberately fail (to hand off to a companion `wd_keepalive.service`) — this cancels the start half of any `systemctl restart watchdog` and leaves nothing running. `install.sh` sets `run_wd_keepalive=0` to avoid that.

## Node context

Node 43172 also has hourly time announcements via `/etc/asterisk/local/hellotime.sh` using `rpt cmd 43172 status 12 xxx`. No root crontab; all jobs in `/etc/cron.d/`.

## Repeater-collision theory (2026-07-14, being tested)

Theory: crashes might correlate with the node actively receiving RF (another station transmitting/using the repeater) at the moment the script tries to broadcast — a conflict between the automated announcement's PTT and real repeater traffic, rather than (or in addition to) the CPU/thermal/USB mechanism in the Gotchas section above.

`channel_snapshot()` in `weather-forecast.py` queries `asterisk -rx "rpt stats <NODE>"` and logs `signal_on_input` (COS/is-the-repeater-currently-receiving-RF), plus `keyups_today` and `tx_time_today` as sanity-check counters, at five points: run start, before piper, after piper/sox, before `play()`, and after `play()`. Confirmed working via `keyups_today` incrementing exactly at "after play" in test runs (both on this node and the local test node) — the metric does track real activity, not just idle noise.

Also worth knowing: `play()`'s call to `asterisk -rx "rpt localplay ..."` only *dispatches* playback — the CLI command returning success does not mean the audio has finished actually transmitting over RF. That happens asynchronously inside Asterisk's own process, which this script has no visibility into once the CLI call returns. So "OK [type]: ..." in the log means the dispatch succeeded, not necessarily that the full broadcast completed before a subsequent crash. Checked Asterisk's own `/var/log/asterisk/messages.log` for evidence of past crashes: it doesn't help — the file is fresh-started by `asterisk.service` on every boot, so it never contains anything from before a crash, only that boot's own startup sequence.

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
