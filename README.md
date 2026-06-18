# Health Insights MCP Server

[![CI](https://github.com/adamconde/personal-health-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/adamconde/personal-health-mcp/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-Model_Context_Protocol-1f6feb)](https://modelcontextprotocol.io)
[![Built with FastMCP](https://img.shields.io/badge/built%20with-FastMCP-4B32C3)](https://github.com/jlowin/fastmcp)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-2A6DB2)](https://mypy-lang.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](#license)

A **self-hosted, single-user MCP server** that aggregates your personal health
data from **Google Health**, **Oura**, and **Withings** behind one normalized,
provider-attributed interface — and is built so new vendors drop in with no
changes to the core.

It exposes [Model Context Protocol](https://modelcontextprotocol.io) tools
(over Streamable HTTP, reachable from Claude Desktop or any MCP client on another
machine) and a small web UI for managing provider connections and preferences.

## Table of contents

- [Health Insights MCP Server](#health-insights-mcp-server)
  - [Table of contents](#table-of-contents)
  - [What it does](#what-it-does)
  - [Architecture at a glance](#architecture-at-a-glance)
  - [Prerequisites](#prerequisites)
  - [Quick start](#quick-start)
  - [Configuration](#configuration)
  - [Provider setup (OAuth apps)](#provider-setup-oauth-apps)
  - [Install: from GHCR or build locally](#install-from-ghcr-or-build-locally)
    - [Sample `docker-compose.yml`](#sample-docker-composeyml)
  - [Hosting](#hosting)
    - [Cloudflare Tunnel (no open ports)](#cloudflare-tunnel-no-open-ports)
    - [LAN / development](#lan--development)
  - [Connecting MCP clients](#connecting-mcp-clients)
    - [Authenticating with GitHub OAuth (optional)](#authenticating-with-github-oauth-optional)
    - [Tools](#tools)
    - [Web UI pages](#web-ui-pages)
  - [Operations](#operations)
  - [Security](#security)
  - [Adding a new provider](#adding-a-new-provider)
  - [Development](#development)
  - [Project layout](#project-layout)
  - [Version history](#version-history)
  - [License](#license)

## What it does

- **One canonical model.** Every provider's data is mapped into one canonical
  unit per dimension (kg, metres, seconds, kcal, bpm, °C). Comparison and
  resolution happen in canonical units; conversion to your display units happens
  only at the edge.
- **You choose the source of truth.** For any metric (Step Count, Weight, …)
  pick **`authority`** (prefer one provider; fall back to an ordered list if it
  has no data) or **`auto`** (the most recent value across all providers).
- **Provenance, always.** Every response names the provider that supplied each
  value.
- **Your units.** Choose kg vs lb, km vs mi, °C vs °F, and cm vs ft/in for
  height — **imperial by default**. When a provider can't serve a unit natively,
  the server converts; height in `ft/in` is rendered as feet and inches (e.g.
  `5' 9"`).
- **Broad coverage.** Steps, distance, calories, weight & body composition,
  heart rate, HRV, SpO₂, sleep stages, readiness/sleep scores, VO₂max,
  temperature, blood pressure/glucose, and more — including metrics only one
  provider supplies.
- **Single user, self-hosted.** No multi-tenant support by design. The
  preferences page _is_ your preferences. If someone else wants it, they host
  their own.

## Architecture at a glance

```text
MCP clients (Claude Desktop, …) ──HTTPS──▶ [   Cloudflare Tunnel   ]
                                                      │  (TLS terminated at CF edge)
                                                      ▼  http (internal docker net)
                                            ┌─────────────────────────┐
                                            │  app (uvicorn)          │
                                            │   /mcp  → FastMCP        │  ← bearer token
                                            │   /     → web UI         │  ← session login
                                            │   /oauth/* → callbacks   │
                                            └───────────┬─────────────┘
                                                        ▼
                                            SQLite (/data) — prefs +
                                            ENCRYPTED tokens & secrets
                          ── outbound ──▶ Google Health · Oura · Withings APIs
```

See [`docs/`](#project-layout) and the source under `src/personal_health_mcp/`
for the provider abstraction, resolution engine, and unit layer.

---

## Prerequisites

- **Docker** and **Docker Compose**.
- **A domain you own**, added to Cloudflare. Hosting needs a stable HTTPS
  hostname because OAuth redirect URIs must be registered with each provider and
  cannot change.
- A **developer account / app** with each provider you want to use (Google
  Health, Oura, Withings).

---

## Quick start

```bash
git clone https://github.com/adamconde/personal-health-mcp.git
cd personal-health-mcp
cp .env.example .env        # then fill it in (see Configuration)
docker compose --env-file .env -f deploy/docker-compose.yml up -d
```

Open `https://<your-domain>/`, sign in (with GitHub if configured, otherwise
`WEB_PASSWORD`), go to **Providers**, enter each provider's client id/secret,
click **Connect**, then set your **Metrics** and **Units** preferences.

> **Always pass `--env-file .env` and run from the repo root.** Compose's
> `${VAR}` interpolation (used for `CF_TUNNEL_TOKEN`) loads its `.env` from the
> **compose file's directory** (`deploy/`), not your shell's working directory —
> so without `--env-file .env` `CF_TUNNEL_TOKEN` resolves empty even though your
> repo-root `.env` is correct.

---

## Configuration

Configuration is via environment variables (`.env`). Generate the secrets:

```bash
# MCP bearer token and session secret
python -c "import secrets; print(secrets.token_urlsafe(48))"
# Token/secret encryption key (Fernet)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

| Variable                                        | Required   | Description                                                                                                      |
| ----------------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------- |
| `PUBLIC_BASE_URL`                               | ✅         | External HTTPS origin, no trailing slash (e.g. `https://health.example.com`). Used to build OAuth redirect URIs. |
| `MCP_AUTH_TOKEN`                                | ✅         | Bearer token MCP clients must send (unless GitHub OAuth is configured below).                                    |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`     | optional   | Set both to protect `/mcp` **and** offer "Sign in with GitHub" on the web UI, using the same OAuth app.          |
| `GITHUB_ALLOWED_USERS`                          | optional   | Comma-separated GitHub logins allowed when GitHub OAuth is on (set it — blank = any account). Applies to web + MCP. |
| `WEB_PASSWORD`                                  | ✅         | Web UI password (argon2id-hashed at boot). Break-glass when GitHub login is on: accepted only from `WEB_PASSWORD_ALLOWED_CIDRS`. |
| `WEB_PASSWORD_ALLOWED_CIDRS`                    | –          | Comma-separated CIDRs from which password login is accepted. Blank = private/loopback LAN ranges ("same VLAN").  |
| `TRUSTED_PROXY_CIDRS`                           | proxy      | CIDRs of your reverse proxy (e.g. the Docker network). Required behind the Cloudflare Tunnel for the IP gate to see the real client. |
| `SESSION_SECRET`                                | ✅         | Signs session cookies.                                                                                           |
| `TOKEN_ENC_KEY`                                 | ✅         | Fernet key encrypting tokens & client secrets at rest. Comma-separate multiple keys (newest first) to rotate.    |
| `DATABASE_PATH`                                 | –          | SQLite path (default `/data/health.db`).                                                                         |
| `LOG_LEVEL`                                     | –          | `debug`/`info`/`warning`/`error`.                                                                                |
| `CF_TUNNEL_TOKEN`                               | Cloudflare | Named-tunnel connector token.                                                                                   |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`     | optional   | Headless fallback — normally set in the UI.                                                                      |
| `OURA_CLIENT_ID` / `OURA_CLIENT_SECRET`         | optional   | Headless fallback.                                                                                               |
| `WITHINGS_CLIENT_ID` / `WITHINGS_CLIENT_SECRET` | optional   | Headless fallback.                                                                                               |

> **Provider API credentials are normally entered in the web UI** (`/providers`)
> and stored encrypted. The `*_CLIENT_*` env vars are only an optional
> bootstrap fallback for headless setups; the UI value always wins.

`.env` and `*.db` are git-ignored. Never commit secrets.

---

## Provider setup (OAuth apps)

Create an OAuth app with each provider and register the **exact** redirect URI.
Replace `health.example.com` with your domain.

| Provider          | Developer console                                                         | Redirect URI                                         | Scopes                                                                                                         |
| ----------------- | ------------------------------------------------------------------------- | ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| **Google Health** | Google Cloud Console → APIs & Services → Credentials → OAuth client (Web) | `https://health.example.com/oauth/google/callback`   | `…/googlehealth.activity_and_fitness.readonly`, `…health_metrics_and_measurements.readonly`, `…sleep.readonly` |
| **Oura**          | <https://cloud.ouraring.com> → OAuth applications                         | `https://health.example.com/oauth/oura/callback`     | `personal daily heartrate workout session spo2`                                                                |
| **Withings**      | <https://developer.withings.com> → your app                               | `https://health.example.com/oauth/withings/callback` | `user.info,user.metrics,user.activity,user.sleepevents`                                                        |

Notes:

- The redirect URI must match **byte-for-byte** what the server uses
  (`PUBLIC_BASE_URL` + `/oauth/<provider>/callback`).
- Google requires consent screen configuration and returns a refresh token only
  with `access_type=offline` + `prompt=consent` (the server requests both).
- Withings rotates its refresh token on every refresh; the server persists the
  new one automatically.

Paste each app's **client id** and **client secret** into the **Providers** page
and click **Connect** to run the OAuth flow.

---

## Install: from GHCR or build locally

Two compose files ship in `deploy/`:

- **`docker-compose.yml`** — pulls the **published image** from GHCR
  (`ghcr.io/adamconde/personal-health-mcp:latest`). This is the default for
  running the server; no build toolchain needed. Override `IMAGE` to pin a tag.
- **`docker-compose-dev.yml`** — **builds the image from source** in this repo.
  Use it when developing or running unreleased changes.

**Pull a published image** directly (the default compose file does this for you):

```bash
docker pull ghcr.io/adamconde/personal-health-mcp:latest
```

**Or build locally** (what `docker-compose-dev.yml` does):

```bash
docker build -f deploy/Dockerfile -t personal-health-mcp:local .
```

### Sample `docker-compose.yml`

A complete single-file example. Adjust the image:

```yaml
services:
  app:
    image: ghcr.io/adamconde/personal-health-mcp:latest
    env_file: [.env]
    environment:
      DATABASE_PATH: /data/health.db
    expose: ["8000"] # internal only — never publish to the host
    volumes: ["health-data:/data"]
    restart: unless-stopped

  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel --no-autoupdate run
    environment:
      - "TUNNEL_TOKEN=${CF_TUNNEL_TOKEN:?set CF_TUNNEL_TOKEN in .env}"
    depends_on: [app]
    restart: unless-stopped

volumes:
  health-data:
```

---

## Hosting

A Cloudflare Tunnel publishes a stable HTTPS hostname (needed for OAuth) and
keeps the app itself unpublished on the internal Docker network.

### Cloudflare Tunnel (no open ports)

Free on a Cloudflare account; you only need a domain added to Cloudflare.

1. Add your domain to Cloudflare (free plan is fine).
2. Create a **named tunnel** (Zero Trust dashboard → Networks → Tunnels, or
   `cloudflared tunnel create health`). Copy the **tunnel token**.
3. Route a hostname to the app: in the tunnel's **Public Hostname** config, map
   `health.example.com` → `http://app:8000`.
4. In `.env`: set `CF_TUNNEL_TOKEN=…` and `PUBLIC_BASE_URL=https://health.example.com`.
5. Launch:

   ```bash
   docker compose --env-file .env -f deploy/docker-compose.yml up -d
   ```

No inbound ports are opened; Cloudflare terminates TLS at its edge.

### LAN / development

You can run the app directly for development:

```bash
pip install -e ".[dev]"
make run    # uvicorn on http://localhost:8000
```

OAuth still requires a stable **HTTPS** redirect URL, so for real provider
connections use the Cloudflare Tunnel (or a tunnel to your dev box).

---

## Connecting MCP clients

Point any MCP client at `https://<your-domain>/mcp` with the bearer token.

**Claude Desktop** (`claude_desktop_config.json`):

```jsonc
{
  "mcpServers": {
    "personal-health": {
      "type": "streamableHttp",
      "url": "https://health.example.com/mcp",
      "headers": { "Authorization": "Bearer YOUR_MCP_AUTH_TOKEN" },
    },
  },
}
```

If your client lacks native remote Streamable HTTP, bridge over stdio:

```jsonc
{
  "mcpServers": {
    "personal-health": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://health.example.com/mcp",
        "--header",
        "Authorization: Bearer YOUR_MCP_AUTH_TOKEN",
      ],
    },
  },
}
```

### Authenticating with GitHub OAuth (optional)

Instead of the static bearer token, you can protect `/mcp` with **GitHub OAuth**
— clients do a browser login, and access is restricted to GitHub logins you allow.
The **web UI** uses the same OAuth app: when GitHub OAuth is configured the login
page shows **"Sign in with GitHub"** (allowlisted by the same `GITHUB_ALLOWED_USERS`),
and `WEB_PASSWORD` becomes a LAN-only break-glass fallback (see
[Web UI login](#web-ui-login)).

**1.** Create a **GitHub OAuth app** (GitHub → Settings → Developer settings →
OAuth Apps) with **Authorization callback URL** `https://<your-domain>/auth/callback`.
The web-UI login flow uses `…/auth/callback/web`, a subdirectory GitHub permits
for the same app — **no second callback to register**.

**2.** In `.env` set the following. When both id+secret are present, `/mcp`
switches from bearer to GitHub OAuth automatically and the web UI offers GitHub
sign-in. **Set `GITHUB_ALLOWED_USERS`** — otherwise any GitHub account that
authorizes the app could reach your data.

```ini
GITHUB_CLIENT_ID=Ov23li...
GITHUB_CLIENT_SECRET=...
GITHUB_ALLOWED_USERS=your-github-login   # comma-separated; restricts access
```

**3.** Point the client at the URL with **no `Authorization` header** — it
discovers OAuth and opens a browser login (or use `npx mcp-remote
https://health.example.com/mcp` with no `--header`):

```jsonc
{
  "mcpServers": {
    "personal-health": { "type": "streamableHttp", "url": "https://health.example.com/mcp" }
  }
}
```

In this mode the server runs the MCP app at the origin root so OAuth discovery
(`/.well-known/...`) and the callback (`/auth/callback`) resolve correctly; the
web UI continues to serve at `/`.

### Tools

| Tool                          | Purpose                                                          |
| ----------------------------- | ---------------------------------------------------------------- |
| `health_list_providers`       | Providers and their connection status.                           |
| `health_provider_auth_status` | Whether a provider is connected/usable.                          |
| `health_list_metrics`         | Metrics available from connected providers.                      |
| `health_get_metric`           | A metric over a date range, resolved (provider named per point). |
| `health_compare_metric`       | A metric from every provider side-by-side (unresolved).          |
| `health_get_sleep`            | Composite sleep summary for a night.                             |
| `health_get_daily_summary`    | Multi-metric summary for a day.                                  |
| `health_set_metric_authority` | Set a metric's resolution preference.                            |

Example prompts: _“What was my weight last week, in pounds?”_, _“Compare my step
count across providers for yesterday.”_, _“Make Withings the authority for
weight, falling back to Google.”_

### Web UI pages

- **Dashboard** — provider status + effective preferences.
- **Providers** — enter credentials (secret is write-only), connect/disconnect.
- **Metrics** — per-metric authority/auto + fallback order, saved for all
  metrics at once with a single button.
- **Units** — mass, distance, height (cm or ft/in), temperature display units;
  imperial by default.
- **Logging** — rolling history of provider API errors in a sortable, filterable
  table (severity, provider, time, error preview); click a row for the full
  message. Replaces the inline error text that used to appear on Providers/Dashboard.

### Web UI login

- **GitHub sign-in** (when `GITHUB_CLIENT_ID`/`SECRET` are set) — the same OAuth
  app and `GITHUB_ALLOWED_USERS` allowlist that guard `/mcp`. Works from anywhere.
- **Password (break-glass)** — `WEB_PASSWORD` is accepted **only** from client IPs
  in `WEB_PASSWORD_ALLOWED_CIDRS` (blank = private/loopback LAN ranges). The login
  page hides the password field entirely when the request isn't from an allowed IP.
- **Behind a proxy:** the real client IP is read from forwarded headers
  (`X-Forwarded-For`, peeled right-to-left; `CF-Connecting-IP` for Cloudflare)
  **only** when the direct peer is in `TRUSTED_PROXY_CIDRS` — otherwise the peer
  address is used and forwarded headers are ignored (anti-spoofing). Set
  `TRUSTED_PROXY_CIDRS` to your Docker/proxy network so LAN clients are recognized;
  if unset, password login effectively fails closed behind the proxy (GitHub still works).

---

## Operations

- **Backups:** back up the `health-data` volume (`/data/health.db*`). It holds
  your preferences and encrypted tokens.
- **Key rotation:** prepend a new Fernet key to `TOKEN_ENC_KEY`
  (`new,old`); reads still work with the old key, new writes use the new one.
  Once everything is re-encrypted you can drop the old key.
- **Upgrades:** `docker compose … pull && docker compose … up -d`.
- **Troubleshooting:**
  - _401 from `/mcp`_ → check the `Authorization: Bearer` header / `MCP_AUTH_TOKEN`.
  - _Redirect-URI mismatch_ → the registered URI must equal
    `PUBLIC_BASE_URL` + `/oauth/<provider>/callback` exactly.
  - _Token refresh failed / reconnect prompt_ → re-connect the provider on the
    Providers page (e.g. credentials changed or refresh token revoked).

---

## Security

- `/mcp` requires a static bearer token (or GitHub OAuth, if configured); the web UI requires a session login
  (argon2id-hashed password). Three distinct secrets (MCP bearer, session key,
  encryption key) — never reuse them.
- OAuth tokens and client secrets are **encrypted at rest** (Fernet).
- CSRF protection on all state-changing forms; strict security headers
  (HSTS, CSP, `nosniff`, `frame-ancestors 'none'`).
- The app is never published to the host; only the TLS proxy/tunnel is exposed.
- Set `cookie_secure=true` (default) in any internet-facing deployment.

---

## Adding a new provider

1. Create `src/personal_health_mcp/providers/<vendor>.py` subclassing
   `HealthProvider`; implement `capabilities()` and `fetch_metric()` (map the
   raw response into canonical `DataPoint`s, reusing existing metric keys), set
   `oauth`, and decorate with `@register`.
2. Import it in `providers/__init__.py`.
3. Run the one-time OAuth connect on the Providers page.

No changes to the aggregator, resolution engine, units, tools, or templates —
the new provider appears automatically. (See `tests/integration/test_extensibility.py`.)

---

## Development

```bash
make install      # editable install with dev extras
make lint         # ruff
make type         # mypy
make cov          # pytest with coverage
make check        # all of the above
make run          # run locally on :8000
```

## Project layout

```text
src/personal_health_mcp/
  server.py        # ASGI root: mounts /mcp (bearer) + web UI; uvicorn factory
  app.py           # wiring of shared services (AppContext)
  config.py        # settings / secrets
  models.py        # DataPoint, MetricSeries, ResponseEnvelope, Token, enums
  metrics.py       # canonical metric registry + preference groups
  units.py         # unit table + conversion
  display.py       # display-unit resolution
  resolution.py    # authority / fallback / auto engine
  aggregator.py    # fetch -> resolve -> convert -> envelope
  storage.py       # SQLite + encrypted token/secret store + prefs
  crypto.py        # Fernet/MultiFernet helper
  oauth.py         # AuthFlow + TokenManager (lazy refresh)
  tools.py         # MCP tools
  providers/       # base + google/oura/withings
  web/             # routes, templates, security middleware
deploy/            # Dockerfile, docker-compose.yml (GHCR image) + -dev.yml (build)
.github/workflows/ # ci.yml, release.yml (GHCR)
tests/             # unit + integration
```

---

## Version history

- **2.6.0** — Simplified deployment: Cloudflare Tunnel only, two compose files.
  - Removed the **Caddy / Let's Encrypt** hosting option (`Caddyfile`,
    `compose.caddy.yml`, the `CADDY_DOMAIN` env var, and the `caddy_domain`
    config field). Cloudflare Tunnel is now the sole supported public-hosting
    path. A stale `CADDY_DOMAIN` in `.env` is harmless — config ignores it.
  - Collapsed the base + cloudflared overlay into a **single self-contained
    compose file**. `deploy/docker-compose.yml` now runs the published GHCR
    image (`ghcr.io/adamconde/personal-health-mcp:latest`); the new
    `deploy/docker-compose-dev.yml` builds from source for development.
  - Run: `docker compose --env-file .env -f deploy/docker-compose.yml up -d`.
- **2.5.0** — Logging tab for provider errors.
  - New **Logging** page: a rolling, capped (newest 500) history of provider API
    errors in a sortable/filterable table — severity, provider, timestamp, and a
    one-line preview; click any row for the full error. Auth failures (401/403)
    are recorded at `warning` severity, others at `error`.
  - Errors are persisted to a new `error_log` table (every `set_status` that
    records a `last_error` now also appends to the log).
  - Removed the inline error text from the Providers and Dashboard pages (status
    chips remain); error detail now lives on the Logging page.
- **2.4.0** — Read-path error visibility & Google query correctness.
  - `health_get_metric` (and `compare_metric`) now surface per-provider failures
    in an `errors` map instead of masking them as an empty result — a 4xx/auth
    error no longer reads as "no data for this metric"; an explaining `note` is
    set when an empty series is due to provider errors.
  - Google Health: build the time-window filter from each data type's actual
    time field — `interval.start_time`, `sample_time.physical_time`, or daily
    `date` (`YYYY-MM-DD`) — fixing 400s on sample metrics (weight, heart rate,
    SpO₂, …) and daily metrics (resting heart rate, respiratory rate).
  - Removed Google `total_calories`: it has no standalone v4 dataPoints type
    (lives only on the dailyRollUp endpoint). Still served by Oura/Withings.
- **2.3.0** — GitHub sign-in for the web UI + IP-gated password.
  - The web UI can authenticate with **GitHub OAuth**, reusing the same OAuth app
    and `GITHUB_ALLOWED_USERS` allowlist that protect `/mcp` (web callback
    `…/auth/callback/web`, a subdirectory of the existing one — no GitHub change).
  - `WEB_PASSWORD` becomes a **break-glass** login accepted only from
    `WEB_PASSWORD_ALLOWED_CIDRS` (default: private/loopback LAN ranges).
  - Real client IP is resolved securely behind proxies via `TRUSTED_PROXY_CIDRS`
    (right-to-left `X-Forwarded-For` peeling + `CF-Connecting-IP`); forwarded
    headers are ignored unless the direct peer is a trusted proxy.
- **2.2.0** — Units & metric-preferences UX.
  - Display units now default to **imperial** across the board: mass `lb`,
    distance `mi`, temperature `°F`, height `ft/in`.
  - Height offers two choices — `cm` or `ft/in` — and `ft/in` renders as whole
    feet and inches (e.g. `5' 9"`) via a new `formatted` field on resolved points.
  - The Metrics tab saves every metric's resolution preference at once with a
    single button (one form) instead of one button per metric.
- **2.1.0** — Read-path correctness & resilience.
  - Recover from a mid-fetch token rejection: providers classify 401/403 as an
    auth error and the aggregator refreshes the token once and retries (instead
    of masking it as "no data").
  - Malformed provider records are skipped individually rather than dropping the
    whole series; Withings `getmeas` pagination can no longer loop forever.
  - Auto-mode resolution tie-breaks are now fully deterministic.
  - Provider fetches run concurrently; date-range inputs are validated and capped.
- **2.0.0** — Fail-fast security validation at startup (**breaking**: the server
  now refuses to boot on insecure defaults). `SESSION_SECRET` is required (the
  insecure built-in fallback was removed); `MCP_AUTH_TOKEN` is required unless
  GitHub OAuth is configured; and `GITHUB_ALLOWED_USERS` is required when GitHub
  OAuth is enabled (an empty allowlist no longer silently admits any GitHub user).
- **1.0.0** — First stable release.
  - Google Health, Oura, and Withings providers behind a canonical, unit-normalized,
    provider-attributed model with authority/fallback/auto resolution.
  - MCP tools over Streamable HTTP; auth via a static bearer token **or optional
    GitHub OAuth** (browser login + GitHub-login allowlist).
  - Material Design 3 web UI (light/dark) for provider connections and
    metric/unit preferences, with links to each vendor's credential console.
  - Docker with Cloudflare Tunnel / Caddy hosting overlays; CI + GHCR release.
  - Run with `docker compose --env-file .env …` (so Compose interpolates
    `CF_TUNNEL_TOKEN` / `CADDY_DOMAIN` from the repo-root `.env`).

## License

MIT.
