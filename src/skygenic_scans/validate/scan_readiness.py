"""Per-scan readiness report against the loaded graph.

Run:  python -m skygenic_scans.validate.scan_readiness

For each in-scope scan, resolves its declared primitives to graph capabilities,
evaluates each capability once against Neo4j, and reports whether the scan could
actually execute. Writes data/generated/scan_readiness.json.

Verdicts:
  READY        every capability the scan needs is satisfied
  BLOCKED      a capability is unsatisfied but achievable with more data/edges
  UNSUPPORTED  a capability cannot be met by the property graph at all
               (in practice: the vector/embedding layer)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ..graph.client import get_driver
from .capabilities import CAPABILITIES, capabilities_for

ROOT = Path(__file__).resolve().parents[3]
SCANS = ROOT / "schema" / "scans.json"
OUT = ROOT / "data" / "generated" / "scan_readiness.json"


def evaluate_capabilities(session) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for name, cap in CAPABILITIES.items():
        if cap.unsupported_reason:
            results[name] = {
                "status": "UNSUPPORTED",
                "description": cap.description,
                "detail": cap.unsupported_reason,
            }
            continue
        try:
            row = session.run(cap.cypher).single()
            row = dict(row) if row else None
            ok = cap.passes(row)
            results[name] = {
                "status": "PASS" if ok else "FAIL",
                "description": cap.description,
                "detail": cap.detail(row) if row else "no rows",
            }
        except Exception as exc:  # noqa: BLE001
            results[name] = {
                "status": "ERROR",
                "description": cap.description,
                "detail": f"{type(exc).__name__}: {exc}"[:200],
            }
    return results


def assess_scans(cap_results: dict[str, dict]) -> dict[str, Any]:
    scans = json.loads(SCANS.read_text())
    out: dict[str, Any] = {}
    for sid, meta in scans.items():
        if sid.startswith("AG-"):
            continue  # agriculture is out of scope for this build
        needed: dict[str, str] = {}
        for prim in meta.get("primitives") or []:
            for cap in capabilities_for(prim):
                needed[cap] = cap_results.get(cap, {}).get("status", "ERROR")

        # INV sheets declare no primitives; they are query shapes over the graph
        # and need core topology plus scored edges.
        if not needed:
            needed = {c: cap_results.get(c, {}).get("status", "ERROR")
                      for c in ("topology", "edge_scores")}

        unsupported = sorted(k for k, v in needed.items() if v == "UNSUPPORTED")
        failed = sorted(k for k, v in needed.items() if v in ("FAIL", "ERROR"))
        verdict = "READY" if not unsupported and not failed else (
            "UNSUPPORTED" if unsupported else "BLOCKED"
        )
        out[sid] = {
            "family": meta.get("family"),
            "scope": meta.get("scope"),
            "primitives": meta.get("primitives") or [],
            "capabilities": needed,
            "blocking": failed,
            "unsupported": unsupported,
            "verdict": verdict,
        }
    return out


def main() -> int:
    driver = get_driver()
    try:
        with driver.session() as s:
            caps = evaluate_capabilities(s)
    finally:
        driver.close()

    scans = assess_scans(caps)

    # --- capability summary ---------------------------------------------
    print("=" * 78)
    print("CAPABILITY CHECKS")
    print("=" * 78)
    for name, r in sorted(caps.items(), key=lambda kv: (kv[1]["status"], kv[0])):
        mark = {"PASS": "PASS", "FAIL": "FAIL", "UNSUPPORTED": "N/A ", "ERROR": "ERR "}[r["status"]]
        print(f"  [{mark}] {name:24s} {r['detail'][:88]}")

    # --- scan verdicts ---------------------------------------------------
    counts: dict[str, int] = {}
    for v in scans.values():
        counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1

    print()
    print("=" * 78)
    print(f"SCAN READINESS  ({len(scans)} in-scope scans; 10 AG-SCAN excluded)")
    print("=" * 78)
    for verdict in ("READY", "BLOCKED", "UNSUPPORTED"):
        n = counts.get(verdict, 0)
        pct = 100 * n / max(1, len(scans))
        print(f"  {verdict:12s} {n:3d}  ({pct:.0f}%)")

    by_family: dict[str, dict[str, int]] = {}
    for v in scans.values():
        fam = by_family.setdefault(v["family"] or "?", {})
        fam[v["verdict"]] = fam.get(v["verdict"], 0) + 1
    print("\n  by family:")
    for fam, c in sorted(by_family.items()):
        total = sum(c.values())
        print(f"    {fam:32s} READY {c.get('READY',0):2d}/{total:2d}"
              f"  BLOCKED {c.get('BLOCKED',0):2d}  UNSUPPORTED {c.get('UNSUPPORTED',0):2d}")

    blocked = {k: v for k, v in scans.items() if v["verdict"] == "BLOCKED"}
    if blocked:
        print(f"\n  BLOCKED scans ({len(blocked)}):")
        for sid, v in sorted(blocked.items()):
            print(f"    {sid:16s} missing: {', '.join(v['blocking'])}")

    unsup = {k: v for k, v in scans.items() if v["verdict"] == "UNSUPPORTED"}
    if unsup:
        print(f"\n  UNSUPPORTED scans ({len(unsup)}) — all blocked on the vector layer:")
        print("   ", ", ".join(sorted(unsup)))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"capabilities": caps, "scans": scans,
                               "summary": counts}, indent=1))
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
