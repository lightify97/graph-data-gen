"""Stamps the universal node/edge property contract onto every record.

No node or edge enters the graph except through `stamp_node` / `stamp_edge`.
That is the only reason the mandatory fields can be trusted to be *correctly*
set rather than merely present: there is one code path, and it computes the
derived fields (uid, record_hash, entity_type, layer, timestamps) rather than
accepting them from callers.

All functions are pure — they return new dicts and never mutate their inputs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping

from .schema import Schema

# Labels for which `species` is legitimately null. Everything else must carry a
# taxon id — a Gene with no species cannot be resolved by PRIM_V13 or compared
# across organisms by SCAN-12/16.
SPECIES_AGNOSTIC = frozenset(
    {
        "OntologyTerm", "Publication", "Dataset", "ScanResult", "AuditEvent",
        "Disease", "Phenotype", "Trait", "Tissue", "CellType", "Drug", "Compound",
        "SkygenicHypothesis", "HypothesisVersion", "LifecycleState", "ReasoningChain",
        "ConfidenceScore", "Evidence", "Assertion", "BiologicalState", "Cohort",
        "Species",
    }
)

SYNTHESIS_METHODS = frozenset(
    {
        "seed_fetched", "seed_derived", "degree_preserving_expansion",
        "ontology_walk", "statistical_sample", "template_instantiated",
    }
)


class ContractViolation(ValueError):
    """Raised when a record cannot satisfy the universal property contract.

    Deliberately fatal. The schema states violations fail the load rather than
    warn: a graph that is 99% provenance-complete cannot answer SCAN-11's
    'Fully Traceable (Audit-Ready)' question for any subgraph, because you
    cannot know which 1% you touched.
    """


@dataclass(frozen=True)
class Provenance:
    """Where a record came from. Attached to every node and edge."""

    source: str
    source_priority: int
    is_synthetic: bool
    source_ids: tuple[str, ...] = ()
    source_url: str | None = None
    source_retrieved_at: datetime | None = None
    synthesis_method: str | None = None
    synthesis_seed_uid: str | None = None

    def __post_init__(self) -> None:
        if self.source_priority not in (1, 2):
            raise ContractViolation(
                f"source_priority must be 1 (primary) or 2 (supplementary), "
                f"got {self.source_priority!r} for source {self.source!r}"
            )
        if self.is_synthetic and not self.synthesis_method:
            raise ContractViolation(
                f"is_synthetic=True requires synthesis_method (source={self.source!r})"
            )
        if not self.is_synthetic and self.synthesis_method:
            raise ContractViolation(
                f"synthesis_method={self.synthesis_method!r} set on a non-synthetic "
                f"record (source={self.source!r}); it must be null when is_synthetic=False"
            )
        if self.synthesis_method and self.synthesis_method not in SYNTHESIS_METHODS:
            raise ContractViolation(
                f"unknown synthesis_method {self.synthesis_method!r}; "
                f"allowed: {sorted(SYNTHESIS_METHODS)}"
            )
        if not self.source:
            raise ContractViolation("source is mandatory and must be a source_registry source_id")

    def derived(self, method: str, seed_uid: str | None) -> Provenance:
        """A synthetic record descended from this one."""
        return replace(
            self, is_synthetic=True, synthesis_method=method, synthesis_seed_uid=seed_uid
        )


@dataclass(frozen=True)
class VersionTriplet:
    """PRIM_S01/S02/S03 — Data, Topology and Hypothesis versions."""

    Dv: int = 1
    Tv: int = 1
    Hv: int = 1


def _utc(dt: datetime | None = None) -> datetime:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _canonical_json(payload: Mapping[str, Any]) -> str:
    """Stable serialisation for hashing: sorted keys, ISO datetimes, no whitespace drift."""

    def enc(v: Any) -> Any:
        if isinstance(v, datetime):
            return _utc(v).isoformat()
        if isinstance(v, (list, tuple)):
            return [enc(x) for x in v]
        if isinstance(v, dict):
            return {k: enc(v[k]) for k in sorted(v)}
        return v

    return json.dumps({k: enc(payload[k]) for k in sorted(payload)}, separators=(",", ":"))


def compute_record_hash(type_specific: Mapping[str, Any]) -> str:
    """SHA256 over the type-specific property set only.

    Excludes the contract's own lifecycle fields — notably `updated_at` — so that
    re-loading unchanged upstream data is a genuine no-op and does not churn the
    hash on every run.
    """
    return hashlib.sha256(_canonical_json(type_specific).encode()).hexdigest()


def stamp_node(
    schema: Schema,
    label: str,
    canonical_id: str,
    type_specific: Mapping[str, Any],
    prov: Provenance,
    *,
    versions: VersionTriplet,
    species: int | None = None,
    synonyms: tuple[str, ...] = (),
    ontology_refs: tuple[str, ...] = (),
    created_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a complete node record satisfying `node_property_contract`.

    `created_at` is accepted so that re-stamping an existing record preserves the
    original immutable value; when omitted it defaults to `now`.
    """
    if label not in schema.nodes:
        raise ContractViolation(f"unknown label {label!r}; not in schema/ontology.yaml")
    if not canonical_id:
        raise ContractViolation(f"{label}: canonical_id is mandatory and must be non-empty")

    ts = _utc(now)
    created = _utc(created_at) if created_at else ts

    missing = [f for f in schema.required_type_fields(label) if type_specific.get(f) is None]
    if missing:
        raise ContractViolation(
            f"{label} {canonical_id}: missing required type-specific fields {missing}"
        )

    if species is None and label not in SPECIES_AGNOSTIC:
        raise ContractViolation(
            f"{label} {canonical_id}: species (NCBI taxon id) is mandatory for this label; "
            f"only {sorted(SPECIES_AGNOSTIC)} may omit it"
        )

    record = {
        # identity
        "uid": f"{label}:{canonical_id}",
        "canonical_id": canonical_id,
        "entity_type": label,
        "synonyms": list(synonyms),
        "ontology_refs": list(ontology_refs),
        "species": species,
        # provenance
        "source": prov.source,
        "source_priority": prov.source_priority,
        "source_ids": list(prov.source_ids or (prov.source,)),
        "source_url": prov.source_url,
        "source_retrieved_at": prov.source_retrieved_at,
        "is_synthetic": prov.is_synthetic,
        "synthesis_method": prov.synthesis_method,
        "synthesis_seed_uid": prov.synthesis_seed_uid,
        # lifecycle
        "created_at": created,
        "updated_at": ts,
        "ingested_at": ts,
        "valid_from": created,
        "valid_to": None,
        # versioning / integrity
        "Dv_created": versions.Dv,
        "Tv_created": versions.Tv,
        "record_hash": compute_record_hash(type_specific),
        "schema_version": schema.version,
        "layer": schema.label_layer(label),
        **dict(type_specific),
    }
    return record


def stamp_edge(
    schema: Schema,
    rel_type: str,
    from_uid: str,
    to_uid: str,
    type_specific: Mapping[str, Any],
    prov: Provenance,
    *,
    versions: VersionTriplet,
    layer: str,
    created_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a complete edge record satisfying `edge_property_contract`."""
    if from_uid == to_uid:
        # Nodes doc Part 1 SII.1, under the reading argued in gap-analysis C-20.
        raise ContractViolation(
            f"{rel_type}: true self-loop on {from_uid}. Self-loops are prohibited "
            f"(they contribute nothing to betweenness and create zero-hop artefacts)."
        )

    ts = _utc(now)
    created = _utc(created_at) if created_at else ts
    edge_id = hashlib.sha256(f"{from_uid}|{rel_type}|{to_uid}".encode()).hexdigest()[:32]

    record = {
        "edge_id": edge_id,
        "_from": from_uid,
        "_to": to_uid,
        "_type": rel_type,
        # provenance
        "source": prov.source,
        "source_priority": prov.source_priority,
        "source_ids": list(prov.source_ids or (prov.source,)),
        "source_url": prov.source_url,
        "source_retrieved_at": prov.source_retrieved_at,
        "is_synthetic": prov.is_synthetic,
        "synthesis_method": prov.synthesis_method,
        # lifecycle
        "created_at": created,
        "updated_at": ts,
        "ingested_at": ts,
        "valid_from": created,
        "valid_to": None,
        # versioning / integrity
        "Dv_created": versions.Dv,
        "Tv_created": versions.Tv,
        "record_hash": compute_record_hash(type_specific),
        "schema_version": schema.version,
        "layer": layer,
        **dict(type_specific),
    }

    missing = [f for f in schema.required_edge_fields if record.get(f) is None]
    if missing:
        raise ContractViolation(
            f"{rel_type} {from_uid}->{to_uid}: missing required edge fields {missing}"
        )
    return record


def validate_node(schema: Schema, record: Mapping[str, Any]) -> list[str]:
    """Return a list of contract violations for one node record. Empty == valid."""
    problems: list[str] = []
    label = record.get("entity_type")

    for field in schema.required_node_fields:
        if record.get(field) is None:
            problems.append(f"missing required field {field!r}")

    if label not in schema.nodes:
        problems.append(f"entity_type {label!r} is not a schema label")
        return problems

    expected_uid = f"{label}:{record.get('canonical_id')}"
    if record.get("uid") != expected_uid:
        problems.append(f"uid {record.get('uid')!r} != expected {expected_uid!r}")

    if record.get("layer") != schema.label_layer(label):
        problems.append(
            f"layer {record.get('layer')!r} != schema layer {schema.label_layer(label)!r}"
        )

    created, updated = record.get("created_at"), record.get("updated_at")
    if created and updated and _utc(updated) < _utc(created):
        problems.append(f"updated_at {updated} precedes created_at {created}")

    if record.get("species") is None and label not in SPECIES_AGNOSTIC:
        problems.append(f"species is mandatory for {label}")

    if record.get("is_synthetic") and not record.get("synthesis_method"):
        problems.append("is_synthetic=True but synthesis_method is null")
    if record.get("is_synthetic") is False and record.get("synthesis_method"):
        problems.append("is_synthetic=False but synthesis_method is set")

    for field in schema.required_type_fields(label):
        if record.get(field) is None:
            problems.append(f"missing required {label} field {field!r}")

    return problems
