# Schema Gap & Conflict Register — v1

Derived from `Nodes and Relationships.docx`, cross-checked against the 104 scan
sheets and `00_Primitive_Library` in `Skygenic_Scan_Master_Workbook_REFACTORED (4).xlsx`,
and against `source_registry.yaml`.

> **Status: HISTORICAL.** This register describes schema **v1**, which is retired to
> `schema/archive/ontology-v1-doc-verbatim.yaml`. Every conflict below is now resolved
> in the active schema — see [`schema-v2-migration.md`](schema-v2-migration.md) for
> what each became and [`../schema/resolutions.yaml`](../schema/resolutions.yaml) for
> the decision and rationale. This document is kept because it is the evidence those
> decisions rest on, and because the *gap* register (Part 2) is still current.

**Build policy at the time:** the graph loaded the Nodes doc **verbatim**. Nothing
below was silently fixed. Every conflict was loaded as written so its effect on the
scan maths was measurable rather than argued about.

Two categories:

- **C-xx — Conflicts.** Present in the doc, and in tension with itself or with a scan.
- **G-xx — Gaps.** Absent from the doc, but required by a named primitive. These are
  the only things added to the graph, and they live in the `extension` layer.

Summary: **20 conflicts across 61 of 117 relationship rows**, and **11 relationship
gaps + 7 node gaps**.

---

## Part 1 — Conflicts

### The three structural families

The 19 endpoint-level conflicts collapse into three mechanisms, each of which breaks
a different part of the scan stack:

| Family | Conflicts | What breaks |
|---|---|---|
| **Synonym duplication** — same endpoint pair, 2–3 names | C-01,02,03,05,06,12,17,18,19 | Evidence counting, degree centrality |
| **Inverse pairs** — A→B and B→A both defined | C-08,09,13,14,15,16 | Creates 2-cycles; breaks DAG extraction and path enumeration |
| **Direction inversion** — arrow points at the agent | C-07 | Makes the drug a sink; directed traversal cannot reach it |

Plus C-04 (asymmetric synonym), C-10 (predicate sprawl), C-11 (type reuse),
C-20 (a wording ambiguity that could destroy real edges).

---

### C-01 · `CAUSES` / `DRIVES_DISEASE_PATHOLOGY` / `DRIVES_PATHOLOGY`
`BiologicalProcess → Disease`, three names for one assertion.
Defined in the *Protein* table, the *Biological process (revised)* table, and the
*Disease* table respectively.

**Impact.** `PRIM_E14 get_edge_evidence_count` counts distinct assertions supporting an
edge — but there is no single edge, there are three. `PRIM_N01` degree centrality for
every Disease node inflates by up to 3×, which propagates into `PRIM_N05` influence and
therefore into the `C` term of `PRIM_R06` mechanism confidence. SCAN-09 evaluates the
same causal claim three times.

**Recommend.** Keep `DRIVES_DISEASE_PATHOLOGY` (the revised table is the later pass and
its name is unambiguous). Retire `CAUSES(BP→Disease)` and `DRIVES_PATHOLOGY`.

---

### C-02 · `PRECEDES` / `TEMPORALLY_PRECEDES`
`BiologicalProcess → BiologicalProcess`.

**Impact.** SCAN-07 extracts a *minimal causal DAG* via `PRIM_T05` (Steiner tree).
Two parallel edges between the same pair inflate the spanning structure and make the
"minimal" subgraph non-minimal. `PRIM_T03 get_all_paths` enumerates each route twice,
doubling cost at every hop up to the ceiling of 6.

**Recommend.** Keep `TEMPORALLY_PRECEDES`; it states the semantics the scan actually
needs. Retire `PRECEDES`.

---

### C-03 · `RESULTS_IN` / `PRODUCES_CLINICAL_PHENOTYPE` / `PRODUCES_OUTCOME`
`BiologicalProcess → Phenotype`, three names.

**Impact.** Same as C-01. Additionally `PRIM_R15 compute_path_score` is
`Π(ES(e_i)) × (1/len(path))` — with duplicate parallel edges the shortest path is
ambiguous and the score depends on which duplicate the traversal happens to pick.

**Recommend.** Keep `PRODUCES_CLINICAL_PHENOTYPE`. Retire the other two.

---

### C-04 · `CONSERVED_IN` vs `EVOLUTIONARILY_CONSERVED_IN` — asymmetric
`CONSERVED_IN` is defined from Gene, Tissue and ProteinComplex to Species.
`EVOLUTIONARILY_CONSERVED_IN` is defined from BiologicalProcess to Species.
The *Protein* table **also** gives `CONSERVED_IN(BP→Species)`.

So BiologicalProcess has both names; every other source type has only one.

**Impact.** SCAN-16 computes an Evolutionary Conservation Score across node types. The
asymmetry means BP nodes are scored from two edge populations and Gene nodes from one —
the score is not comparable across node types, which is exactly what SCAN-16 exists to do.

**Recommend.** One name for all four source types. `CONSERVED_IN` is the smaller change.

---

### C-05 · `BIOMARKER_FOR` vs `SERVES_AS_BIOMARKER_FOR`
`BiologicalProcess → Disease` has both. `Gene → Disease` and
`ProteinComplex → Phenotype` have only `BIOMARKER_FOR`.

**Impact.** SCAN-17 requires a Biomarker Score ≥ 0.70 computed by
`PRIM_R10 = Influence × CR(node→outcome) × mean_ES`. `mean_ES` averages over the
node's biomarker edges — for BP that average is taken over a doubled population.

**Recommend.** Keep `BIOMARKER_FOR` uniformly. Retire `SERVES_AS_BIOMARKER_FOR`.

---

### C-06 · `PART_OF` vs `CONSTITUTES_PATHWAY`
`BiologicalProcess → Pathway`.

**Impact.** SCAN-13's Polygenic Mechanism Score is driven by *Pathway Coverage*.
Duplicate membership edges inflate coverage without adding a single real member.

**Recommend.** Keep `CONSTITUTES_PATHWAY`. Retire `PART_OF`.

---

### C-07 · `ACTIVATED_BY` / `SUPPRESSED_BY` — direction points at the agent ⚠️
Defined as `BiologicalProcess → Drug`. Read literally, the edge runs *from* the
process *to* the drug that modulates it.

**This is the most functionally severe conflict in the register.**

**Impact.** SCAN-07 extracts "the minimal causal DAG connecting the drug exposure node
to the phenotype outcome node". `PRIM_T02 get_shortest_path` and
`PRIM_T01 get_k_hop_neighbors(direction=OUT)` traverse the arrow. With the arrow
reversed the Drug is a **sink**, not a source — a directed walk starting at a Drug can
never reach the process it modulates. SCAN-07, SCAN-29 and the whole
exposure→outcome family silently return empty or truncated paths rather than erroring.
`PRIM_T12 propagate_signal` likewise propagates *away* from the outcome.

Note the *Drug* table already defines `PHARMACOLOGICALLY_ACTIVATES` and
`PHARMACOLOGICALLY_INHIBITS` as `Drug → Protein`, in the correct causal direction. The
BP-scoped pair is the odd one out.

**Recommend.** Re-point to `Drug → BiologicalProcess`. If the inverse reading is
genuinely intended for narration, store the causal direction and derive the inverse
phrasing at render time — do not store it as topology.

---

### C-08 · `INVOLVES` vs `mQTL_MODULATES` — inverse pair
`INVOLVES(BP → Variant)` and `mQTL_MODULATES(Variant → BP)`.

**Impact.** Together these form a **2-cycle** between every linked BP/Variant pair.
`PRIM_T07 detect_feedback_cycles` (Johnson's algorithm) will report each such pair as a
feedback loop and classify it by `net_sign = Π(directions)` — a fabricated regulatory
motif that is purely an artefact of storing both directions. SCAN-07's `acyclic_required`
guarantee fails.

**Recommend.** Keep `mQTL_MODULATES` (Variant→BP, causal direction). Retire `INVOLVES`.

---

### C-09 · `OCCURS_IN` / `OPERATES_WITHIN_CONTEXT` / `CONTEXTUALIZES`
Two synonyms **plus** an inverse: `OCCURS_IN(BP→Tissue)`,
`OPERATES_WITHIN_CONTEXT(BP→Tissue)`, `CONTEXTUALIZES(Tissue→BP)`.
`OCCURS_IN` is additionally reused for `ProteinComplex→Tissue`.

**Impact.** SCAN-08 computes Modality Coverage from `PRIM_E16`, which takes *the
fraction of a node's edges carrying a given modality*. Both the duplicate and the
inverse land in that denominator, so the Context-Universal / Context-Dependent
classification is computed against an inflated edge count. Plus the 2-cycle problem
from C-08.

**Recommend.** Keep `OPERATES_WITHIN_CONTEXT(BP→Tissue)` and extend it to
ProteinComplex. Retire `OCCURS_IN` and `CONTEXTUALIZES`.

---

### C-10 · Provenance predicate sprawl — 5 names, 9 rows
`SUPPORTED_BY` (from BP, Gene, ProteinComplex, Tissue), `PROCESS_EVIDENCED_BY`,
`DISEASE_EVIDENCED_BY`, `DRUG_EVIDENCED_BY`, `PHENOTYPICALLY_SUPPORTED_BY`,
`SUPPORTED_BY_PROVENANCE` — all `X → Publication`.

**Impact.** SCAN-11 must "retrieve the complete provenance chain" to award
*Fully Traceable (Audit-Ready)*. That retrieval has to union five relationship type
names today, and a sixth the moment a new node type is added. A missed name does not
raise an error — it silently downgrades a fully-traceable claim to partially-traceable.
For a control whose stated purpose is regulatory audit, a silent false negative is the
worst available failure mode.

**Recommend.** Collapse to a single `EVIDENCED_BY(* → Publication)`. Keep the source
node's label as the discriminator; it already carries that information.

---

### C-11 · Relationship type reused across endpoint pairs
`ACTIVATES`, `INHIBITS`, `CAUSES` and `PREDICTED_TO_INTERACT_WITH` each appear over
several different `(from, to)` pairs — e.g. `CAUSES` spans `BP→Disease`,
`Gene→Disease`, `Compound→Phenotype`, `ProteinComplex→Disease`.

**Impact.** This is legal and idiomatic in Neo4j, and mostly harmless. The one real
problem is `PRIM_P09 load_predicate_importance_table`, which maps **predicate → float**.
A single importance weight for `CAUSES` has to serve both "a biological process causes a
disease" and "a compound causes a phenotype". That weight feeds
`PRIM_R17 / PRIM_H04 compute_gap_importance` at 20%, so gap ranking inherits the
compromise.

**Recommend.** Keep the shared type names, but key the importance table on
`(predicate, from_label, to_label)` rather than predicate alone.

---

### C-12 · `STANDARDIZES_DISEASE` vs `STANDARDIZED_BY_ONTOLOGY`
`Disease → OntologyTerm` is defined twice under different names (Ontology table and
Disease table). `STANDARDIZED_BY_ONTOLOGY` is additionally used for `BP → OntologyTerm`.

**Impact.** SCAN-27 computes Embedding Similarity and Clinical Indication Overlap from
ontology mappings; duplicated mappings distort the Jaccard term in `CLIN_01`.

**Recommend.** Keep `STANDARDIZED_BY_ONTOLOGY` (already the general form).
Retire `STANDARDIZES_DISEASE`.

---

### C-13 · `REGULATED_BY_QTL` vs `eQTL_MODULATES` — inverse pair
`REGULATED_BY_QTL(Gene → Variant)` and `eQTL_MODULATES(Variant → Gene)`.

**Impact.** 2-cycle on every eQTL. SCAN-18 requires *High QTL Co-localization ≥ 0.75*
via `PRIM_R21 = −log10(qtl.adj_p) × PathScore(gene→pathway_node)`; the duplicate
inflates the gene's local degree and shortens apparent paths.

**Recommend.** Keep `eQTL_MODULATES` (matches the pQTL/mQTL pair, and runs in the
causal direction). Retire `REGULATED_BY_QTL`.

---

### C-14 · `EXPRESSED_IN` vs `EXPRESSES` — inverse pair
`Gene → Tissue` and `Tissue → Gene`.

**Impact.** 2-cycle on every expression fact. Tissue nodes are high-degree hubs, so
this is the single largest contributor to degree inflation in the graph. Directly
distorts `PRIM_N02` betweenness, and therefore SCAN-02's four-way network-role matrix —
a node can be classified *Structural Bottleneck* purely from duplicated expression edges.

**Recommend.** Keep `EXPRESSED_IN(Gene→Tissue)`. Retire `EXPRESSES`. Neo4j traverses
relationships in either direction at no cost, so the inverse reading needs no stored edge.

---

### C-15 · `LOCALIZES_PATHOLOGY_IN` vs `EXHIBITS_PATHOLOGY_OF` — inverse pair
`Disease → Tissue` and `Tissue → Disease`.

**Recommend.** Keep `LOCALIZES_PATHOLOGY_IN`. Retire `EXHIBITS_PATHOLOGY_OF`.

---

### C-16 · `DISTRIBUTED_IN_CONTEXT` vs `ACCUMULATES` — inverse pair
`Drug → Tissue` and `Tissue → Drug`.

**Impact.** SCAN-28 simulates network perturbation to compute Aggregate Off-Target
Risk. The duplicated edge doubles the drug's tissue footprint, and the risk score is
monotonic in that footprint — so this inflates a **safety** number.

**Recommend.** Keep `DISTRIBUTED_IN_CONTEXT`. Retire `ACCUMULATES`.

---

### C-17 · `CAUSALLY_DRIVES` vs `MR_VALIDATES_RISK_FOR`
`Variant → Disease`, both justified in the doc by the same Mendelian-randomisation
criterion under SCAN-09.

**Recommend.** Keep `MR_VALIDATES_RISK_FOR` — it names the evidence class, which is
what SCAN-09 actually branches on. Retire `CAUSALLY_DRIVES`.

---

### C-18 · `AMELIORATES` vs `AMELIORATES_TRAIT`
`Drug → Phenotype`. Pure synonym.

**Recommend.** Keep `AMELIORATES_TRAIT`. Retire `AMELIORATES`.

---

### C-19 · `TREATS_INDICATION` vs `CLINICALLY_TREATS`
`Drug → Disease`. Pure synonym, and this is the primary therapeutic edge in the graph.

**Impact.** SCAN-29 requires *Translationally Concordant ≥ 0.65* and SCAN-07 uses this
edge as the terminus of the exposure→outcome DAG. Duplication here hits the highest-value
path in the product.

**Recommend.** Keep `CLINICALLY_TREATS`. Retire `TREATS_INDICATION`.

---

### C-20 · "Remove all self-loops" is ambiguous and dangerous ⚠️
Part 1 §II.1 says:

> Remove: Gene → Gene, Protein → Protein, Compound → Compound, Pathway → Pathway

**Two readings.**

1. Remove **true self-loops** — `(n)-[r]->(n)`, the same node to itself. These are
   genuinely useless: they contribute nothing to betweenness and create zero-hop
   artefacts, exactly as the doc argues.
2. Remove **all same-label edges** — any Gene→Gene edge, including between *different*
   genes.

Reading 2 would delete `UPREGULATES`, `DOWNREGULATES`, `INTERACTS_WITH`,
`PREDICTED_TO_INTERACT_WITH` and `ORTHOLOG_OF` — all five of which Part 2 explicitly
**defines** and which SCAN-12, SCAN-14 and SCAN-25 are built on. It would also take out
`TEMPORALLY_PRECEDES`, `MECHANISTICALLY_SIMILAR_TO`, `ONTOLOGICALLY_INCLUDES` and
`SEMANTICALLY_OVERLAPS_WITH`.

The doc's own justification ("a compound does not therapeutically interact with itself")
only supports reading 1.

**Recommend.** State reading 1 explicitly in the doc. This build enforces
`NOT (n)-[r]->(n)` and permits same-label edges between distinct nodes.

---

## Part 2 — Gaps

Everything here is **absent from the Nodes doc** but demanded by a named primitive.
These are loaded in the `extension` layer and tagged with the primitive that forces them.

### Node gaps — 7 labels

| Label | Forced by | Consequence if absent |
|---|---|---|
| `Dataset` | `PRIM_N07` node-class enum; `PRIM_E13 get_edges_by_dataset`; `PRIM_T17 get_affected_subgraph_for_dataset` | No recompute trigger on new data; SCAN-10 cannot partition by dataset |
| `Cohort` | `PRIM_N07`; `PRIM_N10 compute_cohort_separation_for_node`; `PRIM_G02` | The `ΔS` term of `PRIM_R06` mechanism confidence is uncomputable — 25% of MC |
| `Assertion` | `PRIM_S16`, `PRIM_I04`, `PRIM_E15`, `PRIM_H03` | No promotion pipeline, no `assertion_ids` in provenance; SCAN-11 cannot reach source rows |
| `BiologicalState` | `PRIM_S07 write_biological_state`, `PRIM_S21` | No versioned graph state; `PRIM_T16` snapshot hashing has nothing to hash |
| `CellType` | `source_registry` CELL_TYPE; `ONC_05`, `IMM_01` | ONC-SCAN-01 (TME Interaction) and IMM-SCAN-01/03 cannot execute |
| `ScanResult` | `PRIM_S10 persist_scan_result` | Scan output has nowhere to land; no version triplet audit trail |
| `AuditEvent` | `PRIM_S13 create_audit_event` | No append-only log; SCAN-11 audit-readiness unachievable |

`PRIM_N07` is worth calling out on its own: it is specified to return
`Gene|Protein|Pathway|Disease|Drug|Cohort|Dataset|Hypothesis|Variant`. **Cohort** and
**Dataset** appear in that enum but nowhere in the Nodes doc.

### Relationship gaps — 11

| Ref | Edge | Forced by | Consequence if absent |
|---|---|---|---|
| **G-01** | `ENCODES(Gene→Protein)` | Nothing in the doc connects Gene to Protein at all | ⚠️ **Silent corruption, not disconnection** — see the measured correction below. Highest-priority gap. |
| **G-02** | `PROTEIN_INTERACTS_WITH(Protein→Protein)` | Part 1 names STRING as the source of protein–protein interaction, but Part 2 defines `INTERACTS_WITH` only Gene→Gene | No PPI network; SCAN-14 proximity enrichment and SCAN-25 link prediction run on the wrong entity type |
| **G-03** | `PROTEIN_PARTICIPATES_IN(Protein→Pathway)` | Part 1 maps Reactome to Protein/ProteinComplex→Pathway; Part 2 defines `PARTICIPATES_IN` only Gene→Pathway | Pathway membership only reachable via Gene, so pathway-level scans miss protein-only members |
| **G-04** | `MAPS_TO_LOCUS(Variant→Gene)` | `PRIM_G01`, verbatim: *"must connect to standard Gene nodes via a localized maps_to_locus edge type"* | `PRIM_G01 get_variants_by_gene` has no edge to traverse |
| **G-05** | `EVIDENCED_BY_ASSERTION`, `DERIVED_FROM_DATASET` | `PRIM_E15`, `PRIM_S17`, `PRIM_E13` | Provenance chain is broken between edge and source row |
| **G-06** | `MEASURED_IN_COHORT(Dataset→Cohort)` | `PRIM_N10` | Cannot partition disease vs control; `ΔS` uncomputable |
| **G-07** | `OBSERVED_IN_CELL_TYPE(Evidence→CellType)` | `ONC_05`, `IMM_01` | TME and immune-infiltration scans blocked |
| **G-08** | `HAS_VERSION(Hypothesis→HypothesisVersion)` | `PRIM_R08` needs the last `min(5, N−1)` entries; `PRIM_S09` is append-only | `SUPERSEDES_VERSION` alone gives no **ordered** history, so stability index cannot be computed |
| **G-09** | `SCOPED_TO_STATE(Hypothesis→BiologicalState)` | `PRIM_S21 link_hypothesis_to_state` | Initial MC never triggers |
| **G-10** | `HYPOTHESIS_INCLUDES_NODE` | `PRIM_T09 extract_hypothesis_subgraph` needs `target_nodes` / `outcome_nodes` | Part 1 §II.2 correctly removed the generic hub-and-spoke hypothesis edges. The doc does still supply subgraph *anchors* — see the measured correction below — but not the mechanism body between them. |
| **G-11** | `SCAN_RESULT_FOR(ScanResult→Hypothesis)` | `PRIM_S10` | Results not attributable |

---

## Part 2b — Measured corrections to G-01 and G-10

An earlier draft of this register asserted that G-01 and G-10 "stop scans
outright". `validate/gap_impact.py` was written to test exactly that, and it
**falsified both claims in their original form**. Both entries above are corrected.
The underlying problems are real; the mechanism is different, and in G-01's case worse.

### G-01 — a mixed failure mode, and not disconnection

Measured on the verified 53,393-node / 232,215-edge graph:

| | |
|---|---|
| Cognate gene/protein pairs sampled | 150 |
| …that have a non-`ENCODES` path | **92 (61.3%)** |
| Mean length of that alternative | **3.37 hops** (vs 1 for `ENCODES`) |
| Proteins reached per gene via the Tissue hub | 6.9 of 5,000 (**0.1%**) |
| Weakly-connected components, with / without `ENCODES` | **260 / 260** |

Three conclusions, none of which match the original claim:

1. **The graph does not fragment.** 260 components either way. "Severs the graph"
   was wrong.
2. **For ~39% of cognate pairs `ENCODES` is the only connector.** Remove it and
   those gene→protein relationships become unreachable outright — a *loud*
   failure, which is the good case.
3. **For the other ~61% the alternative is a ~3.4-hop detour** through routes like
   `Gene-EXPRESSED_IN->Tissue-CONTAINS_BIOMARKER->Protein`. That path asserts only
   *"this gene is expressed in liver, and liver contains this protein"* — not that
   the gene encodes it. Here the failure is quiet: a scan returns a plausible
   wrong answer instead of nothing.

So G-01 is **both** failure modes at once, split roughly 40/60, and which one you
get depends on the pair. It remains the highest-priority gap — it removes the only
one-hop statement that a gene encodes a protein, and SCAN-24 is scoped to `Protein`
while all genetic evidence hangs off `Gene` — but "disconnection" and "uniform
silent corruption" are both overstatements.

Practical note on which primitives degrade how: length-weighted traversal
(`PRIM_T02` Dijkstra on `1−ES`, `PRIM_R15`'s `1/len(path)` term) strongly prefers a
1-hop edge over a 3.4-hop detour, so those degrade gracefully. Unweighted expansion
(`PRIM_T01 get_k_hop_neighbors` at k≥3, `PRIM_T10 get_common_neighbors`) cannot tell
the routes apart at all.

> **Methodological note.** An earlier run of this measurement reported 100% of
> cognate pairs having an alternative path and concluded "uniform silent
> corruption". That run executed against a graph left in a mixed state by a
> partially-completed load (546k relationships against a 232k manifest), which
> manufactured spurious connectivity. The loader now verifies its own row counts
> against the manifest and refuses to let validators run otherwise. The numbers
> above are from a load verified exact.

### G-10 — the doc supplies anchors, not the mechanism body

The original claim ("no way to define a hypothesis subgraph") was wrong. The doc
*does* provide:

- `PRIORITIZES_THERAPEUTIC_TARGET (Hypothesis → Protein)` → `PRIM_H05.target_nodes`
- `EXPLAINS_CLINICAL_OUTCOME (Hypothesis → Disease)` → `PRIM_H05.outcome_nodes`

Those are precisely the two anchor sets `PRIM_H05` names. What is missing is
everything between them: no `Gene`, `Pathway`, `Variant` or `BiologicalProcess` is
reachable from a hypothesis by any documented edge. `PRIM_T09` is specified as
*"targets + expected_edges + 1-hop neighborhood"* — the targets and outcomes
exist, but `expected_edges` has no representation at all.

So the corrected statement is: **a hypothesis subgraph can be anchored but not
delimited.** The extension edge is still needed, but it is narrower than first
claimed — it supplies mechanism membership, not the whole scope.

---

## Part 3 — Other observations

**`Variant` vs `GeneticVariant`.** The Nodes doc uses *Variant*; the Gene table says
*Genetic Variant*; `PRIM_G01` demands a "specialized GeneticVariant node class" with
`variant_id, chromosome, base_position, p_raw, effect_weight`. Modelled here as a single
`Variant` label with a `:GeneticVariant` secondary label applied when those statistical
fields are populated — one entity, two views, no duplication.

**`SkygenicHypothesisTextSnapshot`.** Part 1 is right that it has no place in the
mechanistic graph. Dropped. Note that `PRIM_S22 write_agent_output_to_hypothesis`
(`SUMMARY|GAP_NARRATIVE|DRIFT_ALERT|DOSSIER`) still needs somewhere to write —
that belongs in the document store, not here.

**Agriculture.** 10 AG-SCAN sheets exist, but `source_registry.yaml` excludes
`CROP_SPECIES`, `PLANT_GENE`, `PLANT_DISEASE`, `PLANT_PATHWAY`, `AGROCHEMICAL`,
`QTL_PLANT`. Per the build decision, agriculture is **out of scope**: 94 of 104 scans
are covered. The 10 AG scans remain unvalidated and their plant entity types undesigned.

**Where the mandatory node fields come from.** `PRIM_N08 get_node_metadata` is specified
to return `{entity_type, canonical_id, ontology_refs, synonyms, species, created_at}`.
Those six are therefore contractual, not stylistic — see `node_property_contract` in
`schema/ontology.yaml`, which adds source/provenance, temporal validity and version
fields on top.
