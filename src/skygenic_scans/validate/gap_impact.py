"""Measures what the two blocking structural gaps actually cost.

Run:  python -m skygenic_scans.validate.gap_impact

docs/schema-gap-analysis.md claims G-01 and G-10 stop scans outright rather than
degrading them. This turns those claims into measurements:

  G-01  Nothing in the Nodes doc connects Gene to Protein. Removing the ENCODES
        extension edge should fragment the graph into components that separate
        genetic evidence from the protein-scoped scans.

  G-10  Part 1 removed the hub-and-spoke hypothesis edges and left no typed
        replacement. Removing HYPOTHESIS_INCLUDES_NODE should leave every
        hypothesis-scoped scan with an empty subgraph.

Written so the claims can be falsified: if removing the edge changes nothing, the
gap analysis is overstating and should be corrected.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..graph.client import get_driver

OUT = Path(__file__).resolve().parents[3] / "data" / "generated" / "gap_impact.json"


def _drop(session, name: str) -> None:
    session.run(
        "CALL gds.graph.exists($n) YIELD exists WITH exists WHERE exists "
        "CALL gds.graph.drop($n) YIELD graphName RETURN graphName", n=name
    ).consume()


def _wcc_stats(session, name: str, exclude: list[str]) -> dict[str, Any]:
    """Weakly-connected-component profile with certain edge types removed."""
    _drop(session, name)
    session.run(
        """
        MATCH (s)-[r]->(t) WHERE NOT type(r) IN $exclude
        WITH gds.graph.project($name, s, t) AS g RETURN g.graphName
        """,
        name=name, exclude=exclude,
    ).consume()
    row = session.run(
        f"CALL gds.wcc.stream('{name}') YIELD nodeId, componentId "
        "WITH componentId, count(*) AS size "
        "RETURN count(*) AS components, max(size) AS largest, "
        "sum(size) AS covered"
    ).single()
    _drop(session, name)
    return dict(row) if row else {}


def measure_g01(session) -> dict[str, Any]:
    """What does the absence of Gene->Protein actually cost?

    The naive test — "can any Gene reach any Protein?" — passes trivially and is
    the wrong question. Tissue is a high-degree hub, so
    `Gene-EXPRESSED_IN->Tissue-CONTAINS_BIOMARKER->Protein` connects almost every
    gene to almost every protein. That path asserts only "this gene is expressed
    in liver, and liver contains this protein". It does **not** mean the gene
    encodes it.

    So the real question is specific, not general: without ENCODES, can a gene
    reach **its own cognate protein**, and is that path distinguishable from the
    spurious hub routes? If not, the scans do not fail loudly — they traverse
    meaningless paths and return confident nonsense, which is worse.
    """
    encodes = session.run(
        "MATCH (:Gene)-[r:ENCODES]->(:Protein) RETURN count(r) AS n"
    ).single()["n"]

    # Cognate pairs: exactly the pairs ENCODES connects.
    cognate = session.run(
        """
        MATCH (g:Gene)-[:ENCODES]->(p:Protein)
        WITH g, p LIMIT 150
        OPTIONAL MATCH path = shortestPath((g)-[*1..4]->(p))
        WHERE none(r IN relationships(path) WHERE type(r) = 'ENCODES')
        RETURN count(*) AS pairs,
               count(path) AS pairs_with_alt_path,
               avg(length(path)) AS mean_alt_length
        """
    ).single()

    # How promiscuous is the spurious hub route specifically? Measured on the
    # exact two-hop shape rather than a generic variable-length expansion —
    # that shape is the finding, and an unbounded [*1..3] is explosive at scale.
    total_proteins = session.run("MATCH (p:Protein) RETURN count(p) AS n").single()["n"]
    promiscuity = session.run(
        """
        MATCH (g:Gene)-[:EXPRESSED_IN]->(:Tissue)
        WITH DISTINCT g LIMIT 10
        MATCH (g)-[:EXPRESSED_IN]->(:Tissue)-[:CONTAINS_BIOMARKER]->(p:Protein)
        WITH g, count(DISTINCT p) AS reached
        RETURN avg(reached) AS mean_proteins_reached, count(g) AS genes_sampled
        """
    ).single()

    with_encodes = _wcc_stats(session, "g01_with", [])
    without_encodes = _wcc_stats(session, "g01_without", ["ENCODES"])

    pairs = cognate["pairs"] or 1
    alt = cognate["pairs_with_alt_path"] or 0
    reached = promiscuity["mean_proteins_reached"] or 0
    total_p = total_proteins or 1
    promiscuity_pct = round(100 * reached / total_p, 1)

    return {
        "encodes_edges": encodes,
        "cognate_pairs_tested": pairs,
        "cognate_pairs_with_alternative_path": alt,
        "pct_cognate_with_alternative": round(100 * alt / pairs, 1),
        "mean_alternative_path_length": round(cognate["mean_alt_length"] or 0, 2),
        "mean_proteins_reached_per_gene_within_3_hops": round(reached, 1),
        "total_proteins": total_p,
        "reachability_promiscuity_pct": promiscuity_pct,
        "components_with_encodes": with_encodes.get("components"),
        "components_without_encodes": without_encodes.get("components"),
        # Two signals, and they point in different directions at scale — the
        # verdict has to weigh both rather than pick the flattering one.
        #
        #   uniqueness  : is ENCODES the ONLY route between a cognate pair?
        #   detour cost : how much longer is the alternative?
        #
        # High uniqueness-failure with a LONG detour is the mild case: any
        # hop-bounded or length-weighted traversal still prefers the real edge.
        # High uniqueness-failure with a SHORT detour is the severe case: the
        # spurious route is indistinguishable from the real one.
        "verdict": _g01_verdict(
            pct_alt=100 * alt / pairs,
            mean_alt_len=cognate["mean_alt_length"] or 0.0,
            promiscuity_pct=promiscuity_pct,
        ),
    }


def _g01_verdict(pct_alt: float, mean_alt_len: float, promiscuity_pct: float) -> str:
    """Classify the G-01 failure mode from uniqueness and detour cost.

    Deliberately able to return a NOT-CONFIRMED result. An earlier draft of the
    gap analysis asserted G-01 disconnects the graph; this measurement exists to
    be capable of contradicting that, and it did.
    """
    if pct_alt <= 80:
        return (
            f"NOT CONFIRMED — only {pct_alt:.1f}% of cognate pairs have an alternative "
            "path, so ENCODES is usually the unique connector; its absence would fail "
            "loudly rather than silently."
        )
    if mean_alt_len <= 2.2 or promiscuity_pct > 20:
        return (
            f"SEVERE — {pct_alt:.1f}% of cognate pairs have a non-ENCODES path at mean "
            f"length {mean_alt_len:.2f}, and a gene reaches {promiscuity_pct}% of all "
            "proteins via the Gene->Tissue->Protein hub. The spurious route is short "
            "enough to be indistinguishable from a real encoding relationship, so "
            "scans return confident wrong answers rather than failing."
        )
    return (
        f"CONFIRMED BUT BOUNDED — ENCODES is never the unique connector "
        f"({pct_alt:.1f}% of cognate pairs have an alternative), but the detour is "
        f"long (mean {mean_alt_len:.2f} hops vs 1) and the Gene->Tissue->Protein hub "
        f"reaches only {promiscuity_pct}% of proteins. Consequences split by traversal "
        "style: length-weighted primitives (T02 Dijkstra on 1-ES, R15 PathScore's "
        "1/len term) still strongly prefer the true edge, so they degrade gracefully. "
        "Unweighted expansion (T01 get_k_hop_neighbors at k>=3, T10 "
        "get_common_neighbors) cannot tell the routes apart. Net effect: hop-bounded "
        "scans return nothing, unbounded ones return spurious gene-protein links. "
        "Still the highest-priority gap — it removes the only 1-hop statement that a "
        "gene encodes a protein — but it is not graph disconnection."
    )


def measure_g10(session) -> dict[str, Any]:
    """Without typed membership, can a hypothesis subgraph be extracted at all?"""
    total = session.run("MATCH (h:SkygenicHypothesis) RETURN count(h) AS n").single()["n"]
    with_members = session.run(
        "MATCH (h:SkygenicHypothesis)-[:HYPOTHESIS_INCLUDES_NODE]->() "
        "RETURN count(DISTINCT h) AS n"
    ).single()["n"]

    # What remains if the typed membership edge is removed? Only the governance
    # spokes (state / chain / score / version) — no biological subgraph.
    # `labels(n)[0]` is unsafe here: every node also carries the :Entity
    # supertype added for index-backed uid lookups, and label order is not
    # guaranteed, so [0] can silently return 'Entity' for everything.
    without = session.run(
        """
        MATCH (h:SkygenicHypothesis)-[r]->(n)
        WHERE type(r) <> 'HYPOTHESIS_INCLUDES_NODE'
        WITH h, [l IN labels(n) WHERE l <> 'Entity'] AS ls
        RETURN count(DISTINCT h) AS hyps,
               collect(DISTINCT ls[0])[..20] AS reachable_labels
        """
    ).single()

    bio_labels = {"Gene", "Protein", "Pathway", "Variant", "BiologicalProcess",
                  "Phenotype", "Drug", "Disease"}
    reachable = set(without["reachable_labels"] or [])
    bio_reachable = sorted(reachable & bio_labels)

    # PRIM_T09 extract_hypothesis_subgraph is specified as
    # "targets + expected_edges + 1-hop neighborhood".
    # The doc DOES supply targets (PRIORITIZES_THERAPEUTIC_TARGET -> Protein) and
    # outcomes (EXPLAINS_CLINICAL_OUTCOME -> Disease). What it does not supply is
    # `expected_edges` or any context membership, so the subgraph can be anchored
    # but not delimited.
    covers_targets = "Protein" in reachable
    covers_outcomes = "Disease" in reachable
    mechanism_labels = sorted(
        reachable & {"Gene", "Pathway", "Variant", "BiologicalProcess", "Phenotype"}
    )

    return {
        "hypotheses": total,
        "with_typed_membership": with_members,
        "labels_reachable_without_membership": sorted(reachable),
        "biological_labels_reachable_without_membership": bio_reachable,
        "doc_supplies_targets": covers_targets,
        "doc_supplies_outcomes": covers_outcomes,
        "mechanism_labels_reachable": mechanism_labels,
        "verdict": (
            "OVERSTATED IN v1 OF THE GAP ANALYSIS — the doc does supply subgraph "
            "anchors: PRIORITIZES_THERAPEUTIC_TARGET gives PRIM_H05.target_nodes "
            "and EXPLAINS_CLINICAL_OUTCOME gives outcome_nodes. What is missing is "
            "the mechanism body between them: no Gene, Pathway, Variant or "
            "BiologicalProcess is reachable from a hypothesis, so PRIM_T09's "
            "'targets + expected_edges + 1-hop' can be anchored but not filled."
            if covers_targets and covers_outcomes and not mechanism_labels else
            f"targets={covers_targets} outcomes={covers_outcomes} "
            f"mechanism={mechanism_labels or 'none'}"
        ),
    }


def main() -> int:
    driver = get_driver()
    try:
        with driver.session() as s:
            print("measuring G-01 (Gene->Protein)…", flush=True)
            g01 = measure_g01(s)
            print("measuring G-10 (hypothesis subgraph)…", flush=True)
            g10 = measure_g10(s)
    finally:
        driver.close()

    print("\n" + "=" * 78)
    print("GAP IMPACT")
    print("=" * 78)
    print("\nG-01  Gene -> Protein (ENCODES)")
    print(f"  ENCODES edges present              : {g01['encodes_edges']:,}")
    print(f"  cognate pairs with an alt path     : "
          f"{g01['cognate_pairs_with_alternative_path']}/{g01['cognate_pairs_tested']} "
          f"({g01['pct_cognate_with_alternative']}%), "
          f"mean length {g01['mean_alternative_path_length']}")
    print(f"  proteins reached per gene via hub  : "
          f"{g01['mean_proteins_reached_per_gene_within_3_hops']:.0f} of "
          f"{g01['total_proteins']:,} ({g01['reachability_promiscuity_pct']}%)")
    print(f"  components with / without ENCODES  : "
          f"{g01['components_with_encodes']} / {g01['components_without_encodes']}")
    print(f"  -> {g01['verdict']}")

    print("\nG-10  Hypothesis subgraph membership")
    print(f"  hypotheses                         : {g10['hypotheses']}")
    print(f"  with typed membership              : {g10['with_typed_membership']}")
    print(f"  doc supplies targets / outcomes    : "
          f"{g10['doc_supplies_targets']} / {g10['doc_supplies_outcomes']}")
    print(f"  mechanism labels reachable         : "
          f"{g10['mechanism_labels_reachable'] or 'none'}")
    print(f"  -> {g10['verdict']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"G-01": g01, "G-10": g10}, indent=1))
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
