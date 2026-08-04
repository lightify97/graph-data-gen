# Skygenic Graph — schema diagrams

> **Generated** by `python -m skygenic_scans.diagram` from
> `schema/ontology.yaml`. Do not hand-edit — regenerate instead, so a
> schema change shows up as a diff rather than a quietly wrong picture.

Schema **v2** · 28 node labels · 72 relationship types · 107 endpoint pairs (19 extension) · 31 retired

Edge labels show live counts from the generated graph where available.
Dotted arrows are `extension`-layer relationships — required by a primitive
but absent from the source schema document.

---

## 1. Subsystem overview

The four biological clusters plus the two operational ones. Numbers are
edge volumes between subsystems.

```mermaid
flowchart TB
    molecular["<b>Molecular</b><br/>Gene  Protein  ProteinComplex  Variant"]:::molecular
    functional["<b>Functional</b><br/>BiologicalProcess  Pathway"]:::functional
    clinical["<b>Clinical</b><br/>Disease  Phenotype  Trait  Drug  Compound"]:::clinical
    context["<b>Context</b><br/>Tissue  CellType  Species  OntologyTerm"]:::context
    evidence["<b>Evidence & Provenance</b><br/>Evidence  Assertion  Dataset  Cohort  Publication"]:::evidence
    governance["<b>Hypothesis & Governance</b><br/>SkygenicHypothesis  HypothesisVersion  LifecycleState  ReasoningChain  ConfidenceScore  BiologicalState  ScanResult  AuditEvent"]:::governance

    molecular -->|89k| evidence
    clinical -->|57k| evidence
    molecular -->|46k| clinical
    functional -->|44k| evidence
    molecular -->|31k| functional
    molecular -->|29k| context
    context -->|19k| evidence
    governance -->|19k| evidence
    governance -->|18k| molecular
    governance -->|17k| clinical
    clinical -->|13k| molecular
    governance -->|12k| functional
    functional -->|11k| context
    clinical -->|10k| context
    functional -->|9k| clinical
    evidence -->|4k| context
    clinical -->|3k| functional
    clinical -->|634| governance
    context -->|136| molecular

    classDef molecular fill:#2563eb22,stroke:#2563eb,stroke-width:2px,color:#111;
    classDef functional fill:#05966922,stroke:#059669,stroke-width:2px,color:#111;
    classDef clinical fill:#dc262622,stroke:#dc2626,stroke-width:2px,color:#111;
    classDef context fill:#d9770622,stroke:#d97706,stroke-width:2px,color:#111;
    classDef evidence fill:#7c3aed22,stroke:#7c3aed,stroke-width:2px,color:#111;
    classDef governance fill:#0891b222,stroke:#0891b2,stroke-width:2px,color:#111;
```

---

## 2. Biological core

The mechanism graph the scans actually traverse: molecular entities,
functional units and clinical endpoints.

```mermaid
flowchart LR
    BiologicalProcess[BiologicalProcess]:::functional
    Compound[Compound]:::clinical
    Disease[Disease]:::clinical
    Drug[Drug]:::clinical
    Gene[Gene]:::molecular
    Pathway[Pathway]:::functional
    Phenotype[Phenotype]:::clinical
    Protein[Protein]:::molecular
    ProteinComplex[ProteinComplex]:::molecular
    Trait[Trait]:::clinical
    Variant[Variant]:::molecular

    Gene -->|PREDICTED_TO_INTERACT_WITH| Gene
    ProteinComplex -->|PREDICTED_TO_INTERACT_WITH| ProteinComplex
    Gene -->|UPREGULATES| Gene
    Gene -->|DOWNREGULATES| Gene
    Gene -->|INTERACTS_WITH| Gene
    Gene -->|ORTHOLOG_OF| Gene
    Protein -.->|PROTEIN_INTERACTS_WITH| Protein
    Gene -->|PARTICIPATES_IN| Pathway
    Protein -.->|PROTEIN_PARTICIPATES_IN| Pathway
    Compound -->|CAUSES| Phenotype
    Gene -->|CAUSES| Disease
    ProteinComplex -->|CAUSES| Disease
    BiologicalProcess -->|BIOMARKER_FOR| Disease
    Gene -->|BIOMARKER_FOR| Disease
    ProteinComplex -->|BIOMARKER_FOR| Phenotype
    Drug -->|CLINICALLY_TREATS| Disease
    Protein -->|INHIBITS| BiologicalProcess
    Compound -->|INHIBITS| Protein
    ProteinComplex -->|INHIBITS| Pathway
    Protein -->|ACTIVATES| BiologicalProcess
    Compound -->|ACTIVATES| Protein
    ProteinComplex -->|ACTIVATES| BiologicalProcess
    Gene -->|GENETICALLY_LINKS_TO| Disease
    Variant -->|eQTL_MODULATES| Gene
    Variant -->|pQTL_MODULATES| Protein
    Variant -->|mQTL_MODULATES| BiologicalProcess
    Variant -->|GWAS_ASSOCIATED_WITH| Trait
    Variant -->|ALTERS_DRUG_RESPONSE| Drug
    Variant -->|QTL_ALTERS| Phenotype
    Variant -->|MR_VALIDATES_RISK_FOR| Disease
    Variant -.->|MAPS_TO_LOCUS| Gene
    BiologicalProcess -->|CONSTITUTES_PATHWAY| Pathway
    Protein -->|CAUSALLY_DRIVES_DISEASE| Disease
    Protein -->|PREDICTS_OUTCOME_IN| Disease
    Gene -->|GENETICALLY_ASSOCIATED_WITH| Trait
    Gene -->|DRIVES_PHENOTYPE| Phenotype
    Gene -.->|ENCODES| Protein
    BiologicalProcess -->|ASSOCIATED_WITH| Disease
    Compound -->|OFF_TARGET_INTERACTION| Protein
    BiologicalProcess -->|DRIVES_DISEASE_PATHOLOGY| Disease
    Drug -->|PHARMACOLOGICALLY_ACTIVATES| Protein
    Drug -->|PHARMACOLOGICALLY_INHIBITS| Protein
    Drug -->|PHARMACOLOGICALLY_ACTIVATES| BiologicalProcess
    Drug -->|PHARMACOLOGICALLY_INHIBITS| BiologicalProcess
    BiologicalProcess -->|PRODUCES_CLINICAL_PHENOTYPE| Phenotype
    BiologicalProcess -->|TEMPORALLY_PRECEDES| BiologicalProcess
    Disease -->|MECHANISTICALLY_SIMILAR_TO| Disease
    Phenotype -->|DISTINGUISHES_COHORT| Disease
    Drug -->|EXERTS_OFF_TARGET_EFFECT| Protein
    Drug -->|AMELIORATES_TRAIT| Phenotype
    ProteinComplex -->|HAS_COMPONENT| Protein

    classDef molecular fill:#2563eb22,stroke:#2563eb,stroke-width:2px,color:#111;
    classDef functional fill:#05966922,stroke:#059669,stroke-width:2px,color:#111;
    classDef clinical fill:#dc262622,stroke:#dc2626,stroke-width:2px,color:#111;
    classDef context fill:#d9770622,stroke:#d97706,stroke-width:2px,color:#111;
    classDef evidence fill:#7c3aed22,stroke:#7c3aed,stroke-width:2px,color:#111;
    classDef governance fill:#0891b222,stroke:#0891b2,stroke-width:2px,color:#111;
```

---

## 3. Context and ontology

Anatomical, cellular and taxonomic context. **These are annotation, not
mechanism** — Tissue, Species and CellType are extreme hubs (mean degree
534, 392 and 316) and should be excluded from path-finding and
link-prediction projections.

```mermaid
flowchart LR
    BiologicalProcess[BiologicalProcess]:::functional
    CellType[CellType]:::context
    Compound[Compound]:::clinical
    Disease[Disease]:::clinical
    Drug[Drug]:::clinical
    Gene[Gene]:::molecular
    OntologyTerm[OntologyTerm]:::context
    Pathway[Pathway]:::functional
    Phenotype[Phenotype]:::clinical
    Protein[Protein]:::molecular
    ProteinComplex[ProteinComplex]:::molecular
    Species[Species]:::context
    Tissue[Tissue]:::context
    Trait[Trait]:::clinical
    Variant[Variant]:::molecular

    Gene -->|EXPRESSED_IN| Tissue
    Gene -->|PREDICTED_TO_INTERACT_WITH| Gene
    ProteinComplex -->|PREDICTED_TO_INTERACT_WITH| ProteinComplex
    Gene -->|UPREGULATES| Gene
    Gene -->|DOWNREGULATES| Gene
    Gene -->|INTERACTS_WITH| Gene
    Gene -->|ORTHOLOG_OF| Gene
    Protein -.->|PROTEIN_INTERACTS_WITH| Protein
    BiologicalProcess -->|OPERATES_WITHIN_CONTEXT| Tissue
    ProteinComplex -->|OPERATES_WITHIN_CONTEXT| Tissue
    Gene -->|PARTICIPATES_IN| Pathway
    Protein -.->|PROTEIN_PARTICIPATES_IN| Pathway
    Compound -->|CAUSES| Phenotype
    Gene -->|CAUSES| Disease
    ProteinComplex -->|CAUSES| Disease
    BiologicalProcess -->|BIOMARKER_FOR| Disease
    Gene -->|BIOMARKER_FOR| Disease
    ProteinComplex -->|BIOMARKER_FOR| Phenotype
    Drug -->|CLINICALLY_TREATS| Disease
    Protein -->|INHIBITS| BiologicalProcess
    Compound -->|INHIBITS| Protein
    ProteinComplex -->|INHIBITS| Pathway
    Protein -->|ACTIVATES| BiologicalProcess
    Compound -->|ACTIVATES| Protein
    ProteinComplex -->|ACTIVATES| BiologicalProcess
    Compound -->|HAS_CONTEXT| Tissue
    Gene -->|GENETICALLY_LINKS_TO| Disease
    Disease -->|LOCALIZES_PATHOLOGY_IN| Tissue
    Variant -->|eQTL_MODULATES| Gene
    Variant -->|pQTL_MODULATES| Protein
    Variant -->|mQTL_MODULATES| BiologicalProcess
    Variant -->|GWAS_ASSOCIATED_WITH| Trait
    Variant -->|ALTERS_DRUG_RESPONSE| Drug
    Variant -->|QTL_ALTERS| Phenotype
    Variant -->|MR_VALIDATES_RISK_FOR| Disease
    Variant -.->|MAPS_TO_LOCUS| Gene
    BiologicalProcess -->|CONSTITUTES_PATHWAY| Pathway
    Protein -->|CAUSALLY_DRIVES_DISEASE| Disease
    Protein -->|PREDICTS_OUTCOME_IN| Disease
    Gene -->|GENETICALLY_ASSOCIATED_WITH| Trait
    Gene -->|CATEGORIZES_GENE| OntologyTerm
    OntologyTerm -->|ONTOLOGICALLY_INCLUDES| OntologyTerm
    Gene -->|DRIVES_PHENOTYPE| Phenotype
    Gene -.->|ENCODES| Protein
    Protein -->|CLASSIFIES_PROTEIN| OntologyTerm
    BiologicalProcess -->|CONSERVED_IN| Species
    Gene -->|CONSERVED_IN| Species
    Tissue -->|CONSERVED_IN| Species
    ProteinComplex -->|CONSERVED_IN| Species
    BiologicalProcess -->|STANDARDIZED_BY_ONTOLOGY| OntologyTerm
    Disease -->|STANDARDIZED_BY_ONTOLOGY| OntologyTerm
    Drug -->|DISTRIBUTED_IN_CONTEXT| Tissue
    BiologicalProcess -->|ASSOCIATED_WITH| Disease
    Compound -->|OFF_TARGET_INTERACTION| Protein
    BiologicalProcess -->|DRIVES_DISEASE_PATHOLOGY| Disease
    Drug -->|PHARMACOLOGICALLY_ACTIVATES| Protein
    Drug -->|PHARMACOLOGICALLY_INHIBITS| Protein
    Drug -->|PHARMACOLOGICALLY_ACTIVATES| BiologicalProcess
    Drug -->|PHARMACOLOGICALLY_INHIBITS| BiologicalProcess
    BiologicalProcess -->|PRODUCES_CLINICAL_PHENOTYPE| Phenotype
    BiologicalProcess -->|TEMPORALLY_PRECEDES| BiologicalProcess
    Disease -->|MECHANISTICALLY_SIMILAR_TO| Disease
    Phenotype -->|DISTINGUISHES_COHORT| Disease
    Drug -->|EXERTS_OFF_TARGET_EFFECT| Protein
    Drug -->|AMELIORATES_TRAIT| Phenotype
    Phenotype -->|DEFINES_PHENOTYPE| OntologyTerm
    ProteinComplex -->|HAS_COMPONENT| Protein
    Tissue -->|CONTAINS_BIOMARKER| Protein
    Tissue -->|CONTEXTUALIZES_ANATOMY| OntologyTerm

    classDef molecular fill:#2563eb22,stroke:#2563eb,stroke-width:2px,color:#111;
    classDef functional fill:#05966922,stroke:#059669,stroke-width:2px,color:#111;
    classDef clinical fill:#dc262622,stroke:#dc2626,stroke-width:2px,color:#111;
    classDef context fill:#d9770622,stroke:#d97706,stroke-width:2px,color:#111;
    classDef evidence fill:#7c3aed22,stroke:#7c3aed,stroke-width:2px,color:#111;
    classDef governance fill:#0891b222,stroke:#0891b2,stroke-width:2px,color:#111;
```

---

## 4. Evidence, provenance and governance

The audit chain (`EVIDENCED_BY` → Publication, Assertion → Dataset →
Cohort) and the hypothesis lifecycle that the temporal scan layer operates on.

```mermaid
flowchart LR
    Assertion[Assertion]:::evidence
    AuditEvent[AuditEvent]:::governance
    BiologicalState[BiologicalState]:::governance
    Cohort[Cohort]:::evidence
    ConfidenceScore[ConfidenceScore]:::governance
    Dataset[Dataset]:::evidence
    Evidence[Evidence]:::evidence
    HypothesisVersion[HypothesisVersion]:::governance
    LifecycleState[LifecycleState]:::governance
    Publication[Publication]:::evidence
    ReasoningChain[ReasoningChain]:::governance
    ScanResult[ScanResult]:::governance
    SkygenicHypothesis[SkygenicHypothesis]:::governance

    SkygenicHypothesis -->|EVIDENCED_BY| Publication
    Evidence -.->|EVIDENCED_BY_ASSERTION| Assertion
    Assertion -.->|DERIVED_FROM_DATASET| Dataset
    ScanResult -.->|SCAN_RESULT_FOR| SkygenicHypothesis
    SkygenicHypothesis -->|SUPERSEDES_VERSION| HypothesisVersion
    SkygenicHypothesis -.->|HAS_VERSION| HypothesisVersion
    SkygenicHypothesis -->|SEMANTICALLY_OVERLAPS_WITH| SkygenicHypothesis
    SkygenicHypothesis -->|GOVERNED_BY_LIFECYCLE_STATE| LifecycleState
    SkygenicHypothesis -->|TRACED_THROUGH_PATHWAY| ReasoningChain
    SkygenicHypothesis -->|EVALUATED_BY_METRICS| ConfidenceScore
    Dataset -.->|MEASURED_IN_COHORT| Cohort
    SkygenicHypothesis -.->|SCOPED_TO_STATE| BiologicalState

    classDef molecular fill:#2563eb22,stroke:#2563eb,stroke-width:2px,color:#111;
    classDef functional fill:#05966922,stroke:#059669,stroke-width:2px,color:#111;
    classDef clinical fill:#dc262622,stroke:#dc2626,stroke-width:2px,color:#111;
    classDef context fill:#d9770622,stroke:#d97706,stroke-width:2px,color:#111;
    classDef evidence fill:#7c3aed22,stroke:#7c3aed,stroke-width:2px,color:#111;
    classDef governance fill:#0891b222,stroke:#0891b2,stroke-width:2px,color:#111;
```

---

## Why not a rendered image

The graph holds ~53k nodes and ~234k edges. No diagram of the *instance*
data is readable or useful — it renders as a hairball. What is worth
drawing is the **schema**: 28 labels and 72 relationship types, which is
exactly what these diagrams show, generated from the schema file so they
cannot drift.

For instance-level exploration use Neo4j Browser at <http://localhost:7475>
with a bounded query, e.g.:

```cypher
MATCH p = (g:Gene {symbol:'TP53'})-[r]-(n)
WHERE r.is_synthetic = false
RETURN p LIMIT 100
```
