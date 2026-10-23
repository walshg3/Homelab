# Dewarr — current deployment configuration

This host-specific snapshot was reconciled against running containers on
2026-10-23. It does not install the application on a fresh host.

## Active service and definitions

- Active app: `dewarr-app-v034-1`, Compose service `app-v034`.
- Image: official `ghcr.io/logabell/dewarr:v0.3.4`, pinned by digest in Compose.
- Database: `dewarr-postgres-1`, Compose service `postgres`.
- LAN URL: `http://192.168.5.252:8085`, plain HTTP with native app authentication.
- Canonical default services: **app-v034 + postgres** in `compose.yaml`.

The running app's Compose label points to `compose.v034-candidate.yaml`; that
file is retained in Git and is byte-identical to `compose.yaml` in this snapshot.
The database label points to `compose.yaml`. This preserves actual provenance
without rewriting the live checkout or recreating either container.

Earlier stock pilot, importer, ebook, SMB-patched, SAB5-patched and v0.3.1 app
definitions remain behind `retained-*` profiles. They share state and port 8085;
do not enable them alongside the current app. Historical candidate files not
listed above remain local and ignored. Their presence is not upgrade or rollback
authorization. Local patched-image build contexts are not included in this repo.

## Persistent state and prerequisites

An ignored mode-0600 `.env` must supply `DB_PASSWORD` (see `.env.example`). Never
print it or put it in Git. Existing app configuration and encryption key live
under `/home/port/.local/state/hermes/dewarr/config/`; preserve that key together
with the PostgreSQL data. Application state also uses the adjacent `data/` path.
The `postgres` named volume must retain the existing database.

Existing media is read-only at `/share`. Writable download access is restricted
to the `torrents/dewarr` subpath. Writable imports use the `media/dewarr` subpath
of external volume `dewarr_managed_media`, mounted at `/managed`. The other
external volume is `audiobookshelf_storage-share`. Both external volumes and
their subpaths must exist; their CIFS credentials are deliberately outside Git.
`BOOK_DOWNLOAD_DISPATCH_ENABLED=true` is present for the active app; this is not
a discovery-only deployment. This snapshot does not assert application-level
integration health or change its policies.

Networks reserve `10.255.250.96/28` for egress and `10.255.250.112/28` for the
internal database. Check for conflicts before any new-host deployment. Only the
app publishes a port; PostgreSQL remains internal.

## Read-only checks

Run from this directory on the deployment host:

```sh
docker compose config --quiet
docker compose config --services
docker compose ps
curl --fail --silent --show-error http://192.168.5.252:8085/api/health/ready
```

The default services must be exactly `app-v034` and `postgres`. To inspect the
app's original launch definition without changing anything:

```sh
docker compose -f compose.v034-candidate.yaml config --quiet
```

## Recovery boundary

Preserve database, encryption key, configuration, media and downloader originals.
No database downgrade or off-host restore is validated by this repository
reconciliation. Do not start a retained older application against a migrated
database merely because its profile still exists. No deployment, restart,
removal or data migration is part of committing these definitions.
