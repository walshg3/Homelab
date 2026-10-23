"""Read-only checks for local artifact and secret exclusion boundaries.

Run from the repository root: python3 -m unittest discover -s tests -v
"""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RepositoryHygieneTests(unittest.TestCase):
    def ignored(self, path):
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", path],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode == 0

    def test_generated_original_posters_stay_local(self):
        for name in ("MOVIES - walshit.com", "TV - walshit.com"):
            with self.subTest(name=name):
                self.assertTrue(self.ignored(
                    f"kometa/config/overlays/{name} Original Posters/poster.png"
                ))

    def test_retained_landing_releases_are_opaque(self):
        for directory in (
            "walshit-landing-hugo", "walshit-landing-hugo-ca9a3df3-v2",
            "walshit-landing-live-9b7fbd68aa9e",
        ):
            with self.subTest(directory=directory):
                self.assertTrue(self.ignored(f"{directory}/compose.yaml"))
                self.assertTrue(self.ignored(f"{directory}/README.md"))

    def test_runtime_secrets_dependencies_and_nested_repo_stay_local(self):
        for path in (
            "dewarr/.env", "bookorbit/data/README.md",
            "maintainerr/data/compose.yaml", "notifications/data/README.md",
            "example/node_modules/package/README.md", "unpoller-unifi/compose.yaml",
        ):
            with self.subTest(path=path):
                self.assertTrue(self.ignored(path))

    def test_reviewed_definitions_and_hygiene_tests_are_visible(self):
        for path in (
            "bookorbit/compose.yaml", "dewarr/compose.yaml",
            "dewarr/compose.v034-candidate.yaml", "maintainerr/compose.yaml",
            "tests/test_repository_hygiene.py",
            "walshit-landing/hugo/content/guides/getting-started.md",
            ".github/workflows/walshit.yml",
        ):
            with self.subTest(path=path):
                self.assertFalse(self.ignored(path))

    def test_superseded_candidate_definitions_are_not_published(self):
        for path in (
            "bookorbit/requests.compose.yaml", "bookorbit/compose.reader-only.yaml",
            "dewarr/compose.before-sab5.yaml", "dewarr/compose.v031-candidate.yaml",
        ):
            with self.subTest(path=path):
                self.assertTrue(self.ignored(path))


if __name__ == "__main__":
    unittest.main()
