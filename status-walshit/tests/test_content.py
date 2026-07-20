import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import textwrap
import unittest
from unittest.mock import patch

from publisher import content as content_module
from publisher.content import ContentIssue, ContentLoadResult, load_content
from publisher.schema import validate_incidents, validate_summary
from tests.leakcheck import assert_public_safe


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def source_text(
    *,
    event_id="inc-2026-07-20-example",
    kind="incident",
    phase="investigating",
    started_at="2026-07-20T12:00:00Z",
    update_at="2026-07-20T12:05:00Z",
    extra="",
):
    return textwrap.dedent(
        f'''\
        id = "{event_id}"
        kind = "{kind}"
        title = "Public service interruption"
        body = "Some visitors may be unable to use this public component."
        severity = "medium"
        started_at = {started_at}
        affected_components = ["watch"]
        {extra}
        [[updates]]
        at = {update_at}
        phase = "{phase}"
        body = "Public checks are not completing consistently."
        '''
    )


def load_single(raw, *, category="incidents", filename="event.toml"):
    temporary = TemporaryDirectory()
    content_root = Path(temporary.name)
    directory = content_root / category
    directory.mkdir()
    (directory / filename).write_text(raw, encoding="utf-8")
    return temporary, content_root, load_content(content_root)


def source_with_updates(count):
    lines = [
        'id = "inc-2026-07-20-many-updates"',
        'kind = "incident"',
        'title = "Public service interruption"',
        'body = "Some visitors may be unable to use this public component."',
        'severity = "medium"',
        'started_at = 2026-07-20T12:00:00Z',
        'affected_components = ["watch"]',
    ]
    start = datetime(2026, 7, 20, 12, 1, tzinfo=timezone.utc)
    for index in range(count):
        at = (start + timedelta(minutes=index)).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.extend(
            (
                "[[updates]]",
                f"at = {at}",
                'phase = "investigating"',
                'body = "Public checks are not completing consistently."',
            )
        )
    return "\n".join(lines) + "\n"


class ContentLoaderTests(unittest.TestCase):
    def test_publisher_content_imports_without_the_tests_package(self):
        with TemporaryDirectory() as temporary:
            isolated_root = Path(temporary)
            shutil.copytree(ROOT / "publisher", isolated_root / "publisher")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    (
                        "import sys; "
                        f"sys.path.insert(0, {str(isolated_root)!r}); "
                        "import publisher.content"
                    ),
                ],
                cwd=isolated_root,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_active_incident_maps_to_summary_event(self):
        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            incident_dir.mkdir()
            (incident_dir / "2026-07-20-example.toml").write_text(
                source_text(), encoding="utf-8"
            )

            result = load_content(content_root)

        self.assertIsInstance(result, ContentLoadResult)
        self.assertEqual(result.issues, ())
        self.assertEqual(result.closed_events, ())
        self.assertEqual(len(result.active_events), 1)
        event = result.active_event_documents()[0]
        self.assertEqual(
            event,
            {
                "id": "inc-2026-07-20-example",
                "kind": "incident",
                "status": "investigating",
                "title": "Public service interruption",
                "body": "Some visitors may be unable to use this public component.",
                "started_at": "2026-07-20T12:00:00Z",
                "updated_at": "2026-07-20T12:05:00Z",
                "affected_components": ["watch"],
                "updates": [
                    {
                        "at": "2026-07-20T12:05:00Z",
                        "status": "investigating",
                        "body": "Public checks are not completing consistently.",
                    }
                ],
            },
        )
        summary = json.loads(
            (FIXTURES / "summary.operational.json").read_text(encoding="utf-8")
        )
        summary["generated_at"] = "2026-07-21T00:00:00Z"
        summary["active_events"] = [event]
        validate_summary(summary)

    def test_active_maintenance_maps_and_quoted_offset_normalizes_to_utc(self):
        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            maintenance_dir = content_root / "maintenance"
            maintenance_dir.mkdir()
            (maintenance_dir / "2026-07-20-care.toml").write_text(
                source_text(
                    event_id="maint-2026-07-20-care",
                    kind="maintenance",
                    phase="in-progress",
                    started_at='"2026-07-20T14:00:00+02:00"',
                    update_at='"2026-07-20T14:05:00+02:00"',
                ),
                encoding="utf-8",
            )

            result = load_content(content_root)

        self.assertEqual(result.issues, ())
        self.assertEqual(result.active_events[0].kind, "maintenance")
        self.assertEqual(result.active_events[0].status, "in-progress")
        self.assertEqual(result.active_events[0].started_at, "2026-07-20T12:00:00Z")
        self.assertEqual(result.active_events[0].updated_at, "2026-07-20T12:05:00Z")

    def test_closed_incident_and_completed_maintenance_route_to_closed_events(self):
        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            maintenance_dir = content_root / "maintenance"
            incident_dir.mkdir()
            maintenance_dir.mkdir()
            (incident_dir / "2026-07-20-closed.toml").write_text(
                source_text(
                    event_id="inc-2026-07-20-closed",
                    phase="resolved",
                    update_at="2026-07-20T12:30:00Z",
                    extra="resolved_at = 2026-07-20T12:30:00Z",
                ),
                encoding="utf-8",
            )
            (maintenance_dir / "2026-07-20-complete.toml").write_text(
                source_text(
                    event_id="maint-2026-07-20-complete",
                    kind="maintenance",
                    phase="completed",
                    update_at="2026-07-20T12:45:00Z",
                    extra="resolved_at = 2026-07-20T12:45:00Z",
                ),
                encoding="utf-8",
            )

            result = load_content(content_root)

        self.assertEqual(result.issues, ())
        self.assertEqual(result.active_events, ())
        self.assertEqual(
            [event.id for event in result.closed_events],
            ["maint-2026-07-20-complete", "inc-2026-07-20-closed"],
        )
        self.assertNotIn("severity", result.closed_event_documents()[0])
        validate_incidents(
            {
                "schema_version": "1",
                "generated_at": "2026-07-20T13:00:00Z",
                "events": result.closed_event_documents(),
            }
        )

    def test_duplicate_id_skips_every_source_even_when_one_is_otherwise_invalid(self):
        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            maintenance_dir = content_root / "maintenance"
            incident_dir.mkdir()
            maintenance_dir.mkdir()
            duplicate_id = "event-2026-07-20-duplicate"
            (incident_dir / "a.toml").write_text(
                source_text(event_id=duplicate_id), encoding="utf-8"
            )
            (maintenance_dir / "b.toml").write_text(
                source_text(
                    event_id=duplicate_id,
                    kind="maintenance",
                    phase="planned",
                ).replace('severity = "medium"', 'severity = "critical"'),
                encoding="utf-8",
            )

            result = load_content(content_root)

        self.assertEqual(result.active_events, ())
        self.assertEqual(result.closed_events, ())
        self.assertEqual(
            [issue.code for issue in result.issues],
            ["duplicate_id", "duplicate_id", "invalid_content"],
        )
        self.assertRegex(
            result.issues[0].relative_filename,
            r"^incidents/file-[0-9a-f]{12}\.toml$",
        )
        self.assertEqual(
            result.issues[1].relative_filename, result.issues[2].relative_filename
        )
        self.assertRegex(
            result.issues[1].relative_filename,
            r"^maintenance/file-[0-9a-f]{12}\.toml$",
        )

    def test_duplicate_id_discovery_precedes_every_recoverable_rejection(self):
        duplicate_id = "event-2026-07-20-matrix"
        cases = {
            "parseable unsafe": (
                "unsafe_content",
                source_text(event_id=duplicate_id).replace(
                    'body = "Some visitors may be unable to use this public component."',
                    'body = "client_secret = syntheticvalue"',
                ),
                "b.toml",
                "incidents",
            ),
            "malformed after id": (
                "invalid_toml",
                source_text(event_id=duplicate_id) + "broken = [\n",
                "b.toml",
                "incidents",
            ),
            "invalid filename": (
                "invalid_filename",
                source_text(event_id=duplicate_id),
                "Bad Name.toml",
                "incidents",
            ),
            "invalid contract": (
                "invalid_content",
                source_text(event_id=duplicate_id).replace(
                    'severity = "medium"', 'severity = "critical"'
                ),
                "b.toml",
                "incidents",
            ),
            "cross category": (
                None,
                source_text(
                    event_id=duplicate_id,
                    kind="maintenance",
                    phase="planned",
                ),
                "b.toml",
                "maintenance",
            ),
        }
        for label, (own_code, second_raw, second_filename, second_category) in cases.items():
            with self.subTest(label=label), TemporaryDirectory() as temporary:
                content_root = Path(temporary)
                (content_root / "incidents").mkdir()
                (content_root / "incidents" / "a.toml").write_text(
                    source_text(event_id=duplicate_id), encoding="utf-8"
                )
                second_directory = content_root / second_category
                second_directory.mkdir(exist_ok=True)
                (second_directory / second_filename).write_text(
                    second_raw, encoding="utf-8"
                )

                result = load_content(content_root)

            self.assertEqual(result.active_events, ())
            self.assertEqual(result.closed_events, ())
            codes = [issue.code for issue in result.issues]
            self.assertEqual(codes.count("duplicate_id"), 2)
            if own_code is not None:
                self.assertIn(own_code, codes)

    def test_malformed_id_recovery_ignores_comments_and_near_matches(self):
        event_id = "event-2026-07-20-not-a-duplicate"
        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            incident_dir.mkdir()
            (incident_dir / "a.toml").write_text(
                source_text(event_id=event_id), encoding="utf-8"
            )
            (incident_dir / "b.toml").write_text(
                textwrap.dedent(
                    f'''\
                    # id = "{event_id}"
                    event_id = "{event_id}"
                    id_suffix = "{event_id}"
                    broken = [
                    '''
                ),
                encoding="utf-8",
            )

            result = load_content(content_root)

        self.assertEqual([event.id for event in result.active_events], [event_id])
        self.assertEqual([issue.code for issue in result.issues], ["invalid_toml"])

    def test_malformed_id_recovery_requires_id_as_the_first_source_assignment(self):
        event_id = "event-2026-07-20-not-nested"
        malformed_sources = (
            textwrap.dedent(
                f'''\
                kind = "incident"
                body = """
                id = "{event_id}"
                '''
            ),
            textwrap.dedent(
                f'''\
                kind = "incident"
                [[updates]]
                id = "{event_id}"
                broken = [
                '''
            ),
        )
        for raw in malformed_sources:
            with self.subTest(raw=raw), TemporaryDirectory() as temporary:
                content_root = Path(temporary)
                incident_dir = content_root / "incidents"
                incident_dir.mkdir()
                (incident_dir / "a.toml").write_text(
                    source_text(event_id=event_id), encoding="utf-8"
                )
                (incident_dir / "b.toml").write_text(raw, encoding="utf-8")

                result = load_content(content_root)

            self.assertEqual([event.id for event in result.active_events], [event_id])
            self.assertEqual([issue.code for issue in result.issues], ["invalid_toml"])

        reordered = source_text(event_id=event_id).replace(
            f'id = "{event_id}"\nkind = "incident"',
            f'kind = "incident"\nid = "{event_id}"',
        )
        temporary, _, result = load_single(reordered)
        self.addCleanup(temporary.cleanup)
        self.assertEqual([event.id for event in result.active_events], [event_id])
        self.assertEqual(result.issues, ())

    def test_unreadable_and_oversized_sources_conservatively_cannot_supply_an_id(self):
        duplicate_id = "event-2026-07-20-conservative"
        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            incident_dir.mkdir()
            (incident_dir / "a.toml").write_text(
                source_text(event_id=duplicate_id), encoding="utf-8"
            )
            (incident_dir / "unreadable.toml").write_text(
                source_text(event_id=duplicate_id), encoding="utf-8"
            )
            real_open = os.open

            def selective_open(path, flags, *args, **kwargs):
                if path == "unreadable.toml":
                    raise OSError("synthetic unavailable file")
                return real_open(path, flags, *args, **kwargs)

            with patch("publisher.content.os.open", side_effect=selective_open):
                unreadable = load_content(content_root)

        self.assertEqual([event.id for event in unreadable.active_events], [duplicate_id])
        self.assertEqual([issue.code for issue in unreadable.issues], ["file_unavailable"])

        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            incident_dir.mkdir()
            (incident_dir / "a.toml").write_text(
                source_text(event_id=duplicate_id), encoding="utf-8"
            )
            oversized_raw = source_text(event_id=duplicate_id) + (
                "#" * (64 * 1024)
            )
            (incident_dir / "oversized.toml").write_text(oversized_raw, encoding="utf-8")
            oversized = load_content(content_root)

        self.assertEqual([event.id for event in oversized.active_events], [duplicate_id])
        self.assertEqual([issue.code for issue in oversized.issues], ["file_too_large"])

    def test_fractional_source_timestamps_are_rejected_without_truncation(self):
        cases = {
            "sub-microsecond quoted start": source_text(
                started_at='"2026-07-20T12:00:00.0000001Z"'
            ),
            "sub-microsecond bare start": source_text(
                started_at="2026-07-20T12:00:00.0000001Z"
            ),
            "sub-microsecond bare update": source_text(
                update_at="2026-07-20T12:05:00.0000001Z"
            ),
            "sub-microsecond quoted resolved": source_text(
                phase="resolved",
                update_at='"2026-07-20T12:30:00Z"',
                extra='resolved_at = "2026-07-20T12:30:00.0000001Z"',
            ),
            "fractional start": source_text(
                started_at='"2026-07-20T12:00:00.100Z"'
            ),
            "fractional update": source_text(
                update_at='"2026-07-20T12:05:00.100Z"'
            ),
            "fractional resolved": source_text(
                phase="resolved",
                update_at='"2026-07-20T12:30:00Z"',
                extra='resolved_at = "2026-07-20T12:30:00.100Z"',
            ),
            "same-second reverse": source_text(
                started_at='"2026-07-20T12:00:00.900Z"',
                update_at='"2026-07-20T12:00:00.100Z"',
            ),
            "distinct sub-second updates": source_text(
                update_at='"2026-07-20T12:05:00.100Z"'
            )
            + textwrap.dedent(
                '''\
                [[updates]]
                at = "2026-07-20T12:05:00.900Z"
                phase = "monitoring"
                body = "Public checks remain under observation."
                '''
            ),
        }
        for label, raw in cases.items():
            with self.subTest(label=label):
                temporary, _, result = load_single(raw)
                self.addCleanup(temporary.cleanup)
                self.assertEqual(result.active_events, ())
                self.assertEqual(result.closed_events, ())
                self.assertEqual(result.issues[0].code, "invalid_content")

    def test_timestamp_source_check_ignores_narrative_and_allows_zero_fraction(self):
        original_body = 'body = "Some visitors may be unable to use this public component."'
        narrative = source_text().replace(
            original_body,
            textwrap.dedent(
                '''\
                body = """
                A timestamp-shaped example remains ordinary public prose:
                at = 2026-07-20T12:00:00.0000001Z
                """
                '''
            ).rstrip(),
        )
        zero_fraction = source_text(
            started_at="2026-07-20T12:00:00.0000000Z",
            update_at='"2026-07-20T12:05:00.0000000Z"',
        )
        for raw in (narrative, zero_fraction):
            with self.subTest(raw=raw):
                temporary, _, result = load_single(raw)
                self.addCleanup(temporary.cleanup)
                self.assertEqual(len(result.active_events), 1)
                self.assertEqual(result.issues, ())

    def test_loader_result_is_deeply_immutable_and_projects_fresh_documents(self):
        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            incident_dir.mkdir()
            (incident_dir / "active.toml").write_text(source_text(), encoding="utf-8")
            (incident_dir / "closed.toml").write_text(
                source_text(
                    event_id="inc-2026-07-20-closed",
                    phase="resolved",
                    update_at="2026-07-20T12:30:00Z",
                    extra="resolved_at = 2026-07-20T12:30:00Z",
                ),
                encoding="utf-8",
            )
            result = load_content(content_root)

        active = result.active_events[0]
        with self.assertRaises(FrozenInstanceError):
            active.title = "Changed"
        with self.assertRaises((AttributeError, TypeError)):
            active.affected_components[0] = "site"
        with self.assertRaises(FrozenInstanceError):
            active.updates[0].body = "Changed"

        first_active = result.active_event_documents()
        first_closed = result.closed_event_documents()
        self.assertNotIn("resolved_at", first_active[0])
        self.assertIn("resolved_at", first_closed[0])
        first_active[0]["title"] = "Changed"
        first_active[0]["updates"][0]["body"] = "Changed"
        second_active = result.active_event_documents()
        self.assertEqual(second_active[0]["title"], active.title)
        self.assertEqual(second_active[0]["updates"][0]["body"], active.updates[0].body)

        summary = json.loads(
            (FIXTURES / "summary.operational.json").read_text(encoding="utf-8")
        )
        summary["generated_at"] = "2026-07-21T00:00:00Z"
        summary["active_events"] = second_active
        validate_summary(summary)
        validate_incidents(
            {
                "schema_version": "1",
                "generated_at": "2026-07-21T00:00:00Z",
                "events": result.closed_event_documents(),
            }
        )

    def test_explicit_resource_limits_fail_closed_without_blocking_peer_category(self):
        self.assertEqual(content_module.MAX_CANDIDATE_FILES_PER_CATEGORY, 128)
        self.assertEqual(content_module.MAX_FILE_BYTES, 64 * 1024)
        self.assertEqual(content_module.MAX_UPDATES_PER_EVENT, 64)
        self.assertEqual(content_module.MAX_TITLE_LENGTH, 160)
        self.assertEqual(content_module.MAX_BODY_LENGTH, 2_000)

        length_cases = {
            "title": source_text().replace(
                'title = "Public service interruption"', f'title = "{"T" * 161}"'
            ),
            "body": source_text().replace(
                'body = "Some visitors may be unable to use this public component."',
                f'body = "{"B" * 2001}"',
            ),
            "update body": source_text().replace(
                'body = "Public checks are not completing consistently."',
                f'body = "{"U" * 2001}"',
            ),
            "updates": source_with_updates(65),
        }
        for label, raw in length_cases.items():
            with self.subTest(label=label):
                temporary, _, result = load_single(raw)
                self.addCleanup(temporary.cleanup)
                self.assertEqual(result.active_events, ())
                self.assertEqual(result.issues[0].code, "invalid_content")

        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            maintenance_dir = content_root / "maintenance"
            incident_dir.mkdir()
            maintenance_dir.mkdir()
            for index in range(129):
                (incident_dir / f"event-{index:03d}.toml").write_text(
                    source_text(event_id=f"inc-limit-{index:03d}"), encoding="utf-8"
                )
            (maintenance_dir / "peer.toml").write_text(
                source_text(
                    event_id="maint-2026-07-20-peer",
                    kind="maintenance",
                    phase="planned",
                ),
                encoding="utf-8",
            )
            result = load_content(content_root)

        self.assertEqual([event.id for event in result.active_events], ["maint-2026-07-20-peer"])
        self.assertEqual(result.issues, (ContentIssue("incidents", "candidate_limit_exceeded"),))

    def test_file_reads_never_request_more_than_the_byte_limit(self):
        base = source_text()
        padding_size = content_module.MAX_FILE_BYTES - len(base.encode("utf-8")) - 1
        exact_limit_source = base + "#" + ("P" * padding_size)
        self.assertEqual(
            len(exact_limit_source.encode("utf-8")), content_module.MAX_FILE_BYTES
        )
        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            incident_dir.mkdir()
            (incident_dir / "event.toml").write_text(
                exact_limit_source, encoding="utf-8"
            )
            real_read = os.read
            with patch("publisher.content.os.read", wraps=real_read) as mocked_read:
                result = load_content(content_root)

        self.assertEqual(len(result.active_events), 1)
        requested_bytes = sum(call.args[1] for call in mocked_read.call_args_list)
        self.assertLessEqual(requested_bytes, content_module.MAX_FILE_BYTES)

    def test_candidate_files_are_opened_with_descriptor_no_follow(self):
        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            incident_dir.mkdir()
            (incident_dir / "event.toml").write_text(source_text(), encoding="utf-8")
            real_open = os.open
            with patch("publisher.content.os.open", wraps=real_open) as mocked_open:
                result = load_content(content_root)

        self.assertEqual(result.issues, ())
        candidate_calls = [
            call for call in mocked_open.call_args_list if call.args[0] == "event.toml"
        ]
        self.assertEqual(len(candidate_calls), 1)
        self.assertTrue(candidate_calls[0].args[1] & os.O_NOFOLLOW)

    def test_unknown_missing_and_bad_operator_metadata_are_rejected(self):
        valid = source_text()
        cases = {
            "unknown top-level": valid.replace(
                "[[updates]]", 'operator_note = "not public"\n[[updates]]'
            ),
            "missing top-level": valid.replace('severity = "medium"\n', ""),
            "unknown update": valid + 'detail = "extra"\n',
            "missing update": valid.replace('phase = "investigating"\n', ""),
            "wrong directory kind": valid.replace(
                'kind = "incident"', 'kind = "maintenance"'
            ),
            "bad severity": valid.replace(
                'severity = "medium"', 'severity = "critical"'
            ),
            "unknown component": valid.replace('["watch"]', '["database"]'),
            "duplicate component": valid.replace('["watch"]', '["watch", "watch"]'),
            "bad id": valid.replace(
                'id = "inc-2026-07-20-example"', 'id = "Incident 1"'
            ),
        }
        for label, raw in cases.items():
            with self.subTest(label=label):
                temporary, _, result = load_single(raw)
                self.addCleanup(temporary.cleanup)
                self.assertEqual(result.active_events, ())
                self.assertEqual(result.closed_events, ())
                self.assertEqual([issue.code for issue in result.issues], ["invalid_content"])
                self.assertRegex(
                    result.issues[0].relative_filename,
                    r"^incidents/file-[0-9a-f]{12}\.toml$",
                )

    def test_lifecycle_and_timestamp_edge_cases_are_rejected(self):
        valid = source_text()
        closed_without_resolution = source_text(
            phase="resolved", update_at="2026-07-20T12:30:00Z"
        )
        active_with_resolution = source_text(
            extra="resolved_at = 2026-07-20T12:05:00Z"
        )
        closed_before_final = textwrap.dedent(
            '''\
            id = "inc-2026-07-20-example"
            kind = "incident"
            title = "Public service interruption"
            body = "Some visitors may be unable to use this public component."
            severity = "medium"
            started_at = 2026-07-20T12:00:00Z
            resolved_at = 2026-07-20T12:05:00Z
            affected_components = ["watch"]

            [[updates]]
            at = 2026-07-20T12:05:00Z
            phase = "resolved"
            body = "Public checks recovered."

            [[updates]]
            at = 2026-07-20T12:10:00Z
            phase = "monitoring"
            body = "Public checks remain under observation."
            '''
        )
        duplicate_update = valid + textwrap.dedent(
            '''\
            [[updates]]
            at = 2026-07-20T12:05:00Z
            phase = "monitoring"
            body = "Public checks remain under observation."
            '''
        )
        cases = {
            "active with resolved_at": active_with_resolution,
            "closed without resolved_at": closed_without_resolution,
            "closed before final update": closed_before_final,
            "update before start": valid.replace(
                "2026-07-20T12:05:00Z", "2026-07-20T11:59:59Z"
            ),
            "duplicate update time": duplicate_update,
            "naive datetime": valid.replace(
                "2026-07-20T12:00:00Z", "2026-07-20T12:00:00", 1
            ),
            "date without time": valid.replace(
                "2026-07-20T12:00:00Z", "2026-07-20", 1
            ),
            "boolean timestamp": valid.replace(
                "2026-07-20T12:00:00Z", "true", 1
            ),
            "unsupported timestamp": valid.replace(
                "2026-07-20T12:00:00Z", "42", 1
            ),
            "wrong phase": valid.replace("investigating", "planned"),
        }
        for label, raw in cases.items():
            with self.subTest(label=label):
                temporary, _, result = load_single(raw)
                self.addCleanup(temporary.cleanup)
                self.assertEqual(result.active_events, ())
                self.assertEqual(result.closed_events, ())
                self.assertEqual(result.issues[0].code, "invalid_content")

    def test_invalid_file_does_not_block_peer_and_issues_are_sanitized(self):
        unsafe_marker = "api_key = syntheticvalue123"
        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            incident_dir.mkdir()
            (incident_dir / "a-bad.toml").write_text(
                f'id = [\n# {unsafe_marker}\n', encoding="utf-8"
            )
            (incident_dir / "b-good.toml").write_text(
                source_text(event_id="inc-2026-07-20-good"), encoding="utf-8"
            )

            result = load_content(content_root)

        self.assertEqual([event.id for event in result.active_events], ["inc-2026-07-20-good"])
        self.assertEqual([issue.code for issue in result.issues], ["unsafe_content"])
        self.assertRegex(
            result.issues[0].relative_filename,
            r"^incidents/file-[0-9a-f]{12}\.toml$",
        )
        issue_text = repr(result.issues)
        self.assertNotIn(unsafe_marker, issue_text)
        self.assertNotIn(str(content_root), issue_text)

    def test_privacy_matrix_rejects_unsafe_narrative_and_allows_public_hostname(self):
        unsafe_samples = (
            "Synthetic address 10.1.2.3",
            "Synthetic address 172.16.2.3",
            "Synthetic address 192.168.2.3",
            "Synthetic address 127.0.0.1",
            "Synthetic address 169.254.2.3",
            "Synthetic address fd00::1",
            "Synthetic address fe80::1",
            "Synthetic host sample.local",
            "Synthetic host localhost",
            "api_key = syntheticvalue123",
            "Synthetic path /srv/private/status.json",
            "Synthetic path C:\\Users\\Public\\status.json",
            "Synthetic identity operator@example.test",
            "restore_command: synthetic-switch",
            "Visit https://walshit.com",
            "Synthetic host status.example.test",
        )
        original_body = 'body = "Some visitors may be unable to use this public component."'
        for sample in unsafe_samples:
            with self.subTest(sample=sample):
                raw = source_text().replace(original_body, f"body = {json.dumps(sample)}")
                temporary, content_root, result = load_single(raw)
                self.addCleanup(temporary.cleanup)
                self.assertEqual(result.active_events, ())
                self.assertEqual(len(result.issues), 1)
                issue_text = repr(result.issues)
                self.assertNotIn(sample, issue_text)
                self.assertNotIn(str(content_root), issue_text)

        public_raw = source_text().replace(
            original_body, 'body = "Updates are available from WALSHIT.COM."'
        )
        temporary, _, result = load_single(public_raw)
        self.addCleanup(temporary.cleanup)
        self.assertEqual(result.issues, ())

    def test_url_like_narrative_forms_are_rejected_exactly(self):
        rejected = (
            ".walshit.com",
            "\\walshit.com",
            "walshit.com/path",
            "walshit.com\\path",
            "walshit.com%2fpath",
            "//walshit.com/path",
            "walshit.com:443/path",
            "walshit.com?query",
            "walshit.com#fragment",
            "http:walshit.com",
            "https:walshit.com",
            "https://walshit.com",
            "www.walshit.com",
            "mailto:operator@example.test",
            "ftp:synthetic",
            "file:synthetic",
            "ssh:synthetic",
            "data:text/plain,synthetic",
            "javascript:synthetic",
            "status.example.test",
        )
        original_body = 'body = "Some visitors may be unable to use this public component."'
        for value in rejected:
            with self.subTest(value=value):
                raw = source_text().replace(original_body, f"body = {json.dumps(value)}")
                temporary, _, result = load_single(raw)
                self.addCleanup(temporary.cleanup)
                self.assertEqual(result.active_events, ())
                self.assertEqual(len(result.issues), 1)
                self.assertIn(
                    result.issues[0].code, {"invalid_content", "unsafe_content"}
                )

        for value in (
            "walshit.com",
            "(walshit.com)",
            "WALSHIT.COM.",
            "Updates from walshit.com are public.",
        ):
            with self.subTest(value=value):
                raw = source_text().replace(original_body, f"body = {json.dumps(value)}")
                temporary, _, result = load_single(raw)
                self.addCleanup(temporary.cleanup)
                self.assertEqual(result.issues, ())

    def test_authorization_tokens_are_rejected_without_rejecting_ordinary_prose(self):
        original_body = 'body = "Some visitors may be unable to use this public component."'
        for value in (
            "Basic service checks are operating normally.",
            "Bearer traffic is handled normally.",
        ):
            with self.subTest(safe=value):
                raw = source_text().replace(original_body, f"body = {json.dumps(value)}")
                temporary, _, result = load_single(raw)
                self.addCleanup(temporary.cleanup)
                self.assertEqual(result.issues, ())

        for value in (
            "Bearer abcdefghijklmnop",
            "Basic QWxhZGRpbjpvcGVuU2VzYW1l",
        ):
            with self.subTest(rejected=value):
                raw = source_text().replace(original_body, f"body = {json.dumps(value)}")
                temporary, _, result = load_single(raw)
                self.addCleanup(temporary.cleanup)
                self.assertEqual(result.active_events, ())
                self.assertEqual([issue.code for issue in result.issues], ["unsafe_content"])

    def test_every_candidate_issue_uses_a_stable_opaque_reference(self):
        filenames = (
            "client_secret-password-jane-example.toml",
            r"C:\Users\Jane\credential.toml",
            "usr-local-private-path.toml",
        )
        for filename in filenames:
            with self.subTest(filename=filename):
                first_temporary, _, first = load_single("id = [", filename=filename)
                self.addCleanup(first_temporary.cleanup)
                second_temporary, _, second = load_single("id = [", filename=filename)
                self.addCleanup(second_temporary.cleanup)

                self.assertEqual(first.issues, second.issues)
                reference = first.issues[0].relative_filename
                self.assertRegex(reference, r"^incidents/file-[0-9a-f]{12}\.toml$")
                issue_text = repr(first.issues).lower()
                for fragment in ("client_secret", "password", "jane", "users", "private"):
                    self.assertNotIn(fragment, issue_text)

    def test_missing_non_directory_and_symlink_categories_fail_closed(self):
        with TemporaryDirectory() as temporary:
            missing = load_content(Path(temporary) / "missing")
        self.assertEqual(missing, ContentLoadResult((), (), ()))

        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            (content_root / "incidents").write_text("not a directory", encoding="utf-8")
            maintenance_dir = content_root / "maintenance"
            maintenance_dir.mkdir()
            (maintenance_dir / "care.toml").write_text(
                source_text(
                    event_id="maint-2026-07-20-care",
                    kind="maintenance",
                    phase="planned",
                ),
                encoding="utf-8",
            )
            result = load_content(content_root)
        self.assertEqual(len(result.active_events), 1)
        self.assertEqual(result.issues, (ContentIssue("incidents", "category_unavailable"),))

        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            incident_dir.mkdir()
            target = content_root / "outside.toml"
            target.write_text(source_text(), encoding="utf-8")
            (incident_dir / "linked.toml").symlink_to(target)
            result = load_content(content_root)
        self.assertEqual(result, ContentLoadResult((), (), ()))

    def test_read_failure_and_invalid_filename_have_only_safe_issue_data(self):
        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            incident_dir.mkdir()
            source_path = incident_dir / "event.toml"
            source_path.write_text(source_text(), encoding="utf-8")
            real_open = os.open

            def failing_file_open(path, flags, *args, **kwargs):
                if path == "event.toml":
                    raise OSError("synthetic credential and absolute path /srv/private")
                return real_open(path, flags, *args, **kwargs)

            with patch("publisher.content.os.open", side_effect=failing_file_open):
                result = load_content(content_root)
        self.assertEqual([issue.code for issue in result.issues], ["file_unavailable"])
        self.assertRegex(
            result.issues[0].relative_filename,
            r"^incidents/file-[0-9a-f]{12}\.toml$",
        )
        self.assertNotIn("credential", repr(result.issues))
        self.assertNotIn("/srv/private", repr(result.issues))

        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            incident_dir.mkdir()
            (incident_dir / "event.toml").write_text(source_text(), encoding="utf-8")
            real_stat = os.stat

            def failing_file_stat(path, *args, **kwargs):
                if path == "event.toml":
                    raise OSError("synthetic stat detail /srv/private")
                return real_stat(path, *args, **kwargs)

            with patch("publisher.content.os.stat", side_effect=failing_file_stat):
                stat_result = load_content(content_root)
        self.assertEqual([issue.code for issue in stat_result.issues], ["file_unavailable"])
        self.assertNotIn("/srv/private", repr(stat_result.issues))

        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            incident_dir.mkdir()
            (incident_dir / "event.toml").write_bytes(b"\xff\xfe")
            decode_result = load_content(content_root)
        self.assertEqual([issue.code for issue in decode_result.issues], ["file_unavailable"])

        temporary, _, result = load_single(source_text(), filename="Bad Name.toml")
        self.addCleanup(temporary.cleanup)
        self.assertEqual(result.active_events, ())
        self.assertEqual(result.issues[0].code, "invalid_filename")
        self.assertNotIn("Bad Name", result.issues[0].relative_filename)

    def test_output_and_issue_order_is_deterministic(self):
        with TemporaryDirectory() as temporary:
            content_root = Path(temporary)
            incident_dir = content_root / "incidents"
            incident_dir.mkdir()
            (incident_dir / "z.toml").write_text(
                source_text(event_id="inc-z", started_at="2026-07-20T13:00:00Z", update_at="2026-07-20T13:05:00Z"),
                encoding="utf-8",
            )
            (incident_dir / "a.toml").write_text(
                source_text(event_id="inc-a"), encoding="utf-8"
            )
            (incident_dir / "c.toml").write_text("id = [", encoding="utf-8")
            (incident_dir / "b.toml").write_text("kind = []", encoding="utf-8")

            first = load_content(content_root)
            second = load_content(content_root)

        self.assertEqual(first, second)
        self.assertEqual([event.id for event in first.active_events], ["inc-a", "inc-z"])
        self.assertEqual(
            sorted(issue.code for issue in first.issues),
            ["invalid_content", "invalid_toml"],
        )
        self.assertEqual(
            [issue.relative_filename for issue in first.issues],
            sorted(issue.relative_filename for issue in first.issues),
        )

    def test_example_template_is_present_public_safe_and_ignored(self):
        template = ROOT / "content" / "incidents" / "2026-XX-XX-example.toml.example"
        self.assertTrue(template.is_file())
        text = template.read_text(encoding="utf-8")
        assert_public_safe(text, source=template.name)
        self.assertIn("Example only", text)
        self.assertEqual(load_content(ROOT / "content"), ContentLoadResult((), (), ()))


if __name__ == "__main__":
    unittest.main()
