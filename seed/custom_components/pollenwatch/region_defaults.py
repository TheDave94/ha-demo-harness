"""Region-aware default species preselection for the v2.0+ onboarding.

Maps Home Assistant's ``hass.config.country`` to a defensible per-country
species preselection (subset of the 24 canonical species), and resolves
which sources can be active for a given country (driving the
"locally-available species" filter in the config flow).

Kept import-free of ``homeassistant`` so the data + lookup logic is
testable in isolation — same discipline as ``sources/species_registry``.

Backing data is the locked recommendation table (see project memory:
2026-05-30 sign-off). alternaria deliberately stays OFF default everywhere
(opt-in-spore decision); it's available for users who co-track it but never
pre-selected in any region.
"""

from __future__ import annotations

from typing import Final

from .sources.species_registry import (
    CANONICAL_SPECIES,
    SpeciesClass,
)

# --- Per-PI-country preselection -----------------------------------------

#: Species preselected per ISO-2 country code (PI-supported countries).
#: Lists are intentionally per-country (not class-grouped) — the form
#: builds the UI grouping at display time.
REGION_DEFAULT_SPECIES: Final[dict[str, list[str]]] = {
    # AT (maintainer's region): Central EU + alternaria/ash/oak.
    "AT": ["alder", "birch", "grass", "hazel", "mugwort", "ragweed",
           "ash", "oak"],
    # DE: + DWD-specific rye + urban plane_tree.
    "DE": ["alder", "birch", "grass", "hazel", "mugwort", "ragweed",
           "ash", "oak", "plane_tree", "rye"],
    # CH: + MeteoSwiss-only beech.
    "CH": ["alder", "birch", "grass", "hazel", "mugwort", "ragweed",
           "ash", "oak", "beech"],
    # FR (north-south mix): includes Mediterranean olive/cypress + plane.
    "FR": ["alder", "birch", "grass", "hazel", "mugwort", "ragweed",
           "ash", "oak", "cypress_family", "plane_tree", "olive"],
    # IT / ES (Mediterranean): olive, cypress, pellitory family.
    "IT": ["alder", "birch", "grass", "hazel", "mugwort", "ragweed",
           "olive", "cypress_family", "nettle_family", "plane_tree"],
    "ES": ["alder", "birch", "grass", "hazel", "mugwort", "ragweed",
           "olive", "cypress_family", "nettle_family", "plane_tree"],
    # GB (UK): grass-dominant, urban plane_tree.
    "GB": ["alder", "birch", "grass", "hazel", "mugwort", "ragweed",
           "ash", "plane_tree"],
    # SE (Nordic): birch-dominant, no olive/cypress.
    "SE": ["alder", "birch", "grass", "hazel", "mugwort", "ragweed",
           "ash"],
    # Eastern PI countries (PL/LV/LT/UA): ragweed-heavy.
    "PL": ["alder", "birch", "grass", "hazel", "mugwort", "ragweed",
           "ash", "oak"],
    "LV": ["alder", "birch", "grass", "hazel", "mugwort", "ragweed",
           "ash", "oak"],
    "LT": ["alder", "birch", "grass", "hazel", "mugwort", "ragweed",
           "ash", "oak"],
    "UA": ["alder", "birch", "grass", "hazel", "mugwort", "ragweed",
           "ash", "oak"],
    # TR (partial Mediterranean).
    "TR": ["alder", "birch", "grass", "hazel", "mugwort", "ragweed",
           "olive", "cypress_family", "plane_tree"],
}


# --- Non-PI countries ----------------------------------------------------

#: For non-PI countries without Mediterranean coverage.
NON_PI_DEFAULTS_NORTH: Final[list[str]] = [
    "alder", "birch", "grass", "hazel", "mugwort", "ragweed", "ash", "oak",
]

#: For non-PI Mediterranean countries (+ olive + cypress_family).
NON_PI_DEFAULTS_MED: Final[list[str]] = [
    "alder", "birch", "grass", "hazel", "mugwort", "ragweed",
    "ash", "oak", "olive", "cypress_family",
]

NON_PI_MED_COUNTRIES: Final[frozenset[str]] = frozenset({
    "GR", "PT", "HR", "MT", "CY",
})

NON_PI_NORTH_COUNTRIES: Final[frozenset[str]] = frozenset({
    "NL", "BE", "FI", "NO", "DK", "IE", "CZ", "SK", "HU", "BG", "RO",
    "EE", "LU", "IS", "SI",
})


# --- High-potency cross-validated quick-pick ----------------------------

#: One-click default for users who don't want to think about it. The 8
#: species with HIGH or MODERATE potency AND >= 3 sources globally — the
#: "core" that's cross-validated across the matrix.
HIGH_POTENCY_CORE: Final[list[str]] = [
    "alder", "birch", "grass", "hazel", "mugwort", "ragweed",
    "olive", "ash",
]

#: Fallback for unknown country: the high-potency core.
DEFAULT_FALLBACK: Final[list[str]] = HIGH_POTENCY_CORE


# --- Source availability by country -------------------------------------

# Sources always available (no country constraint).
_ALWAYS_AVAILABLE: Final[frozenset[str]] = frozenset({"open_meteo", "google"})

# PI's supported country list (mirrored from polleninformation.py for
# import-cleanliness — keep in sync).
_PI_COUNTRIES: Final[frozenset[str]] = frozenset({
    "AT", "CH", "DE", "ES", "FR", "GB", "IT", "LV", "LT", "PL", "SE",
    "TR", "UA",
})


def get_active_sources_for_country(country: str | None) -> frozenset[str]:
    """Return the source keys that CAN be active at this country.

    "Can" — meaning the source's coverage area includes the country, so
    the user could enable it. Doesn't check whether the user actually
    enabled it (that's the live ``CONF_SOURCES`` state). Used to filter
    the species picker so users only see species at least one source
    COULD cover at their location.
    """
    active = set(_ALWAYS_AVAILABLE)
    if not country:
        return frozenset(active)
    upper = country.upper()
    if upper in _PI_COUNTRIES:
        active.add("polleninformation")
    if upper == "DE":
        active.add("dwd")
        # ePIN is Bavaria-only but country-level can't distinguish; surface
        # for any DE user (Bavarians will enable, non-Bavarians won't).
        active.add("epin")
    if upper == "CH":
        active.add("meteoswiss")
    return frozenset(active)


def get_locally_available_species(country: str | None) -> list[str]:
    """Canonical species keys whose source set overlaps with what CAN be
    active in this country. Sorted by canonical key for stable UI order."""
    active = get_active_sources_for_country(country)
    return sorted(
        key for key, info in CANONICAL_SPECIES.items()
        if info.sources & active
    )


def get_region_defaults(country: str | None) -> list[str]:
    """Return the preselection list for the species step in this country.

    Result is the locked recommendation table for known countries, then a
    Mediterranean/Northern split for non-PI EU countries, then a fallback
    to the high-potency core for anything unrecognised. alternaria is
    NEVER in any return value (opt-in-only spore — sign-off 2026-05-30).
    The list is filtered down to species available at this country (no
    point preselecting things no local source can cover).
    """
    if not country:
        defaults = list(DEFAULT_FALLBACK)
    else:
        upper = country.upper()
        if upper in REGION_DEFAULT_SPECIES:
            defaults = list(REGION_DEFAULT_SPECIES[upper])
        elif upper in NON_PI_MED_COUNTRIES:
            defaults = list(NON_PI_DEFAULTS_MED)
        elif upper in NON_PI_NORTH_COUNTRIES:
            defaults = list(NON_PI_DEFAULTS_NORTH)
        else:
            defaults = list(DEFAULT_FALLBACK)
    # Filter to locally-available (the picker's universe). Preserves order.
    available = set(get_locally_available_species(country))
    return [s for s in defaults if s in available]


def species_class_label(key: str) -> str:
    """UI grouping prefix — `Tree` / `Grass` / `Herb` / `Spore`."""
    info = CANONICAL_SPECIES[key]
    return {
        SpeciesClass.TREE: "Tree",
        SpeciesClass.GRASS: "Grass",
        SpeciesClass.HERB: "Herb",
        SpeciesClass.SPORE: "Spore",
    }[info.class_]
