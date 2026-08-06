#!/usr/bin/env python3
"""Repository-side contract tests for the Walshit CI/CD pipeline."""
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "walshit.yml"
COMPOSE = ROOT / "compose.production.yaml"
DEPLOY = ROOT / "scripts" / "deploy-production.sh"
DOCKERFILE = ROOT / "Dockerfile.hugo"
README = ROOT / "README.md"


class WorkflowContractTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()

    def test_pipeline_is_limited_to_main_and_serializes_deployments(self):
        self.assertRegex(self.text, r"(?m)^\s+branches:\s*\[main\]\s*$")
        self.assertIn("pull_request:", self.text)
        self.assertIn("push:", self.text)
        self.assertNotIn("paths:", self.text)
        self.assertIn("group: walshit-production", self.text)
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("refs/heads/main", self.text)
        self.assertIn("GITHUB_SHA", self.text)

    def test_pipeline_tests_builds_commit_tag_and_deploys_only_main_pushes(self):
        self.assertIn("python3 tests/test_site.py -v", self.text)
        self.assertIn("python3 tests/test_typography_preview.py -v", self.text)
        self.assertIn("tests/test_generated.py -v", self.text)
        self.assertIn("tests/test_cicd.py -v", self.text)
        self.assertIn("type=sha,format=long,prefix=", self.text)
        self.assertIn("ghcr.io/walshg3/walshit-landing", self.text)
        self.assertRegex(
            self.text,
            r"if:\s*github\.event_name == 'push' && github\.ref == 'refs/heads/main'",
        )
        self.assertIn("environment: production", self.text)
        self.assertIn(
            'sudo /usr/local/sbin/deploy-walshit "$GITHUB_SHA" "$PUBLISHED_DIGEST"',
            self.text,
        )

    def test_privileged_deploy_passes_digest_through_the_environment(self):
        self.assertIn("PUBLISHED_DIGEST: ${{ needs.publish.outputs.digest }}", self.text)
        self.assertNotIn('run: sudo /usr/local/sbin/deploy-walshit "$GITHUB_SHA" "${{', self.text)

    def test_publish_job_smoke_tests_the_pushed_digest(self):
        self.assertIn("Smoke-test the published immutable image", self.text)
        self.assertIn("PUBLISHED_IMAGE: ghcr.io/walshg3/walshit-landing@${{ steps.build.outputs.digest }}", self.text)
        self.assertIn('docker pull "$PUBLISHED_IMAGE"', self.text)
        self.assertIn('docker run --rm -d --name "$name"', self.text)

    def test_pipeline_uses_least_privilege_and_does_not_embed_credentials(self):
        self.assertIn("contents: read", self.text)
        self.assertIn("packages: write", self.text)
        self.assertNotIn("id-token: write", self.text)
        self.assertNotIn("attestations: write", self.text)
        self.assertNotRegex(self.text, r"(?i)(password|private[_ -]?key):\s*[^$\s]")


class ProductionComposeContractTest(unittest.TestCase):
    def setUp(self):
        self.text = COMPOSE.read_text()

    def test_compose_uses_required_commit_tag_without_build(self):
        self.assertIn(
            "image: ghcr.io/walshg3/walshit-landing:${IMAGE_TAG:?IMAGE_TAG is required}@${IMAGE_DIGEST:?IMAGE_DIGEST is required}",
            self.text,
        )
        self.assertNotRegex(self.text, r"(?m)^\s*build:")
        self.assertIn('source-revision: "${IMAGE_TAG:?IMAGE_TAG is required}"', self.text)

    def test_compose_preserves_hardening_fixed_origin_and_external_network(self):
        for marker in (
            '"${PORT_BINDING:-192.168.5.252:3003:8080}"',
            'user: "101:101"',
            "read_only: true",
            "no-new-privileges:true",
            "cap_drop:",
            "pids_limit: 64",
            "mem_limit: 64m",
            "external: true",
            "name: walshit-landing-hugo-prod-9238dbfa_default",
        ):
            self.assertIn(marker, self.text)


class DeploymentScriptContractTest(unittest.TestCase):
    def setUp(self):
        self.text = DEPLOY.read_text()

    def run_sourced_function(self, command, *, container_id="unused", tag_id="unused", digest_id="unused"):
        script = f"""
set -Eeuo pipefail
source {shlex.quote(str(DEPLOY))}
docker() {{
  if [[ "$1" == "inspect" && "$2" == "--format" && "$3" == "{{{{.Image}}}}" ]]; then
    printf '%s\\n' "$FAKE_CONTAINER_ID"
  elif [[ "$1" == "image" && "$2" == "inspect" && "$3" == "--format" && "$4" == "{{{{.Id}}}}" ]]; then
    if [[ "$5" == *'@sha256:'* ]]; then
      printf '%s\\n' "$FAKE_DIGEST_ID"
    else
      printf '%s\\n' "$FAKE_TAG_ID"
    fi
  elif [[ "$1" == "exec" ]]; then
    printf '%s\\n' 'walshit-landing-hugo-ok'
  else
    printf 'unexpected docker invocation: %q ' "$@" >&2
    return 99
  fi
}}
{command}
"""
        return subprocess.run(
            ["bash", "-c", script],
            env={
                "PATH": "/usr/bin:/bin",
                "FAKE_CONTAINER_ID": container_id,
                "FAKE_TAG_ID": tag_id,
                "FAKE_DIGEST_ID": digest_id,
            },
            capture_output=True,
            text=True,
            check=False,
        )

    def test_container_acceptance_uses_matching_immutable_image_ids(self):
        result = self.run_sourced_function(
            'verify_container "candidate" "repo@sha256:digest"',
            container_id="sha256:image-id",
            digest_id="sha256:image-id",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_container_acceptance_rejects_different_immutable_image_ids(self):
        result = self.run_sourced_function(
            'verify_container "candidate" "repo@sha256:digest"',
            container_id="sha256:candidate-id",
            digest_id="sha256:different-id",
        )
        self.assertNotEqual(result.returncode, 0)

    def test_tag_digest_binding_accepts_matching_immutable_image_ids(self):
        result = self.run_sourced_function(
            'verify_same_image "repo:tag" "repo@sha256:digest"',
            tag_id="sha256:image-id",
            digest_id="sha256:image-id",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tag_digest_binding_rejects_different_immutable_image_ids(self):
        result = self.run_sourced_function(
            'verify_same_image "repo:tag" "repo@sha256:digest"',
            tag_id="sha256:tag-id",
            digest_id="sha256:digest-id",
        )
        self.assertNotEqual(result.returncode, 0)

    def test_wrapper_uses_a_root_only_runtime_lock_directory(self):
        self.assertIn('LOCK_DIR="/run/walshit"', self.text)
        self.assertIn('install -d --owner=root --group=root --mode=0700 "$LOCK_DIR"', self.text)
        self.assertIn('LOCK_FILE="${LOCK_DIR}/production.lock"', self.text)

    def test_emergency_rollback_ignores_signal_escalation_until_restored(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            marker = Path(temporary_directory) / "predecessor-started"
            script = f"""
set -Eeuo pipefail
source {shlex.quote(str(DEPLOY))}
CUTOVER_STARTED=true
ROLLBACK_HANDLED=false
LIVE_CONTAINER=rejected
PREVIOUS_CONTAINER=previous
LIVE_PROJECT=live
LIVE_BIND=192.168.5.252:3003:8080
docker() {{
  if [[ "$1" == "inspect" && "$3" == "{{{{.State.Running}}}}" ]]; then
    printf '%s\\n' true
  elif [[ "$1" == "inspect" && "$3" == "{{{{if .State.Health}}}}{{{{.State.Health.Status}}}}{{{{else}}}}missing{{{{end}}}}" ]]; then
    printf '%s\\n' healthy
  elif [[ "$1" == "stop" ]]; then
    sleep 1
  elif [[ "$1" == "start" ]]; then
    : > "$ROLLBACK_MARKER"
  elif [[ "$1" == "exec" ]]; then
    printf '%s\\n' walshit-landing-hugo-ok
  else
    return 99
  fi
}}
(sleep 0.1; kill -TERM $$) &
on_exit 1
"""
            result = subprocess.run(
                ["bash", "-c", script],
                env={"PATH": "/usr/bin:/bin", "ROLLBACK_MARKER": str(marker)},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertTrue(marker.exists(), result.stderr)

    def test_script_locks_validates_and_updates_only_the_application_service(self):
        self.assertIn("flock", self.text)
        self.assertRegex(self.text, r"\[\[.+\^\[0-9a-f\]\{40\}\$.+\]\]")
        self.assertIn("sha256:[0-9a-f]{64}", self.text)
        self.assertIn("docker compose", self.text)
        self.assertIn("pull landing-hugo", self.text)
        self.assertIn("up -d --no-deps --no-build --pull never landing-hugo", self.text)
        self.assertNotRegex(self.text, r"docker (?:compose )?(?:down|rm|system prune|network rm|volume rm)")

    def test_script_requires_the_exact_image_and_waits_for_healthy(self):
        self.assertIn('IMAGE_REPOSITORY="ghcr.io/walshg3/walshit-landing"', self.text)
        self.assertIn("${IMAGE_REPOSITORY}:${IMAGE_TAG}", self.text)
        self.assertIn("${IMAGE_REPOSITORY}@${IMAGE_DIGEST}", self.text)
        self.assertIn("org.opencontainers.image.revision", self.text)
        self.assertIn("Health.Status", self.text)
        self.assertIn("walshit-landing-hugo-ok", self.text)
        self.assertIn("DEPLOY_HEALTH_TIMEOUT", self.text)

    def test_script_stages_then_retains_predecessor_and_rolls_back_failure(self):
        for marker in (
            "candidate",
            "127.0.0.1::8080",
            "docker stop \"$PREVIOUS_CONTAINER\"",
            "docker start \"$PREVIOUS_CONTAINER\"",
            "rollback",
            "git ls-remote",
            "stat --format",
            "retained resources require operator review",
            "trap 'on_exit $?' EXIT",
            "emergency rollback",
        ):
            self.assertIn(marker, self.text)


class BuildMetadataContractTest(unittest.TestCase):
    def test_builder_can_write_generated_output_with_legacy_or_buildkit_builders(self):
        text = DOCKERFILE.read_text()
        builder = text.split("FROM nginxinc/", 1)[0]
        self.assertIn("USER root", builder)

    def test_image_declares_commit_provenance(self):
        text = DOCKERFILE.read_text()
        self.assertIn("ARG SOURCE_REVISION", text)
        self.assertIn("org.opencontainers.image.revision=$SOURCE_REVISION", text)
        self.assertIn("org.opencontainers.image.source=https://github.com/walshg3/Homelab", text)


class DocumentationContractTest(unittest.TestCase):
    def test_ci_setup_and_secret_boundary_are_documented(self):
        text = README.read_text()
        for marker in (
            "GitHub Actions deployment",
            "production Environment",
            "walshit-deploy",
            "/usr/local/sbin/deploy-walshit",
            "GITHUB_TOKEN",
            "No repository or environment secrets are required",
            "ghcr.io/walshg3/walshit-landing:<full-commit-sha>",
            "Require approval for all outside collaborators",
            "ephemeral",
            "/run/walshit/production.lock",
            "same-SHA retry",
            "Install `git`",
        ):
            self.assertIn(marker, text)

    def test_pipeline_files_are_not_hidden_by_the_root_allowlist(self):
        root_ignore = (REPO_ROOT / ".gitignore").read_text()
        self.assertIn("!.github/workflows/walshit.yml", root_ignore)
        self.assertIn("!actionlint.yaml", root_ignore)
        self.assertIn("!walshit-landing/compose.production.yaml", root_ignore)

    def test_actionlint_knows_the_production_runner_label(self):
        actionlint = (REPO_ROOT / "actionlint.yaml").read_text()
        self.assertIn("walshit-production", actionlint)


if __name__ == "__main__":
    unittest.main()
