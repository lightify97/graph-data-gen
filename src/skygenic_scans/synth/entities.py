"""Builds biological-core node records from real seeds, then expands them.

Naming policy for generated entities: synthetic records are keyed in an explicit
`SKYGEN.<TYPE>:<n>` namespace, never in a real one. A reader scanning the graph
must never have to guess whether `HGNC:11998` or `SKYGEN.GENE:000042` came off a
public API. `is_synthetic` says the same thing structurally; the id says it
visually, which is what people actually read.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from ..provenance import Provenance, VersionTriplet, stamp_node
from ..schema import Schema
from .config import ScaleConfig

HUMAN = 9606


@dataclass
class BuildContext:
    schema: Schema
    scale: ScaleConfig
    seeds: dict[str, Any]
    rng: random.Random
    versions: VersionTriplet
    now: datetime

    def past(self, max_days: int = 2500, min_days: int = 1) -> datetime:
        return self.now - timedelta(days=self.rng.randint(min_days, max_days))

    def pick(self, seq: Sequence, k: int = 1) -> list:
        if not seq:
            return []
        k = min(k, len(seq))
        return self.rng.sample(list(seq), k)

    def weighted(self, options: Sequence[str], weights: Sequence[float]) -> str:
        return self.rng.choices(list(options), weights=list(weights), k=1)[0]

    def year(self) -> int:
        lo, hi = self.scale.year_range
        return int(self.rng.triangular(lo, hi, self.scale.year_mode))


def seed_prov(source: str, priority: int, url: str | None = None,
              retrieved: Any = None, extra_ids: Iterable[str] = ()) -> Provenance:
    """Provenance for a record fetched from a live API."""
    return Provenance(
        source=source,
        source_priority=priority,
        is_synthetic=False,
        source_ids=tuple(dict.fromkeys((source, *extra_ids))),
        source_url=url,
        source_retrieved_at=_as_dt(retrieved),
    )


def synth_prov(source: str, priority: int, method: str, seed_uid: str | None = None) -> Provenance:
    """Provenance for a generated record."""
    return Provenance(
        source=source,
        source_priority=priority,
        is_synthetic=True,
        source_ids=(source,),
        synthesis_method=method,
        synthesis_seed_uid=seed_uid,
    )


def _as_dt(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


class EntityBuilder:
    """Produces every biological-core node label."""

    def __init__(self, ctx: BuildContext) -> None:
        self.ctx = ctx
        self.nodes: dict[str, list[dict]] = {}
        # canonical_id -> uid, so the edge builder can resolve endpoints
        self.index: dict[str, dict[str, str]] = {}

    # -- helpers -----------------------------------------------------------
    def _add(self, label: str, canonical_id: str, props: dict, prov: Provenance,
             **kw: Any) -> str:
        rec = stamp_node(
            self.ctx.schema, label, canonical_id, props, prov,
            versions=self.ctx.versions, now=self.ctx.now, **kw
        )
        self.nodes.setdefault(label, []).append(rec)
        self.index.setdefault(label, {})[canonical_id] = rec["uid"]
        return rec["uid"]

    def ids(self, label: str) -> list[str]:
        return list(self.index.get(label, {}).values())

    def canon(self, label: str) -> list[str]:
        return list(self.index.get(label, {}))

    # -- species -----------------------------------------------------------
    def build_species(self) -> None:
        # Falls back to the anchor constant if the seed bundle is absent or
        # partial. Species with zero members would silently disable SCAN-12 and
        # SCAN-16 rather than failing loudly, so it must never be empty.
        rows = self.ctx.seeds.get("species") or [
            {"taxon_id": t, "name": n, "common_name": c, "is_model_organism": m}
            for t, n, c, m in _anchor_species()
        ]
        for s in rows:
            self._add(
                "Species", str(s["taxon_id"]),
                {"taxon_id": int(s["taxon_id"]), "name": s["name"],
                 "common_name": s.get("common_name"),
                 "is_model_organism": bool(s.get("is_model_organism"))},
                seed_prov("ncbi_taxonomy", 1),
            )

    # -- ontology terms ----------------------------------------------------
    def build_ontology_terms(self) -> None:
        c = self.ctx
        seen: set[str] = set()

        def add_real(term: dict, onto: str) -> None:
            tid = term.get("term_id")
            if not tid or tid in seen:
                return
            seen.add(tid)
            self._add(
                "OntologyTerm", tid,
                {"term_id": tid, "name": term.get("name") or tid, "ontology": onto,
                 "namespace": term.get("namespace")},
                seed_prov("ols", 1, term.get("_url"), term.get("_retrieved_at")),
                synonyms=tuple(term.get("synonyms") or ())[:15],
            )

        for key, onto in (("diseases", "MONDO"), ("tissues", "UBERON"),
                          ("cell_types", "CL"), ("phenotypes", "HPO")):
            for t in (c.seeds.get(key) or {}).values():
                add_real(t, onto)

        # GO terms are generated: the seed fetch does not pull GO directly, but
        # BiologicalProcess needs a GO-shaped ontology backbone and SCAN-25's
        # semantic similarity needs a DAG deep enough to compute distance on.
        target = c.scale.ontology_terms
        i = 0
        while len(seen) < target:
            i += 1
            tid = f"SKYGEN.GO:{i:07d}"
            if tid in seen:
                continue
            seen.add(tid)
            self._add(
                "OntologyTerm", tid,
                {"term_id": tid, "name": f"synthetic biological concept {i}",
                 "ontology": "GO", "namespace": c.rng.choice(
                     ["biological_process", "molecular_function", "cellular_component"])},
                synth_prov("go", 2, "ontology_walk"),
            )

    # -- genes -------------------------------------------------------------
    def build_genes(self) -> None:
        c = self.ctx
        real = c.seeds.get("genes") or {}
        for sym, g in real.items():
            hid = g.get("hgnc_id") or f"HGNC.UNKNOWN:{sym}"
            self._add(
                "Gene", hid,
                {"gene_id": hid, "symbol": sym, "name": g.get("name"),
                 "ensembl_id": g.get("ensembl_id"), "ncbi_gene_id": g.get("ncbi_gene_id"),
                 "chromosome": str(g.get("chromosome") or ""), "biotype": g.get("biotype"),
                 "species_taxon": HUMAN},
                seed_prov("hgnc", 1, g.get("_url"), g.get("_retrieved_at"),
                          extra_ids=tuple(g.get("source_ids") or ())),
                species=HUMAN,
                synonyms=tuple(g.get("synonyms") or ())[:15],
                ontology_refs=tuple(
                    x for x in (
                        f"ENSEMBL:{g.get('ensembl_id')}" if g.get("ensembl_id") else None,
                        f"NCBIGene:{g.get('ncbi_gene_id')}" if g.get("ncbi_gene_id") else None,
                    ) if x
                ),
            )

        seed_uids = self.ids("Gene")
        chroms = [str(x) for x in range(1, 23)] + ["X", "Y"]
        for i in range(len(real), c.scale.genes):
            parent = c.rng.choice(seed_uids) if seed_uids else None
            cid = f"SKYGEN.GENE:{i:06d}"
            self._add(
                "Gene", cid,
                {"gene_id": cid, "symbol": f"SG{i:05d}", "name": f"synthetic gene {i}",
                 "ensembl_id": f"SKYGEN.ENSG{i:08d}", "ncbi_gene_id": None,
                 "chromosome": c.rng.choice(chroms), "biotype": "protein_coding",
                 "species_taxon": HUMAN},
                synth_prov("hgnc", 1, "degree_preserving_expansion", parent),
                species=HUMAN,
            )

    # -- proteins ----------------------------------------------------------
    def build_proteins(self) -> None:
        c = self.ctx
        real = c.seeds.get("proteins") or {}

        # Gene -> protein is many-to-one, so the seed dict (keyed by gene symbol)
        # can carry the same accession twice. SMN1 and SMN2 are the real example
        # here: near-identical paralogs that both map to UniProt Q16637. Keying
        # Protein nodes on the accession without deduplicating produced two
        # records with the same uid, and MERGE silently collapsed them at load —
        # the generated/loaded node counts differed by exactly one.
        #
        # Worth noting for the schema itself: the Nodes doc has no Gene->Protein
        # edge at all (gap G-01), so this many-to-one relationship has nowhere to
        # be expressed. The ENCODES extension edge handles it correctly — both
        # genes get their own edge to the shared protein.
        by_accession: dict[str, list[str]] = {}
        for sym, p in real.items():
            acc = p.get("protein_id")
            if acc:
                by_accession.setdefault(acc, []).append(sym)

        for acc, symbols in by_accession.items():
            p = real[symbols[0]]
            extra_syn = tuple(symbols[1:])  # other genes encoding the same protein
            self._add(
                "Protein", acc,
                {"protein_id": acc, "name": p.get("name") or acc,
                 "gene_symbol": p.get("gene_symbol") or symbols[0],
                 "length_aa": p.get("length_aa"), "reviewed": bool(p.get("reviewed")),
                 "pdb_refs": list(p.get("pdb_refs") or ())[:10],
                 "species_taxon": HUMAN,
                 "encoded_by_symbols": list(symbols),
                 "druggability": round(c.rng.betavariate(2, 3), 4)},
                seed_prov("uniprotkb", 1, p.get("_url"), p.get("_retrieved_at")),
                species=HUMAN,
                synonyms=(tuple(p.get("synonyms") or ()) + extra_syn)[:15],
            )

        seed_uids = self.ids("Protein")
        for i in range(len(by_accession), c.scale.proteins):
            parent = c.rng.choice(seed_uids) if seed_uids else None
            cid = f"SKYGEN.PROT:{i:06d}"
            self._add(
                "Protein", cid,
                {"protein_id": cid, "name": f"synthetic protein {i}",
                 "gene_symbol": f"SG{i:05d}",
                 "length_aa": int(c.rng.triangular(80, 2200, 380)),
                 "reviewed": c.rng.random() < 0.35, "pdb_refs": [],
                 "species_taxon": HUMAN,
                 "druggability": round(c.rng.betavariate(2, 3), 4)},
                synth_prov("uniprotkb", 1, "degree_preserving_expansion", parent),
                species=HUMAN,
            )

    # -- pathways ----------------------------------------------------------
    def build_pathways(self) -> None:
        c = self.ctx
        real = c.seeds.get("pathways") or {}
        for pid, pw in real.items():
            self._add(
                "Pathway", pid,
                {"pathway_id": pid, "name": pw.get("name") or pid,
                 "source_db": pw.get("source_db") or "reactome",
                 "species_taxon": HUMAN},
                seed_prov("reactome", 1, pw.get("_url"), pw.get("_retrieved_at")),
                species=HUMAN,
            )
        seed_uids = self.ids("Pathway")
        for i in range(len(real), c.scale.pathways):
            self._add(
                "Pathway", f"SKYGEN.PATH:{i:06d}",
                {"pathway_id": f"SKYGEN.PATH:{i:06d}", "name": f"synthetic pathway {i}",
                 "source_db": "skygen", "species_taxon": HUMAN},
                synth_prov("reactome", 1, "degree_preserving_expansion",
                           c.rng.choice(seed_uids) if seed_uids else None),
                species=HUMAN,
            )

    # -- biological processes ---------------------------------------------
    def build_biological_processes(self) -> None:
        c = self.ctx
        go_terms = [t for t in self.canon("OntologyTerm") if t.startswith("SKYGEN.GO:")]
        for i in range(c.scale.biological_processes):
            cid = f"SKYGEN.BP:{i:06d}"
            self._add(
                "BiologicalProcess", cid,
                {"process_id": cid, "name": f"synthetic biological process {i}",
                 "go_namespace": "biological_process"},
                synth_prov("go", 2, "ontology_walk",
                           self.index["OntologyTerm"].get(go_terms[i % len(go_terms)])
                           if go_terms else None),
                species=HUMAN,
                ontology_refs=(go_terms[i % len(go_terms)],) if go_terms else (),
            )

    # -- protein complexes -------------------------------------------------
    def build_protein_complexes(self) -> None:
        c = self.ctx
        for i in range(c.scale.protein_complexes):
            cid = f"SKYGEN.CPLX:{i:05d}"
            self._add(
                "ProteinComplex", cid,
                {"complex_id": cid, "name": f"synthetic complex {i}",
                 "stoichiometry": f"{c.rng.randint(2, 6)}-mer", "species_taxon": HUMAN},
                synth_prov("reactome", 1, "template_instantiated"),
                species=HUMAN,
            )

    # -- variants ----------------------------------------------------------
    def build_variants(self) -> None:
        c = self.ctx
        real = c.seeds.get("variants") or {}
        # GWAS association stats, keyed by rsID, supply real p-values / betas.
        stats: dict[str, dict] = {}
        for a in c.seeds.get("variant_associations") or []:
            stats.setdefault(a["variant_id"], a)

        for vid, v in real.items():
            st = stats.get(vid, {})
            beta = st.get("beta")
            self._add(
                "Variant", vid,
                {"variant_id": vid,
                 "chromosome": str(v.get("chromosome") or ""),
                 "base_position": int(v.get("base_position") or 0),
                 "consequence": v.get("consequence"),
                 "p_raw": st.get("p_value"),
                 "effect_weight": float(beta) if beta is not None else None,
                 "allele_frequency": st.get("risk_frequency"),
                 "clinical_significance": None,
                 "ref_allele": None, "alt_allele": None},
                seed_prov("gwas_catalog", 1, v.get("_url"), v.get("_retrieved_at")),
                species=HUMAN,
            )

        chroms = [str(x) for x in range(1, 23)] + ["X"]
        bases = "ACGT"
        for i in range(len(real), c.scale.variants):
            # Effect sizes: small for common variants, larger for rare ones —
            # the standard allele-frequency/effect-size relationship. Without it
            # PRIM_R19's polygenic scoring sees an unrealistic flat landscape.
            af = c.rng.betavariate(0.6, 8)
            beta = c.rng.gauss(0, 0.08 + 0.5 * (1 - af) ** 4)
            self._add(
                "Variant", f"SKYGEN.VAR:{i:06d}",
                {"variant_id": f"SKYGEN.VAR:{i:06d}",
                 "chromosome": c.rng.choice(chroms),
                 "base_position": c.rng.randint(10_000, 240_000_000),
                 "ref_allele": c.rng.choice(bases), "alt_allele": c.rng.choice(bases),
                 "consequence": c.rng.choice(
                     ["intron_variant", "missense_variant", "synonymous_variant",
                      "3_prime_UTR_variant", "regulatory_region_variant",
                      "stop_gained", "splice_region_variant"]),
                 "p_raw": 10 ** (-c.rng.triangular(1.3, 40, 6)),
                 "effect_weight": round(beta, 5),
                 "allele_frequency": round(af, 5),
                 "clinical_significance": c.rng.choice(
                     [None, "benign", "likely_benign", "uncertain_significance",
                      "likely_pathogenic", "pathogenic"])},
                synth_prov("dbsnp", 1, "statistical_sample"),
                species=HUMAN,
            )

    # -- diseases ----------------------------------------------------------
    def build_diseases(self) -> None:
        c = self.ctx
        real = c.seeds.get("diseases") or {}
        anchor_domain = {curie: dom for curie, _l, dom in _anchor_disease_domains()}
        for curie, t in real.items():
            dom = anchor_domain.get(curie) or c.rng.choice(c.scale.domains)
            self._add(
                "Disease", curie,
                {"disease_id": curie, "name": t.get("name") or curie,
                 "biological_domain": dom,
                 "efo_id": None,
                 "omim_ids": list(t.get("omim_ids") or ())[:5],
                 "prevalence": round(10 ** (-c.rng.uniform(2, 6)), 8)},
                seed_prov("mondo", 1, t.get("_url"), t.get("_retrieved_at")),
                synonyms=tuple(t.get("synonyms") or ())[:12],
                ontology_refs=tuple(
                    f"{k}:{v[0]}" for k, v in (t.get("xrefs") or {}).items() if v
                )[:10],
            )
        for i in range(len(real), c.scale.diseases):
            self._add(
                "Disease", f"SKYGEN.DIS:{i:06d}",
                {"disease_id": f"SKYGEN.DIS:{i:06d}", "name": f"synthetic disease {i}",
                 "biological_domain": c.rng.choice(c.scale.domains),
                 "efo_id": None, "omim_ids": [],
                 "prevalence": round(10 ** (-c.rng.uniform(2, 6)), 8)},
                synth_prov("mondo", 1, "ontology_walk"),
            )

    # -- phenotypes / traits ----------------------------------------------
    def build_phenotypes(self) -> None:
        c = self.ctx
        real = c.seeds.get("phenotypes") or {}
        for pid, p in real.items():
            self._add(
                "Phenotype", pid,
                {"phenotype_id": pid, "name": p.get("name") or pid},
                seed_prov("hpo", 1, p.get("_url"), p.get("_retrieved_at")),
                synonyms=tuple(p.get("synonyms") or ())[:10],
            )
        for i in range(len(real), c.scale.phenotypes):
            self._add(
                "Phenotype", f"SKYGEN.HP:{i:06d}",
                {"phenotype_id": f"SKYGEN.HP:{i:06d}", "name": f"synthetic phenotype {i}"},
                synth_prov("hpo", 1, "ontology_walk"),
            )

    def build_traits(self) -> None:
        c = self.ctx
        seen: set[str] = set()
        for a in c.seeds.get("variant_associations") or []:
            for t in a.get("traits") or []:
                tid = t.get("trait_id")
                if not tid or tid in seen or not t.get("name"):
                    continue
                seen.add(tid)
                self._add(
                    "Trait", tid,
                    {"trait_id": tid, "name": t["name"], "trait_category": "gwas"},
                    seed_prov("gwas_catalog", 1, a.get("_url"), a.get("_retrieved_at")),
                )
        for i in range(len(seen), c.scale.traits):
            self._add(
                "Trait", f"SKYGEN.EFO:{i:06d}",
                {"trait_id": f"SKYGEN.EFO:{i:06d}", "name": f"synthetic trait {i}",
                 "trait_category": c.rng.choice(
                     ["anthropometric", "haematological", "metabolic", "behavioural",
                      "cardiovascular", "immunological"])},
                synth_prov("efo", 2, "statistical_sample"),
            )

    # -- drugs / compounds -------------------------------------------------
    def build_drugs(self) -> None:
        c = self.ctx
        real = c.seeds.get("drugs") or {}
        stage_phase = {"PHASE_1": 1, "PHASE_2": 2, "PHASE_3": 3, "PHASE_4": 4,
                       "APPROVED": 4, "PRECLINICAL": 0}
        for did, d in real.items():
            phase = stage_phase.get((d.get("max_clinical_stage") or "").upper(), 0)
            self._add(
                "Drug", did,
                {"drug_id": did, "name": d.get("name") or did, "max_phase": phase,
                 "approved": phase >= 4, "molecule_type": d.get("drug_type"),
                 "atc_codes": []},
                seed_prov("opentargets", 2),
            )
        for i in range(len(real), c.scale.drugs):
            phase = c.rng.choices([0, 1, 2, 3, 4], weights=[0.3, 0.2, 0.2, 0.17, 0.13])[0]
            self._add(
                "Drug", f"SKYGEN.DRUG:{i:05d}",
                {"drug_id": f"SKYGEN.DRUG:{i:05d}", "name": f"synthetic drug {i}",
                 "max_phase": phase, "approved": phase >= 4,
                 "molecule_type": c.rng.choice(
                     ["Small molecule", "Antibody", "Oligonucleotide", "Protein", "Cell therapy"]),
                 "atc_codes": []},
                synth_prov("chembl", 1, "template_instantiated"),
            )

    def build_compounds(self) -> None:
        c = self.ctx
        for i in range(c.scale.compounds):
            cid = f"SKYGEN.CMPD:{i:06d}"
            # Morgan fingerprint stand-in: a sparse bit vector. SIM_03 computes
            # Tanimoto over exactly this shape, so scaffold similarity is testable.
            fp = sorted(c.rng.sample(range(2048), c.rng.randint(20, 60)))
            self._add(
                "Compound", cid,
                {"compound_id": cid, "name": f"synthetic compound {i}",
                 "smiles": None, "inchikey": None, "morgan_fp": fp,
                 "mw": round(c.rng.triangular(120, 900, 340), 2)},
                synth_prov("pubchem", 2, "statistical_sample"),
            )

    # -- tissues / cell types ---------------------------------------------
    def build_tissues(self) -> None:
        c = self.ctx
        real = c.seeds.get("tissues") or {}
        gtex_names: dict[str, str] = {}
        for row in c.seeds.get("expression") or []:
            if row.get("tissue_uberon"):
                gtex_names.setdefault(row["tissue_uberon"], row.get("tissue"))

        seen = set()
        for curie, t in real.items():
            seen.add(curie)
            self._add(
                "Tissue", curie,
                {"tissue_id": curie, "name": t.get("name") or curie,
                 "gtex_name": gtex_names.get(curie)},
                seed_prov("uberon", 1, t.get("_url"), t.get("_retrieved_at")),
                synonyms=tuple(t.get("synonyms") or ())[:8],
            )
        # UBERON ids seen in GTEx but not in the anchor list — real tissues,
        # discovered through expression data rather than pre-declared.
        for curie, gname in gtex_names.items():
            if curie in seen:
                continue
            seen.add(curie)
            self._add(
                "Tissue", curie,
                {"tissue_id": curie, "name": (gname or curie).replace("_", " "),
                 "gtex_name": gname},
                seed_prov("gtex", 1),
            )
        if not seen:
            for curie, name in _anchor_tissues():
                self._add(
                    "Tissue", curie,
                    {"tissue_id": curie, "name": name, "gtex_name": None},
                    synth_prov("uberon", 1, "template_instantiated"),
                )

    def build_cell_types(self) -> None:
        real = self.ctx.seeds.get("cell_types") or {}
        for curie, t in real.items():
            self._add(
                "CellType", curie,
                {"cell_type_id": curie, "name": t.get("name") or curie},
                seed_prov("cell_ontology", 1, t.get("_url"), t.get("_retrieved_at")),
                synonyms=tuple(t.get("synonyms") or ())[:8],
            )
        if not real:
            for curie, name in _anchor_cell_types():
                self._add(
                    "CellType", curie, {"cell_type_id": curie, "name": name},
                    synth_prov("cell_ontology", 1, "template_instantiated"),
                )

    # -- publications ------------------------------------------------------
    def build_publications(self) -> None:
        c = self.ctx
        real = c.seeds.get("publications") or []
        for p in real:
            year = p.get("publication_year") or c.year()
            self._add(
                "Publication", p["publication_id"],
                {"publication_id": p["publication_id"], "title": p.get("title"),
                 "journal": p.get("journal"), "issn": p.get("issn"),
                 "publication_year": int(year), "source_type": "peer_reviewed",
                 "citation_count": int(abs(c.rng.gauss(40, 90))), "doi": p.get("doi")},
                seed_prov("pubmed", 2, p.get("_url"), p.get("_retrieved_at")),
            )
        for i in range(len(real), c.scale.publications):
            st = c.weighted(c.scale.source_types, c.scale.source_type_weights)
            self._add(
                "Publication", f"SKYGEN.PUB:{i:06d}",
                {"publication_id": f"SKYGEN.PUB:{i:06d}",
                 "title": f"synthetic publication {i}",
                 "journal": c.rng.choice(
                     ["Synthetic J Biol", "J Synth Med", "Comput Biol Rep", "bioRxiv"]),
                 "issn": None, "publication_year": c.year(), "source_type": st,
                 "citation_count": int(abs(c.rng.gauss(15, 40))), "doi": None},
                synth_prov("pubmed", 2, "statistical_sample"),
            )

    # -- orchestration -----------------------------------------------------
    def build_all(self) -> dict[str, list[dict]]:
        self.build_species()
        self.build_ontology_terms()
        self.build_genes()
        self.build_proteins()
        self.build_pathways()
        self.build_biological_processes()
        self.build_protein_complexes()
        self.build_variants()
        self.build_diseases()
        self.build_phenotypes()
        self.build_traits()
        self.build_drugs()
        self.build_compounds()
        self.build_tissues()
        self.build_cell_types()
        self.build_publications()
        return self.nodes


def _anchor_disease_domains() -> tuple[tuple[str, str, str], ...]:
    from ..sources.anchors import all_diseases

    return all_diseases()


def _anchor_species() -> tuple[tuple[int, str, str, bool], ...]:
    from ..sources.anchors import ANCHOR_SPECIES

    return ANCHOR_SPECIES


def _anchor_cell_types() -> tuple[tuple[str, str], ...]:
    from ..sources.anchors import ANCHOR_CELL_TYPES

    return ANCHOR_CELL_TYPES


def _anchor_tissues() -> tuple[tuple[str, str], ...]:
    from ..sources.anchors import ANCHOR_TISSUES

    return ANCHOR_TISSUES
