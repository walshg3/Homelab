import json
import ipaddress
from pathlib import Path
import re
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
WEB_IMAGE = "nginxinc/nginx-unprivileged:1.30.3-alpine@sha256:a0c30a699fa18d7bbdc6faf197742741a1f5e4631066abf18578dc8102b62c5d"
PUBLISHER_IMAGE = "python:3.12.11-alpine3.22@sha256:efcdfa6a6b2fd2afb9c7dfa9a5b288a6f68338b5cfdebe6b637d986067d85757"


def source(name):
    return (ROOT / name).read_text()


def compose_service_blocks(compose):
    """Split only the two-space service mappings without pretending to parse YAML."""
    lines = compose.splitlines(keepends=True)
    try:
        services_index = next(
            index for index, line in enumerate(lines) if line.rstrip() == "services:"
        )
    except StopIteration as exc:
        raise AssertionError("services mapping is missing") from exc
    blocks = {}
    current = None
    for line in lines[services_index + 1:]:
        if line.strip() and not line.startswith(" "):
            break
        match = re.match(r"^  ([a-z][a-z0-9-]*):\s*$", line)
        if match:
            current = match.group(1)
            blocks[current] = line
        elif current is not None:
            blocks[current] += line
    return blocks


def compose_list_items(service_block, section):
    lines = service_block.splitlines(keepends=True)
    heading = f"    {section}:"
    try:
        section_index = next(
            index for index, line in enumerate(lines) if line.rstrip() == heading
        )
    except StopIteration as exc:
        raise AssertionError(f"{section} section is missing") from exc
    items = []
    current = None
    for line in lines[section_index + 1:]:
        if line.strip() and not line.startswith("      "):
            break
        if line.startswith("      - "):
            current = line
            items.append(current)
        elif current is not None:
            items[-1] += line
    return items


class RuntimeConfigurationTests(unittest.TestCase):
    def test_dockerfiles_are_pinned_package_free_and_run_as_fixed_users(self):
        web = source("Dockerfile.web")
        publisher = source("Dockerfile.publisher")
        self.assertIn(f"FROM {WEB_IMAGE}", web)
        self.assertIn(f"FROM {PUBLISHER_IMAGE}", publisher)
        self.assertRegex(web, r"(?m)^USER 101:101$")
        self.assertRegex(publisher, r"(?m)^USER 1000:1000$")
        self.assertNotRegex(web + publisher, r"(?i)\b(?:apk|apt|curl|bash)\b")
        self.assertIn("chown -R 1000:1000 /var/lib/status/public /var/lib/status/state", publisher)
        self.assertIn("config/checks.toml", publisher)
        self.assertIn("/usr/share/nginx/html/status/v2", web)
        self.assertNotIn("/status/v1", web)

    def test_compose_mount_network_secret_and_staging_boundaries(self):
        compose = source("compose.yaml")
        staging = source("compose.staging.yaml")
        services = compose_service_blocks(compose)
        self.assertEqual(set(services), {"publisher", "web"})
        publisher = services["publisher"]
        web = services["web"]
        self.assertNotRegex(compose, r"(?m)^\s+ports:")
        self.assertNotIn("ports:", publisher)
        self.assertNotIn("ports:", web)
        self.assertEqual(compose_list_items(publisher, "networks"), ["      - publisher-egress\n"])
        self.assertEqual(compose_list_items(web, "networks"), ["      - web-internal\n"])
        for name, block, user in (
            ("publisher", publisher, "1000:1000"),
            ("web", web, "101:101"),
        ):
            with self.subTest(service=name):
                self.assertIn("platform: linux/amd64", block)
                self.assertIn(f'user: "{user}"', block)
                self.assertIn("read_only: true", block)
                self.assertIn("no-new-privileges:true", block)
                self.assertRegex(block, r"(?m)^    cap_drop:\n      - ALL$")
                self.assertIn("restart: unless-stopped", block)
                self.assertIn("driver: json-file", block)
                self.assertIn('max-size: "10m"', block)
                self.assertIn('max-file: "3"', block)
        self.assertIn("internal: true", compose)
        self.assertIn("subnet: 10.255.250.48/28", compose)
        self.assertIn("subnet: 10.255.250.64/28", compose)
        self.assertEqual(compose.count("subnet: 10.255.250."), 2)
        self.assertIn("condition: service_healthy", web)
        self.assertIn("- publisher-egress", publisher)
        self.assertNotIn("web-internal", publisher)
        self.assertIn("- web-internal", web)
        self.assertNotIn("publisher-egress", web)
        publisher_mounts = compose_list_items(publisher, "volumes")
        web_mounts = compose_list_items(web, "volumes")
        self.assertEqual(len(publisher_mounts), 3)
        self.assertEqual(len(web_mounts), 1)
        public_rw = next(item for item in publisher_mounts if "source: public-data" in item)
        state_rw = next(item for item in publisher_mounts if "source: publisher-state" in item)
        content_ro = next(item for item in publisher_mounts if "source: ./content" in item)
        self.assertIn("target: /var/lib/status/public", public_rw)
        self.assertNotIn("read_only", public_rw)
        self.assertIn("target: /var/lib/status/state", state_rw)
        self.assertNotIn("read_only", state_rw)
        self.assertIn("target: /app/content", content_ro)
        self.assertIn("read_only: true", content_ro)
        self.assertIn("create_host_path: false", content_ro)
        self.assertIn("source: public-data", web_mounts[0])
        self.assertIn("target: /usr/share/nginx/html/status/v2", web_mounts[0])
        self.assertIn("read_only: true", web_mounts[0])
        self.assertIn("nocopy: true", web_mounts[0])
        self.assertNotIn("secrets:", web)
        self.assertNotIn("publisher-state", web)
        self.assertIn("./content", publisher)
        self.assertIn("/app/content", publisher)
        self.assertIn("target: plex-probe.env", publisher)
        self.assertNotIn("ports:", publisher)
        self.assertIn("file: ./secrets/plex-probe.env", compose)
        self.assertNotIn("env_file", compose)
        self.assertNotIn("${", compose)
        for forbidden in ("privileged:", "network_mode:", "pid:", "ipc:",
                          "/var/run/docker.sock", "devices:", "expose:"):
            self.assertNotIn(forbidden, compose)
        self.assertRegex(publisher, r"(?s)/var/lib/status/public.*?/var/lib/status/state.*?/app/content.*?target: plex-probe.env")

    def test_staging_gateway_is_the_only_loopback_ingress_and_uses_a_third_subnet(self):
        compose = source("compose.yaml")
        staging = source("compose.staging.yaml")
        base_services = compose_service_blocks(compose)
        staging_services = compose_service_blocks(staging)
        self.assertEqual(set(staging_services), {"gateway"})
        gateway = staging_services["gateway"]
        self.assertEqual(staging.count('"127.0.0.1:3015:8080"'), 1)
        self.assertIn('"127.0.0.1:3015:8080"', gateway)
        self.assertNotIn("ports:", base_services["publisher"])
        self.assertNotIn("ports:", base_services["web"])
        self.assertEqual(
            set(item.strip().removeprefix("- ") for item in compose_list_items(gateway, "networks")),
            {"web-internal", "staging-ingress"},
        )
        subnets = [
            ipaddress.ip_network(value)
            for value in re.findall(r"(?m)^\s+- subnet: (10\.255\.250\.\d+/28)$", compose + staging)
        ]
        self.assertEqual(
            subnets,
            [
                ipaddress.ip_network("10.255.250.48/28"),
                ipaddress.ip_network("10.255.250.64/28"),
                ipaddress.ip_network("10.255.250.32/28"),
            ],
        )
        for index, subnet in enumerate(subnets):
            for other in subnets[index + 1:]:
                self.assertFalse(subnet.overlaps(other), f"{subnet} overlaps {other}")
        self.assertRegex(staging, r"(?s)staging-ingress:\n\s+driver: bridge\n\s+ipam:")
        self.assertNotRegex(staging, r"(?s)staging-ingress:.*?internal:\s*true")

    def test_staging_gateway_has_no_data_or_build_inputs_and_is_fully_hardened(self):
        staging = source("compose.staging.yaml")
        gateway = compose_service_blocks(staging)["gateway"]
        self.assertIn("name: status-walshit-staging-v2", staging)
        for token in (
            "platform: linux/amd64", 'user: "101:101"', "read_only: true",
            "no-new-privileges:true", "cap_drop:\n      - ALL",
            "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777", 'cpus: "0.25"',
            "mem_limit: 64m", "pids_limit: 32", "restart: unless-stopped",
            "driver: json-file", 'max-size: "10m"', 'max-file: "3"',
            "condition: service_healthy", "status-walshit-web-ok",
            "/etc/nginx/nginx.staging-gateway.conf", "interval:", "timeout:",
            "retries:", "start_period:",
        ):
            self.assertIn(token, gateway)
        for forbidden_section in (
            "volumes", "secrets", "environment", "env_file", "build",
        ):
            self.assertNotRegex(gateway, rf"(?m)^    {forbidden_section}:")
        for forbidden in ("/var/run/docker.sock", "privileged:", "network_mode:", "devices:"):
            self.assertNotIn(forbidden, gateway)

    def test_stable_local_images_make_gateway_and_web_the_same_artifact(self):
        compose = source("compose.yaml")
        staging = source("compose.staging.yaml")
        base_services = compose_service_blocks(compose)
        gateway = compose_service_blocks(staging)["gateway"]
        web_image = re.search(r"(?m)^    image: (status-walshit-web:[0-9-]+)$", base_services["web"])
        publisher_image = re.search(
            r"(?m)^    image: (status-walshit-publisher:[0-9-]+)$",
            base_services["publisher"],
        )
        gateway_image = re.search(r"(?m)^    image: (status-walshit-web:[0-9-]+)$", gateway)
        self.assertIsNotNone(web_image)
        self.assertIsNotNone(publisher_image)
        self.assertIsNotNone(gateway_image)
        self.assertEqual(gateway_image.group(1), web_image.group(1))

    def test_staging_gateway_config_is_fixed_bounded_and_header_safe(self):
        dockerfile = source("Dockerfile.web")
        dockerignore = source(".dockerignore")
        gateway = source("nginx.staging-gateway.conf")
        security_headers = source("security-headers.conf")
        self.assertIn(
            "COPY --chown=101:101 nginx.staging-gateway.conf /etc/nginx/nginx.staging-gateway.conf",
            dockerfile,
        )
        self.assertIn("!nginx.staging-gateway.conf", dockerignore)
        self.assertEqual(gateway.count("proxy_pass http://web:8080;"), 1)
        self.assertNotRegex(gateway, r"proxy_pass\s+[^;]*\$")
        for token in (
            "worker_processes 1;", "pid /tmp/nginx.pid;", "listen 8080",
            "server_tokens off;", "autoindex off;", "proxy_connect_timeout 3s;",
            "proxy_read_timeout 10s;", "proxy_send_timeout 10s;",
            "proxy_buffering off;", "proxy_request_buffering off;",
            "client_body_temp_path /tmp/client_body;", "proxy_temp_path /tmp/proxy;",
            "fastcgi_temp_path /tmp/fastcgi;", "uwsgi_temp_path /tmp/uwsgi;",
            "scgi_temp_path /tmp/scgi;",
            "if ($request_method !~ ^(?:GET|HEAD)$)", "return 405;",
            "include /etc/nginx/security-headers.conf;",
        ):
            self.assertIn(token, gateway)
        header_names = set(re.findall(r"(?m)^add_header ([^ ]+) ", security_headers))
        hidden_names = set(re.findall(r"(?m)^\s*proxy_hide_header ([^;]+);", gateway))
        self.assertEqual(hidden_names, header_names)
        for name in header_names:
            self.assertEqual(gateway.count(f"proxy_hide_header {name};"), 1)
        for forbidden in (
            "resolver ", "proxy_cache", "proxy_store", "rewrite ", "Access-Control-Allow",
            "add_header Cache-Control", "proxy_intercept_errors", "error_page", "try_files",
            "alias ", "root ", "location = /healthz", "status_stub", "stub_status",
            "http://web:8080/", "https://", "http://127.", "http://localhost",
        ):
            self.assertNotIn(forbidden, gateway)
        self.assertNotRegex(gateway, r"(?m)^\s*proxy_hide_header (?:Content-Type|Cache-Control);")

    def test_each_service_has_bounded_runtime_controls_and_healthcheck(self):
        compose = source("compose.yaml")
        services = compose_service_blocks(compose)
        for name, block in services.items():
            with self.subTest(service=name):
                for token in ("cpus:", "mem_limit:", "pids_limit:", "tmpfs:",
                              "size=", "mode=1777", "rw,noexec,nosuid,nodev",
                              "interval:", "timeout:", "retries:", "start_period:"):
                    self.assertIn(token, block)
        self.assertRegex(services["publisher"], r'"publisher\.runner", "health"')
        self.assertIn("status-walshit-web-ok", services["web"])

    def test_browser_nginx_and_volume_share_one_exact_v2_contract(self):
        javascript = source("web/status.js")
        nginx = source("nginx.conf")
        web = compose_service_blocks(source("compose.yaml"))["web"]
        browser_paths = dict(re.findall(
            r'^\s*const\s+([A-Z_]+_PATH)\s*=\s*"([^"]+)";',
            javascript,
            re.M,
        ))
        self.assertEqual(browser_paths, {
            "SUMMARY_PATH": "/status/v2/summary.json",
            "HISTORY_PATH": "/status/v2/history.json",
        })
        nginx_paths = set(re.findall(
            r"(?m)^\s*location\s+=\s+(/status/v\d+/[^\s{]+\.json)\s*\{",
            nginx,
        ))
        mount = re.search(
            r"(?m)^\s*target:\s*(/usr/share/nginx/html/status/v\d+)$",
            web,
        )
        self.assertIsNotNone(mount)
        public_prefix = mount.group(1).removeprefix("/usr/share/nginx/html")
        expected = {
            f"{public_prefix}/summary.json",
            f"{public_prefix}/history.json",
            f"{public_prefix}/incidents.json",
        }
        self.assertEqual(nginx_paths, expected)
        self.assertEqual(set(browser_paths.values()), expected - {f"{public_prefix}/incidents.json"})

    def test_runtime_and_documentation_never_serve_or_instruct_v1(self):
        runtime_files = (
            "Dockerfile.web", "Dockerfile.publisher", "compose.yaml",
            "compose.staging.yaml", "nginx.conf", "security-headers.conf",
        )
        for name in runtime_files:
            with self.subTest(path=name):
                self.assertNotIn("/status/v1", source(name))
        for name in ("README.md", "runbooks/operations.md"):
            for line in source(name).splitlines():
                if "/status/v1" in line:
                    self.assertIn("404", line)

    def test_nginx_has_exact_routes_headers_caches_and_default_denies(self):
        nginx = source("nginx.conf")
        headers = source("security-headers.conf")
        required = {
            "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; form-action 'none'; connect-src 'self'; base-uri 'self'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
        }
        for name, value in required.items():
            self.assertIn(f'add_header {name} "{value}" always;', headers)
        for token in ("listen 8080", "pid /tmp/nginx.pid", "server_tokens off",
                      "autoindex off", "location = /healthz.txt", "location = /",
                      "location = /walsh-ticket-crest.png",
                      "location ~ \\.html$", "location ~ \\.(?:css|js)$",
                      "location ~ ^/fonts/[a-z0-9-]+\\.woff2$",
                      "location = /status/v2/summary.json",
                      "location = /status/v2/history.json",
                      "location = /status/v2/incidents.json", "return 404"):
            self.assertIn(token, nginx)
        workers = re.findall(r"(?m)^worker_processes\s+([^;]+);$", nginx)
        self.assertEqual(workers, ["1"])
        self.assertNotIn("worker_processes auto", nginx)
        self.assertIn('Cache-Control "no-cache"', nginx)
        self.assertIn('Cache-Control "public, max-age=3600"', nginx)
        self.assertIn('Cache-Control "no-store, max-age=0"', nginx)
        header_locations = re.findall(r"location[^\{]*\{(.*?)\n\s*\}", nginx, re.S)
        self.assertTrue(header_locations)
        for body in header_locations:
            if "add_header" in body:
                self.assertIn("include /etc/nginx/security-headers.conf;", body)
        for forbidden in ("proxy_pass", "fastcgi_pass", "ssi on", "Access-Control-Allow-Origin"):
            self.assertNotIn(forbidden, nginx)

    def test_dockerignore_and_repository_ignore_secret_runtime_and_build_context(self):
        dockerignore = source(".dockerignore")
        for entry in (".git", "tests", "fixtures", "runbooks", "runtime", "render",
                      "secrets", "__pycache__", "*.pyc", "*.json", "DESIGN.md"):
            self.assertIn(entry, dockerignore)
        self.assertEqual(dockerignore.splitlines()[1], "*")
        for admitted in (
            "!Dockerfile.web", "!Dockerfile.publisher", "!nginx.conf",
            "!nginx.staging-gateway.conf", "!security-headers.conf", "!web/", "!publisher/",
            "!config/checks.toml",
        ):
            self.assertIn(admitted, dockerignore)
        root_ignore = (REPO / ".gitignore").read_text()
        self.assertIn("status-walshit/secrets/*", root_ignore)
        self.assertIn("status-walshit/runtime/*", root_ignore)
        for canary in (
            "status-walshit/secrets/SHOULD_NEVER_ENTER_GIT.env",
            "status-walshit/runtime/SHOULD_NEVER_ENTER_GIT.json",
        ):
            completed = subprocess.run(
                ["git", "check-ignore", "--no-index", "--quiet", canary],
                cwd=REPO, check=False, timeout=10,
            )
            self.assertEqual(completed.returncode, 0, canary)

    def test_documentation_has_safe_complete_operator_workflow(self):
        docs = source("README.md") + source("runbooks/operations.md")
        for phrase in (
            "docker compose -f compose.yaml -f compose.staging.yaml",
            "127.0.0.1:3015", "chmod 600", "docker compose stop publisher",
            "docker compose start publisher", "three intervals", "Cloudflare",
            "DNS", "no port", "destructive", "scoped approval", "rollback",
            "without removing volumes", "command arguments", "environment", "logs",
            "status-walshit-staging-v2", "status-walshit-staging",
            "gateway", "internal-only", "must not be removed", "core web",
        ):
            self.assertIn(phrase, docs)
        operations = source("runbooks/operations.md")
        exact_preflights = (
            'test "$(id -u)" -eq 1000',
            'test "$(id -g)" -eq 1000',
            'test -f secrets/plex-probe.env',
            'test ! -L secrets/plex-probe.env',
            'test "$(stat -c %h secrets/plex-probe.env)" -eq 1',
            'test "$(stat -c %u secrets/plex-probe.env)" -eq 1000',
            'test "$(stat -c %g secrets/plex-probe.env)" -eq 1000',
            'test "$(stat -c %a secrets/plex-probe.env)" -eq 600',
        )
        for preflight in exact_preflights:
            self.assertIn(preflight, operations)
        first_compose = operations.index(
            "docker compose -f compose.yaml -f compose.staging.yaml config"
        )
        for preflight in exact_preflights:
            self.assertLess(operations.index(preflight), first_compose)
        self.assertRegex(docs, r"explicitly\s+designed numeric-ID mapping")
        for unsafe in ("chmod 644", "run the container as root"):
            self.assertNotIn(unsafe, docs)

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI is unavailable")
    def test_combined_compose_config_is_valid_without_secret_material(self):
        completed = subprocess.run(
            ["docker", "compose", "-f", "compose.yaml", "-f", "compose.staging.yaml",
             "config", "--format", "json"], cwd=ROOT, text=True, capture_output=True,
            timeout=20, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        model = json.loads(completed.stdout)
        self.assertEqual(model["name"], "status-walshit-staging-v2")
        publisher = model["services"]["publisher"]
        web = model["services"]["web"]
        gateway = model["services"]["gateway"]
        self.assertNotIn("ports", publisher)
        self.assertNotIn("ports", web)
        self.assertEqual(set(publisher["networks"]), {"publisher-egress"})
        self.assertEqual(set(web["networks"]), {"web-internal"})
        self.assertEqual(set(gateway["networks"]), {"web-internal", "staging-ingress"})
        self.assertEqual(web["user"], "101:101")
        self.assertEqual(publisher["user"], "1000:1000")
        for service, expected_pids in ((publisher, 64), (web, 32), (gateway, 32)):
            self.assertTrue(service["read_only"])
            self.assertEqual(service["cap_drop"], ["ALL"])
            self.assertEqual(service["security_opt"], ["no-new-privileges:true"])
            self.assertEqual(service["pids_limit"], expected_pids)
            self.assertEqual(service["logging"]["driver"], "json-file")
            self.assertIn("healthcheck", service)
        public_mount = next(
            mount for mount in web["volumes"] if mount["source"] == "public-data"
        )
        self.assertEqual(public_mount["target"], "/usr/share/nginx/html/status/v2")
        self.assertTrue(public_mount["read_only"])
        self.assertTrue(public_mount["volume"]["nocopy"])
        self.assertNotIn("volumes", gateway)
        self.assertNotIn("secrets", gateway)
        self.assertNotIn("environment", gateway)
        self.assertNotIn("build", gateway)
        self.assertEqual(gateway["image"], web["image"])
        self.assertEqual(len(gateway["ports"]), 1)
        self.assertEqual(gateway["ports"][0]["host_ip"], "127.0.0.1")
        self.assertEqual(int(gateway["ports"][0]["published"]), 3015)
        self.assertEqual(int(gateway["ports"][0]["target"]), 8080)
        self.assertEqual(web["depends_on"]["publisher"]["condition"], "service_healthy")
        self.assertEqual(gateway["depends_on"]["web"]["condition"], "service_healthy")
        self.assertEqual(publisher["secrets"][0]["source"], "plex-probe")
        self.assertTrue(model["networks"]["web-internal"]["internal"])
        self.assertFalse(model["networks"]["staging-ingress"].get("internal", False))
        self.assertEqual(
            model["networks"]["staging-ingress"]["ipam"]["config"][0]["subnet"],
            "10.255.250.32/28",
        )


if __name__ == "__main__":
    unittest.main()
