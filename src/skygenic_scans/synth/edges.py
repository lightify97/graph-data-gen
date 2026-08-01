"""Builds every relationship in the schema, real-data-first.

Two passes:

1. **Seed-driven.** Relationship rows with genuine upstream evidence are built
   from it — OpenTargets associations, STRING interactions, Reactome membership,
   GTEx expression, GWAS variant links, ontology parentage. These carry
   `is_synthetic=False` and a real `source_url`.
2. **Synthetic filler.** Every remaining row in the schema gets generated edges
   at the configured degree, so no relationship type is left with zero instances
   and every scan has something to run against.

Duplicates and inverse pairs from the Nodes doc are built *deliberately*, not by
accident: loading them is how the centrality distortion gets measured.
"""

from __future__ import annotations

import random
from typing import Any, Callable, Sequence

from ..provenance import Provenance, VersionTriplet, stamp_edge
from ..schema import RelSpec, Schema
from .entities import BuildContext, EntityBuilder, seed_prov, synth_prov
from .governance import GovernanceBuilder
from . import scoring


class EdgeBuilder:
    def __init__(self, ctx: BuildContext, ent: EntityBuilder, gov: GovernanceBuilder) -> None:
        self.ctx = ctx
        self.ent = ent
        self.gov = gov
        self.edges: list[dict] = []
        self._seen: set[tuple[str, str, str]] = set()
        self._built_types: set[tuple[str, str, str]] = set()
        # Counts per (type, from_label, to_label), NOT per type. Several types are
        # legitimately reused across endpoint pairs (conflict C-11: ACTIVATES,
        # INHIBITS, CAUSES, PREDICTED_TO_INTERACT_WITH), so counting by type alone
        # makes the filler believe a pair is satisfied when a different pair
        # sharing its name was the one that got built.
        self._pair_counts: dict[tuple[str, str, str], int] = {}

    # -- uid lookup --------------------------------------------------------
    def uid(self, label: str, canonical_id: str) -> str | None:
        return (self.ent.index.get(label) or self.gov.index.get(label) or {}).get(canonical_id)

    def all_uids(self, label: str) -> list[str]:
        return self.ent.ids(label) or self.gov.ids(label)

    def gene_uid_by_symbol(self, symbol: str) -> str | None:
        g = (self.ctx.seeds.get("genes") or {}).get(symbol)
        if not g:
            return None
        return self.uid("Gene", g.get("hgnc_id") or f"HGNC.UNKNOWN:{symbol}")

    def protein_uid_by_symbol(self, symbol: str) -> str | None:
        p = (self.ctx.seeds.get("proteins") or {}).get(symbol)
        return self.uid("Protein", p["protein_id"]) if p and p.get("protein_id") else None

    # -- edge factory ------------------------------------------------------
    def add(
        self,
        rel: RelSpec,
        from_uid: str | None,
        to_uid: str | None,
        prov: Provenance,
        *,
        direction: int | None = None,
        evidence_type: str | None = None,
        source_type: str | None = None,
        effect_size: float | None = None,
        adj_p: float | None = None,
        n: int | None = None,
        pub_year: int | None = None,
        domain: str = "general",
        modality: str | None = None,
    ) -> bool:
        """Compute the full edge property contract and append the edge."""
        c = self.ctx
        if not from_uid or not to_uid or from_uid == to_uid:
            return False
        key = (rel.type, from_uid, to_uid)
        if key in self._seen:
            return False

        st = source_type or c.weighted(c.scale.source_types, c.scale.source_type_weights)
        et = evidence_type or c.weighted(c.scale.evidence_types, c.scale.evidence_type_weights)
        year = pub_year or c.year()
        eff = c.rng.gauss(0, 0.85) if effect_size is None else effect_size
        p = 10 ** (-c.rng.triangular(0.4, 28, 3)) if adj_p is None else adj_p
        size = int(c.rng.triangular(4, 4000, 60)) if n is None else n

        agg = scoring.compute_edge_aggregate_score(eff, p, size)
        # SCAN-01's minimum ingestion threshold is a real filter, not decoration:
        # edges at or below 0.10 are classified as noise and must not enter.
        if not scoring.passes_ingestion_threshold(agg):
            return False

        # Contradiction: a fraction of edges carry opposing observations, so
        # PRIM_E06/E07 (the K term of mechanism confidence) is non-degenerate.
        if c.rng.random() < c.scale.contradiction_rate:
            pos = c.rng.randint(1, 6)
            neg = c.rng.randint(1, 6)
        else:
            pos, neg = c.rng.randint(2, 9), c.rng.randint(0, 1)

        conc_score, conc_flag = scoring.concordance_flag(pos, neg)
        datasets = self.gov.index.get("Dataset", {})
        ds_ids = c.pick(list(datasets), k=c.rng.randint(1, 3))
        assertions = self.gov.index.get("Assertion", {})
        as_ids = c.pick(list(assertions), k=c.rng.randint(0, 2))
        ev_count = pos + neg

        props = {
            "SA": scoring.compute_SA(st, year),
            "ES_edge": scoring.compute_ES_edge(et),
            "ES_assertion": scoring.compute_ES_assertion(p, eff, ev_count, size),
            "edge_aggregate_score": agg,
            "edge_quality_tier": scoring.edge_quality_tier(agg),
            "direction": rel.direction_default if direction is None else direction,
            "recency": scoring.compute_recency(year, domain),
            "replication_score": round(min(1.0, ev_count / 6), 4),
            "evidence_count": ev_count,
            "pos_count": pos,
            "neg_count": neg,
            "conflict_score": scoring.compute_edge_conflict(pos, neg),
            "concordance_flag": conc_flag,
            "concordance_score": conc_score,
            "edge_class": f"{rel.from_label}_{rel.type}_{rel.to_label}",
            "source_type": st,
            "evidence_type": et,
            "publication_year": year,
            "dataset_ids": ds_ids,
            "assertion_ids": as_ids,
            "modality": modality or c.rng.choice(c.scale.modalities),
            "observed_families": c.rng.randint(1, 8),
            "formula_version": "v2.0",
            "promotion_ts": c.past(1200),
            "doc_table": rel.doc_table,
            "conflict_ref": rel.conflict_ref,
            "gap_ref": rel.gap_ref,
        }

        rec = stamp_edge(
            c.schema, rel.type, from_uid, to_uid, props, prov,
            versions=c.versions, layer=rel.layer, now=c.now,
            created_at=c.past(1400),
        )
        self.edges.append(rec)
        self._seen.add(key)
        self._built_types.add(rel.endpoint_key)
        self._pair_counts[rel.endpoint_key] = self._pair_counts.get(rel.endpoint_key, 0) + 1
        return True

    # -- seed-driven builders ---------------------------------------------
    def build_from_seeds(self, specs: dict[tuple[str, str, str], RelSpec]) -> None:
        c = self.ctx
        seeds = c.seeds

        def spec(t: str, f: str, to: str) -> RelSpec | None:
            return specs.get((t, f, to))

        # --- Gene -> Protein (G-01 extension) -----------------------------
        s = spec("ENCODES", "Gene", "Protein")
        if s:
            for sym in (seeds.get("proteins") or {}):
                self.add(s, self.gene_uid_by_symbol(sym), self.protein_uid_by_symbol(sym),
                         seed_prov("hgnc", 1), evidence_type="curated_db",
                         source_type="curated_ontology", direction=1)

        # --- OpenTargets: gene->disease, drug->protein, drug->disease -----
        for sym, tgt in (seeds.get("targets") or {}).items():
            g_uid = self.gene_uid_by_symbol(sym)
            p_uid = self.protein_uid_by_symbol(sym)
            url, ts = tgt.get("_url"), tgt.get("_retrieved_at")
            ot_prov = seed_prov("opentargets", 2, url, ts)

            for d in tgt.get("diseases") or []:
                d_uid = self.uid("Disease", d["disease_id"])
                if not d_uid:
                    continue
                score = float(d.get("score") or 0)
                dts = d.get("datatype_scores") or {}
                # OpenTargets datatype channels map onto distinct doc edge types:
                # genetic_association -> GENETICALLY_LINKS_TO, known_drug/literature
                # -> CAUSES, etc. Using the channel rather than one blanket edge is
                # what gives SCAN-09 a real causal-vs-correlational distinction.
                for rel_t, chan in (("GENETICALLY_LINKS_TO", "genetic_association"),
                                    ("CAUSES", "somatic_mutation"),
                                    ("BIOMARKER_FOR", "literature")):
                    sp = spec(rel_t, "Gene", "Disease")
                    if not sp or chan not in dts:
                        continue
                    ch = float(dts[chan])
                    self.add(sp, g_uid, d_uid, ot_prov,
                             evidence_type="curated_db" if ch > 0.5 else "text_mined",
                             source_type="curated_ontology" if ch > 0.5 else "peer_reviewed",
                             effect_size=round(ch * 2, 4),
                             adj_p=10 ** (-max(1.5, ch * 20)),
                             n=int(200 + score * 4000))

            for d in tgt.get("drugs") or []:
                dr_uid = self.uid("Drug", d["drug_id"])
                if not dr_uid:
                    continue
                direction = int(d.get("direction") or 0)
                rel_t = ("PHARMACOLOGICALLY_INHIBITS" if direction < 0
                         else "PHARMACOLOGICALLY_ACTIVATES" if direction > 0
                         else "EXERTS_OFF_TARGET_EFFECT")
                sp = spec(rel_t, "Drug", "Protein")
                if sp and p_uid:
                    self.add(sp, dr_uid, p_uid, ot_prov, direction=direction,
                             evidence_type="experimental_validation",
                             source_type="clinical_registry")
                for ind in d.get("indications") or []:
                    di_uid = self.uid("Disease", ind["disease_id"])
                    if not di_uid:
                        continue
                    for rel_t2 in ("CLINICALLY_TREATS", "TREATS_INDICATION"):
                        sp2 = spec(rel_t2, "Drug", "Disease")
                        if sp2:
                            self.add(sp2, dr_uid, di_uid, ot_prov, direction=-1,
                                     evidence_type="replication",
                                     source_type="clinical_registry")

            # --- orthologs / conservation (SCAN-12, SCAN-16) --------------
            for h in tgt.get("homologues") or []:
                sp_sp = self.uid("Species", str(h.get("species_id")))
                pct = h.get("query_pct_identity")
                if h.get("is_ortholog") and sp_sp:
                    sp2 = spec("CONSERVED_IN", "Gene", "Species")
                    if sp2:
                        self.add(sp2, g_uid, sp_sp, ot_prov,
                                 evidence_type="curated_db",
                                 source_type="curated_ontology",
                                 effect_size=round((pct or 50) / 50, 4),
                                 adj_p=1e-8, n=1)

        # --- STRING PPI ---------------------------------------------------
        for e in seeds.get("ppi") or []:
            pv = seed_prov("string", 2, e.get("_url"), e.get("_retrieved_at"))
            ga, gb = self.gene_uid_by_symbol(e["a"]), self.gene_uid_by_symbol(e["b"])
            pa, pb = self.protein_uid_by_symbol(e["a"]), self.protein_uid_by_symbol(e["b"])
            # Experimental/database channels -> observed interaction;
            # text-mining-dominant -> predicted. PRIM_E02 grades these differently.
            experimental = max(e.get("experimental", 0), e.get("database", 0)) > 0.3
            rel_t = "INTERACTS_WITH" if experimental else "PREDICTED_TO_INTERACT_WITH"
            sp = spec(rel_t, "Gene", "Gene")
            if sp:
                self.add(sp, ga, gb, pv,
                         evidence_type="high_throughput" if experimental else "text_mined",
                         effect_size=round(e["score"] * 2, 4),
                         adj_p=10 ** (-max(1.5, e["score"] * 12)))
            sp = spec("PROTEIN_INTERACTS_WITH", "Protein", "Protein")
            if sp:
                self.add(sp, pa, pb, pv,
                         evidence_type="high_throughput" if experimental else "text_mined",
                         effect_size=round(e["score"] * 2, 4),
                         adj_p=10 ** (-max(1.5, e["score"] * 12)))

        # --- Reactome pathway membership ----------------------------------
        for m in seeds.get("pathway_membership") or []:
            pw_uid = self.uid("Pathway", m["pathway_id"])
            rp = seed_prov("reactome", 1)
            sp = spec("PARTICIPATES_IN", "Gene", "Pathway")
            if sp:
                self.add(sp, self.gene_uid_by_symbol(m["gene_symbol"]), pw_uid, rp,
                         evidence_type="curated_db", source_type="curated_ontology")
            sp = spec("PROTEIN_PARTICIPATES_IN", "Protein", "Pathway")
            if sp:
                self.add(sp, self.uid("Protein", m["protein_id"]), pw_uid, rp,
                         evidence_type="curated_db", source_type="curated_ontology")

        # --- GTEx expression: BOTH directions, per conflict C-14 ----------
        for row in seeds.get("expression") or []:
            t_uid = self.uid("Tissue", row.get("tissue_uberon") or "")
            g_uid = self.gene_uid_by_symbol(row["gene_symbol"])
            if not t_uid or not g_uid:
                continue
            gp = seed_prov("gtex", 1, row.get("_url"), row.get("_retrieved_at"))
            tpm = float(row["median_tpm"])
            if tpm < 1.0:
                continue
            kw = dict(evidence_type="high_throughput", source_type="curated_ontology",
                      effect_size=round(min(2.0, tpm / 30), 4), adj_p=1e-6,
                      n=int(row.get("n") or 300), modality="rna_seq")
            sp = spec("EXPRESSED_IN", "Gene", "Tissue")
            if sp:
                self.add(sp, g_uid, t_uid, gp, **kw)
            # The documented inverse. Built on purpose so the 2-cycle and the
            # degree inflation it causes are measurable rather than theoretical.
            sp = spec("EXPRESSES", "Tissue", "Gene")
            if sp:
                self.add(sp, t_uid, g_uid, gp, **kw)

        # --- GWAS variants ------------------------------------------------
        for link in seeds.get("variant_gene_links") or []:
            v_uid = self.uid("Variant", link["variant_id"])
            g_uid = self.gene_uid_by_symbol(link["gene_symbol"])
            gw = seed_prov("gwas_catalog", 1)
            for rel_t, f, t in (("eQTL_MODULATES", "Variant", "Gene"),
                                ("MAPS_TO_LOCUS", "Variant", "Gene"),
                                ("REGULATED_BY_QTL", "Gene", "Variant")):
                sp = spec(rel_t, f, t)
                if not sp:
                    continue
                a, b = (v_uid, g_uid) if f == "Variant" else (g_uid, v_uid)
                self.add(sp, a, b, gw, evidence_type="high_throughput",
                         source_type="curated_ontology", modality="gwas")

        for a in seeds.get("variant_associations") or []:
            v_uid = self.uid("Variant", a["variant_id"])
            gw = seed_prov("gwas_catalog", 1, a.get("_url"), a.get("_retrieved_at"))
            beta = a.get("beta")
            for t in a.get("traits") or []:
                tr_uid = self.uid("Trait", t.get("trait_id") or "")
                sp = spec("GWAS_ASSOCIATED_WITH", "Variant", "Trait")
                if sp and tr_uid:
                    self.add(sp, v_uid, tr_uid, gw, evidence_type="high_throughput",
                             source_type="peer_reviewed", modality="gwas",
                             effect_size=float(beta) if beta else None,
                             adj_p=float(a.get("p_value") or 1e-8),
                             n=int(c.rng.triangular(1000, 500000, 40000)))

        # --- ontology parentage (the DAG SCAN-25 needs) -------------------
        sp = spec("ONTOLOGICALLY_INCLUDES", "OntologyTerm", "OntologyTerm")
        if sp:
            op = seed_prov("ols", 1)
            for child, parents in (seeds.get("disease_parents") or {}).items():
                for parent in parents:
                    self.add(sp, self.uid("OntologyTerm", parent),
                             self.uid("OntologyTerm", child), op,
                             evidence_type="curated_db", source_type="curated_ontology")

        # --- disease <-> ontology, phenotype -----------------------------
        for rel_t in ("STANDARDIZED_BY_ONTOLOGY", "STANDARDIZES_DISEASE"):
            sp = spec(rel_t, "Disease", "OntologyTerm")
            if not sp:
                continue
            for curie in (seeds.get("diseases") or {}):
                self.add(sp, self.uid("Disease", curie), self.uid("OntologyTerm", curie),
                         seed_prov("mondo", 1), evidence_type="curated_db",
                         source_type="curated_ontology")

        sp = spec("DISTINGUISHES_COHORT", "Phenotype", "Disease")
        if sp:
            for dp in seeds.get("disease_phenotypes") or []:
                self.add(sp, self.uid("Phenotype", dp["phenotype_id"]),
                         self.uid("Disease", dp["disease_id"]),
                         seed_prov("hpo", 1), evidence_type="curated_db",
                         source_type="curated_ontology")

    # -- synthetic filler --------------------------------------------------
    def build_synthetic(self, specs: dict[tuple[str, str, str], RelSpec]) -> None:
        """Fill every relationship row up to its configured degree."""
        c = self.ctx
        degree_for = self._degree_map()

        # Two passes. First price every endpoint pair at its nominal degree, then
        # scale uniformly so the total respects scale.target_edges. Scaling
        # uniformly preserves the *relative* density between families, which is
        # what makes the topology realistic — only the absolute size changes.
        planned: dict[tuple[str, str, str], int] = {}
        for key, rel in specs.items():
            sources = self.all_uids(rel.from_label)
            if not sources or not self.all_uids(rel.to_label):
                continue
            planned[key] = int(len(sources) * degree_for(rel))

        nominal = sum(planned.values())
        budget = max(0, c.scale.target_edges - len(self.edges))
        scale_factor = min(1.0, budget / nominal) if nominal else 0.0

        for key, rel in specs.items():
            if key not in planned:
                continue
            sources = self.all_uids(rel.from_label)
            targets = self.all_uids(rel.to_label)
            # Floor of 1 so no relationship type is scaled out of existence —
            # a type with zero instances leaves its scans untestable.
            target_count = max(1, int(planned[key] * scale_factor))
            existing = self._pair_counts.get(rel.endpoint_key, 0)
            need = max(0, target_count - existing)
            if need == 0:
                continue

            prov = synth_prov(_source_for(rel), 2, "degree_preserving_expansion")
            attempts = 0
            made = 0
            # Preferential attachment: pick targets with a bias toward the head of
            # the list, producing a heavy-tailed degree distribution rather than a
            # uniform one. Betweenness on a uniform random graph is nearly
            # featureless and would make SCAN-02's role matrix meaningless.
            while made < need and attempts < need * 6:
                attempts += 1
                src = c.rng.choice(sources)
                idx = min(len(targets) - 1, int(abs(c.rng.gauss(0, len(targets) / 3.2))))
                if self.add(rel, src, targets[idx], prov,
                            domain=c.rng.choice(c.scale.domains)):
                    made += 1

    def _degree_map(self) -> Callable[[RelSpec], float]:
        s = self.ctx.scale

        def deg(rel: RelSpec) -> float:
            f, t = rel.from_label, rel.to_label
            if {f, t} == {"Gene"}:
                return s.gene_gene_degree
            if {f, t} == {"Protein"}:
                return s.protein_protein_degree
            if "Pathway" in (f, t):
                return s.gene_pathway_degree
            if "Tissue" in (f, t):
                return s.gene_tissue_degree
            if "Publication" in (f, t) or "Evidence" in (f, t):
                return s.provenance_degree
            if "Variant" in (f, t):
                return s.variant_gene_degree
            if "Disease" in (f, t):
                return s.disease_gene_degree / 4
            if "Drug" in (f, t) or "Compound" in (f, t):
                return s.drug_protein_degree
            if f in ("SkygenicHypothesis",) or t in ("SkygenicHypothesis",):
                return 3.0
            if "Species" in (f, t):
                return 0.6
            return 1.2

        return deg

    # -- hypothesis wiring -------------------------------------------------
    def build_hypothesis_edges(self, specs: dict[tuple[str, str, str], RelSpec]) -> None:
        """Wire hypotheses to versions, state, chain, score and subgraph members."""
        c = self.ctx
        hyps = self.gov.index.get("SkygenicHypothesis", {})

        pairs = [
            ("GOVERNED_BY_LIFECYCLE_STATE", "LifecycleState", lambda h: f"{h}.STATE"),
            ("TRACED_THROUGH_PATHWAY", "ReasoningChain", lambda h: f"{h}.CHAIN"),
            ("EVALUATED_BY_METRICS", "ConfidenceScore", lambda h: f"{h}.SCORE"),
        ]
        for rel_t, label, keyer in pairs:
            sp = specs.get((rel_t, "SkygenicHypothesis", label))
            if not sp:
                continue
            for hid, h_uid in hyps.items():
                self.add(sp, h_uid, self.gov.index.get(label, {}).get(keyer(hid)),
                         synth_prov("skygenic", 1, "template_instantiated"),
                         evidence_type="curated_db", source_type="user_hypothesis")

        for rel_t in ("HAS_VERSION", "SUPERSEDES_VERSION"):
            sp = specs.get((rel_t, "SkygenicHypothesis", "HypothesisVersion"))
            if not sp:
                continue
            for hid, h_uid in hyps.items():
                for vid in self.gov.version_chain.get(hid, []):
                    self.add(sp, h_uid, self.gov.index["HypothesisVersion"][vid],
                             synth_prov("skygenic", 1, "template_instantiated"),
                             evidence_type="curated_db", source_type="user_hypothesis")

        # HYPOTHESIS_INCLUDES_NODE — gap G-10. Without it there is no hypothesis
        # subgraph and every hypothesis-scoped scan has nothing to run on.
        member_labels = ["Gene", "Protein", "Pathway", "Disease", "Variant",
                         "BiologicalProcess", "Drug", "Phenotype"]
        for label in member_labels:
            sp = specs.get(("HYPOTHESIS_INCLUDES_NODE", "SkygenicHypothesis", label))
            if not sp:
                continue
            pool = self.all_uids(label)
            if not pool:
                continue
            for h_uid in hyps.values():
                for m in c.pick(pool, k=c.rng.randint(3, 9)):
                    self.add(sp, h_uid, m,
                             synth_prov("skygenic", 1, "template_instantiated"),
                             evidence_type="curated_db", source_type="user_hypothesis")

    # -- orchestration -----------------------------------------------------
    def build_all(self) -> list[dict]:
        specs = {r.endpoint_key: r for r in self.ctx.schema.relationships}
        self.build_from_seeds(specs)
        self.build_hypothesis_edges(specs)
        self.build_synthetic(specs)
        return self.edges

    def coverage(self) -> dict[str, Any]:
        specs = {r.endpoint_key for r in self.ctx.schema.relationships}
        return {
            "declared_endpoint_pairs": len(specs),
            "built_endpoint_pairs": len(self._built_types),
            "unbuilt": sorted(specs - self._built_types),
        }


def _source_for(rel: RelSpec) -> str:
    """Best-guess source_registry source_id for a generated edge."""
    f, t = rel.from_label, rel.to_label
    if "Pathway" in (f, t):
        return "reactome"
    if "Variant" in (f, t):
        return "dbsnp"
    if "Disease" in (f, t):
        return "mondo"
    if "Drug" in (f, t) or "Compound" in (f, t):
        return "chembl"
    if "Tissue" in (f, t):
        return "uberon"
    if "Phenotype" in (f, t):
        return "hpo"
    if "Publication" in (f, t):
        return "pubmed"
    if "OntologyTerm" in (f, t):
        return "ols"
    if "Species" in (f, t):
        return "ncbi_taxonomy"
    if "Protein" in (f, t):
        return "uniprotkb"
    if "SkygenicHypothesis" in (f, t):
        return "skygenic"
    return "hgnc"
