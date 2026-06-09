# personal-health-mcp

A **self-hosted, single-user MCP server** that aggregates your personal health
data from **Google Health**, **Oura**, and **Withings** behind one normalized,
provider-attributed interface — and is built so new vendors drop in with no
changes to the core.

It exposes [Model Context Protocol](https://modelcontextprotocol.io) tools
(over Streamable HTTP, reachable from Claude Desktop or any MCP client on another
machine) and a small web UI for managing provider connections and preferences.

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
- **Your units.** Choose kg vs lb, km vs mi, °C vs °F. When a provider can't
  serve a unit natively, the server converts.
- **Broad coverage.** Steps, distance, calories, weight & body composition,
  heart rate, HRV, SpO₂, sleep stages, readiness/sleep scores, VO₂max,
  temperature, blood pressure/glucose, and more — including metrics only one
  provider supplies.
- **Single user, self-hosted.** No multi-tenant support by design. The
  preferences page _is_ your preferences. If someone else wants it, they host
  their own.

## Architecture at a glance

```
MCP clients (Claude Desktop, …) ──HTTPS──▶ [ Cloudflare Tunnel  OR  Caddy ]
                                                      │  (TLS terminated here)
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
- **A domain you own.** Both supported hosting options need a stable HTTPS
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
# pick ONE hosting overlay (see Hosting options):
docker compose -f deploy/docker-compose.yml -f deploy/compose.caddy.yml up -d
```

Open `https://<your-domain>/`, log in with `WEB_PASSWORD`, go to **Providers**,
enter each provider's client id/secret, click **Connect**, then set your
**Metrics** and **Units** preferences.

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
| `MCP_AUTH_TOKEN`                                | ✅         | Bearer token MCP clients must send.                                                                              |
| `WEB_PASSWORD`                                  | ✅         | Single-user web UI password (hashed with argon2id at boot; never stored in plaintext).                           |
| `SESSION_SECRET`                                | ✅         | Signs session cookies.                                                                                           |
| `TOKEN_ENC_KEY`                                 | ✅         | Fernet key encrypting tokens & client secrets at rest. Comma-separate multiple keys (newest first) to rotate.    |
| `DATABASE_PATH`                                 | –          | SQLite path (default `/data/health.db`).                                                                         |
| `LOG_LEVEL`                                     | –          | `debug`/`info`/`warning`/`error`.                                                                                |
| `CADDY_DOMAIN`                                  | Caddy      | Domain Caddy serves + gets a cert for.                                                                           |
| `CF_TUNNEL_TOKEN`                               | Cloudflare | Named-tunnel token.                                                                                              |
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
| **Oura**          | https://cloud.ouraring.com → OAuth applications                           | `https://health.example.com/oauth/oura/callback`     | `daily heartrate personal workout session spo2Daily`                                                           |
| **Withings**      | https://developer.withings.com → your app                                 | `https://health.example.com/oauth/withings/callback` | `user.info,user.metrics,user.activity,user.sleepevents`                                                        |

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

**Pull a published image** (set `IMAGE` and drop the `build:` section, or just
reference it):

```bash
docker pull ghcr.io/adamconde/personal-health-mcp:latest
```

**Or build locally** (default in the compose files):

```bash
docker build -f deploy/Dockerfile -t personal-health-mcp:local .
```

### Sample `docker-compose.yml`

A complete single-file example (Caddy variant). Adjust the image/domain:

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

  caddy:
    image: caddy:2
    ports: ["80:80", "443:443"]
    environment:
      CADDY_DOMAIN: ${CADDY_DOMAIN}
    volumes:
      - ./deploy/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config
    depends_on: [app]
    restart: unless-stopped

volumes:
  health-data:
  caddy-data:
  caddy-config:
```

---

## Hosting options (pick one)

Both publish a stable HTTPS hostname (needed for OAuth) and keep the app itself
unpublished on the internal Docker network.

### Option A — Cloudflare Tunnel (no open ports)

Free on a Cloudflare account; you only need a domain added to Cloudflare.

1. Add your domain to Cloudflare (free plan is fine).
2. Create a **named tunnel** (Zero Trust dashboard → Networks → Tunnels, or
   `cloudflared tunnel create health`). Copy the **tunnel token**.
3. Route a hostname to the app: in the tunnel's **Public Hostname** config, map
   `health.example.com` → `http://app:8000`.
4. In `.env`: set `CF_TUNNEL_TOKEN=…` and `PUBLIC_BASE_URL=https://health.example.com`.
5. Launch:
   ```bash
   docker compose -f deploy/docker-compose.yml -f deploy/compose.cloudflared.yml up -d
   ```

No inbound ports are opened; Cloudflare terminates TLS at its edge.

### Option B — Caddy reverse proxy (Let's Encrypt)

For when you can port-forward.

1. Point a DNS `A`/`AAAA` record for `health.example.com` at your host.
2. Ensure ports **80** and **443** are reachable from the internet.
3. In `.env`: set `CADDY_DOMAIN=health.example.com` and
   `PUBLIC_BASE_URL=https://health.example.com`.
4. Launch:
   ```bash
   docker compose -f deploy/docker-compose.yml -f deploy/compose.caddy.yml up -d
   ```

Caddy obtains and renews the certificate automatically.

### Option C — LAN / development

You can run the app directly for development:

```bash
pip install -e ".[dev]"
make run    # uvicorn on http://localhost:8000
```

OAuth still requires a stable **HTTPS** redirect URL, so for real provider
connections use Option A or B (or a tunnel to your dev box).

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
- **Metrics** — per-metric authority/auto + fallback order.
- **Units** — mass, distance, height, temperature display units.

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

- `/mcp` requires a static bearer token; the web UI requires a session login
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

```
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
deploy/            # Dockerfile, compose (+ cloudflared/caddy overlays), Caddyfile
.github/workflows/ # ci.yml, release.yml (GHCR)
tests/             # unit + integration
```

---

## Version history

- **0.1.0** — Initial release: Google Health, Oura, Withings providers;
  authority/fallback/auto resolution; unit conversion; MCP tools; Material
  Design 3 web UI (light/dark); Docker + Cloudflare/Caddy hosting; CI/CD to GHCR.

## License

MIT.
