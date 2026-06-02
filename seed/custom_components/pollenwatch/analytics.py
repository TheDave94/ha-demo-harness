"""Derived analytics for PollenWatch — pure, Home Assistant-free.

All cross-source comparison happens on a common 3-level scale (see ANALYTICS.md):
    0 = below season onset, 1 = in season, 2 = at/above peak.
Open-Meteo grains/m³ is bucketed *down* to a level via EAACI/CAMS thresholds;
the polleninformation 0–4 index is collapsed onto the same 3 levels (an
operational alignment). Raw per-source values are never reconstructed from a
level.

These functions take values in and return numbers out — no HA, no I/O — so they
are unit-tested in isolation like the source parsers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sources.base import AllergenSeries

# (onset, peak) grains/m³ per species. Provenance is documented per row;
# the evidence tier for each species lives in
# ``sources/species_registry.CANONICAL_SPECIES[<key>].thresholds`` (a 5-value
# ``ThresholdStatus`` enum: SPECIES_SPECIFIC / FAMILY_EAACI /
# ESTABLISHED_NO_THRESHOLD / FAMILY_ANALOGY / FUNGAL). v2.2 reconciles this
# table, the registry's enum, and the README to the approved literature review
# in ``docs/THRESHOLD_PROVENANCE_REVIEW.md`` (issue #3) — see ANALYTICS.md
# for the rigorous EAACI/Pfaar 2017 derivation of the 3-level scale itself.
#
# Boundary convention: a value equal to a threshold belongs to the higher
# level (``>=``). Below ``onset`` → 0 (none); ``onset`` ≤ x < ``peak`` → 1
# (low / in season); ``>= peak`` → 2 (high / at or above peak).
_THRESHOLDS: dict[str, tuple[float, float]] = {
    # ---- Tier 2 (SPECIES_SPECIFIC): per-species published evidence ----
    # birch: low ~20 per Aerobiologia syst. review 2021; peak 100 VALIDATED
    # by Struß 2025 controlled chamber (doi:10.1159/000545509, PMID 40328230,
    # sham-controlled titrate-to-effect, threshold 50–100 pollen/m³).
    "birch": (20, 100),
    # alder: 45 = first symptoms, 80 = all alder-allergics symptomatic,
    # severe >95 (Rapiejko 2007 PMC6245103; Kraków PM10 study PMID 29693977).
    # Cited peak below the EAACI tree family bracket (100) — per-species
    # evidence wins.
    "alder": (45, 80),
    # hazel: operational low 0–35 / high >35; severe symptoms >80
    # (Rapiejko 2007 PMC4996891; Kraków study). Same per-species-over-family
    # reasoning as alder.
    "hazel": (35, 80),
    # ash: SPECIES_SPECIFIC label in the registry — 2021 Aerobiologia
    # systematic review documents 18–28 grains/m³ ↑doctor visits (strongest
    # single evidence in the 24-species set). Numeric refinement to ~18
    # onset was OUT OF SCOPE for v2.2 (the approved 6-species change list
    # was ragweed/olive/birch/alder/hazel/mugwort). Tracked for a future
    # release: see the FINAL provenance review for the cited value.
    "ash": (10, 100),
    # olive: regional Spanish operational scale low 1–50 / mod 51–200 /
    # high >200 (PMC7349006); single-study cutoff 162 (Sciencedirect
    # S1081120610010537); monosensitized cohort ~400 (PubMed 10394105).
    # PW low (10) is inside the Spanish "low 1–50" range — uncited but
    # consistent inherited bracket; the high (200) is the anchored number
    # and corrects the v1 over-warning at 100.
    "olive": (10, 200),
    # plane_tree: symptom onset ~91 (Thermo t11); clinical onset ~130
    # (ClinicAL BCN (130)); operational risk >50 / high >200 (EMJ Évora).
    # Existing (10, 100) bracket fits the operational range; cited Tier 2
    # but no single anchor strong enough to override.
    "plane_tree": (10, 100),
    # mugwort: HERB class (was previously at the tree bracket — class-error
    # fix). No mugwort-specific published cutoff exists; evidence graded
    # *limited* per the 2021 Aerobiologia systematic review. Rapiejko 2007
    # documents mugwort-specific symptom data ("intensive symptoms") but
    # does not yield a point cutoff — hence the bracket is the herb-class
    # default (3, 50), not a mugwort-derived number. The
    # SPECIES_SPECIFIC label in the registry reflects the existence of
    # taxon-specific evidence, not the existence of a cited cutoff.
    "mugwort": (3, 50),
    # ragweed: best published evidence — clinical threshold <20, sensitive
    # patients 1–5, children 40 for 50% symptomatic, 4-severity-level
    # Milan model (PMC5357339; PMC2868868; Nature s41598-022-20069-y).
    # NB: the Fraunhofer UBAMBI controlled-chamber trial (NCT05346718)
    # completed Dec 2024 — birch arm published May 2025; ragweed arm
    # remains UNPUBLISHED as of mid-2026. Revisit if/when it appears;
    # do not imply imminent supersession.
    "ragweed": (5, 20),
    # grass: all sensitized symptomatic ~50; 25% at 20; dyspnoea >120
    # (Rapiejko 2007; Davies & Smith 1973; EAACI Pfaar 2017 ≥50 is
    # grass-anchored; PMC3699361 30/80). The (3, 50) bracket is the
    # best-anchored in the table — EAACI ≥50 is grass-derived. Hold.
    "grass": (3, 50),
    # plantago: bracket is the herb-class default. Rapiejko 2007
    # (PMID 18260258) finds SPT-positive patients had "low intensity or
    # even no symptoms" during pollination — cited weak-allergen evidence
    # rather than a numeric cutoff.
    "plantago": (3, 50),
    # urtica / nettle_family: same Rapiejko 2007 weak-allergen finding —
    # SPT-positive → low/no symptoms. NOTE: Parietaria (a sibling
    # Urticaceae) IS clinically potent; not separately tracked here, but
    # users in Mediterranean regions should be aware.
    "urtica": (3, 50),
    "nettle_family": (3, 50),
    # carpinus: weak Tier 2 — 12.5% of Betulaceae-allergics
    # hornbeam-*only*; Croatia asthma admissions +21% at high hornbeam;
    # birch-confounded, no isolated numeric cutoff (Gumowski et al.,
    # Aerobiologia, doi:10.1023/A:1007600313862). Bracket inherited from
    # the Fagales family.
    "carpinus": (10, 100),
    # ---- Tier 1-legitimate (FAMILY_EAACI): EAACI family cutoffs ----
    # rye: no rye-specific cutoff, but rye is Poaceae → EAACI grass ≥50.
    "rye": (3, 50),
    # oak: clinical relevance contested (Madrid: high counts but negative
    # nasal challenge, JACI S0091-6749(15)02270-8) but recent Fagales work
    # argues Bet v 1-homolog sensitizing activity is *underestimated*
    # (Que i 1 paper 2020); Canadian +2.3% asthma-hospitalization signal
    # (Thermo t7). EAACI Fagales ≥100 holds.
    "oak": (10, 100),
    # holm_oak (Q. ilex): 1995 "does not cause allergies" null is
    # SUPERSEDED — recent study finds 59.8% of pollen-allergic children
    # sensitized, "significant increase since 1995"; Que i 1 (Bet v 1
    # homolog) now characterised. Still no grains/m³ threshold; EAACI
    # Fagales ≥100 carried.
    "holm_oak": (10, 100),
    # beech: no threshold study; minor aerobiological contributor
    # (0.9–2% of tree pollen, Biedermann 2019 doi:10.1111/all.13758);
    # birch-homologous cross-reactant. EAACI Fagales ≥100.
    "beech": (10, 100),
    # cypress_family: clinically major Mediterranean winter allergen
    # (Cup s 1 / Cup a 1), genuine primary monosensitization (Charpin 2005
    # doi:10.1111/j.1398-9995.2005.00731.x); reported as peaks/prevalence,
    # no isolated symptom-onset grains/m³ threshold. EAACI Cupressaceae
    # ≥100 holds.
    "cypress_family": (10, 100),
    # ---- Tier 1-legitimate (ESTABLISHED_NO_THRESHOLD): characterised allergen,
    #      no number, working bracket carried ----
    # chenopodium: established allergen (Che a 1/2/3) with 8–10%
    # SPT-positivity in arid regions (PubMed 2817534 Córdoba 8.4%;
    # Thermo w10 Iran 9.6%; JACI S0091-6749(04)00807-3 allergens). No
    # threshold study; allergenicity contested across authors. Working
    # bracket — herb-class default.
    "chenopodium": (3, 50),
    # juglans (walnut): pollen moderately allergenic; sibling pecan
    # symptom onset 10–20 grains/m³ (Thermo t22). NB: most "walnut
    # allergy" literature = the NUT/food, irrelevant to pollen — do not
    # cite food studies for pollen thresholds.
    "juglans": (10, 100),
    # elm (Ulmus): established allergen (US 24.6% / Buenos Aires 37.4%
    # SPT-positive; Canadian +2.63% asthma hospitalizations per IQR Ulmus
    # increase) but NO published Ulmus-specific grains/m³ threshold, and
    # Ulmaceae is NOT in EAACI's family scheme. The (10, 100) bracket is
    # an uncited working value — defensible only as a working bucket.
    "elm": (10, 100),
    # ---- Tier 1-borrowed (FAMILY_ANALOGY): bracket from aerobiological analogy ----
    # rumex (dock/sorrel): secondary allergen, nasal-challenge-confirmed
    # responders exist (17–19% SPT-positive in some cohorts); no
    # threshold; grass-season confounded; partial ragweed cross-reactivity;
    # no IUIS allergens. Herb-class default.
    "rumex": (3, 50),
    # asteraceae (other) — the residual after mugwort and ragweed are
    # extracted. Catch-all; clinical weight carried by the separately
    # tracked Asteraceae species. Genuinely the weakest provenance row in
    # the table — herb-class default by analogy alone.
    "asteraceae": (3, 50),
    # ---- Tier 3 (FUNGAL): alternaria ----
    # alternaria is NOT in this table — it routes via the PI 0–4 index
    # (``collapse_index``) path, never ``bucket_level``. The FINAL
    # provenance review notes alternaria has the best-cited threshold in
    # the entire 24-species set: ~80–100 spores/m³ first symptoms
    # (Rapiejko 2007; Ricci 1995; PMC4473279 European multi-city); modern
    # refinement Alt a 1 ~20.7 pg/m³ (Thermo m229). The number is NOT
    # wired here because no current source emits alternaria as spores/m³
    # — adding an entry would be dead code that falsely implies the
    # bucketing path uses it. If a future source ever emits spores/m³ for
    # alternaria, wire ~80 onset / ~100 peak (cited) at that time.
}

# polleninformation 0–4 index -> 3-level scale (operational alignment).
_INDEX_TO_LEVEL: dict[int, int] = {0: 0, 1: 1, 2: 1, 3: 2, 4: 2}

#: Trailing window for recent_percentile (rolling days, relative to today).
PERCENTILE_WINDOW_DAYS = 92
#: Minimum distinct days of data before a percentile is emitted (else
#: "insufficient history"). ~2 weeks.
MIN_PERCENTILE_DAYS = 14


def bucket_level(species: str, grains: float) -> int | None:
    """Bucket a grains/m³ concentration down to the 0/1/2 level.

    Boundary convention: a value equal to a threshold belongs to the higher
    level (``>=``). Returns ``None`` for species without published thresholds
    (the source contributes a raw value but no level → not in consensus
    aggregation). Defensive even though v2.0+ covers all 24 species.
    """
    bounds = _THRESHOLDS.get(species)
    if bounds is None:
        return None
    onset, peak = bounds
    if grains >= peak:
        return 2
    if grains >= onset:
        return 1
    return 0


def collapse_index(index: int) -> int:
    """Collapse the polleninformation 0–4 index to the 0/1/2 level."""
    return _INDEX_TO_LEVEL[max(0, min(4, int(index)))]


# DWD 7-point string scale -> 3-level (operational alignment, by meaning; see
# ANALYTICS.md). "high"(3)/"moderate-high"(2-3) -> 2; "moderate"(2) is mid -> 1.
_DWD_TO_LEVEL: dict[str, int] = {
    "0": 0,
    "0-1": 0,
    "1": 1,
    "1-2": 1,
    "2": 1,
    "2-3": 2,
    "3": 2,
}


def dwd_collapse(value: str | None) -> int | None:
    """Collapse a DWD value ('0'..'3' with half-steps) to the 0/1/2 level.

    Returns ``None`` (omit the source for this species) for no-data ('-1'),
    missing, or any unexpected value — never crash, never silently treat as 0.
    """
    if value is None:
        return None
    return _DWD_TO_LEVEL.get(str(value).strip())


# Google Universal Pollen Index (UPI) 0–5 -> 3-level (operational alignment, by
# meaning; see ANALYTICS.md). "None"(0) -> 0 (below onset); "Very Low"/"Low"/
# "Moderate"(1–3) -> 1 (in season); "High"/"Very High"(4–5) -> 2 (peak). Moderate
# stays at 1 because Google reserves High/Very High for the elevated tier — the
# health-conservative bias lives once in consensus take-the-higher, not here.
# Top-two -> 2 mirrors the polleninformation 0–4 collapse.
_UPI_TO_LEVEL: dict[int, int] = {0: 0, 1: 1, 2: 1, 3: 1, 4: 2, 5: 2}


def google_collapse(value: object) -> int | None:
    """Collapse a Google UPI value (0–5) to the 0/1/2 level.

    Returns ``None`` (omit the source for this species) for missing or
    unexpected values — never crash, never silently treat as 0.
    """
    if value is None:
        return None
    try:
        upi = int(value)
    except (ValueError, TypeError):
        return None
    return _UPI_TO_LEVEL.get(upi)


# Per-source bucketing dispatch. Hardcoded source-key strings (mirrored from
# const.SOURCE_*) rather than const.py imports — keeps this module HA-free +
# dependency-light, and the keys are baseline constants whose value would
# break entity unique_ids if changed.
_SRC_DWD = "dwd"
_SRC_POLLENINFORMATION = "polleninformation"
_SRC_GOOGLE = "google"

#: Canonical human-readable labels for the 3-level scale. Matches the
#: consensus enum vocabulary verbatim (see brand/GAUGE_SPEC.md) so a raw
#: sensor's ``level_label`` reads the same as the consensus state.
LEVEL_LABELS: dict[int, str] = {0: "none", 1: "low", 2: "high"}


def level_label(level: int | None) -> str | None:
    """Human-readable label for an int level. ``None`` in → ``None`` out."""
    if level is None:
        return None
    return LEVEL_LABELS.get(level)


def level_for_source(
    source_key: str, species: str, series: AllergenSeries
) -> int | None:
    """Bucket one source's reading for one species to the common 0/1/2 level.

    Single source of truth for every consumer that needs a severity bucket:
    the analytics consensus pass, the raw-sensor ``level`` attribute (v2.1+),
    and any future caller. Mirrors what the analytics layer already does;
    the previous private duplicate on the analytics coordinator delegates
    to this function as of v2.1.

    Returns ``None`` when bucketing can't resolve: DWD ``"-1"`` no-data, a
    missing/unparseable Google UPI, or a grains/m³ species without a
    threshold entry (only alternaria — which never reaches this path,
    routed via collapse_index instead).
    """
    if source_key == _SRC_DWD:
        return dwd_collapse(series.native)  # 7-point string
    if series.current is None:
        return None
    if source_key == _SRC_POLLENINFORMATION:
        return collapse_index(int(series.current))  # 0–4 index
    if source_key == _SRC_GOOGLE:
        return google_collapse(series.current)  # UPI 0–5 index
    return bucket_level(species, series.current)  # grains/m³ concentration


def daily_peaks(
    times: list[str], values: list[float | None]
) -> list[tuple[str, float]]:
    """Group an aligned (times, values) series into per-day peak values.

    Returns ``(date, peak)`` pairs sorted by date; ``None`` values are skipped.
    Peaks (per-day max), not hourly values, are the population for percentiles.
    """
    peaks: dict[str, float] = {}
    for time, value in zip(times, values, strict=False):
        if value is None:
            continue
        date = time[:10]
        peaks[date] = max(peaks.get(date, value), value)
    return sorted(peaks.items())


def percentile_rank(value: float, distribution: list[float]) -> float | None:
    """Empirical percentile rank of ``value`` within ``distribution`` (0–100).

    Midrank ("mean") convention with linear tie handling:
    ``100 * (count(x < value) + 0.5 * count(x == value)) / n``. ``None`` for an
    empty distribution.
    """
    n = len(distribution)
    if n == 0:
        return None
    less = sum(1 for x in distribution if x < value)
    equal = sum(1 for x in distribution if x == value)
    return 100.0 * (less + 0.5 * equal) / n


@dataclass(slots=True)
class PercentileResult:
    """Outcome of a recent_percentile computation."""

    percentile: float | None
    days: int
    status: str  # "ok" | "insufficient_history" | "off_season" | "no_data"


def compute_recent_percentile(
    peaks: list[tuple[str, float]],
    today: str,
    *,
    min_days: int = MIN_PERCENTILE_DAYS,
) -> PercentileResult:
    """Rank today's daily peak against the window's daily-peak distribution.

    ``peaks`` is the daily-peak series already limited to the trailing window
    (``daily_peaks`` output, dates ≤ today). The distribution includes today.

    Statuses (state is a number only for ``ok``):
    - ``no_data`` — today's value is missing from the window.
    - ``insufficient_history`` — fewer than ``min_days`` distinct days.
    - ``off_season`` — the **whole window is zero** (max == 0): a percentile
      would be a misreadable 50% ("no signal", not "mid-range"), and any trace
      would jerk it to ~90%. Note this is keyed on the window max, not today: a
      zero *today* in a window that has signal is a genuine, informative low
      percentile and stays ``ok``.
    - ``ok`` — a real percentile.
    """
    by_date = dict(peaks)
    today_peak = by_date.get(today)
    if today_peak is None:
        return PercentileResult(None, len(by_date), "no_data")
    if len(by_date) < min_days:
        return PercentileResult(None, len(by_date), "insufficient_history")
    distribution = list(by_date.values())
    if max(distribution) == 0:
        return PercentileResult(None, len(by_date), "off_season")
    return PercentileResult(
        percentile_rank(today_peak, distribution), len(by_date), "ok"
    )


# --- consensus / divergence (cross-source) --------------------------------

# Categorical consensus vocabulary. Levels 0/1/2 map to none/low/high; "mixed"
# is genuine disagreement (sources differ by >1 level) — a number can't hold it.
# (Level 1 is "in season, below peak"; "low" is the user-facing label — see
# ANALYTICS.md. "moderate" was considered.)
CONSENSUS_NONE = "none"
CONSENSUS_LOW = "low"
CONSENSUS_HIGH = "high"
CONSENSUS_MIXED = "mixed"
CONSENSUS_OPTIONS = [CONSENSUS_NONE, CONSENSUS_LOW, CONSENSUS_HIGH, CONSENSUS_MIXED]
_LEVEL_TO_CONSENSUS = {0: CONSENSUS_NONE, 1: CONSENSUS_LOW, 2: CONSENSUS_HIGH}


@dataclass(slots=True)
class ConsensusResult:
    """Cross-source consensus for one species.

    v2.0+: single-source species are also represented (state = that source's
    own level mapped to none/low/high; `diverged` always False; the n/m badge
    on the sensor tells the user it's single-source). `source_count` is how
    many sources actually contributed; `max_possible` is the global ceiling
    from the species registry (used by the card for the badge denominator).
    """

    state: str | None        # one of CONSENSUS_OPTIONS, or None if 0 sources
    level: int | None        # 0/1/2 when single or agreed; None for mixed/0
    diverged: bool           # True only in the "mixed" case (levels differ by >1)
    source_levels: dict[str, int]  # contributing per-source levels
    source_count: int        # len(source_levels) — how many contributed now
    max_possible: int        # global ceiling from species_registry


def consensus(levels: dict[str, int], max_possible: int = 0) -> ConsensusResult:
    """Combine per-source levels (0/1/2) into a consensus.

    Equal weighting (v1.0). Tiebreak (deliberate, health-conservative — see
    ANALYTICS.md): equal → that level; adjacent (differ by 1) → the **higher**
    level; differ by >1 → "mixed".

    Source-count semantics:
    - 0 sources: state=None, level=None, source_count=0 (sensor unavailable).
    - 1 source: pass-through — state = that source's level mapped to
      none/low/high; never "mixed" (nothing to disagree with); never diverged.
      The n/m badge on the sensor tells the user this is single-source.
    - >=2 sources: existing v1 logic.

    ``max_possible`` is the registry ceiling (how many sources GLOBALLY cover
    this species); defaults to ``source_count`` if not provided (safe but
    less informative for the badge).
    """
    source_levels = dict(levels)
    source_count = len(source_levels)
    if max_possible == 0:
        max_possible = source_count or 1
    if source_count == 0:
        return ConsensusResult(None, None, False, source_levels, 0, max_possible)
    if source_count == 1:
        # Single-source pass-through. The lone source IS the consensus.
        level = next(iter(source_levels.values()))
        return ConsensusResult(
            _LEVEL_TO_CONSENSUS[level], level, False, source_levels,
            1, max_possible,
        )
    values = list(source_levels.values())
    if max(values) - min(values) > 1:
        return ConsensusResult(
            CONSENSUS_MIXED, None, True, source_levels,
            source_count, max_possible,
        )
    level = max(values)  # take-the-higher on equal/adjacent
    return ConsensusResult(
        _LEVEL_TO_CONSENSUS[level], level, False, source_levels,
        source_count, max_possible,
    )


def recent_percentile_from_series(
    times: list[str],
    values: list[float | None],
    today: str,
    *,
    window_days: int = PERCENTILE_WINDOW_DAYS,
    min_days: int = MIN_PERCENTILE_DAYS,
) -> PercentileResult:
    """recent_percentile for a source that carries its own history series.

    Daily-peaks the aligned (times, values), keeps the trailing ``window_days``
    up to and including ``today``, and ranks today within it. (Open-Meteo's
    92-day backfill path.)
    """
    peaks = [(d, p) for d, p in daily_peaks(times, values) if d <= today]
    return compute_recent_percentile(
        peaks[-window_days:], today, min_days=min_days
    )

