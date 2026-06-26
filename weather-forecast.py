#!/usr/bin/env python3
"""Fetch Channel Islands inshore waters forecast from Met Office and announce on ASL3."""

import sys
import subprocess
import re
import argparse
import requests
from html.parser import HTMLParser

URL = "https://weather.metoffice.gov.uk/specialist-forecasts/coast-and-sea/print/inshore-waters-forecast"
NODE = "43172"
PIPER = "/usr/local/bin/piper-speak"
VOICE = "/usr/local/share/piper-voices/en_GB-jenny_dioco-medium.onnx"

TYPES = {
    "forecast": {
        "div_class": "forecast-block",
        "strip_pattern": r"24 hour forecast:\s*",
        "output": "/var/lib/asterisk/sounds/custom/forecast",
        "intro": "Good morning, here is the Channel Islands 24 hour maritime forecast.",
    },
    "outlook": {
        "div_class": "outlook-block",
        "strip_pattern": r"Outlook for the following 24 hours:\s*",
        "output": "/var/lib/asterisk/sounds/custom/outlook",
        "intro": "Good evening, here is the Channel Islands outlook for the following 24 hours.",
    },
}


class ForecastParser(HTMLParser):
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


def fetch(announcement_type):
    cfg = TYPES[announcement_type]
    resp = requests.get(URL, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    parser = ForecastParser(cfg["div_class"], cfg["strip_pattern"])
    parser.feed(resp.text)
    if not parser.text:
        raise RuntimeError(f"Channel Islands {announcement_type} not found in page")
    return parser.text


def speak(announcement_type, text):
    cfg = TYPES[announcement_type]
    announcement = f"{cfg['intro']} {text}"
    output_path = cfg["output"]
    piper = subprocess.Popen(
        [PIPER, "--model", VOICE, "--output-raw"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    sox = subprocess.Popen(
        ["sox", "-t", "raw", "-r", "22050", "-e", "signed-integer", "-b", "16", "-c", "1", "-",
         "-r", "8000", "-c", "1", f"{output_path}.wav"],
        stdin=piper.stdout,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    piper.stdout.close()
    piper.stdin.write(announcement.encode())
    piper.stdin.close()
    piper.wait()
    sox.wait()
    if piper.returncode != 0:
        raise RuntimeError(f"piper failed (code {piper.returncode})")
    if sox.returncode != 0:
        raise RuntimeError(f"sox failed (code {sox.returncode})")
    return output_path


def play(node, output_path):
    subprocess.run(
        ["asterisk", "-rx", f"rpt localplay {node} {output_path}"],
        check=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=["forecast", "outlook"], default="forecast")
    args = parser.parse_args()

    try:
        text = fetch(args.type)
        output_path = speak(args.type, text)
        play(NODE, output_path)
        print(f"OK [{args.type}]: {text[:100]}...")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
