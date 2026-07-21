# Status Walshit local runtime

This directory contains the local-only status publisher and its read-only Nginx
surface. The base `compose.yaml` publishes no port. Local staging adds only the
loopback binding `127.0.0.1:3015`; Cloudflare and DNS remain a separately gated
future transaction.

## Runtime boundary

| Service | Identity | Networks | Read/write mounts | Read-only mounts | Inbound surface |
| --- | --- | --- | --- | --- | --- |
| `publisher` | `1000:1000` | ordinary `publisher-egress` bridge | `public-data` at `/var/lib/status/public`; `publisher-state` at `/var/lib/status/state` | `./content` and the Compose secret | none |
| `web` | `101:101` | internal-only `web-internal` bridge | none | `public-data` at `/usr/share/nginx/html/status/v2` with `nocopy` | none in base; staging loopback only |

The services share only `public-data`. Publisher state, operator content, and
the secret never enter the web container. The publisher has no inbound service,
published port, Docker socket, or privileged access. It loads the secret into an
immutable in-process mapping and calls the one-shot publisher directly.

## Staging entry point

Run every staging operation with both files, in this order:

First complete the numeric-ID and copied-secret preflights in
`runbooks/operations.md`; they must succeed before configuration or startup.

```sh
docker compose -f compose.yaml -f compose.staging.yaml config --quiet
docker compose -f compose.yaml -f compose.staging.yaml build
docker compose -f compose.yaml -f compose.staging.yaml up -d
```

These commands are documented for a host-side operator; this repository slice
does not claim that a live build or run has occurred. See
`runbooks/operations.md` for secret preparation, acceptance checks, recovery,
and rollback.

Secrets never belong in command arguments, environment variables, image layers,
Compose interpolation, logs, health output, or public JSON. The only accepted
secret file lines are `PLEX_TOKEN=value` and
`PLEX_MACHINE_IDENTIFIER=value`; prepare them interactively without echoing
their values.
