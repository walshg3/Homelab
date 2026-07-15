# Homelab Notifications

One user-facing notification inbox (`ntfy`), a private stateless Apprise API bridge for applications such as Audiobookshelf, and a one-way ntfy-to-Discord subscriber bridge.

## Architecture

Publishers send to authenticated ntfy topics. The Python-stdlib-only `discord-bridge` subscribes inside the private `notifications` Docker network at `http://ntfy`, authenticating with a dedicated read-only ntfy access token. It posts selected messages to one Discord channel-scoped incoming webhook. It has no host port, never fetches attachment or action URLs, and has no Discord bot-token fallback.

The script and container root are read-only. Only `discord-bridge/data/` is writable for its atomic cursor/health state. The subscriber token and webhook URL are read from the Compose secret mount `/run/secrets/discord_bridge_env`, sourced from a mode-`0600`, UID-`1000` local file, rather than container environment variables. Docker Compose bind-mounted secrets do not honor long-syntax `uid`/`gid`/`mode`; verify the effective in-container file remains readable only by the bridge UID instead of relying on those unsupported fields.

## Security model

- ntfy starts with `auth-default-access=deny-all`.
- Apprise has no host-published port and is reachable only on the Docker network named `notifications`.
- The Discord bridge is non-root, capability-free, restricted to the private notifications network, and disables all Discord mentions.
- Publisher credentials belong in application-owned restricted state, never in this repository.
- `data/` and all `.env` secret files are intentionally ignored by the repository.
- Images are pinned by release tag and multi-platform manifest digest.

## Files

- `compose.yaml` — ntfy, private Apprise, and hardened Discord bridge services.
- `.env.example` — non-secret runtime template.
- `.env` — live local settings, mode `0600`, not committed.
- `data/` — ntfy cache/auth SQLite state, not committed.
- `discord-bridge/bridge.py` — read-only mounted subscriber implementation.
- `discord-bridge/.env` — bridge secrets, mode `0600`, not committed.
- `discord-bridge/data/state.json` — cursor and health state, not committed.

## Discord bridge prerequisites and filters

Create a Discord incoming webhook scoped to the single destination channel. Do not use a bot token. In ntfy, create a dedicated access token with subscribe/read-only access to exactly `homelab-critical` and `homelab-ops`; deny publish and all other topics.

The bridge forwards only `homelab-critical` and `homelab-ops` messages with numeric priority 4 or 5. It filters events with an exact `test` or `smoke` tag (case-insensitive), the `homelab-info` topic, attachments, actions, low/invalid priority, and any message containing `[ntfy-discord-bridge-origin]`. A similar marker is included in Discord text for one-way loop prevention. Attachment/action URLs are never requested.

Titles, messages, total Discord content, incoming JSON lines, and persisted error text are bounded. Control characters and common credential forms—including Authorization values, token-like assignments, ntfy tokens, JWT-like strings, and Discord webhook URLs—are redacted. Discord `allowed_mentions.parse` is empty.

On the first start there is no `since` parameter, so only messages arriving after the subscription opens are seen. ntfy `open`/`keepalive` control-record IDs never become cursors. Afterward, reconnects use the persisted last-seen message ID. Filtered messages and successfully delivered messages advance the cursor. A Discord delivery that exhausts bounded retries does not advance it, so reconnect replays that event. This is at-least-once behavior: a crash after Discord accepts a post but before the cursor write can produce a duplicate.

## Preflight

From `/home/port/stacks`:

```bash
install -m 600 notifications/.env.example notifications/.env
mkdir -p notifications/data
chmod 700 notifications/data
install -d -m 700 -o 1000 -g 1000 notifications/discord-bridge/data
test -e notifications/discord-bridge/.env || install -m 600 -o 1000 -g 1000 /dev/null notifications/discord-bridge/.env
docker compose --env-file notifications/.env -f notifications/compose.yaml config --quiet
docker compose --env-file notifications/.env -f notifications/compose.yaml config --images
```

Edit `notifications/discord-bridge/.env` locally and supply exactly these two key names: `NTFY_SUBSCRIBER_TOKEN` and `DISCORD_WEBHOOK_URL`. Do not paste their values into Git, shell arguments, logs, or chat. Re-run `chmod 0600 notifications/discord-bridge/.env` after editing.

The bridge uses `python:3.13-alpine` pinned to the registry-verified multi-platform manifest digest captured during implementation. Re-resolve and review the digest deliberately before a future image update.

Run local source validation from the repository root:

```bash
python3 -m unittest notifications/discord-bridge/test_bridge.py
python3 -m py_compile notifications/discord-bridge/bridge.py notifications/discord-bridge/test_bridge.py
git diff --check
git status --short
docker compose --env-file notifications/.env -f notifications/compose.yaml config --quiet
```

Before first start:

- verify `192.168.5.252:8093` is unused and the `notifications` Docker network does not conflict with an unrelated network;
- capture the live Git status and staged-index state;
- capture container IDs/start times for services that later phases will recreate;
- note that this is a new stack, so there is no prior notification Compose state to restore.

## Start

```bash
docker compose --env-file notifications/.env -f notifications/compose.yaml pull ntfy apprise
docker compose --env-file notifications/.env -f notifications/compose.yaml up -d --no-deps ntfy apprise
docker compose --env-file notifications/.env -f notifications/compose.yaml pull discord-bridge
docker compose --env-file notifications/.env -f notifications/compose.yaml up -d --no-deps discord-bridge
```

Poll health with a deadline rather than using a blind sleep. Expected services:

- ntfy: `healthy`, canonical URL `https://notify.walshit.com`, with direct LAN origin `http://192.168.5.252:8093`
- Apprise: `healthy`, no host port, internal endpoint `http://apprise:8000/notify`
- Discord bridge: `healthy`, no host port, state under `notifications/discord-bridge/data/`

## Authentication initialization

Create users and access tokens with `ntfy user`, `ntfy access`, and `ntfy token` inside the running ntfy container. Supply passwords/tokens through restricted local files or hidden input; never put credential values in shell arguments, Git, logs, or chat.

Required identities:

- Greg subscriber/admin
- Dozzle write-only publisher
- Audiobookshelf write-only publisher
- media-app write-only publisher
- monitoring write-only publisher

Verify anonymous publish and subscribe return HTTP 401/403 before adding any public route.

## Health verification

```bash
curl -fsS http://192.168.5.252:8093/v1/health

docker inspect ntfy apprise --format '{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.RestartCount}}'

docker inspect ntfy-discord-bridge --format '{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.RestartCount}}'

docker port apprise

docker port ntfy-discord-bridge

docker compose --env-file notifications/.env -f notifications/compose.yaml logs --since=10m discord-bridge
```

Both `docker port` commands must print nothing. Send one authorized priority-4-or-5 message to each subscribed topic using an existing publisher workflow, without exposing its credential. Confirm one sanitized Discord post per message. Then send low-priority, exact `test`/`smoke` tag, attachment, action, and origin-marker cases and confirm they do not post. Do not include real sensitive text in validation messages.

The healthcheck requires a `running` state refreshed within `BRIDGE_STATE_MAX_AGE_SECONDS`. ntfy keepalives refresh it while the stream is quiet. Retryable 429, 5xx, timeout, and network failures receive bounded exponential/backoff delay with jitter and are visible as sanitized state/log errors. A non-429 Discord 4xx is not retried: the process remains alive with unhealthy `blocked` state at the prior cursor until an operator fixes the webhook and explicitly restarts the service.

## Native media integrations

Application-owned notification state is configured at runtime and is not stored in this repository. Integrations publish to `homelab-ops` through `http://ntfy` with the restricted `media-publisher` token.

Batch A:

- Sonarr: health issue, health restored, and manual interaction required.
- Radarr: health issue, health restored, and manual interaction required.
- Prowlarr: health issue and health restored.
- Seerr: request processing failed and request available (`types=24`); poster embedding is disabled.

Batch B:

- Kometa: native ntfy for errors and new-version notices. Routine run-start, run-end, collection-change, and delete events remain disabled.
- Tautulli: native ntfy for Plex internal/external availability down/up, Tautulli database corruption, and expired Plex tokens. Playback, recently-added, update, and other routine events remain disabled.
- SABnzbd: built-in Apprise-to-ntfy for disk-full events only. Per-job failures were disabled after live sampling showed that each incomplete Usenet candidate generated an alert even when Sonarr automatically retried and successfully imported the episode. Successful jobs, startup, queue completion, warning, login, quota, and other routine events remain disabled.
- Bazarr: intentionally not enabled. Its current notification provider emits successful subtitle download/upgrade/upload events and does not provide the requested failure-only filtering.

Failure-focused integrations use high priority where the application supports a fixed priority. After any change, run each application's native notification test, verify delivery in ntfy, restart only the affected service when safe, and confirm settings persist. Never commit application API keys, Apprise URLs containing tokens, or ntfy credentials.

## Non-destructive rollback

For this new stack, rollback is a pause: disable application destinations and Dozzle rules through their APIs, then stop ntfy and Apprise while leaving their containers, network, images, and state intact. Later phases that modify existing Compose files must capture those files before editing and restore the captured version before recreating only the affected service.

```bash
docker compose --env-file notifications/.env -f notifications/compose.yaml stop ntfy apprise
```

To roll back only the new bridge while preserving its cursor and the existing notification services:

```bash
docker compose --env-file notifications/.env -f notifications/compose.yaml stop discord-bridge
docker inspect ntfy-discord-bridge --format '{{.Name}}|{{.State.Status}}'
```

Stopping the bridge prevents new Discord posts. It does not delete `discord-bridge/data/state.json`; a later start resumes after its cursor and may replay the last failed event. Do not remove the container, secret file, or state directory without separate approval. If the webhook must be disabled immediately, revoke it in Discord using an authorized operator session after stopping the bridge.

Do not use `docker compose down`, container/network removal, image prune, or delete `data/` without separate explicit removal/data-loss approval.

## Public/mobile endpoint

`https://notify.walshit.com` is routed through the remotely managed `Sand Hills Media` Cloudflare Tunnel to `http://192.168.5.252:8093`. The proxied CNAME points to the tunnel UUID. ntfy authentication remains authoritative; anonymous access is denied.

`NTFY_BEHIND_PROXY` intentionally remains `false`: the origin is also directly reachable on the LAN, and forwarded headers must not be trusted without explicit trusted-proxy restrictions.

### Native iOS instant notifications

The native iOS app requires `NTFY_UPSTREAM_BASE_URL=https://ntfy.sh` for reliable instant delivery from a self-hosted server. The iOS Default Server must exactly match `NTFY_BASE_URL` (`https://notify.walshit.com`). ntfy.sh relays the APNS wake-up/poll request; the device retrieves message content from this self-hosted server. This adds an external ntfy.sh/APNS availability and metadata dependency, while application publishers continue to use the private LAN endpoint.

Any future route change or rollback must capture the full current tunnel configuration and exact DNS record first, then obtain scoped approval immediately before using the retained overprivileged Cloudflare token.
