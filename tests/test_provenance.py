"""Tests for the universal property contract.

The contract is the reason the mandatory fields can be trusted to be *correctly*
set rather than merely present. These tests pin the invariants that make that
claim true.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from skygenic_scans.provenance import (
    ContractViolation,
    Provenance,
    VersionTriplet,
    compute_record_hash,
    stamp_edge,
    stamp_node,
    validate_node,
)
from skygenic_scans.schema import load_schema


@pytest.fixture(scope="module")
def schema():
    return load_schema()


@pytest.fixture
def real_prov():
    return Provenance(
        source="hgnc", source_priority=1, is_synthetic=False,
        source_url="https://rest.genenames.org/fetch/symbol/TP53",
        source_retrieved_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def gene_props():
    return {"gene_id": "HGNC:11998", "symbol": "TP53", "name": "tumor protein p53",
            "species_taxon": 9606}


class TestProvenanceInvariants:
    def test_synthetic_requires_a_method(self):
        with pytest.raises(ContractViolation, match="synthesis_method"):
            Provenance(source="hgnc", source_priority=1, is_synthetic=True)

    def test_non_synthetic_must_not_carry_a_method(self):
        with pytest.raises(ContractViolation, match="non-synthetic"):
            Provenance(source="hgnc", source_priority=1, is_synthetic=False,
                       synthesis_method="seed_derived")

    def test_unknown_method_rejected(self):
        with pytest.raises(ContractViolation, match="unknown synthesis_method"):
            Provenance(source="hgnc", source_priority=1, is_synthetic=True,
                       synthesis_method="made_up")

    @pytest.mark.parametrize("priority", [0, 3, -1, 99])
    def test_priority_must_be_1_or_2(self, priority):
        with pytest.raises(ContractViolation, match="source_priority"):
            Provenance(source="hgnc", source_priority=priority, is_synthetic=False)

    def test_source_is_mandatory(self):
        with pytest.raises(ContractViolation, match="source is mandatory"):
            Provenance(source="", source_priority=1, is_synthetic=False)

    def test_derived_produces_a_synthetic_child(self, real_prov):
        child = real_prov.derived("seed_derived", "Gene:HGNC:11998")
        assert child.is_synthetic and child.synthesis_method == "seed_derived"
        assert child.synthesis_seed_uid == "Gene:HGNC:11998"
        # the parent is unchanged — Provenance is frozen
        assert real_prov.is_synthetic is False


class TestStampNode:
    def test_produces_all_required_contract_fields(self, schema, real_prov, gene_props):
        rec = stamp_node(schema, "Gene", "HGNC:11998", gene_props, real_prov,
                         versions=VersionTriplet(3, 7, 1), species=9606)
        for field in schema.required_node_fields:
            assert rec.get(field) is not None, f"{field} missing"
        assert validate_node(schema, rec) == []

    def test_uid_is_namespaced_and_derived(self, schema, real_prov, gene_props):
        rec = stamp_node(schema, "Gene", "HGNC:11998", gene_props, real_prov,
                         versions=VersionTriplet(), species=9606)
        assert rec["uid"] == "Gene:HGNC:11998"
        assert rec["entity_type"] == "Gene"
        assert rec["layer"] == "doc_verbatim"

    def test_created_at_is_preserved_on_restamp(self, schema, real_prov, gene_props):
        """created_at is immutable; updated_at moves. That distinction is the
        whole point of having both."""
        original = datetime(2020, 1, 1, tzinfo=timezone.utc)
        rec = stamp_node(schema, "Gene", "HGNC:11998", gene_props, real_prov,
                         versions=VersionTriplet(), species=9606, created_at=original)
        assert rec["created_at"] == original
        assert rec["updated_at"] > original
        assert rec["valid_from"] == original
        assert rec["valid_to"] is None

    def test_species_required_for_species_specific_labels(self, schema, real_prov,
                                                          gene_props):
        with pytest.raises(ContractViolation, match="species"):
            stamp_node(schema, "Gene", "HGNC:11998", gene_props, real_prov,
                       versions=VersionTriplet())

    def test_species_optional_for_agnostic_labels(self, schema, real_prov):
        rec = stamp_node(
            schema, "Publication", "PMID:1",
            {"publication_id": "PMID:1", "publication_year": 2024,
             "source_type": "peer_reviewed"},
            real_prov, versions=VersionTriplet(),
        )
        assert rec["species"] is None
        assert validate_node(schema, rec) == []

    def test_missing_required_type_field_rejected(self, schema, real_prov):
        with pytest.raises(ContractViolation, match="symbol"):
            stamp_node(schema, "Gene", "HGNC:1", {"gene_id": "HGNC:1"}, real_prov,
                       versions=VersionTriplet(), species=9606)

    def test_unknown_label_rejected(self, schema, real_prov):
        with pytest.raises(ContractViolation, match="unknown label"):
            stamp_node(schema, "Wombat", "X:1", {}, real_prov, versions=VersionTriplet())

    def test_empty_canonical_id_rejected(self, schema, real_prov, gene_props):
        with pytest.raises(ContractViolation, match="canonical_id"):
            stamp_node(schema, "Gene", "", gene_props, real_prov,
                       versions=VersionTriplet(), species=9606)

    def test_lists_default_to_empty_not_null(self, schema, real_prov, gene_props):
        """PRIM_N08 returns synonyms/ontology_refs; an empty list is valid,
        null is not — a null would force every consumer to null-check."""
        rec = stamp_node(schema, "Gene", "HGNC:11998", gene_props, real_prov,
                         versions=VersionTriplet(), species=9606)
        assert rec["synonyms"] == []
        assert rec["ontology_refs"] == []


class TestRecordHash:
    def test_key_order_does_not_change_hash(self):
        assert compute_record_hash({"a": 1, "b": 2}) == compute_record_hash({"b": 2, "a": 1})

    def test_value_change_changes_hash(self):
        assert compute_record_hash({"a": 1}) != compute_record_hash({"a": 2})

    def test_hash_excludes_updated_at(self, schema, real_prov, gene_props):
        """Re-loading unchanged upstream data must be a genuine no-op rather than
        churning the hash on every run."""
        a = stamp_node(schema, "Gene", "HGNC:11998", gene_props, real_prov,
                       versions=VersionTriplet(), species=9606,
                       now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        b = stamp_node(schema, "Gene", "HGNC:11998", gene_props, real_prov,
                       versions=VersionTriplet(), species=9606,
                       now=datetime(2026, 6, 1, tzinfo=timezone.utc))
        assert a["record_hash"] == b["record_hash"]
        assert a["updated_at"] != b["updated_at"]


class TestStampEdge:
    def test_self_loops_rejected(self, schema, real_prov):
        # Nodes doc Part 1 SII.1 under the reading argued in gap-analysis C-20
        with pytest.raises(ContractViolation, match="self-loop"):
            stamp_edge(schema, "INTERACTS_WITH", "Gene:A", "Gene:A", {}, real_prov,
                       versions=VersionTriplet(), layer="doc_verbatim")

    def test_missing_required_edge_field_rejected(self, schema, real_prov):
        with pytest.raises(ContractViolation, match="missing required edge fields"):
            stamp_edge(schema, "INTERACTS_WITH", "Gene:A", "Gene:B", {}, real_prov,
                       versions=VersionTriplet(), layer="doc_verbatim")

    def test_edge_id_is_deterministic(self, schema, real_prov):
        props = _min_edge_props()
        a = stamp_edge(schema, "INTERACTS_WITH", "Gene:A", "Gene:B", props, real_prov,
                       versions=VersionTriplet(), layer="doc_verbatim")
        b = stamp_edge(schema, "INTERACTS_WITH", "Gene:A", "Gene:B", props, real_prov,
                       versions=VersionTriplet(), layer="doc_verbatim")
        assert a["edge_id"] == b["edge_id"]

    def test_direction_is_endpoint_sensitive(self, schema, real_prov):
        props = _min_edge_props()
        fwd = stamp_edge(schema, "INTERACTS_WITH", "Gene:A", "Gene:B", props, real_prov,
                         versions=VersionTriplet(), layer="doc_verbatim")
        rev = stamp_edge(schema, "INTERACTS_WITH", "Gene:B", "Gene:A", props, real_prov,
                         versions=VersionTriplet(), layer="doc_verbatim")
        assert fwd["edge_id"] != rev["edge_id"]


class TestValidateNode:
    def test_detects_uid_mismatch(self, schema, real_prov, gene_props):
        rec = stamp_node(schema, "Gene", "HGNC:11998", gene_props, real_prov,
                         versions=VersionTriplet(), species=9606)
        problems = validate_node(schema, {**rec, "uid": "Gene:WRONG"})
        assert any("uid" in p for p in problems)

    def test_detects_updated_before_created(self, schema, real_prov, gene_props):
        rec = stamp_node(schema, "Gene", "HGNC:11998", gene_props, real_prov,
                         versions=VersionTriplet(), species=9606)
        broken = {**rec, "updated_at": rec["created_at"] - timedelta(days=1)}
        assert any("precedes" in p for p in validate_node(schema, broken))

    def test_detects_synthetic_flag_mismatch(self, schema, real_prov, gene_props):
        rec = stamp_node(schema, "Gene", "HGNC:11998", gene_props, real_prov,
                         versions=VersionTriplet(), species=9606)
        broken = {**rec, "is_synthetic": True, "synthesis_method": None}
        assert any("synthesis_method" in p for p in validate_node(schema, broken))


def _min_edge_props() -> dict:
    return {
        "SA": 0.85, "ES_edge": 0.7, "edge_aggregate_score": 0.6, "direction": 1,
        "evidence_count": 3, "edge_class": "test", "source_type": "peer_reviewed",
        "dataset_ids": ["SKYGEN.DS:00001"],
    }
