"""Graph capability checks, and the primitive -> capability mapping.

A scan is "ready" when every primitive it declares in the workbook can actually
be evaluated against the loaded graph. Each primitive is mapped to one or more
*capabilities*, and each capability is a concrete query with a pass condition.

Where a capability genuinely cannot be satisfied by a property graph — vector
embeddings being the obvious case — it is marked `UNSUPPORTED` rather than
quietly passed. Those are real findings about what the scan layer needs beyond
Neo4j, not failures of the data generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    cypher: str
    passes: Callable[[dict | None], bool]
    unsupported_reason: str | None = None
    detail: Callable[[dict | None], str] = lambda r: str(r)


def _nonzero(key: str, minimum: int = 1) -> Callable[[dict | None], bool]:
    return lambda r: bool(r) and (r.get(key) or 0) >= minimum


CAPABILITIES: dict[str, Capability] = {
    # --- structural -------------------------------------------------------
    "topology": Capability(
        "topology", "Graph has enough nodes and directed edges to traverse",
        "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() "
        "RETURN nodes, count(r) AS rels",
        lambda r: bool(r) and r["nodes"] > 100 and r["rels"] > 500,
        detail=lambda r: f"{r['nodes']:,} nodes / {r['rels']:,} rels",
    ),
    "node_metadata": Capability(
        "node_metadata", "PRIM_N08 six mandatory fields present on every node",
        "MATCH (n) WHERE n.entity_type IS NULL OR n.canonical_id IS NULL "
        "OR n.synonyms IS NULL OR n.ontology_refs IS NULL OR n.created_at IS NULL "
        "RETURN count(n) AS bad",
        lambda r: bool(r) and r["bad"] == 0,
        detail=lambda r: f"{r['bad']} nodes missing N08 fields",
    ),
    "provenance_fields": Capability(
        "provenance_fields", "source / is_synthetic / temporal validity on all nodes",
        "MATCH (n) WHERE n.source IS NULL OR n.is_synthetic IS NULL "
        "OR n.valid_from IS NULL OR n.updated_at IS NULL RETURN count(n) AS bad",
        lambda r: bool(r) and r["bad"] == 0,
        detail=lambda r: f"{r['bad']} nodes missing provenance/temporal fields",
    ),
    # --- edge scoring -----------------------------------------------------
    "edge_scores": Capability(
        "edge_scores", "PRIM_E01/E02/E04 inputs on edges",
        "MATCH ()-[r]->() WHERE r.SA IS NOT NULL AND r.ES_edge IS NOT NULL "
        "AND r.edge_aggregate_score IS NOT NULL RETURN count(r) AS n",
        _nonzero("n", 500), detail=lambda r: f"{r['n']:,} scored edges",
    ),
    "edge_tiers": Capability(
        "edge_tiers", "SCAN-01 quality tiers span more than one band",
        "MATCH ()-[r]->() WHERE r.edge_quality_tier IS NOT NULL "
        "RETURN count(DISTINCT r.edge_quality_tier) AS tiers, count(r) AS n",
        lambda r: bool(r) and r["tiers"] >= 2,
        detail=lambda r: f"{r['tiers']} distinct tiers over {r['n']:,} edges",
    ),
    "edge_conflict": Capability(
        "edge_conflict", "PRIM_E05/E06/E07 need pos/neg observation counts",
        "MATCH ()-[r]->() WHERE r.pos_count IS NOT NULL AND r.neg_count > 0 "
        "RETURN count(r) AS n",
        _nonzero("n", 100), detail=lambda r: f"{r['n']:,} edges with contradiction",
    ),
    "edge_direction": Capability(
        "edge_direction", "SCAN-03 needs signed direction on edges",
        "MATCH ()-[r]->() WHERE r.direction IN [-1,1] RETURN count(r) AS n",
        _nonzero("n", 100), detail=lambda r: f"{r['n']:,} directional edges",
    ),
    "edge_provenance": Capability(
        "edge_provenance", "PRIM_E15 provenance chain fields",
        "MATCH ()-[r]->() WHERE r.dataset_ids IS NOT NULL AND r.source_type IS NOT NULL "
        "AND r.publication_year IS NOT NULL AND r.formula_version IS NOT NULL "
        "RETURN count(r) AS n",
        _nonzero("n", 500), detail=lambda r: f"{r['n']:,} edges with full provenance",
    ),
    "edge_modality": Capability(
        "edge_modality", "PRIM_E16/E17 modality coverage and concordance",
        "MATCH ()-[r]->() WHERE r.modality IS NOT NULL "
        "RETURN count(DISTINCT r.modality) AS m, count(r) AS n",
        lambda r: bool(r) and r["m"] >= 3,
        detail=lambda r: f"{r['m']} modalities over {r['n']:,} edges",
    ),
    "edge_recency": Capability(
        "edge_recency", "PRIM_E19 needs a spread of publication years",
        "MATCH ()-[r]->() WHERE r.publication_year IS NOT NULL "
        "RETURN count(DISTINCT r.publication_year) AS years, "
        "min(r.publication_year) AS lo, max(r.publication_year) AS hi",
        lambda r: bool(r) and r["years"] >= 8,
        detail=lambda r: f"{r['years']} distinct years ({r['lo']}-{r['hi']})",
    ),
    # --- evidence layer ---------------------------------------------------
    "datasets": Capability(
        "datasets", "PRIM_E13/T17 need Dataset nodes",
        "MATCH (d:Dataset) RETURN count(d) AS n",
        _nonzero("n", 10), detail=lambda r: f"{r['n']} datasets",
    ),
    "cohorts": Capability(
        "cohorts", "PRIM_N10 needs paired disease/control cohorts",
        "MATCH (c:Cohort) RETURN count(DISTINCT c.cohort_state) AS states, count(c) AS n",
        lambda r: bool(r) and r["states"] >= 2,
        detail=lambda r: f"{r['n']} cohorts, {r['states']} states",
    ),
    "assertions": Capability(
        "assertions", "PRIM_S16/I04/H03 need Assertion nodes with promotion decisions",
        "MATCH (a:Assertion) RETURN count(a) AS n, "
        "count(DISTINCT a.promotion_decision) AS decisions",
        lambda r: bool(r) and r["n"] > 50 and r["decisions"] >= 2,
        detail=lambda r: f"{r['n']} assertions, {r['decisions']} decision classes",
    ),
    "evidence_modality": Capability(
        "evidence_modality", "SCAN-10 cross-modality concordance",
        "MATCH (e:Evidence) RETURN count(DISTINCT e.modality) AS m, count(e) AS n",
        lambda r: bool(r) and r["m"] >= 4, detail=lambda r: f"{r['m']} modalities / {r['n']} evidence",
    ),
    "evidence_stage": Capability(
        "evidence_stage", "PRIM_E21 stage-stratified effect (SCAN-33)",
        "MATCH (e:Evidence) WHERE e.disease_stage IS NOT NULL "
        "RETURN count(DISTINCT e.disease_stage) AS s, count(e) AS n",
        lambda r: bool(r) and r["s"] >= 2, detail=lambda r: f"{r['s']} stages / {r['n']} evidence",
    ),
    "evidence_batch": Capability(
        "evidence_batch", "PRIM_E22 batch/platform/lab variance (SCAN-35)",
        "MATCH (e:Evidence) WHERE e.batch_id IS NOT NULL AND e.platform IS NOT NULL "
        "AND e.lab_id IS NOT NULL RETURN count(e) AS n",
        _nonzero("n", 50), detail=lambda r: f"{r['n']} evidence with batch metadata",
    ),
    "clinical_evidence": Capability(
        "clinical_evidence", "CLIN_02 distinguishes clinical from preclinical",
        "MATCH (e:Evidence) WHERE e.is_clinical = true RETURN count(e) AS n",
        _nonzero("n", 10), detail=lambda r: f"{r['n']} clinical evidence records",
    ),
    "publications": Capability(
        "publications", "SCAN-11 provenance chain terminates in Publication",
        "MATCH (p:Publication) RETURN count(p) AS n, "
        "count(DISTINCT p.source_type) AS types",
        lambda r: bool(r) and r["n"] > 50, detail=lambda r: f"{r['n']} pubs, {r['types']} source types",
    ),
    # --- genetics ---------------------------------------------------------
    "variant_stats": Capability(
        "variant_stats", "PRIM_G01 mandatory GeneticVariant attributes",
        "MATCH (v:Variant) WHERE v.chromosome IS NOT NULL AND v.base_position IS NOT NULL "
        "AND v.p_raw IS NOT NULL AND v.effect_weight IS NOT NULL RETURN count(v) AS n",
        _nonzero("n", 100), detail=lambda r: f"{r['n']:,} variants with full G01 attributes",
    ),
    "variant_locus": Capability(
        "variant_locus", "PRIM_G01 MAPS_TO_LOCUS edge to Gene",
        "MATCH (:Variant)-[r:MAPS_TO_LOCUS]->(:Gene) RETURN count(r) AS n",
        _nonzero("n", 10), detail=lambda r: f"{r['n']:,} maps_to_locus edges",
    ),
    "qtl": Capability(
        "qtl", "SCAN-18 needs eQTL/pQTL/mQTL edges",
        "MATCH ()-[r]->() WHERE type(r) IN ['eQTL_MODULATES','pQTL_MODULATES','mQTL_MODULATES'] "
        "RETURN count(r) AS n, count(DISTINCT type(r)) AS kinds",
        lambda r: bool(r) and r["n"] > 50, detail=lambda r: f"{r['n']:,} QTL edges, {r['kinds']} kinds",
    ),
    "orthologs": Capability(
        "orthologs", "PRIM_T15/R22 cross-species conservation",
        "MATCH (s:Species) WITH count(s) AS species "
        "MATCH ()-[r]->() WHERE type(r) IN ['ORTHOLOG_OF','CONSERVED_IN'] "
        "RETURN species, count(r) AS n",
        lambda r: bool(r) and r["species"] >= 2 and r["n"] > 10,
        detail=lambda r: f"{r['species']} species, {r['n']:,} conservation edges",
    ),
    # --- hypothesis / governance -----------------------------------------
    "hypotheses": Capability(
        "hypotheses", "PRIM_H05 hypothesis context fields",
        "MATCH (h:SkygenicHypothesis) WHERE h.biological_domain IS NOT NULL "
        "AND h.decision_context IS NOT NULL RETURN count(h) AS n, "
        "count(DISTINCT h.biological_domain) AS domains",
        lambda r: bool(r) and r["n"] >= 10, detail=lambda r: f"{r['n']} hypotheses, {r['domains']} domains",
    ),
    "hypothesis_subgraph": Capability(
        "hypothesis_subgraph", "PRIM_T09 needs typed hypothesis membership (gap G-10)",
        "MATCH (:SkygenicHypothesis)-[r:HYPOTHESIS_INCLUDES_NODE]->() RETURN count(r) AS n",
        _nonzero("n", 20), detail=lambda r: f"{r['n']:,} membership edges",
    ),
    "version_history": Capability(
        "version_history", "PRIM_R08/H02 need >=2 ordered versions per hypothesis",
        "MATCH (h:SkygenicHypothesis)-[:HAS_VERSION]->(v:HypothesisVersion) "
        "WITH h, count(v) AS vc WHERE vc >= 2 RETURN count(h) AS n, avg(vc) AS avg_versions",
        _nonzero("n", 5),
        detail=lambda r: f"{r['n']} hypotheses with history (avg {r['avg_versions']:.1f} versions)",
    ),
    "mc_trajectory": Capability(
        "mc_trajectory", "SCAN-30/31 need MC deltas including a converged run",
        "MATCH (v:HypothesisVersion) WHERE v.delta_MC IS NOT NULL "
        "RETURN count(v) AS n, sum(CASE WHEN abs(v.delta_MC) < 0.02 THEN 1 ELSE 0 END) AS converged",
        lambda r: bool(r) and r["n"] > 20 and r["converged"] > 0,
        detail=lambda r: f"{r['n']} deltas, {r['converged']} below convergence threshold",
    ),
    "confidence_components": Capability(
        "confidence_components", "PRIM_R06 C/dS/R/P/K components",
        "MATCH (c:ConfidenceScore) WHERE c.component_C IS NOT NULL "
        "AND c.component_dS IS NOT NULL AND c.component_R IS NOT NULL "
        "AND c.component_P IS NOT NULL AND c.component_K IS NOT NULL RETURN count(c) AS n",
        _nonzero("n", 5), detail=lambda r: f"{r['n']} full MC component sets",
    ),
    "lifecycle": Capability(
        "lifecycle", "SCAN-20 lifecycle states",
        "MATCH (l:LifecycleState) RETURN count(DISTINCT l.name) AS states, count(l) AS n",
        lambda r: bool(r) and r["states"] >= 3, detail=lambda r: f"{r['states']} distinct states",
    ),
    "reasoning_chains": Capability(
        "reasoning_chains", "SCAN-21 explainability",
        "MATCH (c:ReasoningChain) WHERE c.path_traceability IS NOT NULL RETURN count(c) AS n",
        _nonzero("n", 5), detail=lambda r: f"{r['n']} reasoning chains",
    ),
    "biological_state": Capability(
        "biological_state", "PRIM_S07/T16 versioned state + snapshot hash",
        "MATCH (b:BiologicalState) WHERE b.snapshot_hash IS NOT NULL RETURN count(b) AS n",
        _nonzero("n", 5), detail=lambda r: f"{r['n']} states",
    ),
    "scan_results": Capability(
        "scan_results", "PRIM_S10 persisted results with version triplet",
        "MATCH (s:ScanResult) WHERE s.Dv IS NOT NULL AND s.Tv IS NOT NULL "
        "AND s.Hv IS NOT NULL AND s.snapshot_hash IS NOT NULL RETURN count(s) AS n",
        _nonzero("n", 20), detail=lambda r: f"{r['n']:,} scan results",
    ),
    "audit_log": Capability(
        "audit_log", "PRIM_S13 append-only audit events",
        "MATCH (a:AuditEvent) RETURN count(a) AS n, count(DISTINCT a.event_type) AS types",
        lambda r: bool(r) and r["n"] > 20, detail=lambda r: f"{r['n']:,} events, {r['types']} types",
    ),
    # --- context ----------------------------------------------------------
    "tissues": Capability(
        "tissues", "SCAN-08 interaction context",
        "MATCH (t:Tissue) RETURN count(t) AS n",
        _nonzero("n", 5), detail=lambda r: f"{r['n']} tissues",
    ),
    "cell_types": Capability(
        "cell_types", "ONC_05/IMM_01 need CellType (gap G-07)",
        "MATCH (c:CellType) RETURN count(c) AS n",
        _nonzero("n", 3), detail=lambda r: f"{r['n']} cell types",
    ),
    "ontology_dag": Capability(
        "ontology_dag", "SCAN-25/27 semantic distance needs a term hierarchy",
        "MATCH (:OntologyTerm)-[r:ONTOLOGICALLY_INCLUDES]->(:OntologyTerm) RETURN count(r) AS n",
        _nonzero("n", 10), detail=lambda r: f"{r['n']:,} parent-child edges",
    ),
    "drug_directional": Capability(
        "drug_directional", "SCAN-03/29 need signed drug-target edges",
        "MATCH ()-[r]->() WHERE type(r) IN "
        "['PHARMACOLOGICALLY_ACTIVATES','PHARMACOLOGICALLY_INHIBITS'] RETURN count(r) AS n",
        _nonzero("n", 20), detail=lambda r: f"{r['n']:,} directional drug edges",
    ),
    "off_target": Capability(
        "off_target", "SCAN-28 off-target risk",
        "MATCH ()-[r]->() WHERE type(r) IN "
        "['EXERTS_OFF_TARGET_EFFECT','OFF_TARGET_INTERACTION'] RETURN count(r) AS n",
        _nonzero("n", 10), detail=lambda r: f"{r['n']:,} off-target edges",
    ),
    "compound_fingerprints": Capability(
        "compound_fingerprints", "SIM_03 Tanimoto on Morgan fingerprints",
        "MATCH (c:Compound) WHERE c.morgan_fp IS NOT NULL RETURN count(c) AS n",
        _nonzero("n", 20), detail=lambda r: f"{r['n']:,} compounds with fingerprints",
    ),
    "gene_protein_link": Capability(
        "gene_protein_link", "Gap G-01: Gene->Protein must connect the two halves",
        "MATCH (:Gene)-[r:ENCODES]->(:Protein) RETURN count(r) AS n",
        _nonzero("n", 20), detail=lambda r: f"{r['n']:,} ENCODES edges",
    ),
    # --- unsupported ------------------------------------------------------
    "agriculture_data": Capability(
        "agriculture_data", "Plant entity types for the AG-SCAN family",
        "RETURN 0 AS n",
        lambda r: False,
        unsupported_reason=(
            "Agriculture is out of scope for this build (ADR-002). source_registry.yaml "
            "excludes CROP_SPECIES, PLANT_GENE, PLANT_DISEASE, PLANT_PATHWAY, "
            "AGROCHEMICAL and QTL_PLANT. The EDIT_*, BREED_*, EPI_* and MICRO_* "
            "primitive families exist in the workbook but have no entity types to "
            "operate on."
        ),
        detail=lambda r: "out of scope",
    ),
    "embeddings": Capability(
        "embeddings", "PRIM_V01-V16 vector layer",
        "RETURN 0 AS n",
        lambda r: False,
        unsupported_reason=(
            "No vector store. PRIM_V01-V16 (generate/store/search embeddings, cosine "
            "similarity, novelty baselines) cannot run against a property graph alone. "
            "Every semantic scan depends on this. Requires a vector index decision — "
            "Neo4j 2026 native vector index, or an external store (Qdrant is already "
            "running on this machine)."
        ),
        detail=lambda r: "not implemented",
    ),
}


# --- primitive -> capabilities ------------------------------------------------
PRIMITIVE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    # node
    "N01": ("topology",), "N02": ("topology",), "N03": ("topology",),
    "N04": ("topology",), "N05": ("topology",),
    "N06": ("edge_scores",), "N07": ("node_metadata",), "N08": ("node_metadata",),
    "N09": ("topology",), "N10": ("cohorts", "topology"),
    # edge
    "E01": ("edge_provenance", "edge_recency"), "E02": ("edge_scores",),
    "E03": ("edge_scores",), "E04": ("edge_scores",), "E05": ("edge_conflict",),
    "E06": ("edge_conflict",), "E07": ("edge_conflict",), "E08": ("edge_scores",),
    "E09": ("edge_scores",), "E10": ("embeddings",), "E11": ("edge_provenance", "datasets"),
    "E12": ("topology",), "E13": ("datasets",), "E14": ("edge_scores",),
    "E15": ("edge_provenance", "assertions"), "E16": ("edge_modality",),
    "E17": ("edge_modality",), "E18": ("edge_direction",),
    "E19": ("edge_recency",), "E20": ("edge_scores",),
    "E21": ("evidence_stage",), "E22": ("evidence_batch",),
    # traversal
    "T01": ("topology",), "T02": ("topology",), "T03": ("topology",), "T04": ("topology",),
    "T05": ("topology",), "T06": ("topology",), "T07": ("topology", "edge_direction"),
    "T08": ("topology",), "T09": ("hypothesis_subgraph",), "T10": ("topology",),
    "T11": ("topology",), "T12": ("topology", "edge_direction"), "T13": ("topology",),
    "T14": ("topology",), "T15": ("orthologs",), "T16": ("biological_state",),
    "T17": ("datasets",), "T18": ("biological_state",), "T19": ("node_metadata",),
    "T20": ("topology",), "T21": ("topology",),
    # reasoning
    "R01": ("embeddings",), "R02": ("embeddings",), "R03": ("embeddings",),
    "R04": ("embeddings",), "R05": ("embeddings",),
    "R06": ("confidence_components",), "R07": ("topology", "edge_scores"),
    "R08": ("version_history", "mc_trajectory"), "R09": ("topology",),
    "R10": ("topology", "edge_scores"), "R11": ("embeddings",), "R12": ("topology",),
    "R13": ("edge_scores",), "R14": ("embeddings",), "R15": ("topology", "edge_scores"),
    "R16": ("embeddings",), "R17": ("topology",), "R18": ("topology",),
    "R19": ("variant_stats", "qtl"), "R20": ("cohorts", "variant_stats"),
    "R21": ("qtl",), "R22": ("orthologs",), "R23": ("edge_scores",),
    # vector
    **{f"V{i:02d}": ("embeddings",) for i in range(1, 17)},
    # state
    "S01": ("provenance_fields",), "S02": ("provenance_fields",),
    "S03": ("version_history",), "S04": ("edge_scores",), "S05": ("topology",),
    "S06": ("confidence_components",), "S07": ("biological_state",),
    "S08": ("confidence_components",), "S09": ("version_history",),
    "S10": ("scan_results",), "S11": ("mc_trajectory",), "S12": ("embeddings",),
    "S13": ("audit_log",), "S14": ("hypotheses",), "S15": ("hypotheses",),
    "S16": ("assertions",), "S17": ("assertions",), "S18": ("assertions",),
    "S19": ("hypotheses",), "S20": ("hypotheses",), "S21": ("biological_state",),
    "S22": ("hypotheses",), "S20H": ("topology",),
    # policy — config tables, always satisfiable
    **{f"P{i:02d}": () for i in range(1, 17)},
    # ingestion
    "I01": ("datasets",), "I02": ("assertions",), "I03": ("node_metadata",),
    "I04": ("assertions",), "I05": ("assertions",), "I06": ("edge_conflict",),
    "I07": ("datasets",), "I08": ("assertions",), "I09": ("topology",),
    "I10": ("edge_modality",),
    # genetics
    "G01": ("variant_stats", "variant_locus"), "G02": ("variant_stats", "cohorts"),
    "G03": ("qtl",), "G04": ("variant_stats",), "G05": ("cohorts", "variant_stats"),
    # hypothesis
    "H01": ("node_metadata",), "H02": ("version_history", "mc_trajectory"),
    "H03": ("assertions",), "H04": ("topology",), "H05": ("hypotheses",),
    "H06": ("scan_results",),
    # temporal
    "TEMP_01": ("version_history",), "TEMP_02": ("edge_recency",),
    "TEMP_03": ("embeddings",),
    # semantic. The workbook's scan sheets cite SEM_07..SEM_10 while the
    # primitive library names them PRIM_SEM_07..10; both spellings map here.
    **{f"SEM_{i:02d}": ("embeddings",) for i in (1, 2, 5)},
    "SEM_03": ("edge_conflict",), "SEM_04": ("hypotheses",), "SEM_06": ("hypotheses",),
    "SEM_07": ("edge_scores",), "SEM_08": ("embeddings",),
    "SEM_09": ("edge_scores",), "SEM_10": ("topology", "edge_direction"),
    "PRIM_SEM_07": ("edge_scores",), "PRIM_SEM_08": ("embeddings",),
    "PRIM_SEM_09": ("edge_scores",), "PRIM_SEM_10": ("topology", "edge_direction"),

    # SCAN-34 declares no computational primitive: it is a pure meta-aggregator
    # that reads the persisted outputs of SCAN-10, 12, 29 and 30. Its real
    # requirement is therefore that prior scan results exist and are queryable —
    # which also makes it a genuine DAG dependency for the orchestrator.
    "READ": ("scan_results",),

    # Agriculture primitive families — out of scope (ADR-002).
    **{f"EDIT_{i:02d}": ("agriculture_data",) for i in range(1, 5)},
    **{f"BREED_{i:02d}": ("agriculture_data",) for i in range(1, 7)},
    **{f"EPI_{i:02d}": ("agriculture_data",) for i in range(1, 5)},
    **{f"MICRO_{i:02d}": ("agriculture_data",) for i in range(1, 5)},
    # clinical
    "CLIN_01": ("ontology_dag",), "CLIN_02": ("clinical_evidence",),
    "CLIN_03": ("off_target",), "CLIN_04": ("clinical_evidence", "orthologs"),
    # similarity
    "SIM_01": ("topology",), "SIM_02": ("drug_directional",),
    "SIM_03": ("compound_fingerprints",), "SIM_04": ("topology",),
    "SIM_05": ("topology",), "SIM_06": ("embeddings",), "SIM_07": ("embeddings",),
    "SIM_08": ("topology",),
    # domain families — all need core topology plus their context data
    **{f"ONC_{i:02d}": ("topology", "cell_types") for i in range(1, 7)},
    **{f"IMM_{i:02d}": ("topology", "cell_types") for i in range(1, 7)},
    **{f"NEURO_{i:02d}": ("topology", "tissues") for i in range(1, 6)},
    **{f"CARDIO_{i:02d}": ("topology", "tissues") for i in range(1, 5)},
    **{f"GENE_{i:02d}": ("topology", "clinical_evidence") for i in range(1, 6)},
    **{f"RARE_{i:02d}": ("variant_stats", "cohorts") for i in range(1, 5)},
    **{f"INF_{i:02d}": ("topology", "orthologs") for i in range(1, 5)},
    **{f"AGE_{i:02d}": ("topology", "tissues") for i in range(1, 5)},
}


def capabilities_for(primitive: str) -> tuple[str, ...]:
    """Resolve a primitive code (PRIM_N01, N01, NEURO_01) to capability names."""
    key = primitive.replace("PRIM_", "") if primitive.startswith("PRIM_") else primitive
    if key in PRIMITIVE_CAPABILITIES:
        return PRIMITIVE_CAPABILITIES[key]
    if primitive in PRIMITIVE_CAPABILITIES:
        return PRIMITIVE_CAPABILITIES[primitive]
    return ("topology",)  # conservative default: at minimum it needs a graph
