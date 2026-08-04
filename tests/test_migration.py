"""Tests for the v1 -> v2 conflict resolution.

The migration is mechanical, which is exactly why it needs tests: a per-name
retirement instead of a per-triple one silently deletes legitimate relationships,
and nothing downstream would notice.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from skygenic_scans.schema import Schema, load_schema
from skygenic_scans.schema_migrate import build_plan, check, load, migrate

ROOT = Path(__file__).resolve().parents[1]
V2_PATH = ROOT / "schema" / "ontology.yaml"  # v2 is canonical since ADR-012


@pytest.fixture(scope="module")
def migrated():
    v1, res = load()
    plan = build_plan(res)
    v2, stats = migrate(v1, plan)
    return v1, plan, v2, stats


class TestResolutionTable:
    def test_every_v1_conflict_is_addressed(self, migrated):
        v1, plan, _, _ = migrated
        v1_conflicts = {r["conflict_ref"] for r in v1["relationships"]
                        if r.get("conflict_ref")}
        addressed = set(plan["kinds"])
        assert v1_conflicts <= addressed, f"unaddressed: {sorted(v1_conflicts - addressed)}"

    def test_no_row_retired_twice(self, migrated):
        """build_plan raises on double-retirement; this pins that it stays that way."""
        _, plan, _, _ = migrated
        assert len(plan["retire"]) == len(set(plan["retire"]))

    def test_c20_is_a_clarification_not_a_deletion(self, migrated):
        """C-20 must never retire anything — the alternative reading would delete
        UPREGULATES, ORTHOLOG_OF, TEMPORALLY_PRECEDES and six others."""
        _, plan, _, _ = migrated
        assert plan["kinds"]["C-20"] == "clarification"
        assert not any(v["conflict"] == "C-20" for v in plan["retire"].values())


class TestPerTripleRetirement:
    """The single most dangerous failure mode in this migration."""

    def test_causes_retired_only_on_bp_to_disease(self, migrated):
        _, _, v2, _ = migrated
        rows = {(r["from"], r["to"]): r.get("status")
                for r in v2["relationships"] if r["type"] == "CAUSES"}
        assert rows[("BiologicalProcess", "Disease")] == "retired"
        assert rows[("Gene", "Disease")] == "active"
        assert rows[("Compound", "Phenotype")] == "active"
        assert rows[("ProteinComplex", "Disease")] == "active"

    def test_occurs_in_retired_on_both_its_pairs(self, migrated):
        """C-09 retires OCCURS_IN from BiologicalProcess AND ProteinComplex."""
        _, _, v2, _ = migrated
        rows = {(r["from"], r["to"]): r.get("status")
                for r in v2["relationships"] if r["type"] == "OCCURS_IN"}
        assert rows[("BiologicalProcess", "Tissue")] == "retired"
        assert rows[("ProteinComplex", "Tissue")] == "retired"

    def test_protein_complex_keeps_a_tissue_edge(self, migrated):
        """Retiring OCCURS_IN would strand ProteinComplex with no Tissue edge,
        so C-09 must add OPERATES_WITHIN_CONTEXT for it."""
        _, _, v2, _ = migrated
        active = {(r["type"], r["from"], r["to"]) for r in v2["relationships"]
                  if r.get("status") == "active"}
        assert ("OPERATES_WITHIN_CONTEXT", "ProteinComplex", "Tissue") in active


class TestRedirects:
    def test_c07_maps_each_direction_to_its_own_successor(self, migrated):
        """Activation and suppression must not collapse onto one successor."""
        _, _, v2, _ = migrated
        sup = {r["type"]: r.get("superseded_by") for r in v2["relationships"]
               if r.get("retired_by") == "C-07"}
        assert sup["ACTIVATED_BY"] == "PHARMACOLOGICALLY_ACTIVATES"
        assert sup["SUPPRESSED_BY"] == "PHARMACOLOGICALLY_INHIBITS"

    def test_c07_replacements_run_drug_to_process(self, migrated):
        """The whole point of C-07: the Drug must be a source, not a sink."""
        _, _, v2, _ = migrated
        active = {(r["type"], r["from"], r["to"]) for r in v2["relationships"]
                  if r.get("status") == "active"}
        assert ("PHARMACOLOGICALLY_ACTIVATES", "Drug", "BiologicalProcess") in active
        assert ("PHARMACOLOGICALLY_INHIBITS", "Drug", "BiologicalProcess") in active

    def test_c07_directions_are_signed_correctly(self, migrated):
        _, _, v2, _ = migrated
        by = {(r["type"], r["from"], r["to"]): r.get("direction_default")
              for r in v2["relationships"]}
        assert by[("PHARMACOLOGICALLY_ACTIVATES", "Drug", "BiologicalProcess")] == 1
        assert by[("PHARMACOLOGICALLY_INHIBITS", "Drug", "BiologicalProcess")] == -1


class TestCollapse:
    def test_evidenced_by_covers_every_retired_provenance_source(self, migrated):
        _, _, v2, _ = migrated
        retired_sources = {r["from"] for r in v2["relationships"]
                           if r.get("retired_by") == "C-10"}
        evidenced = {r["from"] for r in v2["relationships"]
                     if r["type"] == "EVIDENCED_BY" and r.get("status") == "active"}
        assert retired_sources <= evidenced, f"lost: {sorted(retired_sources - evidenced)}"

    def test_collapse_extends_provenance_to_previously_uncovered_labels(self, migrated):
        """Protein, Variant and Pathway had no path to Publication in v1."""
        _, _, v2, _ = migrated
        evidenced = {r["from"] for r in v2["relationships"]
                     if r["type"] == "EVIDENCED_BY"}
        assert {"Protein", "Variant", "Pathway"} <= evidenced


class TestGeneratedSchema:
    def test_validation_passes(self, migrated):
        _, plan, v2, _ = migrated
        assert check(v2, plan) == []

    def test_no_conflict_refs_survive(self, migrated):
        _, _, v2, _ = migrated
        assert [r["type"] for r in v2["relationships"] if r.get("conflict_ref")] == []

    def test_retired_rows_are_kept_for_migration(self, migrated):
        """Retired rows must remain in the file with a pointer, so a data
        migration can map old edges to new ones."""
        _, _, v2, stats = migrated
        retired = [r for r in v2["relationships"] if r.get("status") == "retired"]
        assert len(retired) == stats["retired"] > 0
        assert all(r.get("superseded_by") and r.get("retired_by") for r in retired)

    def test_extension_layer_survives(self, migrated):
        """The 11 gap-filling extension edges must not be collateral damage."""
        _, _, v2, _ = migrated
        ext = {r["type"] for r in v2["relationships"]
               if r["layer"] == "extension" and r.get("status") == "active"}
        assert {"ENCODES", "MAPS_TO_LOCUS", "HYPOTHESIS_INCLUDES_NODE"} <= ext

    def test_self_loop_constraint_is_explicit(self, migrated):
        _, _, v2, _ = migrated
        assert v2["constraints"]["no_self_loops"]["enforced"] is True

    def test_predicate_importance_keying_recorded(self, migrated):
        """C-11's fix is config, not schema — but it must not be lost."""
        _, _, v2, _ = migrated
        rule = v2["constraints"]["predicate_importance_key"]["rule"]
        assert "from_label" in rule and "to_label" in rule


@pytest.mark.skipif(not V2_PATH.exists(), reason="v2 not generated")
class TestGeneratedFileOnDisk:
    def test_v2_file_loads_through_schema_api(self):
        s = Schema(raw=yaml.safe_load(V2_PATH.read_text()))
        assert s.version == 2
        assert len(s.relationships) > 0

    def test_schema_api_filters_retired_rows(self):
        """Schema.relationships must never hand a retired row to the builder."""
        raw = yaml.safe_load(V2_PATH.read_text())
        s = Schema(raw=raw)
        retired = {(r["type"], r["from"], r["to"]) for r in raw["relationships"]
                   if r.get("status") == "retired"}
        active = {r.endpoint_key for r in s.relationships}
        assert not (retired & active)

    def test_v2_has_no_conflicts(self):
        s = Schema(raw=yaml.safe_load(V2_PATH.read_text()))
        assert s.conflicts() == {}

    def test_v2_is_regenerable(self):
        """The on-disk file must match what the generator produces — otherwise
        someone hand-edited it and the resolution table is no longer the truth."""
        v1, res = load()
        plan = build_plan(res)
        fresh, _ = migrate(v1, plan)
        on_disk = yaml.safe_load(V2_PATH.read_text())
        assert fresh["relationships"] == on_disk["relationships"]
