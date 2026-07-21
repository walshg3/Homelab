# Local staging operations

This runbook is for the loopback-only project `status-walshit-staging`. Always
run commands from `status-walshit/` and always combine the base and staging
files:

```sh
docker compose -f compose.yaml -f compose.staging.yaml COMMAND
```

The production base publishes no port. The staging override binds only
`127.0.0.1:3015`; it is not reachable from the LAN.

The two staging bridges use explicit, non-overlapping private `/28` subnets
(`10.255.250.0/28` and `10.255.250.16/28`). This avoids dependence on the
Docker host's exhausted automatic address pools. Verify these ranges do not
overlap host routes or existing Docker networks before bringing up the stack.
Cloudflare and DNS remain a separately gated future transaction.

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

## Acceptance checks

Inspect bounded service status and sanitized logs:

```sh
docker compose -f compose.yaml -f compose.staging.yaml ps
docker compose -f compose.yaml -f compose.staging.yaml logs --no-color --tail 100 publisher web
docker compose -f compose.yaml -f compose.staging.yaml exec publisher python3 -m publisher.runner health --public-dir /var/lib/status/public
```

The publisher health command prints only `publisher-health-ok` or
`publisher-health-failed`. Logs must contain no credentials, internal responses,
or document bodies.

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
check that generation timestamps advance, cycles do not overlap, both health
checks stay healthy, and logs remain sanitized. Restart each service and repeat
status, health, freshness, header, and browser checks:

```sh
docker compose -f compose.yaml -f compose.staging.yaml restart publisher
docker compose -f compose.yaml -f compose.staging.yaml restart web
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

Staging rollback is `stop`, without removing volumes, images, or files:

```sh
docker compose -f compose.yaml -f compose.staging.yaml stop
```

`stop` is a routine reversible action. `down`, volume removal or prune, image
removal, secret deletion, runtime-file deletion, and any other teardown are
destructive/removal operations and require explicit scoped approval identifying
the exact project and objects. Never add `-v` or prune as part of rollback.
