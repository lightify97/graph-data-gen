"""Tests that the schema itself is internally consistent.

These guard the schema against edits that would silently break the build — a
relationship pointing at a label that does not exist, a conflict losing its
partner, a gap losing the primitive that justifies it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from skygenic_scans.schema import Schema, load_schema
from skygenic_scans.validate.capabilities import PRIMITIVE_CAPABILITIES, capabilities_for


ARCHIVE_V1 = Path(__file__).resolve().parents[1] / "schema" / "archive" / "ontology-v1-doc-verbatim.yaml"


@pytest.fixture(scope="module")
def schema():
    """The ACTIVE schema — v2 since ADR-012."""
    return load_schema()


@pytest.fixture(scope="module")
def schema_v1():
    """The archived doc-verbatim schema.

    The conflict-register assertions belong here, not on the active schema:
    they exist to pin what the source document actually said, which is the
    evidence the v2 resolutions rest on. Asserting them against v2 would be
    asserting that v2 is still broken.
    """
    return Schema(raw=yaml.safe_load(ARCHIVE_V1.read_text()))


class TestStructuralIntegrity:
    def test_every_relationship_endpoint_is_a_declared_label(self, schema):
        labels = set(schema.labels)
        bad = [
            f"{r.type}({r.from_label}->{r.to_label})"
            for r in schema.relationships
            if r.from_label not in labels or r.to_label not in labels
        ]
        assert bad == [], f"relationships referencing unknown labels: {bad}"

    def test_every_node_declares_a_layer(self, schema):
        for label, spec in schema.nodes.items():
            assert spec.get("layer") in ("doc_verbatim", "extension"), label

    def test_every_relationship_declares_a_layer(self, schema):
        for r in schema.relationships:
            assert r.layer in ("doc_verbatim", "extension"), r.type

    def test_every_node_declares_a_key(self, schema):
        for label, spec in schema.nodes.items():
            assert spec.get("key"), f"{label} has no key"

    def test_node_key_is_a_declared_property(self, schema):
        for label, spec in schema.nodes.items():
            key = spec["key"]
            props = spec.get("properties") or {}
            assert key in props, f"{label} key {key!r} is not in its properties"


class TestLayerDiscipline:
    def test_extension_relationships_name_their_justification(self, schema):
        """Every extension edge must say which primitive forces it — otherwise
        it is indistinguishable from an invented convenience."""
        for row in schema.raw["relationships"]:
            if row["layer"] == "extension":
                assert row.get("forced_by"), f"{row['type']} has no forced_by"
                assert row.get("gap_ref"), f"{row['type']} has no gap_ref"

    def test_extension_nodes_name_their_justification(self, schema):
        for label, spec in schema.nodes.items():
            if spec["layer"] == "extension":
                assert spec.get("forced_by"), f"{label} has no forced_by"

    def test_doc_verbatim_relationships_record_their_source_table(self, schema):
        """Rows transcribed from the source document must say which table they came
        from. Rows added by a v2 resolution legitimately have no source table —
        they carry `introduced_by` instead."""
        # Must match on the full triple: PHARMACOLOGICALLY_ACTIVATES now exists on
        # both Drug->Protein (from the doc) and Drug->BiologicalProcess (added by
        # C-07), and matching on (type, from) alone silently picks the wrong one.
        rows = {
            (x["type"], x["from"], t): x
            for x in schema.raw["relationships"]
            for t in [x["to"], *(x.get("also_to") or [])]
        }
        for r in schema.relationships:
            if r.layer != "doc_verbatim":
                continue
            row = rows[(r.type, r.from_label, r.to_label)]
            assert r.doc_table or row.get("introduced_by"), (
                f"{r.type}({r.from_label}->{r.to_label}) has neither doc_table "
                f"nor introduced_by"
            )


class TestActiveSchemaIsResolved:
    """ADR-012: the active schema must carry no unresolved conflicts."""

    def test_no_conflicts_remain(self, schema):
        assert schema.conflicts() == {}, (
            f"active schema still declares conflicts: {sorted(schema.conflicts())}"
        )

    def test_version_is_2(self, schema):
        assert schema.version == 2

    def test_retired_rows_are_not_served_to_builders(self, schema):
        """Schema.relationships must filter `status: retired`, or the generator
        would rebuild the very duplicates v2 removed."""
        retired = {(r["type"], r["from"], r["to"])
                   for r in schema.raw["relationships"] if r.get("status") == "retired"}
        assert retired, "expected v2 to retain retired rows for migration"
        assert not (retired & {r.endpoint_key for r in schema.relationships})


class TestArchivedV1ConflictRegister:
    """Pins what the source document actually said. This is the evidence the v2
    resolutions rest on, so it must stay verifiable after v1 is retired."""

    def test_every_conflict_has_at_least_two_members(self, schema_v1):
        for ref, rows in schema_v1.conflicts().items():
            assert len(rows) >= 2, f"{ref} has only {len(rows)} member(s)"

    def test_expected_conflicts_are_present(self, schema_v1):
        found = set(schema_v1.conflicts())
        expected = {f"C-{i:02d}" for i in range(1, 20)}
        assert expected <= found, f"missing: {sorted(expected - found)}"

    def test_known_duplicate_pairs_share_endpoints(self, schema_v1):
        by_ref = schema_v1.conflicts()
        c19 = {r.type for r in by_ref["C-19"]}
        assert c19 == {"TREATS_INDICATION", "CLINICALLY_TREATS"}
        c14 = {(r.type, r.from_label, r.to_label) for r in by_ref["C-14"]}
        assert ("EXPRESSED_IN", "Gene", "Tissue") in c14
        assert ("EXPRESSES", "Tissue", "Gene") in c14

    def test_archive_is_still_v1(self, schema_v1):
        assert schema_v1.version == 1


class TestContracts:
    def test_prim_n08_fields_are_all_required(self, schema):
        """PRIM_N08 is specified to return exactly these six; if any becomes
        optional, N08 can return null and every consumer breaks."""
        required = set(schema.required_node_fields)
        for f in ("entity_type", "canonical_id", "ontology_refs", "synonyms", "created_at"):
            assert f in required, f"{f} must be required (PRIM_N08)"

    def test_provenance_fields_are_required(self, schema):
        required = set(schema.required_node_fields)
        for f in ("source", "source_priority", "is_synthetic", "updated_at", "valid_from"):
            assert f in required

    def test_edge_contract_covers_scan01_inputs(self, schema):
        required = set(schema.required_edge_fields)
        for f in ("SA", "ES_edge", "edge_aggregate_score", "direction",
                  "evidence_count", "source_type", "dataset_ids"):
            assert f in required, f"{f} must be required on edges"

    def test_temporal_fields_present_on_both_contracts(self, schema):
        for contract in (schema.node_contract, schema.edge_contract):
            for f in ("created_at", "updated_at", "valid_from", "valid_to"):
                assert f in contract


class TestCapabilityMapping:
    def test_all_workbook_primitives_resolve(self):
        """Every primitive referenced by a scan must map to capabilities, or the
        readiness report silently understates what a scan needs."""
        import json
        from pathlib import Path

        scans = json.loads(
            (Path(__file__).resolve().parents[1] / "schema" / "scans.json").read_text()
        )
        unmapped = set()
        for meta in scans.values():
            for prim in meta.get("primitives") or []:
                key = prim.replace("PRIM_", "")
                if key not in PRIMITIVE_CAPABILITIES and prim not in PRIMITIVE_CAPABILITIES:
                    unmapped.add(prim)
        assert unmapped == set(), f"primitives with no capability mapping: {sorted(unmapped)}"

    def test_vector_primitives_are_marked_unsupported(self):
        for i in range(1, 17):
            assert capabilities_for(f"PRIM_V{i:02d}") == ("embeddings",)
