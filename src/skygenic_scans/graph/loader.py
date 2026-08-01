"""Bulk-loads data/generated/*.jsonl into Neo4j.

Run:  python -m skygenic_scans.graph.loader [--wipe]

Design notes:

* MERGE on `uid`, which has a uniqueness constraint, so re-running is idempotent.
* Temporal contract fields are converted to native Neo4j `datetime`, not left as
  strings. The scan service is temporal; storing `valid_from`/`valid_to` as text
  would make every window query a string comparison and rule out index-backed
  range scans.
* Map-valued properties are JSON-encoded. Neo4j properties cannot hold nested
  maps, and silently dropping them would remove `computed_fields` from every
  ScanResult.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterator

from .client import get_driver

ROOT = Path(__file__).resolve().parents[3]
GEN = ROOT / "data" / "generated"

# Contract + domain temporal fields, converted to native datetime on write.
TEMPORAL_FIELDS = (
    "created_at", "updated_at", "ingested_at", "valid_from", "valid_to",
    "source_retrieved_at", "entered_at", "timestamp", "computed_at",
    "written_at", "promotion_ts",
)
MAP_FIELDS = ("computed_fields", "narrative_terms", "payload")

# Batch sizes are a memory decision, not a throughput one. Each row carries ~40
# properties and edge rows MERGE against an existing relationship, so the
# transaction working set grows fast. At 5,000 the edge load exhausted the
# 2.8 GiB transaction pool part-way through and left the graph half-written.
NODE_BATCH = 2_000
EDGE_BATCH = 1_000


def _read_jsonl(path: Path) -> Iterator[dict]:
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _clean(rec: dict) -> dict:
    """Drop internal keys and JSON-encode map-valued properties."""
    out = {}
    for k, v in rec.items():
        if k in ("_label", "_from", "_to", "_type"):
            continue
        if k in MAP_FIELDS:
            out[k] = json.dumps(v or {})
        elif isinstance(v, dict):
            out[k] = json.dumps(v)
        else:
            out[k] = v
    return out


def _temporal_setters(alias: str, keys: set[str]) -> str:
    """Cypher fragment converting ISO strings to native datetime."""
    present = [f for f in TEMPORAL_FIELDS if f in keys]
    if not present:
        return ""
    parts = [
        f"{alias}.{f} = CASE WHEN row.{f} IS NULL THEN NULL ELSE datetime(row.{f}) END"
        for f in present
    ]
    return "SET " + ", ".join(parts)


def load_nodes(session, path: Path, verbose: bool = True) -> int:
    by_label: dict[str, list[dict]] = {}
    for rec in _read_jsonl(path):
        by_label.setdefault(rec["_label"], []).append(_clean(rec))

    total = 0
    for label, recs in by_label.items():
        keys = set().union(*(r.keys() for r in recs)) if recs else set()
        temporal = _temporal_setters("n", keys)
        # :Entity is the shared supertype that makes uid lookups index-backed
        # during edge loading (see constraints.py).
        cypher = (
            f"UNWIND $rows AS row "
            f"MERGE (n:`{label}` {{uid: row.uid}}) "
            f"SET n:Entity, n += row "
            f"{temporal}"
        )
        for i in range(0, len(recs), NODE_BATCH):
            chunk = recs[i : i + NODE_BATCH]
            session.run(cypher, rows=chunk).consume()
            total += len(chunk)
        if verbose:
            print(f"  {label:24s} {len(recs):>7,}", flush=True)
    return total


def load_edges(session, path: Path, verbose: bool = True) -> int:
    by_type: dict[str, list[dict]] = {}
    for rec in _read_jsonl(path):
        # Endpoints travel outside `props` so they never land as edge properties.
        by_type.setdefault(rec["_type"], []).append(
            {"from": rec["_from"], "to": rec["_to"], "props": _clean(rec)}
        )

    total = 0
    for rtype, recs in by_type.items():
        keys = set().union(*(r["props"].keys() for r in recs)) if recs else set()
        temporal = _temporal_setters("r", keys).replace("row.", "row.props.")
        cypher = (
            f"UNWIND $rows AS row "
            f"MATCH (a:Entity {{uid: row.from}}) "
            f"MATCH (b:Entity {{uid: row.to}}) "
            f"MERGE (a)-[r:`{rtype}`]->(b) "
            f"SET r += row.props "
            f"{temporal}"
        )
        for i in range(0, len(recs), EDGE_BATCH):
            chunk = recs[i : i + EDGE_BATCH]
            session.run(cypher, rows=chunk).consume()
            total += len(chunk)
        if verbose and len(recs) >= 2000:
            print(f"  {rtype:34s} {len(recs):>7,}", flush=True)
    return total


def wipe(session) -> None:
    """Delete every node, then verify the graph is actually empty.

    The previous implementation batched `DETACH DELETE` in the driver and could
    die part-way on a large graph without saying so. That left a mix of two
    different builds in the database and silently invalidated every downstream
    measurement. apoc.periodic.iterate commits per batch, and the assertion
    afterwards means a failed wipe can never again be mistaken for a clean one.
    """
    print("wiping existing data…", flush=True)

    # `CALL { … } IN TRANSACTIONS` is the supported replacement for
    # apoc.periodic.iterate, which is deprecated and — observed here — can
    # return successfully while leaving tens of thousands of nodes behind.
    # Looped because a single pass is not guaranteed to drain the graph.
    for attempt in range(1, 11):
        remaining = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        if remaining == 0:
            break
        print(f"  pass {attempt}: {remaining:,} nodes remaining…", flush=True)
        session.run(
            "MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS"
        ).consume()

    remaining = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
    if remaining:
        raise RuntimeError(
            f"wipe incomplete: {remaining:,} nodes remain after 10 passes. Refusing to "
            f"load on top of a partially-cleared graph — that mixes two builds and "
            f"corrupts every downstream measurement."
        )
    print("  wipe verified: graph is empty", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wipe", action="store_true", help="delete all nodes before loading")
    args = ap.parse_args()

    nodes_p, edges_p = GEN / "nodes.jsonl", GEN / "edges.jsonl"
    if not nodes_p.exists():
        print(f"missing {nodes_p}; run `python -m skygenic_scans.synth.build` first",
              file=sys.stderr)
        return 1

    started = time.time()
    driver = get_driver()
    try:
        with driver.session() as s:
            if args.wipe:
                wipe(s)
            print("loading nodes…", flush=True)
            n = load_nodes(s, nodes_p)
            print("loading edges…", flush=True)
            e = load_edges(s, edges_p)
            counts = s.run(
                "MATCH (n) WITH count(n) AS nodes "
                "MATCH ()-[r]->() RETURN nodes, count(r) AS rels"
            ).single()
    finally:
        driver.close()

    print(f"\nloaded {n:,} node rows, {e:,} edge rows in {time.time() - started:.0f}s")
    print(f"graph now holds {counts['nodes']:,} nodes / {counts['rels']:,} relationships")

    # Verify the database matches what was generated. A partial load is worse
    # than a failed one: every validator downstream reports confident numbers
    # against a graph that is quietly wrong.
    problems = []
    if counts["nodes"] != n:
        problems.append(f"nodes: generated {n:,}, loaded {counts['nodes']:,}")
    if counts["rels"] != e:
        problems.append(f"relationships: generated {e:,}, loaded {counts['rels']:,}")

    manifest_p = GEN / "manifest.json"
    if manifest_p.exists():
        man = json.loads(manifest_p.read_text())["totals"]
        if man["nodes"] != counts["nodes"]:
            problems.append(f"manifest nodes {man['nodes']:,} != loaded {counts['nodes']:,}")
        if man["edges"] != counts["rels"]:
            problems.append(f"manifest edges {man['edges']:,} != loaded {counts['rels']:,}")

    if problems:
        print("\nLOAD VERIFICATION FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("\nDo NOT run the validators against this graph.", file=sys.stderr)
        return 2

    print("load verified: database matches manifest exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
