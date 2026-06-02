"""harness_seed — deterministic staging of the demo's DYNAMIC state.

Everything that the reference demo on :8126 had to be poked in by hand after boot
(WS-API / force_state chains) lives here instead, as version-controlled,
self-documenting code. Two jobs, both fired once on EVENT_HOMEASSISTANT_STARTED
(i.e. after every integration — including pollenwatch — has done its first
refresh, so our values win the race):

  1. STATE STAGING — overwrite a fixed table of pollen-consensus + person states
     with the demo spread (grass:high, alder/birch:low, ragweed:mixed,
     mugwort:none, olive:unknown; Alex home, Sam away).

  2. RECORDER HISTORY — inject NOW-RELATIVE synthetic history for the per-area
     temp/humidity template sensors (incl. sensor.living_room_temperature, the
     oriel sparkline target) so ApexCharts has 48h of curve to plot the instant
     the container is ready. Now-relative (not a committed static .db) so a
     "last 24h" sparkline never ages out no matter when the container boots.

Plant moisture/temperature is NOT staged here — the oriel_demo shim already owns
plant.* entities and sets those attributes on setup.
"""
from __future__ import annotations

import logging
import math
import time

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

DOMAIN = "harness_seed"

# --- 1. dynamic state spread (entity_id -> state) -----------------------------
# Only the STATE is forced; existing attributes (friendly_name, etc.) are merged
# so we don't clobber names the owning integration set.
POLLEN_CONSENSUS = {
    "sensor.pollenwatch_analytics_grass_consensus": "high",
    "sensor.pollenwatch_analytics_alder_consensus": "low",
    "sensor.pollenwatch_analytics_birch_consensus": "low",
    "sensor.pollenwatch_analytics_ragweed_consensus": "mixed",
    "sensor.pollenwatch_analytics_mugwort_consensus": "none",
    "sensor.pollenwatch_analytics_olive_consensus": "unknown",
}
PERSON_STATES = {
    "person.demo_user": "home",      # Alex
    "person.sam": "not_home",        # Sam
}
STAGED_STATES = {**POLLEN_CONSENSUS, **PERSON_STATES}

# --- 2. recorder-history targets ----------------------------------------------
# (entity_id, attributes, base_value, amplitude). 48h of half-hourly points on a
# deterministic 24h sine around base. Attributes match the template sensors'
# shared_attrs exactly (device_class + unit + friendly_name, no state_class).
_AREAS = [
    ("living_room", "Living room", 21.8, 47),
    ("kitchen", "Kitchen", 23.1, 52),
    ("bedroom", "Bedroom", 19.4, 43),
    ("bathroom", "Bathroom", 22.7, 68),
    ("office", "Office", 20.9, 41),
]


def _history_targets() -> list[tuple[str, dict, float, float]]:
    targets: list[tuple[str, dict, float, float]] = []
    for slug, label, temp, hum in _AREAS:
        targets.append(
            (
                f"sensor.{slug}_temperature",
                {
                    "device_class": "temperature",
                    "unit_of_measurement": "°C",
                    "friendly_name": f"{label} temperature",
                },
                float(temp),
                1.5,
            )
        )
        targets.append(
            (
                f"sensor.{slug}_humidity",
                {
                    "device_class": "humidity",
                    "unit_of_measurement": "%",
                    "friendly_name": f"{label} humidity",
                },
                float(hum),
                5.0,
            )
        )
    return targets


HISTORY_SPAN_S = 48 * 3600
HISTORY_STEP_S = 1800
SPARKLINE_SENTINEL = "sensor.living_room_temperature"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # Serve oriel's files at the /hacsfiles/ route too, so its async chunks load.
    # oriel's webpack hardcodes publicPath '/hacsfiles/oriel-dashboard/', so
    # oriel.js fetches its chunks (oriel-core/views/editor/lit/422) from there.
    # In a real install HACS provides that route; the harness has no HACS, so
    # without this the chunks 404 and no cards mount. We map the SAME baked dir
    # to both /local/community/oriel-dashboard/ (HA's default www route, kept for
    # the other cards) and /hacsfiles/oriel-dashboard/ (what oriel needs), so
    # oriel runs tested-as-shipped with its publicPath unchanged.
    from homeassistant.components.http import StaticPathConfig

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                "/hacsfiles/oriel-dashboard",
                hass.config.path("www/community/oriel-dashboard"),
                # No long-lived caching: a renderer test docker-cp's a fresh build
                # in, and the stable-named oriel.js must not be served stale.
                False,
            )
        ]
    )
    _LOGGER.info(
        "harness_seed: serving oriel at /hacsfiles/oriel-dashboard "
        "(chunk route for publicPath)"
    )

    async def _on_started(_event: Event) -> None:
        # Stop pollenwatch's coordinators FIRST so nothing re-derives the pollen
        # states after we stage them (replaces the old out-race-it heuristic with
        # a hard neutralization — see _neutralize_pollen_coordinators).
        await _neutralize_pollen_coordinators(hass)
        _stage_states(hass)
        try:
            await _inject_recorder_history(hass)
        except Exception:  # pragma: no cover - escape hatch logs, never crashes boot
            _LOGGER.exception("harness_seed: recorder history injection failed")

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)
    return True


async def _neutralize_pollen_coordinators(hass: HomeAssistant) -> None:
    """Shut down pollenwatch's coordinators so the staged pollen states are
    authoritative and STABLE regardless of timing or network.

    Without this it's a race: the per-source coordinators (≤24h interval) and
    especially the **hardcoded-1h analytics/consensus coordinator** would re-fetch
    and re-derive on their next tick and clobber the staged consensus states that
    the pollen-card assertion depends on. `async_shutdown()` is the documented HA
    API that cancels the scheduled refresh + debouncer and makes the coordinator
    ignore all future runs — a hard stop, not an out-race. We log each
    coordinator's post-shutdown flags as proof the refresh timer is cancelled.

    Config can't do this (source interval caps at 24h; the analytics interval is
    hardcoded), and we deliberately don't fork the vendored pollenwatch source.
    """
    entries = hass.config_entries.async_entries("pollenwatch")
    if not entries:
        _LOGGER.warning("harness_seed: no pollenwatch entry found; nothing to neutralize")
        return

    coords = []
    for entry in entries:
        data = getattr(entry, "runtime_data", None)
        if data is None:
            continue
        coords.extend(getattr(data, "coordinators", {}).values())
        analytics = getattr(data, "analytics", None)
        if analytics is not None:
            coords.append(analytics)

    for c in coords:
        try:
            await c.async_shutdown()
        except Exception:  # pragma: no cover - never let this crash boot
            _LOGGER.exception(
                "harness_seed: failed to shut down coordinator %s",
                getattr(c, "name", c),
            )

    proof = "; ".join(
        f"{getattr(c, 'name', '?')}"
        f"(shutdown={getattr(c, '_shutdown_requested', None)},"
        f"timer={'cancelled' if getattr(c, '_unsub_refresh', None) is None else 'LIVE'})"
        for c in coords
    )
    _LOGGER.info(
        "harness_seed: neutralized %d pollen coordinators: %s", len(coords), proof
    )


def _stage_states(hass: HomeAssistant) -> None:
    staged = 0
    for entity_id, state in STAGED_STATES.items():
        cur = hass.states.get(entity_id)
        attrs = dict(cur.attributes) if cur else {}
        hass.states.async_set(entity_id, state, attrs)
        staged += 1
    _LOGGER.info("harness_seed: staged %d dynamic states", staged)


async def _inject_recorder_history(hass: HomeAssistant) -> None:
    from homeassistant.components.recorder import get_instance

    instance = get_instance(hass)
    await instance.async_add_executor_job(_write_history_blocking, hass)


def _write_history_blocking(hass: HomeAssistant) -> None:
    """Bulk-insert backdated state rows. Runs on the recorder executor.

    CRITICAL: we never INSERT into states_meta ourselves — the recorder owns that
    table via a cached StatesMetaManager, and writing it behind the recorder's
    back corrupts its cache and breaks recording (UNIQUE constraint races). The
    per-area template sensors are recorded by the recorder at boot, so we poll
    until the recorder has created their metadata, then reference the
    recorder-owned metadata_id read-only and insert only state_attributes + states
    rows (neither of which has a conflicting unique constraint).
    """
    from sqlalchemy.exc import OperationalError

    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.db_schema import (
        StateAttributes,
        States,
        StatesMeta,
    )
    from homeassistant.components.recorder.util import session_scope
    from homeassistant.helpers.json import json_bytes

    instance = get_instance(hass)
    now = time.time()
    start = now - HISTORY_SPAN_S
    n_points = HISTORY_SPAN_S // HISTORY_STEP_S
    targets = _history_targets()
    target_ids = [t[0] for t in targets]

    # Wait (up to ~25s) for the recorder to own metadata for every target.
    deadline = now + 25
    present: set[str] = set()
    while time.time() < deadline:
        with session_scope(session=instance.get_session(), read_only=True) as s:
            present = {
                row[0]
                for row in s.query(StatesMeta.entity_id)
                .filter(StatesMeta.entity_id.in_(target_ids))
                .all()
            }
        if len(present) == len(target_ids):
            break
        time.sleep(1.0)

    for attempt in range(5):
        try:
            with session_scope(session=instance.get_session()) as session:
                # Idempotency: if backdated history already exists for the
                # sparkline sentinel, assume injection already happened.
                sentinel_id = (
                    session.query(StatesMeta.metadata_id)
                    .filter_by(entity_id=SPARKLINE_SENTINEL)
                    .scalar()
                )
                if sentinel_id is not None:
                    existing = (
                        session.query(States)
                        .filter(
                            States.metadata_id == sentinel_id,
                            States.last_updated_ts < now - 3600,
                        )
                        .count()
                    )
                    if existing > 0:
                        _LOGGER.info(
                            "harness_seed: recorder history already present "
                            "(%d backdated rows); skipping",
                            existing,
                        )
                        return

                total = 0
                skipped: list[str] = []
                for entity_id, attrs, base, amp in targets:
                    metadata_id = (
                        session.query(StatesMeta.metadata_id)
                        .filter_by(entity_id=entity_id)
                        .scalar()
                    )
                    if metadata_id is None:
                        # Recorder hasn't created metadata for this sensor yet;
                        # never create it ourselves — skip rather than race.
                        skipped.append(entity_id)
                        continue

                    shared = json_bytes(attrs)
                    sa = StateAttributes(
                        hash=StateAttributes.hash_shared_attrs_bytes(shared),
                        shared_attrs=shared.decode(),
                    )
                    session.add(sa)
                    session.flush()
                    attributes_id = sa.attributes_id

                    rows = []
                    for i in range(n_points + 1):
                        ts = start + i * HISTORY_STEP_S
                        hours = (ts / 3600.0) % 24
                        val = base + amp * math.sin(2 * math.pi * hours / 24.0)
                        # deterministic micro-jitter so the curve isn't a pure sine
                        val += (((int(ts) // HISTORY_STEP_S) % 7) - 3) * 0.05 * amp
                        rows.append(
                            States(
                                metadata_id=metadata_id,
                                state=str(round(val, 1)),
                                attributes_id=attributes_id,
                                last_updated_ts=ts,
                                last_changed_ts=ts,
                                last_reported_ts=ts,
                                origin_idx=0,
                            )
                        )
                    session.add_all(rows)
                    total += len(rows)
            if skipped:
                _LOGGER.warning(
                    "harness_seed: %d sensors had no recorder metadata, skipped: %s",
                    len(skipped),
                    ", ".join(skipped),
                )
            _LOGGER.info(
                "harness_seed: injected %d backdated state rows across %d/%d sensors",
                total,
                len(targets) - len(skipped),
                len(targets),
            )
            return
        except OperationalError as err:
            if "locked" in str(err).lower() and attempt < 4:
                _LOGGER.warning(
                    "harness_seed: recorder DB locked, retry %d/5", attempt + 1
                )
                time.sleep(1.0)
                continue
            raise
