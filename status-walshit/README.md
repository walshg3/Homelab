# Status Walshit local runtime

This directory contains the local-only status publisher and its read-only Nginx
surface. The production-shaped base `compose.yaml` publishes no port and
contains no gateway. Local staging project `status-walshit-staging-v2` adds a
separate gateway as the sole owner of the loopback binding
`127.0.0.1:3015:8080`; Cloudflare and DNS remain a separately gated future
transaction.

## Operator content workflow

An independent ntfy or Discord alert starts a human decision, never an
automatic public post. The operator then makes a local copy of the appropriate
template, edits only public-safe content, runs the validator, and sends the
change through the reviewed Git and deployment path. The next publisher cycle
loads it; the operator verifies `summary.json` and `incidents.json`, then uses a
reviewed change to resolve and eventually archive the event. Automatic
component state remains separate from manual narration.

Run the templates and validator only from `status-walshit/`. The exact content
promotion, lifecycle, privacy, soak-watchdog deployment, reporting, and
rollback procedures are in `runbooks/operations.md`.

## Runtime boundary

| Service | Identity | Networks | Read/write mounts | Read-only mounts | Inbound surface |
| --- | --- | --- | --- | --- | --- |
| `publisher` | `1000:1000` | ordinary `publisher-egress` bridge | `public-data` at `/var/lib/status/public`; `publisher-state` at `/var/lib/status/state` | `./content` and the Compose secret | none |
| `web` | `101:101` | internal-only `web-internal` bridge | none | `public-data` at `/usr/share/nginx/html/status/v2` with `nocopy` | none |
| staging-only `gateway` | `101:101` | internal-only `web-internal` plus ordinary `staging-ingress` | none | none | `127.0.0.1:3015:8080` |

The services share only `public-data`. Publisher state, operator content, and
the secret never enter the web container. The publisher has no inbound service,
published port, Docker socket, or privileged access. It loads the secret into an
immutable in-process mapping and calls the one-shot publisher directly.

A host port configured directly on a container attached only to an
`internal: true` bridge is intentionally not used: on the observed Docker
runtime it records a requested binding but installs no host listener. Attaching
the core web service to an ordinary bridge would repair ingress by granting it
an egress path, violating its boundary. Instead, the staging-only gateway joins
both bridges and reverse-proxies only to the fixed `http://web:8080` upstream.
It has a read-only root, dropped capabilities, bounded resources, no secrets,
environment, mounts, content, public data, state, or Docker socket. Core startup
order remains publisher → core web → gateway through healthy dependencies.

## Staging entry point

Run every staging operation with both files, in this order:

First complete the numeric-ID and copied-secret preflights in
`runbooks/operations.md`; they must succeed before configuration or startup.

```sh
docker compose -f compose.yaml -f compose.staging.yaml config --quiet
docker compose -f compose.yaml -f compose.staging.yaml build
docker compose -f compose.yaml -f compose.staging.yaml up -d
```

The top-level Compose name makes these exact combined commands operate on
`status-walshit-staging-v2`, allowing a parallel replacement without removing
the first failed `status-walshit-staging` project. That first project may be
stopped, but it must not be removed, downed, or pruned without explicit scoped
approval. Only the gateway in the `-v2` project owns loopback port 3015; the
core web and publisher continue to publish no port.

These commands remain documented for a host-side operator. An isolated,
loopback-only staging acceptance was completed on 2026-07-22. No push, public
deployment, DNS, Cloudflare, tunnel, or production cutover occurred. See
`runbooks/operations.md` for secret preparation, acceptance checks, recovery,
and rollback.

Secrets never belong in command arguments, environment variables, image layers,
Compose interpolation, logs, health output, or public JSON. The only accepted
secret file lines are `PLEX_TOKEN=value` and
`PLEX_MACHINE_IDENTIFIER=value`; prepare them interactively without echoing
their values.
