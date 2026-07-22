# Local staging operations

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
publisher egress is `10.255.250.0/28`, core web is `10.255.250.16/28`, and
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
This repository does not claim that this repair has been built or run.

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
