"""Builds the full synthetic graph and writes it to data/generated/.

Run:  python -m skygenic_scans.synth.build [--smoke]

Output:
  data/generated/nodes.jsonl   one stamped node per line
  data/generated/edges.jsonl   one stamped edge per line
  data/generated/manifest.json counts, provenance split, coverage report
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..provenance import VersionTriplet, validate_node
from ..schema import load_schema
from .config import DEFAULT_SCALE, RANDOM_SEED, ScaleConfig, SmokeScale
from .edges import EdgeBuilder
from .entities import BuildContext, EntityBuilder
from .governance import GovernanceBuilder

ROOT = Path(__file__).resolve().parents[3]
SEED_BUNDLE = ROOT / "data" / "seeds" / "seed_bundle.json"
OUT_DIR = ROOT / "data" / "generated"
SCANS = ROOT / "schema" / "scans.json"


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def build(scale: ScaleConfig, seed: int = RANDOM_SEED,
          now: datetime | None = None) -> dict[str, Any]:
    """Build the graph.

    `now` pins the build clock. Left unset it is wall-clock, which makes the
    output deterministic in *content* but not in *bytes*: created_at,
    updated_at, ingested_at, valid_from and promotion_ts all move between runs.
    Pass a fixed timestamp (or set SKYGENIC_BUILD_NOW) when you need byte-exact
    reproducibility — e.g. to diff two builds, or to prove a refactor changed
    nothing.
    """
    schema = load_schema()
    seeds = json.loads(SEED_BUNDLE.read_text()) if SEED_BUNDLE.exists() else {}
    if not seeds:
        print(f"WARNING: no seed bundle at {SEED_BUNDLE}; graph will be fully synthetic",
              file=sys.stderr)

    scan_ids = list(json.loads(SCANS.read_text())) if SCANS.exists() else []
    # Agriculture is out of scope per the build decision; its scans must not
    # appear in generated ScanResults or the coverage report would overstate.
    scan_ids = [s for s in scan_ids if not s.startswith("AG-")]

    ctx = BuildContext(
        schema=schema,
        scale=scale,
        seeds=seeds,
        rng=random.Random(seed),
        versions=VersionTriplet(Dv=7, Tv=12, Hv=1),
        now=now or datetime.now(timezone.utc),
    )

    print("building entities…", flush=True)
    ent = EntityBuilder(ctx)
    ent.build_all()

    print("building governance layer…", flush=True)
    gov = GovernanceBuilder(ctx, ent)
    gov.build_all(scan_ids)

    print("building edges…", flush=True)
    eb = EdgeBuilder(ctx, ent, gov)
    edges = eb.build_all()

    all_nodes = {**ent.nodes}
    for label, recs in gov.nodes.items():
        all_nodes.setdefault(label, []).extend(recs)

    # Uniqueness first. A duplicate uid does not fail the contract check — both
    # records are individually valid — but MERGE collapses them at load time, so
    # the only symptom is a node count that is quietly short. Caught here it is
    # one line; caught at load it looks like a loader bug.
    print("checking uid uniqueness…", flush=True)
    seen_uids: dict[str, str] = {}
    collisions: list[str] = []
    for label, recs in all_nodes.items():
        for r in recs:
            prev = seen_uids.get(r["uid"])
            if prev is not None:
                collisions.append(f"{r['uid']} emitted by {prev} and {label}")
            else:
                seen_uids[r["uid"]] = label
    if collisions:
        print(f"DUPLICATE uids ({len(collisions)}), build aborted:", file=sys.stderr)
        for cdup in collisions[:10]:
            print(f"  {cdup}", file=sys.stderr)
        raise SystemExit(1)

    # Contract enforcement: every node must satisfy node_property_contract.
    print("validating node contract…", flush=True)
    violations: list[str] = []
    for label, recs in all_nodes.items():
        for r in recs:
            for v in validate_node(schema, r):
                violations.append(f"{label} {r.get('uid')}: {v}")
                if len(violations) > 50:
                    break
    if violations:
        print(f"CONTRACT VIOLATIONS ({len(violations)} shown, build aborted):", file=sys.stderr)
        for v in violations[:20]:
            print(f"  {v}", file=sys.stderr)
        raise SystemExit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    node_count = 0
    with (OUT_DIR / "nodes.jsonl").open("w") as fh:
        for label, recs in all_nodes.items():
            for r in recs:
                fh.write(json.dumps({"_label": label, **r}, default=_json_default) + "\n")
                node_count += 1
    with (OUT_DIR / "edges.jsonl").open("w") as fh:
        for e in edges:
            fh.write(json.dumps(e, default=_json_default) + "\n")

    real_nodes = sum(1 for recs in all_nodes.values() for r in recs if not r["is_synthetic"])
    real_edges = sum(1 for e in edges if not e["is_synthetic"])

    manifest = {
        "generated_at": ctx.now.isoformat(),
        "random_seed": seed,
        "schema_version": schema.version,
        "totals": {
            "nodes": node_count,
            "edges": len(edges),
            "node_labels": len(all_nodes),
            "edge_types": len({e["_type"] for e in edges}),
        },
        "provenance_split": {
            "nodes_real": real_nodes,
            "nodes_synthetic": node_count - real_nodes,
            "real_node_pct": round(100 * real_nodes / max(1, node_count), 2),
            "edges_real": real_edges,
            "edges_synthetic": len(edges) - real_edges,
            "real_edge_pct": round(100 * real_edges / max(1, len(edges)), 2),
        },
        "nodes_by_label": {k: len(v) for k, v in sorted(all_nodes.items())},
        "edges_by_type": _count(edges, "_type"),
        "edges_by_layer": _count(edges, "layer"),
        "edges_by_tier": _count(edges, "edge_quality_tier"),
        "edges_by_conflict_ref": _count([e for e in edges if e.get("conflict_ref")],
                                        "conflict_ref"),
        "relationship_coverage": eb.coverage(),
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def _count(items: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        v = i.get(key)
        if v is not None:
            out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="tiny graph for fast iteration")
    ap.add_argument("--seed", type=int, default=RANDOM_SEED)
    ap.add_argument(
        "--now", default=os.getenv("SKYGENIC_BUILD_NOW"),
        help="pin the build clock to an ISO8601 instant for byte-exact reproducibility "
             "(default: wall clock, which makes output content-stable but not byte-stable)",
    )
    args = ap.parse_args()

    pinned = None
    if args.now:
        pinned = datetime.fromisoformat(args.now)
        if pinned.tzinfo is None:
            pinned = pinned.replace(tzinfo=timezone.utc)

    m = build(SmokeScale() if args.smoke else DEFAULT_SCALE, seed=args.seed, now=pinned)
    t, p = m["totals"], m["provenance_split"]
    print(f"\nnodes={t['nodes']:,} across {t['node_labels']} labels")
    print(f"edges={t['edges']:,} across {t['edge_types']} types")
    print(f"real: {p['nodes_real']:,} nodes ({p['real_node_pct']}%), "
          f"{p['edges_real']:,} edges ({p['real_edge_pct']}%)")
    cov = m["relationship_coverage"]
    print(f"endpoint pairs built: {cov['built_endpoint_pairs']}/{cov['declared_endpoint_pairs']}")
    if cov["unbuilt"]:
        print(f"  unbuilt: {cov['unbuilt'][:8]}{'…' if len(cov['unbuilt']) > 8 else ''}")
    print(f"\nwrote {OUT_DIR}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
