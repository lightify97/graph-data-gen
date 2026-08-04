"""Generates and applies Neo4j constraints/indexes from schema/ontology.yaml.

Constraint policy, and why it is what it is:

* A uniqueness constraint on `uid` per label. `uid` is the namespaced global key
  and the only thing the loader MERGEs on, so this is the constraint that makes
  the load idempotent.
* An existence constraint would be the natural way to enforce the mandatory
  contract fields — but property existence constraints are Enterprise-only.
  On Community they are enforced in `provenance.validate_node` before write and
  re-checked post-load by `validate.contracts`. See ADR in docs/decisions.md.
* Range indexes on the fields the scan primitives actually filter by.
"""

from __future__ import annotations

from ..schema import Schema, load_schema
from .client import session


def constraint_statements(schema: Schema) -> list[str]:
    stmts: list[str] = []

    # Shared supertype label carried by every node. Edge loading matches
    # endpoints by uid alone; without a label on the pattern Neo4j cannot use
    # any of the per-label uid indexes and every MATCH degrades to a scan.
    # One index on :Entity(uid) turns edge load from O(n) scans into lookups.
    stmts.append(
        "CREATE CONSTRAINT uid_entity IF NOT EXISTS "
        "FOR (n:Entity) REQUIRE n.uid IS UNIQUE"
    )

    for label in schema.labels:
        stmts.append(
            f"CREATE CONSTRAINT uid_{label.lower()} IF NOT EXISTS "
            f"FOR (n:`{label}`) REQUIRE n.uid IS UNIQUE"
        )

    # canonical_id must also be unique within a label — two nodes sharing a
    # canonical_id would break PRIM_I03 entity resolution non-deterministically.
    for label in schema.labels:
        stmts.append(
            f"CREATE CONSTRAINT canon_{label.lower()} IF NOT EXISTS "
            f"FOR (n:`{label}`) REQUIRE n.canonical_id IS UNIQUE"
        )

    # contract-wide lookup indexes
    for label in schema.labels:
        for prop in ("is_synthetic", "source", "layer"):
            stmts.append(
                f"CREATE INDEX ix_{label.lower()}_{prop} IF NOT EXISTS "
                f"FOR (n:`{label}`) ON (n.{prop})"
            )

    for extra in schema.raw.get("constraints", {}).get("extra_indexes", []) or []:
        label, prop = extra["label"], extra["property"]
        stmts.append(
            f"CREATE INDEX ix_{label.lower()}_{prop} IF NOT EXISTS "
            f"FOR (n:`{label}`) ON (n.{prop})"
        )

    # relationship indexes — the loader and every scan filter on these
    for rtype in schema.rel_types:
        safe = rtype.lower().replace("-", "_")
        stmts.append(
            f"CREATE INDEX rx_{safe}_score IF NOT EXISTS "
            f"FOR ()-[r:`{rtype}`]-() ON (r.edge_aggregate_score)"
        )
        stmts.append(
            f"CREATE INDEX rx_{safe}_valid IF NOT EXISTS "
            f"FOR ()-[r:`{rtype}`]-() ON (r.valid_to)"
        )

    return stmts


def apply(schema: Schema | None = None) -> dict[str, int]:
    s = schema or load_schema()
    stmts = constraint_statements(s)
    applied, failed = 0, []
    with session() as sess:
        for stmt in stmts:
            try:
                sess.run(stmt)
                applied += 1
            except Exception as exc:  # noqa: BLE001 - report, don't abort the batch
                failed.append((stmt, str(exc)))
    if failed:
        for stmt, err in failed[:5]:
            print(f"FAILED: {stmt}\n  {err}")
    return {"applied": applied, "failed": len(failed), "total": len(stmts)}


if __name__ == "__main__":
    print(apply())
