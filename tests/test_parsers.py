#!/usr/bin/env python3
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_module():
    spec = importlib.util.spec_from_file_location("weather_forecast", ROOT / "weather-forecast.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wf = load_module()


class MetOfficeParserTests(unittest.TestCase):
    def test_multi_class_and_multi_paragraph(self):
        html = (FIXTURES / "metoffice_channel_islands.html").read_text()
        parser = wf.MetOfficeParser(
            "inshore-waters-19", "forecast-block", r"24 hour forecast:\s*"
        )
        parser.feed(html)
        self.assertEqual(parser.region_title, "Channel Islands")
        self.assertIn("Variable 1 to 3", parser.text)
        self.assertIn("Second paragraph", parser.text)

    def test_outlook_block(self):
        html = (FIXTURES / "metoffice_channel_islands.html").read_text()
        parser = wf.MetOfficeParser(
            "inshore-waters-19",
            "outlook-block",
            r"Outlook for the following 24 hours:\s*",
        )
        parser.feed(html)
        self.assertIn("West to southwest", parser.text)

    def test_discover_sections(self):
        html = (FIXTURES / "metoffice_channel_islands.html").read_text()
        sections = wf.discover_sections(html)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]["section_id"], "inshore-waters-19")
        self.assertEqual(sections[0]["title"], "Channel Islands")

    def test_title_fallback_when_section_id_changes(self):
        html = """
        <section id="inshore-waters-99"><h3>Channel Islands</h3>
        <div class="forecast-block"><p>24 hour forecast: Wind northwest 3.</p></div>
        </section>
        """
        cfg = wf.REGIONS["channel-islands"]
        section_id = wf.resolve_section_id(html, cfg)
        self.assertEqual(section_id, "inshore-waters-99")


class GovGGParserTests(unittest.TestCase):
    def test_today_and_tomorrow(self):
        html = (FIXTURES / "govgg_weather.html").read_text()
        parser = wf.GovGGParser()
        parser.feed(html)
        self.assertEqual(parser.data["today"]["summary"], "Sunny periods.")
        self.assertEqual(parser.data["today"]["high"], "26")
        self.assertEqual(parser.data["today"]["low"], "16")
        self.assertEqual(parser.data["tomorrow"]["high"], "21")


class RegionTests(unittest.TestCase):
    def test_guernsey_coords(self):
        key = wf.resolve_region_from_coords(49.45, -2.54)
        self.assertEqual(key, "channel-islands")

    def test_smallest_bbox_wins(self):
        # Point inside both a large mainland box and Isle of Man
        key = wf.resolve_region_from_coords(54.15, -4.48)
        self.assertEqual(key, "isle-of-man")


if __name__ == "__main__":
    unittest.main()
