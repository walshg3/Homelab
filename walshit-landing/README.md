# Walshit landing page

Production-ready static landing page served by unprivileged Nginx. The approved page design and copy live in `site/`; its only browser-side behavior is the local setup/help dialog.

## Local validation

Run the portable source checks from this directory:

```sh
python3 - <<'PY'
from pathlib import Path

required = {
    "Dockerfile",
    "compose.yaml",
    "nginx.conf",
    "site/index.html",
    "site/styles.css",
    "site/app.js",
    "site/healthz.txt",
}
missing = sorted(path for path in required if not Path(path).is_file())
assert not missing, f"missing required files: {missing}"

html = Path("site/index.html").read_text()
assert Path("site/healthz.txt").read_text().strip() == "walshit-landing-ok"
assert "Bad name." in html
assert 'href="styles.css"' in html
assert 'src="app.js"' in html
for url in (
    "https://app.plex.tv/desktop/",
    "https://home.walshit.com",
    "https://requests.walshit.com",
    "https://books.walshit.com",
):
    assert url in html, f"missing canonical link: {url}"
print("static source validation passed")
PY

git diff --check
```

On the deployment host, also validate the rendered Compose model:

```sh
docker compose config --quiet
```

The runtime image is intentionally pinned for `linux/amd64`. Building and runtime verification still require a Docker daemon in the deployment environment. The portable source checks require no network access, live credentials, or a running container.

## Deployment boundary

The intended deployment is exactly one static container on the `linux/amd64` host that owns `192.168.5.252`, publishing `192.168.5.252:3003` to container port `8080`. The immutable local image tag is `walshit-landing:20260713`. TLS and public routing, if any, terminate outside this stack; Nginx intentionally does not emit HSTS.

This stack has no secrets, no `.env` requirement, and no persistence. It makes no application network calls and has no database or writable volume. Its only writable runtime filesystem is the bounded `/tmp` tmpfs required by unprivileged Nginx.

## Rollback

Retain the previous image tag before replacing the running revision. To roll back, restore the previous Compose revision (or change `image:` back to the previous image tag) and recreate only the `landing` service using the deployment host's normal change procedure. Confirm `/healthz.txt` returns exactly `walshit-landing-ok` afterward.

Rollback does not require deleting volumes, pruning images, or removing unrelated containers. Keep the failed image tag available for diagnosis until the incident is resolved.

---

## Hugo site (Updates / Guides)

The production Hugo site lives alongside the legacy static page above,
under `hugo/`, `tests/`, `Dockerfile.hugo`, `nginx.hugo.conf`, and
`compose.hugo.yaml`. It does not replace or modify the legacy `site/`,
`Dockerfile`, `nginx.conf`, or `compose.yaml` files, which remain the
rollback target. The prototype adds two content sections — `/updates/`
(reverse-chronological planned maintenance/service notes, with RSS) and
`/guides/` (a short evergreen how-to index) — while reproducing the live
baseline theme (`prototype-artifacts/baseline/`) for the homepage, header,
footer, and setup/help dialog.

All commands below run from this directory (`walshit-landing/`).

### Author

Add a new update or guide with Hugo's archetypes using Hugo `v0.164.0`:

```sh
hugo new updates/some-slug.md --source hugo
hugo new guides/some-slug.md --source hugo
```

Fill in `title`, `summary`, `slug` (must match the filename stem), `date`
(RFC3339, timezone-aware), and set `draft = false` when ready to publish.
Optional fields: `updated`, `tags`, `affected_services` (one or more of
`plex`, `framerr`, `seerr`, `audiobookshelf`, `site-wide`), and maintenance
metadata (`status`, `severity`, `starts_at`, `ends_at`, `expires_at`). A
`status` of `planned` or `in-progress` promotes the post into the homepage
maintenance treatment. The timestamp fields are validated metadata only in
this MVP; they do not schedule publication or automatically hide a banner.

### Preview

```sh
hugo server --source hugo --disableFastRender
```

### Validate

Validation requires Python 3.11 or newer (`tomllib` is part of the standard
library). The generated-output suite fails closed unless `HUGO_BIN` points to
Hugo exactly at v0.164.0.

`tests/test_site.py` performs a vertical-slice source check: front-matter
schema/safety, unique slugs, timezone-aware dates, allowed
`affected_services`, no drafts/future-dated posts, required generated
route templates, RSS output config, and runtime hardening in
`Dockerfile.hugo`/`nginx.hugo.conf`/`compose.hugo.yaml`:

```sh
python3 tests/test_site.py -v
```

```sh
HUGO_BIN=/absolute/path/to/verified/hugo-v0.164.0 python3 tests/test_generated.py -v
```

The generated-output suite performs a real warning-free Hugo build in a
temporary directory, then checks required routes/assets, RSS/XML,
internal links, article classes, external runtime assets, forms, and
browser networking APIs.

```sh
git diff --check
```

All three checks were run against the prototype. The Hugo Linux amd64 archive
was verified against the official v0.164.0 checksums file before use; its
SHA-256 was
`d9c8b17285ea4ec004d9f814273ea910f2051ce02c284993fd1f91ba455ae50d`.

### Build

```sh
docker build --platform linux/amd64 -f Dockerfile.hugo -t walshit-landing-hugo:20260716-2 .
```

Or render the static output with a locally installed, verified Hugo v0.164.0
binary:

```sh
hugo --source hugo --minify --gc --cleanDestinationDir --destination public
```

The local Hugo build was run successfully and produced the homepage,
Updates and Guides indexes/articles, Updates RSS, sitemap, 404 page, and
four static assets without warnings. The Docker build remains unverified on
this host because no Docker daemon is available. `Dockerfile.hugo` pins the
official Hugo v0.164.0 multi-architecture index by verified digest and keeps
the existing digest-pinned unprivileged Nginx image as the final stage.

### Hardened-runtime test

```sh
docker compose -f compose.hugo.yaml config --quiet
docker compose -f compose.hugo.yaml up -d
curl -fsS http://127.0.0.1:3004/healthz.txt
docker compose -f compose.hugo.yaml down
```

**Not run here:** requires a Docker daemon, unavailable in this sandbox.

### Local release preview

Build and tag the image, then bring the prototype service up in its declared
`walshit-landing-hugo` Compose project, independent of the legacy `landing`
service:

```sh
docker compose -f compose.hugo.yaml build
docker compose -f compose.hugo.yaml up -d
```

**Not run here:** requires Docker.

### Rollback

The prototype is fully isolated: stopping and removing the
`landing-hugo` service never touches the legacy `landing` service or its
image.

```sh
docker compose -f compose.hugo.yaml down
```

The legacy stack (`compose.yaml`, `Dockerfile`, `nginx.conf`, `site/`)
is untouched by the Hugo site and remains a separately retained rollback input.

## GitHub Actions deployment

`.github/workflows/walshit.yml` runs for every pull request and push to `main`
so its required check cannot be skipped by path filtering. It installs checksum-verified Hugo
v0.164.0, runs all source/generated/CI contract tests, renders both Compose
models, and performs a real hardened-container smoke test. After publication,
the publish job pulls and smoke-tests the exact pushed digest before deployment
can begin. A successful
`main` push publishes both
`ghcr.io/walshg3/walshit-landing:<full-commit-sha>` and the convenience
`main` tag. Production always deploys the full commit-SHA tag; it never uses
`main` as a deployment identity. The publish job also returns the immutable
manifest digest, and the production Compose model runs the `image@sha256`
reference after proving the commit tag resolves to that exact digest.

Repository-side workflow syntax can be checked with
`actionlint -config-file actionlint.yaml .github/workflows/walshit.yml` from
the repository root. The explicit config path registers the private
`walshit-production` runner label.

The publish job uses only the workflow-scoped `GITHUB_TOKEN`. No repository or environment secrets are required. Set the GHCR package to public so the
deployment wrapper can pull it anonymously; if the package must remain
private, provision a host-local read-only package credential outside GitHub
Actions and outside this repository.

### Required GitHub setup

1. Create a protected production Environment named `production`, restricted to `main`; add a
   required reviewer when the repository plan supports it.
2. Protect `main`: require pull requests and the `Test and container smoke
   test` check, require current branches, and block force pushes/deletion.
3. Because this repository is public, first choose **Require approval for all outside collaborators** under **Settings > Actions > General > Fork pull request workflows from outside collaborators**. Never approve untrusted workflow
   changes while production runner capacity is online. Only then register a
   repository-scoped self-hosted runner on the containers VM with labels
   `self-hosted`, `linux`, `x64`, and `walshit-production`. Run it as a dedicated
   unprivileged `walshit-deploy` account, preferably as an ephemeral/JIT runner
   that is offline except for a reviewed deployment. Install `git` for the
   freshness check. Do not add that account to the Docker or sudo groups.
4. Install `compose.production.yaml` as root-owned mode `0644` at
   `/etc/walshit/compose.production.yaml` and install
   `scripts/deploy-production.sh` as root-owned mode `0755` at
   `/usr/local/sbin/deploy-walshit`.
5. Add one exact sudoers rule allowing only the `walshit-deploy` account to
   execute `/usr/local/sbin/deploy-walshit` as root. Do not grant arbitrary
   Docker, shell, file-copy, or environment-preservation privileges.
6. Confirm the retained external network
   `walshit-landing-hugo-prod-9238dbfa_default` exists and still has the
   reviewed subnet before enabling the Environment.

The deploy job has a production concurrency lock and rechecks remote `main`
immediately before invoking the wrapper, so a superseded queued run cannot
deploy after a newer revision. The wrapper accepts exactly one lowercase
40-character commit SHA plus its `sha256` digest, verifies its own and the
trusted Compose file's root ownership/modes, takes a host lock, pulls the
exact commit tag, and proves by immutable Docker image-ID equality that the tag
resolves to the supplied digest and revision label. The host lock lives at
`/run/walshit/production.lock` inside a root-owned mode `0700` directory. It
first creates only `landing-hugo` as a loopback candidate on the
retained external bridge. After candidate acceptance and a second remote-main
check, it stops but retains the current production container and starts a
SHA-named live project on the fixed origin. Each Compose start uses
`--no-deps --no-build --pull never`; it never runs Compose `down`, removes
volumes/networks/containers, or prunes resources.

Both candidate and live acceptance wait for Docker health and the exact
`walshit-landing-hugo-ok` body. A failed candidate is stopped and retained
without touching production. A failed live cutover stops and retains the
rejected container, restarts the retained predecessor, waits for predecessor
health, and exits nonzero with a clear rollback result. Successful releases
also leave the predecessor stopped and retained. Cleanup remains a separate,
explicitly approved operation. A failed candidate or repeated revision is
retained for diagnosis; a same-SHA retry requires operator review and removal
of only that SHA-named retained project. If no container owns production port
3003, restore the retained predecessor before retrying rather than bypassing
the fail-closed listener check. Exit and signal traps attempt the same rollback
when a catchable termination signal reaches the wrapper after cutover begins;
the deploy job allows 40 minutes for pull, acceptance, and rollback. A failed
emergency rollback prints an immediate operator-recovery warning.

The production Compose model declares no persistent volume. Its retained
external network, fixed origin binding, read-only root, non-root identity,
capability drop, resource limits, tmpfs, health check, and bounded logs are
all explicit. Cloudflare/DNS are outside this pipeline and are not mutated.
