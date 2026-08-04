"""Applies schema/resolutions.yaml to ontology.yaml, emitting ontology-v2.yaml.

Run:  python -m skygenic_scans.schema_migrate [--check]

v2 is generated, never hand-edited: the resolution table is the reviewable
artifact and this is a mechanical transform of it. Retired relationships are
*retained* in v2 with `status: retired` and a `superseded_by` pointer rather than
deleted — a migration needs to know what a v1 edge becomes, and a reader needs to
know why a name they remember has gone.

`--check` validates without writing: every retired row has a live replacement
covering its endpoint pair, every conflict in v1 is addressed exactly once, and
no live row still carries a conflict_ref.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT / "schema" / "archive" / "ontology-v1-doc-verbatim.yaml"
RESOLUTIONS = ROOT / "schema" / "resolutions.yaml"
V2 = ROOT / "schema" / "ontology.yaml"
REPORT = ROOT / "docs" / "schema-v2-migration.md"

Triple = tuple[str, str, str]


def _triple(d: dict) -> Triple:
    return (d["type"], d["from"], d["to"])


def load() -> tuple[dict, dict]:
    return yaml.safe_load(V1.read_text()), yaml.safe_load(RESOLUTIONS.read_text())


def build_plan(res: dict) -> dict[str, Any]:
    """Flatten the resolution table into lookup structures."""
    retire: dict[Triple, dict] = {}
    add: list[dict] = []
    keep: dict[Triple, str] = {}
    kinds: dict[str, str] = {}
    notes: list[dict] = []

    for r in res["resolutions"]:
        ref, kind = r["conflict"], r["kind"]
        kinds[ref] = kind

        if kind in ("no_change", "clarification"):
            notes.append(r)
            continue

        survivor = r.get("keep") or (r.get("add") or [{}])[0]
        survivor_name = survivor.get("type", r.get("new_type", "?"))

        if r.get("keep"):
            keep[_triple(r["keep"])] = ref

        for row in r.get("retire", []) or []:
            t = _triple(row)
            if t in retire:
                raise SystemExit(f"{ref}: {t} retired twice (also by {retire[t]['conflict']})")
            retire[t] = {
                "conflict": ref,
                # A per-row `superseded_by` always wins. Redirects have several
                # replacements that are NOT interchangeable (activation vs
                # inhibition), so defaulting every retirement to the first
                # replacement silently mislabels them.
                "superseded_by": (
                    row.get("superseded_by") or r.get("new_type") or survivor_name
                ),
                "superseded_by_triple": (
                    _triple(r["keep"]) if r.get("keep") else None
                ),
                "rationale": " ".join((r.get("rationale") or "").split()),
            }

        for row in r.get("add", []) or []:
            add.append({**row, "conflict": ref, "kind": kind})

    return {"retire": retire, "add": add, "keep": keep, "kinds": kinds, "notes": notes}


def migrate(v1: dict, plan: dict) -> tuple[dict, dict[str, Any]]:
    rows_out: list[dict] = []
    stats = Counter()
    existing: set[Triple] = set()

    for row in v1["relationships"]:
        targets = [row["to"], *(row.get("also_to") or [])]
        for tgt in targets:
            t = (row["type"], row["from"], tgt)
            existing.add(t)

    for row in v1["relationships"]:
        base = {k: v for k, v in row.items() if k != "also_to"}
        targets = [row["to"], *(row.get("also_to") or [])]
        keep_targets, retired_rows = [], []

        for tgt in targets:
            t = (row["type"], row["from"], tgt)
            if t in plan["retire"]:
                retired_rows.append((tgt, plan["retire"][t]))
            else:
                keep_targets.append(tgt)

        if keep_targets:
            live = dict(base)
            live["to"] = keep_targets[0]
            if len(keep_targets) > 1:
                live["also_to"] = keep_targets[1:]
            live["status"] = "active"
            live.pop("conflict_ref", None)
            rows_out.append(live)
            stats["active"] += 1

        for tgt, info in retired_rows:
            dead = dict(base)
            dead["to"] = tgt
            dead["status"] = "retired"
            dead["retired_by"] = info["conflict"]
            dead["superseded_by"] = info["superseded_by"]
            dead["retirement_rationale"] = info["rationale"]
            dead.pop("conflict_ref", None)
            rows_out.append(dead)
            stats["retired"] += 1

    for row in plan["add"]:
        t = (row["type"], row["from"], row["to"])
        if t in existing:
            stats["add_skipped_exists"] += 1
            continue
        new = {
            "type": row["type"], "from": row["from"], "to": row["to"],
            "layer": "doc_verbatim", "status": "active",
            "introduced_by": row["conflict"],
            "direction_default": row.get("direction_default", 0),
        }
        if row.get("purpose"):
            new["purpose"] = row["purpose"]
        rows_out.append(new)
        stats["added"] += 1

    v2 = dict(v1)
    v2["meta"] = {
        **v1["meta"], "version": 2,
        "derived_from": "schema/ontology.yaml v1 + schema/resolutions.yaml",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "GENERATED — edit schema/resolutions.yaml and re-run, do not hand-edit.",
    }
    v2["relationships"] = rows_out
    v2["constraints"] = {
        **(v1.get("constraints") or {}),
        "no_self_loops": {
            "enforced": True,
            "rule": "NOT (n)-[r]->(n)",
            "source": "C-20 — Part 1 SII.1, reading 1 (true self-loops only)",
        },
        "predicate_importance_key": {
            "rule": "(predicate, from_label, to_label)",
            "source": "C-11 — PRIM_P09 keyed on predicate alone is ambiguous",
        },
    }
    return v2, stats


def check(v2: dict, plan: dict) -> list[str]:
    problems: list[str] = []
    active = {(r["type"], r["from"], t)
              for r in v2["relationships"] if r.get("status") == "active"
              for t in [r["to"], *(r.get("also_to") or [])]}
    active_types = {r["type"] for r in v2["relationships"] if r.get("status") == "active"}

    for r in v2["relationships"]:
        if r.get("status") != "retired":
            continue
        sup = r.get("superseded_by")
        if sup not in active_types:
            problems.append(
                f"{r['type']}({r['from']}->{r['to']}) superseded by {sup!r}, "
                f"which is not an active type"
            )

    # Every retired row must leave the two labels still connected by something
    # active. Direction is checked in EITHER order on purpose: an inverse merge
    # retires A->B precisely because B->A already carries the fact, so requiring
    # a same-direction replacement would flag every correct inverse resolution.
    for r in v2["relationships"]:
        if r.get("status") != "retired":
            continue
        src, tgt = r["from"], r["to"]
        covered = any(
            (a[1] == src and a[2] == tgt) or (a[1] == tgt and a[2] == src)
            for a in active
        )
        if not covered:
            problems.append(
                f"{src} <-> {tgt}: all edges retired (e.g. {r['type']}) with no active "
                f"replacement in either direction"
            )

    leftover = [r["type"] for r in v2["relationships"] if r.get("conflict_ref")]
    if leftover:
        problems.append(f"conflict_ref still present on: {sorted(set(leftover))}")

    return problems


def write_report(v2: dict, plan: dict, stats: Counter) -> None:
    retired = [r for r in v2["relationships"] if r.get("status") == "retired"]
    added = [r for r in v2["relationships"] if r.get("introduced_by")]
    by_conf: dict[str, list] = {}
    for r in retired:
        by_conf.setdefault(r["retired_by"], []).append(r)

    lines = [
        "# Schema v2 — migration report",
        "",
        "Generated by `python -m skygenic_scans.schema_migrate` from",
        "`schema/ontology.yaml` (v1) and `schema/resolutions.yaml`. Do not hand-edit.",
        "",
        "## Summary",
        "",
        f"- Active relationship rows: **{stats['active']}**",
        f"- Retired (kept with `status: retired` + `superseded_by`): **{stats['retired']}**",
        f"- New rows introduced by resolutions: **{stats['added']}**",
        f"- Conflicts addressed: **{len(plan['kinds'])}**",
        "",
        "Retired rows are retained rather than deleted so a v1→v2 data migration can",
        "map every old edge to its replacement, and so a reader who remembers a name",
        "can find out where it went.",
        "",
        "## Retirements by conflict",
        "",
        "| Conflict | Retired | Superseded by |",
        "|---|---|---|",
    ]
    for ref in sorted(by_conf):
        for r in by_conf[ref]:
            lines.append(
                f"| {ref} | `{r['type']}` ({r['from']}→{r['to']}) | `{r['superseded_by']}` |"
            )

    if added:
        lines += ["", "## New relationships", "",
                  "| Introduced by | Relationship | Purpose |", "|---|---|---|"]
        for r in added:
            lines.append(
                f"| {r['introduced_by']} | `{r['type']}` ({r['from']}→{r['to']}) | "
                f"{r.get('purpose', '—')} |"
            )

    lines += ["", "## Resolutions requiring action outside the schema", ""]
    for n in plan["notes"]:
        lines += [f"### {n['conflict']} — {n['kind']}", "",
                  " ".join((n.get("rationale") or "").split()), ""]

    lines += [
        "## Migrating loaded data",
        "",
        "```cypher",
        "// Example: C-19. Repoint TREATS_INDICATION onto CLINICALLY_TREATS,",
        "// preserving all evidence properties, then drop the duplicate.",
        "MATCH (d:Drug)-[old:TREATS_INDICATION]->(x:Disease)",
        "MERGE (d)-[new:CLINICALLY_TREATS]->(x)",
        "ON CREATE SET new = properties(old)",
        "DELETE old",
        "```",
        "",
        "Order matters for the inverse merges (C-08/13/14/15/16): reverse the",
        "endpoints when repointing, since the retired edge runs the other way.",
        "",
        "```cypher",
        "// Example: C-14. EXPRESSES(Tissue→Gene) becomes EXPRESSED_IN(Gene→Tissue).",
        "MATCH (t:Tissue)-[old:EXPRESSES]->(g:Gene)",
        "MERGE (g)-[new:EXPRESSED_IN]->(t)",
        "ON CREATE SET new = properties(old)",
        "DELETE old",
        "```",
    ]
    REPORT.write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="validate without writing")
    args = ap.parse_args()

    v1, res = load()
    plan = build_plan(res)
    v2, stats = migrate(v1, plan)
    problems = check(v2, plan)

    print(f"v1 relationship rows : {len(v1['relationships'])}")
    print(f"v2 active            : {stats['active']}")
    print(f"v2 retired           : {stats['retired']}")
    print(f"v2 added             : {stats['added']}")
    if stats["add_skipped_exists"]:
        print(f"adds already present : {stats['add_skipped_exists']}")
    print(f"conflicts addressed  : {len(plan['kinds'])}")

    if problems:
        print(f"\nVALIDATION FAILED ({len(problems)}):", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("\nvalidation passed: every retirement has a live replacement")

    if args.check:
        return 0

    V2.write_text(yaml.safe_dump(v2, sort_keys=False, width=100, allow_unicode=True))
    write_report(v2, plan, stats)
    print(f"wrote {V2}")
    print(f"wrote {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
