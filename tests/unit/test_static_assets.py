"""Vendored Chart.js and font, no off-site assets, no inline scripts (tasks 2.5 and T1)."""

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app" / "static"
TEMPLATES = sorted((ROOT / "app" / "templates").rglob("*.html"))
OWN_FILES = [*sorted(STATIC.glob("*.css")), STATIC / "charts.js", STATIC / "map.js"]


def test_vendored_chart_js_matches_the_recorded_hash():
    readme = (STATIC / "vendor" / "README.md").read_text(encoding="utf-8")
    recorded = re.search(r"checked by .*?: `([0-9a-f]{64})`", readme).group(1)
    assert hashlib.sha256((STATIC / "vendor" / "chart.umd.js").read_bytes()).hexdigest() == recorded
    assert (STATIC / "vendor" / "chart.umd.js").read_bytes().startswith(b"/*!\n * Chart.js v4.4.7")


def test_vendored_font_matches_the_recorded_hash_and_is_used():
    readme = (STATIC / "vendor" / "README.md").read_text(encoding="utf-8")
    recorded = re.search(r"Font SHA-256 in hex \(checked by .*?: `([0-9a-f]{64})`", readme).group(1)
    font = STATIC / "vendor" / "figtree-latin.woff2"
    assert hashlib.sha256(font.read_bytes()).hexdigest() == recorded
    assert font.read_bytes().startswith(b"wOF2")
    assert 'url("vendor/figtree-latin.woff2")' in (STATIC / "app.css").read_text(encoding="utf-8")
    licence = (STATIC / "vendor" / "LICENSE.figtree-OFL.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE" in licence.upper()


def test_vendored_leaflet_matches_the_recorded_hashes():
    readme = (STATIC / "vendor" / "README.md").read_text(encoding="utf-8")
    line = re.search(r"Leaflet SHA-256 in hex \(checked by .*", readme).group(0)
    recorded = dict(re.findall(r"`(leaflet\.(?:js|css))` `([0-9a-f]{64})`", line))
    assert set(recorded) == {"leaflet.js", "leaflet.css"}
    for name, digest in recorded.items():
        assert hashlib.sha256((STATIC / "vendor" / name).read_bytes()).hexdigest() == digest, name
    assert (STATIC / "vendor" / "leaflet.js").read_bytes().startswith(b"/* @preserve\n * Leaflet 1.9.4")
    assert "BSD 2-Clause" in (STATIC / "vendor" / "LICENSE.leaflet.txt").read_text(encoding="utf-8")


def test_both_licence_files_exist():
    for name in ("LICENSE.chartjs.txt", "LICENSE.kurkle-color.txt"):
        text = (STATIC / "vendor" / name).read_text(encoding="utf-8")
        assert "MIT License" in text and "Permission is hereby granted" in text


class Scan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.problems = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for name in a:
            if name.startswith("on"):
                self.problems.append(f"{tag} has attribute {name}")
        if tag == "script" and "src" not in a and a.get("type") != "application/json":
            self.problems.append("inline script")
        for url in (a.get("src"),) + ((a.get("href"),) if tag == "link" or tag == "script" else ()):
            if url and re.match(r"(?i)(https?:)?//", url):
                self.problems.append(f"{tag} points off site: {url}")


def test_templates_have_no_inline_scripts_or_event_attributes_or_offsite_assets():
    assert TEMPLATES
    for path in TEMPLATES:
        scan = Scan()
        scan.feed(path.read_text(encoding="utf-8"))
        assert scan.problems == [], (path.name, scan.problems)


def test_css_scrolls_wrappers_not_tables():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".table-wrap { overflow-x: auto; }" in css
    assert not re.search(r"(?m)^\s*table\s*\{[^}]*display\s*:\s*block", css)


def test_the_checker_catches_what_it_should():
    scan = Scan()
    scan.feed(
        '<script>x()</script><a onclick="x()">a</a><script src="https://cdn.example/x.js"></script>'
        '<link rel="stylesheet" href="//cdn.example/a.css"><script type="application/json">{}</script>'
    )
    assert len(scan.problems) == 4


def test_own_css_and_js_load_nothing_off_site():
    for path in OWN_FILES:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?i)url\(\s*['\"]?\s*(https?:)?//", text), path.name
        assert not re.search(r"(?i)@import", text), path.name
        assert not re.search(r"(?i)(src|href)\s*=\s*['\"](https?:)?//", text), path.name
        assert not re.search(r"https?://", text), path.name
