# Maintainerr

Host-specific Compose definition reconciled with the running `maintainerr`
container on 2026-10-23. The image is pinned by digest. The app runs as UID/GID
1000 with a read-only root, dropped capabilities, bounded resources and logs,
and an HTTP healthcheck. LAN HTTP is bound to `192.168.5.252:6246`.

## Prerequisites and state

- Existing external Docker network `arr_default`.
- Existing `./data` bind directory, owned/readable by UID/GID 1000.
- Native application setup and integrations are stored in that data directory,
  not in this repository. No API credentials or runtime database are tracked.

Maintainerr can drive media removal. Adding its Compose definition is **not**
authorization to create/enable cleanup rules or remove media. Review the exact
rule, targets and recovery status separately before enabling destructive work.

## Read-only checks

Run from this directory on the deployment host:

```sh
docker compose config --quiet
docker compose ps
curl --fail --silent --show-error --output /dev/null http://192.168.5.252:6246/
```

No backup/restore verification is established by this snapshot. Preserve `data/`
and establish recovery before a stateful upgrade. Repository reconciliation
requires no restart, recreation, volume removal or pruning.
