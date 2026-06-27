# GB3GU Maritime Forecast

Announces **UK inshore waters forecasts** from the [Met Office](https://weather.metoffice.gov.uk/specialist-forecasts/coast-and-sea/print/inshore-waters-forecast) on an [AllStarLink 3 (ASL3)](https://allstarlink.org) node, twice daily.

The script supports all 19 Met Office inshore waters areas. [GB3GU](https://g1lro.github.io/gb3gu-maritime-forecast/) (Guernsey, node 43172) uses the **Channel Islands** region and adds Guernsey land temperatures from [gov.gg](https://www.gov.gg/weather).

- **07:30** — *"Good morning, here is the Channel Islands 24 hour maritime forecast…"*
- **19:30** — *"Good evening, here is the Channel Islands outlook for the following 24 hours…"*

Inspired by [Saytime-Weather-TimeFormat-ASL3](https://github.com/G1LRO/Saytime-Weather-TimeFormat-ASL3).

---

## Sample audio

**[Listen online &rarr;](https://g1lro.github.io/gb3gu-maritime-forecast/)** (plays in your browser)

- Morning forecast sample: [`samples/forecast-sample.wav`](samples/forecast-sample.wav)
- Evening outlook sample: [`samples/outlook-sample.wav`](samples/outlook-sample.wav)

---

## Requirements

- ASL3 node running on Debian 12 (Bookworm)
- Python 3 with `requests` (`python3-requests`)
- `sox`
- `espeak-ng` (used only as a phonemizer by Piper — not for synthesis)
- [Piper TTS](https://github.com/rhasspy/piper) binary for `linux_aarch64` (or `x86_64`)
- Piper voice model: `en_GB-jenny_dioco-medium`
- Optional: `gpsd` for automatic region selection from GPS coordinates

---

## Installation

### 1. Install system packages

```bash
sudo apt-get install -y sox espeak-ng python3-requests
```

### 2. Install Piper TTS

Download the Piper binary for your architecture from the [Piper releases page](https://github.com/rhasspy/piper/releases/latest).

```bash
# Example for aarch64 (Raspberry Pi)
wget https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz
tar -xzf piper_linux_aarch64.tar.gz

sudo cp piper/piper /usr/local/bin/piper
sudo mkdir -p /usr/local/lib/piper
sudo cp piper/lib*.so* /usr/local/lib/piper/
sudo mkdir -p /usr/local/lib/piper/espeak-ng-data
sudo cp -r piper/espeak-ng-data/* /usr/local/lib/piper/espeak-ng-data/
```

Create a wrapper script to isolate Piper's libraries:

```bash
sudo tee /usr/local/bin/piper-speak << 'WEOF'
#!/bin/bash
exec env \
  LD_LIBRARY_PATH=/usr/local/lib/piper \
  ESPEAK_DATA_PATH=/usr/local/lib/piper/espeak-ng-data \
  /usr/local/bin/piper "$@"
WEOF
sudo chmod 755 /usr/local/bin/piper-speak
```

### 3. Download the voice model

```bash
sudo mkdir -p /usr/local/share/piper-voices
sudo wget -O /usr/local/share/piper-voices/en_GB-jenny_dioco-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx
sudo wget -O /usr/local/share/piper-voices/en_GB-jenny_dioco-medium.onnx.json \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx.json
```

### 4. Install the script

```bash
sudo cp weather-forecast.py /usr/local/bin/weather-forecast.py
sudo chmod 755 /usr/local/bin/weather-forecast.py
```

Edit `NODE` at the top of `weather-forecast.py` to match your ASL3 node number.

### 5. Create the output directory

```bash
sudo mkdir -p /var/lib/asterisk/sounds/custom
```

### 6. Set up cron

For a fixed region (e.g. Channel Islands on GB3GU):

```bash
sudo tee /etc/cron.d/weather-forecast << 'EOF2'
# Maritime forecast announcements
30 7  * * * root /usr/bin/python3 /usr/local/bin/weather-forecast.py --type forecast --region channel-islands >> /var/log/weather-forecast.log 2>&1
30 19 * * * root /usr/bin/python3 /usr/local/bin/weather-forecast.py --type outlook  --region channel-islands >> /var/log/weather-forecast.log 2>&1
EOF2
```

For GPS-based region selection (requires a local `gpsd` instance):

```bash
sudo tee /etc/cron.d/weather-forecast << 'EOF2'
30 7  * * * root /usr/bin/python3 /usr/local/bin/weather-forecast.py --type forecast --gps >> /var/log/weather-forecast.log 2>&1
30 19 * * * root /usr/bin/python3 /usr/local/bin/weather-forecast.py --type outlook  --gps >> /var/log/weather-forecast.log 2>&1
EOF2
```

Alternatively, set `WEATHER_REGION`, or `WEATHER_LAT` and `WEATHER_LON`, in the cron environment.

---

## Region selection

Region is resolved in this order:

1. `--region` on the command line
2. `--lat` / `--lon` (must be used together)
3. `--gps` (reads a fix from local gpsd at `127.0.0.1:2947`)
4. `WEATHER_LAT` / `WEATHER_LON` environment variables
5. `WEATHER_REGION` environment variable
6. Default: `channel-islands`

When using coordinates, the script picks the smallest bounding box that contains the point. Overlapping mainland areas may be ambiguous — use `--region` for fixed installations.

List configured regions and their Met Office section ids:

```bash
python3 weather-forecast.py --list-regions
```

Fetch the live Met Office page and list current section ids (useful if the Met Office renumbers sections):

```bash
python3 weather-forecast.py --list-sections
```

Override a section id directly:

```bash
python3 weather-forecast.py --type forecast --region channel-islands --section inshore-waters-19
```

---

## Manual test

```bash
# Morning forecast (Channel Islands)
sudo python3 /usr/local/bin/weather-forecast.py --type forecast --region channel-islands

# Evening outlook
sudo python3 /usr/local/bin/weather-forecast.py --type outlook --region channel-islands

# Another region
sudo python3 /usr/local/bin/weather-forecast.py --type forecast --region isle-of-man

# Verbose logging
sudo python3 /usr/local/bin/weather-forecast.py --type forecast -v
```

Logs are written to `/var/log/weather-forecast.log`.

---

## How it works

1. Resolves the forecast region (CLI, environment, or GPS)
2. Fetches the Met Office inshore waters forecast page (with HTTP retries)
3. Parses the matching `<section id="inshore-waters-N">` — falls back to title match if the section id has changed
4. Extracts the 24-hour forecast or outlook text, and the region title from the section heading
5. For Channel Islands only: fetches Guernsey land temperature from gov.gg (non-fatal if unavailable)
6. Synthesises speech using **Piper TTS** with the Jenny (en_GB) neural voice
7. Resamples audio to 8 kHz mono WAV using **sox**
8. Plays on the ASL3 node via `asterisk -rx "rpt localplay <node> <file>"`

---

## Unit tests

Parser and region logic can be tested offline using HTML fixtures:

```bash
python3 -m unittest discover -s tests -v
```

---

## Licence

GPL-3.0
