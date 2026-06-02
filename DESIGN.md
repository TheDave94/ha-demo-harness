# Design Doc — Reusable HA Test Harness (`ha-demo-harness`)

**Status:** Proposal for architectural review. Nothing built. No repo created. Running demo on `:8126` untouched.
**Author:** Claude (investigation + architecture)
**Date:** 2026-06-02
**Decision already locked (not re-litigated here):** the harness lives in its **own** new repo, a sibling of `oriel-dashboard` and `pollenwatch`, so neither existing repo depends on the other.

---

## 0. TL;DR / Recommendations

| Question | Recommendation |
|---|---|
| What is the shared core? | A **pre-baked, populated demo HA** (config + `oriel_demo` shim + deterministic fixtures + a known token) shipped as a **public GHCR Docker image** + a thin shared HA client lib. Assertions are **NOT** shared. |
| How to make it reproducible? | **Hybrid:** commit the static layer (`configuration.yaml`, custom components, JSON `.storage` registry seeds) + a **`harness_seed` bootstrap custom_component** that deterministically stages dynamic states **and synthetic recorder history** on startup. Bake into a Docker image. One command: `docker compose up`. |
| How does a *separate public* repo's CI consume it? | **Pull the published GHCR image** (`docker run`), not submodule/checkout/vendor. Inject the consumer's freshly-built artifact with `docker cp`. |
| Self-hosted runner? | **No.** The harness brings its own HA via the image — runs entirely on `ubuntu-latest`. This is strictly better than pollenwatch's persistent-throwaway + self-hosted model. |
| Token/secret? | The demo token guards a disposable fake container with no real-world effects. **Bake a fixed, public, non-secret long-lived token into the image.** oriel needs **zero GitHub secrets** to run e2e in CI. This is the exact realization of the DEFERRED.md revisit trigger. |
| First test? | **F4-regression:** the ApexCharts sparkline renders inside an `ha-card` with a nonzero bounding box. Plus ~7 more below. |

---

## 1. Architecture — shared core vs. per-project assertions

### 1.1 The boundary, stated as a contract

The harness's job is to **stand up a known, populated, authenticated HA and hand the consumer a way to inject its build**. It stops there. It asserts nothing about either project's behavior.

**The harness guarantees (the "core"):**

1. A running HA reachable at a known URL/port (container `:8123`) at a pinned HA version (today: `2026.5.4` / image `ghcr.io/home-assistant/home-assistant:stable` — we will **pin a digest**, see §2).
2. A deterministic fixture: **7 areas** (Living Room, Kitchen, Bedroom, Bathroom, Office, Hallway, Garage), **178 entities** across the same platform mix observed today — `demo` (69), `template` (40, per-area temp/humidity + battery tiers + door/window/motion binary sensors), `pollenwatch` (24), `oriel_demo` (12 lights + 4 plants), plus `input_select.house_mode`, scenes, scripts, 2 persons (Alex, Sam).
3. Synthetic **recorder history** for at least `sensor.living_room_temperature` (and a small set) so ApexCharts/sparklines have data to plot.
4. A storage-mode dashboard `lovelace.oriel_demo` already configured with `strategy: custom:oriel` (all the toggles the strategy reads — `show_pollen`, `show_plants_section`, `show_persons_section`, `show_routines_section`, `use_bubble_drawers`, `favorite_entities`, a `custom:oriel-sparkline-card` with `use_apexcharts: true`).
5. `lovelace_resources` pointing at `/local/community/oriel-dashboard/oriel.js`, `bubble-card.js`, `apexcharts-card.js`.
6. A **known long-lived token** (baked, public — see §6).
7. A documented **injection point** for the consumer's build: `/config/www/community/oriel-dashboard/` (frontend) and `/config/custom_components/<integration>/` (backend).
8. A small **shared client library** (lifted from pollenwatch's proven `cleanroom/lib/`): a WS client (`ha_ws.py` — auth handshake, id-matched RPC, `max_size=20MiB`), a REST client (`ha_api.py` — `wait_until_up`, `wait_for_component`), and settle-point polling helpers. This is the one piece of *test plumbing* that's genuinely shared because both projects need "is HA up / is my thing loaded yet."

**The consumer owns (the "per-project" layer):**

- **oriel-dashboard** — *rendering* assertions. Its existing Playwright suite (`tests/e2e-browser/*.spec.ts`) and vitest API smoke (`tests/e2e/strategy-api.test.ts`), pointed at the harness URL with the harness token. It builds `dist/oriel.js`, injects it, drives a headless browser, pierces shadow DOM, asserts cards mount and look right.
- **pollenwatch** — *data-contract* assertions. Its WS discovery-contract test (`tests/e2e/test_real_ha_discovery.py`): `config_entries/get`, `pollenwatch/config` shape, options-flow round-trip. No browser.

### 1.2 Why we do NOT force a shared assertion framework

These are **categorically different test shapes**:

- oriel asserts **pixels and DOM** (does the sparkline have a nonzero box? does the editor's health section appear?). Tooling: Playwright, shadow-DOM piercing, axe-core.
- pollenwatch asserts **JSON contracts** (does `pollenwatch/config` return `selected_species: [str…]` and a valid `default_layout`?). Tooling: pytest + websockets.

A shared "assertion DSL" would be a leaky abstraction over two unrelated problem domains. The **only** thing they share is *"a populated HA exists and I can reach it"* — and that's exactly what the core provides (the running HA + the `lib/` client). Everything above that line is project-local. This mirrors pollenwatch's own internal split: `cleanroom/lib/` (reusable) vs `verify.py` Gate D (pollenwatch-specific).

```
┌─────────────────────────── ha-demo-harness (own repo) ────────────────────────────┐
│  SHARED CORE                                                                         │
│   • configuration.yaml (generic fake multi-area home)                               │
│   • custom_components/oriel_demo  (registry-backed lights + plants)                  │
│   • custom_components/harness_seed (NEW: stages dynamic states + recorder history)  │
│   • .storage/ JSON seeds (areas, entities, devices, persons, lovelace, resources)   │
│   • baked public long-lived token                                                   │
│   • lib/  (ha_ws.py, ha_api.py, settle helpers — from pollenwatch cleanroom)        │
│   • bring-up: docker-compose.yml + published GHCR image                             │
│   • injection contract: www/community/<proj>/  and  custom_components/<proj>/        │
└─────────────────────────────────────────────────────────────────────────────────-─┘
        ▲ consumes (pull image, inject build)              ▲ consumes (pull image)
        │                                                  │
┌───────┴────────────────────┐                  ┌──────────┴──────────────────────────┐
│ oriel-dashboard (public)   │                  │ pollenwatch                          │
│  PER-PROJECT: rendering     │                  │  PER-PROJECT: data contract          │
│  Playwright e2e-browser/    │                  │  pytest WS discovery test            │
│  + vitest strategy-api      │                  │  (+ keeps its own cleanroom gate)    │
└────────────────────────────┘                  └──────────────────────────────────────┘
```

---

## 2. Reproducibility — turning the hand-staged pile into one command

This is the make-or-break section. Today the demo is a **hand-staged pile**: a bind-mounted config dir, a manually-built `.db`, registry edits, and an undocumented chain of WS-pokes / `force_state` / `sed`. Reproducing it = re-running steps nobody wrote down. We classify every piece of state by how it should be reproduced.

### 2.1 Inventory → reproduction strategy (per layer)

| State | Today | Reproduction strategy | Why |
|---|---|---|---|
| `configuration.yaml` (76 lines: template sensors, scenes, scripts, `input_select`, `demo:`, `oriel_demo:`) | committed text already | **Commit verbatim.** | Plain YAML, diffable, deterministic. |
| `custom_components/oriel_demo/` (shim: 8 lights + 4 plants into registry+state) | committed Python | **Commit verbatim.** | Code; deterministic on boot. |
| `.storage/core.area_registry`, `core.entity_registry`, `core.device_registry`, `core.config_entries`, `person`, `homeassistant.exposed_entities` | hand-edited JSON in the live container | **Commit as templated JSON seeds.** Strip/placeholder the few volatile fields (uuids, `user_id`, timestamps) and let bring-up fill them, OR commit fixed values (a disposable container can have fixed uuids). | JSON is diffable and review-friendly. This is the registry **shape** — it must be stable across runs or entity_ids drift. |
| `.storage/lovelace.oriel_demo`, `lovelace_resources`, `lovelace_dashboards` | hand-staged JSON | **Commit verbatim.** | This *is* the system-under-test config (`strategy: custom:oriel`). |
| `.storage/auth`, `auth_provider.homeassistant`, `http.auth`, `onboarding`, `core.uuid` | live, secret-bearing | **Generate a fixed seed once, commit it** (owner user + the baked token, see §6). Onboarding marked done. | Lets HA boot straight past onboarding with a known token. The container is fake/disposable so committing these is safe. |
| **Dynamic states** — pollen sensor states, plant moisture/temperature attributes, anything poked over WS after boot | undocumented WS-poke chain | **`harness_seed` custom_component** sets them on startup from a committed table. | A startup component is deterministic, version-controlled, and self-documenting. Replaces `sed`/WS-poke. |
| **Synthetic recorder history** (the `home-assistant_v2.db`, ~1.1 MB + 3.5 MB WAL — drives ApexCharts/sparkline) | hand-built sqlite | **`harness_seed` generates it on first boot** via a seeded deterministic function injecting historical `states`/`statistics` rows; do NOT commit the binary `.db`. | A committed `.db` is opaque, large, churns on every HA schema bump, and isn't reviewable. A generator from a fixed seed is reproducible and survives HA version upgrades. **This is the hardest part — flagged in §8.** |

### 2.2 The three options, weighed

**Option A — docker-compose + committed binary `.storage` + committed `.db`.**
Fastest to *capture* (snapshot the live container). But: the `.db` is a binary blob that breaks on every HA recorder-schema migration, isn't reviewable in PRs, and bloats the repo. The `.storage` blobs are at least JSON. **Rejected for the `.db`; partially adopted for JSON `.storage`.**

**Option B — fully programmatic bootstrap** (a setup script or component builds *everything*, including registries, from scratch on boot).
Most robust against HA upgrades, nothing binary committed. But: reconstructing 178 entities + 7 areas + device links + a strategy dashboard purely in code is a large, fiddly surface; the current `oriel_demo/__init__.py` already shows how awkward direct registry writes are (it has to `async_get_or_create` then `hass.states.async_set` by hand). Overkill for the static registry shape that almost never changes. **Rejected as the sole mechanism.**

**Option C — HYBRID (recommended).**
- **Static & diffable → committed** (configuration.yaml, custom_components, JSON `.storage` registry + lovelace + auth seeds).
- **Dynamic & schema-fragile → programmatic** via the `harness_seed` component (dynamic states + recorder history).
- Everything baked into a **Docker image**; `docker-compose.yml` for local one-command bring-up.

This is exactly the split pollenwatch already validated: it commits `config/matrix.json` + pinned configs (static) and uses `bootstrap.py`/`lib/` to do the live staging (dynamic). We're adopting their proven pattern.

### 2.3 The one-command bring-up

```bash
# local
docker compose up           # builds-or-pulls image, boots demo HA, runs harness_seed, ready on :8126
# CI
docker run -d -p 8123:8123 ghcr.io/thedave94/ha-demo-harness:v1   # pinned tag
```

The image's entrypoint: copy committed `/seed` → `/config`, start HA, `harness_seed` runs on `async_setup` to stage dynamic states + synthetic history, HA reaches a "ready" state polled by `lib.ha_api.wait_until_up` + `wait_for_component`. **HA version is pinned by digest** in the Dockerfile `FROM` so the fixture and the renderer never drift out from under the tests.

---

## 3. CI consumption — how a separate public repo stands this up

### 3.1 The four options for cross-repo consumption

| Mechanism | How | Verdict |
|---|---|---|
| **git submodule** | oriel adds harness as a submodule, builds the HA image in-job | ❌ Couples oriel's checkout to harness internals; every e2e run rebuilds an HA image (slow, minutes); submodule SHA bumps are friction. |
| **checkout-other-repo action** (`actions/checkout` with `repository:`) | oriel checks out harness source, `docker compose build` then up | ❌ Same rebuild cost as submodule; public→public makes the checkout itself trivial, but you pay image-build time every run. |
| **vendored copy** | copy harness files into oriel | ❌ Guaranteed drift; defeats the "neutral shared home" decision. |
| **published GHCR image** ⭐ | harness CI builds + publishes `ghcr.io/thedave94/ha-demo-harness:vX`; consumers `docker run` it | ✅ **Recommended.** Pre-baked = seconds to boot. Pinnable by tag/digest. Public image + public consumer = no auth friction (anonymous pull). Image build cost is paid once in the harness repo, not on every consumer run. |

**Recommendation: published GHCR image.** It's the cleanest realization of "neutral shared home": consumers depend on a *versioned artifact*, not on harness source layout. The shared `lib/` client (small, pure Python) can additionally be vendored or pip-installed if a consumer wants the WS helpers, but the *HA itself* always arrives as the image.

### 3.2 The build-injection handoff (per consumer)

The image ships with an **empty placeholder** at the injection points. Each consumer drops its freshly-built artifact in during its own CI run.

**oriel-dashboard (frontend):**
```yaml
- run: npm ci && npm run build            # produces dist/oriel.js + code-split chunks
- run: docker run -d --name demo -p 8123:8123 ghcr.io/thedave94/ha-demo-harness:v1
- run: ./wait-for-ha.sh http://localhost:8123     # uses harness lib settle-poll
- run: |                                   # INJECT the build the browser will load
    docker exec demo rm -rf /config/www/community/oriel-dashboard
    docker cp dist/. demo:/config/www/community/oriel-dashboard/
    docker exec demo sh -c 'rm -f /config/www/community/oriel-dashboard/*.gz /config/www/community/oriel-dashboard/*.br'
- run: HA_URL=http://localhost:8123 HA_TOKEN=$HARNESS_TOKEN npx playwright test
```
`lovelace_resources` in the image already points at `/local/community/oriel-dashboard/oriel.js`, so once the files land, the next browser load serves the fresh build. No HA restart needed (frontend assets are served on demand). Strip stale `.gz`/`.br` so HA doesn't serve a cached compressed copy — this is the exact gotcha oriel's own deploy notes already document.

**pollenwatch (backend integration):** the integration is *already in the image* (24 entities), but to test **its own HEAD build** it injects + restarts:
```yaml
- run: docker cp custom_components/pollenwatch/. demo:/config/custom_components/pollenwatch/
- run: docker restart demo && ./wait-for-ha.sh http://localhost:8123
- run: pytest tests/e2e/test_real_ha_discovery.py   # WS contract, no browser
```
This is the same `rsync HEAD → restart → poll` move pollenwatch's `cleanroom/upgrade.py` already performs, just against the harness image instead of a bespoke cleanroom container.

### 3.3 What the image bakes for *each* consumer

The fixture already contains **both** `oriel_demo` and a `pollenwatch` config entry (`PollenWatch (48.123, 14.456)`, 24 entities). So a single image serves both:
- oriel reads pollenwatch's entities to render its pollen card (cross-project realism — a real win the hand-staged demo accidentally already has).
- pollenwatch gets a representative multi-area home to run its contract test against.

---

## 4. CI wiring

### 4.1 oriel-dashboard (the DEFERRED.md trigger, now fired)

DEFERRED.md's revisit condition (paraphrased): *if a non-production HA throwaway is stood up, e2e could move to CI against THAT, never against the production HA.* This harness **is** that throwaway, productized.

- Add a job to `.github/workflows/e2e.yml` (or a new `e2e-demo.yml`) triggered on `push`/`pull_request` (not just `workflow_dispatch`), `runs-on: ubuntu-latest`.
- Steps: build → run image → inject (§3.2) → `npx vitest run tests/e2e/strategy-api.test.ts` + `npx playwright test`.
- `HA_URL=http://localhost:8123`, `HA_TOKEN=<baked public token>` (a workflow env literal — **no GitHub secret**, see §6).
- The existing `workflow_dispatch` job against the production HA stays as a manual escape hatch; the **new** demo job is the one that runs automatically. The production HA is never touched by CI.

**Required vs informational:**
- **Phase in as informational** (`continue-on-error: true`, or a non-required check) for a stabilization window — browser e2e against a real HA is flake-prone (chunk preload races, settle timing), and oriel's specs already carry a benign-error filter list acknowledging this.
- **Promote to required** once it's demonstrably green across ~20+ runs. The vitest **API smoke** (WS-only, no browser) can be **required immediately** — it's fast and stable (it's just `lovelace/config` + shape checks).

### 4.2 pollenwatch

pollenwatch already has a *proven* model and should **not** be forced onto the harness. Two clean outcomes:
1. **Keep the prerelease gate as-is** (a self-hosted runner + a persistent throwaway, fires on `release: prereleased`). It tests the *deployed artifact* — a different question than the harness answers.
2. **Optionally add a fast PR-time contract check** that runs `test_real_ha_discovery.py` against the harness image on `ubuntu-latest`. This gives pollenwatch a pre-merge data-contract signal without waiting for a prerelease, and **removes the self-hosted-runner dependency** for that pre-merge layer.

### 4.3 The self-hosted-runner question — resolved

pollenwatch's gate needs a self-hosted runner **only because** its throwaway HA is a persistent, separately-managed host that the runner must be co-located with. **The harness eliminates that requirement:** because the demo HA arrives as a self-contained ephemeral image, the consumer's job boots it *inside the GitHub-hosted runner* and tears it down at job end. **Everything runs on `ubuntu-latest`. No self-hosted runner for harness-based tests.** (pollenwatch's *existing* prerelease gate keeps its self-hosted runner; that's a separate, deployed-artifact test we're not replacing.)

---

## 5. First assertions — the starter set

The harness's value is catching the class of bug this session found by eyeballing. Concrete starter set (oriel rendering unless noted), ordered by value:

1. **F4 regression — sparkline has a real box (THE flagship).** With the `custom:oriel-sparkline-card` (`use_apexcharts: true`) configured, the rendered chart is wrapped in an `ha-card` **and** its bounding box is nonzero (`getBoundingClientRect().width > 0 && .height > 0`). This is the exact failure (mounts at 0×0 / invisible, no `ha-card` wrapper) that was invisible to unit tests.
2. **F2 regression — no raw localization keys leak.** Walk the rendered dashboard's text; assert no visible text matches a localization-key pattern (e.g. `component.oriel.*` or the `foo.bar.baz` key shape). `localize()` returning the key-string on a miss means fallbacks never fire — only a rendered-DOM check catches it.
3. **Overview sections populate.** Persons (Alex, Sam), plants (Monstera/Fiddle-leaf/Snake/Pothos), routines (scenes + scripts — Movie night, Good morning, Start cleaning, Bedtime routine), and favorites (`light.bed_light`, `climate.heatpump`, `cover.living_room_window`) each render ≥1 item. Catches "section silently empty" regressions.
4. **Specialized views mount.** Each of the lights / covers / security / batteries views (oriel's `oriel-*-group-card` / view strategies) renders and lists a nonzero count. The fixture deliberately spreads batteries across critical/low/ok tiers (12%, 18%, 35%… 91%) so a battery-tiering regression shows up.
5. **Pollen card reflects pollenwatch states.** With `show_pollen: true`, the pollen surface renders the `pollenwatch.*` entities (cross-project assertion — proves the strategy reads a real integration's data, not a mock).
6. **Bubble tap-action emission.** With `use_bubble_drawers: true`, actionable tiles carry `tap_action = {action:'navigate', navigation_path:'#bubble-<entity>'}` and clicking sets `location.hash` — oriel already has `bubble-tile-tap-action.spec.ts`; the harness gives it a stable home to run.
7. **API contract — strategy mode.** (vitest, WS-only, fast/required) `lovelace/config` for the demo dashboard returns `strategy.type === 'custom:oriel'` with the expected toggle fields. This is oriel's existing `strategy-api.test.ts` verbatim, just pointed at the harness.
8. **No unfiltered console errors.** After network-settle, zero console errors beyond the documented benign-filter list. Catches runtime explosions on mount.

(For pollenwatch, the "first assertions" already exist and ported unchanged: `config_entries/get` has a `loaded` entry; `pollenwatch/config` returns `selected_species:[str…]` + valid `default_layout`; options round-trip.)

---

## 6. Token / secret model

### 6.1 Why the demo token resolves the concern that benched oriel e2e

DEFERRED.md's blocker, reasoning: oriel is a **public repo**, HA 2026.5.x has **no fine-grained tokens** so any `HA_TOKEN` is **full-admin scope**, and the only deployed HA is **the production deployment** whose admin token is reused across several local config files — so a leak forces rotating it everywhere it lives, a token that controls a real home.

**The demo token is the opposite on every axis:**
- It controls a **disposable, fake container** — fake lights, template sensors, no real devices, no network egress to anything that matters.
- It's **regenerable**: blow away the container, get a new one; the token guards nothing of value.
- A leak costs **nothing** — there's nothing to rotate, no real home, no shared blast radius.

Therefore the public-repo-admin-token objection **does not apply** to the harness token. This is precisely the calculus DEFERRED.md anticipated.

### 6.2 Provisioning in CI

Because the token is non-secret by construction, **bake a fixed, well-known long-lived token into the image's seed** (`.storage/auth`), document it loudly in the README as *intentionally public*, and reference it as a **plain workflow env literal** in consuming CI:

```yaml
env:
  HA_URL: http://localhost:8123
  HA_TOKEN: ha-demo-harness-public-token-not-a-secret   # documented public; guards only the throwaway
```

**Net result: oriel needs zero GitHub repository secrets to run e2e in CI.** That removes the entire "secret in a public repo's CI" attack surface that the original deferral was about — there is no secret. (If desired, the token can instead be minted at bring-up via the onboarding walk from pollenwatch's `lib/onboarding.py`; but baking a public one is simpler and sufficient.)

---

## 7. The harness repo itself

### 7.1 Name
**`ha-demo-harness`** (clear, neutral, not oriel- or pollenwatch-specific). Alternatives: `ha-test-harness`, `ha-fixture-ha`. Recommending `ha-demo-harness` — it's the *populated demo HA* productized.

### 7.2 Top-level layout
```
ha-demo-harness/
├── README.md                  # what it is, the public token, how to consume, version policy
├── DESIGN.md                  # this doc
├── docker-compose.yml         # one-command local bring-up (port 8126:8123, like today)
├── Dockerfile                 # FROM ghcr.io/home-assistant/home-assistant@sha256:<pinned>
├── seed/                      # baked into image, copied to /config on boot
│   ├── configuration.yaml
│   ├── custom_components/
│   │   ├── oriel_demo/        # existing shim, lifted verbatim
│   │   └── harness_seed/      # NEW: dynamic states + synthetic recorder history
│   └── .storage/             # committed JSON seeds (registries, lovelace, auth+token)
├── lib/                       # shared HA client (from pollenwatch cleanroom/lib)
│   ├── ha_ws.py  ha_api.py  settle.py
├── scripts/
│   ├── wait-for-ha.sh         # consumers copy/curl this
│   └── inject-frontend.sh     # docker cp dist → www/community + strip .gz/.br
├── .github/workflows/
│   ├── build-publish.yml      # build image, push ghcr.io/.../ha-demo-harness:vX on tag
│   └── self-test.yml          # boot image, assert fixture invariants (178 entities, 7 areas…)
└── CHANGELOG.md
```

### 7.3 Versioning / release so consumers can pin
- Git tags `vMAJOR.MINOR.PATCH`; each tag triggers `build-publish.yml` → `ghcr.io/thedave94/ha-demo-harness:vX.Y.Z` **and** `:latest`.
- **Pin two things together**: the harness version *and* the HA base-image digest (in the `Dockerfile FROM`). Bump MINOR when the HA version moves, PATCH for fixture tweaks, MAJOR for a breaking fixture/contract change (entity renamed/removed).
- Consumers pin a tag (`:v1.2.0`), never `:latest`, so a fixture change can't silently break a consumer's CI. The harness's own `self-test.yml` asserts fixture invariants (entity count by platform, area names, the strategy dashboard exists) so a regression in the *fixture itself* fails in the harness repo, not downstream.

---

## 8. Effort / phasing (honest, multi-session)

### Session 1 — Codify the demo (the foundation; highest risk)
**Goal:** `docker compose up` reproduces today's `:8126` demo byte-faithfully, from version-controlled source, with nothing hand-staged.
- Scaffold repo + `Dockerfile` (pin HA digest) + `docker-compose.yml`.
- Lift `configuration.yaml` + `custom_components/oriel_demo/` verbatim into `seed/`.
- Extract the JSON `.storage` registry/lovelace/auth files from the live container, sanitize, commit as seeds; bake the public token.
- Write `harness_seed` component for **dynamic states**.
- **Hard part:** reproduce **synthetic recorder history** programmatically (deterministic generator for `states`/`statistics` rows) instead of the committed `.db`. Validate ApexCharts actually plots from it.
- **Acceptance:** diff the fresh container's entity registry / area list / dashboard config against the live `:8126` and confirm parity; sparkline has data.

### Session 2 — Publish + wire oriel (first green CI)
**Goal:** oriel's e2e runs against the harness image in CI on `ubuntu-latest`, informational.
- `build-publish.yml` → first GHCR image; `self-test.yml` fixture invariants.
- `scripts/wait-for-ha.sh` + `inject-frontend.sh`.
- Add oriel's demo e2e job (build → run image → inject dist → vitest API + Playwright).
- Land **F4** + **API-contract** assertions green first; mark browser job informational, API job required.
- **Hard part:** the **build-injection handoff** (docker cp timing, `.gz`/`.br` stripping, ensuring the browser loads the *fresh* bundle) and first-run flake.

### Session 3 — Expand, harden, promote, pollenwatch
**Goal:** full starter assertion set; promote to required; optional pollenwatch consumption.
- Implement remaining assertions (#2,3,4,5,6,8 from §5).
- Stabilize flake; promote oriel browser e2e to a required check after a green streak.
- Add pollenwatch's optional PR-time contract check against the image (reuses `test_real_ha_discovery.py`); leave its prerelease gate alone.
- README/CHANGELOG/version-pin docs; first `v1.0.0`.

### Risk register (the hard parts, flagged)
| Risk | Why hard | Mitigation |
|---|---|---|
| **Reproducible recorder history** | Binary `.db` churns on HA schema bumps; programmatic stats injection touches HA internals | Generator targets the public recorder API where possible; pin HA digest so schema is fixed within a harness version; treat as Session-1 spike. |
| **Deterministic `.storage`/auth seeding** | uuids/user_id/token must be stable for entity_ids not to drift | Fixed values committed (safe for a fake container); `self-test.yml` guards drift. |
| **Build-injection handoff** | timing + stale compressed assets + fresh-bundle loading | `inject-frontend.sh` strips `.gz`/`.br`; settle-poll before tests; documented gotcha. |
| **Cross-repo CI for a public repo** | normally the scary part | Largely dissolved: public image + public consumer = anonymous pull; no secret needed. |
| **Self-hosted runner** | pollenwatch needs one today | Dissolved for harness: ephemeral image runs on `ubuntu-latest`. |
| **Browser-e2e flake** | real HA + headless chromium | Phase in informational; reuse oriel's benign-error filters; required only after green streak. |

---

## 9. Open questions for David
1. **Recorder history fidelity** — is a small deterministic synthetic series (a handful of sensors, 24–48h) enough for the sparkline/ApexCharts assertions, or do you want the fuller multi-sensor history the current `.db` has?
2. **GHCR namespace** — publish under your user (`ghcr.io/thedave94/ha-demo-harness`) or a neutral org?
3. **pollenwatch scope** — add the optional PR-time harness contract check now, or defer and keep pollenwatch entirely on its existing gate for v1?
4. **Token** — bake a fixed public token (simplest), or mint-at-bring-up via the onboarding walk (more "realistic", more moving parts)? Recommending baked.
5. **HA version cadence** — pin to `stable` digest and bump deliberately (recommended), or track a specific HA release line?
