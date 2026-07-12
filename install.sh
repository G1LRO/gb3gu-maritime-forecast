#!/bin/bash
# GB3GU Maritime Forecast — automated installer
# Run from a fresh AllStarLink 3 (ASL3) / Debian 12 (Bookworm) SD image.
#
# Usage:
#   sudo ./install.sh <NODE_NUMBER>
#
# Prerequisites: node already set up via `asl-menu` (rpt.conf / simpleusb.conf
# stanzas must exist for the node before this script runs asterisk commands).

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root (sudo ./install.sh <NODE_NUMBER>)" >&2
    exit 1
fi

if [[ $# -ne 1 || ! "$1" =~ ^[0-9]+$ ]]; then
    echo "Usage: sudo ./install.sh <NODE_NUMBER>" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE="$1"
ARCH=$(uname -m)

case "$ARCH" in
    aarch64) PIPER_ASSET="piper_linux_aarch64.tar.gz" ;;
    x86_64)  PIPER_ASSET="piper_linux_x86_64.tar.gz" ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

echo "=== 1. System packages ==="
apt-get update -qq
apt-get install -y sox espeak-ng python3-requests

echo ""
echo "=== 2. Piper TTS ==="
if [[ -x /usr/local/bin/piper ]]; then
    echo "  piper already installed, skipping download"
else
    TMP_DIR=$(mktemp -d)
    wget -q -O "$TMP_DIR/piper.tar.gz" \
        "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/${PIPER_ASSET}"
    tar -xzf "$TMP_DIR/piper.tar.gz" -C "$TMP_DIR"

    cp "$TMP_DIR/piper/piper" /usr/local/bin/piper
    mkdir -p /usr/local/lib/piper
    cp "$TMP_DIR/piper"/lib*.so* /usr/local/lib/piper/
    mkdir -p /usr/local/lib/piper/espeak-ng-data
    cp -r "$TMP_DIR/piper/espeak-ng-data/"* /usr/local/lib/piper/espeak-ng-data/
    rm -rf "$TMP_DIR"
    echo "  piper installed to /usr/local/bin/piper"
fi

# NOTE: no `exec` here — `exec` replaces the calling shell process, which
# closes the terminal session if this line is ever run directly at a prompt
# instead of via the piper-speak script file.
tee /usr/local/bin/piper-speak > /dev/null << 'WEOF'
#!/bin/bash
env \
  LD_LIBRARY_PATH=/usr/local/lib/piper \
  ESPEAK_DATA_PATH=/usr/local/lib/piper/espeak-ng-data \
  /usr/local/bin/piper "$@"
WEOF
chmod 755 /usr/local/bin/piper-speak
echo "  piper-speak wrapper installed"

echo ""
echo "=== 3. Voice model ==="
mkdir -p /usr/local/share/piper-voices
if [[ -f /usr/local/share/piper-voices/en_GB-jenny_dioco-medium.onnx ]]; then
    echo "  voice model already present, skipping download"
else
    wget -q -O /usr/local/share/piper-voices/en_GB-jenny_dioco-medium.onnx \
        https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx
    wget -q -O /usr/local/share/piper-voices/en_GB-jenny_dioco-medium.onnx.json \
        https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx.json
    echo "  voice model downloaded"
fi

echo ""
echo "=== 4. weather-forecast.py (node $NODE) ==="
cp "$SCRIPT_DIR/weather-forecast.py" /usr/local/bin/weather-forecast.py
sed -i "s/^NODE = \".*\"/NODE = \"$NODE\"/" /usr/local/bin/weather-forecast.py
chmod 755 /usr/local/bin/weather-forecast.py
grep -q "^NODE = \"$NODE\"\$" /usr/local/bin/weather-forecast.py || {
    echo "  ERROR: failed to set NODE in weather-forecast.py" >&2
    exit 1
}
echo "  installed (NODE=$NODE)"

echo ""
echo "=== 5. Output directory ==="
mkdir -p /var/lib/asterisk/sounds/custom
chown asterisk:asterisk /var/lib/asterisk/sounds/custom
echo "  /var/lib/asterisk/sounds/custom ready"

echo ""
echo "=== 6. Cron ==="
tee /etc/cron.d/weather-forecast > /dev/null << EOF2
# Channel Islands maritime forecast announcements
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
30 7  * * * root /usr/bin/python3 /usr/local/bin/weather-forecast.py --type forecast >> /var/log/weather-forecast.log 2>&1
30 12 * * * root /usr/bin/python3 /usr/local/bin/weather-forecast.py --type midday   >> /var/log/weather-forecast.log 2>&1
30 19 * * * root /usr/bin/python3 /usr/local/bin/weather-forecast.py --type outlook  >> /var/log/weather-forecast.log 2>&1
EOF2
chmod 644 /etc/cron.d/weather-forecast
echo "  cron jobs installed (07:30 forecast, 12:30 midday, 19:30 outlook)"

echo ""
echo "=== Done ==="
echo "Test manually with:"
echo "  sudo python3 /usr/local/bin/weather-forecast.py --type forecast"
echo "  sudo python3 /usr/local/bin/weather-forecast.py --type midday"
echo "  sudo python3 /usr/local/bin/weather-forecast.py --type outlook"
