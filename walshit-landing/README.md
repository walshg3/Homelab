# Walshit landing page

Production-ready static landing page served by unprivileged Nginx. The approved page design and copy live in `site/`; its only browser-side behavior is the local setup/help dialog.

## Local validation

Run the repository checks from its root:

```sh
python3 -m unittest -v
git diff --check
```

The runtime image is intentionally pinned for `linux/amd64`. An operator with a Docker daemon may separately validate the Compose model and build in the deployment environment. This repository workflow does not require network access, live credentials, or a running container.

## Deployment boundary

The intended deployment is exactly one static container on the `linux/amd64` host that owns `192.168.5.252`, publishing `192.168.5.252:3003` to container port `8080`. The immutable local image tag is `walshit-landing:20260713`. TLS and public routing, if any, terminate outside this stack; Nginx intentionally does not emit HSTS.

This stack has no secrets, no `.env` requirement, and no persistence. It makes no application network calls and has no database or writable volume. Its only writable runtime filesystem is the bounded `/tmp` tmpfs required by unprivileged Nginx.

## Rollback

Retain the previous image tag before replacing the running revision. To roll back, restore the previous Compose revision (or change `image:` back to the previous image tag) and recreate only the `landing` service using the deployment host's normal change procedure. Confirm `/healthz.txt` returns exactly `walshit-landing-ok` afterward.

Rollback does not require deleting volumes, pruning images, or removing unrelated containers. Keep the failed image tag available for diagnosis until the incident is resolved.
