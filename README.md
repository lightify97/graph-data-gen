# skygenic-scans

Graph-structure validation for the Skygenic scan layer.

Builds a Neo4j knowledge graph from **real biology database seeds expanded into
grounded synthetic data**, then checks — scan by scan — whether the structure defined in
`Nodes and Relationships.docx` can actually support the 104 scans in
`Skygenic_Scan_Master_Workbook_REFACTORED.xlsx`.

The ingestion layer is deliberately out of scope. This exists to answer one question:
**does the graph structure satisfy the use cases, and where doesn't it?**

---

## What it found

| | |
|---|---|
| Schema conflicts in the Nodes doc | **20**, touching 61 of 117 relationship rows |
| Structural gaps (absent, but required by a named primitive) | **7 node labels, 11 relationship types** |
| Scans ready to execute | **75 / 94** in scope |
| Scans blocked on a missing vector layer | **19 (20%)** |
| Scans blocked on missing data | **0** |
| Graph | **53,393 nodes / 232,215 edges**, 124/124 endpoint pairs |
| Real seed data | 5,401 nodes (10.1%), 28,422 edges (12.2%) |

### Schema v2 — conflicts resolved

All 20 conflicts are now resolved in [`schema/ontology.yaml`](schema/ontology.yaml),
generated from the reviewable [`schema/resolutions.yaml`](schema/resolutions.yaml).
Measured on a graph rebuilt under v2:

| | v1 (doc-verbatim) | v2 (resolved) |
|---|---|---|
| Relationship types | 97 | **72** |
| Endpoint pairs | 124 | **107** |
| Edges removed by dedup | 36,450 (15.7%) | **0** |
| Network-role changes | 45 | **0** |
| Top-50 target churn | 38 of 50 | **0 of 50** |
| Scans READY | 75 / 94 | **75 / 94** — no coverage lost |

Zero across the board is the proof: under v2 the canonical projection is identical
to the graph as loaded, so there is no residual duplication to remove.

Retired relationships are **kept** in v2 with `status: retired` and a
`superseded_by` pointer, so a v1→v2 data migration can map every old edge to its
replacement. Cypher for that is in
[`docs/schema-v2-migration.md`](docs/schema-v2-migration.md).

**v2 is the schema.** `schema/ontology.yaml` is v2; v1 is retired to
`schema/archive/ontology-v1-doc-verbatim.yaml` and nothing loads it by default. It is
kept rather than deleted because v2 is *generated* from it — a test asserts the
on-disk v2 still matches what the generator produces, which is what makes v2
verifiable rather than merely asserted. Load it on demand with `SKYGENIC_SCHEMA=v1`.

`validate/conflict_regression.py` now guards against a retired relationship
reappearing in either the schema or the graph, and exits non-zero for CI.

### The conflicts were not cosmetic

Loading the doc verbatim vs. applying the recommended deduplication, on the same
53,393-node graph:

```
edges removed by dedup : 36,450  (15.7% of the graph)
network-role changes   : 45 nodes flip SCAN-02 archetype
top-50 target churn    : 38 of 50 positions
```

**76% of a top-50 target-prioritisation list changes** depending on whether the
duplicate and inverse edges are present. EGFR (`P00533`) flips between *Peripheral
Node* and *Structural Bottleneck*. Full register: [`docs/schema-gap-analysis.md`](docs/schema-gap-analysis.md).

### Four formula defects found by reading the source documents against each other

`PRIM_E05` as specified is **degenerate** — `count(consistent)/count(all)` is ≥0.50
for every input, so two of its three tiers could never fire and all 232,215 edges
came out CONCORDANT. `PRIM_R06` **cannot reach 1.0** (attainable range is
[−0.15, 0.85], not the [0,1] the requirements doc states). Domain scaling is
applied **in the wrong direction** — rare disease at 0.35× moves *away* from
parity, not toward it. And SCAN-04 is **deprecated** in one document, active in the
other. Details and evidence in [`docs/decisions.md`](docs/decisions.md) ADR-010.

### G-01 is the most serious gap, but not for the reason first assumed

Nothing in the doc connects `Gene` to `Protein`. The intuitive claim — that this
severs the graph — is **false**, and `validate/gap_impact.py` exists to be able to say
so: 260 weakly-connected components with `ENCODES`, 260 without.

What the measurement actually shows is two failure modes at once:

- for **~39%** of cognate gene/protein pairs `ENCODES` is the only connector, so those
  relationships simply vanish — a loud failure, which is the good case;
- for the other **~61%** the alternative is a **3.4-hop** detour such as
  `Gene -EXPRESSED_IN-> Tissue -CONTAINS_BIOMARKER-> Protein`, which asserts only that
  a gene and a protein occur in the same tissue. Here a scan returns a plausible wrong
  answer instead of nothing.

Length-weighted primitives (`T02` Dijkstra, `R15` PathScore) prefer the true 1-hop edge
and degrade gracefully; unweighted expansion (`T01` at k≥3, `T10`) cannot tell the routes
apart.

**G-10** was also corrected by measurement: the doc *does* supply subgraph anchors
(`PRIORITIZES_THERAPEUTIC_TARGET` → `target_nodes`, `EXPLAINS_CLINICAL_OUTCOME` →
`outcome_nodes`). What is missing is the mechanism body between them — the subgraph can
be anchored but not delimited.

---

## Quick start

Everything, from nothing:

```bash
./scripts/reproduce.sh
```

~12 minutes using the committed seed bundle; add `--refetch` to re-pull from the
live APIs (~50 min), or `--smoke` for a ~3k-node graph in ~2 min. See
[`docs/reproducing.md`](docs/reproducing.md) for timings, determinism guarantees and
the failure modes worth knowing about.

The individual steps, if you want to run them by hand:

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
```

```bash
cd infra && docker compose up -d
```

```bash
./.venv/bin/python -m skygenic_scans.graph.constraints
```

```bash
./.venv/bin/python -m skygenic_scans.sources.fetch_all
```

```bash
./.venv/bin/python -m skygenic_scans.synth.build
```

```bash
./.venv/bin/python -m skygenic_scans.graph.loader --wipe
```

```bash
./.venv/bin/python -m skygenic_scans.validate.scan_readiness
```

```bash
./.venv/bin/python -m skygenic_scans.validate.conflict_regression
```

```bash
./.venv/bin/python -m skygenic_scans.validate.gap_impact
```

Add `--smoke` to `synth.build` for a ~3k-node graph that builds and loads in seconds.

Neo4j Browser: <http://localhost:7475> — user `neo4j`, password `skygenic-scans-dev`.
Ports are deliberately non-default (7475/7688) so this stack does not collide with the
other Neo4j containers on this machine.

---

## Layout

```
schema/ontology.yaml        Schema v1 — the single source of truth
schema/scans.json           All 104 scans extracted from the workbook
docs/schema-gap-analysis.md Conflict register (C-01..C-20) + gap register (G-01..G-11)
docs/decisions.md           ADRs, including the spec gaps found along the way
docs/graph-guide.md         Guide for bioinformaticians
docs/schema-diagram.md      Generated schema diagrams (Mermaid)
docs/reproducing.md         Full rebuild runbook
scripts/reproduce.sh        One-command rebuild
infra/docker-compose.yml    Neo4j 2026-community + APOC + GDS

src/skygenic_scans/
  schema.py                 Loads and interrogates the ontology
  provenance.py             Stamps + enforces the universal property contract
  sources/                  Cached fetchers for 15 public biology APIs
  synth/                    Grounded expansion; scoring.py implements the real primitives
  graph/                    Constraints and bulk loader
  validate/                 Capability checks, per-scan readiness, conflict + gap impact

data/seeds/                 Raw cached API responses (reproducibility)
data/generated/             nodes.jsonl, edges.jsonl, manifest, reports
```

---

## Design commitments

**Real data first.** Seeds come from HGNC, Ensembl, UniProt, Reactome, OpenTargets,
STRING, GWAS Catalog, GTEx, OLS4 (MONDO/UBERON/CL), HPO and PubMed. Direction of drug
action comes from OpenTargets `actionType`, orthology from real percentage identity,
expression from measured GTEx TPM — not invented.

**Real vs generated is never ambiguous.** Every node and edge carries `is_synthetic`,
`synthesis_method` and `synthesis_seed_uid`. Generated entities live in a reserved
`SKYGEN.*` id namespace so they cannot be misread as real at a glance.

**Every record carries the same mandatory contract.** 18 required node fields and 22
required edge fields — identity, source provenance, temporal validity, version triplet,
content hash. Six of them are not stylistic: `PRIM_N08 get_node_metadata` is specified
to return exactly `{entity_type, canonical_id, ontology_refs, synonyms, species,
created_at}`. Enforced at write time; violations abort the build.

**Scores use the workbook's own formulas.** `synth/scoring.py` implements PRIM_E01–E04,
E19 and the SCAN-01 tier map as specified, so re-running SCAN-01 against the graph must
reproduce the stored values. Random scores would validate nothing.

**Honest failure reporting.** The vector layer is reported `UNSUPPORTED`, not stubbed.
19 scans depend on it. That is a storage decision to make, not a gap to paper over.
