"""Gene, protein and pathway seeds — HGNC, Ensembl, UniProt, Reactome.

Every function returns normalised dicts carrying `_url` and `_retrieved_at`, which
flow straight into `Provenance` so the graph can defend where each field came from.
Missing records return None rather than raising: public APIs have gaps, and one
absent gene must not abort a 100-gene fetch.
"""

from __future__ import annotations

from typing import Any

from .http import Fetcher

HGNC = Fetcher("hgnc", rate_key="hgnc")
ENSEMBL = Fetcher("ensembl", rate_key="ensembl")
UNIPROT = Fetcher("uniprot", rate_key="uniprot")
REACTOME = Fetcher("reactome", rate_key="reactome")

UNIPROT_FIELDS = "accession,id,protein_name,gene_primary,gene_synonym,length,xref_pdb,cc_subcellular_location"


def fetch_hgnc_gene(symbol: str) -> dict[str, Any] | None:
    """Canonical gene identity. HGNC is the naming authority, so it is the key source."""
    res = HGNC.get(f"https://rest.genenames.org/fetch/symbol/{symbol}")
    if not res:
        return None
    docs = (res.data.get("response") or {}).get("docs") or []
    if not docs:
        return None
    d = docs[0]
    return {
        "hgnc_id": d.get("hgnc_id"),
        "symbol": d.get("symbol"),
        "name": d.get("name"),
        "ensembl_id": d.get("ensembl_gene_id"),
        "ncbi_gene_id": str(d["entrez_id"]) if d.get("entrez_id") else None,
        "chromosome": d.get("location"),
        "biotype": d.get("locus_type"),
        "synonyms": tuple(d.get("alias_symbol") or ()) + tuple(d.get("prev_symbol") or ()),
        "uniprot_ids": tuple(d.get("uniprot_ids") or ()),
        "_url": res.url,
        "_retrieved_at": res.retrieved_at,
    }


def fetch_ensembl_gene(symbol: str, species: str = "homo_sapiens") -> dict[str, Any] | None:
    res = ENSEMBL.get(
        f"https://rest.ensembl.org/lookup/symbol/{species}/{symbol}",
        params={"content-type": "application/json"},
    )
    if not res or not isinstance(res.data, dict):
        return None
    d = res.data
    return {
        "ensembl_id": d.get("id"),
        "biotype": d.get("biotype"),
        "chromosome": d.get("seq_region_name"),
        "start": d.get("start"),
        "end": d.get("end"),
        "strand": d.get("strand"),
        "description": d.get("description"),
        "_url": res.url,
        "_retrieved_at": res.retrieved_at,
    }


def fetch_uniprot_protein(symbol: str, taxon: int = 9606) -> dict[str, Any] | None:
    """Reviewed (Swiss-Prot) entry for a gene symbol.

    Restricted to reviewed entries deliberately: source_registry lists
    'UniProtKB (reviewed)' as the PROTEIN primary source, and unreviewed TrEMBL
    entries would flood the graph with low-authority duplicates.
    """
    res = UNIPROT.get(
        "https://rest.uniprot.org/uniprotkb/search",
        params={
            "query": f"gene_exact:{symbol} AND organism_id:{taxon} AND reviewed:true",
            "format": "json",
            "size": 1,
            "fields": UNIPROT_FIELDS,
        },
    )
    if not res:
        return None
    results = res.data.get("results") or []
    if not results:
        return None
    r = results[0]
    desc = r.get("proteinDescription") or {}
    rec_name = ((desc.get("recommendedName") or {}).get("fullName") or {}).get("value")
    alt_names = tuple(
        (a.get("fullName") or {}).get("value")
        for a in (desc.get("alternativeNames") or [])
        if (a.get("fullName") or {}).get("value")
    )
    pdb = tuple(
        x.get("id") for x in (r.get("uniProtKBCrossReferences") or [])
        if x.get("database") == "PDB" and x.get("id")
    )
    genes = r.get("genes") or []
    synonyms = tuple(
        s.get("value") for g in genes for s in (g.get("synonyms") or []) if s.get("value")
    )
    return {
        "protein_id": r.get("primaryAccession"),
        "uniprot_id": r.get("uniProtkbId"),
        "name": rec_name or r.get("uniProtkbId"),
        "gene_symbol": ((genes[0] or {}).get("geneName") or {}).get("value") if genes else symbol,
        "length_aa": (r.get("sequence") or {}).get("length"),
        "reviewed": "reviewed" in (r.get("entryType") or "").lower(),
        "pdb_refs": pdb[:20],
        "synonyms": (alt_names + synonyms)[:20],
        "_url": res.url,
        "_retrieved_at": res.retrieved_at,
    }


def fetch_reactome_pathways(uniprot_acc: str, species: str = "9606") -> list[dict[str, Any]]:
    """Pathways a protein participates in.

    Source of the G-03 extension edge (Protein -> Pathway): Part 1 of the Nodes
    doc names Reactome for exactly this, but Part 2 only defined Gene -> Pathway.
    """
    res = REACTOME.get(
        f"https://reactome.org/ContentService/data/mapping/UniProt/{uniprot_acc}/pathways",
        params={"species": species},
    )
    if not res or not isinstance(res.data, list):
        return []
    out = []
    for p in res.data:
        if not p.get("stId"):
            continue
        out.append(
            {
                "pathway_id": p["stId"],
                "name": p.get("displayName") or (p.get("name") or [None])[0],
                "source_db": "reactome",
                "species_name": p.get("speciesName"),
                "is_in_disease": bool(p.get("isInDisease")),
                "_url": res.url,
                "_retrieved_at": res.retrieved_at,
            }
        )
    return out


def fetch_reactome_pathway_hierarchy(stable_id: str) -> list[str]:
    """Parent pathways — supplies the Pathway DAG rather than a flat set."""
    res = REACTOME.get(f"https://reactome.org/ContentService/data/event/{stable_id}/ancestors")
    if not res or not isinstance(res.data, list):
        return []
    out: list[str] = []
    for branch in res.data:
        for ev in branch if isinstance(branch, list) else [branch]:
            sid = (ev or {}).get("stId")
            if sid and sid != stable_id:
                out.append(sid)
    return list(dict.fromkeys(out))


def fetch_gene_bundle(symbol: str) -> dict[str, Any] | None:
    """HGNC + Ensembl + UniProt merged into one gene/protein seed.

    Merge order is recorded in `source_ids` so `PRIM_N08` provenance is honest
    about which source supplied which field.
    """
    hgnc = fetch_hgnc_gene(symbol)
    if not hgnc:
        return None
    ens = fetch_ensembl_gene(symbol)
    prot = fetch_uniprot_protein(symbol)

    source_ids = ["hgnc"]
    if ens:
        source_ids.append("ensembl")
    if prot:
        source_ids.append("uniprotkb")

    return {
        "symbol": symbol,
        "gene": {
            **hgnc,
            "ensembl_id": hgnc.get("ensembl_id") or (ens or {}).get("ensembl_id"),
            "chromosome": (ens or {}).get("chromosome") or hgnc.get("chromosome"),
            "biotype": (ens or {}).get("biotype") or hgnc.get("biotype"),
        },
        "protein": prot,
        "ensembl": ens,
        "source_ids": tuple(source_ids),
    }
