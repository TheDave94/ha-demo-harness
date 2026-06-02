"""Canonical PollenWatch species registry.

Single source of truth for the species PollenWatch tracks: botanical
identity, allergenic class, EAACI/D'Amato potency rating, threshold
provenance, and which upstream sources can report them globally.

Replaces (in v2.0+) the simpler ``ALLERGENS`` tuple in ``sources.base``.
The 24-species set is the v2.0 expansion from the v1.x hand-picked 6
(alder, birch, grass, mugwort, olive, ragweed).

v2.2 (issue #3): ``ThresholdStatus`` expanded from a contested
``YES``/``PARTIAL`` binary to a 5-value evidence-provenance tier,
reconciled across analytics.py / README / this file to the approved
literature review (``docs/THRESHOLD_PROVENANCE_REVIEW.md``).

Kept import-free of ``homeassistant`` so the data layer is testable in
isolation — same discipline as ``sources/base.py`` and ``const.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class SpeciesClass(StrEnum):
    """Botanical / clinical class — used for onboarding UI grouping."""

    TREE = "tree"
    GRASS = "grass"
    HERB = "herb"
    SPORE = "spore"


class Potency(StrEnum):
    """EAACI / D'Amato 2007 allergenic-potency tier.

    LOW and NEGLIGIBLE are deliberately not represented — only species with
    HIGH or MODERATE clinical relevance enter the registry. Pine, poplar,
    and the like were filtered out before the registry was defined.
    """

    HIGH = "high"
    MODERATE = "moderate"


class ThresholdStatus(StrEnum):
    """Evidence provenance for a species' grains/m³ (or spores/m³) threshold.

    v2.2: 5-value tiering reconciled across all three files (this registry,
    ``analytics.py`` comments, ``README.md``) to the approved literature review
    in ``docs/THRESHOLD_PROVENANCE_REVIEW.md`` (issue #3). The values
    describe **where the threshold number comes from**, not how clinically
    settled the number is — see the review for honest caveats on each tier.
    """

    #: Tier 2 — at least one peer-reviewed study reports a grains/m³ symptom
    #: threshold for *this species* (numbers may disagree across studies).
    SPECIES_SPECIFIC = "species_specific"

    #: Tier 1-legitimate (family-in-EAACI) — no species-specific cutoff, but
    #: the species sits in a family EAACI actually defines (Fagales /
    #: Cupressaceae / Oleaceae ≥100; Poaceae / Ambrosia ≥50; Pfaar et al.
    #: 2017, doi:10.1111/all.13092). Defensible, not arbitrary.
    FAMILY_EAACI = "family_eaaci"

    #: Tier 1-legitimate (characterised allergen) — clinically established
    #: allergen with characterised allergen proteins but no published numeric
    #: threshold. Working bracket carried for operational purposes; honest
    #: about the absence of a cited number.
    ESTABLISHED_NO_THRESHOLD = "established_no_threshold"

    #: Tier 1-borrowed — family not in EAACI's scheme; bracket assigned by
    #: aerobiological analogy only. Weakest tier — defensible only as
    #: "in season / above peak."
    FAMILY_ANALOGY = "family_analogy"

    #: Tier 3 — fungal spore (separate evidence base from pollen).
    FUNGAL = "fungal"


#: Coarse provenance grouping derived from :class:`ThresholdStatus`, used by
#: card UIs to drive a binary "is this threshold provenance worth flagging?"
#: glance treatment. The mapping collapses the 5 source tiers into 3 derived
#: values; the rule lives here so neither the PollenWatch card nor any
#: downstream consumer (e.g. oriel-dashboard) hardcodes its own copy.
#:
#: ``FUNGAL`` collapses to ``"species"`` deliberately — alternaria carries
#: the best-cited threshold in the 24-species set (Rapiejko / Ricci /
#: PMC4473279, ~80–100 spores/m³), so provenance-wise it belongs unmarked
#: even though its measurement basis differs from pollen.
THRESHOLD_BASIS_FROM_STATUS: Final[dict[ThresholdStatus, str]] = {
    ThresholdStatus.SPECIES_SPECIFIC: "species",
    ThresholdStatus.FUNGAL: "species",
    ThresholdStatus.FAMILY_EAACI: "family",
    ThresholdStatus.ESTABLISHED_NO_THRESHOLD: "estimated",
    ThresholdStatus.FAMILY_ANALOGY: "estimated",
}


# Source keys — literal strings mirrored from const.SOURCE_*. Kept inline
# here to keep this module import-free of const (which depends on this
# module via const.ALLERGEN_NAMES derivation in Phase B+).
_OM = "open_meteo"
_PI = "polleninformation"
_DWD = "dwd"
_MS = "meteoswiss"
_EPIN = "epin"
_GOOGLE = "google"


@dataclass(frozen=True)
class SpeciesInfo:
    """Canonical metadata for a PollenWatch species.

    ``sources`` is the *global* set — the upstream sources that can report
    this species when actively configured and in their coverage area.
    Per-install coverage is computed at runtime by intersecting with the
    user's enabled sources.

    ``thresholds`` is the evidence-provenance tier (see
    :class:`ThresholdStatus`). v2.2+: the per-species value is surfaced on
    the raw sensors as the ``threshold_status`` attribute so downstream
    consumers can mark "this 'high' is on a borrowed family bracket"
    without consulting a second data source.
    """

    key: str
    latin: str
    common: str
    class_: SpeciesClass
    potency: Potency
    thresholds: ThresholdStatus
    sources: frozenset[str]


CANONICAL_SPECIES: Final[dict[str, SpeciesInfo]] = {
    # =================================================================
    # HIGH potency (12)
    # =================================================================
    "alder": SpeciesInfo(
        "alder", "Alnus", "Alder",
        SpeciesClass.TREE, Potency.HIGH, ThresholdStatus.SPECIES_SPECIFIC,
        frozenset({_OM, _PI, _DWD, _MS, _EPIN, _GOOGLE}),
    ),
    "birch": SpeciesInfo(
        "birch", "Betula", "Birch",
        SpeciesClass.TREE, Potency.HIGH, ThresholdStatus.SPECIES_SPECIFIC,
        frozenset({_OM, _PI, _DWD, _MS, _EPIN, _GOOGLE}),
    ),
    "grass": SpeciesInfo(
        "grass", "Poaceae", "Grass",
        SpeciesClass.GRASS, Potency.HIGH, ThresholdStatus.SPECIES_SPECIFIC,
        frozenset({_OM, _PI, _DWD, _MS, _EPIN, _GOOGLE}),
    ),
    "hazel": SpeciesInfo(
        "hazel", "Corylus", "Hazel",
        SpeciesClass.TREE, Potency.HIGH, ThresholdStatus.SPECIES_SPECIFIC,
        frozenset({_PI, _DWD, _MS, _EPIN, _GOOGLE}),
    ),
    # mugwort: SPECIES_SPECIFIC because Rapiejko 2007 has mugwort-specific
    # symptom data (in the systematic-review 6-species set), even though that
    # data does NOT yield a single numeric cutoff (evidence graded *limited*
    # per the 2021 Aerobiologia review). The (3, 50) bracket in analytics.py
    # is the herb-class default, not a mugwort-derived number — see the
    # analytics.py comment for that nuance.
    "mugwort": SpeciesInfo(
        "mugwort", "Artemisia", "Mugwort",
        SpeciesClass.HERB, Potency.HIGH, ThresholdStatus.SPECIES_SPECIFIC,
        frozenset({_OM, _PI, _DWD, _EPIN, _GOOGLE}),
    ),
    "ragweed": SpeciesInfo(
        "ragweed", "Ambrosia", "Ragweed",
        SpeciesClass.HERB, Potency.HIGH, ThresholdStatus.SPECIES_SPECIFIC,
        frozenset({_OM, _PI, _DWD, _EPIN, _GOOGLE}),
    ),
    "olive": SpeciesInfo(
        "olive", "Olea", "Olive",
        SpeciesClass.TREE, Potency.HIGH, ThresholdStatus.SPECIES_SPECIFIC,
        frozenset({_OM, _PI, _GOOGLE}),
    ),
    "rye": SpeciesInfo(
        "rye", "Secale", "Cereal rye",
        SpeciesClass.GRASS, Potency.HIGH, ThresholdStatus.FAMILY_EAACI,
        frozenset({_PI, _DWD, _EPIN}),
    ),
    "plantago": SpeciesInfo(
        "plantago", "Plantago", "Plantain",
        SpeciesClass.HERB, Potency.HIGH, ThresholdStatus.SPECIES_SPECIFIC,
        frozenset({_EPIN}),
    ),
    # urtica + nettle_family: SPECIES_SPECIFIC carries direct taxon
    # evidence (Rapiejko 2007 found nettle SPT-positive patients had low/no
    # symptoms) — a low-potency cited finding, not analogy.
    "urtica": SpeciesInfo(
        "urtica", "Urtica", "Common nettle",
        SpeciesClass.HERB, Potency.HIGH, ThresholdStatus.SPECIES_SPECIFIC,
        frozenset({_EPIN}),
    ),
    "nettle_family": SpeciesInfo(
        "nettle_family", "Urticaceae", "Nettle / pellitory",
        SpeciesClass.HERB, Potency.HIGH, ThresholdStatus.SPECIES_SPECIFIC,
        frozenset({_PI}),
    ),
    # alternaria: FUNGAL — separate evidence base (spores/m³ scale). The
    # review notes ~80–100 spores/m³ as the best-cited threshold in the
    # whole set (Rapiejko 2007; Ricci 1995; PMC4473279 European multi-city)
    # with a modern refinement of Alt a 1 ~20.7 pg/m³ (Thermo m229) — BUT
    # this number is NOT wired into analytics.py because alternaria routes
    # via the PI collapse_index path (0–4 index), never bucket_level. A
    # _THRESHOLDS entry would be dead code unless a future source emits
    # spores/m³ for alternaria. See PART C of issue #3.
    "alternaria": SpeciesInfo(
        "alternaria", "Alternaria", "Alternaria (spore)",
        SpeciesClass.SPORE, Potency.HIGH, ThresholdStatus.FUNGAL,
        frozenset({_PI}),
    ),
    # =================================================================
    # MODERATE potency (12)
    # =================================================================
    "ash": SpeciesInfo(
        "ash", "Fraxinus", "Ash",
        SpeciesClass.TREE, Potency.MODERATE, ThresholdStatus.SPECIES_SPECIFIC,
        frozenset({_DWD, _MS, _EPIN, _GOOGLE}),
    ),
    "oak": SpeciesInfo(
        "oak", "Quercus", "Oak",
        SpeciesClass.TREE, Potency.MODERATE, ThresholdStatus.FAMILY_EAACI,
        frozenset({_MS, _EPIN, _GOOGLE}),
    ),
    # Cupressaceae family — Google's JUNIPER + CYPRESS_PINE both fold here.
    "cypress_family": SpeciesInfo(
        "cypress_family", "Cupressaceae", "Cypress / juniper",
        SpeciesClass.TREE, Potency.MODERATE, ThresholdStatus.FAMILY_EAACI,
        frozenset({_PI, _GOOGLE}),
    ),
    "plane_tree": SpeciesInfo(
        "plane_tree", "Platanus", "Plane tree",
        SpeciesClass.TREE, Potency.MODERATE, ThresholdStatus.SPECIES_SPECIFIC,
        frozenset({_PI, _EPIN}),
    ),
    "beech": SpeciesInfo(
        "beech", "Fagus", "Beech",
        SpeciesClass.TREE, Potency.MODERATE, ThresholdStatus.FAMILY_EAACI,
        frozenset({_MS, _EPIN}),
    ),
    # elm: established allergen (US 24.6% / Buenos Aires 37.4% / Turkey
    # 18.2% SPT-positive; Canadian +2.63% asthma hospitalizations per IQR
    # Ulmus increase) but NO published Ulmus-specific grains/m³ threshold,
    # and Ulmaceae is NOT in EAACI's family scheme. Current (10, 100) is an
    # uncited working bracket inherited from the v2.0 default.
    "elm": SpeciesInfo(
        "elm", "Ulmus", "Elm",
        SpeciesClass.TREE, Potency.MODERATE, ThresholdStatus.ESTABLISHED_NO_THRESHOLD,
        frozenset({_EPIN, _GOOGLE}),
    ),
    "carpinus": SpeciesInfo(
        "carpinus", "Carpinus", "Hornbeam",
        SpeciesClass.TREE, Potency.MODERATE, ThresholdStatus.SPECIES_SPECIFIC,
        frozenset({_EPIN}),
    ),
    # Quercus ilex — distinct from Quercus (deciduous oak); Mediterranean
    # evergreen with different clinical profile. ePIN distinguishes.
    "holm_oak": SpeciesInfo(
        "holm_oak", "Quercus ilex", "Holm oak",
        SpeciesClass.TREE, Potency.MODERATE, ThresholdStatus.FAMILY_EAACI,
        frozenset({_EPIN}),
    ),
    # chenopodium: established allergen (Che a 1/2/3, 8–10% SPT-positivity
    # in arid regions per Córdoba/Iran studies) — no published threshold,
    # allergenicity contested across authors. Working bracket carried.
    "chenopodium": SpeciesInfo(
        "chenopodium", "Chenopodium", "Goosefoot",
        SpeciesClass.HERB, Potency.MODERATE, ThresholdStatus.ESTABLISHED_NO_THRESHOLD,
        frozenset({_EPIN}),
    ),
    "rumex": SpeciesInfo(
        "rumex", "Rumex", "Sorrel / dock",
        SpeciesClass.HERB, Potency.MODERATE, ThresholdStatus.FAMILY_ANALOGY,
        frozenset({_EPIN}),
    ),
    # juglans: pollen moderately allergenic (sibling pecan onset 10–20
    # grains/m³ per Thermo t22); no Juglans-specific threshold. "Walnut
    # allergy" literature mostly = nut/food, irrelevant to pollen.
    "juglans": SpeciesInfo(
        "juglans", "Juglans", "Walnut",
        SpeciesClass.TREE, Potency.MODERATE, ThresholdStatus.ESTABLISHED_NO_THRESHOLD,
        frozenset({_EPIN}),
    ),
    # Family-level catch-all for Asteraceae pollen ePIN measures but cannot
    # identify to genus. Mugwort and ragweed (also Asteraceae) are tracked
    # separately and carry their own SPECIES_SPECIFIC evidence; this row is
    # the residual ("other Asteraceae"), genuinely the only species in the
    # registry with no real basis beyond the herb family bracket.
    "asteraceae": SpeciesInfo(
        "asteraceae", "Asteraceae", "Asteraceae (other)",
        SpeciesClass.HERB, Potency.MODERATE, ThresholdStatus.FAMILY_ANALOGY,
        frozenset({_EPIN}),
    ),
}


ALL_SPECIES_KEYS: Final[frozenset[str]] = frozenset(CANONICAL_SPECIES.keys())


# The v1.x canonical set — used as the migration fallback when a v2 entry is
# encountered with no recoverable allergen list. These keys MUST exist in
# CANONICAL_SPECIES above, since that's the lossless-upgrade contract.
CANONICAL_V1_SPECIES: Final[tuple[str, ...]] = (
    "alder", "birch", "grass", "mugwort", "olive", "ragweed",
)


# Sanity: the v1 set is a subset of the canonical registry.
assert set(CANONICAL_V1_SPECIES) <= ALL_SPECIES_KEYS, (
    "CANONICAL_V1_SPECIES contains keys not in CANONICAL_SPECIES — "
    "migration would orphan data"
)


def threshold_basis_for(species_key: str) -> str:
    """Coarse provenance grouping for a species, derived from its
    :class:`ThresholdStatus`. Returns one of ``"species"`` / ``"family"`` /
    ``"estimated"`` per :data:`THRESHOLD_BASIS_FROM_STATUS`."""
    return THRESHOLD_BASIS_FROM_STATUS[CANONICAL_SPECIES[species_key].thresholds]
