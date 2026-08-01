"""Evidence, provenance and hypothesis-governance nodes.

This layer is entirely `extension`: none of Dataset, Cohort, Assertion,
BiologicalState, ScanResult or AuditEvent appear in the Nodes doc, yet the
primitives cannot run without them (see gap-analysis Part 2).

It is also where the temporal dimension is manufactured. The scan service is
temporal, so a graph with a single frozen state would not exercise it at all.
Each hypothesis gets a real version trajectory with a deliberate mix of
behaviours, so the trend classifier has all four of its classes present:

  RISING    monotone MC gain as evidence accumulates
  STABLE    |dMC| < 0.02 for >= 3 steps -> PRIM_S11 must flag `converged`
  DECLINING contradictory evidence erodes confidence
  VOLATILE  sign-flipping deltas -> low PRIM_R08 stability score
"""

from __future__ import annotations

import math
from typing import Any

from ..provenance import VersionTriplet, stamp_node
from .config import ScaleConfig
from .entities import BuildContext, EntityBuilder, synth_prov

MC_TRENDS = ("RISING", "STABLE", "DECLINING", "VOLATILE")


class GovernanceBuilder:
    """Produces the evidence and governance node layer."""

    def __init__(self, ctx: BuildContext, entities: EntityBuilder) -> None:
        self.ctx = ctx
        self.ent = entities
        self.nodes: dict[str, list[dict]] = {}
        self.index: dict[str, dict[str, str]] = {}
        # hypothesis canonical_id -> ordered version canonical_ids
        self.version_chain: dict[str, list[str]] = {}
        self.hypothesis_meta: dict[str, dict] = {}

    def _add(self, label: str, canonical_id: str, props: dict, prov, **kw: Any) -> str:
        rec = stamp_node(
            self.ctx.schema, label, canonical_id, props, prov,
            versions=self.ctx.versions, now=self.ctx.now, **kw
        )
        self.nodes.setdefault(label, []).append(rec)
        self.index.setdefault(label, {})[canonical_id] = rec["uid"]
        return rec["uid"]

    def ids(self, label: str) -> list[str]:
        return list(self.index.get(label, {}).values())

    # -- datasets & cohorts -------------------------------------------------
    def build_cohorts(self) -> None:
        c = self.ctx
        ancestries = ["EUR", "EAS", "AFR", "SAS", "AMR", "multi"]
        for i in range(c.scale.cohorts):
            # Paired disease/control cohorts: PRIM_N10 computes cohort separation
            # as |centrality(disease_state) - centrality(control_state)|, which
            # requires both arms to exist.
            state = "disease" if i % 2 == 0 else "control"
            self._add(
                "Cohort", f"SKYGEN.COHORT:{i:05d}",
                {"cohort_id": f"SKYGEN.COHORT:{i:05d}",
                 "name": f"synthetic cohort {i} ({state})",
                 "cohort_state": state,
                 "n_subjects": int(c.rng.triangular(30, 12000, 400)),
                 "ancestry": c.rng.choice(ancestries),
                 "age_mean": round(c.rng.triangular(18, 85, 58), 1)},
                synth_prov("geo", 2, "statistical_sample"),
            )

    def build_datasets(self) -> None:
        c = self.ctx
        platforms = ["Illumina NovaSeq", "10x Chromium", "Affymetrix", "Olink",
                     "MassSpec Orbitrap", "Nanopore", "REDCap"]
        for i in range(c.scale.datasets):
            modality = c.rng.choice(c.scale.modalities)
            st = c.weighted(c.scale.source_types, c.scale.source_type_weights)
            self._add(
                "Dataset", f"SKYGEN.DS:{i:05d}",
                {"dataset_id": f"SKYGEN.DS:{i:05d}", "name": f"synthetic dataset {i}",
                 "modality": modality, "platform": c.rng.choice(platforms),
                 "sample_count": int(c.rng.triangular(6, 5000, 120)),
                 "file_hash": f"{c.rng.getrandbits(256):064x}",
                 "source_type": st},
                synth_prov("geo", 2, "statistical_sample"),
            )

    # -- evidence & assertions ---------------------------------------------
    def build_evidence(self) -> None:
        c = self.ctx
        labs = [f"LAB-{i:03d}" for i in range(40)]
        batches = [f"BATCH-{i:04d}" for i in range(120)]
        platforms = ["NovaSeq", "HiSeq", "10x", "Olink", "Orbitrap"]
        stages = [None, "early", "intermediate", "advanced", "terminal"]
        for i in range(c.scale.evidence):
            etype = c.weighted(c.scale.evidence_types, c.scale.evidence_type_weights)
            modality = c.rng.choice(c.scale.modalities)
            n = int(c.rng.triangular(4, 4000, 60))
            self._add(
                "Evidence", f"SKYGEN.EV:{i:06d}",
                {"evidence_id": f"SKYGEN.EV:{i:06d}", "modality": modality,
                 "evidence_type": etype,
                 "effect_size": round(c.rng.gauss(0, 0.85), 4),
                 "adj_p": 10 ** (-c.rng.triangular(0.5, 30, 3)),
                 "sample_size": n,
                 "replicate_count": c.rng.choices([1, 2, 3, 4, 6],
                                                  weights=[.4, .25, .18, .1, .07])[0],
                 "is_clinical": modality in ("clinical_trial", "imaging"),
                 "disease_stage": c.rng.choice(stages),
                 "batch_id": c.rng.choice(batches),
                 "platform": c.rng.choice(platforms),
                 "lab_id": c.rng.choice(labs)},
                synth_prov("geo", 2, "statistical_sample"),
            )

    def build_assertions(self) -> None:
        c = self.ctx
        predicates = ["upregulates", "downregulates", "interacts_with", "causes",
                      "associated_with", "treats", "inhibits", "activates"]
        for i in range(c.scale.assertions):
            effect = round(c.rng.gauss(0, 0.9), 4)
            adj_p = 10 ** (-c.rng.triangular(0.5, 25, 3))
            n = int(c.rng.triangular(4, 3000, 50))
            from .scoring import compute_ES_assertion

            reps = c.rng.choices([1, 2, 3, 5], weights=[.45, .28, .17, .10])[0]
            es = compute_ES_assertion(adj_p, effect, reps, n)
            res_conf = round(min(1.0, max(0.3, c.rng.betavariate(8, 2))), 4)
            # PRIM_I05 route_promotion_decision, implemented as specified.
            if es >= 0.80 and res_conf >= 0.90:
                decision = "AUTOMATIC"
            elif es < 0.55:
                decision = "REJECTED"
            else:
                decision = "REVIEW"
            self._add(
                "Assertion", f"SKYGEN.ASRT:{i:06d}",
                {"assertion_id": f"SKYGEN.ASRT:{i:06d}",
                 "row_hash": f"{c.rng.getrandbits(256):064x}",
                 "predicate": c.rng.choice(predicates),
                 "direction": c.rng.choice([-1, 0, 1]),
                 "strength": round(min(1.0, abs(effect) / 2), 4),
                 "ES_assertion": es, "effect_size": effect, "adj_p": adj_p, "n": n,
                 "context": c.rng.choice(["in_vitro", "in_vivo", "clinical", "computational"]),
                 "promotion_decision": decision,
                 "resolution_confidence": res_conf},
                synth_prov("user_upload", 2, "statistical_sample"),
            )

    # -- hypotheses & versions ---------------------------------------------
    def build_hypotheses(self) -> None:
        c = self.ctx
        lo, hi = c.scale.versions_per_hypothesis

        for i in range(c.scale.hypotheses):
            hid = f"SKYGEN.HYP:{i:05d}"
            domain = c.rng.choice(c.scale.domains)
            trend = MC_TRENDS[i % len(MC_TRENDS)]
            n_versions = c.rng.randint(lo, hi)

            trajectory = self._mc_trajectory(trend, n_versions)
            final_mc = trajectory[-1]

            self._add(
                "SkygenicHypothesis", hid,
                {"hypothesis_id": hid, "title": f"synthetic hypothesis {i} ({domain})",
                 "Hv": n_versions, "Dv": c.versions.Dv, "Tv": c.versions.Tv,
                 "biological_domain": domain,
                 "decision_context": c.rng.choice(c.scale.decision_contexts),
                 "query_type": c.rng.choice(c.scale.query_types),
                 "pending": False,
                 "locked": c.rng.random() < 0.12,
                 "current_MC": round(final_mc, 4),
                 "novelty_score": round(c.rng.betavariate(2, 2), 4)},
                synth_prov("skygenic", 1, "template_instantiated"),
            )
            self.hypothesis_meta[hid] = {
                "domain": domain, "trend": trend, "trajectory": trajectory,
            }
            self._build_versions(hid, trajectory)
            self._build_lifecycle(hid, i)
            self._build_reasoning_chain(hid, i)
            self._build_confidence_score(hid, trajectory[-1], i)

    def _mc_trajectory(self, trend: str, n: int) -> list[float]:
        """Generate an MC series with the requested qualitative behaviour."""
        c = self.ctx
        start = c.rng.uniform(0.15, 0.55)
        out = [start]
        for _ in range(n - 1):
            prev = out[-1]
            if trend == "RISING":
                step = abs(c.rng.gauss(0.05, 0.02))
            elif trend == "DECLINING":
                step = -abs(c.rng.gauss(0.045, 0.02))
            elif trend == "STABLE":
                # deliberately under the 0.02 convergence threshold in PRIM_S11
                step = c.rng.uniform(-0.012, 0.012)
            else:  # VOLATILE
                step = c.rng.gauss(0, 0.13)
            out.append(max(-1.0, min(1.0, prev + step)))
        return [round(x, 4) for x in out]

    def _build_versions(self, hid: str, trajectory: list[float]) -> None:
        c = self.ctx
        chain: list[str] = []
        triggers = ["DATA_INJECTION", "HYPOTHESIS_EDIT", "RECALIBRATION", "SCHEMA_UPDATE"]
        total_days = 900
        for v, mc in enumerate(trajectory, start=1):
            vid = f"{hid}.V{v:03d}"
            prev = trajectory[v - 2] if v > 1 else None
            delta = round(mc - prev, 4) if prev is not None else None
            # PRIM_H02 compute_stability_index: 1 - mean|dMC|/0.20, floored at 0
            window = trajectory[max(0, v - 5): v]
            deltas = [abs(window[k] - window[k - 1]) for k in range(1, len(window))]
            stability = round(max(0.0, 1 - (sum(deltas) / len(deltas)) / 0.20), 4) if deltas else 1.0
            ts = c.now - __import__("datetime").timedelta(
                days=int(total_days * (len(trajectory) - v) / max(1, len(trajectory)))
            )
            self._add(
                "HypothesisVersion", vid,
                {"version_id": vid, "Hv": v, "MC": mc,
                 "MR": round(min(1.0, max(0.0, c.rng.betavariate(2, 4))), 4),
                 "R": round(c.rng.betavariate(3, 2), 4),
                 "K": round(c.rng.betavariate(2, 5), 4),
                 "stability": stability, "delta_MC": delta,
                 "evidence_count": int(6 * v + c.rng.randint(0, 25)),
                 "trigger": c.rng.choice(triggers),
                 "snapshot_hash": f"{c.rng.getrandbits(256):064x}",
                 "timestamp": ts},
                synth_prov("skygenic", 1, "template_instantiated"),
            )
            chain.append(vid)
        self.version_chain[hid] = chain

    def _build_lifecycle(self, hid: str, i: int) -> None:
        c = self.ctx
        name = c.scale.lifecycle_states[i % len(c.scale.lifecycle_states)]
        sid = f"{hid}.STATE"
        days = c.rng.randint(3, 420)
        self._add(
            "LifecycleState", sid,
            {"state_id": sid, "name": name,
             "entered_at": c.now - __import__("datetime").timedelta(days=days),
             "days_in_state": days},
            synth_prov("skygenic", 1, "template_instantiated"),
        )

    def _build_reasoning_chain(self, hid: str, i: int) -> None:
        c = self.ctx
        cid = f"{hid}.CHAIN"
        steps = [
            f"step {k}: {c.rng.choice(['observe', 'infer', 'corroborate', 'exclude'])} "
            f"evidence node {c.rng.randint(1000, 9999)}"
            for k in range(1, c.rng.randint(3, 9))
        ]
        cov = round(c.rng.betavariate(5, 2), 4)
        self._add(
            "ReasoningChain", cid,
            {"chain_id": cid, "steps": steps, "step_count": len(steps),
             "path_traceability": round(c.rng.betavariate(4, 2), 4),
             "evidence_coverage": cov,
             "explanation_completeness": round(min(1.0, cov * c.rng.uniform(0.8, 1.1)), 4)},
            synth_prov("skygenic", 1, "template_instantiated"),
        )

    def _build_confidence_score(self, hid: str, mc: float, i: int) -> None:
        c = self.ctx
        sid = f"{hid}.SCORE"
        # Components chosen first, then MC recomputed from PRIM_R06 so the stored
        # score is genuinely the formula's output rather than an independent number.
        #
        # Drawn around a per-hypothesis latent quality rather than independently.
        # Independent draws produced MC in [0.137, 0.571] across all 120
        # hypotheses — SCAN-26's "Strong Mechanistic Support" tier (>= 0.60) was
        # never once exercised, so anything gated on it went untested.
        #
        # Correlated components are also the more realistic model: a
        # well-supported mechanism tends to be central AND replicated AND
        # perturbation-sensitive together, not to score high on one axis at
        # random. Note PRIM_R06's attainable maximum is 0.85, not 1.0
        # (0.25+0.25+0.20+0.15 with K=0), so quality must reach ~0.75 to tier
        # Strong.
        quality = c.rng.uniform(0.10, 0.98)

        def around(q: float, spread: float = 0.12) -> float:
            return round(min(1.0, max(0.0, c.rng.gauss(q, spread))), 4)

        C = around(quality)
        dS = around(quality)
        R = around(quality)
        P = around(quality)
        # Contradiction runs opposite to quality: strong mechanisms have less
        # conflicting evidence.
        K = round(min(1.0, max(0.0, c.rng.gauss((1 - quality) * 0.55, 0.10))), 4)
        mc_calc = round(0.25 * C + 0.25 * dS + 0.20 * R + 0.15 * P - 0.15 * K, 4)
        mr_calc = round(0.40 * K + 0.35 * (1 - R) + 0.25 * P, 4)
        self._add(
            "ConfidenceScore", sid,
            {"score_id": sid, "MC": mc_calc, "MR": mr_calc,
             "target_score": round(c.rng.betavariate(2, 3), 4),
             "component_C": C, "component_dS": dS, "component_R": R,
             "component_P": P, "component_K": K,
             "formula_version": "PRIM_R06/v2.0",
             "computed_at": c.now},
            synth_prov("skygenic", 1, "template_instantiated"),
        )

    # -- states, results, audit --------------------------------------------
    def build_biological_states(self) -> None:
        c = self.ctx
        for i in range(c.scale.biological_states):
            sid = f"SKYGEN.STATE:{i:05d}"
            self._add(
                "BiologicalState", sid,
                {"state_id": sid, "Dv": c.versions.Dv - (i % 5),
                 "Tv": c.versions.Tv - (i % 3),
                 "snapshot_hash": f"{c.rng.getrandbits(256):064x}",
                 "node_count": int(c.rng.triangular(500, 60000, 20000)),
                 "edge_count": int(c.rng.triangular(2000, 300000, 90000)),
                 "weight_sum": round(c.rng.uniform(100, 90000), 2),
                 "modularity": round(c.rng.uniform(0.25, 0.85), 4),
                 "written_at": c.past(900)},
                synth_prov("skygenic", 1, "template_instantiated"),
            )

    def build_scan_results(self, scan_ids: list[str]) -> None:
        c = self.ctx
        hyps = list(self.index.get("SkygenicHypothesis", {}))
        if not hyps or not scan_ids:
            return
        for i in range(c.scale.scan_results):
            scan_id = c.rng.choice(scan_ids)
            hid = c.rng.choice(hyps)
            rid = f"SKYGEN.SR:{i:06d}"
            self._add(
                "ScanResult", rid,
                {"result_id": rid, "scan_id": scan_id, "subject_id": hid,
                 "subject_kind": "hypothesis",
                 "computed_fields": {}, "narrative_terms": {},
                 "narrative_primitive_type": c.rng.choice(
                     ["evidence_summary", "structural_finding", "mechanistic_finding",
                      "governance_finding"]),
                 "result_category": c.rng.choice(["Local", "Global", "None"]),
                 "Dv": c.versions.Dv, "Tv": c.versions.Tv, "Hv": c.versions.Hv,
                 "snapshot_hash": f"{c.rng.getrandbits(256):064x}",
                 "formula_version": "v2.0",
                 "timestamp": c.past(700)},
                synth_prov("skygenic", 1, "template_instantiated"),
            )

    def build_audit_events(self) -> None:
        c = self.ctx
        types = ["DATA_VERSION_INCREMENT", "TOPOLOGY_VERSION_INCREMENT",
                 "HYPOTHESIS_VERSION_INCREMENT", "SCAN_EXECUTED", "STATE_WRITTEN",
                 "SCORES_WRITTEN", "OVERRIDE_APPLIED", "ASSERTION_PROMOTED",
                 "CAPITAL_DECISION_LOCK", "MANUAL_APPROVAL_GRANTED",
                 "MANUAL_APPROVAL_DENIED"]
        for i in range(c.scale.audit_events):
            eid = f"SKYGEN.AUDIT:{i:06d}"
            self._add(
                "AuditEvent", eid,
                {"event_id": eid, "event_type": c.rng.choice(types),
                 "actor": c.rng.choice(["USER", "AGENT", "SYSTEM"]),
                 "payload": {}, "timestamp": c.past(900)},
                synth_prov("skygenic", 1, "template_instantiated"),
            )

    def build_all(self, scan_ids: list[str]) -> dict[str, list[dict]]:
        self.build_cohorts()
        self.build_datasets()
        self.build_evidence()
        self.build_assertions()
        self.build_hypotheses()
        self.build_biological_states()
        self.build_scan_results(scan_ids)
        self.build_audit_events()
        return self.nodes
