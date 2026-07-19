#!/usr/bin/env python3
"""Vertical-slice validation suite for the Hugo prototype.

Run from walshit-landing/:
    python3 tests/test_site.py
"""
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frontmatter import (  # noqa: E402
    ALLOWED_SERVICES,
    ALLOWED_SEVERITY,
    ALLOWED_STATUS,
    FrontMatterError,
    PRIVATE_ADDRESS_PATTERNS,
    SECRET_PATTERNS,
    content_body,
    load_markdown_files,
    parse_frontmatter,
    require_rfc3339_datetime,
)
import nginxconf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT.parent
HUGO = ROOT / "hugo"
CONTENT = HUGO / "content"

REQUIRED_STRUCTURE_FILES = [
    "hugo.toml",
    "content/_index.md",
    "layouts/_default/baseof.html",
    "layouts/index.html",
    "static/styles.css",
    "static/app.js",
    "static/walsh-ticket-crest.png",
    "static/buy-me-a-coffee-logo.png",
]

REQUIRED_FRONTMATTER_FIELDS = {"title", "summary", "slug", "date"}
MAINTENANCE_FIELDS = {"status", "severity", "starts_at", "ends_at", "expires_at"}
KNOWN_FRONTMATTER_FIELDS = (
    REQUIRED_FRONTMATTER_FIELDS | {"updated", "draft", "tags", "affected_services"} | MAINTENANCE_FIELDS
)


class HugoSourceStructureTest(unittest.TestCase):
    """Step 1: the isolated Hugo source tree must exist with a homepage."""

    def test_required_homepage_structure_exists(self):
        missing = [f for f in REQUIRED_STRUCTURE_FILES if not (HUGO / f).is_file()]
        self.assertEqual(missing, [], f"missing required Hugo source files under {HUGO}: {missing}")


class FundingIntegrationTest(unittest.TestCase):
    """Repository and site funding links stay explicit, static, and CSP-safe."""

    def test_github_funding_uses_official_buy_me_a_coffee_key(self):
        funding = REPO_ROOT / ".github" / "FUNDING.yml"
        self.assertTrue(funding.is_file(), f"missing GitHub funding file {funding}")
        lines = [
            line.strip()
            for line in funding.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(lines, ["buy_me_a_coffee: gregwalsh"])
        root_ignore = (REPO_ROOT / ".gitignore").read_text()
        self.assertIn("!.github/FUNDING.yml", root_ignore)

    def test_site_uses_a_header_support_control_with_local_logo(self):
        header = (HUGO / "layouts" / "partials" / "header.html").read_text()
        footer = (HUGO / "layouts" / "partials" / "footer.html").read_text()
        self.assertIn('href="https://buymeacoffee.com/gregwalsh"', header)
        self.assertIn('class="coffee-nav"', header)
        self.assertIn('rel="noopener noreferrer"', header)
        self.assertIn('aria-label="Buy me a Coffee"', header)
        self.assertIn("Buy me a Coffee", header)
        self.assertNotIn("Buy Greg", header)
        self.assertIn('src="{{ "buy-me-a-coffee-logo.png" | relURL }}?v={{ .Site.Params.assetVersion }}"', header)
        self.assertLess(header.index("help-trigger"), header.index("coffee-nav"))
        self.assertNotIn("buymeacoffee.com", footer)
        self.assertNotIn("coffee-link", footer)
        self.assertNotIn("<script", header)
        self.assertNotIn("button-api", header)
        self.assertNotIn("cdn.buymeacoffee.com", header)

        css = (HUGO / "static" / "styles.css").read_text()
        self.assertIn(".coffee-nav", css)
        self.assertIn(".coffee-label", css)
        self.assertNotIn(".coffee-link", css)

        validator = (ROOT / "scripts" / "validate_generated.py").read_text()
        self.assertIn('"buymeacoffee.com"', validator)
        self.assertIn('"buy-me-a-coffee-logo.png"', validator)

    def test_header_support_css_uses_a_new_asset_version(self):
        config = (HUGO / "hugo.toml").read_text()
        self.assertIn('assetVersion = "20260719-3"', config)

    def test_header_support_label_preserves_requested_case(self):
        css = (HUGO / "static" / "styles.css").read_text()
        self.assertRegex(css, r"nav a\.coffee-nav\s*\{[^}]*text-transform:\s*none")

    def test_header_uses_a_complete_accessible_responsive_disclosure(self):
        header = (HUGO / "layouts" / "partials" / "header.html").read_text()
        css = (HUGO / "static" / "styles.css").read_text()
        script = (HUGO / "static" / "app.js").read_text()
        self.assertIn('class="text-button nav-toggle"', header)
        self.assertIn('aria-controls="primary-navigation"', header)
        self.assertIn('aria-expanded="false"', header)
        self.assertIn('id="primary-navigation"', header)
        self.assertIn("@media (max-width: 1024px)", css)
        self.assertNotIn("@media (max-width: 1100px)", css)
        self.assertNotIn("nav > a:not(.coffee-nav) { display: none; }", css)
        self.assertNotRegex(css, r"\.coffee-label\s*\{[^}]*clip:")
        for marker in ('matchMedia("(max-width: 1024px)")', 'aria-expanded', ".hidden", 'Escape', ".focus()"):
            self.assertIn(marker, script)

    def test_mobile_toggle_is_an_accessible_animated_hamburger(self):
        header = (HUGO / "layouts" / "partials" / "header.html").read_text()
        css = (HUGO / "static" / "styles.css").read_text()
        script = (HUGO / "static" / "app.js").read_text()

        self.assertIn('aria-label="Open menu"', header)
        self.assertIn('class="nav-toggle-icon" aria-hidden="true"', header)
        self.assertEqual(header.count('class="nav-toggle-line"'), 3)
        self.assertNotRegex(header, r">\s*Menu\s*</button>")
        self.assertRegex(css, r"\.nav-toggle-icon\s*\{[^}]*position:\s*relative[^}]*width:\s*24px[^}]*height:\s*18px")
        self.assertRegex(css, r"\.nav-toggle-line\s*\{[^}]*transition:")
        self.assertRegex(css, r"\.nav-toggle\[aria-expanded=\"true\"\][^{]*\.nav-toggle-line:first-child\s*\{[^}]*rotate\(45deg\)")
        self.assertRegex(css, r"\.nav-toggle\[aria-expanded=\"true\"\][^{]*\.nav-toggle-line:nth-child\(2\)\s*\{[^}]*opacity:\s*0")
        self.assertRegex(css, r"\.nav-toggle\[aria-expanded=\"true\"\][^{]*\.nav-toggle-line:last-child\s*\{[^}]*rotate\(-45deg\)")
        self.assertIn('navToggle.setAttribute("aria-label", expanded ? "Close menu" : "Open menu")', script)

    def test_mobile_hamburger_respects_reduced_motion(self):
        css = (HUGO / "static" / "styles.css").read_text()
        self.assertRegex(
            css,
            r"@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?"
            r"\.nav-toggle-line\s*\{\s*transition:\s*none\s*!important",
        )

    def test_mobile_hamburger_uses_a_new_asset_version(self):
        config = (HUGO / "hugo.toml").read_text()
        self.assertIn('assetVersion = "20260719-3"', config)

    def test_header_support_control_is_compact_and_flat(self):
        css = (HUGO / "static" / "styles.css").read_text()
        match = re.search(r"nav a\.coffee-nav\s*\{(?P<body>[^}]*)\}", css)
        self.assertIsNotNone(match)
        assert match is not None
        rule = match.group("body")
        self.assertNotIn("background:", rule)
        self.assertNotIn("box-shadow:", rule)
        self.assertNotRegex(rule, r"(?:^|;)\s*border:")
        self.assertIn("padding-inline: .6rem", rule)
        self.assertNotRegex(css, r"nav a\.coffee-nav:hover\s*\{[^}]*(?:transform|box-shadow)")


class UpdatesLinkStylingTest(unittest.TestCase):
    """Updates intro and RSS links reuse the established article-link treatment."""

    def test_updates_intro_links_share_article_link_css(self):
        css = (HUGO / "static" / "styles.css").read_text()
        match = re.search(
            r"\.article-body a,\s*\.article-back a,\s*\.maintenance-item a,\s*"
            r"\.program-intro a\s*\{(?P<body>[^}]*)\}",
            css,
        )
        self.assertIsNotNone(match)
        assert match is not None
        rule = match.group("body")
        self.assertIn("color: inherit", rule)
        self.assertIn("text-decoration-color: var(--red)", rule)
        self.assertIn("text-decoration-thickness: 2px", rule)
        self.assertIn("text-underline-offset: 3px", rule)

    def test_updates_link_style_uses_a_new_asset_version(self):
        config = (HUGO / "hugo.toml").read_text()
        self.assertIn('assetVersion = "20260719-3"', config)


class UpdatesDateSpacingTest(unittest.TestCase):
    """Update dates stay on one line with deliberate space before the title."""

    def test_update_rows_use_scoped_date_title_spacing(self):
        template = (HUGO / "layouts" / "updates" / "list.html").read_text()
        css = (HUGO / "static" / "styles.css").read_text()

        self.assertIn('class="service update-entry"', template)
        self.assertRegex(
            css,
            r"\.update-entry\s*\{[^}]*grid-template-columns:\s*minmax\(7\.5rem, auto\)"
            r"[^}]*column-gap:\s*clamp\(1\.5rem, 2vw, 2rem\)",
        )
        self.assertRegex(
            css,
            r"\.update-entry \.num-date\s*\{[^}]*white-space:\s*nowrap",
        )
        self.assertRegex(
            css,
            r"@media \(max-width: 820px\)\s*\{[\s\S]*?\.update-entry\s*\{"
            r"[^}]*grid-template-columns:\s*minmax\(7\.25rem, auto\) minmax\(0, 1fr\) auto"
            r"[^}]*column-gap:\s*1rem",
        )

    def test_update_date_spacing_uses_a_new_asset_version(self):
        config = (HUGO / "hugo.toml").read_text()
        self.assertIn('assetVersion = "20260719-3"', config)


def _all_content_files():
    files = []
    for section in ("updates", "guides"):
        section_dir = CONTENT / section
        if section_dir.is_dir():
            files.extend(load_markdown_files(section_dir))
    return files


class ContentFrontMatterTest(unittest.TestCase):
    """Step 3: public-safe Markdown front matter/content validation."""

    def setUp(self):
        self.files = _all_content_files()
        self.assertTrue(
            self.files, f"no Updates/Guides content files found under {CONTENT} (need at least one each)"
        )

    def test_required_fields_present(self):
        for path in self.files:
            data = parse_frontmatter(path.read_text())
            missing = REQUIRED_FRONTMATTER_FIELDS - data.keys()
            self.assertEqual(missing, set(), f"{path}: missing required fields {missing}")

    def test_no_unknown_fields(self):
        for path in self.files:
            data = parse_frontmatter(path.read_text())
            unknown = data.keys() - KNOWN_FRONTMATTER_FIELDS
            self.assertEqual(unknown, set(), f"{path}: unknown front matter fields {unknown}")

    def test_dates_are_timezone_aware(self):
        for path in self.files:
            data = parse_frontmatter(path.read_text())
            require_rfc3339_datetime(data["date"], "date")
            if "updated" in data:
                require_rfc3339_datetime(data["updated"], "updated")

    def test_no_future_dated_non_draft_posts(self):
        now = datetime.now(timezone.utc)
        for path in self.files:
            data = parse_frontmatter(path.read_text())
            is_draft = data.get("draft") is True
            dt = require_rfc3339_datetime(data["date"], "date")
            if not is_draft:
                self.assertLessEqual(
                    dt, now, f"{path}: non-draft post is dated in the future ({dt.isoformat()})"
                )

    def test_no_draft_content_published(self):
        for path in self.files:
            data = parse_frontmatter(path.read_text())
            self.assertIsNot(
                data.get("draft"), True, f"{path}: draft posts must not ship in this prototype"
            )

    def test_affected_services_are_allowed(self):
        for path in self.files:
            data = parse_frontmatter(path.read_text())
            services = data.get("affected_services", [])
            if isinstance(services, str):
                services = [services]
            unknown = set(services) - ALLOWED_SERVICES
            self.assertEqual(
                unknown, set(), f"{path}: unknown affected_services {unknown} (allowed: {sorted(ALLOWED_SERVICES)})"
            )

    def test_maintenance_metadata_is_valid_when_present(self):
        for path in self.files:
            data = parse_frontmatter(path.read_text())
            if "status" in data:
                self.assertIn(data["status"], ALLOWED_STATUS, f"{path}: invalid status {data['status']!r}")
            if "severity" in data:
                self.assertIn(data["severity"], ALLOWED_SEVERITY, f"{path}: invalid severity {data['severity']!r}")
            for field in ("starts_at", "ends_at", "expires_at"):
                if field in data:
                    require_rfc3339_datetime(data[field], field)

    def test_slugs_are_unique_and_match_filename(self):
        seen = {}
        for path in self.files:
            data = parse_frontmatter(path.read_text())
            slug = data["slug"]
            self.assertEqual(
                slug, path.stem, f"{path}: slug {slug!r} must match filename stem {path.stem!r}"
            )
            bucket = seen.setdefault(path.parent.name, {})
            self.assertNotIn(slug, bucket, f"{path}: duplicate slug {slug!r} in section {path.parent.name}")
            bucket[slug] = path

    def test_no_secret_or_private_address_patterns(self):
        for path in self.files:
            text = path.read_text()
            for pattern in SECRET_PATTERNS:
                self.assertIsNone(
                    pattern.search(text), f"{path}: possible secret pattern matched {pattern.pattern!r}"
                )
            for pattern in PRIVATE_ADDRESS_PATTERNS:
                self.assertIsNone(
                    pattern.search(text), f"{path}: possible private address/hostname pattern matched {pattern.pattern!r}"
                )

    def test_frontmatter_is_well_formed_toml_fence(self):
        for path in self.files:
            try:
                parse_frontmatter(path.read_text())
            except FrontMatterError as exc:
                self.fail(f"{path}: {exc}")

    def test_malformed_toml_is_rejected(self):
        malformed = '+++\ntitle = "broken"\ntags = ["open"\n+++\nbody\n'
        with self.assertRaises(FrontMatterError):
            parse_frontmatter(malformed)

    def test_body_is_non_empty(self):
        for path in self.files:
            body = content_body(path.read_text()).strip()
            self.assertTrue(body, f"{path}: article body must not be empty")


REQUIRED_ROUTE_TEMPLATES = [
    "layouts/updates/list.html",
    "layouts/updates/single.html",
    "layouts/guides/list.html",
    "layouts/guides/single.html",
    "layouts/_default/404.html",
]

PARTIAL_CALL_RE = re.compile(r'partial\s+"([^"]+)"')


class GeneratedRoutesAndRssTest(unittest.TestCase):
    """Step 4: required generated routes (Updates/Guides list+single, 404) and RSS."""

    def test_required_route_templates_exist(self):
        missing = [f for f in REQUIRED_ROUTE_TEMPLATES if not (HUGO / f).is_file()]
        self.assertEqual(missing, [], f"missing required route templates under {HUGO}: {missing}")

    def test_updates_section_enables_rss_output(self):
        index = CONTENT / "updates" / "_index.md"
        self.assertTrue(index.is_file(), f"missing {index}")
        data = parse_frontmatter(index.read_text())
        outputs = data.get("outputs", [])
        outputs_lower = {o.lower() for o in outputs} if isinstance(outputs, list) else set()
        self.assertIn("rss", outputs_lower, f"{index}: updates section front matter must enable rss output")

    def test_layout_templates_have_balanced_action_delimiters(self):
        layouts_dir = HUGO / "layouts"
        self.assertTrue(layouts_dir.is_dir(), f"missing {layouts_dir}")
        for path in sorted(layouts_dir.rglob("*.html")):
            text = path.read_text()
            opens = text.count("{{")
            closes = text.count("}}")
            self.assertEqual(
                opens, closes, f"{path}: unbalanced {{ }} action delimiters ({opens} opens vs {closes} closes)"
            )

    def test_referenced_partials_exist(self):
        layouts_dir = HUGO / "layouts"
        partials_dir = layouts_dir / "partials"
        self.assertTrue(layouts_dir.is_dir(), f"missing {layouts_dir}")
        for path in sorted(layouts_dir.rglob("*.html")):
            text = path.read_text()
            for name in PARTIAL_CALL_RE.findall(text):
                candidate = partials_dir / name
                self.assertTrue(candidate.is_file(), f"{path}: references missing partial {name!r}")


DOCKERFILE = ROOT / "Dockerfile.hugo"
DOCKERIGNORE = ROOT / "Dockerfile.hugo.dockerignore"
NGINX_CONF = ROOT / "nginx.hugo.conf"
COMPOSE = ROOT / "compose.hugo.yaml"
SECURITY_HEADERS_CONF = ROOT / "security-headers.conf"
EXISTING_NGINX_IMAGE_LINE = (
    "nginxinc/nginx-unprivileged:1.30.3-alpine@sha256:"
    "a0c30a699fa18d7bbdc6faf197742741a1f5e4631066abf18578dc8102b62c5d"
)
REQUIRED_SECURITY_HEADERS = (
    "Content-Security-Policy",
    "Referrer-Policy",
    "X-Content-Type-Options",
    "Permissions-Policy",
)


class RuntimeHardeningTest(unittest.TestCase):
    """Step 5: Dockerfile/Nginx/Compose preserve the existing hardening posture."""

    def test_dockerfile_uses_verified_hugo_builder_tag_and_digest(self):
        text = DOCKERFILE.read_text()
        self.assertIn(
            "ghcr.io/gohugoio/hugo:v0.164.0@sha256:"
            "f8671f2299e60154536c158bff8ce27f6eef4dddbbfc73bcce66263276ae0f80",
            text,
        )
        self.assertNotIn("--platform=", text, "Compose/CLI select linux/amd64 without Dockerfile platform warnings")
        self.assertIn("--cleanDestinationDir", text)

    def test_dockerfile_is_multi_stage_and_keeps_pinned_nginx_final_image(self):
        text = DOCKERFILE.read_text()
        self.assertGreaterEqual(text.count("FROM "), 2, "Dockerfile.hugo must be multi-stage")
        self.assertIn(EXISTING_NGINX_IMAGE_LINE, text)
        self.assertIn("USER 101:101", text)

    def test_nginx_conf_has_connect_src_none_and_no_external_origins(self):
        text = SECURITY_HEADERS_CONF.read_text()
        self.assertIn("connect-src 'none'", text)
        csp_lines = [line for line in text.splitlines() if "Content-Security-Policy" in line]
        self.assertTrue(csp_lines, "no CSP header found")
        for line in csp_lines:
            self.assertNotIn("http://", line)
            self.assertNotIn("https://", line)

    def test_nginx_conf_has_real_404_and_security_headers(self):
        text = NGINX_CONF.read_text()
        self.assertIn("error_page 404", text)
        self.assertIn("location = /healthz.txt", text)
        headers = SECURITY_HEADERS_CONF.read_text()
        for header in ("Content-Security-Policy", "Referrer-Policy", "X-Content-Type-Options", "Permissions-Policy"):
            self.assertIn(header, headers)

    def test_docker_context_is_allowlisted_and_excludes_generated_state(self):
        self.assertTrue(DOCKERIGNORE.is_file(), f"missing {DOCKERIGNORE}")
        text = DOCKERIGNORE.read_text()
        self.assertTrue(text.lstrip().startswith("**"), "Docker context must default-deny")
        for required in (
            "!Dockerfile.hugo",
            "!nginx.hugo.conf",
            "!security-headers.conf",
            "!hugo/**",
            "hugo/public/",
            "hugo/.hugo_build.lock",
            "hugo/resources/",
            "hugo/cache/",
            "hugo/.cache/",
        ):
            self.assertIn(required, text)

    def test_article_and_maintenance_components_are_scoped_and_styled(self):
        css = (HUGO / "static" / "styles.css").read_text()
        for selector in (
            ".article {",
            ".article-title",
            ".article-body",
            ".article-back",
            ".maintenance-banner",
            ".maintenance-item",
        ):
            self.assertIn(selector, css, f"missing scoped theme style {selector}")

    def test_root_ignore_rules_exclude_hugo_generated_state_after_allowlist(self):
        root_ignore = ROOT.parent / ".gitignore"
        text = root_ignore.read_text()
        allow_at = text.index("!walshit-landing/hugo/**")
        for pattern in (
            "walshit-landing/hugo/public/",
            "walshit-landing/hugo/.hugo_build.lock",
            "walshit-landing/hugo/hugo_stats.json",
            "walshit-landing/hugo/resources/_gen/",
            "walshit-landing/hugo/cache/",
            "walshit-landing/hugo/.cache/",
        ):
            self.assertGreater(text.index(pattern), allow_at, f"{pattern} must override the Hugo allowlist")

    def test_compose_hardening_flags_present(self):
        text = COMPOSE.read_text()
        for token in (
            "read_only: true",
            "no-new-privileges:true",
            "cap_drop:",
            "- ALL",
            'user: "101:101"',
            "tmpfs:",
            "healthcheck:",
        ):
            self.assertIn(token, text, f"missing hardening token: {token!r}")

    def test_compose_has_no_env_file_or_volumes(self):
        text = COMPOSE.read_text()
        self.assertNotIn("env_file", text)
        self.assertNotIn(".env", text)
        self.assertNotRegex(text, re.compile(r"^\s*volumes:", re.MULTILINE))

    def test_compose_declares_independent_project_name(self):
        text = COMPOSE.read_text()
        self.assertRegex(
            text,
            re.compile(r"^name:\s*walshit-landing-hugo\s*$", re.MULTILINE),
            "compose.hugo.yaml must declare its own top-level Compose project `name:` "
            "so it never shares a project with the legacy compose.yaml",
        )

    def test_compose_uses_immutable_candidate_image_tag(self):
        text = COMPOSE.read_text()
        self.assertIn("image: walshit-landing-hugo:20260716-2", text)
        self.assertIn("platform: linux/amd64", text)
        self.assertNotIn("image: walshit-landing-hugo:prototype", text)


def _location_blocks(server_block):
    return nginxconf.find_blocks(
        server_block, lambda b: b.header_words and b.header_words[0] == "location"
    )


class NginxHeaderInheritanceTest(unittest.TestCase):
    """Step 6 (HIGH finding #1): nginx add_header does NOT merge across levels.

    A location block that defines even one add_header (e.g. just
    Cache-Control) silently drops every add_header inherited from the
    server block. This simulates nginx's actual inheritance rule against
    the real config text (including any `include` files) so the test
    fails for real if a location loses its security headers.
    """

    def _parse_root(self):
        text = NGINX_CONF.read_text()
        return nginxconf.parse(text, ROOT)

    def test_every_location_retains_all_security_headers(self):
        root = self._parse_root()
        servers = nginxconf.find_blocks(root, lambda b: b.header_words == ["server"])
        self.assertTrue(servers, "no server block found in nginx.hugo.conf")
        checked_any = False
        for server in servers:
            for location in _location_blocks(server):
                checked_any = True
                for header_name in REQUIRED_SECURITY_HEADERS:
                    directives = location.effective_directives("add_header")
                    header_present = any(
                        len(d.words) >= 2 and d.words[1] == header_name for d in directives
                    )
                    self.assertTrue(
                        header_present,
                        f"location {location.header!r} is missing effective header "
                        f"{header_name!r} (nginx add_header inheritance is broken by "
                        "this location's own add_header directives)",
                    )
        self.assertTrue(checked_any, "no location blocks found in nginx.hugo.conf")

    def test_every_location_retains_connect_src_none(self):
        root = self._parse_root()
        servers = nginxconf.find_blocks(root, lambda b: b.header_words == ["server"])
        for server in servers:
            for location in _location_blocks(server):
                directives = location.effective_directives("add_header")
                csp = next(
                    (d for d in directives if len(d.words) >= 2 and d.words[1] == "Content-Security-Policy"),
                    None,
                )
                self.assertIsNotNone(csp, f"location {location.header!r} has no effective CSP header")
                self.assertIn("connect-src 'none'", csp.words[2].strip('"'))


class NginxSecurityHeadersIncludeCoverageTest(unittest.TestCase):
    """Step 6 (LOW finding #6): static include-coverage check.

    Every location block in nginx.hugo.conf must explicitly `include` the
    reviewed security-headers.conf file, and that file must be copied into
    the final image by Dockerfile.hugo.
    """

    def test_security_headers_conf_exists_and_defines_all_headers(self):
        self.assertTrue(SECURITY_HEADERS_CONF.is_file(), f"missing {SECURITY_HEADERS_CONF}")
        text = SECURITY_HEADERS_CONF.read_text()
        for header_name in REQUIRED_SECURITY_HEADERS:
            self.assertIn(header_name, text, f"security-headers.conf missing {header_name}")
        self.assertIn("connect-src 'none'", text)

    def test_every_location_includes_security_headers_conf(self):
        text = NGINX_CONF.read_text()
        root = nginxconf.parse(text, ROOT)
        servers = nginxconf.find_blocks(root, lambda b: b.header_words == ["server"])
        self.assertTrue(servers, "no server block found in nginx.hugo.conf")
        for server in servers:
            for location in _location_blocks(server):
                # After include-expansion, security-headers.conf's own
                # add_header directives must appear directly on this
                # location (not merely inherited), proving explicit coverage.
                own = location.own_directives("add_header")
                own_headers = {d.words[1] for d in own if len(d.words) >= 2}
                missing = set(REQUIRED_SECURITY_HEADERS) - own_headers
                self.assertEqual(
                    missing,
                    set(),
                    f"location {location.header!r} does not explicitly include "
                    f"security-headers.conf (missing own add_header for {sorted(missing)})",
                )

    def test_dockerfile_copies_security_headers_conf_into_image(self):
        text = DOCKERFILE.read_text()
        self.assertIn("security-headers.conf", text)
        self.assertIn("COPY security-headers.conf /etc/nginx/security-headers.conf", text)


if __name__ == "__main__":
    sys.exit(unittest.main())
