"""Edge scoring — direct implementations of the workbook's own primitives.

This module deliberately implements PRIM_E01–E04, E19 and the SCAN-01 tier map
*exactly as specified*, rather than assigning plausible-looking random scores.

That choice is the point of the exercise. If the generator computes
`edge_aggregate_score` with the real formula, then re-running SCAN-01 against
the loaded graph must reproduce the stored value. Any divergence is a genuine
finding about the spec — an under-determined weight, a missing input, a formula
that cannot be evaluated from the data model — rather than an artefact of made-up
numbers. Random scores would validate nothing.

Sources:
  PRIM_E01  compute_SA                     SA_base x (1/log(age_years + e))
  PRIM_E02  compute_ES_edge                static lookup by evidence type
  PRIM_E03  compute_ES_assertion           points -> band mapping
  PRIM_E04  compute_edge_aggregate_score   w_effect/w_sig/w_n weighted
  PRIM_E19  compute_evidence_recency_decay exp(-lambda x years)
  SCAN-01   TIER_MAP                       aggregate score -> quality tier
"""

from __future__ import annotations

import math
from typing import Iterable

# --- PRIM_P05 load_source_authority_table -----------------------------------
SOURCE_AUTHORITY: dict[str, float] = {
    "curated_ontology": 1.00,
    "peer_reviewed": 0.85,
    "clinical_registry": 0.85,
    "user_uploaded": 0.75,
    "preprint": 0.60,
    "user_hypothesis": 0.50,
    "llm_inference": 0.30,
    "chat": 0.30,
}

# --- PRIM_E02 compute_ES_edge (static lookup) -------------------------------
EVIDENCE_TYPE_ES: dict[str, float] = {
    "experimental_validation": 1.00,
    "replication": 0.90,
    "high_throughput": 0.75,
    "curated_db": 0.70,
    "inferred": 0.50,
    "text_mined": 0.40,
    "speculative": 0.25,
    "llm_inferred": 0.20,
}

# --- 00_Domain_Scaling_Table ------------------------------------------------
DOMAIN_SCALING: dict[str, tuple[float, float]] = {
    "oncology": (1.4, 25.0),
    "immunology": (1.1, 15.0),
    "neurodegeneration": (1.0, 12.0),
    "cardiometabolic": (1.2, 18.0),
    "gene_therapy": (0.8, 8.0),
    "rare_disease": (0.35, 4.0),
    "infectious_disease": (1.1, 14.0),
    "aging": (0.7, 7.0),
    "agriculture": (0.6, 6.0),
    "general": (1.0, 10.0),
}

CURRENT_YEAR = 2026


def compute_SA(source_type: str, publication_year: int | None = None) -> float:
    """PRIM_E01.

    `SA_decayed = SA_base x (1/log(age_years + e))`. At age 0 the divisor is
    log(e) = 1, so a current-year source keeps its full base authority and older
    ones decay sub-linearly. Clamped to [0,1] because the spec declares that range.
    """
    base = SOURCE_AUTHORITY.get(source_type, 0.50)
    if publication_year is None:
        return round(base, 4)
    age = max(0, CURRENT_YEAR - publication_year)
    return round(min(1.0, base * (1.0 / math.log(age + math.e))), 4)


def compute_ES_edge(evidence_type: str) -> float:
    """PRIM_E02 — static lookup."""
    return EVIDENCE_TYPE_ES.get(evidence_type, 0.50)


def compute_ES_assertion(adj_p: float | None, effect_size: float | None,
                         replicates: int = 0, n: int = 0) -> float:
    """PRIM_E03.

    Points: adj_p<=0.01 -> +2, <=0.05 -> +1; |effect|>=1.0 -> +2, >=0.5 -> +1;
    replicates>=3 -> +1; n>=10 -> +1.
    Map: 0-1 -> 0.20, 2-3 -> 0.55, 4-5 -> 0.80, 6 -> 1.0
    """
    pts = 0
    if adj_p is not None:
        if adj_p <= 0.01:
            pts += 2
        elif adj_p <= 0.05:
            pts += 1
    if effect_size is not None:
        mag = abs(effect_size)
        if mag >= 1.0:
            pts += 2
        elif mag >= 0.5:
            pts += 1
    if replicates >= 3:
        pts += 1
    if n >= 10:
        pts += 1

    if pts >= 6:
        return 1.00
    if pts >= 4:
        return 0.80
    if pts >= 2:
        return 0.55
    return 0.20


def _normalize(value: float, cap: float = 1.0) -> float:
    return max(0.0, min(1.0, value / cap)) if cap else 0.0


def compute_edge_aggregate_score(
    effect_size: float | None,
    adj_p: float | None,
    n: int | None,
    *,
    w_effect: float = 0.40,
    w_sig: float = 0.40,
    w_n: float = 0.20,
) -> float:
    """PRIM_E04.

    `ES_agg = w_effect x normalize(|effect|) + w_sig x normalize(-log10(adj_p), cap=10)
              + w_n x normalize(log10(n+1))`

    Note the spec leaves the effect-size and sample-size caps unstated (only the
    significance cap of 10 is given). Documented choice: |effect| capped at 2.0
    and log10(n+1) capped at 4.0 (n = 10,000). Both are recorded in
    docs/decisions.md as ADR-004 because they change every score in the graph.
    """
    eff = _normalize(abs(effect_size), 2.0) if effect_size is not None else 0.0
    sig = (
        _normalize(-math.log10(max(adj_p, 1e-300)), 10.0)
        if adj_p is not None and adj_p > 0
        else 0.0
    )
    size = _normalize(math.log10((n or 0) + 1), 4.0)
    return round(w_effect * eff + w_sig * sig + w_n * size, 4)


def compute_recency(publication_year: int | None, biological_domain: str = "general") -> float:
    """PRIM_E19 — `recency = exp(-lambda x years_since_last_publication)`.

    lambda is tuned per domain scaling factor: fast-moving fields decay faster.
    A saturation_multiplier of 1.4 (oncology) gives a shorter half-life than 0.35
    (rare disease), which is the behaviour the primitive describes.
    """
    if publication_year is None:
        return 0.5
    mult, _ = DOMAIN_SCALING.get(biological_domain, DOMAIN_SCALING["general"])
    lam = 0.05 * mult
    years = max(0, CURRENT_YEAR - publication_year)
    return round(math.exp(-lam * years), 4)


def compute_replication_score(dataset_counts: Iterable[int], min_datasets: int = 2) -> float:
    """PRIM_E11 — fraction of edges observed in >= min_datasets datasets."""
    counts = list(dataset_counts)
    if not counts:
        return 0.0
    return round(sum(1 for c in counts if c >= min_datasets) / len(counts), 4)


def compute_edge_conflict(pos: int, neg: int) -> float:
    """PRIM_E06 — `K_edge = 1 - (|pos - neg| / total)`."""
    total = pos + neg
    if total == 0:
        return 0.0
    return round(1.0 - (abs(pos - neg) / total), 4)


def compute_direction_score(pos: int, neg: int) -> float:
    """SCAN-03 DirectionScore — the signed mean of directional observations.

    `DirectionScore = sum(d_i) / n`, where d_i is +1 for activation, -1 for
    inhibition, 0 for neutral. From the requirements doc S3, worked as
    7 activations vs 1 inhibition -> (7-1)/8 = 0.75.

    Range [-1, 1]. The SIGN is the biological direction; the MAGNITUDE is the
    strength of agreement.
    """
    total = pos + neg
    return 0.0 if total == 0 else round((pos - neg) / total, 4)


def conflict_severity(pos: int, neg: int) -> float:
    """SCAN-03 Conflict Severity = 1 - |DirectionScore|.

    0 = complete agreement, ~1 = even split. Algebraically identical to
    PRIM_E06 compute_edge_conflict, which is a useful consistency check between
    the two documents.
    """
    return round(1.0 - abs(compute_direction_score(pos, neg)), 4)


def concordance_flag(pos: int, neg: int) -> tuple[float, str]:
    """SCAN-03 directional concordance tier.

    Returns (DirectionScore, flag) using |DirectionScore|:
      >= 0.50  CONCORDANT
      >= 0.30  CONTEXT_DEPENDENT
      <  0.30  CONTRADICTORY

    ---------------------------------------------------------------------------
    SPEC CONFLICT — the workbook and the requirements doc disagree, and the
    workbook's version is degenerate.

    PRIM_E05 in 00_Primitive_Library reads:

        concordance = count(consistent_direction) / count(all_observations)

    i.e. max(pos, neg) / total. That quantity is **>= 0.50 by construction** —
    the majority of a two-way split is never less than half. Against the stated
    thresholds it means CONTEXT_DEPENDENT and CONTRADICTORY can never fire.
    A perfectly conflicted edge (5 activations, 5 inhibitions) scores 0.50 and
    is reported CONCORDANT.

    That is not a rounding quibble. It disables SCAN-03's entire downstream
    cascade: CONTEXT_DEPENDENT is the trigger for SEM-SCAN-10 (Contextual
    Mechanism Switching) and CONTRADICTORY is the trigger for INV-03
    (Contradiction Arbitrator). Under PRIM_E05 as written, both are dead code.

    This implements the requirements-doc formula, which is well-behaved and
    whose worked example reproduces exactly. PRIM_E05 should be corrected to
    match. Verified empirically: before this fix, all 232,215 generated edges
    were CONCORDANT and neither other tier appeared anywhere in the graph.
    ---------------------------------------------------------------------------
    """
    total = pos + neg
    if total == 0:
        return 0.0, "CONTRADICTORY"
    score = compute_direction_score(pos, neg)
    magnitude = abs(score)
    if magnitude >= 0.50:
        return score, "CONCORDANT"
    if magnitude >= 0.30:
        return score, "CONTEXT_DEPENDENT"
    return score, "CONTRADICTORY"


def compute_support_saturation(observed: int, max_possible: int) -> float:
    """PRIM_E08 / PRIM_N06 — `log(1 + 5 x raw) / log(6)`, raw = min(1, obs/max)."""
    if max_possible <= 0:
        return 0.0
    raw = min(1.0, observed / max_possible)
    return round(math.log(1 + 5 * raw) / math.log(6), 4)


def compute_scan01_consolidated_score(
    SA: float, ES: float, recency: float, replication: float,
    *, w_SA: float = 0.30, w_ES: float = 0.25, w_recency: float = 0.35,
    w_replication: float = 0.10,
) -> float:
    """SCAN-01's four-factor consolidation, per the requirements doc.

    Weight profile from the SCAN-01 sheet: w1=0.30 (source authority),
    w2=0.25 (effect size / ES), w3=0.35 (recency), w4=0.10 (replication).

    Validated against the worked example in "Requirment Report Scans.docx" S3
    (Metformin activates AMPK):
        (0.95 x 0.30) + (0.90 x 0.35) + (0.85 x 0.25) + (0.80 x 0.10) = 0.8925

    SPEC INCONSISTENCY (see docs/decisions.md open questions). The workbook's
    SCAN-01 formula row reads:

        score(e) = E04(w1xSA + w2xES + w3xrecency + w4xreplication)

    i.e. PRIM_E04 applied to the weighted sum. But PRIM_E04's signature is
    `compute_edge_aggregate_score(effect_size, adj_p, n)` — it consumes three
    raw statistics, not a single pre-combined scalar, so the composition as
    written is not evaluable. The requirements doc's own worked example drops
    the E04 wrapper and returns the weighted sum directly, which is what this
    implements. The workbook should either change the formula row or redefine
    E04's signature.
    """
    total = w_SA + w_ES + w_recency + w_replication
    if abs(total - 1.0) > 0.001:  # PRIM_P11 validate_weight_profile
        raise ValueError(f"weight profile must sum to 1.0, got {total}")
    return round(
        SA * w_SA + ES * w_ES + recency * w_recency + replication * w_replication, 4
    )


# --- SCAN-01 TIER_MAP -------------------------------------------------------
TIERS: tuple[tuple[float, float, str], ...] = (
    (0.85, 1.01, "Tier 1 (Validated Consensus)"),
    (0.60, 0.85, "Tier 2 (Supported)"),
    (0.40, 0.60, "Tier 3 (Exploratory)"),
    (0.00, 0.40, "Tier 4 (Speculative)"),
)

MIN_INGESTION_THRESHOLD = 0.10  # SCAN-01 low-pass filter, cited by SCAN-03


def edge_quality_tier(aggregate_score: float) -> str:
    for lo, hi, label in TIERS:
        if lo <= aggregate_score < hi:
            return label
    return "Tier 4 (Speculative)"


def passes_ingestion_threshold(aggregate_score: float) -> bool:
    """SCAN-01's 0.10 minimum. Edges below it are classified as noise."""
    return aggregate_score > MIN_INGESTION_THRESHOLD
