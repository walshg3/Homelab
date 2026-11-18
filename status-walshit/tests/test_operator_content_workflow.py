import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from publisher.content import load_content


ROOT = Path(__file__).resolve().parents[1]
INCIDENT_TEMPLATE = (
    ROOT / "content" / "incidents" / "2026-XX-XX-example.toml.example"
)
MAINTENANCE_TEMPLATE = (
    ROOT / "content" / "maintenance" / "maintenance-template.toml.example"
)


def run_validator(*arguments):
    return subprocess.run(
        [sys.executable, "-m", "operations.validate_content", *arguments],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


class OperatorContentWorkflowTests(unittest.TestCase):
    def test_default_validator_reports_only_repository_content_counts(self):
        completed = run_validator()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout,
            "active_events=0\nclosed_events=0\nissues=0\n",
        )
        self.assertEqual(completed.stderr, "")

    def test_validator_uses_only_sanitized_issue_identity_and_code(self):
        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            (content_root / "incidents").mkdir()
            (content_root / "maintenance").mkdir()
            raw_name = "private-event.toml"
            sentinel = "NARRATIVE_SENTINEL_SHOULD_NOT_PRINT"
            (content_root / "incidents" / raw_name).write_text(
                f'title = "{sentinel}"\n', encoding="utf-8"
            )

            completed = run_validator(os.fspath(content_root))

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stderr, "")
        self.assertIn("active_events=0\nclosed_events=0\nissues=1\n", completed.stdout)
        self.assertRegex(
            completed.stdout,
            r"issue incidents/file-[0-9a-f]{12}\.toml invalid_content\n$",
        )
        self.assertNotIn(raw_name, completed.stdout)
        self.assertNotIn(sentinel, completed.stdout)

    def test_templates_copy_as_valid_complete_closed_examples(self):
        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incidents = content_root / "incidents"
            maintenance = content_root / "maintenance"
            incidents.mkdir()
            maintenance.mkdir()
            shutil.copyfile(INCIDENT_TEMPLATE, incidents / "incident-example.toml")
            shutil.copyfile(
                MAINTENANCE_TEMPLATE, maintenance / "maintenance-example.toml"
            )

            result = load_content(content_root)
            completed = run_validator(os.fspath(content_root))

        self.assertEqual(result.issues, ())
        self.assertEqual(result.active_events, ())
        self.assertEqual(len(result.closed_events), 2)
        events = {event.kind: event for event in result.closed_events}
        self.assertEqual(
            [update.status for update in events["incident"].updates],
            ["investigating", "identified", "monitoring", "resolved"],
        )
        self.assertEqual(
            [update.status for update in events["maintenance"].updates],
            ["planned", "in-progress", "completed"],
        )
        for event in events.values():
            self.assertEqual(event.resolved_at, event.updated_at)
            self.assertRegex(event.started_at, r"Z$")
            self.assertNotRegex(event.started_at, r"\.\d+Z$")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout,
            "active_events=0\nclosed_events=2\nissues=0\n",
        )

    def test_templates_document_edits_and_exact_component_vocabulary(self):
        combined = (
            INCIDENT_TEMPLATE.read_text(encoding="utf-8")
            + MAINTENANCE_TEMPLATE.read_text(encoding="utf-8")
        ).lower()
        for token in (
            "opening",
            "update",
            "closure",
            "resolved_at",
            "final update",
            '"watch"',
            '"home"',
            '"requests"',
            '"audiobooks"',
            '"site"',
        ):
            self.assertIn(token, combined)
        timestamps = re.findall(r"\d{4}-\d{2}-\d{2}t\d{2}:\d{2}:\d{2}z", combined)
        self.assertGreaterEqual(len(timestamps), 9)
        self.assertNotRegex(combined, r"\d{2}:\d{2}:\d{2}\.\d+z")

    def test_operator_docs_define_promotion_privacy_lifecycle_and_rollback(self):
        docs = (
            (ROOT / "README.md").read_text(encoding="utf-8")
            + (ROOT / "runbooks" / "operations.md").read_text(encoding="utf-8")
        ).lower()
        ordered_terms = (
            "ntfy",
            "human decision",
            "copy",
            "validator",
            "reviewed git",
            "publisher cycle",
            "summary.json",
            "incidents.json",
            "resolve",
            "archive",
        )
        positions = [docs.find(term) for term in ordered_terms]
        self.assertTrue(all(position >= 0 for position in positions), positions)
        self.assertEqual(positions, sorted(positions))
        for term in (
            "credentials",
            "private hosts",
            "private addresses",
            "infrastructure details",
            "media identities",
            "media titles",
            "raw probe output",
            "recovery procedures",
            "investigating",
            "identified",
            "monitoring",
            "resolved",
            "planned",
            "in-progress",
            "completed",
            "resolved_at",
            "automatic component state",
            "manual narration",
            "pause",
            "retain",
            "source revert",
        ):
            self.assertIn(term, docs)
        self.assertIn("cd status-walshit", docs)
        self.assertIn("python3 -m operations.validate_content", docs)

    def test_docs_review_exact_untracked_file_with_expected_diff_exit(self):
        docs = (ROOT / "runbooks" / "operations.md").read_text(encoding="utf-8")
        section = docs[docs.index("## Incident and Maintenance content") :]
        normalized = " ".join(section.split()).lower()

        self.assertIn(
            'git diff --no-index -- /dev/null "$CONTENT_FILE"',
            section,
        )
        self.assertIn("exit status `1`", normalized)
        self.assertIn("expected", normalized)
        self.assertIn(
            "normal `git diff` does not display an untracked file",
            normalized,
        )
        self.assertIn(
            "Before staging, review the exact new file",
            section,
        )

    def test_docs_install_schema_package_and_precreate_exact_secret_path(self):
        docs = (ROOT / "runbooks" / "operations.md").read_text(encoding="utf-8")

        for source in (
            "operations/soak_watchdog.py",
            "publisher/schema.py",
            "publisher/__init__.py",
        ):
            self.assertIn(source, docs)
        create = 'install -m 600 /dev/null "$WATCHDOG_SECRET_FILE"'
        edit = '${EDITOR:?set an interactive editor} "$WATCHDOG_SECRET_FILE"'
        self.assertIn(create, docs)
        self.assertIn(edit, docs)
        self.assertLess(docs.index(create), docs.index(edit))

    def test_docs_disclose_delivery_and_runtime_parent_limits(self):
        docs = (ROOT / "runbooks" / "operations.md").read_text(encoding="utf-8").lower()
        normalized = " ".join(docs.split())

        for phrase in (
            "at-least-once",
            "may duplicate a transition",
            "does not provide exactly-once delivery",
            "residual parent-path race",
            "dedicated profile-owned runtime directory",
        ):
            self.assertIn(phrase, normalized)

    def test_example_destination_filenames_are_lowercase(self):
        docs = (ROOT / "runbooks" / "operations.md").read_text(encoding="utf-8")

        self.assertNotIn("YYYY-MM-DD-short-name.toml", docs)
        self.assertIn("2026-07-22-short-name.toml", docs)


if __name__ == "__main__":
    unittest.main()
