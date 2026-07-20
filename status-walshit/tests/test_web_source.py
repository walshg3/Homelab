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
        scripts = re.findall(r'<script\s+([^>]*)></script>', self.html)
        self.assertEqual(scripts, ['src="navigation.js" defer'])
        self.assertNotRegex(self.html, r"(?i)<style\b")
        self.assertNotRegex(self.html, r"(?i)\sstyle\s*=")
        self.assertNotRegex(self.html, r"(?i)\son[a-z]+\s*=")

    def test_shared_header_identity_and_dom_order(self):
        header = re.search(r'<header class="shell">(.*?)</header>', self.html, re.S)
        self.assertIsNotNone(header)
        markup = header.group(1)
        brand = re.search(r'<a class="brand"([^>]*)>(.*?)</a>', markup, re.S)
        self.assertIsNotNone(brand)
        self.assertIn('href="https://walshit.com/"', brand.group(1))
        self.assertIn('aria-label="Walshit home"', brand.group(1))
        self.assertRegex(
            brand.group(2),
            r'^\s*WAL<span class="slash">/</span>SHIT\s*'
            r'<small>Service status<br>House report</small>\s*$',
        )
        direct_children = re.findall(
            r'^\s*<(a|button|nav)\b[^>]*(?:class="([^"]*)"|id="([^"]*)")',
            markup,
            re.M,
        )
        self.assertEqual(
            direct_children,
            [
                ("a", "brand", ""),
                ("button", "text-button nav-toggle", ""),
                ("nav", "", "status-navigation"),
            ],
        )
        toggle = re.search(r'<button class="text-button nav-toggle"([^>]*)>', markup)
        self.assertIsNotNone(toggle)
        for attribute in (
            'type="button"',
            'aria-expanded="false"',
            'aria-controls="status-navigation"',
            'aria-label="Open menu"',
        ):
            with self.subTest(attribute=attribute):
                self.assertIn(attribute, toggle.group(1))
        self.assertEqual(markup.count('class="nav-toggle-line"'), 3)
        navigation = re.search(
            r'<nav id="status-navigation" aria-label="Status navigation">(.*?)</nav>',
            markup,
            re.S,
        )
        self.assertIsNotNone(navigation)
        links = re.findall(r'<a href="([^"]+)">([^<]+)</a>', navigation.group(1))
        self.assertEqual(
            links,
            [
                ("https://walshit.com/", "Walshit.com"),
                ("#history", "Incident history"),
                ("https://walshit.com/updates/", "Updates"),
            ],
        )
        forbidden = ("brand-mark", "brand-name", "brand-slash")
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, f"{self.html}\n{self.css}")

    def test_navigation_disclosure_behavior_is_local_and_complete(self):
        path = WEB / "navigation.js"
        self.assertTrue(path.is_file(), "web/navigation.js must exist")
        source = path.read_text(encoding="utf-8")
        self.assertIn('window.matchMedia("(max-width: 1024px)")', source)
        self.assertRegex(source, r'\.hidden\s*=\s*compactNavigation\.matches\s*&&\s*!expanded')
        self.assertIn('setAttribute("aria-expanded", String(expanded))', source)
        self.assertIn('expanded ? "Close menu" : "Open menu"', source)
        self.assertRegex(source, r'navToggle\.addEventListener\("click"')
        self.assertRegex(source, r'statusNavigation\.addEventListener\("click"')
        self.assertIn('event.target.closest("a")', source)
        self.assertIn('!header.contains(event.target)', source)
        self.assertIn('event.key === "Escape"', source)
        self.assertIn('setNavigationOpen(false, true)', source)
        self.assertIn('if (returnFocus) navToggle.focus()', source)
        self.assertIn('compactNavigation.addEventListener("change", syncNavigation)', source)
        self.assertRegex(source, r'syncNavigation\(\);\s*$')
        self.assertNotRegex(
            source,
            r"(?i)fetch\s*\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|"
            r"\.innerHTML\b|showModal\s*\(|\.submit\s*\(",
        )
        assert_public_safe(source, source="web/navigation.js")

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
        self.assertEqual(self.html.count("History pending"), 5)
        self.assertEqual(self.html.count("Awaiting data"), 5)

    def test_assets_are_local_and_page_has_no_runtime_surface(self):
        self.assertNotRegex(self.css, r"(?i)@import|@font-face|url\s*\(\s*['\"]?https?://")
        self.assertEqual(self.css.count('url("data:image/svg+xml,'), 1)
        self.assertNotRegex(self.html, r"(?i)<(?:img|picture|source|iframe|object|embed)\b")
        self.assertNotRegex(self.html, r"(?i)<form\b|<input\b|<select\b|<textarea\b")
        self.assertRegex(
            self.html,
            r'<link\s+rel="stylesheet"\s+href="status\.css">',
        )
        links = re.findall(r'(?:href|src)="([^"]+)"', self.html)
        self.assertEqual(
            set(links),
            {
                "#main",
                "#history",
                "status.css",
                "navigation.js",
                "https://walshit.com/",
                "https://walshit.com/updates/",
            },
        )
        runtime_assets = re.findall(
            r'<(?:script|link)[^>]+(?:src|href)="([^"]+)"', self.html
        )
        self.assertEqual(runtime_assets, ["status.css", "navigation.js"])
        external_anchors = re.findall(r'<a[^>]+href="(https://[^"]+)"', self.html)
        self.assertEqual(
            external_anchors,
            [
                "https://walshit.com/",
                "https://walshit.com/",
                "https://walshit.com/updates/",
            ],
        )

    def test_canonical_theme_color_and_house_report_boundary(self):
        self.assertRegex(
            self.html,
            r'<meta\s+name="theme-color"\s+content="#f2ead9">',
        )
        required = (
            'class="overall" role="status" aria-live="polite"',
            "Status data is being prepared.",
            'class="component-ledger"',
            'class="ticket-event"',
            'class="history-section" id="history"',
            'class="coverage"',
            "No active-event data is available yet.",
            "No past availability or incident record is claimed by this shell.",
            "This source shell is not connected to monitoring yet.",
        )
        for item in required:
            with self.subTest(item=item):
                self.assertIn(item, self.html)

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
        self.assertIn('aria-label="Walshit home"', self.html)

    def test_event_and_history_shells_use_honest_non_live_language(self):
        self.assertIn('class="active-section"', self.html)
        self.assertIn('class="ticket-event"', self.html)
        self.assertIn('class="history-section"', self.html)
        self.assertIn('id="history"', self.html)
        self.assertIn("No active-event data is available yet.", self.html)
        self.assertIn("History will appear after monitoring begins.", self.html)
        self.assertNotRegex(self.html, r"\b\d{2,3}\.\d{1,2}%|90 days ago")

    def test_house_report_content_css_remains_present(self):
        self.assertIn(".component-ledger", self.css)
        self.assertIn(".ticket-event", self.css)
        self.assertIn(".overall", self.css)
        self.assertIn(".history-list", self.css)
        self.assertIn(".coverage", self.css)

    def test_canonical_shared_chrome(self):
        compact = re.sub(r"\s+", " ", self.css.lower())
        variables = {
            "--paper": "#f2ead9",
            "--paper-2": "#e5d8bd",
            "--ink": "#171713",
            "--red": "#c93424",
            "--yellow": "#f1bd38",
            "--green": "#39754d",
            "--white": "#ffffff",
            "--rule": "rgba(23, 23, 19, 0.28)",
            "--max": "1280px",
        }
        for name, value in variables.items():
            with self.subTest(variable=name):
                self.assertIn(f"{name}: {value}", compact)
        for retired in ("#f3ead7", "#e2d3b5", "#faf6ed", "#fffdf7", "1180px"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, compact)
        self.assertIn("font-size: 16px", compact)
        self.assertIn("line-height: 1.45", compact)
        self.assertIn(
            "radial-gradient(circle at 12% 8%, rgba(255, 255, 255, 0.45), transparent 22rem)",
            compact,
        )
        self.assertIn("body::before", compact)
        self.assertIn("position: fixed", compact)
        self.assertIn("opacity: 0.055", compact)
        self.assertIn("fractalnoise", compact)
        self.assertNotIn("body::after", compact)
        self.assertNotIn("repeating-linear-gradient(0deg", compact)
        self.assertNotIn("mix-blend-mode", compact)
        self.assertIn("width: min(calc(100% - 48px), var(--max))", compact)
        self.assertIn("min-height: 90px", compact)
        self.assertIn("border-bottom: 1px solid var(--ink)", compact)
        self.assertNotIn("min-height: 104px", compact)
        self.assertIn("outline: 3px solid var(--yellow)", compact)
        self.assertIn("outline-offset: 4px", compact)
        self.assertIn("font-size: clamp(1.55rem, 3vw, 2.15rem)", compact)
        self.assertIn("letter-spacing: -0.07em", compact)
        brand_rule = re.search(r"\.brand\s*\{([^}]*)\}", self.css, re.S)
        if brand_rule is None:
            self.fail(".brand CSS rule must exist")
        self.assertIn("min-height: 44px", brand_rule.group(1))
        self.assertIn(".brand .slash", compact)
        self.assertIn(".brand small", compact)
        self.assertIn("max-width: 9rem", compact)
        self.assertIn("min-height: 160px", compact)
        self.assertIn("font-size: 2rem", compact)
        self.assertIn('<p class="footer-mark">Walshit. Yes, really.</p>', self.html)
        self.assertIn("<p>Service status · House report</p>", self.html)
        footer_rule = re.search(r"footer\s*\{([^}]*)\}", self.css, re.S)
        self.assertIsNotNone(footer_rule)
        self.assertNotIn("border-top", footer_rule.group(1))

    def test_canonical_responsive_navigation_and_breakpoints(self):
        compact = re.sub(r"\s+", " ", self.css.lower())
        breakpoints = re.findall(r"@media\s*\(max-width:\s*(\d+)px\)", self.css)
        self.assertEqual(breakpoints, ["1024", "820", "460"])
        required = (
            "header { flex-wrap: wrap; align-content: center; padding-block: 16px; }",
            ".nav-toggle { display: inline-flex; margin-left: auto; }",
            "nav[hidden] { display: none; }",
            "flex: 0 0 100%",
            "min-height: 48px",
            "border-top: 1px dashed var(--rule)",
            "width: min(calc(100% - 32px), var(--max))",
            "header { min-height: 76px; }",
            ".brand small { display: none; }",
            "footer { align-items: flex-start; flex-direction: column;",
            "html { scroll-behavior: auto; }",
            "transition-duration: 0.01ms !important",
            ".nav-toggle-line { transition: none !important; }",
        )
        for declaration in required:
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, compact)
        self.assertNotIn(".utility-nav", compact)
        at_820 = compact.index("@media (max-width: 820px)")
        at_460 = compact.index("@media (max-width: 460px)")
        self.assertNotRegex(compact[at_820:at_460], r"footer \{[^}]*flex-direction")
        self.assertEqual(compact.count(".brand small { display: none; }"), 1)

    def test_static_web_source_is_public_safe(self):
        assert_public_safe(self.html, source="web/index.html")
        assert_public_safe(self.css, source="web/status.css")
        navigation = (WEB / "navigation.js").read_text(encoding="utf-8")
        assert_public_safe(navigation, source="web/navigation.js")


class HealthMarkerTests(unittest.TestCase):
    def test_healthz_content(self):
        health = (WEB / "healthz.txt").read_bytes()
        self.assertEqual(health, b"status-walshit-web-ok\n")


if __name__ == "__main__":
    unittest.main()
