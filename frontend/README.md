# Pachelarr Frontend

Pachelarr dashboard + settings UI. Built with Next.js, @tremor/react, and Tailwind CSS v3. It talks to the Pachelarr Python backend over a server-side proxy.

## Dev

```bash
npm install
npm run dev
```

Serves on `:3000` and proxies `/api/*` to `PACHELARR_BACKEND_URL` (default `http://localhost:6800`). Requires the backend running on `:6800`.

## Build

```bash
npm run build
```

Produces a standalone output (`.next/standalone`) via `output: "standalone"` in `next.config.ts`.

## Prod (Docker)

Built via the compose `frontend` service:

```bash
docker compose up --build
```

This builds the frontend image from `frontend/Dockerfile` and runs `node server.js` on `:3000`.

## Env Vars

| Variable | Scope | Description |
|----------|-------|-------------|
| `PACHELARR_BACKEND_URL` | server-side | Backend URL the proxy targets. Must match the backend's `PACHELARR_PORT` — currently `6800`, NOT the Dockerfile `EXPOSE 8080`. |
| `PACHELARR_API_KEY` | server-side | API key for the `/settings` proxy. Injected from the top-level `.env`; the browser never sees it. |
| `FRONTEND_PORT` | compose | Host port for the frontend (default `3000`). |

## Architecture

Two separate images: the backend image (Python, from the repo-root `Dockerfile`) and the frontend image (Node, from `frontend/Dockerfile`). They communicate over the docker network by service name (`pachelarr`).

## Per-indexer metrics

The `/statsz/indexers` endpoint currently returns real indexer names with all metrics zeroed (stub); full instrumentation is a follow-up. The IndexerTable shows an "Awaiting instrumentation" badge.
