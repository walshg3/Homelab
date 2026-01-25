# Homelab Stacks & Configurations

This repository contains my personal Docker Compose stacks and configuration files for my home media server setup. It is designed to be modular, with separate stacks for different functionalities.

## Repository Structure

The configurations are organized into the following stacks:

### ARR Stack (`/arr`)
The core media management and retrieval stack.
- **Radarr**: Movie collection manager.
- **Sonarr**: TV show collection manager.
- **Sabnzbd**: Usenet download client.

### Client Facing (`/client-facing`)
Services exposed to end-users for interaction and requests.
- **Overseerr**: Request management and media discovery tool.
- **Homarr**: A sleek, modern dashboard for your applications.
- **Wizarr**: Advanced user invitation and management system for Plex/Jellyfin.

### Kometa (`/kometa`)
*Formerly Plex Meta Manager*
Automated metadata managing, collections, and overlays for Plex.
- Includes my personal `config.yml` templates (sanitized).
- **Overlays**: Custom 4K, HDR, Audio code overlays.
- **Fonts**: Custom fonts used for image generation.

### Posterizarr (`/posterizarr`)
A tool to automatically create and handle overlays for Plex posters.

### Tautulli (`/tautulli`)
Monitoring and tracking tool for Plex Media Server statistics.

---

## Security & Privacy

This repository uses a strict whitelist approach to ensure no sensitive information is leaked.
- **Environment Variables**: All sensitive data (passwords, API keys, IPs) are referenced via environment variables (`${VAR}`).
- **Examples**: `.env.example` files are provided in each directory to show required variables without exposing actual secrets.

## Getting Started

1. **Clone the repository:**
   ```bash
   git clone https://github.com/walshg3/Homelab.git
   cd Homelab
   ```

2. **Configure Environment:**
   Navigate to the desired stack folder and copy the example environment file:
   ```bash
   cd arr
   cp .env.example .env
   # Edit .env with your specific values (API keys, Paths, IDs)
   nano .env
   ```

3. **Deploy:**
   Run the stack using Docker Compose:
   ```bash
   docker compose up -d
   ```

## Notes
- Most stacks utilize `storage-share` volume mounts configured for SMB/CIFS access to a central storage server. Ensure your `.env` variables for `SMB_USER`, `SMB_PASSWORD`, and `SMB_SERVER_IP` are set defined if you use this storage method.
