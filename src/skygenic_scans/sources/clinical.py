"""Ontology, disease, phenotype and drug seeds — OLS4, HPO, ChEMBL.

OLS4 (EBI Ontology Lookup Service) is used uniformly for MONDO, GO, UBERON, CL
and EFO. One client for five ontologies keeps `OntologyTerm` genuinely uniform,
which is what SCAN-25 semantic similarity assumes when it compares terms across
namespaces.
"""

from __future__ import annotations

from typing import Any

from .http import Fetcher

OLS = Fetcher("ols4", rate_key="ebi")
HPO = Fetcher("hpo", rate_key="hpo")
CHEMBL = Fetcher("chembl", rate_key="chembl")

OLS_BASE = "https://www.ebi.ac.uk/ols4/api/ontologies"


def _short_form(curie: str) -> str:
    return curie.replace(":", "_")


def fetch_ols_term(curie: str, ontology: str) -> dict[str, Any] | None:
    """One ontology term with its label, definition and direct parents."""
    res = OLS.get(
        f"{OLS_BASE}/{ontology.lower()}/terms",
        params={"short_form": _short_form(curie)},
    )
    if not res:
        return None
    terms = ((res.data.get("_embedded") or {}).get("terms")) or []
    if not terms:
        return None
    t = terms[0]
    desc = t.get("description") or []
    # obo_xref is the structured form; it carries the OMIM/DOID/MESH mappings that
    # let a MONDO disease be joined to sources keyed on other namespaces (HPO
    # annotations are keyed on OMIM, not MONDO).
    xrefs: dict[str, list[str]] = {}
    for x in (t.get("obo_xref") or []):
        db, xid = x.get("database"), x.get("id")
        if db and xid:
            xrefs.setdefault(db.upper(), []).append(str(xid))
    return {
        "term_id": t.get("obo_id") or curie,
        "name": t.get("label"),
        "ontology": ontology.upper(),
        "namespace": (t.get("annotation") or {}).get("has_obo_namespace", [None])[0],
        "definition": desc[0] if desc else None,
        "iri": t.get("iri"),
        "is_obsolete": bool(t.get("is_obsolete")),
        "synonyms": tuple(t.get("synonyms") or ()),
        "xrefs": xrefs,
        "omim_ids": tuple(f"OMIM:{i}" for i in xrefs.get("OMIM", [])),
        "_url": res.url,
        "_retrieved_at": res.retrieved_at,
    }


def fetch_ols_parents(curie: str, ontology: str) -> list[str]:
    """Direct parents — supplies ONTOLOGICALLY_INCLUDES, the DAG SCAN-25 needs."""
    res = OLS.get(
        f"{OLS_BASE}/{ontology.lower()}/terms/"
        f"http%253A%252F%252Fpurl.obolibrary.org%252Fobo%252F{_short_form(curie)}/parents"
    )
    if not res:
        return []
    terms = ((res.data.get("_embedded") or {}).get("terms")) or []
    return [t["obo_id"] for t in terms if t.get("obo_id")]


def fetch_hpo_term(curie: str) -> dict[str, Any] | None:
    res = HPO.get(f"https://ontology.jax.org/api/hp/terms/{curie.replace(':', '%3A')}")
    if not res or not isinstance(res.data, dict):
        return None
    d = res.data
    return {
        "phenotype_id": d.get("id") or curie,
        "name": d.get("name"),
        "definition": d.get("definition"),
        "synonyms": tuple(d.get("synonyms") or ()),
        "_url": res.url,
        "_retrieved_at": res.retrieved_at,
    }


def fetch_hpo_disease_annotations(curie: str) -> list[dict[str, Any]]:
    """Phenotypes annotated to a disease. Supplies real Disease<->Phenotype structure.

    Takes an **OMIM** curie, not MONDO: the JAX annotation endpoint 404s on MONDO
    ids. Use `fetch_ols_term(...)["omim_ids"]` to resolve first. The response nests
    phenotypes under `categories`, which is a dict keyed by organ system.
    """
    res = HPO.get(
        f"https://ontology.jax.org/api/network/annotation/{curie.replace(':', '%3A')}"
    )
    if not res or not isinstance(res.data, dict):
        return []

    out: list[dict[str, Any]] = []

    def collect(items: Any, category: str | None = None) -> None:
        for item in items or []:
            if isinstance(item, dict) and item.get("id"):
                out.append(
                    {
                        "phenotype_id": item["id"],
                        "name": item.get("name"),
                        "category": category,
                        "onset": item.get("onset"),
                        "frequency": item.get("frequency"),
                    }
                )

    cats = res.data.get("categories")
    if isinstance(cats, dict):
        for cat_name, items in cats.items():
            collect(items if isinstance(items, list) else (items or {}).get("terms"), cat_name)
    elif isinstance(cats, list):
        for c in cats:
            collect((c or {}).get("terms"), (c or {}).get("catLabel") or (c or {}).get("name"))

    collect(res.data.get("phenotypes"))

    seen: dict[str, dict] = {}
    for o in out:
        seen.setdefault(o["phenotype_id"], o)
    return list(seen.values())


def search_hpo_diseases(name: str, limit: int = 10) -> list[dict[str, Any]]:
    """Name -> OMIM/ORPHA disease ids, with the MONDO id JAX maps each to."""
    res = HPO.get(
        "https://ontology.jax.org/api/network/search/disease",
        params={"q": name, "limit": limit},
    )
    if not res or not isinstance(res.data, dict):
        return []
    return [
        {"id": r["id"], "name": r.get("name"), "mondo_id": r.get("mondoId")}
        for r in (res.data.get("results") or [])
        if r.get("id")
    ]


def fetch_phenotypes_for_disease(mondo_curie: str, name: str | None = None,
                                 max_sources: int = 3) -> list[dict[str, Any]]:
    """MONDO -> OMIM -> HPO phenotype annotations.

    Two-step because JAX keys annotations on OMIM/ORPHA, not MONDO. Broad MONDO
    terms (e.g. MONDO:0004975 'Alzheimer disease') often carry no direct OMIM
    xref at all — only their subtypes do — so when the xref route comes up empty
    we fall back to a name search and union the annotations across the matching
    subtypes. That union is the right answer biologically: the phenotype profile
    of 'Alzheimer disease' is the profile of its subtypes.
    """
    term = fetch_ols_term(mondo_curie, "mondo")
    label = name or (term or {}).get("name")

    candidates: list[str] = list((term or {}).get("omim_ids") or ())
    if not candidates and label:
        candidates = [d["id"] for d in search_hpo_diseases(label, limit=max_sources * 2)][
            : max_sources
        ]

    merged: dict[str, dict] = {}
    for source_id in candidates[:max_sources]:
        for p in fetch_hpo_disease_annotations(source_id):
            merged.setdefault(p["phenotype_id"], {**p, "source_disease": source_id})
    return list(merged.values())


def fetch_chembl_molecule(chembl_id: str) -> dict[str, Any] | None:
    """Enrichment only.

    Drug identity comes from OpenTargets (which returns ChEMBL ids directly);
    this fills in structure and phase. ChEMBL rate-limits aggressively, so a
    None here degrades the drug node rather than blocking the build.
    """
    res = CHEMBL.get(f"https://www.ebi.ac.uk/chembl/api/data/molecule/{chembl_id}.json")
    if not res or not isinstance(res.data, dict):
        return None
    d = res.data
    structures = d.get("molecule_structures") or {}
    props = d.get("molecule_properties") or {}
    return {
        "drug_id": d.get("molecule_chembl_id") or chembl_id,
        "name": d.get("pref_name"),
        "max_phase": int(d["max_phase"]) if d.get("max_phase") is not None else None,
        "molecule_type": d.get("molecule_type"),
        "first_approval": d.get("first_approval"),
        "smiles": structures.get("canonical_smiles"),
        "inchikey": structures.get("standard_inchi_key"),
        "mw": float(props["full_mwt"]) if props.get("full_mwt") else None,
        "_url": res.url,
        "_retrieved_at": res.retrieved_at,
    }


def fetch_ontology_bundle(curie: str, ontology: str) -> dict[str, Any] | None:
    """Term plus its parent edges in one call pair."""
    term = fetch_ols_term(curie, ontology)
    if not term:
        return None
    return {**term, "parents": fetch_ols_parents(curie, ontology)}
