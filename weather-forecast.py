#!/usr/bin/env python3
"""Fetch Channel Islands inshore waters forecast from Met Office and announce on ASL3."""

import sys
import os
import subprocess
import re
import argparse
import time
import traceback
import requests
from html.parser import HTMLParser

URL_METOFFICE = "https://weather.metoffice.gov.uk/specialist-forecasts/coast-and-sea/print/inshore-waters-forecast"
URL_GOVGG = "https://www.gov.gg/weather"
NODE = "43172"
PIPER = "/usr/local/bin/piper-speak"
VOICE = "/usr/local/share/piper-voices/en_GB-jenny_dioco-medium.onnx"
ASTERISK = "/usr/sbin/asterisk"
# Tried pinning piper off one core + `nice -19` on 2026-07-13 to relieve dwc_otg FIQ/USB
# interrupt starvation during piper's CPU-heavy inference (see CLAUDE.md gotchas). Reverted:
# a stuck manual test overran into the next cron firing, and nice -19 caused severe starvation
# between the two overlapping piper processes — both stalled for over an hour and the midday
# announcement was missed entirely, worse than the problem it was meant to fix. Concurrent
# invocations shouldn't happen in normal operation; if trying this again, test a much milder
# niceness first.
MIN_TEXT_WORDS = 10
MIN_AUDIO_BYTES = 50_000

TYPES = {
    "forecast": {
        "div_class": "forecast-block",
        "strip_pattern": r"24 hour forecast:\s*",
        "output": "/var/lib/asterisk/sounds/custom/forecast",
        "intro": "Good morning, here is the Channel Islands 24 hour maritime forecast.",
        "temp_day": "today",
        "temp_label": "The Guernsey inland forecast",
    },
    "midday": {
        "div_class": "forecast-block",
        "strip_pattern": r"24 hour forecast:\s*",
        "output": "/var/lib/asterisk/sounds/custom/midday",
        "intro": "Good afternoon, here is the Channel Islands 24 hour maritime forecast.",
        "temp_day": "today",
        "temp_label": "The Guernsey inland forecast",
    },
    "outlook": {
        "div_class": "outlook-block",
        "strip_pattern": r"Outlook for the following 24 hours:\s*",
        "output": "/var/lib/asterisk/sounds/custom/outlook",
        "intro": "Good evening, here is the Channel Islands outlook for the following 24 hours.",
        "temp_day": "tomorrow",
        "temp_label": "Tomorrow's inland forecast",
    },
}


def log(msg, file=sys.stdout):
    """Timestamped, immediately-flushed log line — cron redirects stdout/stderr to a file,
    which is block-buffered by default; without flush=True, output can sit in the buffer
    and be lost entirely if the process is killed (OOM, segfault) before a normal exit."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {msg}", file=file, flush=True)


def mem_snapshot(label):
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = v.strip()
        log(f"MEM [{label}]: MemAvailable={info.get('MemAvailable', '?')} "
            f"MemFree={info.get('MemFree', '?')}")
    except Exception as e:
        log(f"MEM [{label}]: snapshot failed: {e}")


def throttle_snapshot(label):
    try:
        result = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                                 text=True, timeout=5)
        log(f"THROTTLE [{label}]: {result.stdout.strip() or result.stderr.strip()}")
    except Exception as e:
        log(f"THROTTLE [{label}]: snapshot failed: {e}")


# ---------------------------------------------------------------------------
# Met Office parser
# ---------------------------------------------------------------------------

class MetOfficeParser(HTMLParser):
    def __init__(self, div_class, strip_pattern):
        super().__init__()
        self.div_class = div_class
        self.strip_pattern = strip_pattern
        self.in_target_section = False
        self.in_block = False
        self.in_paragraph = False
        self._para_buf = []
        self.text = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section" and attrs.get("id") == "inshore-waters-19":
            self.in_target_section = True
        if self.in_target_section:
            if tag == "div" and attrs.get("class") == self.div_class:
                self.in_block = True
            if self.in_block and tag == "p":
                self.in_paragraph = True
                self._para_buf = []

    def handle_endtag(self, tag):
        if tag == "p" and self.in_paragraph and self.text is None:
            full = " ".join(self._para_buf).strip()
            full = re.sub(self.strip_pattern, "", full, flags=re.IGNORECASE).strip()
            if full:
                self.text = full
            self.in_paragraph = False
        if tag == "div" and self.in_block:
            self.in_block = False
        if tag == "section" and self.in_target_section:
            self.in_target_section = False

    def handle_data(self, data):
        if self.in_paragraph:
            self._para_buf.append(data)


# ---------------------------------------------------------------------------
# gov.gg temperature parser
# ---------------------------------------------------------------------------

class GovGGParser(HTMLParser):
    """Parses today's and tomorrow's summary and temperatures from gov.gg/weather.

    Both sections use id="weatherToday"; tomorrow's also has class="weatherTomorrow".
    Inside each: id="wsummary" for summary text, id="wtemp" (×2) for High then Low.
    """
    def __init__(self):
        super().__init__()
        self._in_day = None        # "today" or "tomorrow"
        self._in_wsummary = False
        self._in_wtemp = False
        self._in_wtemp_label = False
        self._wtemp_label = None
        self.data = {
            "today":    {"summary": None, "high": None, "low": None},
            "tomorrow": {"summary": None, "high": None, "low": None},
        }

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and attrs.get("id") == "weatherToday":
            self._in_day = "tomorrow" if "weatherTomorrow" in attrs.get("class", "") else "today"
        if self._in_day and tag == "span":
            sid = attrs.get("id", "")
            if sid == "wsummary":
                self._in_wsummary = True
            elif sid == "wtemp":
                self._in_wtemp = True
                self._wtemp_label = None

    def handle_endtag(self, tag):
        if tag == "div" and self._in_day:
            self._in_day = None
        if tag == "span":
            self._in_wsummary = False
            if self._in_wtemp_label:
                self._in_wtemp_label = False
            elif self._in_wtemp:
                self._in_wtemp = False

    def handle_data(self, data):
        if not self._in_day:
            return
        day = self._in_day
        text = data.strip()
        if not text:
            return
        if self._in_wsummary and self.data[day]["summary"] is None:
            self.data[day]["summary"] = text
        elif self._in_wtemp:
            if text in ("High:", "Low:"):
                self._in_wtemp_label = True
                self._wtemp_label = text
            else:
                # strip degree symbol and C
                val = re.sub(r"[°C\s]", "", text)
                if val.lstrip("-").isdigit():
                    if self._wtemp_label == "High:" and self.data[day]["high"] is None:
                        self.data[day]["high"] = val
                    elif self._wtemp_label == "Low:" and self.data[day]["low"] is None:
                        self.data[day]["low"] = val


# ---------------------------------------------------------------------------
# Fetch functions
# ---------------------------------------------------------------------------

def fetch_maritime(announcement_type):
    cfg = TYPES[announcement_type]
    try:
        resp = requests.get(URL_METOFFICE, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to fetch Met Office page: {e}") from e

    parser = MetOfficeParser(cfg["div_class"], cfg["strip_pattern"])
    parser.feed(resp.text)

    if not parser.text:
        raise RuntimeError(
            f"Channel Islands {announcement_type} not found — "
            "Met Office page structure may have changed"
        )
    if len(parser.text.split()) < MIN_TEXT_WORDS:
        raise RuntimeError(
            f"Maritime text too short ({len(parser.text.split())} words): {parser.text!r}"
        )
    return parser.text


def fetch_temperature(day):
    """Return a temperature sentence for 'today' or 'tomorrow', or None on any failure."""
    try:
        resp = requests.get(URL_GOVGG, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        parser = GovGGParser()
        parser.feed(resp.text)
        d = parser.data[day]
        if not d["summary"] or not d["high"] or not d["low"]:
            return None
        return f"{d['summary']} High {d['high']}, low {d['low']} degrees Celsius."
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

def _describe_exit(returncode):
    if returncode < 0:
        return f"code {returncode} (killed by signal {-returncode})"
    return f"code {returncode}"


def speak(announcement_type, maritime_text, temp_sentence):
    cfg = TYPES[announcement_type]
    parts = [cfg["intro"], maritime_text]
    if temp_sentence:
        parts.append(f"{cfg['temp_label']}: {temp_sentence}")
    announcement = " ".join(parts)

    output_path = cfg["output"]
    tmp_path = f"{output_path}.tmp.wav"

    mem_snapshot("before piper")
    throttle_snapshot("before piper")
    log(f"Starting piper (announcement is {len(announcement)} chars)")

    piper = subprocess.Popen(
        [PIPER, "--model", VOICE, "--output-raw"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    sox = subprocess.Popen(
        ["sox", "-t", "raw", "-r", "22050", "-e", "signed-integer", "-b", "16", "-c", "1", "-",
         "-r", "8000", "-c", "1", tmp_path],
        stdin=piper.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    piper.stdout.close()
    piper.stdin.write(announcement.encode())
    piper.stdin.close()

    piper_stderr = piper.stderr.read()
    piper.wait()
    log(f"piper exited: {_describe_exit(piper.returncode)}")
    if piper_stderr:
        log(f"piper stderr: {piper_stderr.decode(errors='replace').strip()}")

    sox_stdout, sox_stderr = sox.communicate()
    log(f"sox exited: {_describe_exit(sox.returncode)}")
    if sox_stderr:
        log(f"sox stderr: {sox_stderr.decode(errors='replace').strip()}")

    mem_snapshot("after piper/sox")
    throttle_snapshot("after piper/sox")

    if piper.returncode != 0:
        _cleanup(tmp_path)
        raise RuntimeError(f"piper failed ({_describe_exit(piper.returncode)})")
    if sox.returncode != 0:
        _cleanup(tmp_path)
        raise RuntimeError(f"sox failed ({_describe_exit(sox.returncode)})")

    size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
    if size < MIN_AUDIO_BYTES:
        _cleanup(tmp_path)
        raise RuntimeError(
            f"Generated audio too small ({size} bytes) — TTS may have produced no output"
        )

    os.replace(tmp_path, f"{output_path}.wav")
    return output_path


def _cleanup(path):
    try:
        os.remove(path)
    except OSError:
        pass


def play(node, output_path):
    subprocess.run(
        [ASTERISK, "-rx", f"rpt localplay {node} {output_path}"],
        check=True,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=["forecast", "midday", "outlook"], default="forecast")
    args = parser.parse_args()

    log(f"=== run starting: type={args.type} pid={os.getpid()} ===")
    mem_snapshot("run start")
    throttle_snapshot("run start")

    try:
        maritime = fetch_maritime(args.type)
        log("Maritime forecast fetched OK")
    except Exception as e:
        log(f"ERROR (maritime fetch): {e}", file=sys.stderr)
        log(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)

    cfg = TYPES[args.type]
    temp = fetch_temperature(cfg["temp_day"])
    if temp is None:
        log("WARNING: gov.gg temperature unavailable — announcing without it", file=sys.stderr)
    else:
        log("Temperature fetched OK")

    try:
        output_path = speak(args.type, maritime, temp)
        log("speak() completed OK, calling asterisk to play")
        play(NODE, output_path)
        log(f"OK [{args.type}]: {maritime[:80]}...")
        if temp:
            log(f"   temp: {temp}")
    except Exception as e:
        log(f"ERROR: {e}", file=sys.stderr)
        log(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)

    log(f"=== run finished OK: type={args.type} ===")


if __name__ == "__main__":
    main()
