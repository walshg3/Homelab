# 🌐 IP & Port Allocations

This document outlines the default port allocations for the services defined in this repository's Docker Compose stacks.

> **Note**: These addresses assume you are accessing the services via the Docker host's IP address (e.g., `http://192.168.x.x:PORT`).

## 🏴‍☠️ ARR Stack
| Service | Internal Port | External Port | Default URL |
| :--- | :--- | :--- | :--- |
| **Radarr** | 7878 | 7878 | `http://<host-ip>:7878` |
| **Sonarr** | 8989 | 8989 | `http://<host-ip>:8989` |
| **Sabnzbd** | 8080 | 8080 | `http://<host-ip>:8080` |
| **Prowlarr** | 9696 | 9696 | `http://<host-ip>:9696` |
| **Bazarr** | 6767 | 6767 | `http://<host-ip>:6767` |

## 👥 Client Facing
| Service | Internal Port | External Port | Default URL |
| :--- | :--- | :--- | :--- |
| **Overseerr** | 5055 | 5055 | `http://<host-ip>:5055` |
| **Homarr** | 7575 | 7575 | `http://<host-ip>:7575` |
| **Wizarr** | 5690 | 5690 | `http://<host-ip>:5690` |

## 📊 Management & Monitoring
| Service | Internal Port | External Port | Default URL |
| :--- | :--- | :--- | :--- |
| **Tautulli** | 8181 | 8181 | `http://<host-ip>:8181` |

## ℹ️ Background Services
The following services run as background tasks or scheduled jobs and do not expose a web interface via a mapped port by default.

*   **Kometa** (Scheduled Meta Manager)
*   **Posterizarr** (Scheduled Poster Overlay Manager)
