# Weather Forecast Announcer — Context

## What this does

Fetches the Channel Islands inshore waters forecast from the Met Office and the Guernsey land temperature from gov.gg, generates a TTS announcement with Piper, and plays it on ASL3 node 43172.

Two announcement types, each run by a cron job:

| Type | Cron | Audio file | Content |
|------|------|------------|---------|
| `forecast` | 07:30 daily | `/var/lib/asterisk/sounds/custom/forecast.wav` | 24-hour maritime forecast + today's temperature |
| `outlook` | 19:30 daily | `/var/lib/asterisk/sounds/custom/outlook.wav` | Outlook for following 24 hours + tomorrow's temperature |

## Key files

| Path | Purpose |
|------|---------|
| `/usr/local/bin/weather-forecast.py` | Main script |
| `/etc/cron.d/weather-forecast` | Cron jobs (07:30 forecast, 19:30 outlook) |
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
                  → /var/lib/asterisk/sounds/custom/{forecast,outlook}.wav
                  → asterisk -rx "rpt localplay 43172 <path>"
```

Atomic write: sox writes to `{output}.tmp.wav`, replaced with `os.replace()` only on success.

## Testing

```bash
# Test morning announcement
sudo python3 /usr/local/bin/weather-forecast.py --type forecast

# Test evening announcement
sudo python3 /usr/local/bin/weather-forecast.py --type outlook

# Check last cron run
tail -50 /var/log/weather-forecast.log
```

## Node context

Node 43172 also has hourly time announcements via `/etc/asterisk/local/hellotime.sh` using `rpt cmd 43172 status 12 xxx`. No root crontab; all jobs in `/etc/cron.d/`.

## Existing ASL3 repo reference

Similar project (for reference, not used here): `Saytime-Weather-TimeFormat-ASL3`
