"""Tests for the primitive implementations.

These matter more than typical unit tests: the generator uses these functions to
compute stored edge scores, so an error here silently corrupts every edge in the
graph and every downstream tier, gap and target ranking.
"""

from __future__ import annotations

import math

import pytest

from skygenic_scans.synth import scoring as s


class TestScan01WorkedExample:
    """Validated against the worked example in the requirements doc."""

    def test_metformin_ampk_reproduces_documented_value(self):
        # Requirment Report Scans.docx, S3 "Real-World Calculation: Metformin/AMPK"
        got = s.compute_scan01_consolidated_score(
            SA=0.95, ES=0.85, recency=0.90, replication=0.80
        )
        assert got == pytest.approx(0.8925, abs=1e-4)

    def test_documented_value_lands_in_tier_1(self):
        score = s.compute_scan01_consolidated_score(0.95, 0.85, 0.90, 0.80)
        assert s.edge_quality_tier(score) == "Tier 1 (Validated Consensus)"

    def test_weight_profile_must_sum_to_one(self):
        # PRIM_P11 validate_weight_profile
        with pytest.raises(ValueError, match="sum to 1.0"):
            s.compute_scan01_consolidated_score(0.9, 0.9, 0.9, 0.9, w_SA=0.5)


class TestSourceAuthority:
    def test_current_year_keeps_full_base_authority(self):
        # SA_decayed = SA_base x 1/log(0 + e) = SA_base x 1
        assert s.compute_SA("peer_reviewed", s.CURRENT_YEAR) == pytest.approx(0.85, abs=1e-3)

    def test_authority_decays_with_age(self):
        recent = s.compute_SA("peer_reviewed", 2025)
        old = s.compute_SA("peer_reviewed", 2005)
        assert old < recent

    def test_curated_ontology_outranks_llm_inference(self):
        assert s.compute_SA("curated_ontology", 2024) > s.compute_SA("llm_inference", 2024)

    def test_unknown_source_gets_conservative_default(self):
        assert s.compute_SA("not_a_real_source") == pytest.approx(0.50)

    def test_stays_within_declared_range(self):
        for st in s.SOURCE_AUTHORITY:
            for yr in (1990, 2010, 2026):
                assert 0.0 <= s.compute_SA(st, yr) <= 1.0


class TestAssertionEvidence:
    """PRIM_E03 point-to-band mapping."""

    def test_maximum_points_map_to_one(self):
        # adj_p<=0.01 (+2), |effect|>=1.0 (+2), replicates>=3 (+1), n>=10 (+1) = 6
        assert s.compute_ES_assertion(0.001, 1.5, replicates=3, n=100) == 1.00

    def test_no_evidence_maps_to_floor(self):
        assert s.compute_ES_assertion(0.9, 0.01, replicates=1, n=2) == 0.20

    def test_bands_are_monotonic(self):
        weak = s.compute_ES_assertion(0.04, 0.6, 1, 5)      # 1+1 = 2 -> 0.55
        mid = s.compute_ES_assertion(0.001, 0.6, 3, 50)     # 2+1+1+1 = 5 -> 0.80
        strong = s.compute_ES_assertion(0.001, 2.0, 4, 500)  # 6 -> 1.00
        assert weak < mid < strong


class TestEdgeAggregateScore:
    def test_strong_evidence_scores_high(self):
        assert s.compute_edge_aggregate_score(2.0, 1e-12, 10_000) > 0.9

    def test_null_evidence_scores_zero(self):
        assert s.compute_edge_aggregate_score(0.0, 1.0, 0) == 0.0

    def test_bounded_to_unit_interval(self):
        # caps documented in ADR-004 must hold even far beyond them
        assert s.compute_edge_aggregate_score(500.0, 1e-300, 10**9) <= 1.0

    def test_monotonic_in_effect_size(self):
        lo = s.compute_edge_aggregate_score(0.1, 0.01, 100)
        hi = s.compute_edge_aggregate_score(1.9, 0.01, 100)
        assert hi > lo


class TestTiers:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (1.00, "Tier 1 (Validated Consensus)"),
            (0.85, "Tier 1 (Validated Consensus)"),
            (0.84, "Tier 2 (Supported)"),
            (0.60, "Tier 2 (Supported)"),
            (0.59, "Tier 3 (Exploratory)"),
            (0.40, "Tier 3 (Exploratory)"),
            (0.39, "Tier 4 (Speculative)"),
            (0.00, "Tier 4 (Speculative)"),
        ],
    )
    def test_boundaries_match_workbook(self, score, expected):
        assert s.edge_quality_tier(score) == expected

    def test_ingestion_threshold(self):
        # SCAN-01: "Any edge falling below this threshold is classified as noise"
        assert not s.passes_ingestion_threshold(0.10)
        assert not s.passes_ingestion_threshold(0.05)
        assert s.passes_ingestion_threshold(0.11)


class TestDirectionScore:
    """SCAN-03. See the spec-conflict note on concordance_flag."""

    def test_worked_example_from_requirements_doc(self):
        # 7 activations vs 1 inhibition -> (7-1)/8 = 0.75
        assert s.compute_direction_score(7, 1) == pytest.approx(0.75)
        assert s.conflict_severity(7, 1) == pytest.approx(0.25)

    def test_sign_carries_biological_direction(self):
        assert s.compute_direction_score(8, 2) > 0    # net activation
        assert s.compute_direction_score(2, 8) < 0    # net inhibition

    def test_even_split_is_contradictory_not_concordant(self):
        """The degenerate case that PRIM_E05 as written gets wrong: 5 vs 5 is a
        maximally conflicted edge, and max(pos,neg)/total would score it 0.50
        and call it CONCORDANT."""
        score, flag = s.concordance_flag(5, 5)
        assert score == 0.0
        assert flag == "CONTRADICTORY"

    def test_all_three_tiers_are_reachable(self):
        """The whole point. Under PRIM_E05's formula two of these are impossible."""
        flags = {s.concordance_flag(p, n)[1] for p, n in
                 [(10, 0), (7, 1), (3, 7), (13, 7), (6, 4), (5, 5), (1, 1)]}
        assert flags == {"CONCORDANT", "CONTEXT_DEPENDENT", "CONTRADICTORY"}

    def test_conflict_severity_agrees_with_prim_e06(self):
        """Cross-check between the two source documents: the requirements doc's
        `1 - |DirectionScore|` and the workbook's PRIM_E06 must agree."""
        for p, n in [(7, 1), (6, 4), (5, 5), (9, 0), (2, 8)]:
            assert s.conflict_severity(p, n) == pytest.approx(
                s.compute_edge_conflict(p, n), abs=1e-4
            )


class TestConcordanceAndConflict:
    def test_unanimous_agreement_is_concordant(self):
        score, flag = s.concordance_flag(pos=10, neg=0)
        assert score == 1.0 and flag == "CONCORDANT"

    def test_balanced_disagreement_maximises_conflict(self):
        # K_edge = 1 - |pos-neg|/total; equal counts -> 1.0
        assert s.compute_edge_conflict(5, 5) == 1.0

    def test_unanimous_has_no_conflict(self):
        assert s.compute_edge_conflict(8, 0) == 0.0

    def test_empty_observations_do_not_divide_by_zero(self):
        assert s.compute_edge_conflict(0, 0) == 0.0
        assert s.concordance_flag(0, 0) == (0.0, "CONTRADICTORY")


class TestSaturation:
    def test_log_curve_saturates_at_one(self):
        # log(1+5)/log(6) = 1.0
        assert s.compute_support_saturation(10, 10) == pytest.approx(1.0, abs=1e-4)

    def test_zero_observations_is_zero(self):
        assert s.compute_support_saturation(0, 10) == 0.0

    def test_curve_is_concave(self):
        """Early evidence should add more than late evidence — that is the point
        of the log curve, and what makes domain scaling meaningful."""
        first = s.compute_support_saturation(2, 10) - s.compute_support_saturation(1, 10)
        last = s.compute_support_saturation(10, 10) - s.compute_support_saturation(9, 10)
        assert first > last


class TestRecency:
    def test_current_year_has_no_decay(self):
        assert s.compute_recency(s.CURRENT_YEAR, "oncology") == pytest.approx(1.0)

    def test_fast_moving_domains_decay_faster(self):
        """Oncology (multiplier 1.4) must decay faster than rare disease (0.35) —
        the behaviour PRIM_E19 describes."""
        onc = s.compute_recency(2010, "oncology")
        rare = s.compute_recency(2010, "rare_disease")
        assert onc < rare

    def test_domain_scaling_table_is_complete(self):
        for d in ("oncology", "immunology", "neurodegeneration", "cardiometabolic",
                  "gene_therapy", "rare_disease", "infectious_disease", "aging",
                  "agriculture", "general"):
            assert d in s.DOMAIN_SCALING
