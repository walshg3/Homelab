from pathlib import Path
import re
import unittest

from tests.leakcheck import assert_public_safe


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


class WebSourceTests(unittest.TestCase):
    def setUp(self):
        self.html = (WEB / "index.html").read_text(encoding="utf-8")
        self.css = (WEB / "status.css").read_text(encoding="utf-8")

    def test_no_inline_script_or_style(self):
        self.assertNotRegex(self.html, r"(?i)<script\b")
        self.assertNotRegex(self.html, r"(?i)<style\b")
        self.assertNotRegex(self.html, r"(?i)\sstyle\s*=")
        self.assertNotRegex(self.html, r"(?i)\son[a-z]+\s*=")

    def test_no_demo_or_inert_interface(self):
        forbidden = (
            "concept",
            "scenario",
            "synthetic",
            "demo",
            "mockup",
            "subscribe",
            "toast",
            "dialog",
            "inert-link",
            "radio",
        )
        source = f"{self.html}\n{self.css}".lower()
        for term in forbidden:
            with self.subTest(term=term):
                self.assertNotIn(term, source)

    def test_component_registry_names_and_placeholders_present(self):
        expected = {
            "watch": "Watch",
            "home": "Home base",
            "requests": "Requests",
            "audiobooks": "Audiobooks",
            "site": "Walshit.com",
        }
        ids = re.findall(r'data-component="([a-z]+)"', self.html)
        self.assertEqual(ids, list(expected))
        for name in expected.values():
            with self.subTest(name=name):
                self.assertIn(f"<h3>{name}</h3>", self.html)

    def test_assets_are_local_and_page_has_no_runtime_surface(self):
        self.assertNotRegex(f"{self.html}\n{self.css}", r"(?i)https?://|//[a-z0-9]")
        self.assertNotRegex(self.css, r"(?i)@import|@font-face|url\s*\(")
        self.assertNotRegex(self.html, r"(?i)<(?:img|picture|source|iframe|object|embed)\b")
        self.assertNotRegex(self.html, r"(?i)<form\b|<input\b|<button\b|<select\b|<textarea\b")
        self.assertRegex(
            self.html,
            r'<link\s+rel="stylesheet"\s+href="status\.css">',
        )
        links = re.findall(r'(?:href|src)="([^"]+)"', self.html)
        self.assertEqual(set(links), {"#main", "#history", "status.css"})

    def test_document_landmarks_and_accessible_status_message(self):
        self.assertIn("<title>Walshit.com / Status</title>", self.html)
        self.assertRegex(
            self.html,
            r'<meta\s+name="viewport"\s+content="width=device-width, initial-scale=1">',
        )
        self.assertEqual(len(re.findall(r"(?i)<h1\b", self.html)), 1)
        for landmark in ("header", "main", "footer"):
            with self.subTest(landmark=landmark):
                self.assertEqual(len(re.findall(fr"(?i)<{landmark}\b", self.html)), 1)
        self.assertIn('href="#main"', self.html)
        self.assertRegex(self.html, r'<main\s+id="main"[^>]*>')
        self.assertRegex(self.html, r'role="status"[^>]*aria-live="polite"')
        self.assertIn("Status data is being prepared.", self.html)
        self.assertIn('aria-label="Walshit.com status home"', self.html)

    def test_event_and_history_shells_use_honest_non_live_language(self):
        self.assertIn('class="active-section"', self.html)
        self.assertIn('class="ticket-event"', self.html)
        self.assertIn('class="history-section"', self.html)
        self.assertIn('id="history"', self.html)
        self.assertIn("No active-event data is available yet.", self.html)
        self.assertIn("History will appear after monitoring begins.", self.html)
        self.assertNotRegex(self.html, r"\b\d{2,3}\.\d{1,2}%|90 days ago")

    def test_house_report_css_is_responsive_and_keyboard_visible(self):
        for color in ("#f3ead7", "#171713", "#c93424", "#f1bd38"):
            with self.subTest(color=color):
                self.assertIn(color, self.css.lower())
        self.assertIn(":focus-visible", self.css)
        self.assertRegex(self.css, r"@media\s*\(max-width:\s*\d+px\)")
        self.assertIn(".component-ledger", self.css)
        self.assertIn(".ticket-event", self.css)

    def test_static_web_source_is_public_safe(self):
        assert_public_safe(self.html, source="web/index.html")
        assert_public_safe(self.css, source="web/status.css")


class HealthMarkerTests(unittest.TestCase):
    def test_healthz_content(self):
        health = (WEB / "healthz.txt").read_bytes()
        self.assertEqual(health, b"status-walshit-web-ok\n")


if __name__ == "__main__":
    unittest.main()
