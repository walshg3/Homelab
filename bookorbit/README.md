# BookOrbit side-by-side reader and request stack

This is a deployment-specific configuration, not a portable bootstrap recipe.
The canonical `compose.yaml` records the running stack observed on 2026-10-23.

## Active services

- `app-requests`: BookOrbit, LAN HTTP at `192.168.5.252:8086`, with native
  application authentication. Its image reference remains `latest`, matching the
  live definition; repository reconciliation did not pull or upgrade it.
- `postgres`: independent pgvector/PostgreSQL database; no published port.
- `kokoro-runtime`: private CPU text-to-speech service; no published port.

The earlier reader-only app, initial Kokoro preflight, and CPU-tuning definitions
are retained behind non-default profiles. Do not enable them alongside the
active services: they share state, aliases, or bindings. Files such as
`requests.compose.yaml` and `compose.reader-only.yaml` are historical local
candidates, not the current source of truth.

## Data and prerequisites

Persistent app data, PostgreSQL data and file-backed secrets live outside this
repository under `/home/port/.local/state/hermes/bookorbit/`. The secret directory
must be restricted and readable by UID/GID 1000. Compose contains file references,
not secret values. Existing database credentials and encryption keys must remain
paired with their data; a fresh clone does not recreate those secrets.

The external `audiobookshelf_storage-share` volume must already exist. Existing
`media/audiobooks` and `media/shelfmark` are mounted read-only through volume
subpaths. The request app additionally has writable `media/bookorbit` and
read-only `usenet/bookorbit` and `torrents/bookorbit` subpaths. This configuration
is therefore **not** a reader-only/no-ingestion pilot.

Explicit bridge subnets are `10.255.250.128/28` (egress), `10.255.250.144/28`
(internal database), and `10.255.250.160/28` (internal TTS). Preflight address
conflicts before using this configuration on another host. PostgreSQL and TTS
are not public endpoints. Native-device playback/progress acceptance is separate
from Docker health.

## Read-only checks

Run from this directory on the deployment host:

```sh
docker compose config --quiet
docker compose config --services
docker compose ps
curl --fail --silent --show-error http://192.168.5.252:8086/api/v1/health
```

The default service set must be exactly `app-requests`, `kokoro-runtime`, and
`postgres`. Do not print rendered Compose/environment values: they can expose
credentials in other stacks.

## Recovery boundary

Keep Audiobookshelf and the source media authoritative during evaluation. No
backup/restore verification is established by this repository snapshot. Before
a database migration, preserve app data, database and the original secrets and
verify restoration. Changing the image tag alone is not a proven database
rollback. Repository reconciliation neither deploys nor removes anything;
container/volume/file cleanup requires a separate exact scope.
