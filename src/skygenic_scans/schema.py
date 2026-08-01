"""Loads and interrogates schema/ontology.yaml.

The YAML is the single source of truth for labels, relationship types, endpoint
pairs and the universal property contracts. Nothing in this package hardcodes a
label or relationship name — if it is not in the YAML it does not exist.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"

# schema/ontology.yaml IS v2 — the resolved schema, generated from
# schema/resolutions.yaml. It is the only schema anything should build against.
#
# v1 (doc-verbatim, all 20 conflicts intact) is retired to
# schema/archive/ontology-v1-doc-verbatim.yaml. It is kept, not deleted, for two
# reasons: `schema_migrate` regenerates v2 from it, so it is what makes v2
# verifiable rather than merely asserted; and it is the evidentiary basis for the
# resolution decisions. Nothing loads it by default.
_SELECTED = os.getenv("SKYGENIC_SCHEMA", "current")
SCHEMA_PATH = {
    "current": _SCHEMA_DIR / "ontology.yaml",
    "v2": _SCHEMA_DIR / "ontology.yaml",
    "v1": _SCHEMA_DIR / "archive" / "ontology-v1-doc-verbatim.yaml",
}.get(_SELECTED, Path(_SELECTED))


@dataclass(frozen=True)
class RelSpec:
    """One row of the `relationships:` list."""

    type: str
    from_label: str
    to_label: str
    layer: str
    doc_table: str | None
    conflict_ref: str | None
    gap_ref: str | None
    direction_default: int
    symmetric: bool
    acyclic_required: bool

    @property
    def endpoint_key(self) -> tuple[str, str, str]:
        return (self.type, self.from_label, self.to_label)


@dataclass(frozen=True)
class Schema:
    raw: dict[str, Any]

    # -- contracts ---------------------------------------------------------
    @property
    def node_contract(self) -> dict[str, dict]:
        return self.raw["node_property_contract"]

    @property
    def edge_contract(self) -> dict[str, dict]:
        return self.raw["edge_property_contract"]

    @property
    def required_node_fields(self) -> tuple[str, ...]:
        return tuple(k for k, v in self.node_contract.items() if v.get("required"))

    @property
    def required_edge_fields(self) -> tuple[str, ...]:
        return tuple(k for k, v in self.edge_contract.items() if v.get("required"))

    # -- nodes -------------------------------------------------------------
    @property
    def nodes(self) -> dict[str, dict]:
        return self.raw["nodes"]

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(self.nodes)

    def label_layer(self, label: str) -> str:
        return self.nodes[label]["layer"]

    def type_specific_fields(self, label: str) -> dict[str, dict]:
        return self.nodes[label].get("properties") or {}

    def required_type_fields(self, label: str) -> tuple[str, ...]:
        return tuple(
            k for k, v in self.type_specific_fields(label).items() if v.get("required")
        )

    # -- relationships -----------------------------------------------------
    @property
    def relationships(self) -> tuple[RelSpec, ...]:
        """Active relationships only.

        v2 retains retired rows with `status: retired` so a migration can map old
        edges to their replacements. They must never be built or loaded, so they
        are filtered here — the single place every consumer goes through. v1 rows
        have no `status` key and are all treated as active.
        """
        out = []
        for r in self.raw["relationships"]:
            if r.get("status") == "retired":
                continue
            targets = [r["to"], *(r.get("also_to") or [])]
            for tgt in targets:
                out.append(
                    RelSpec(
                        type=r["type"],
                        from_label=r["from"],
                        to_label=tgt,
                        layer=r["layer"],
                        doc_table=r.get("doc_table"),
                        conflict_ref=r.get("conflict_ref"),
                        gap_ref=r.get("gap_ref"),
                        direction_default=r.get("direction_default", 0),
                        symmetric=bool(r.get("symmetric")),
                        acyclic_required=bool(r.get("acyclic_required")),
                    )
                )
        return tuple(out)

    @property
    def rel_types(self) -> tuple[str, ...]:
        return tuple(sorted({r.type for r in self.relationships}))

    def allowed_endpoints(self) -> set[tuple[str, str, str]]:
        return {r.endpoint_key for r in self.relationships}

    def conflicts(self) -> dict[str, list[RelSpec]]:
        out: dict[str, list[RelSpec]] = {}
        for r in self.relationships:
            if r.conflict_ref:
                out.setdefault(r.conflict_ref, []).append(r)
        return out

    @property
    def version(self) -> int:
        return int(self.raw["meta"]["version"])


@lru_cache(maxsize=1)
def load_schema(path: Path | None = None) -> Schema:
    p = path or SCHEMA_PATH
    return Schema(raw=yaml.safe_load(p.read_text()))
