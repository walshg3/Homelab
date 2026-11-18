# Homelab Stacks & Configurations

Personal Docker Compose definitions and reviewed configuration for a media-focused
homelab. These are deployment-specific source snapshots, **not** a complete backup
or a portable one-command installation. Persistent data, credentials, external
networks/volumes and some separately managed services live outside this repository.

## Stack map

| Directory | Purpose |
| --- | --- |
| `arr/` | Sonarr, Radarr, Prowlarr, SABnzbd, Bazarr and Unpackerr |
| `audiobookshelf/` | Audiobook and ebook library server |
| `bookorbit/` | Side-by-side BookOrbit reader/requests, PostgreSQL and private Kokoro TTS |
| `client-facing/` | Seerr requests, Homarr dashboard, Wizarr invitations and Dozzle |
| `cloudflare/` | Cloudflared tunnel connector |
| `dewarr/` | Dewarr v0.3.4 app and PostgreSQL; retained older profiles are inactive |
| `dispatcharr/` | Live-TV stream management |
| `flaresolverr/` | Internal browser challenge helper |
| `framerr/` | Application dashboard |
| `homebridge/` | HomeKit bridge |
| `kometa/` | Plex collection/overlay automation and reusable artwork/fonts |
| `maintainerr/` | Media lifecycle management; destructive rules require separate review |
| `notifications/` | ntfy, Apprise, Discord subscriber bridge and independent watchdog source |
| `posterizarr/` | Plex artwork generation |
| `qbittorrent-vpn/` | qBittorrent behind Gluetun |
| `readmeabook/` | Book request/acquisition application |
| `shelfmark/` | Ebook acquisition application |
| `suggestarr/` | Media suggestion automation |
| `tautulli/` | Plex statistics and monitoring |
| `tracearr/` | Plex playback monitoring, TimescaleDB and Redis |
| `walshit-landing/` | Public Hugo site, retained legacy source, tests and deployment workflow |

`unpoller-unifi/` is a separate nested checkout, deliberately excluded from this
repository. Retained immutable `walshit-landing-hugo*` and `walshit-landing-live-*`
directories are local release artifacts, not authoring source.

## Security and data boundaries

- `.gitignore` uses a deny-by-default allowlist. Runtime databases, downloads,
  dependency trees, generated original posters, `.env` files and nested checkouts
  stay local. Ignore rules reduce accidental staging; they are not a secret scanner
  and do not untrack files that have already been committed.
- Use placeholder-only `.env.example` files where provided. Real credentials belong
  in restricted local `.env` or file-secret paths, never in Git, chat or logs.
  Several stacks require pre-existing file secrets and cannot be bootstrapped by
  copying an example environment file.
- LAN addresses and paths can be literal deployment values, not secrets. Adjust
  them deliberately when adapting the definitions. CIFS mounts and external
  Docker resources must already exist with the intended permissions/subpaths.
- Cloudflare Tunnel does not mean every route is protected by Access. Public
  pages and application-authenticated services coexist with Access-protected
  management routes. Direct LAN listeners do not inherit Cloudflare Access.
- Digest pins record reviewed deployments; floating tags remain in some existing
  definitions. A repository housekeeping commit is not an image update.
- Preserve application encryption keys together with their databases. An image
  downgrade is not a validated database rollback. This repository is not evidence
  of a tested off-host backup/restore path.

## Read-only validation

From the repository root (Python 3 and Git required):

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s notifications/discord-bridge -v
python3 -m unittest discover -s notifications/ntfy-watchdog -v
git diff --check
```

For website source/build/runtime checks, follow `walshit-landing/README.md`; those
checks require the documented Hugo version and, for container checks, Docker.
The GitHub workflow tests PRs and main pushes and publishes a site image after
successful main tests. Production deployment is separately protected by the
`production` environment and an offline, operator-controlled runner. Merging a
configuration-only PR is not permission to approve an unrelated site deployment.

On the deployment host, validate an individual stack from its own directory with
`docker compose config --quiet` and inspect `docker compose ps`. Do not print a
fully rendered Compose model or raw container inspect: those can include secrets.
A successful render is not enough if Compose warns about missing variables;
resolve every such warning before a deployment. New-service READMEs include
read-only health checks and prerequisites.

## Change discipline

Use an isolated checkout for reconciliation when the live stack root has local
edits. Stage exact reviewed paths, scan the candidate and history for secrets,
and preserve unrelated changes. Do not fetch/reset/switch a dirty live checkout
merely to make its status look clean. A healthy runtime, local Git history and
GitHub `main` are separate sources of truth and should be compared explicitly.

Apply service-scoped deployments only after validating the target and rollback.
Do not automatically run `down`, `rm`, prune, `git clean`, history rewrites or
branch deletion as housekeeping. Preserve retained releases and runtime data
until the exact removal scope and recovery status have been reviewed.
