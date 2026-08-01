# Decision Record

Choices made during this build that a reviewer would otherwise have to reverse-engineer.
Each states what was decided, why, and what it would cost to change.

---

## ADR-001 — Load the Nodes doc verbatim, do not normalise  ✅ SUPERSEDED by ADR-012

**Decision (phase 1).** The graph was loaded exactly as `Nodes and Relationships.docx`
specifies, including all 19 conflicts: synonym duplicates, inverse pairs and the one
reversed direction. Nothing was merged, renamed or redirected.

**Why.** The stated goal was to *finalise* the graph structure. Silently fixing the
schema during the build would have hidden the very evidence needed to decide whether
to fix it. Loading it as written made the cost measurable in the units the product
cares about: 15.7% of edges duplicated, 45 nodes changing SCAN-02 archetype, and 38
of the top 50 targets churning.

**Outcome.** That measurement did its job and the decision it informed is made. All 20
conflicts are resolved in schema v2 (see ADR-012). This ADR is retained because it
explains why the earlier artefacts look the way they do — it is no longer the active
policy.

---

## ADR-002 — Agriculture is out of scope

**Decision.** The 10 `AG-SCAN-*` sheets are excluded. 94 of 104 scans are covered.

**Why.** `source_registry.yaml` explicitly lists `CROP_SPECIES`, `PLANT_GENE`,
`PLANT_DISEASE`, `PLANT_PATHWAY`, `AGROCHEMICAL` and `QTL_PLANT` under
`excluded_entity_types`. Generating plant data would have required inventing entity
types the MVP has deliberately deferred.

**Cost to change.** Moderate. Needs plant entity types in the schema, an Ensembl
Plants / Gramene fetcher, and breeding/epigenetic primitive support (`BREED_*`,
`EPI_*`, `MICRO_*` families are already in the primitive library).

**Refinement (measured after the fact).** The blanket exclusion is coarser than the
scans warrant. Running the readiness check against the AG sheets splits them in two:

| | Scans | What they need |
|---|---|---|
| No agriculture-specific machinery | **AG-SCAN-07, AG-SCAN-09** | only `R19, R20, G04, N05, T12, T13, CLIN_03` — all generic and working today |
| Need new primitive families | AG-SCAN-01…06, 08, 10 | 15 unimplemented primitives across `EDIT_*` (4), `BREED_*` (3), `EPI_*` (4), `MICRO_*` (4) |

AG-SCAN-07 (Yield Component Dissection) and AG-SCAN-09 (Trait Pleiotropy Risk) are
quantitative-genetics scans: polygenic scoring, QTL-to-pathway mapping, GxE and
signal propagation are organism-agnostic maths. Wheat QTL and human GWAS are the
same computation.

Their gap is therefore **data, not machinery** — a materially different and cheaper
gap than the other eight. Note the distinction between *executes* and *is valid*:
these two would run against the current human graph and produce numbers, but those
numbers would be about human biology, so they are ready-in-principle only.

Also worth noting for sequencing: the `EDIT_*` family (CRISPR on-target efficiency,
off-target sites, editing-outcome distribution, regulatory-element overlap) is not
actually agriculture-specific. It applies directly to human gene-editing
therapeutics, so building it is not purely agricultural investment even though
today only AG-SCAN-01/02 cite it.

---

## ADR-003 — Extension layer is separate and self-justifying

**Decision.** Seven node labels and eleven relationship types absent from the Nodes doc
are added, each tagged `layer: extension` and carrying a `forced_by` field naming the
primitive that requires it.

**Why.** Distinguishing "the doc is wrong here" from "the doc is silent here" matters.
Conflicts are for the doc's authors to resolve; gaps are things the scans cannot run
without. Mixing them would make the gap analysis unreadable. The `layer` property is
denormalised onto every node and edge so a query can exclude the extension layer
entirely and see exactly what the doc alone supports.

**Cost to change.** None — it is additive and separable.

---

## ADR-004 — Undefined normalisation caps in PRIM_E04

**Decision.** `compute_edge_aggregate_score` caps `|effect_size|` at 2.0 and
`log10(n+1)` at 4.0 (n = 10,000).

**Why.** The primitive specifies `normalize(-log10(adj_p), cap=10)` but leaves the
effect-size and sample-size caps unstated. `normalize` without a cap is undefined for
unbounded inputs. These two constants change *every* edge score in the graph and
therefore every downstream tier, so they are recorded rather than buried.

**This is a genuine spec gap, not just an implementation detail.** The workbook should
state both caps explicitly.

**Cost to change.** One constant each in `synth/scoring.py`, then regenerate.

---

## ADR-005 — Property existence enforced in code, not by constraint

**Decision.** The 18 mandatory node fields and 22 mandatory edge fields are enforced by
`provenance.stamp_node` / `stamp_edge` at write time and re-checked post-load by
`validate.capabilities`, not by database constraints.

**Why.** Neo4j property existence constraints are an Enterprise feature; this is
Community. The single-code-path design gets most of the benefit: no record can be
constructed without passing through the stamping functions, which compute the derived
fields rather than trusting callers.

**Cost to change.** Trivial on Enterprise — add `REQUIRE n.x IS NOT NULL` per field.

---

## ADR-006 — Shared `:Entity` supertype label

**Decision.** Every node carries `:Entity` in addition to its type label, with a
uniqueness constraint on `:Entity(uid)`.

**Why.** Edge loading matches endpoints by `uid` alone. Without a label on the match
pattern Neo4j cannot use any per-label index and every lookup degrades to a scan.
Measured: 118s → 11s for 35k edges, a 10.7× improvement; the effect grows with graph size.

**Cost to change.** None; it is invisible to the scan logic.

---

## ADR-007 — Scores computed with the real formulas, not sampled

**Decision.** `synth/scoring.py` implements PRIM_E01–E04, E19, E11, E06, E05, E08 and
the SCAN-01 tier map exactly as the workbook specifies, and the generator uses them to
compute stored edge scores.

**Why.** If scores were random, re-running SCAN-01 against the graph could not verify
anything — any result would be equally consistent with a correct or a broken
implementation. Because they are computed with the specified formula, recomputation
*must* reproduce the stored value, and any divergence is a real finding about the spec.

**Cost to change.** None; this is strictly stronger than sampling.

---

## ADR-008 — Synthetic records use a reserved id namespace

**Decision.** Generated entities are keyed `SKYGEN.<TYPE>:<n>` (e.g.
`SKYGEN.GENE:000042`), never in a real namespace. Real seeds keep their upstream ids
(`HGNC:11998`, `P04637`, `MONDO:0004975`).

**Why.** `is_synthetic` makes the distinction queryable, but people read ids. A
generated gene that looked like `HGNC:99999` would eventually be cited as real. The
namespace makes misreading impossible at a glance.

**Cost to change.** Would invalidate every stored uid; effectively a rebuild.

---

## ADR-009 — Vector layer is out of scope and reported as UNSUPPORTED

**Decision.** No embeddings are generated. `PRIM_V01`–`V16` and everything depending on
them are reported `UNSUPPORTED` rather than stubbed or faked.

**Why.** 19 of 94 in-scope scans (20%) depend on the vector layer, including all of
SCAN-19, 22, 23, 24, 25, 27 and most of the Semantic family. Faking cosine similarity
with random vectors would have produced a green readiness report that was worthless.
The honest result is a quantified requirement: **the scan layer needs a vector store,
and this is how much of the product depends on it.**

**Open question for the storage design.** Neo4j 2026 has native vector indexes, which
would keep embeddings next to the topology; a Qdrant instance is already running on this
machine, which would decouple them. This is a real fork in the storage decision that is
still being thought through.

**Cost to change.** Substantial — an embedding model choice, a store, a
re-embedding/versioning strategy (`PRIM_V14`/`V15` already specify version tracking).

---

---

## ADR-010 — Formula conflicts between the two source documents

A full read of `Requirment Report Scans.docx` (3,210 lines) against the workbook
found four places where the two disagree. In each case the requirements doc
carries a worked numeric example, so it can be checked; the workbook cannot.

### 10a. `PRIM_E05` concordance is degenerate — **implemented per the requirements doc**

`PRIM_E05` reads `concordance = count(consistent_direction) / count(all_observations)`,
i.e. `max(pos, neg) / total`. That is **≥ 0.50 for every possible input** — the
majority side of a two-way split is never less than half. Against the stated tiers
(CONCORDANT ≥0.50, CONTEXT_DEPENDENT 0.30–0.49, CONTRADICTORY <0.30) the lower two
are unreachable. A maximally conflicted edge (5 activations, 5 inhibitions) scores
0.50 and reports CONCORDANT.

Confirmed empirically: before the fix, **all 232,215 edges in the graph were
CONCORDANT** and neither other tier appeared anywhere.

This is not cosmetic. CONTEXT_DEPENDENT is the documented trigger for SEM-SCAN-10
(Contextual Mechanism Switching) and CONTRADICTORY the trigger for INV-03
(Contradiction Arbitrator). Under `PRIM_E05` as written, both branches are dead code.

The requirements doc S3 instead defines a signed mean, `DirectionScore = Σd_i / n`,
worked as 7 activations vs 1 inhibition → `(7−1)/8 = 0.75`. That is well-behaved,
reproduces exactly, and its sign carries the biological direction. **Implemented.**
`PRIM_E05` should be corrected to match.

Cross-check: the doc's `Conflict Severity = 1 − |DirectionScore|` is algebraically
identical to `PRIM_E06`, so the two documents already agree on that half.

### 10b. `PRIM_R06` cannot reach 1.0 — **range mis-stated in both places**

`MC = 0.25C + 0.25ΔS + 0.20R + 0.15P − 0.15K` with all components in [0,1] has an
attainable range of **[−0.15, 0.85]**. It can never reach 1.0.

- The requirements doc says MC "ranges from 0 to 1" — wrong at both ends.
- `schema/ontology.yaml` declared `[-1,1]` — also wrong.

Both worked examples reproduce exactly (MC 0.6525 → doc's 0.65; MR 0.1775 → doc's
0.18), so the formula is right and only the stated range is wrong. Practical
consequence: a UI rendering "MC 0.85 / 1.00" implies 15% of headroom that does not
exist, and "Strong Mechanistic Support" (≥0.60) needs all four positive components
around 0.75+ simultaneously.

This also surfaced a data defect — see 10e.

### 10c. Domain scaling is applied in the wrong direction

`00_Domain_Scaling_Table` says it "multiplies its raw saturation input by
saturation_multiplier … so a field with structurally less possible evidence
(e.g. Rare Disease) reaches 'mature' status at a realistic dataset count".

Multiplying does the opposite. Rare disease at 0.35× takes saturation from 0.226
down to **0.083** — further from oncology, not closer. Applied as a *divisor* it
gives 0.493, which matches the documented intent and the requirements doc's
example (25 rare-disease papers ≈ 250 oncology papers).

The multiplier is correct for `PRIM_E19` recency, where a fast-moving field should
decay faster. It is inverted for saturation. Recommend either separate constants
per use, or applying it to `max_possible` rather than to `raw`.

### 10d. Domain scaling constants differ between documents

Requirements doc: oncology 1.00, rare disease 0.30. Workbook: oncology 1.4, rare
disease 0.35. The doc's are prefixed "suppose", so likely illustrative — but the
workbook's should be confirmed as normative.

### 10e. SCAN-04 is deprecated in one document and active in the other

The requirements doc states SCAN-04 is "no longer an active analytical module …
solely a backward-compatible routing stub" redirecting to SCAN-06. The workbook
index still lists it as an active Core scan, and the readiness report counts it
among the 94 in-scope scans. Effective active count is **93**.

---

## ADR-011 — Confidence components must be correlated, not independent

Initial generation drew C, ΔS, R, P and K independently. Across all 120
hypotheses that produced MC in **[0.137, 0.571]** — SCAN-26's "Strong Mechanistic
Support" tier (≥0.60) was never exercised, so every downstream behaviour gated on
it went untested.

The cause is arithmetic: independent draws with means near 0.4 average to ~0.33,
and reaching 0.60 requires four components near 0.75 simultaneously.

Components are now drawn around a per-hypothesis latent quality. This is also the
more realistic model — a genuinely well-supported mechanism tends to be central
*and* replicated *and* perturbation-sensitive together, not to score high on one
axis by chance.

**General lesson for synthetic data:** a generator must be checked for *tier-space
coverage*, not just for plausible marginal distributions. Both defects found here
(this and 10a) were invisible in summary statistics and only appeared when the
tier counts were tabulated.

---

---

## ADR-012 — Schema v2 is canonical; v1 is retired, not deleted

**Decision.** `schema/ontology.yaml` **is** v2. All 20 conflicts are resolved. v1 is
archived to `schema/archive/ontology-v1-doc-verbatim.yaml` and nothing loads it by
default. Supersedes ADR-001.

**Why.** v1's purpose — making the cost of the conflicts measurable — is complete. It
was never a candidate for production: it duplicated 15.7% of edges and moved 38 of
the top 50 targets. Keeping it as the default would have meant every build carried a
known-broken topology.

**Why archived rather than deleted.** Two reasons, both practical:

1. **v2 is *generated* from v1 + `resolutions.yaml`.** `schema_migrate` reads the
   archived file, and a test asserts the on-disk v2 still matches what the generator
   produces. Delete v1 and v2 becomes an unverifiable artefact that nobody can
   regenerate or diff — it would go from *derived* to *asserted*.
2. **It is the evidentiary basis for 20 resolution decisions.** If someone later
   disputes why `EXPRESSES` was retired, the answer has to be reproducible.

It costs 54 KB and is loadable on demand with `SKYGENIC_SCHEMA=v1`.

**What changed operationally.**

| | Before | After |
|---|---|---|
| Default schema | v1 (doc-verbatim) | **v2 (resolved)** |
| `SKYGENIC_SCHEMA` default | `v1` | `current` |
| `validate/conflict_impact.py` | quantified the v1 cost | **removed** |
| `validate/conflict_regression.py` | — | **new**: fails if a retired relationship reappears |

**The validator swap is the substantive part.** `conflict_impact` answered "what do
these conflicts cost?", which is now a settled question. `conflict_regression` answers
"has anyone reintroduced one?", which is a live risk forever. Re-adding `EXPRESSES`
alongside `EXPRESSED_IN` would not error anywhere — it would silently re-inflate
degree and start moving target rankings again. That is exactly the class of change a
guard should catch, and it exits non-zero for CI.

The historical numbers are preserved in `data/generated/v1-baseline/` and reported
for context by the regression guard.

---

## Open questions

1. **C-07 direction.** `ACTIVATED_BY` / `SUPPRESSED_BY` are documented as
   `BiologicalProcess → Drug`, which makes the Drug a sink and silently truncates every
   exposure→outcome path. Loaded as written; needs an authoring decision.
2. **C-20 self-loop wording.** "Remove Gene → Gene" is ambiguous between true
   self-loops and all same-label edges. This build enforces the former. Needs the doc
   to say so explicitly.
3. **PRIM_P09 predicate importance.** Keyed on predicate alone, but `CAUSES` spans four
   different endpoint pairs. Recommend keying on `(predicate, from_label, to_label)`.
4. **Temporal service semantics.** `valid_from` / `valid_to` are populated but never
   closed during load. Whether a superseded edge is closed-and-replaced or versioned
   in place determines whether historical scan results stay reproducible.
