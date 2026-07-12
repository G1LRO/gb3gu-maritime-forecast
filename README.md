# GB3GU Maritime Forecast

Announces the **Channel Islands inshore waters forecast** from the [Met Office](https://weather.metoffice.gov.uk/specialist-forecasts/coast-and-sea/print/inshore-waters-forecast) on an [AllStarLink 3 (ASL3)](https://allstarlink.org) node, three times daily:

- **07:30** — *"Good morning, here is the Channel Islands 24 hour maritime forecast…"*
- **12:30** — *"Good afternoon, here is the Channel Islands 24 hour maritime forecast…"*
- **19:30** — *"Good evening, here is the Channel Islands outlook for the following 24 hours…"*

Inspired by [Saytime-Weather-TimeFormat-ASL3](https://github.com/G1LRO/Saytime-Weather-TimeFormat-ASL3).

---

## Sample audio

**[Listen online &rarr;](https://g1lro.github.io/gb3gu-maritime-forecast/)** (plays in your browser)

- Morning forecast sample: [`samples/forecast-sample.wav`](samples/forecast-sample.wav)
- Midday forecast sample: [`samples/midday-sample.wav`](samples/midday-sample.wav)
- Evening outlook sample: [`samples/outlook-sample.wav`](samples/outlook-sample.wav)

---

## Requirements

- ASL3 node running on Debian 12 (Bookworm)
- Python 3 with `requests` (`python3-requests`)
- `sox`
- `espeak-ng` (used only as a phonemizer by Piper — not for synthesis)
- [Piper TTS](https://github.com/rhasspy/piper) binary for `linux_aarch64` (or `x86_64`)
- Piper voice model: `en_GB-jenny_dioco-medium`

---

## Installation

### Quick install

`install.sh` automates steps 1–6 below on a fresh ASL3 / Debian 12 (Bookworm) image:

```bash
sudo ./install.sh <NODE_NUMBER>
```

It installs the system packages, Piper TTS, the voice model, `weather-forecast.py` (with `NODE`
patched to the number you pass), the output directory, and the cron jobs. The node must already be
set up via `asl-menu` (the `rpt.conf` / `simpleusb.conf` stanzas for it need to exist first). Safe
to re-run — already-installed packages, the Piper binary, and the voice model are skipped if present.

Use the manual steps below instead if you want to customise the install or see exactly what each
step does.

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
env \
  LD_LIBRARY_PATH=/usr/local/lib/piper \
  ESPEAK_DATA_PATH=/usr/local/lib/piper/espeak-ng-data \
  /usr/local/bin/piper "$@"
WEOF
sudo chmod 755 /usr/local/bin/piper-speak
```

> **Note:** earlier versions of this script used `exec env ...`. If that line is ever run directly at an
> interactive shell prompt (rather than as the `piper-speak` script file), `exec` replaces the current
> shell process, and the terminal session closes as soon as Piper exits. Plain `env` avoids that.

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

```bash
sudo tee /etc/cron.d/weather-forecast << 'EOF2'
# Channel Islands maritime forecast announcements
30 7  * * * root /usr/bin/python3 /usr/local/bin/weather-forecast.py --type forecast >> /var/log/weather-forecast.log 2>&1
30 12 * * * root /usr/bin/python3 /usr/local/bin/weather-forecast.py --type midday   >> /var/log/weather-forecast.log 2>&1
30 19 * * * root /usr/bin/python3 /usr/local/bin/weather-forecast.py --type outlook  >> /var/log/weather-forecast.log 2>&1
EOF2
```

---

## Manual test

```bash
# Morning forecast
sudo python3 /usr/local/bin/weather-forecast.py --type forecast

# Midday forecast
sudo python3 /usr/local/bin/weather-forecast.py --type midday

# Evening outlook
sudo python3 /usr/local/bin/weather-forecast.py --type outlook
```

Logs are written to `/var/log/weather-forecast.log`.

---

## How it works

1. Fetches the Met Office inshore waters forecast page
2. Parses the **Channel Islands** section (`<section id="inshore-waters-19">`)
3. Extracts either the 24-hour forecast or the outlook text
4. Synthesises speech using **Piper TTS** with the Jenny (en_GB) neural voice
5. Resamples audio to 8 kHz mono WAV using **sox**
6. Plays on the ASL3 node via `asterisk -rx "rpt localplay <node> <file>"`

---

## Licence

GPL-3.0
