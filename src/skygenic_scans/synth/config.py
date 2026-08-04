"""Scale and shape parameters for synthetic expansion.

Targets ~50k nodes / ~250k edges (the agreed sizing). Counts are chosen so the
graph is not merely large but *structurally adequate* for the scans: enough
hypotheses to compare (SCAN-19), enough version history to trend (SCAN-30),
enough contradiction to make K non-zero (SCAN-26), enough cross-species coverage
to score conservation (SCAN-16).
"""

from __future__ import annotations

from dataclasses import dataclass, field

RANDOM_SEED = 20260801  # fixed: the whole build must be reproducible


@dataclass(frozen=True)
class ScaleConfig:
    # --- biological core --------------------------------------------------
    genes: int = 6_000
    proteins: int = 5_000
    protein_complexes: int = 800
    biological_processes: int = 2_500
    pathways: int = 2_000
    variants: int = 5_000
    diseases: int = 1_500
    phenotypes: int = 1_200
    traits: int = 500
    drugs: int = 700
    compounds: int = 1_500
    ontology_terms: int = 6_000

    # --- evidence / provenance layer --------------------------------------
    publications: int = 4_000
    evidence: int = 5_000
    assertions: int = 6_000
    datasets: int = 300
    cohorts: int = 200

    # --- hypothesis / governance layer ------------------------------------
    hypotheses: int = 120
    versions_per_hypothesis: tuple[int, int] = (4, 12)
    biological_states: int = 200
    scan_results: int = 2_000
    audit_events: int = 1_500

    # --- edge budget ------------------------------------------------------
    # Hard ceiling on total edges. The per-family degrees below set the
    # *relative* density of each relationship family; the builder then scales
    # them uniformly so the total lands on this number.
    #
    # Without a global budget the degrees compound badly: they are applied per
    # (type, from_label, to_label) endpoint pair, and several types share a
    # source label — five distinct Gene->Gene types at degree 4.5 over 6,000
    # genes is 135k edges from that one shape alone. First full build overshot
    # to 691k against a 250k target.
    #
    # The ceiling is not cosmetic. Exact betweenness (PRIM_N02) is O(V*E); at
    # 53k nodes it is tractable at 250k edges and not at 691k. The primitive's
    # own note to "cache aggressively per graph_scope + Tv" is a hint that the
    # spec authors already knew this.
    target_edges: int = 250_000

    # --- edge density controls -------------------------------------------
    # Mean out-degree per relationship family, interpreted as a *relative*
    # weight and rescaled to the budget above. Kept heavy-tailed, which is what
    # real biological networks look like and what betweenness needs in order to
    # discriminate at all.
    gene_gene_degree: float = 4.5
    gene_pathway_degree: float = 3.0
    gene_tissue_degree: float = 6.0
    protein_protein_degree: float = 4.0
    variant_gene_degree: float = 1.6
    disease_gene_degree: float = 6.0
    drug_protein_degree: float = 2.5
    provenance_degree: float = 2.0

    # Fraction of edges that carry a contradicting observation. Without this the
    # K term of PRIM_R06 is identically zero and SCAN-26/INF-SCAN-02 are untested.
    contradiction_rate: float = 0.12
    # Fraction of two-hop paths deliberately left un-bridged, so SCAN-05
    # (Mechanistic Gap Detection) and PRIM_T06 have real gaps to find.
    gap_rate: float = 0.18
    # Fraction of drug-protein edges that are off-target (SCAN-28).
    off_target_rate: float = 0.22

    modalities: tuple[str, ...] = (
        "rna_seq", "proteomics", "crispr_screen", "clinical_trial", "gwas",
        "imaging", "metabolomics", "single_cell", "in_vitro", "in_vivo",
    )
    source_types: tuple[str, ...] = (
        "curated_ontology", "peer_reviewed", "clinical_registry", "user_uploaded",
        "preprint", "user_hypothesis", "llm_inference", "chat",
    )
    # Realistic mixture — most real edges come from curated DBs and literature,
    # with a long thin tail of low-authority sources. A uniform mixture would
    # make SCAN-01's tier distribution meaningless.
    source_type_weights: tuple[float, ...] = (0.22, 0.38, 0.10, 0.08, 0.12, 0.05, 0.03, 0.02)

    evidence_types: tuple[str, ...] = (
        "experimental_validation", "replication", "high_throughput", "curated_db",
        "inferred", "text_mined", "speculative", "llm_inferred",
    )
    evidence_type_weights: tuple[float, ...] = (0.12, 0.10, 0.18, 0.24, 0.14, 0.14, 0.05, 0.03)

    domains: tuple[str, ...] = (
        "oncology", "immunology", "neurodegeneration", "cardiometabolic",
        "gene_therapy", "rare_disease", "infectious_disease", "aging",
    )
    decision_contexts: tuple[str, ...] = (
        "fundraising", "grant_submission", "portfolio_decision", "indication_expansion",
        "pre_ind", "partnership", "publication", "discovery",
    )
    lifecycle_states: tuple[str, ...] = (
        "Draft", "Active", "Submitted", "Defended", "Archived",
    )
    query_types: tuple[str, ...] = ("exploratory", "refinement", "validation", "clinical")

    # Publication-year distribution. Skewed recent, with a real tail back to
    # 2005 so PRIM_E01's authority decay and PRIM_E19's recency term both have
    # a spread to act on rather than a single cohort.
    year_range: tuple[int, int] = (2005, 2026)
    year_mode: int = 2021


DEFAULT_SCALE = ScaleConfig()


@dataclass(frozen=True)
class SmokeScale(ScaleConfig):
    """Tiny config for fast iteration on the loader and validators."""

    # Must be overridden, not inherited. Leaving it at the full-scale 250k made
    # the budget non-binding at smoke size, so smoke built 85k edges over 2.8k
    # nodes — denser than the full graph it is supposed to approximate, which
    # defeats the point of a fast iteration config.
    target_edges: int = 25_000

    genes: int = 300
    proteins: int = 250
    protein_complexes: int = 40
    biological_processes: int = 120
    pathways: int = 100
    variants: int = 250
    diseases: int = 80
    phenotypes: int = 60
    traits: int = 30
    drugs: int = 40
    compounds: int = 60
    ontology_terms: int = 300
    publications: int = 200
    evidence: int = 250
    assertions: int = 300
    datasets: int = 25
    cohorts: int = 20
    hypotheses: int = 12
    biological_states: int = 15
    scan_results: int = 100
    audit_events: int = 80
