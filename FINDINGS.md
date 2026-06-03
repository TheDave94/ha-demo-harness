# Session 1 — Findings

Build of the reproducible, one-command bring-up that reproduces the hand-staged
reference demo (`:8126`, container `throwaway-oriel-demo`) from version control.
Goal: parity, local reproduction only. Reference left running and untouched.

## Outcome: PARITY ACHIEVED

Fresh harness container booted on `:8127` and diffed against live `:8126`:

| Check | Reference :8126 | Harness :8127 | Result |
|---|---|---|---|
| Total registry entities | 178 | 178 | ✅ |
| Platform `demo` | 69 | 69 | ✅ |
| Platform `template` | 40 | 40 | ✅ |
| Platform `pollenwatch` | 24 | 24 | ✅ |
| Platform `oriel_demo` | 12 | 12 | ✅ |
| (sun 9, input_boolean 8, backup 5, input_select 4, person 2, script 2, met 1, google_translate 1, shopping_list 1) | match | match | ✅ |
| Area names (7) | Bathroom, Bedroom, Garage, Hallway, Kitchen, Living Room, Office | identical | ✅ |
| Strategy dashboard `/oriel-demo` | `custom:oriel` | `custom:oriel` | ✅ |
| All 7 strategy toggles (show_pollen, plants, persons, routines, bubble_drawers, house_mode_entity, favorite_entities) | — | identical | ✅ |
| Pollen consensus spread | grass:high, alder:low, birch:low, ragweed:mixed, mugwort:none, olive:unknown | identical | ✅ |
| Person states | Alex home, Sam not_home | identical | ✅ |
| Plant states (state, moisture) | monstera ok/62, fiddle_leaf problem/31, snake ok/45, pothos ok/54 | identical | ✅ |
| Sparkline history (`sensor.living_room_temperature`) | 58 pts (24h window) | 42 pts (24h window) | ✅ both plottable |
| Frontend resources served (oriel.js, bubble-card.js, apexcharts-card.js) | — | all HTTP 200 | ✅ |

> `oriel_demo` = 12 (8 lights + 4 plants), not the "16-ish" estimated in the
> Session-1 prompt. Both sides agree at 12; the estimate was approximate.

> Sparkline point counts differ only because the reference has ~24h of live +
> synthetic points accumulated, while the harness injects a clean 48h curve at
> 30-min cadence (the default `/history/period` returns the last 24h ≈ 42–48
> points). Both are non-empty and plot correctly. The harness history is
> **now-relative** (see below), so it never ages out.

## Recorder-history path taken: PROGRAMMATIC (now-relative), not a static .db

I took the **programmatic** path from the design's risk register, **not** the
committed-`.db` escape hatch. Reasoning:

- A committed static `.db` carries **fixed past timestamps**. A "last 24h"
  ApexCharts sparkline would show **empty** whenever the container is booted more
  than 24h after the `.db` was generated — which defeats a reusable harness meant
  to boot fresh at any time. Now-relative generation (history spanning
  `now-48h … now`, computed at each boot) is the only durable option.

`harness_seed` injects 48h of deterministic half-hourly history (~97 points ×
10 temp/humidity sensors) on `EVENT_HOMEASSISTANT_STARTED`.

### The one real hazard hit + fixed: recorder `states_meta` race

First implementation inserted `states_meta` rows directly. This **corrupted the
recorder's cached `StatesMetaManager`** (concurrent `UNIQUE constraint failed:
states_meta.entity_id`), which broke a recorder transaction and left the DB with
only ~11 `states_meta` rows (normal recording disrupted).

**Fix:** never write `states_meta`. The per-area template sensors are recorded
by the recorder at boot, so `harness_seed` now polls (≤25s) until the recorder
**owns** each target's metadata, then references that `metadata_id` read-only and
inserts only `state_attributes` + `states` rows (neither has a conflicting unique
constraint). After the fix: **0 errors, 210 `states_meta` rows (healthy normal
recording), 95 backdated sparkline rows.**

This is the maintainable target. If a future HA bump makes the in-process
injection fight the recorder again, the documented fallback (commit a generated
`.db` + rebase its timestamps to now at pre-start) remains available — but it was
not needed.

## 2026.5 fixture lock — template-entity migration scope (REPORT ONLY, not fixed)

Per the Session-1 addendum: HA **2026.6 removes legacy `template:` entities**.
The lifted `seed/configuration.yaml` defines legacy template entities in its
`template:` block. **These are NOT migrated this session** (parity-on-2026.5.4 is
the goal); the pinned digest keeps them working. Documented loudly in `README.md`
and the `Dockerfile`. Migration scope, to be done before any HA bump past 2026.5:

**Legacy `template:` entities that need migration** (registry `platform=template`,
40 total):

- **`template:` → `sensor:` block (16 defined in configuration.yaml):**
  - 5 area temperatures (`sensor.<area>_temperature`) + 5 area humidities
    (`sensor.<area>_humidity`)
  - 6 batteries (`sensor.front_door_sensor_battery`, `..._backyard_motion_*`,
    `..._bedroom_window_*`, `..._kitchen_smoke_*`, `..._hallway_thermostat_*`,
    `..._garage_door_*`)
- **`template:` → `binary_sensor:` block (10 defined):** front/back door, 4
  windows, garage door, 3 motion sensors.

**Migration options (pick during the bump):** convert the `template:` YAML to the
modern template-entity format, OR fold these synthetic sensors into the
`oriel_demo` shim as real `SensorEntity`/`BinarySensorEntity` Python (the shim
already owns lights + plants this way, so this is the cleaner long-term home and
removes the YAML-template dependency entirely).

## Things that did NOT reproduce cleanly (faithfully carried, noted)

1. **Orphan plant template sensors.** The seeded `core.entity_registry` contains
   8 plant template sensors (`sensor.<plant>_moisture` / `_temperature`, e.g.
   `sensor.monstera_moisture`) with **no backing definition** in the current
   `configuration.yaml` — leftovers from an earlier demo iteration. They are
   `unavailable` on **both** `:8126` and `:8127` (verified), so parity holds, but
   they are dead registry entries. (Note: oriel's plant *section* uses the
   `plant.*` domain entities from the `oriel_demo` shim, which are fine — these
   orphan `sensor.*` ones are unrelated cruft.) Cleanup candidate for a later
   pass; harmless for now.

2. **Network-dependent integrations log first-boot errors.** `met` (weather),
   `radio_browser`, `go2rtc`, and `pollenwatch`'s source fetches emit fetch
   errors/warnings on boot. Expected and benign — the demo's *staged* values
   (which `harness_seed` overwrites) are what tests assert against. Flagged for
   Session 2/3: for fully network-independent determinism, the pollenwatch
   coordinator's live fetch should eventually be neutralized so it can't briefly
   show real (non-deterministic) source values before `harness_seed` re-stages.
   Currently mitigated by staging on `homeassistant_started` (after the
   coordinator's first refresh) + a 5s re-assert; the coordinator's update
   interval is ≥1h, so staged values hold for any realistic test run.

3. **`docker compose` not installed on the build box.** Neither the compose
   plugin nor `docker-compose` is present here, so verification used plain
   `docker build` + `docker run`. The committed `docker-compose.yml` is correct
   and is the documented bring-up; it just couldn't be exercised on this box.

## Token model — verified

The deterministic JWT algorithm in `scripts/make_seed_auth.py` was validated
against the **live** `:8126` (reproduced its existing token byte-for-byte and got
HTTP 200) before baking our own. The baked public token authenticates against the
fresh harness (HTTP 200, all WS/REST queries succeed). No GitHub secret is needed
to use it — that is the resolution of the public-repo-admin-token concern.

## Not done (correctly deferred to Sessions 2–3)

- No GHCR publish, no CI wiring, no Playwright/WS assertions.
- `lib/` (shared HA client, to be ported from `pollenwatch/cleanroom/lib`) and
  `scripts/inject-frontend.sh` are stubs — Session 2.
- `.github/workflows/` are placeholders — Session 2.
- Template-entity migration — deferred to the HA bump (scoped above).
- Orphan-sensor cleanup — optional later pass.

---

# Demo-exercise findings — oriel bugs surfaced by populating the demo (provenance)

_Dated 2026-06-02. Frozen history — this is the record of what the populated demo
caught, not a backlog._

**This is the harness's proof of value.** Standing up a fully-populated demo HA
and exercising oriel's *entire* render surface (overview sections, the
specialized views, the sparkline, bubble drawers, the pollen card, i18n
fallbacks) surfaced **six findings (F1–F6)** that unit tests and a sparse fixture
had not. Four are real oriel bugs (one user-facing-invisible), one is an
enhancement, one turned out to be a test-method artifact. Catching this class of
defect *automatically* is exactly what Sessions 2–3 wire into CI.

**Reproduction context:** oriel built and deployed into the demo's
`www/community/oriel-dashboard/`, strategy `custom:oriel` on the `/oriel-demo`
dashboard with `show_pollen`, `show_plants_section`, `show_persons_section`,
`show_routines_section`, `use_bubble_drawers: true`, a `custom:oriel-sparkline-card`
(`use_apexcharts: true`), and `favorite_entities` set. Surfaced by rendering +
DOM-walking the live dashboard, not by reading code.

> **Actionable backlog lives in oriel, not here.** The real fixes (F1–F4, plus
> F6 as an enhancement) are tracked as a live queue in
> `oriel-dashboard/KNOWN_ISSUES.md` — that's where the work happens. This section
> is the immutable provenance record of *what the demo caught and when*.

| # | Severity | Area / location | What the demo surfaced |
|---|---|---|---|
| **F1** | minor (i18n) | `src/translations/en.json` | `sections.routines` key is **missing** — the Routines section header renders the literal key string instead of "Routines". |
| **F2** | medium | `src/utils/localize.ts` (e.g. callsite `OverviewViewStrategy.ts:391`) | `localize()` returns the **key-string on a miss** (truthy), so `localize(...) || 'fallback'` never fires — defensive fallbacks are silently bypassed for *any* missing key. Likely affects every such callsite. F1 only became *visible* because F2 swallows the fallback. |
| **F3** | medium | `src/types/strategy.ts` (CustomCard schema) | The custom-card field is `parsed_config`, not the natural `card`/`config`. YAML-direct users hand-writing the strategy use `card:` and the card **silently doesn't render** — editor-internal terminology leaking into the user-facing contract. |
| **F4** | medium, **USER-FACING (flagship)** | `src/cards/SparklineCard.ts` (apex render branch) | The ApexCharts render path was **invisible** (`getBoundingClientRect()` **0×0**) for any user enabling `use_apexcharts`. **Root cause (corrected — see note below):** oriel bound `.config=${apexConfig}` as a lit *property*, but `apexcharts-card` has **no `set config()` accessor** — it configures only via its **`setConfig()` method**. The bind was a silent no-op, so the delegate was never configured and rendered an empty 0×0 shadow. The fix (oriel PR, [#114]) configures it imperatively via `setConfig()`. This is the flagship find: the demo + an eyeball caught what no unit test could. |
| **F5** | **NOT a bug** (test-method lesson) | — | An early DOM walk reported "0 bubble-card elements emitted"; re-testing with a clean context found **all 35**. The walker had bailed at a shadow-DOM boundary — a test-method artifact, not an oriel defect. **Lesson: verify the walker before concluding non-emission.** Recorded so the false alarm isn't rediscovered as a "bug." |
| **F6** | informational / enhancement | bubble-drawer emission (`use_bubble_drawers: true`) | oriel emits pop-ups for **all** actionable entities (35 in the demo) with no knob to scope (e.g. favorites-only) — heavy DOM. Worth a scoping option or at least a doc note. Not a defect. |

**Harness coverage:** F4 → assertion #1 ("sparkline renders inside `ha-card`
with nonzero bounding box" — still valid: `apexcharts-card` renders its *own*
`ha-card` once configured); F2 → assertion #2 ("no raw localization keys in
rendered DOM"). Both now live as e2e specs in the oriel PR ([#114]:
`sparkline-apex.spec.ts`, `no-raw-localization-keys.spec.ts`), plus a required
unit gate `SparklineCard.test.ts`. F5's lesson is baked into how the
bubble-emission assertion's DOM walker must be written.

> **F4 root-cause correction (2026-06-03).** The original F4 entry above
> attributed the 0×0 chart to a **missing `<ha-card>` wrapper**. That was
> **wrong**, established empirically while scoping the fix: wrapping the bare
> `<apexcharts-card>` in `ha-card` does *not* fix it — the delegate stays 0×0
> because it is never **configured**. `apexcharts-card` reads config only via
> `setConfig()` (no `set config()` accessor), so oriel's `.config=` property
> bind was a silent no-op. The real fix is imperative `setConfig()`; no wrapper
> is needed (apexcharts-card mounts its own `ha-card`). Lesson: confirm the
> failure mode by building the candidate fix before recording a root cause.

---

# Session 1.5 — hygiene pass (2026-06-02)

Three pre-publish cleanups so the S2 image consumers pin to is clean. All verified
on the **compose-managed** container on `:8127`.

## ⚠️ NEW PARITY BASELINE: 164 entities (was 178)

After item 1, the harness registry is **164 entities** (`platform=template` = 26).
This is the **new expected baseline** — future parity checks should expect 164, not
178. The 14-entity drop is the orphan-sensor cleanup below, **not** a regression.
All other platforms are unchanged vs `:8126` (demo 69, pollenwatch 24, oriel_demo
12, sun 9, input_boolean 8, backup 5, input_select 4, person 2, script 2, met 1,
google_translate 1, shopping_list 1).

> The `:8126` reference is **no longer a valid baseline for two surfaces**:
> (a) entity count — it still carries the 14 orphans (178); (b) pollen consensus —
> its live coordinator has drifted the hand-staged spread (see item 2). For those
> two, the **harness is now the source of truth**; `:8126` remains the baseline for
> everything else (areas, strategy, persons, plants, structure).

## Item 1 — orphan template sensors removed (14, not 8)

S1 reported "8 orphan plant template sensors"; the real count is **14** — two
leftover generations:
- `demo_plant_*` set (6): `sensor.{monstera,fiddle_leaf,snake_plant}_{moisture,temperature}`
- `pm_*`/`pt_*` set (8): all four plants `{…}_{moisture,temperature}`, with `_2`
  object-id suffixes where they collided with the first set.

All 14 verified fully orphaned before removal: `platform=template`, **no**
`configuration.yaml` backing, `device_id=None`, `config_entry_id=None`, **all
`unavailable`** on both `:8126`/`:8127`, **zero references** anywhere outside the
registry. Removed from `seed/.storage/core.entity_registry`. `40 template − 14 =
26`, matching exactly the configuration.yaml-backed template entities (16 sensors +
10 binary_sensors). No live entity affected (all were dead).

## Item 2 — pollen states made deterministic (coordinators neutralized)

**Problem:** the staged pollen spread was being *out-raced*, not owned. The
per-source coordinators (≤24h interval) and the **hardcoded-1h analytics/consensus
coordinator** would re-derive on their next tick and clobber the staged consensus —
the very states the pollen-card assertion (#5) will read. Proof it's real: by the
time of this pass, `:8126`'s live coordinator had **already drifted** the spread
(grass `high→low`, alder/birch/ragweed/olive `→none`).

**Fix:** `harness_seed` now calls the documented public `await
coordinator.async_shutdown()` on every pollenwatch coordinator (reached via
`entry.runtime_data.coordinators` + `.analytics`) **before** staging — a hard stop
(cancels the refresh timer + debouncer, ignores future runs), not an out-race. The
old 5s re-assert heuristic was removed. Rejected alternatives: config (source caps
at 24h, analytics interval hardcoded — can't); forking the vendored source
(invasive); seeding `ConsensusResult`/`AnalyticsData` natively (deepest coupling).

**Verified:** harness_seed logs `neutralized 2 pollen coordinators:
pollenwatch_open_meteo(shutdown=True,timer=cancelled);
pollenwatch_analytics(shutdown=True,timer=cancelled)` — the `timer=cancelled` flag
is direct proof the periodic refresh can't fire. The staged spread (grass:high,
alder/birch:low, ragweed:mixed, mugwort:none, olive:unknown) held exactly across a
re-read window. (The 1h analytics interval can't be fast-forwarded in-session, but a
cancelled timer cannot fire by construction — the mechanism, not the clock, is the
guarantee.)

**Honest residuals:** (a) staged states are written to the state machine via
`async_set`; a forced `homeassistant.update_entity` on a consensus sensor would
re-render it from the (now-frozen, boot-computed) coordinator data and could revert
— but CI assertions only *read* states, never force-update. (b) Raw `open_meteo_*`
source sensors keep their boot-fetch values (network-dependent), frozen
post-shutdown; assertion #5 reads *consensus* (staged → deterministic), not raw
source. (c) pollenwatch's `open_meteo` first-refresh is **blocking + networked at
load** — a fully-offline CI wouldn't load pollenwatch at all (its 24 entities would
be absent). Realistic CI (ubuntu-latest) has network so the boot fetch succeeds;
offline support would need a source stub (deferred, larger change).

## Item 3 — docker-compose.yml exercised (no longer un-validated)

The compose plugin was installed on the box (`~/.docker/cli-plugins`, v5.1.4) and
the file was **actually run**: `docker compose config` validates (rc 0, port
`8127:8123` correct), and `docker compose up -d` boots the populated demo on `:8127`
— harness_seed runs (neutralize + stage + 970 history rows), 164 entities, pollen
at the staged spread. The S1 "un-exercised" flag is cleared.

---

# Session S3-prep — presence fixture gap closed (2026-06-02)

oriel's `container-queries.spec` needs an `oriel-zone-presence-card` on the
overview; the demo rendered none, so PR #113's `demo-browser` failed on that one
spec. **Closed it** — config-only, **zero new entities, baseline still 164.**

**The mechanism (not the one first assumed).** The S3-prep investigation expected
a top-level `presence_zones` list to render the card. In practice it does **not**:
oriel builds the `presence` section into its `sectionMap` but **never places it in
the overview section order** — `DEFAULT_SECTIONS_ORDER` is just
`[overview, custom_cards, areas, weather, energy, plants]`, and
`normalizeSectionsOrder` only auto-appends those defaults; `presence`/`persons`/
`agenda`/`todos`/`vacuums`/`maintenance` are built but unreachable on the
overview, and even an explicit `sections_order: [...,presence]` entry is dropped
(it's not in `BUILTIN_SECTION_KEYS`). **This looks like an oriel quirk/bug worth a
separate upstream issue** — flagged, not fixed here (harness-only session).

**What works:** the per-area pin path (also identified in the investigation),
which routes through the **favorites grid** inside the always-eager `overview`
section, bypassing the broken ordering. Added to the `oriel_demo` strategy config:
```jsonc
"areas_options": { "living_room": {
  "pin_zone_presence_to_favorites": true,
  "presence_entities": ["binary_sensor.living_room_motion",
                        "binary_sensor.hallway_motion"] } }
```
Reuses 2 existing motion sensors. **Verified (baked AND fresh-injected oriel):**
1 `oriel-zone-presence-card` mounts on `/oriel-demo/0`, `--oriel-icon-wrap`
resolves to `36px` (exactly what container-queries.spec asserts). `assert_fixture.py`
gains a guard asserting an area pins the card (so dropping it fails the harness's
own self-test). Published as **v0.1.2**.

# Session S-F3-prep — `card:`-alias custom card staged ahead of oriel's F3 fix (2026-06-03)

F3 (see the Demo-exercise findings table above): oriel renders a custom-card
entry **only** when it carries `parsed_config` (the editor's serialized output).
A YAML-direct author writing the natural `card:`/`config:` gets a **silent
non-render** — at render time oriel reads `parsed_config` and nothing else (no
runtime YAML parse; `yaml.load` lives only in oriel's editor chunk). oriel's
upcoming fix will accept `card:`/`config:` as render-time aliases (normalize
`card`→`parsed_config`).

**Staged the e2e surface here, prep-then-consumer (same pattern as the F4 /
presence prep).** Added a **second** `custom_cards` entry to the `oriel_demo`
strategy config (`seed/.storage/lovelace.oriel_demo`) written in the **`card:`
alias form** (NOT `parsed_config`):
```jsonc
{ "target_section": "custom_cards",
  "title": "F3 alias check",
  "card": { "type": "markdown", "content": "F3 custom-card alias OK" } }
```
A built-in HA `markdown` card — **no custom element, zero new entities, baseline
unchanged at 164.** The distinctive content string (`F3 custom-card alias OK`) is
the marker the future oriel F3 e2e will assert on.

**Pre-fix behavior (expected & correct): this entry renders NOTHING today.** With
the currently-published oriel (no F3 fix), `card:` is ignored and the card is
silently dropped. The fixture is deliberately staged **ahead** of the fix — it
only renders once BOTH oriel's F3 alias fix AND this fixture are in place. That
is the point of prep-then-consumer.

**Self-test is unaffected.** `assert_fixture.py` checks entity counts (164),
areas, strategy toggles, pollen spread, and sparkline history — it does **not**
inspect `custom_cards` structure or require any custom card to render. The new
entry is config (not an entity) and need only be *present and valid*, not
rendering-yet. Verified: JSON valid, baseline still 164. Published as **v0.1.3**.

# Session S-F6-prep — `no_dboard`-excluded actionable entity staged ahead of oriel's F6/Rung-0 fix (2026-06-03)

F6/Rung-0 (the bubble-drawer enhancement, see the oriel-side investigation):
oriel's `collectBubbleCandidates` iterates **raw `hass.states`** and filters
**only by domain** (`light`/`climate`/`cover`/`fan`/`media_player`). It does
**not** route through `Registry.isEntityExcluded()`, so an entity the user has
excluded (the `no_dboard` label, `hidden_by`, per-area hidden, config/diagnostic)
**still gets a bubble drawer** — and the docstring even falsely claims it skips
registry-hidden entities. oriel's Rung-0 fix will make bubble emission honor the
exclusion pipeline.

**Staged the e2e surface here (prep-then-consumer, same as F3 / F4 / presence).**
Marked ONE existing actionable entity — **`light.kitchen_lights`** (demo platform,
Kitchen area, a `light`, so it currently WOULD get a drawer at hash
`#bubble-light-kitchen-lights`) — with oriel's documented exclusion signal, the
**`no_dboard` label**:
- New file `seed/.storage/core.label_registry` defining label_id `no_dboard`.
- `light.kitchen_lights`'s entity-registry `labels` set to `["no_dboard"]` (a
  surgical one-line edit preserving HA's compact storage format — the entity is
  **not deleted**, just labelled, so baseline stays 164).

Chose `no_dboard` over `hidden_by` because it's oriel's canonical, user-facing
"hide from dashboard" mechanism and the first check in `isEntityExcluded()` — the
most representative Rung-0 test. (`hidden_by` was the documented fallback.)

**Pre-fix behaviour (expected & correct): `light.kitchen_lights` STILL gets a
drawer today.** With the currently-published oriel, the domain-only filter ignores
the label, so the excluded entity is wrongly emitted. **Post-fix (Rung-0): it must
get NO drawer.** The fixture stages the excluded entity; the assertion **flips
with the oriel fix** — meaningful only once oriel ships Rung-0.

**Self-test unaffected.** `assert_fixture.py` checks entity counts (164),
platforms, areas, toggles, pollen, sparkline — none inspect labels/`hidden_by`.
Labelling an entity changes none of them. **Verified on a booted container:** the
`no_dboard` label **round-trips through HA** (`config/label_registry/list` →
`no_dboard`; `config/entity_registry/list` → `light.kitchen_lights.labels =
["no_dboard"]`, and it's the only labelled entity); `light.kitchen_lights` state
is still live (`on`, so it currently still emits a drawer); all fixture invariants
hold at baseline 164. Published as **v0.1.4**.
