#!/usr/bin/env python3
"""Fetch UK inshore waters forecast from Met Office and announce on ASL3."""

import argparse
import json
import logging
import os
import re
import socket
import subprocess
import sys
from html.parser import HTMLParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

URL_METOFFICE = (
    "https://weather.metoffice.gov.uk/specialist-forecasts/coast-and-sea/"
    "print/inshore-waters-forecast"
)
URL_GOVGG = "https://www.gov.gg/weather"
NODE = "43172"
PIPER = "/usr/local/bin/piper-speak"
VOICE = "/usr/local/share/piper-voices/en_GB-jenny_dioco-medium.onnx"
DEFAULT_REGION = "channel-islands"
USER_AGENT = "GB3GU-weather-forecast/2.0"
MIN_TEXT_WORDS = 10
MIN_AUDIO_BYTES = 50_000
HTTP_TIMEOUT = 20
GPSD_HOST = "127.0.0.1"
GPSD_PORT = 2947
GPSD_READ_ATTEMPTS = 30

log = logging.getLogger("weather-forecast")

TYPES = {
    "forecast": {
        "div_class": "forecast-block",
        "strip_pattern": r"24 hour forecast:\s*",
        "output": "/var/lib/asterisk/sounds/custom/forecast",
        "intro": "Good morning, here is the {region} 24 hour maritime forecast.",
        "temp_day": "today",
    },
    "outlook": {
        "div_class": "outlook-block",
        "strip_pattern": r"Outlook for the following 24 hours:\s*",
        "output": "/var/lib/asterisk/sounds/custom/outlook",
        "intro": "Good evening, here is the {region} outlook for the following 24 hours.",
        "temp_day": "tomorrow",
    },
}

# Approximate bounding boxes (min_lat, max_lat, min_lon, max_lon) for GPS lookup.
# Mainland areas overlap at coarse resolution; smallest matching box wins.
# Island areas are tighter. Use --region for fixed nodes if GPS is ambiguous.
REGIONS = {
    "cape-wrath-rattray": {
        "section_id": "inshore-waters-1",
        "name": "Cape Wrath to Rattray Head including Orkney",
        "title_match": "Cape Wrath to Rattray Head including Orkney",
        "bbox": (58.2, 59.5, -5.5, -2.0),
    },
    "rattray-berwick": {
        "section_id": "inshore-waters-2",
        "name": "Rattray Head to Berwick upon Tweed",
        "title_match": "Rattray Head to Berwick upon Tweed",
        "bbox": (55.5, 58.5, -3.5, 0.5),
    },
    "berwick-whitby": {
        "section_id": "inshore-waters-3",
        "name": "Berwick upon Tweed to Whitby",
        "title_match": "Berwick upon Tweed to Whitby",
        "bbox": (54.0, 56.5, -2.5, 0.5),
    },
    "whitby-gibraltar": {
        "section_id": "inshore-waters-4",
        "name": "Whitby to Gibraltar Point",
        "title_match": "Whitby to Gibraltar Point",
        "bbox": (52.5, 55.0, -1.5, 1.0),
    },
    "gibraltar-north-foreland": {
        "section_id": "inshore-waters-5",
        "name": "Gibraltar Point to North Foreland",
        "title_match": "Gibraltar Point to North Foreland",
        "bbox": (51.0, 53.5, -1.0, 2.0),
    },
    "north-foreland-selsey": {
        "section_id": "inshore-waters-6",
        "name": "North Foreland to Selsey Bill",
        "title_match": "North Foreland to Selsey Bill",
        "bbox": (50.0, 52.0, -1.5, 2.0),
    },
    "selsey-lyme": {
        "section_id": "inshore-waters-7",
        "name": "Selsey Bill to Lyme Regis",
        "title_match": "Selsey Bill to Lyme Regis",
        "bbox": (50.0, 51.5, -5.5, -0.5),
    },
    "lyme-lands-end": {
        "section_id": "inshore-waters-8",
        "name": "Lyme Regis to Lands End including the Isles of Scilly",
        "title_match": "Lyme Regis to Lands End including the Isles of Scilly",
        "bbox": (49.5, 51.5, -7.5, -2.5),
    },
    "lands-end-st-davids": {
        "section_id": "inshore-waters-9",
        "name": "Lands End to St Davids Head including the Bristol Channel",
        "title_match": "Lands End to St Davids Head including the Bristol Channel",
        "bbox": (50.0, 52.5, -6.5, -3.5),
    },
    "st-davids-great-orme": {
        "section_id": "inshore-waters-10",
        "name": "St Davids Head to Great Orme Head, including St Georges Channel",
        "title_match": "St Davids Head to Great Orme Head",
        "bbox": (51.5, 54.0, -6.5, -3.0),
    },
    "great-orme-mull-galloway": {
        "section_id": "inshore-waters-11",
        "name": "Great Orme Head to the Mull of Galloway",
        "title_match": "Great Orme Head to the Mull of Galloway",
        "bbox": (53.0, 55.5, -6.0, -3.0),
    },
    "isle-of-man": {
        "section_id": "inshore-waters-12",
        "name": "Isle of Man",
        "title_match": "Isle of Man",
        "bbox": (53.9, 54.5, -5.0, -4.0),
    },
    "lough-foyle-carlingford": {
        "section_id": "inshore-waters-13",
        "name": "Lough Foyle to Carlingford Lough",
        "title_match": "Lough Foyle to Carlingford Lough",
        "bbox": (53.8, 55.5, -8.0, -5.5),
    },
    "mull-galloway-mull-kintyre": {
        "section_id": "inshore-waters-14",
        "name": "Mull of Galloway to Mull of Kintyre including the Firth of Clyde and North Channel",
        "title_match": "Mull of Galloway to Mull of Kintyre",
        "bbox": (54.5, 56.5, -7.5, -4.5),
    },
    "mull-kintyre-ardnamurchan": {
        "section_id": "inshore-waters-15",
        "name": "Mull of Kintyre to Ardnamurchan Point",
        "title_match": "Mull of Kintyre to Ardnamurchan Point",
        "bbox": (55.0, 57.0, -7.5, -5.0),
    },
    "the-minch": {
        "section_id": "inshore-waters-16",
        "name": "The Minch",
        "title_match": "The Minch",
        "bbox": (57.0, 58.8, -7.5, -5.5),
    },
    "ardnamurchan-cape-wrath": {
        "section_id": "inshore-waters-17",
        "name": "Ardnamurchan Point to Cape Wrath",
        "title_match": "Ardnamurchan Point to Cape Wrath",
        "bbox": (56.5, 59.0, -7.5, -4.5),
    },
    "shetland": {
        "section_id": "inshore-waters-18",
        "name": "Shetland Isles",
        "title_match": "Shetland Isles",
        "bbox": (59.5, 61.0, -2.5, -0.5),
    },
    "channel-islands": {
        "section_id": "inshore-waters-19",
        "name": "Channel Islands",
        "title_match": "Channel Islands",
        "bbox": (48.8, 50.0, -3.05, -1.75),
        "temp_source": "gov.gg",
        "temp_label": {
            "forecast": "The Guernsey inland forecast",
            "outlook": "Tomorrow's inland forecast",
        },
    },
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_session():
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def _fetch_url(url, timeout=HTTP_TIMEOUT):
    try:
        resp = _http_session().get(url, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to fetch {url}: {e}") from e


def _has_class(attrs, class_name):
    raw = attrs.get("class", "")
    if isinstance(raw, list):
        classes = raw
    else:
        classes = raw.split()
    return class_name in classes


# ---------------------------------------------------------------------------
# Met Office parser
# ---------------------------------------------------------------------------

class MetOfficeParser(HTMLParser):
    def __init__(self, section_id, div_class, strip_pattern):
        super().__init__()
        self.section_id = section_id
        self.div_class = div_class
        self.strip_pattern = strip_pattern
        self.in_target_section = False
        self.in_block = False
        self.in_paragraph = False
        self._para_buf = []
        self._paragraphs = []
        self._in_h3 = False
        self._h3_buf = []
        self.region_title = None
        self.text = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section" and attrs.get("id") == self.section_id:
            self.in_target_section = True
        if self.in_target_section:
            if tag == "h3" and self.region_title is None:
                self._in_h3 = True
                self._h3_buf = []
            if tag == "div" and _has_class(attrs, self.div_class):
                self.in_block = True
                self._paragraphs = []
            if self.in_block and tag == "p":
                self.in_paragraph = True
                self._para_buf = []

    def handle_endtag(self, tag):
        if tag == "h3" and self._in_h3:
            self.region_title = " ".join("".join(self._h3_buf).split())
            self._in_h3 = False
        if tag == "p" and self.in_paragraph:
            full = " ".join(self._para_buf).strip()
            full = re.sub(self.strip_pattern, "", full, flags=re.IGNORECASE).strip()
            if full:
                self._paragraphs.append(full)
            self.in_paragraph = False
        if tag == "div" and self.in_block:
            self.in_block = False
            if self._paragraphs and self.text is None:
                self.text = " ".join(self._paragraphs)
        if tag == "section" and self.in_target_section:
            self.in_target_section = False

    def handle_data(self, data):
        if self._in_h3:
            self._h3_buf.append(data)
        if self.in_paragraph:
            self._para_buf.append(data)


def discover_sections(html):
    """Return [{section_id, title}, ...] from a Met Office print page."""
    sections = []
    for match in re.finditer(
        r'<section[^>]*id="(inshore-waters-\d+)"[^>]*>\s*<h3[^>]*>(.*?)</h3>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        title = re.sub(r"<[^>]+>", "", match.group(2))
        title = " ".join(title.split())
        sections.append({"section_id": match.group(1), "title": title})
    return sections


def resolve_section_id(html, region_cfg):
    """Resolve section id, falling back to title match if the configured id moved."""
    section_id = region_cfg["section_id"]
    title_match = region_cfg.get("title_match", region_cfg["name"]).lower()

    sections = discover_sections(html)
    known_ids = {item["section_id"] for item in sections}
    if section_id in known_ids:
        return section_id

    for item in sections:
        if title_match in item["title"].lower():
            log.warning(
                "Section id %s not found; using %s (%s) via title match",
                section_id,
                item["section_id"],
                item["title"],
            )
            return item["section_id"]

    available = ", ".join(f'{s["section_id"]}={s["title"]!r}' for s in sections)
    raise RuntimeError(
        f"Could not locate section for {region_cfg['name']!r}. "
        f"Available sections: {available or 'none'}"
    )


# ---------------------------------------------------------------------------
# gov.gg temperature parser
# ---------------------------------------------------------------------------

class GovGGParser(HTMLParser):
    """Parses today's and tomorrow's summary and temperatures from gov.gg/weather."""

    def __init__(self):
        super().__init__()
        self._in_day = None
        self._in_wsummary = False
        self._in_wtemp = False
        self._in_wtemp_label = False
        self._wtemp_label = None
        self.data = {
            "today": {"summary": None, "high": None, "low": None},
            "tomorrow": {"summary": None, "high": None, "low": None},
        }

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and attrs.get("id") == "weatherToday":
            classes = attrs.get("class", "")
            if isinstance(classes, list):
                class_str = " ".join(classes)
            else:
                class_str = classes
            self._in_day = "tomorrow" if "weatherTomorrow" in class_str else "today"
        if self._in_day and tag == "span":
            sid = attrs.get("id", "")
            if sid == "wsummary":
                self._in_wsummary = True
            elif sid == "wtemp":
                self._in_wtemp = True

    def handle_endtag(self, tag):
        if tag == "div" and self._in_day:
            self._in_day = None
        if tag == "span":
            self._in_wsummary = False
            if self._in_wtemp_label:
                self._in_wtemp_label = False
            elif self._in_wtemp:
                self._in_wtemp = False
                if self._wtemp_label in ("High:", "Low:"):
                    self._wtemp_label = None

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
                val = re.sub(r"[°C\s]", "", text)
                if val.lstrip("-").isdigit():
                    if self._wtemp_label == "High:" and self.data[day]["high"] is None:
                        self.data[day]["high"] = val
                    elif self._wtemp_label == "Low:" and self.data[day]["low"] is None:
                        self.data[day]["low"] = val


# ---------------------------------------------------------------------------
# Region / GPS resolution
# ---------------------------------------------------------------------------

def _bbox_area(bbox):
    min_lat, max_lat, min_lon, max_lon = bbox
    return (max_lat - min_lat) * (max_lon - min_lon)


def resolve_region_from_coords(lat, lon):
    """Pick the smallest bounding box that contains the coordinates."""
    matches = []
    for key, cfg in REGIONS.items():
        min_lat, max_lat, min_lon, max_lon = cfg["bbox"]
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            matches.append((key, _bbox_area(cfg["bbox"])))

    if not matches:
        return None

    matches.sort(key=lambda item: item[1])
    return matches[0][0]


def read_gpsd(host=GPSD_HOST, port=GPSD_PORT, attempts=GPSD_READ_ATTEMPTS):
    """Read a 2D+ fix from a local gpsd instance. Returns (lat, lon) or None."""
    try:
        sock = socket.create_connection((host, port), timeout=5)
    except OSError as e:
        log.warning("gpsd unavailable at %s:%s: %s", host, port, e)
        return None

    try:
        with sock:
            sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
            sock_file = sock.makefile("rb")
            for _ in range(attempts):
                line = sock_file.readline()
                if not line:
                    continue
                try:
                    payload = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if payload.get("class") != "TPV":
                    continue
                if payload.get("mode", 0) < 2:
                    continue
                lat = payload.get("lat")
                lon = payload.get("lon")
                if lat is not None and lon is not None:
                    return float(lat), float(lon)
    except OSError as e:
        log.warning("gpsd read failed: %s", e)

    log.warning("gpsd returned no valid fix after %s attempts", attempts)
    return None


def resolve_region_config(args):
    """Determine region key and config from CLI args, env, or GPS."""
    if args.region:
        if args.region not in REGIONS:
            known = ", ".join(sorted(REGIONS))
            raise RuntimeError(f"Unknown region {args.region!r}. Known: {known}")
        return args.region, REGIONS[args.region]

    lat = args.lat
    lon = args.lon
    if lat is None and lon is None:
        env_lat = os.environ.get("WEATHER_LAT")
        env_lon = os.environ.get("WEATHER_LON")
        if env_lat and env_lon:
            lat = float(env_lat)
            lon = float(env_lon)
    if args.gps:
        fix = read_gpsd()
        if fix:
            lat, lon = fix
            log.info("GPS fix: %.5f, %.5f", lat, lon)

    if lat is not None and lon is not None:
        region_key = resolve_region_from_coords(lat, lon)
        if region_key is None:
            log.warning(
                "Coordinates %.5f, %.5f not in any inshore area; using %s",
                lat,
                lon,
                DEFAULT_REGION,
            )
            region_key = DEFAULT_REGION
        else:
            log.info("Coordinates resolved to region %s", region_key)
        return region_key, REGIONS[region_key]

    env_region = os.environ.get("WEATHER_REGION")
    region_key = env_region or DEFAULT_REGION
    if region_key not in REGIONS:
        raise RuntimeError(f"Unknown WEATHER_REGION {region_key!r}")
    return region_key, REGIONS[region_key]


# ---------------------------------------------------------------------------
# Fetch functions
# ---------------------------------------------------------------------------

def fetch_maritime(announcement_type, region_cfg, html=None):
    cfg = TYPES[announcement_type]
    if html is None:
        html = _fetch_url(URL_METOFFICE)

    section_id = region_cfg.get("section_override") or resolve_section_id(html, region_cfg)
    parser = MetOfficeParser(section_id, cfg["div_class"], cfg["strip_pattern"])
    parser.feed(html)

    region_name = parser.region_title or region_cfg["name"]
    if not parser.text:
        raise RuntimeError(
            f"{region_name} {announcement_type} not found — "
            "Met Office page structure may have changed"
        )
    if len(parser.text.split()) < MIN_TEXT_WORDS:
        raise RuntimeError(
            f"Maritime text too short ({len(parser.text.split())} words): "
            f"{parser.text!r}"
        )
    return parser.text, region_name


def fetch_temperature(day, region_cfg, announcement_type):
    """Return a temperature sentence, or None on any failure (non-fatal)."""
    if region_cfg.get("temp_source") != "gov.gg":
        return None
    try:
        html = _fetch_url(URL_GOVGG, timeout=15)
        parser = GovGGParser()
        parser.feed(html)
        d = parser.data[day]
        missing = [k for k, v in d.items() if not v]
        if missing:
            log.warning("gov.gg incomplete for %s (missing: %s)", day, ", ".join(missing))
            return None
        return f"{d['summary']} High {d['high']}, low {d['low']} degrees Celsius."
    except Exception as e:
        log.warning("gov.gg temperature unavailable: %s", e)
        return None


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

def speak(announcement_type, maritime_text, temp_sentence, region_name, region_cfg):
    cfg = TYPES[announcement_type]
    intro = cfg["intro"].format(region=region_name)
    parts = [intro, maritime_text]
    if temp_sentence:
        labels = region_cfg.get("temp_label", {})
        label = labels.get(announcement_type, "The inland forecast")
        parts.append(f"{label}: {temp_sentence}")
    announcement = " ".join(parts)

    output_path = cfg["output"]
    tmp_path = f"{output_path}.tmp.wav"

    piper = subprocess.Popen(
        [PIPER, "--model", VOICE, "--output-raw"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    sox = subprocess.Popen(
        [
            "sox",
            "-t",
            "raw",
            "-r",
            "22050",
            "-e",
            "signed-integer",
            "-b",
            "16",
            "-c",
            "1",
            "-",
            "-r",
            "8000",
            "-c",
            "1",
            tmp_path,
        ],
        stdin=piper.stdout,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    piper.stdout.close()

    _, piper_err = piper.communicate(announcement.encode("utf-8"))
    sox_err = sox.stderr.read().decode("utf-8", errors="replace").strip()
    sox.wait()

    if piper.returncode != 0:
        _cleanup(tmp_path)
        detail = piper_err.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"piper failed (code {piper.returncode}): {detail}")
    if sox.returncode != 0:
        _cleanup(tmp_path)
        raise RuntimeError(f"sox failed (code {sox.returncode}): {sox_err}")

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
        ["asterisk", "-rx", f"rpt localplay {node} {output_path}"],
        check=True,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Announce Met Office inshore waters forecast on ASL3."
    )
    parser.add_argument("--type", choices=["forecast", "outlook"], default="forecast")
    parser.add_argument(
        "--region",
        choices=sorted(REGIONS),
        help=f"Forecast region (default: {DEFAULT_REGION} or GPS/env)",
    )
    parser.add_argument(
        "--section",
        help="Override Met Office section id (e.g. inshore-waters-19)",
    )
    parser.add_argument("--lat", type=float, help="Latitude for region lookup")
    parser.add_argument("--lon", type=float, help="Longitude for region lookup")
    parser.add_argument(
        "--gps",
        action="store_true",
        help="Read coordinates from local gpsd (127.0.0.1:2947)",
    )
    parser.add_argument(
        "--list-regions",
        action="store_true",
        help="List configured regions and exit",
    )
    parser.add_argument(
        "--list-sections",
        action="store_true",
        help="Fetch Met Office page and list live section ids",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser


def main():
    args = build_arg_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.list_regions:
        for key, cfg in sorted(REGIONS.items()):
            min_lat, max_lat, min_lon, max_lon = cfg["bbox"]
            print(
                f"{key}: {cfg['name']} [{cfg['section_id']}] "
                f"bbox=({min_lat},{max_lat},{min_lon},{max_lon})"
            )
        return

    if args.list_sections:
        html = _fetch_url(URL_METOFFICE)
        for item in discover_sections(html):
            print(f"{item['section_id']}: {item['title']}")
        return

    if (args.lat is None) ^ (args.lon is None):
        print("ERROR: --lat and --lon must be used together", file=sys.stderr)
        sys.exit(1)

    try:
        region_key, region_cfg = resolve_region_config(args)
    except Exception as e:
        print(f"ERROR (region): {e}", file=sys.stderr)
        sys.exit(1)

    if args.section:
        region_cfg = dict(region_cfg)
        region_cfg["section_override"] = args.section

    log.info("Using region %s (%s)", region_key, region_cfg["name"])

    try:
        html = _fetch_url(URL_METOFFICE)
        maritime, region_name = fetch_maritime(args.type, region_cfg, html=html)
    except Exception as e:
        print(f"ERROR (maritime fetch): {e}", file=sys.stderr)
        sys.exit(1)

    cfg = TYPES[args.type]
    temp = fetch_temperature(cfg["temp_day"], region_cfg, args.type)
    if temp is None and region_cfg.get("temp_source") == "gov.gg":
        print(
            "WARNING: gov.gg temperature unavailable — announcing without it",
            file=sys.stderr,
        )

    try:
        output_path = speak(args.type, maritime, temp, region_name, region_cfg)
        play(NODE, output_path)
        print(f"OK [{args.type}/{region_key}]: {maritime[:80]}...")
        if temp:
            print(f"   temp: {temp}")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
