"""Association and network seeds — OpenTargets, STRING, GWAS Catalog, GTEx.

These supply the *edges* the graph is validated on. Node identity comes from
biomolecules/clinical; this module supplies the topology that centrality,
path-finding and link-prediction scans actually operate over.
"""

from __future__ import annotations

from typing import Any

from .http import Fetcher

OPENTARGETS = Fetcher("opentargets", rate_key="opentargets")
STRING = Fetcher("string", rate_key="string")
GWAS = Fetcher("gwas_catalog", rate_key="ebi")
GTEX = Fetcher("gtex", rate_key="gtex")

OT_URL = "https://api.platform.opentargets.org/api/v4/graphql"

# Field names verified against the live v4 schema by introspection (2026-08-01).
# Note `drugAndClinicalCandidates`, not `knownDrugs` — the latter no longer exists
# on Target. `actionType` is the important one: it yields a real INHIBITOR /
# ACTIVATOR label, which is what lets PHARMACOLOGICALLY_INHIBITS vs
# PHARMACOLOGICALLY_ACTIVATES be assigned from evidence rather than invented.
_OT_TARGET_QUERY = """
query T($ensemblId: String!, $n: Int!) {
  target(ensemblId: $ensemblId) {
    id
    approvedSymbol
    biotype
    associatedDiseases(page: {index: 0, size: $n}) {
      count
      rows { score datatypeScores { id score } disease { id name therapeuticAreas { id name } } }
    }
    drugAndClinicalCandidates {
      count
      rows {
        maxClinicalStage
        drug {
          id name drugType maximumClinicalStage
          mechanismsOfAction { rows { mechanismOfAction actionType } }
        }
        diseases { disease { id name } }
      }
    }
    homologues {
      targetGeneId targetGeneSymbol speciesId speciesName
      homologyType queryPercentageIdentity targetPercentageIdentity isHighConfidence
    }
  }
}
"""

# ChEMBL/OpenTargets actionType -> SCAN-03 directional signal.
_ACTION_DIRECTION = {
    "INHIBITOR": -1, "ANTAGONIST": -1, "BLOCKER": -1, "NEGATIVE MODULATOR": -1,
    "NEGATIVE ALLOSTERIC MODULATOR": -1, "RNAI INHIBITOR": -1, "DEGRADER": -1,
    "ANTISENSE INHIBITOR": -1, "DISRUPTING AGENT": -1,
    "AGONIST": 1, "ACTIVATOR": 1, "POSITIVE MODULATOR": 1, "OPENER": 1,
    "POSITIVE ALLOSTERIC MODULATOR": 1, "PARTIAL AGONIST": 1, "STABILISER": 1,
}


def action_direction(action_type: str | None) -> int:
    """Map a mechanism actionType to {-1, 0, +1}. Unknown/modulatory -> 0."""
    return _ACTION_DIRECTION.get((action_type or "").strip().upper(), 0)


def _norm_curie(x: str | None) -> str | None:
    """OpenTargets returns MONDO_0004975; the rest of the graph uses MONDO:0004975."""
    if not x:
        return None
    return x.replace("_", ":", 1) if "_" in x and ":" not in x else x


def fetch_opentargets_target(ensembl_id: str, n: int = 25) -> dict[str, Any] | None:
    """Gene-disease associations and known drugs for one target.

    OpenTargets is the single richest seed here: it supplies GENETICALLY_LINKS_TO
    (gene->disease with a real evidence score), CLINICALLY_TREATS (drug->disease)
    and the drug->target mechanism direction that SCAN-03 needs.
    """
    res = OPENTARGETS.post_json(
        OT_URL, {"query": _OT_TARGET_QUERY, "variables": {"ensemblId": ensembl_id, "n": n}}
    )
    if not res:
        return None
    tgt = ((res.data or {}).get("data") or {}).get("target")
    if not tgt:
        return None

    diseases = []
    for row in ((tgt.get("associatedDiseases") or {}).get("rows") or []):
        d = row.get("disease") or {}
        if not d.get("id"):
            continue
        diseases.append(
            {
                "disease_id": _norm_curie(d["id"]),
                "name": d.get("name"),
                "score": row.get("score"),
                "datatype_scores": {
                    s["id"]: s["score"] for s in (row.get("datatypeScores") or []) if s.get("id")
                },
                "therapeutic_areas": [
                    ta.get("name") for ta in (d.get("therapeuticAreas") or []) if ta.get("name")
                ],
            }
        )

    drugs = []
    for row in ((tgt.get("drugAndClinicalCandidates") or {}).get("rows") or []):
        drug = row.get("drug") or {}
        if not drug.get("id"):
            continue
        moa_rows = ((drug.get("mechanismsOfAction") or {}).get("rows")) or []
        moa = moa_rows[0] if moa_rows else {}
        # `diseases` entries can carry a null `disease`; filter before use.
        indications = [
            {"disease_id": _norm_curie((x.get("disease") or {}).get("id")),
             "name": (x.get("disease") or {}).get("name")}
            for x in (row.get("diseases") or [])
            if (x.get("disease") or {}).get("id")
        ]
        drugs.append(
            {
                "drug_id": drug["id"],
                "name": drug.get("name"),
                "drug_type": drug.get("drugType"),
                "max_clinical_stage": drug.get("maximumClinicalStage")
                or row.get("maxClinicalStage"),
                "mechanism_of_action": moa.get("mechanismOfAction"),
                "action_type": moa.get("actionType"),
                "direction": action_direction(moa.get("actionType")),
                "indications": indications,
            }
        )

    # Orthologs/paralogs -> ORTHOLOG_OF and CONSERVED_IN. PRIM_T15 specifies
    # min_confidence 0.70, and PRIM_R22 multiplies orthology confidence terms,
    # so percentage identity is carried through rather than flattened to a bool.
    homologues = []
    for h in (tgt.get("homologues") or []):
        if not h.get("targetGeneSymbol") or not h.get("speciesId"):
            continue
        homologues.append(
            {
                "target_gene_id": h.get("targetGeneId"),
                "target_symbol": h.get("targetGeneSymbol"),
                "species_id": h.get("speciesId"),
                "species_name": h.get("speciesName"),
                "homology_type": h.get("homologyType"),
                "is_ortholog": "ortholog" in (h.get("homologyType") or ""),
                "query_pct_identity": h.get("queryPercentageIdentity"),
                "target_pct_identity": h.get("targetPercentageIdentity"),
                "is_high_confidence": bool(h.get("isHighConfidence")),
            }
        )

    return {
        "ensembl_id": tgt.get("id"),
        "symbol": tgt.get("approvedSymbol"),
        "biotype": tgt.get("biotype"),
        "diseases": diseases,
        "drugs": drugs,
        "homologues": homologues,
        "_url": res.url,
        "_retrieved_at": res.retrieved_at,
    }


def fetch_string_network(symbols: tuple[str, ...], taxon: int = 9606,
                         min_score: float = 0.4) -> list[dict[str, Any]]:
    """Protein-protein interactions with STRING's evidence-channel breakdown.

    The channel scores matter: `escore` (experimental) and `dscore` (database)
    map to high-confidence evidence types, while `tscore` (text-mining) maps to
    `text_mined` in PRIM_E02's lookup. That distinction is what makes SCAN-01's
    tiering meaningful instead of uniform.
    """
    if not symbols:
        return []
    res = STRING.get(
        "https://string-db.org/api/json/network",
        params={"identifiers": "\r".join(symbols), "species": taxon},
    )
    if not res or not isinstance(res.data, list):
        return []
    out = []
    for e in res.data:
        score = float(e.get("score") or 0)
        if score < min_score:
            continue
        out.append(
            {
                "a": e.get("preferredName_A"),
                "b": e.get("preferredName_B"),
                "ensp_a": e.get("stringId_A"),
                "ensp_b": e.get("stringId_B"),
                "score": score,
                "experimental": float(e.get("escore") or 0),
                "database": float(e.get("dscore") or 0),
                "textmining": float(e.get("tscore") or 0),
                "coexpression": float(e.get("ascore") or 0),
                "_url": res.url,
                "_retrieved_at": res.retrieved_at,
            }
        )
    return out


def fetch_gwas_variants_for_gene(symbol: str, size: int = 20) -> list[dict[str, Any]]:
    """SNPs mapped to a gene, with genomic coordinates.

    Supplies the `chromosome` / `base_position` that PRIM_G01 requires on the
    GeneticVariant class.
    """
    res = GWAS.get(
        "https://www.ebi.ac.uk/gwas/rest/api/singleNucleotidePolymorphisms/search/findByGene",
        params={"geneName": symbol, "size": size},
    )
    if not res:
        return []
    snps = ((res.data.get("_embedded") or {}).get("singleNucleotidePolymorphisms")) or []
    out = []
    for s in snps:
        if not s.get("rsId"):
            continue
        loc = (s.get("locations") or [{}])[0]
        out.append(
            {
                "variant_id": s["rsId"],
                "chromosome": str(loc.get("chromosomeName") or ""),
                "base_position": loc.get("chromosomePosition"),
                "consequence": s.get("functionalClass"),
                "cytogenetic_region": ((loc.get("region") or {}).get("name")),
                "gene_symbol": symbol,
                "_url": res.url,
                "_retrieved_at": res.retrieved_at,
            }
        )
    return out


def fetch_gwas_associations(rs_id: str, size: int = 10) -> list[dict[str, Any]]:
    """Trait associations with real p-values and effect sizes.

    `p_raw` and `effect_weight` on the Variant node come from here — they are
    PRIM_G01-mandated and drive PRIM_R19 polygenic scoring and PRIM_R21 QTL scoring.
    """
    res = GWAS.get(
        f"https://www.ebi.ac.uk/gwas/rest/api/singleNucleotidePolymorphisms/{rs_id}/associations",
        params={"size": size},
    )
    if not res:
        return []
    assocs = ((res.data.get("_embedded") or {}).get("associations")) or []
    out = []
    for a in assocs:
        traits = [
            {"trait_id": (t.get("shortForm") or "").replace("_", ":", 1), "name": t.get("trait")}
            for t in (a.get("efoTraits") or [])
            if t.get("trait")
        ]
        out.append(
            {
                "variant_id": rs_id,
                "p_value": a.get("pvalue"),
                "p_mantissa": a.get("pvalueMantissa"),
                "p_exponent": a.get("pvalueExponent"),
                "or_per_copy": a.get("orPerCopyNum"),
                "beta": a.get("betaNum"),
                "beta_direction": a.get("betaDirection"),
                "risk_frequency": a.get("riskFrequency"),
                "traits": traits,
                "_url": res.url,
                "_retrieved_at": res.retrieved_at,
            }
        )
    return out


def fetch_gtex_gencode_id(symbol: str) -> str | None:
    """GTEx keys expression on versioned GENCODE ids, not symbols or plain ENSG."""
    res = GTEX.get("https://gtexportal.org/api/v2/reference/gene", params={"geneId": symbol})
    if not res:
        return None
    for g in (res.data.get("data") or []):
        if g.get("geneSymbol", "").upper() == symbol.upper() and g.get("gencodeId"):
            return g["gencodeId"]
    return None


def fetch_gtex_expression(symbol: str) -> list[dict[str, Any]]:
    """Median TPM per tissue — the real basis for EXPRESSED_IN edges.

    Using measured expression rather than invented values means SCAN-08's
    Context-Universal vs Highly-Specific classification is being tested against a
    realistic tissue-breadth distribution.
    """
    gencode = fetch_gtex_gencode_id(symbol)
    if not gencode:
        return []
    # datasetId is mandatory in practice: omitting it (or passing gtex_v10)
    # returns an empty result set rather than an error.
    res = GTEX.get(
        "https://gtexportal.org/api/v2/expression/medianGeneExpression",
        params={"gencodeId": gencode, "datasetId": "gtex_v8", "itemsPerPage": 100},
    )
    if not res:
        return []
    out = []
    for row in (res.data.get("data") or []):
        if row.get("median") is None:
            continue
        out.append(
            {
                "gene_symbol": symbol,
                "gencode_id": gencode,
                "tissue": row.get("tissueSiteDetailId"),
                # GTEx returns the UBERON id directly, so Tissue nodes are keyed
                # on a real ontology term instead of a GTEx-local tissue string.
                "tissue_uberon": row.get("ontologyId"),
                "median_tpm": float(row["median"]),
                "unit": row.get("unit"),
                "_url": res.url,
                "_retrieved_at": res.retrieved_at,
            }
        )
    return out
