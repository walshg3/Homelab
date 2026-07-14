# Homelab Notifications

One user-facing notification inbox (`ntfy`) plus a private stateless Apprise API bridge for applications such as Audiobookshelf.

## Security model

- ntfy starts with `auth-default-access=deny-all`.
- Apprise has no host-published port and is reachable only on the Docker network named `notifications`.
- Publisher credentials belong in application-owned restricted state, never in this repository.
- `data/` and `.env` are intentionally ignored by the repository.
- Images are pinned by release tag and multi-platform manifest digest.

## Files

- `compose.yaml` — ntfy and private Apprise services.
- `.env.example` — non-secret runtime template.
- `.env` — live local settings, mode `0600`, not committed.
- `data/` — ntfy cache/auth SQLite state, not committed.

## Preflight

From `/home/port/stacks`:

```bash
install -m 600 notifications/.env.example notifications/.env
mkdir -p notifications/data
chmod 700 notifications/data
docker compose --env-file notifications/.env -f notifications/compose.yaml config --quiet
docker compose --env-file notifications/.env -f notifications/compose.yaml config --images
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
```

Poll health with a deadline rather than using a blind sleep. Expected services:

- ntfy: `healthy`, canonical URL `https://notify.walshit.com`, with direct LAN origin `http://192.168.5.252:8093`
- Apprise: `healthy`, no host port, internal endpoint `http://apprise:8000/notify`

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

docker port apprise
```

`docker port apprise` must print nothing.

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

Do not use `docker compose down`, container/network removal, image prune, or delete `data/` without separate explicit removal/data-loss approval.

## Public/mobile endpoint

`https://notify.walshit.com` is routed through the remotely managed `Sand Hills Media` Cloudflare Tunnel to `http://192.168.5.252:8093`. The proxied CNAME points to the tunnel UUID. ntfy authentication remains authoritative; anonymous access is denied.

`NTFY_BEHIND_PROXY` intentionally remains `false`: the origin is also directly reachable on the LAN, and forwarded headers must not be trusted without explicit trusted-proxy restrictions.

### Native iOS instant notifications

The native iOS app requires `NTFY_UPSTREAM_BASE_URL=https://ntfy.sh` for reliable instant delivery from a self-hosted server. The iOS Default Server must exactly match `NTFY_BASE_URL` (`https://notify.walshit.com`). ntfy.sh relays the APNS wake-up/poll request; the device retrieves message content from this self-hosted server. This adds an external ntfy.sh/APNS availability and metadata dependency, while application publishers continue to use the private LAN endpoint.

Any future route change or rollback must capture the full current tunnel configuration and exact DNS record first, then obtain scoped approval immediately before using the retained overprivileged Cloudflare token.
