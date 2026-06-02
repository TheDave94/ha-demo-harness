# ha-demo-harness

A pre-baked, **populated demo Home Assistant** for end-to-end testing of HA
projects — currently [`oriel-dashboard`](../oriel-dashboard) (a Lovelace
strategy) and [`pollenwatch`](../pollenwatch) (an integration), plus future HA
projects. It is the standing, version-controlled replacement for the
hand-staged throwaway demo that used to live only as a running container.

> **Status:** Session 1 — reproducible one-command bring-up. No CI wiring, no
> published image, no assertions yet (those are Sessions 2–3). See `DESIGN.md`
> for the full architecture and `FINDINGS.md` for Session-1 results.

## Quick start

```bash
docker compose up --build      # build + boot; ready on http://localhost:8127
# ... or without the compose plugin:
docker build -t ha-demo-harness:dev .
docker run -d --name ha-demo-harness -p 8127:8123 ha-demo-harness:dev
```

The demo comes up fully populated: 7 areas, 178 registry entities (a generic
fake multi-area home via HA's `demo:` integration + template sensors), the
`oriel_demo` shim (extra registry-backed lights + plants), a baked-in
`pollenwatch` integration with a staged pollen spread, an `oriel` strategy
dashboard at `/oriel-demo`, and 48h of synthetic recorder history for the
sparkline charts.

Port **8127** is deliberately different from the legacy hand-staged reference
demo on `:8126`, so the harness runs alongside it without colliding.

## The public token (intentionally NOT a secret)

The demo HA is a disposable fake container. Its admin token controls nothing
real — no devices, no production data — and it is regenerable at will. So we
**bake a fixed, well-known long-lived access token into the image on purpose**.
This is the whole point: it lets a *public* repo run live-HA e2e in CI with **no
GitHub secret at all** (see `DESIGN.md` §6 and `oriel-dashboard/DEFERRED.md`).

```
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI1MTkzOTBlMWQwODA3Mzc5NTBhMDFkNWM5NWExMGFkZCIsImlhdCI6MTcxNzIwMDAwMCwiZXhwIjo0MDcwOTA4ODAwfQ.TNaKl44ES4522YFug934BowsEY1bbIi3x7T0D7KoPfU
```

It is an HS256 JWT that never expires (exp = 2099). It is regenerated
deterministically by `scripts/make_seed_auth.py` (which writes
`seed/.storage/auth` and the matching token). **Never reuse this pattern for a
real HA** — it is safe here only because the container is disposable.

Example:

```bash
TOKEN=$(cat seed/.storage/.PUBLIC_TOKEN)
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8127/api/ | jq .
```

## HA version lock — 2026.5.x (do not bump casually)

The base image is **pinned by digest** to HA 2026.5.4:

```
ghcr.io/home-assistant/home-assistant@sha256:ceb1202133a5a036e8b03e20a10eb113186cc2f871968323c6fc6c3fc4205716
```

This pin protects **two** things:

1. **The fixture.** `seed/configuration.yaml` uses **legacy `template:` entities**
   (per-area temp/humidity sensors + door/window/motion binary sensors). Legacy
   template entities are **REMOVED in HA 2026.6**. Bumping HA past 2026.5 is a
   **breaking change** that requires migrating those entities first — see
   `FINDINGS.md` for the exact migration scope. It is *not* a routine bump.
2. **The recorder schema** (v53), which `harness_seed` writes backdated history
   against.

## Layout

```
.
├── Dockerfile              # FROM pinned HA digest; bakes seed/ → /config
├── docker-compose.yml      # one-command bring-up on :8127
├── seed/                   # everything copied into /config at build
│   ├── configuration.yaml  # lifted verbatim from the reference (+ harness_seed: line)
│   ├── custom_components/
│   │   ├── oriel_demo/      # registry-backed extra lights + plants (verbatim)
│   │   ├── pollenwatch/     # baked pinned integration (consumers inject HEAD over it)
│   │   └── harness_seed/    # NEW: stages dynamic state + injects recorder history
│   ├── .storage/           # sanitized registry/lovelace seeds + baked auth
│   └── www/community/       # oriel build + bubble-card + apexcharts-card
├── scripts/
│   ├── make_seed_auth.py    # regenerates seed/.storage/auth + the public token
│   └── wait-for-ha.sh       # poll until the demo HA is ready
├── lib/                    # shared HA client (ported in Session 2 from pollenwatch)
└── .github/workflows/      # stubs — wired in Session 2
```

### What is verbatim vs. staged

- **Static + diffable → committed** (configuration.yaml, the custom components,
  JSON `.storage` registry/lovelace/auth seeds).
- **Dynamic + schema-fragile → programmatic** in `harness_seed`: the pollen
  consensus spread (grass:high, alder/birch:low, ragweed:mixed, mugwort:none,
  olive:unknown), person home/away (Alex home, Sam away), and now-relative
  synthetic recorder history for the temp/humidity sensors. This replaces the
  old hand-poked WS/force-state chain with version-controlled code.

See `DESIGN.md` §2 for the reproducibility rationale.
