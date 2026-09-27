# Local staging operations

## Permanent public status watchdog

The no-agent `status-public-watchdog` checks the public user path independently
of the status origin. Its restricted JSON config is resolved only from
`${HERMES_HOME}/config/status-public-watchdog/watchdog.json` by
`run_public_status_watchdog.py`. `HERMES_HOME` must be an absolute, normalized,
non-root path. The config is an owner-controlled regular file with mode `0600`;
it supplies the exact health, summary, HTTP-root, HTTPS-root, webhook-env,
state, and lock paths, the public recursive resolvers, and all timeout and size
bounds. The production URLs are the `status.walshit.com` health, v2 summary,
plain-HTTP root, and HTTPS root described by the checked-in watchdog contract.
The strict config has this complete shape; replace only the three illustrative
absolute filesystem paths with reviewed absolute, normalized, non-root paths:

```json
{
  "schema_version": "1",
  "health_url": "https://status.walshit.com/healthz.txt",
  "summary_url": "https://status.walshit.com/status/v2/summary.json",
  "http_root_url": "http://status.walshit.com/",
  "https_root_url": "https://status.walshit.com/",
  "webhook_env_file": "/absolute/restricted/status-public-watchdog/discord.env",
  "state_file": "/absolute/restricted/status-public-watchdog/runtime/state.json",
  "lock_file": "/absolute/restricted/status-public-watchdog/runtime/watchdog.lock",
  "resolvers": ["1.1.1.1", "8.8.8.8", "9.9.9.9"],
  "operation_timeout_seconds": 10,
  "cycle_timeout_seconds": 45,
  "max_health_body_bytes": 128,
  "max_summary_body_bytes": 262144,
  "max_dns_output_bytes": 8192,
  "max_age_seconds": 180,
  "future_tolerance_seconds": 5
}
```

Install a fresh profile layout from the repository artifacts with the exact
owner-only modes below. Run this from `status-walshit/`; do not source the
Discord env file or print any config, state, or secret body:

```sh
set -eu
: "${HERMES_HOME:?set the active Hermes profile root}"
case "$HERMES_HOME" in /*) ;; *) exit 2 ;; esac
test "$HERMES_HOME" != /
test "$(realpath -m "$HERMES_HOME")" = "$HERMES_HOME"
install -d -m 700 \
  "$HERMES_HOME/scripts" \
  "$HERMES_HOME/scripts/publisher" \
  "$HERMES_HOME/config/status-public-watchdog" \
  "$HERMES_HOME/runtime/status-public-watchdog"
install -m 700 operations/public_status_watchdog.py "$HERMES_HOME/scripts/public_status_watchdog.py"
install -m 700 operations/run_public_status_watchdog.py "$HERMES_HOME/scripts/run_public_status_watchdog.py"
install -m 600 publisher/__init__.py "$HERMES_HOME/scripts/publisher/__init__.py"
install -m 600 publisher/schema.py "$HERMES_HOME/scripts/publisher/schema.py"
WATCHDOG_CONFIG_FILE="$HERMES_HOME/config/status-public-watchdog/watchdog.json"
test ! -e "$WATCHDOG_CONFIG_FILE"
test ! -L "$WATCHDOG_CONFIG_FILE"
install -m 600 /dev/null "$WATCHDOG_CONFIG_FILE"
```

Edit the new config without changing its mode. Use
`$HERMES_HOME/runtime/status-public-watchdog/state.json` and
`$HERMES_HOME/runtime/status-public-watchdog/watchdog.lock` as its exact state
and lock paths. Set `WATCHDOG_SECRET_FILE` to the existing restricted Discord
env path entered as `webhook_env_file`; the installer must not replace or
rewrite that secret:

```sh
${EDITOR:?set an interactive editor} "$WATCHDOG_CONFIG_FILE"
: "${WATCHDOG_SECRET_FILE:?set the existing configured Discord env path}"
case "$WATCHDOG_SECRET_FILE" in /*) ;; *) exit 2 ;; esac
test -f "$WATCHDOG_CONFIG_FILE"
test ! -L "$WATCHDOG_CONFIG_FILE"
test "$(stat -c %h "$WATCHDOG_CONFIG_FILE")" -eq 1
test "$(stat -c %u "$WATCHDOG_CONFIG_FILE")" -eq "$(id -u)"
test "$(stat -c %a "$WATCHDOG_CONFIG_FILE")" -eq 600
test -d "$HERMES_HOME/runtime/status-public-watchdog"
test ! -L "$HERMES_HOME/runtime/status-public-watchdog"
test "$(stat -c %u "$HERMES_HOME/runtime/status-public-watchdog")" -eq "$(id -u)"
test "$(stat -c %a "$HERMES_HOME/runtime/status-public-watchdog")" -eq 700
test -f "$WATCHDOG_SECRET_FILE"
test ! -L "$WATCHDOG_SECRET_FILE"
test "$(stat -c %h "$WATCHDOG_SECRET_FILE")" -eq 1
test "$(stat -c %u "$WATCHDOG_SECRET_FILE")" -eq "$(id -u)"
test "$(stat -c %a "$WATCHDOG_SECRET_FILE")" -eq 600
```

These four copied Python artifacts are the complete application dependency
set; all other imports are from the Python standard library. Do not install a
third-party package for this watchdog.

The state and lock share an owner-controlled mode-`0700` directory. State is
atomically replaced at mode `0600`. The Discord env file contains exactly one
`DISCORD_WEBHOOK_URL` assignment and must be a regular, owner-readable file
with no group or world permission bits. The watchdog opens config, state, and
secret files with no-follow descriptor checks; never display any of them.

Every five-minute run checks HTTPS health and summary behavior, exact HTTP
redirection, and public A/AAAA resolution through every configured resolver.
The HTTP client ignores proxy environment variables, disables redirects, and
uses the platform CA trust store with certificate and exact-hostname
verification. DNS output may contain bounded, syntactically valid CNAME lines,
but every resolver must still return at least one correctly typed public A or
AAAA address. Summary freshness is measured from a UTC sample taken only after
the complete bounded response body has arrived.
The summary's schema and freshness determine watchdog validity, but valid
`degraded`, `outage`, `maintenance`, and `no-data` states do not make the
watchdog fail. Three consecutive watchdog failures send one outage alert; two
consecutive valid cycles send one recovery. Runs below a transition threshold
and healthy runs are silent. Cron delivery stays `local`; transition delivery
goes directly to the restricted Discord webhook with mentions disabled.

The configured cycle timeout is also enforced as a process-level real-time
timer across the lock-held transaction, including state load, probes, alert
delivery, and atomic persistence. The commit boundary is atomic replacement.
Before replacement, expiry leaves the prior state authoritative,
releases the lock, emits only `watchdog-cycle-hard-timeout`, and exits nonzero.
After the exact intended mode-`0600` state bytes are atomically visible at the
destination, the sample is committed. If replacement or the following
directory fsync is interrupted, the watchdog performs a bounded,
descriptor-checked metadata and exact-byte reconciliation: an exact match
returns success, while an absent, invalid, ambiguous, or nonmatching
destination fails closed with fixed output. A committed sample whose directory
fsync was interrupted has a narrow durability caveat: it is authoritative and
visible to the next run, but a subsequent host crash could lose the directory
entry before the filesystem makes it durable. A backward wall-clock sample is
a fixed local failure: the existing state is preserved byte-for-byte and no
alert is attempted.

Alert delivery is intentionally at-least-once: the transition is posted before
the updated state is persisted. A crash or local state-write failure in that
narrow interval can cause a duplicate transition alert on the next run. This
duplicate-crash window is preferable to recording an alert that Discord never
received.

To roll back, pause the `Independent public status watchdog` scheduler entry.
Preserve the wrapper, watchdog script, config, state, lock, and secret while the
scheduler is paused so investigation and safe resumption retain the evidence
and consecutive-run history. Do not delete or rewrite those files as part of
rollback.

This runbook is for the loopback-only replacement project
`status-walshit-staging-v2`. Always run commands from `status-walshit/` and
always combine the base and staging files:

```sh
docker compose -f compose.yaml -f compose.staging.yaml COMMAND
```

The top-level `name` in `compose.staging.yaml` selects
`status-walshit-staging-v2`; do not override it for final staging. The production
base publishes no ports and contains no gateway. The staging override adds the
gateway, and only that gateway binds `127.0.0.1:3015:8080`; it is not reachable
from the LAN. The core web service still publishes no port and remains attached
only to its no-egress, internal-only bridge.

The three bridges use explicit, non-overlapping private `/28` subnets:
publisher egress is `10.255.250.48/28`, core web is `10.255.250.64/28`, and
staging ingress is `10.255.250.32/28`. This avoids dependence on the Docker
host's exhausted automatic address pools. Verify these ranges do not overlap
host routes or existing Docker networks before bringing up the stack. Cloudflare
and DNS remain a separately gated future transaction.

Direct publishing from the core web container is intentionally not used. On the
observed Docker 28.4 runtime, a port binding requested from a container attached
only to an `internal: true` bridge appears in `HostConfig.PortBindings`, but no
host listener is installed and `NetworkSettings.Ports` remains empty. Adding an
ordinary egress-capable bridge to core web would weaken its isolation. The
staging-only gateway therefore joins `web-internal` and `staging-ingress` and
proxies only to `http://web:8080`.

The gateway runs as numeric user `101:101` with a read-only root, no added
capabilities, no-new-privileges, and bounded temporary storage, CPU, memory,
PIDs, logs, and health timing. It receives no secret, environment, state,
public-data, content, or Docker socket mount. Publisher remains egress-only with
no ports; core web remains internal-only with its read-only public volume and no
ports. Healthy dependencies enforce publisher → core web → gateway.

## Prepare the secret without printing it

Do not place values in command arguments, environment variables, shell history,
Compose, logs, tickets, or chat. The fixed container identity and a mode-0600
bind-mounted secret require the Docker-host operator to be exactly `1000:1000`.
Fail closed before creating the file, running Compose configuration, or starting
either service:

```sh
test "$(id -u)" -eq 1000
test "$(id -g)" -eq 1000
install -d -m 700 secrets
umask 077
${EDITOR:?set an interactive editor} secrets/plex-probe.env
chmod 600 secrets/plex-probe.env
test -f secrets/plex-probe.env
test ! -L secrets/plex-probe.env
test "$(stat -c %h secrets/plex-probe.env)" -eq 1
test "$(stat -c %u secrets/plex-probe.env)" -eq 1000
test "$(stat -c %g secrets/plex-probe.env)" -eq 1000
test "$(stat -c %a secrets/plex-probe.env)" -eq 600
```

Enter exactly two unquoted ASCII lines, one named `PLEX_TOKEN` and one named
`PLEX_MACHINE_IDENTIFIER`. Do not use comments, shell syntax, expansion, or
quotes. To copy an already prepared restricted file without displaying it:

```sh
install -m 600 /operator/restricted/plex-probe.env secrets/plex-probe.env
test -f secrets/plex-probe.env
test ! -L secrets/plex-probe.env
test "$(stat -c %h secrets/plex-probe.env)" -eq 1
test "$(stat -c %u secrets/plex-probe.env)" -eq 1000
test "$(stat -c %g secrets/plex-probe.env)" -eq 1000
test "$(stat -c %a secrets/plex-probe.env)" -eq 600
```

The destination is Git-ignored and mounted read-only at
`/run/secrets/plex-probe.env`. Do not run a command that prints its contents.
A host whose operator UID or GID differs from 1000 requires an explicitly
designed numeric-ID mapping before deployment; stop here until that design has
been separately reviewed.

## Configure, build, and start

Configuration validation does not require the secret to contain a real value:

```sh
docker compose -f compose.yaml -f compose.staging.yaml config --quiet
docker compose -f compose.yaml -f compose.staging.yaml build
docker compose -f compose.yaml -f compose.staging.yaml up -d
docker compose -f compose.yaml -f compose.staging.yaml ps
```

Build and startup are host-side acceptance steps. Review their output before
continuing; never paste probe credentials into an override or retry command.
An isolated, loopback-only staging acceptance was completed on 2026-07-22. No
push, public deployment, DNS, Cloudflare, tunnel, or production cutover occurred.

## Acceptance checks

Inspect bounded service status and sanitized logs:

```sh
docker compose -f compose.yaml -f compose.staging.yaml ps
docker compose -f compose.yaml -f compose.staging.yaml logs --no-color --tail 100 publisher web gateway
docker compose -f compose.yaml -f compose.staging.yaml exec publisher python3 -m publisher.runner health --public-dir /var/lib/status/public
```

The publisher health command prints only `publisher-health-ok` or
`publisher-health-failed`. Logs must contain no credentials, internal responses,
or document bodies.

Before relying on the listener, verify the rendered separation and then confirm
that gateway and core web are distinct running containers:

```sh
docker compose -f compose.yaml -f compose.staging.yaml config --format json | python3 -c 'import json,sys; m=json.load(sys.stdin); p=m["services"]["publisher"]; w=m["services"]["web"]; g=m["services"]["gateway"]; assert m["name"] == "status-walshit-staging-v2"; assert "ports" not in p and "ports" not in w; assert set(p["networks"]) == {"publisher-egress"}; assert set(w["networks"]) == {"web-internal"}; assert set(g["networks"]) == {"web-internal", "staging-ingress"}; assert g["ports"][0]["host_ip"] == "127.0.0.1" and int(g["ports"][0]["published"]) == 3015'
test "$(docker compose -f compose.yaml -f compose.staging.yaml ps -q web)" != "$(docker compose -f compose.yaml -f compose.staging.yaml ps -q gateway)"
```

The first assertion proves core web has no published port or egress network;
the second proves the gateway and core web are separate containers.

Check the exact health body, freshness, headers, and browser surface:

```sh
test "$(wget -qO- http://127.0.0.1:3015/healthz.txt)" = status-walshit-web-ok
curl --fail --silent --show-error --dump-header - --output /dev/null http://127.0.0.1:3015/
curl --fail --silent --show-error --dump-header - --output /dev/null http://127.0.0.1:3015/status/v2/summary.json
python3 -c 'import datetime,json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:3015/status/v2/summary.json")); t=datetime.datetime.fromisoformat(d["generated_at"].replace("Z","+00:00")); assert 0 <= (datetime.datetime.now(datetime.timezone.utc)-t).total_seconds() <= 180'
test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:3015/status/v1/summary.json)" = 404
test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:3015/status/v2/other.json)" = 404
test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:3015/status/v2/)" = 404
test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:3015/unrelated.json)" = 404
```

Confirm the HTML response carries the CSP, referrer, MIME-sniffing, and
permissions headers. Confirm JSON carries `Cache-Control: no-store, max-age=0`.

The page also requests `/status/v2/incidents.json` for resolved incidents and
completed maintenance. Verify its schema version `"2"`, exact `generated_at`
match with `summary.json`, descending resolved times, and freshness within
180 seconds. The archive is an independent display: an unavailable, stale, or
incoherent archive shows "Incident archive unavailable" while a valid summary
and history still show current status. A fresh same-generation archive stays
visible during revalidation; a new summary generation hides it until the
matching archive arrives. The static fallback and noscript copy continue to
describe the archive as unavailable when live data cannot be verified.
The HTML currently references `status.css` and `status.js` with cache version
`20260926-history2`; verify that version with the reviewed assets. Local tests
cover the contract and fallback in `test_incident_frontend.py` and
`test_web_source.py`; `scripts/render_check.py` covers rendered archive states.

Open `http://127.0.0.1:3015/` in a local browser and verify that unknown routes,
unknown JSON/status paths, and dotfiles return 404. The retired `/status/v1/*`
prefix must return 404 along with `/status/v2/other.json`, `/status/v2/`, and
unrelated JSON.

Observe at least three intervals (three 60-second publisher boundaries), then
check that generation timestamps advance and cycles do not overlap. Verify that
all three health checks stay healthy and logs remain sanitized. Restart each
service and repeat
status, health, freshness, header, and browser checks:

```sh
docker compose -f compose.yaml -f compose.staging.yaml restart publisher
docker compose -f compose.yaml -f compose.staging.yaml restart web
docker compose -f compose.yaml -f compose.staging.yaml restart gateway
docker compose -f compose.yaml -f compose.staging.yaml ps
```

## Stale-and-recovery drill

Stopping and starting the publisher is the routine reversible drill:

The actions are conventionally named `docker compose stop publisher` and
`docker compose start publisher`; for staging, retain both explicit files:

```sh
docker compose -f compose.yaml -f compose.staging.yaml stop publisher
docker compose -f compose.yaml -f compose.staging.yaml start publisher
```

After `stop publisher`, leave the web service running and verify the public data
becomes stale after 180 seconds while the last bounded documents remain
available. After `start publisher`, verify a fresh generation appears, publisher
health recovers, and the browser clears its stale warning. Re-run the three
intervals observation to confirm stable recovery.

## Rollback and destructive boundaries

The first failed project `status-walshit-staging` may be stopped in place, but
must not be removed, downed, or pruned without scoped approval. If stopping it
is approved operationally, retain the old project name explicitly:

```sh
docker compose -p status-walshit-staging -f compose.yaml -f compose.staging.yaml stop
```

Final staging uses only the `status-walshit-staging-v2` project. Its rollback is
`stop`, without removing volumes, images, or files:

```sh
docker compose -f compose.yaml -f compose.staging.yaml stop
```

`stop` is a routine reversible action. For either project, `down`, container or
volume removal, prune, image removal, secret deletion, runtime-file deletion,
and any other teardown are destructive/removal operations and require explicit
scoped approval identifying the exact project and objects. Never add `-v` or
prune as part of rollback.

## Incident and Maintenance content

Run every command in this section from the documented source directory:

```sh
cd status-walshit
```

An independent ntfy or Discord alert is only an operator signal. It leads to a
human decision about whether public narration is warranted. It must never
create or edit public content automatically, and automatic component state
remains separate from manual narration.

For an Incident, make a local copy with a lowercase validator-accepted
`*.toml` filename, edit every fictional field, and retain only the lifecycle
updates that have actually happened:

```sh
CONTENT_FILE=content/incidents/2026-07-22-short-name.toml
cp content/incidents/2026-XX-XX-example.toml.example "$CONTENT_FILE"
${EDITOR:?set an interactive editor} "$CONTENT_FILE"
python3 -m operations.validate_content
```

For Maintenance, follow the same process with its own template:

```sh
CONTENT_FILE=content/maintenance/2026-07-22-short-name.toml
cp content/maintenance/maintenance-template.toml.example "$CONTENT_FILE"
${EDITOR:?set an interactive editor} "$CONTENT_FILE"
python3 -m operations.validate_content
```

The validator uses the repository `content/` root by default. To validate a
separate candidate tree without printing its narrative or raw TOML, supply that
root explicitly:

```sh
python3 -m operations.validate_content /operator/review/status-content
```

It prints only active, closed, and issue counts followed by the loader's
sanitized relative filename and issue code for each rejection. Any issue makes
the command nonzero. Do not promote a source when the validator is nonzero.

Before staging, review the exact new file and use the established reviewed Git
and deployment path; this procedure does not bypass review or deploy directly:

```sh
git diff --no-index -- /dev/null "$CONTENT_FILE"
python3 -m operations.validate_content
```

Exit status `1` from that exact `git diff --no-index` command is expected: it
means the new file differs from `/dev/null` and the content was displayed for
review. Exit status `0` means there was no difference, and any status greater
than `1` is an error that must be resolved. A normal `git diff` does not display
an untracked file, so it is not the pre-staging review command for a new source.

The next publisher cycle will load an accepted change. Verify the public v2
`summary.json` active event and `incidents.json` closed-event history through
the separately approved public verification path. Append another reviewed
update for each real phase change. Resolve an Incident or complete Maintenance
only after public checks support that statement. Keep the closed source for the
reviewed retention period, then archive it only through another reviewed source
change; do not move unreviewed files into a deployment.

Public narrative prohibits credentials, private hosts, private addresses,
infrastructure details, media identities, media titles, raw probe output, and
recovery procedures. It also prohibits internal URLs or paths, personal
identities, and any detail that would turn the status page into an operations
guide. Titles and bodies must describe only visitor-visible impact and safe
progress.

The Incident lifecycle is `investigating` → `identified` → `monitoring` →
`resolved`. An active Incident may stop at any of the first three phases. The
Maintenance lifecycle is `planned` → `in-progress` → `completed`; an active
Maintenance item may stop at either of the first two. Updates are unique and in
ascending UTC second-resolution order. `resolved_at` is omitted while an event
is active. For either closed kind it is required and must equal the final
update time (`resolved` for an Incident, `completed` for Maintenance).

The exact public component vocabulary is `watch`, `home`, `requests`,
`audiobooks`, and `site`. Each event needs at least one unique component from
that list. The examples under `content/incidents/` and
`content/maintenance/` are complete, fictional, validator-accepted closed
events; their comments explain the opening, update, and closure edits.

## Independent soak watchdog

The watchdog uses the complete checked-in public v2 contract in
`publisher/schema.py`; it is not a standalone single file. Its no-agent
deployment must copy that module with the watchdog and no-argument wrappers.
The recurring wrapper performs one transaction every five minutes and delivers
only threshold transitions directly. The separate report wrapper prints one
privacy-safe state summary for an explicitly scheduled one-shot delivery.

From `status-walshit/`, set the active profile root explicitly. This runbook
intentionally contains no status URL, SSH alias, container name, private route,
webhook, or scheduler destination value:

```sh
set -eu
: "${HERMES_HOME:?set the active homelab Hermes profile root}"
case "$HERMES_HOME" in /*) ;; *) exit 2 ;; esac
test "$HERMES_HOME" != /
install -d -m 700 \
  "$HERMES_HOME/scripts" \
  "$HERMES_HOME/scripts/publisher" \
  "$HERMES_HOME/config" \
  "$HERMES_HOME/config/status-soak" \
  "$HERMES_HOME/runtime" \
  "$HERMES_HOME/runtime/status-soak"
install -m 700 operations/soak_watchdog.py "$HERMES_HOME/scripts/soak_watchdog.py"
install -m 700 operations/run_status_soak_watchdog.py "$HERMES_HOME/scripts/run_status_soak_watchdog.py"
install -m 700 operations/report_status_soak_watchdog.py "$HERMES_HOME/scripts/report_status_soak_watchdog.py"
install -m 600 publisher/schema.py "$HERMES_HOME/scripts/publisher/schema.py"
install -m 600 publisher/__init__.py "$HERMES_HOME/scripts/publisher/__init__.py"
```

Pre-create the config at the wrapper's fixed profile path before editing it:

```sh
set -eu
WATCHDOG_CONFIG_FILE="$HERMES_HOME/config/status-soak/watchdog.json"
test ! -e "$WATCHDOG_CONFIG_FILE"
test ! -L "$WATCHDOG_CONFIG_FILE"
install -m 600 /dev/null "$WATCHDOG_CONFIG_FILE"
${EDITOR:?set an interactive editor} "$WATCHDOG_CONFIG_FILE"
test -f "$WATCHDOG_CONFIG_FILE"
test ! -L "$WATCHDOG_CONFIG_FILE"
test "$(stat -c %u "$WATCHDOG_CONFIG_FILE")" -eq "$(id -u)"
test "$(stat -c %a "$WATCHDOG_CONFIG_FILE")" -eq 600
```

The config is a strict JSON object with required keys `schema_version`
(currently `"1"`), `summary_url`, `webhook_env_file`, `state_file`, and
`lock_file`. To remain compatible with the report wrapper, `state_file` must be
the absolute expansion of
`$HERMES_HOME/runtime/status-soak/soak-state.json`; put `lock_file` in that same
dedicated directory. Optional positive integer keys are `max_age_seconds`
(default `180`), `max_body_bytes` (default `262144`), `failure_threshold`
(default `3`), and `recovery_threshold` (default `2`). Optional Docker checking
requires both `ssh_alias` and a nonempty, duplicate-free `container_names`
array; omitting both disables it. Unknown keys, other URL schemes, broad modes,
symlinks, arbitrary commands, invalid aliases, and invalid container names are
rejected.

Set `WATCHDOG_SECRET_FILE` to the exact absolute path entered as
`webhook_env_file` in that reviewed config. Pre-create that exact configured
path at mode `0600` before opening the editor; do not substitute a hard-coded
filename:

```sh
set -eu
: "${WATCHDOG_SECRET_FILE:?set the exact configured webhook_env_file path}"
case "$WATCHDOG_SECRET_FILE" in "$HERMES_HOME"/*) ;; *) exit 2 ;; esac
install -d -m 700 "$(dirname "$WATCHDOG_SECRET_FILE")"
test ! -e "$WATCHDOG_SECRET_FILE"
test ! -L "$WATCHDOG_SECRET_FILE"
install -m 600 /dev/null "$WATCHDOG_SECRET_FILE"
${EDITOR:?set an interactive editor} "$WATCHDOG_SECRET_FILE"
test -f "$WATCHDOG_SECRET_FILE"
test ! -L "$WATCHDOG_SECRET_FILE"
test "$(stat -c %h "$WATCHDOG_SECRET_FILE")" -eq 1
test "$(stat -c %u "$WATCHDOG_SECRET_FILE")" -eq "$(id -u)"
test "$(stat -c %a "$WATCHDOG_SECRET_FILE")" -eq 600
```

The secret must contain exactly one newline-terminated
`DISCORD_WEBHOOK_URL` entry. Do not display it, put it in JSON, pass it as an
argument, or reuse a webhook coupled to the publisher.

Before enabling either job, validate the dedicated profile-owned runtime
directory. The watchdog rejects it instead of changing its permissions:

```sh
set -eu
test -d "$HERMES_HOME/runtime/status-soak"
test ! -L "$HERMES_HOME/runtime/status-soak"
test "$(stat -c %u "$HERMES_HOME/runtime/status-soak")" -eq "$(id -u)"
test "$(stat -c %a "$HERMES_HOME/runtime/status-soak")" -eq 700
```

Hermes cron uses profile-relative filenames under the active profile's
`scripts/` directory. `operations/status-soak-cron-job.json` is the reviewed
recurring logical manifest: its exact job fields are `schedule: every 5m`,
`script: run_status_soak_watchdog.py`, `no_agent: true`, and `deliver: local`.
No scheduler command arguments are needed. `local` is intentional because
steady-state execution is silent and transitions go directly to the restricted
webhook.

`operations/status-soak-report-cron-job.json` is the reviewed one-shot logical
manifest. Its exact non-schedule fields are
`script: report_status_soak_watchdog.py`, `no_agent: true`, and
`deliver: announce`, so its privacy-safe stdout goes to the separately approved
scheduler destination. Resolve the `at <reviewed-utc-timestamp>` placeholder to
one explicit future UTC time in the reviewed scheduler transaction; never
submit the placeholder or convert it into a recurring report. Verify the
scheduler's bounded job display field-for-field against the recurring manifest
or the reviewed, timestamp-resolved report manifest before enabling it.

The checked-in logical manifests can be validated offline without contacting
Hermes:

```sh
python3 -c 'import json,pathlib; p=pathlib.Path("operations/status-soak-cron-job.json"); d=json.loads(p.read_text()); assert d == {"name":"Independent status soak watchdog","schedule":"every 5m","script":"run_status_soak_watchdog.py","no_agent":True,"deliver":"local"}'
python3 -c 'import json,pathlib; p=pathlib.Path("operations/status-soak-report-cron-job.json"); d=json.loads(p.read_text()); assert d == {"name":"Status soak one-shot report","schedule":"at <reviewed-utc-timestamp>","script":"report_status_soak_watchdog.py","no_agent":True,"deliver":"announce"}'
```

The process holds a nonblocking lock across state read, public probe, optional
fixed Docker inspect, state evaluation, transition delivery, and atomic state
save. Docker stdout is streamed only up to its fixed bound; overflow and timeout
kill and reap the fixed-argv subprocess, and stderr is discarded. A healthy or
non-transition probe is silent. An unhealthy completed probe also exits zero
because the consecutive state machine owns alerting; configuration, state,
lock, secret, or delivery failures use fixed sanitized stderr and a nonzero
exit. Three consecutive failures produce one outage transition, and two
consecutive successes produce one recovery transition by default. Mentions are
disabled in both Discord payloads.

Transition delivery is deliberately at-least-once. A crash or ambiguous
Discord acceptance after delivery but before atomic state persistence may
duplicate a transition on retry; the watchdog does not provide exactly-once
delivery. This preserves retry of an actionable transition instead of marking
it delivered before Discord has accepted it.

Path validation opens and verifies the configured runtime parent as an owned,
non-symlink mode-`0700` directory before lock or state work. Lock and state
operations are still pathname-based, leaving a residual parent-path race if an
ancestor is renamed concurrently. Therefore the runtime path must remain a
dedicated profile-owned runtime directory whose ancestors are not writable by
other users; descriptor-relative state/lock operations are an unresolved
hardening opportunity.

Report mode reads only the wrapper's fixed restricted state path and prints
fixed privacy-safe JSON scalars; absent, corrupt, symlinked, broad-mode, or
wrong-parent state fails without printing state content. A local wrapper smoke
uses no command arguments:

```sh
HERMES_HOME="$HERMES_HOME" python3 -m operations.report_status_soak_watchdog
```

Watchdog rollback is deliberately non-destructive: pause the five-minute job;
retain the copied source, schema package, wrappers, manifests, state, and secret
for diagnosis; and use a reviewed source revert only if the watchdog source
must be rolled back. Do not delete or reset state, rotate or delete the webhook,
remove runtime files, or change the publisher as part of routine rollback.
