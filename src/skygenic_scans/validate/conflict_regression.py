"""Guards against schema conflicts reappearing after the v2 resolution.

Run:  python -m skygenic_scans.validate.conflict_regression

Replaces the old `conflict_impact` module, whose job — quantifying the cost of
shipping the doc-verbatim schema — is finished. Those numbers are recorded in the
README and preserved in `data/generated/v1-baseline/conflict_impact.json`. To
re-derive them, rebuild with `SKYGENIC_SCHEMA=v1`.

What this checks instead, on every run:

1. the active schema declares no unresolved conflicts;
2. no relationship retired by `resolutions.yaml` has reappeared in the schema as
   active, on its retired endpoint pair;
3. no such relationship exists in the loaded graph;
4. every retired row still carries a `superseded_by` that resolves to a live type.

Non-zero exit on any failure, so it can sit in CI. The failure mode this exists
to catch is quiet: re-adding `EXPRESSES` alongside `EXPRESSED_IN` would not error
anywhere, it would just silently re-inflate degree and start moving target
rankings again.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

from ..graph.client import get_driver
from ..schema import SCHEMA_PATH, load_schema

ROOT = Path(__file__).resolve().parents[3]
RESOLUTIONS = ROOT / "schema" / "resolutions.yaml"
BASELINE = ROOT / "data" / "generated" / "v1-baseline" / "conflict_impact.json"
OUT = ROOT / "data" / "generated" / "conflict_regression.json"


def retired_triples() -> list[dict[str, str]]:
    res = yaml.safe_load(RESOLUTIONS.read_text())
    out = []
    for r in res.get("resolutions", []):
        for row in r.get("retire", []) or []:
            out.append({
                "type": row["type"], "from": row["from"], "to": row["to"],
                "conflict": r["conflict"],
                "superseded_by": row.get("superseded_by") or r.get("new_type")
                or (r.get("keep") or {}).get("type"),
            })
    return out


def check_schema(retired: list[dict]) -> list[str]:
    s = load_schema()
    problems: list[str] = []

    if s.conflicts():
        problems.append(
            f"active schema declares unresolved conflicts: {sorted(s.conflicts())}"
        )

    active = {r.endpoint_key for r in s.relationships}
    for row in retired:
        t = (row["type"], row["from"], row["to"])
        if t in active:
            problems.append(
                f"{row['conflict']}: retired {row['type']}({row['from']}->{row['to']}) "
                f"is ACTIVE again in {SCHEMA_PATH.name}"
            )

    active_types = {r.type for r in s.relationships}
    raw = s.raw["relationships"]
    for r in raw:
        if r.get("status") != "retired":
            continue
        sup = r.get("superseded_by")
        if not sup:
            problems.append(f"retired {r['type']} has no superseded_by pointer")
        elif sup not in active_types:
            problems.append(
                f"retired {r['type']} points at {sup!r}, which is not an active type"
            )
    return problems


def check_graph(retired: list[dict]) -> tuple[list[str], dict[str, Any]]:
    problems: list[str] = []
    found: dict[str, int] = {}
    driver = get_driver()
    try:
        with driver.session() as s:
            counts = s.run(
                """
                MATCH (a)-[r]->(b)
                WHERE any(x IN $retired WHERE
                          type(r) = x[0] AND x[1] IN labels(a) AND x[2] IN labels(b))
                RETURN type(r) AS t, count(r) AS n ORDER BY n DESC
                """,
                retired=[[r["type"], r["from"], r["to"]] for r in retired],
            )
            for row in counts:
                found[row["t"]] = row["n"]
            totals = s.run(
                "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() "
                "RETURN nodes, count(r) AS rels, count(DISTINCT type(r)) AS types"
            ).single()
            totals = dict(totals) if totals else {}
    finally:
        driver.close()

    for t, n in found.items():
        problems.append(f"retired relationship {t} present in the loaded graph ({n:,} edges)")
    return problems, totals


def main() -> int:
    retired = retired_triples()
    print(f"active schema : {SCHEMA_PATH.name} (v{load_schema().version})")
    print(f"retired rows  : {len(retired)} from resolutions.yaml\n")

    problems = check_schema(retired)
    graph_problems, totals = check_graph(retired)
    problems += graph_problems

    if totals:
        print(f"graph: {totals['nodes']:,} nodes / {totals['rels']:,} rels "
              f"/ {totals['types']} relationship types")

    if BASELINE.exists():
        b = json.loads(BASELINE.read_text())
        print("\nhistorical baseline (v1 doc-verbatim, for context):")
        print(f"  edges that dedup removed : {b['edges_removed']:,} "
              f"({b['edges_removed_pct']}%)")
        print(f"  network-role changes     : {b['role_changes']}")
        print(f"  top-50 target churn      : {b['top50_ranking_churn']} of 50")
        print("  -> resolved in v2; those are the numbers this guard protects.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "schema": SCHEMA_PATH.name,
        "retired_rows_checked": len(retired),
        "graph_totals": totals,
        "problems": problems,
        "passed": not problems,
    }, indent=1))

    if problems:
        print(f"\nREGRESSION DETECTED ({len(problems)}):", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    print("\nPASS — no retired relationship has reappeared in the schema or the graph")
    return 0


if __name__ == "__main__":
    sys.exit(main())
