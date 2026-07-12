# qbittorrent-vpn

Dockge stack for qBittorrent routed through Gluetun using NordVPN.

## Files

- `compose.yaml` — Gluetun + qBittorrent stack.
- `.env` — real local values go here; keep mode `600` and do not commit.
- `.gitignore` — excludes `.env` and runtime data from Git.

## Important notes

- Existing host port `8080` is SABnzbd, so qBittorrent Web UI is mapped on `8081`.
- Existing host port `8888` is Dozzle, so Gluetun HTTP proxy was not enabled/published.
- NordVPN does not provide normal port forwarding for inbound torrent peers. `QBITTORRENT_PORT=8694` is kept for qBittorrent consistency and Gluetun firewall allowance, but it will not magically create a NordVPN-forwarded public port.
- The old OpenVPN credentials pasted in chat should be considered exposed and rotated/revoked.

## Start after filling `.env`

From the containers VM:

```bash
cd /home/port/stacks/qbittorrent-vpn
docker compose config --quiet
docker compose up -d
```

Post-checks:

```bash
docker ps --filter name=gluetun --filter name=qbittorrent
curl -fsS http://127.0.0.1:8081/ >/dev/null && echo qbit-webui-ok
```
