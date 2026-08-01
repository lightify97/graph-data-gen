# The Skygenic Validation Graph — a guide for bioinformaticians

**Read this first: this graph is ~10% real and ~90% generated.** It exists to test
whether a schema can support 104 analytical scans, not to make biological claims.
Nothing in it should be cited, and generated edges should not be treated as
hypotheses worth following up. What *is* trustworthy is the ~5,400 nodes and
~18,800 edges pulled from live public APIs, and every record tells you which it is.

> **Schema v2**, 53,375 nodes / 233,845 edges / 72 relationship types.
> Visual overview: [`schema-diagram.md`](schema-diagram.md).

---

## 1. What this is, in one paragraph

A Neo4j property graph of 53k nodes / 232k edges spanning genes, proteins,
variants, pathways, diseases, phenotypes, drugs and tissues, plus an evidence
layer (assertions, datasets, cohorts, publications) and a hypothesis-governance
layer (versioned hypotheses with confidence trajectories). Real seed entities were
fetched from 15 public databases and anchored on 8 disease domains; the remainder
was generated around those seeds to reach a size where graph algorithms behave
realistically.

---

## 2. Telling real from generated — do this before any analysis

Every node and edge carries these:

| Property | Meaning |
|---|---|
| `is_synthetic` | `false` = fetched from a live API; `true` = generated |
| `source` | `source_id` from `source_registry.yaml` (`hgnc`, `uniprotkb`, `gtex`…) |
| `source_url` | exact retrieval URL — the raw response is on disk under `data/seeds/` |
| `source_retrieved_at` | when the API was actually called |
| `synthesis_method` | how a generated record was produced; null iff `is_synthetic=false` |
| `synthesis_seed_uid` | the real node a generated one descends from |

There is also a visual tell, which matters more in practice because people read
identifiers rather than checking flags. **Real records keep their upstream
accession; generated ones live in a reserved `SKYGEN.*` namespace:**

```
Gene:HGNC:11998          real      Protein:P04637            real
Gene:SKYGEN.GENE:000042  generated Protein:SKYGEN.PROT:00042  generated
```

To restrict any analysis to real data:

```cypher
MATCH (g:Gene {is_synthetic: false})-[r {is_synthetic: false}]->(d:Disease)
RETURN g.symbol, type(r), d.name, r.edge_aggregate_score
ORDER BY r.edge_aggregate_score DESC LIMIT 25
```

### What the real subgraph actually contains

| Label | Real | Identifier system | Source |
|---|---|---|---|
| Gene | 92 | HGNC | HGNC + Ensembl |
| Protein | 90 | UniProt accession | UniProtKB (reviewed/Swiss-Prot only) |
| Variant | 770 | dbSNP rsID | GWAS Catalog |
| Pathway | 581 | Reactome stable ID | Reactome |
| Disease | 797 | MONDO | OLS4 + OpenTargets |
| Phenotype | 433 | HPO | HPO (via MONDO→OMIM) |
| Drug | 584 | ChEMBL | OpenTargets |
| Tissue | 63 | UBERON | GTEx + UBERON |
| CellType | 10 | Cell Ontology | OLS4 |
| Publication | 1,156 | PMID | PubMed E-utilities |

92 genes is small, deliberately: they are the anchor set (TP53, EGFR, APP, CFTR,
PCSK9…) spanning oncology, immunology, neurodegeneration, cardiometabolic, gene
therapy, rare disease, infectious disease and aging. Cross-domain genes (TP53,
CDKN2A) are intentional bridges — SCAN-27 and SEM-SCAN-04 have nothing to find in
a graph whose domains are disjoint.

---

## 3. Which real edges are biologically meaningful

These carry genuine upstream evidence and are worth looking at:

| Edge | Source | What the value means |
|---|---|---|
| `GENETICALLY_LINKS_TO` (Gene→Disease) | OpenTargets `genetic_association` channel | association score, not causality |
| `CAUSES` (Gene→Disease) | OpenTargets `somatic_mutation` channel | somatic evidence |
| `PHARMACOLOGICALLY_INHIBITS` / `_ACTIVATES` (Drug→Protein) | OpenTargets `mechanismsOfAction.actionType` | **real direction** — INHIBITOR/AGONIST, not inferred |
| `CLINICALLY_TREATS` (Drug→Disease) | OpenTargets indications | clinical-stage indication |
| `INTERACTS_WITH` (Gene→Gene) | STRING, `escore`/`dscore` > 0.3 | experimentally supported PPI |
| `PREDICTED_TO_INTERACT_WITH` | STRING, text-mining dominant | *predicted*, treat accordingly |
| `EXPRESSED_IN` (Gene→Tissue) | GTEx v8 median TPM ≥ 1.0 | measured expression, UBERON-keyed |
| `eQTL_MODULATES` (Variant→Gene) | GWAS Catalog | variant mapped to gene locus |
| `GWAS_ASSOCIATED_WITH` (Variant→Trait) | GWAS Catalog | real p-values and betas |
| `CONSERVED_IN` (Gene→Species) | OpenTargets homologues | ortholog with % identity |
| `PARTICIPATES_IN` (Gene→Pathway) | Reactome | curated membership |

The STRING split is worth noting: experimental/database channels become
`INTERACTS_WITH`, text-mining-dominant become `PREDICTED_TO_INTERACT_WITH`. That
distinction is what makes the evidence tiering non-uniform.

---

## 4. The evidence model

Every edge carries a scored evidence bundle computed with the workbook's own
formulas — not sampled — so recomputation should reproduce the stored values.

```
SA                    source authority, decayed by age:  SA_base × 1/ln(age + e)
ES_edge               evidence-type lookup (experimental 1.0 … llm_inferred 0.2)
edge_aggregate_score  0.40·|effect| + 0.40·(−log10 adj_p, cap 10) + 0.20·log10(n+1)
edge_quality_tier     Tier 1 ≥0.85 | Tier 2 ≥0.60 | Tier 3 ≥0.40 | Tier 4 <0.40
direction             −1 inhibition / 0 neutral / +1 activation
pos_count, neg_count  supporting vs contradicting observations
concordance_flag      CONCORDANT | CONTEXT_DEPENDENT | CONTRADICTORY
recency               exp(−λ·years), λ scaled per biological domain
```

`source_type` follows the platform's authority hierarchy: `curated_ontology` 1.00,
`peer_reviewed` 0.85, `clinical_registry` 0.85, `user_uploaded` 0.75, `preprint`
0.60, `llm_inference` 0.30. Edges scoring ≤ 0.10 are dropped at generation, per
SCAN-01's minimum ingestion threshold.

**Distribution sanity.** Edge quality: Tier 2 (103,858) > Tier 3 (87,527) >
Tier 4 (31,102) > Tier 1 (11,358). Tier 1 being rarest is intentional and matches
how curated evidence actually distributes.

Directional concordance: CONCORDANT 200,933 (86%), CONTEXT_DEPENDENT 18,982 (8%),
CONTRADICTORY 13,930 (6%). Worth knowing that these three were *not* all populated
until a defect in `PRIM_E05` was fixed — see §9.1. If you are working from an
older build where every edge is CONCORDANT, that build predates the fix.

Mechanistic confidence across the 120 hypotheses: 36 Strong / 38 Moderate / 46
Weak, spanning −0.039 to 0.83 against an attainable ceiling of 0.85.

---

## 5. Provenance and the temporal model

Every node and edge carries an 18-field contract. The parts you'll care about:

```
uid, canonical_id, entity_type, synonyms, ontology_refs, species
created_at, updated_at, ingested_at, valid_from, valid_to
Dv_created, Tv_created, record_hash, schema_version, layer
```

Six of those (`entity_type`, `canonical_id`, `ontology_refs`, `synonyms`,
`species`, `created_at`) are mandated by `PRIM_N08 get_node_metadata`, not chosen
stylistically.

`valid_from` / `valid_to` are the temporal anchors: `valid_to IS NULL` means
currently valid. Nothing is closed during the initial load — the intent is that a
retracted edge is closed rather than deleted, so historical scan results stay
reproducible. `Dv` (data version) and `Tv` (topology version) let you ask what the
graph looked like at a given version.

`layer` is worth knowing: `doc_verbatim` = specified in the source schema document;
`extension` = added because a primitive required it. To see only what the source
document specifies:

```cypher
MATCH ()-[r {layer: 'doc_verbatim'}]->() RETURN type(r), count(*) ORDER BY count(*) DESC
```

---

## 6. Where this graph will mislead you

Read this section before drawing any topological conclusion.

### 6.1 Duplicate edges — resolved in v2, but relevant if you have older data

The source schema document defined 20 conflicting relationship pairs — synonyms
(`TREATS_INDICATION` / `CLINICALLY_TREATS`), inverse pairs (`EXPRESSED_IN` /
`EXPRESSES`) and one reversed direction. **All are resolved.** The active schema
has one name per fact and one stored direction per relationship.

You should not need to work around this. Two caveats:

- **If you are holding a graph built before the v2 migration**, it carries the
  duplicates: 15.7% more edges, 45 nodes with a different SCAN-02 network role,
  and 38 of the top 50 targets in a different order. Check
  `MATCH ()-[r]->() RETURN count(DISTINCT type(r))` — **72** means v2, 97 means v1.
- **Neo4j traverses relationships in either direction at no cost**, so where you
  might expect `(t:Tissue)-[:EXPRESSES]->(g:Gene)`, write
  `(t:Tissue)<-[:EXPRESSED_IN]-(g:Gene)` or just use an undirected pattern. The
  inverse edge was removed precisely because storing it doubled degree for no
  query benefit.

`validate/conflict_regression.py` fails the build if any retired relationship
reappears.

### 6.2 Tissue, Species and CellType are pathological hubs

| Label | Nodes | Mean degree |
|---|---|---|
| Tissue | 63 | **534** |
| Species | 6 | **392** |
| CellType | 10 | 316 |
| Gene | 6,000 | 23 |

Tissue mediates **1.96 million** gene–gene common-neighbour pairs. Two genes
"sharing a neighbour" because both are expressed in liver carries essentially no
signal. Six Species nodes connect nearly every gene via `CONSERVED_IN`, so any
path through them is meaningless.

**If you run link prediction, common-neighbour or shortest-path analysis, exclude
these three labels.** They are context annotations, not mechanism steps:

```cypher
MATCH p = shortestPath((a:Gene {symbol:'TP53'})-[*..4]-(b:Protein))
WHERE none(n IN nodes(p) WHERE n:Tissue OR n:Species OR n:CellType)
RETURN p LIMIT 5
```

### 6.3 There is no Gene→Protein edge in the source schema

The source document never connects Gene to Protein. An `ENCODES` edge was added
as an extension. Without it, ~39% of cognate gene/protein pairs have no path at
all, and the remaining ~61% connect only via ~3.4-hop detours like
`Gene→Tissue→Protein`, which means "co-occurs in a tissue", not "encodes".

Also note gene→protein is **many-to-one**: SMN1 and SMN2 both encode UniProt
Q16637. `Protein.encoded_by_symbols` carries the full list.

### 6.4 Statistical properties are plausible, not real

Generated variants use an allele-frequency/effect-size relationship (rare variants
get larger effects), and publication years are skewed recent with a tail to 2005.
These are *shaped* distributions, not resampled from real data. Do not use this
graph to estimate any population parameter.

### 6.5 Agriculture is absent

10 AG-SCAN sheets exist but plant entity types are excluded from MVP. No plant
data is present.

---

## 7. Getting started

```bash
docker exec -it skygenic-scans-neo4j cypher-shell -u neo4j -p skygenic-scans-dev
```

Browser at <http://localhost:7475>. Some queries to orient yourself:

**What real evidence links a gene to disease?**

```cypher
MATCH (g:Gene {symbol: 'TP53'})-[r]->(d:Disease)
WHERE r.is_synthetic = false
RETURN type(r) AS evidence_channel, d.name, d.biological_domain,
       r.edge_aggregate_score, r.edge_quality_tier
ORDER BY r.edge_aggregate_score DESC
```

**Drugs with a real, directional mechanism on a target**

```cypher
MATCH (dr:Drug)-[r]->(p:Protein {gene_symbol: 'EGFR'})
WHERE r.is_synthetic = false AND r.direction <> 0
RETURN dr.name, type(r), r.direction, dr.max_phase
ORDER BY dr.max_phase DESC
```

**Tissue expression breadth — is a gene context-universal or specific?**

```cypher
MATCH (g:Gene {is_synthetic: false})-[r:EXPRESSED_IN {is_synthetic: false}]->(t:Tissue)
RETURN g.symbol, count(t) AS tissues, round(avg(r.effect_size), 3) AS mean_signal
ORDER BY tissues DESC LIMIT 20
```

**Contradicted edges — where the literature disagrees**

```cypher
MATCH ()-[r]->() WHERE r.concordance_flag <> 'CONCORDANT'
RETURN r.concordance_flag, type(r), r.pos_count, r.neg_count, r.conflict_score
LIMIT 25
```

**Cross-species conservation**

```cypher
MATCH (g:Gene {is_synthetic: false})-[r:CONSERVED_IN {is_synthetic: false}]->(s:Species)
RETURN g.symbol, collect(s.common_name) AS conserved_in
LIMIT 20
```

**Hypothesis confidence trajectory (the temporal layer)**

```cypher
MATCH (h:SkygenicHypothesis)-[:HAS_VERSION]->(v:HypothesisVersion)
RETURN h.hypothesis_id, h.biological_domain,
       collect(v.MC ORDER BY v.Hv) AS mc_trajectory,
       h.current_MC
LIMIT 10
```

---

## 8. Graph algorithms

GDS 2026.06.0 is installed. Project a *mechanism-scoped* graph rather than the
whole thing — otherwise §6.1 and §6.2 will dominate your results:

```cypher
MATCH (a)-[r]->(b)
WHERE NOT a:Tissue     AND NOT b:Tissue
  AND NOT a:Species    AND NOT b:Species
  AND NOT a:CellType   AND NOT b:CellType
WITH gds.graph.project('mechanism', a, b) AS g
RETURN g.graphName, g.nodeCount, g.relationshipCount
```

Under v1 this projection also had to exclude nineteen duplicate relationship
types. It no longer does — the schema has one name per fact — so the only
exclusions left are the three context hubs from §6.2. Add
`AND NOT a:SkygenicHypothesis AND NOT b:SkygenicHypothesis` if you want a purely
biological projection: hypothesis membership bridges 4,177 gene–protein pairs
that have no biological relationship, though measured impact on betweenness is
small (3 of 50 top positions).

Exact betweenness on the full graph takes ~2.5 minutes; use `samplingSize` if
you're iterating.

---

## 9. Known defects found while validating

Documented in full in [`schema-gap-analysis.md`](schema-gap-analysis.md) and
[`decisions.md`](decisions.md). The ones that would affect your interpretation:

1. **`PRIM_E05` as specified is degenerate.** `count(consistent)/count(all)` is
   ≥0.50 by construction, so `CONTEXT_DEPENDENT` and `CONTRADICTORY` could never
   fire — a 5-vs-5 split scored as CONCORDANT. Implemented instead from the
   requirements doc's signed `DirectionScore = Σd_i/n`, whose worked example
   (7 activations vs 1 inhibition → 0.75) reproduces exactly.
2. **`PRIM_R06` cannot reach 1.0.** Attainable MC range is **[−0.15, 0.85]**, not
   [0,1] as the requirements doc states nor [−1,1] as the schema declared. Both
   worked examples reproduce (MC 0.6525→0.65, MR 0.1775→0.18), so the formula is
   right and the stated range is wrong. A UI showing "MC 0.85 / 1.00" implies
   headroom that does not exist.
3. **Domain scaling is applied in the wrong direction.** The table multiplies raw
   saturation by `saturation_multiplier`, but rare disease at 0.35× *lowers*
   saturation (0.226 → 0.083) — the opposite of its stated goal. As a divisor it
   gives 0.493, matching the documented intent.
4. **`PRIM_E04` has undefined caps.** Effect-size and sample-size normalisation
   bounds are unspecified; 2.0 and 10⁴ were chosen and documented.
5. **SCAN-04 is deprecated** in the requirements doc (a routing stub to SCAN-06)
   but still listed as active in the workbook index.
