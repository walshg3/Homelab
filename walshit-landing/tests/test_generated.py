#!/usr/bin/env python3
"""Real Hugo build and generated-output acceptance tests.

Run from walshit-landing/ with an explicitly verified Hugo binary:
    HUGO_BIN=/absolute/path/to/hugo python3 tests/test_generated.py -v
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
HUGO_SOURCE = ROOT / "hugo"
VALIDATOR = ROOT / "scripts" / "validate_generated.py"


class GeneratedSiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hugo = os.environ.get("HUGO_BIN")
        if not hugo:
            raise RuntimeError("HUGO_BIN is required and must point to verified Hugo v0.164.0")
        cls.hugo = Path(hugo)
        if not cls.hugo.is_file():
            raise RuntimeError(f"HUGO_BIN does not exist: {cls.hugo}")
        version = subprocess.run(
            [str(cls.hugo), "version"],
            text=True,
            capture_output=True,
            check=False,
        )
        if version.returncode or "v0.164.0" not in (version.stdout + version.stderr):
            raise RuntimeError(
                "HUGO_BIN must be Hugo v0.164.0; got: "
                f"{(version.stdout + version.stderr).strip()!r}"
            )
        cls.hugo_version = version.stdout + version.stderr
        cls.temp = tempfile.TemporaryDirectory(prefix="walshit-hugo-test-")
        cls.public = Path(cls.temp.name) / "public"
        proc = subprocess.run(
            [
                str(cls.hugo),
                "--source",
                str(HUGO_SOURCE),
                "--minify",
                "--gc",
                "--cleanDestinationDir",
                "--destination",
                str(cls.public),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        cls.build_output = proc.stdout + proc.stderr
        if proc.returncode:
            raise RuntimeError(f"Hugo build failed ({proc.returncode}):\n{cls.build_output}")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temp"):
            cls.temp.cleanup()

    def test_hugo_build_is_warning_free(self):
        self.assertIn("v0.164.0", self.hugo_version)
        self.assertNotIn("WARN", self.build_output, self.build_output)

    def test_generated_validator_passes_real_output(self):
        self.assertTrue(VALIDATOR.is_file(), f"missing generated-output validator: {VALIDATOR}")
        proc = subprocess.run(
            [sys.executable, str(VALIDATOR), "--public-dir", str(self.public)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("generated output validation passed", proc.stdout)

    def test_updates_index_renders_current_section_verbiage(self):
        generated = (self.public / "updates" / "index.html").read_text()
        self.assertIn("Active incidents are posted to", generated)
        self.assertIn("https://status.walshit.com", generated)
        self.assertNotIn("ntfy/Discord and Framerr status", generated)

    def test_header_support_control_is_generated_on_every_page(self):
        pages = sorted(self.public.rglob("*.html"))
        self.assertTrue(pages)
        for page in pages:
            generated = page.read_text()
            self.assertEqual(generated.count("https://buymeacoffee.com/gregwalsh"), 1, page)
            self.assertIn("Buy me a Coffee", generated, page)
            self.assertIn("buy-me-a-coffee-logo.png?v=20260718-3", generated, page)
            self.assertNotIn("Buy Greg", generated, page)
            self.assertLess(generated.index("help-trigger"), generated.index("coffee-nav"), page)
            self.assertIn('class="text-button nav-toggle"', generated, page)
            self.assertRegex(generated, r'aria-controls=["\']?primary-navigation["\']?', page)
            self.assertRegex(generated, r'id=["\']?primary-navigation["\']?', page)


if __name__ == "__main__":
    unittest.main()
