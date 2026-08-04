from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "hugo" / "static" / "styles.css"
FONTS = ROOT / "hugo" / "static" / "fonts"


class TypographyPreviewTest(unittest.TestCase):
    def test_self_hosted_fonts_and_licenses_exist(self):
        for name in (
            "libre-baskerville-variable.woff2",
            "libre-baskerville-italic-variable.woff2",
            "source-serif-4-variable.woff2",
            "source-serif-4-italic-variable.woff2",
            "archivo-narrow-variable.woff2",
            "archivo-narrow-italic-variable.woff2",
        ):
            path = FONTS / name
            self.assertTrue(path.is_file(), f"missing {path}")
            self.assertEqual(path.read_bytes()[:4], b"wOF2", f"{name} is not WOFF2")
        for name in ("libre-baskerville-OFL.txt", "source-serif-4-OFL.txt", "archivo-narrow-OFL.txt"):
            self.assertTrue((FONTS / "licenses" / name).is_file(), f"missing license {name}")

    def test_only_approved_font_assets_are_packaged(self):
        approved = {
            "archivo-narrow-italic-variable.woff2",
            "archivo-narrow-variable.woff2",
            "libre-baskerville-italic-variable.woff2",
            "libre-baskerville-variable.woff2",
            "source-serif-4-italic-variable.woff2",
            "source-serif-4-variable.woff2",
            "licenses/archivo-narrow-OFL.txt",
            "licenses/libre-baskerville-OFL.txt",
            "licenses/source-serif-4-OFL.txt",
        }
        packaged = {
            path.relative_to(FONTS).as_posix()
            for path in FONTS.rglob("*")
            if path.is_file()
        }
        self.assertEqual(packaged, approved)
        self.assertNotIn("Fraunces", CSS.read_text())

    def test_css_uses_explicit_typography_roles(self):
        css = CSS.read_text()
        self.assertIn('--display: "Libre Baskerville", Georgia, "Times New Roman", serif;', css)
        self.assertIn('--serif: "Source Serif 4", Georgia, "Times New Roman", serif;', css)
        self.assertIn('--sans: "Archivo Narrow", "Arial Narrow", "Aptos Narrow", sans-serif;', css)
        self.assertRegex(css, re.compile(r"h1\s*\{[^}]*font-family:\s*var\(--display\)", re.S))
        self.assertRegex(css, re.compile(r"h1\s*\{[^}]*letter-spacing:\s*-.02em", re.S))
        self.assertRegex(css, re.compile(r"h1\s*\{[^}]*line-height:\s*.92", re.S))
        self.assertRegex(css, re.compile(r"\.article-title\s*\{[^}]*max-width:\s*1120px", re.S))
        self.assertRegex(css, re.compile(r"\.article-title\s*\{[^}]*letter-spacing:\s*-.015em", re.S))
        self.assertRegex(css, re.compile(r"\.article-title\s*\{[^}]*line-height:\s*.98", re.S))
        self.assertRegex(css, re.compile(r"@media\s*\(max-width:\s*820px\).*?\.article-title\s*\{[^}]*font-size:\s*clamp\(2.65rem,\s*11.5vw,\s*4.25rem\)[^}]*line-height:\s*1", re.S))

    def test_display_font_is_preloaded(self):
        head = (ROOT / "hugo" / "layouts" / "partials" / "head.html").read_text()
        self.assertIn('rel="preload"', head)
        self.assertIn('fonts/libre-baskerville-variable.woff2', head)
        self.assertIn('as="font"', head)
        self.assertIn('type="font/woff2"', head)
        self.assertIn('crossorigin', head)

    def test_fonts_are_local_and_swap_safely(self):
        css = CSS.read_text()
        self.assertEqual(css.count("@font-face"), 6)
        self.assertEqual(css.count("font-display: swap"), 6)
        self.assertNotRegex(css, re.compile(r"https?://[^)]*(?:font|woff|ttf)", re.I))
        for name in (
            "libre-baskerville-variable.woff2",
            "libre-baskerville-italic-variable.woff2",
            "source-serif-4-variable.woff2",
            "source-serif-4-italic-variable.woff2",
            "archivo-narrow-variable.woff2",
            "archivo-narrow-italic-variable.woff2",
        ):
            self.assertIn(f'url("fonts/{name}") format("woff2")', css)


if __name__ == "__main__":
    unittest.main()
