"""Pulls the complete real-data seed set and writes a normalised bundle.

Run:  python -m skygenic_scans.sources.fetch_all

Everything is cached under data/seeds/<source>/, so re-runs are free and the
build is resumable after an interruption or a transient upstream outage.
Output: data/seeds/seed_bundle.json — the single input to the synthetic layer.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import anchors, biomolecules as bio, clinical as clin, literature as lit, networks as net

OUT_PATH = Path(__file__).resolve().parents[3] / "data" / "seeds" / "seed_bundle.json"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_genes_and_proteins(symbols: tuple[str, ...]) -> dict[str, Any]:
    genes, proteins, missing = {}, {}, []
    for i, sym in enumerate(symbols, 1):
        bundle = bio.fetch_gene_bundle(sym)
        if not bundle:
            missing.append(sym)
            continue
        genes[sym] = bundle["gene"] | {"source_ids": list(bundle["source_ids"])}
        if bundle["protein"]:
            proteins[sym] = bundle["protein"]
        if i % 20 == 0:
            _log(f"  genes {i}/{len(symbols)}")
    _log(f"genes={len(genes)} proteins={len(proteins)} missing={missing}")
    return {"genes": genes, "proteins": proteins, "missing": missing}


def fetch_pathways(proteins: dict[str, Any]) -> dict[str, Any]:
    pathways: dict[str, dict] = {}
    membership: list[dict] = []
    hierarchy: dict[str, list[str]] = {}
    for i, (sym, p) in enumerate(proteins.items(), 1):
        acc = p.get("protein_id")
        if not acc:
            continue
        for pw in bio.fetch_reactome_pathways(acc):
            pid = pw["pathway_id"]
            pathways.setdefault(pid, pw)
            membership.append({"protein_id": acc, "gene_symbol": sym, "pathway_id": pid})
        if i % 20 == 0:
            _log(f"  pathways {i}/{len(proteins)} (distinct={len(pathways)})")
    # Hierarchy for the top pathways only — ancestors are one call each and the
    # long tail adds little structure.
    top = sorted(pathways, key=lambda p: -sum(1 for m in membership if m["pathway_id"] == p))[:120]
    for pid in top:
        anc = bio.fetch_reactome_pathway_hierarchy(pid)
        if anc:
            hierarchy[pid] = anc
    _log(f"pathways={len(pathways)} membership={len(membership)} hierarchy={len(hierarchy)}")
    return {"pathways": pathways, "membership": membership, "hierarchy": hierarchy}


def fetch_associations(genes: dict[str, Any]) -> dict[str, Any]:
    targets, diseases_seen = {}, {}
    drugs: dict[str, dict] = {}
    for i, (sym, g) in enumerate(genes.items(), 1):
        ens = g.get("ensembl_id")
        if not ens:
            continue
        ot = net.fetch_opentargets_target(ens, n=25)
        if not ot:
            continue
        targets[sym] = ot
        for d in ot["diseases"]:
            diseases_seen.setdefault(d["disease_id"], d["name"])
        for d in ot["drugs"]:
            drugs.setdefault(d["drug_id"], d)
        if i % 20 == 0:
            _log(f"  opentargets {i}/{len(genes)}")
    _log(f"targets={len(targets)} diseases_discovered={len(diseases_seen)} drugs={len(drugs)}")
    return {"targets": targets, "discovered_diseases": diseases_seen, "drugs": drugs}


def fetch_ppi(symbols: tuple[str, ...], chunk: int = 40) -> list[dict]:
    edges: list[dict] = []
    for i in range(0, len(symbols), chunk):
        edges.extend(net.fetch_string_network(symbols[i : i + chunk]))
    seen: dict[tuple, dict] = {}
    for e in edges:
        if not e["a"] or not e["b"]:
            continue
        seen.setdefault(tuple(sorted((e["a"], e["b"]))), e)
    _log(f"ppi_edges={len(seen)}")
    return list(seen.values())


def fetch_variants(symbols: tuple[str, ...], per_gene: int = 15,
                   assoc_for: int = 6) -> dict[str, Any]:
    variants: dict[str, dict] = {}
    gene_links: list[dict] = []
    associations: list[dict] = []
    for i, sym in enumerate(symbols, 1):
        vs = net.fetch_gwas_variants_for_gene(sym, size=per_gene)
        for v in vs:
            variants.setdefault(v["variant_id"], v)
            gene_links.append({"variant_id": v["variant_id"], "gene_symbol": sym})
        for v in vs[:assoc_for]:
            associations.extend(net.fetch_gwas_associations(v["variant_id"], size=5))
        if i % 20 == 0:
            _log(f"  gwas {i}/{len(symbols)} (variants={len(variants)})")
    _log(f"variants={len(variants)} gene_links={len(gene_links)} assocs={len(associations)}")
    return {"variants": variants, "gene_links": gene_links, "associations": associations}


def fetch_expression(symbols: tuple[str, ...]) -> list[dict]:
    rows: list[dict] = []
    for i, sym in enumerate(symbols, 1):
        rows.extend(net.fetch_gtex_expression(sym))
        if i % 20 == 0:
            _log(f"  gtex {i}/{len(symbols)} (rows={len(rows)})")
    _log(f"expression_rows={len(rows)}")
    return rows


def fetch_ontologies(discovered: dict[str, str]) -> dict[str, Any]:
    terms: dict[str, dict] = {}
    parents: dict[str, list[str]] = {}
    phenotypes: dict[str, dict] = {}
    disease_phenotypes: list[dict] = []

    anchor_diseases = {c: (label, dom) for c, label, dom in anchors.all_diseases()}
    all_disease_curies = {**{c: v[0] for c, v in anchor_diseases.items()}, **discovered}

    for i, (curie, label) in enumerate(all_disease_curies.items(), 1):
        if not curie.startswith("MONDO:"):
            continue
        t = clin.fetch_ols_term(curie, "mondo")
        if t:
            terms[curie] = t
            p = clin.fetch_ols_parents(curie, "mondo")
            if p:
                parents[curie] = p
        if i % 25 == 0:
            _log(f"  mondo {i}/{len(all_disease_curies)}")

    # Phenotypes only for the anchor diseases — HPO annotation lookup is 2-3 calls
    # per disease and the anchors are where the Disease/Phenotype structure matters.
    for curie, (label, _dom) in anchor_diseases.items():
        for ph in clin.fetch_phenotypes_for_disease(curie, label):
            phenotypes.setdefault(ph["phenotype_id"], ph)
            disease_phenotypes.append(
                {"disease_id": curie, "phenotype_id": ph["phenotype_id"],
                 "category": ph.get("category")}
            )

    tissues = {}
    for curie, _name in anchors.ANCHOR_TISSUES:
        t = clin.fetch_ols_term(curie, "uberon")
        if t:
            tissues[curie] = t
    cell_types = {}
    for curie, _name in anchors.ANCHOR_CELL_TYPES:
        t = clin.fetch_ols_term(curie, "cl")
        if t:
            cell_types[curie] = t

    _log(
        f"mondo={len(terms)} parents={len(parents)} hpo={len(phenotypes)} "
        f"disease_pheno={len(disease_phenotypes)} uberon={len(tissues)} cl={len(cell_types)}"
    )
    return {
        "diseases": terms, "disease_parents": parents,
        "phenotypes": phenotypes, "disease_phenotypes": disease_phenotypes,
        "tissues": tissues, "cell_types": cell_types,
    }


def fetch_literature() -> list[dict]:
    pubs: dict[str, dict] = {}
    for a in anchors.DOMAIN_ANCHORS:
        for _curie, label in a.diseases:
            for p in lit.fetch_publications_for(f"{label} mechanism", retmax=25):
                pubs.setdefault(p["publication_id"], p)
        for gene in a.genes[:6]:
            for p in lit.fetch_publications_for(f"{gene} {a.domain} pathway", retmax=12):
                pubs.setdefault(p["publication_id"], p)
    _log(f"publications={len(pubs)}")
    return list(pubs.values())


def main() -> int:
    started = datetime.now(timezone.utc)
    symbols = anchors.all_genes()
    _log(f"anchors: {len(symbols)} genes, {len(anchors.all_diseases())} diseases, "
         f"{len(anchors.DOMAIN_ANCHORS)} domains")

    gp = fetch_genes_and_proteins(symbols)
    resolved = tuple(gp["genes"])

    pw = fetch_pathways(gp["proteins"])
    assoc = fetch_associations(gp["genes"])
    ppi = fetch_ppi(resolved)
    var = fetch_variants(resolved)
    expr = fetch_expression(resolved)
    onto = fetch_ontologies(assoc["discovered_diseases"])
    pubs = fetch_literature()

    bundle = {
        "meta": {
            "fetched_at": started.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "anchor_gene_count": len(symbols),
            "domains": [a.domain for a in anchors.DOMAIN_ANCHORS],
        },
        "genes": gp["genes"],
        "proteins": gp["proteins"],
        "pathways": pw["pathways"],
        "pathway_membership": pw["membership"],
        "pathway_hierarchy": pw["hierarchy"],
        "targets": assoc["targets"],
        "drugs": assoc["drugs"],
        "ppi": ppi,
        "variants": var["variants"],
        "variant_gene_links": var["gene_links"],
        "variant_associations": var["associations"],
        "expression": expr,
        "diseases": onto["diseases"],
        "disease_parents": onto["disease_parents"],
        "phenotypes": onto["phenotypes"],
        "disease_phenotypes": onto["disease_phenotypes"],
        "tissues": onto["tissues"],
        "cell_types": onto["cell_types"],
        "species": [
            {"taxon_id": t, "name": n, "common_name": c, "is_model_organism": m}
            for t, n, c, m in anchors.ANCHOR_SPECIES
        ],
        "publications": pubs,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(bundle, indent=1, default=str))
    size_mb = OUT_PATH.stat().st_size / 1e6
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    _log(f"WROTE {OUT_PATH} ({size_mb:.1f} MB) in {elapsed:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
