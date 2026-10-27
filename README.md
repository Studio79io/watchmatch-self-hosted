# WatchMatch

WatchMatch is a self-hosted, Plex-first movie and TV picker for two people. Each person can browse a shared catalog and swipe through choices; when both choose the same title, WatchMatch records the match.

This repository is a clean installation template. It contains no deployment history, server addresses, Plex tokens, or household data.

## What you need

- A Linux host with Docker Engine and Docker Compose v2
- A Plex Media Server reachable from that host
- A Plex account with access to the server for each person who will use WatchMatch

## Start locally

1. Clone this repository and open its directory.
2. Run `sh scripts/prepare-local-install.sh`. This creates a private `.env`, generates a PostgreSQL password, and copies `config.example.yaml` to `config.yaml`.
3. Edit `.env`:
   - Set `PLEX_URL` to the Plex server URL. When Plex runs on the same Linux host, the generated `http://host.docker.internal:32400` value normally works.
   - Set `PLEX_TOKEN` to a Plex token for an account that can read the libraries.
   - If accessing WatchMatch from another device, set `PUBLIC_URL` to the host's reachable address, for example `http://192.168.1.20:8080`.
4. Review `config.yaml` and choose the libraries and content settings appropriate for your home.
5. Start it with `docker compose --env-file .env up --build -d`.
6. Open the URL in `PUBLIC_URL`. For a first local check, use `http://localhost:8080` on the host.

The PostgreSQL data directory defaults to `./postgres-data`. Keep `.env`, `config.yaml`, and that directory private and out of source control.

## Authentication and exposure

The starter configuration uses `AUTH_REQUIRED=false` so a local installer can verify the stack before configuring Plex sign-in. Set `AUTH_REQUIRED=true` before exposing WatchMatch beyond a trusted local network. Do not put WatchMatch directly on the public internet without HTTPS and suitable access controls.

## Verify the template

Run the full disposable test suite with:

```sh
scripts/test-all.sh
```

It runs database-backed API tests and browser tests in isolated Compose profiles and does not use your `.env` or persistent data.

## Scope

WatchMatch currently supports Plex as its catalog provider. This template intentionally does not include appliance-specific deployment files, operational backups, production configuration, or credentials.

## License

No license has been selected for this repository yet. You need the repository owner's permission before reusing, redistributing, or modifying the code beyond rights that apply by default.
