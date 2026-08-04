"""Anchor entities the real-data fetch is seeded from.

These are not arbitrary. Each domain in 00_Domain_Scaling_Table (minus
agriculture, excluded per source_registry) gets a set of genes, diseases and
drugs that are genuinely central to that field, so the fetched subgraph has the
topology a real one would: dense well-studied hubs, sparse periphery, and
cross-domain genes (TP53, CDKN2A) that legitimately bridge two domains.

That cross-domain overlap is load-bearing. SCAN-27 (Indication Similarity) and
SEM-SCAN-04 (Cross-Domain Mechanism Translation) have nothing to find in a graph
whose domains are disjoint.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DomainAnchor:
    domain: str
    diseases: tuple[tuple[str, str], ...]   # (MONDO curie, label)
    genes: tuple[str, ...]                  # HGNC symbols
    drugs: tuple[str, ...]                  # ChEMBL-resolvable names


DOMAIN_ANCHORS: tuple[DomainAnchor, ...] = (
    DomainAnchor(
        domain="oncology",
        diseases=(
            ("MONDO:0005233", "non-small cell lung carcinoma"),
            ("MONDO:0007254", "breast carcinoma"),
            ("MONDO:0005575", "colorectal cancer"),
            ("MONDO:0018875", "Li-Fraumeni syndrome"),
        ),
        genes=("TP53", "EGFR", "KRAS", "ERBB2", "ALK", "BRAF", "PIK3CA", "MYC",
               "PTEN", "RB1", "BRCA1", "BRCA2", "MET", "CDKN2A", "VHL", "APC"),
        drugs=("imatinib", "trastuzumab", "osimertinib", "pembrolizumab",
               "erlotinib", "olaparib"),
    ),
    DomainAnchor(
        domain="immunology",
        diseases=(
            ("MONDO:0008383", "rheumatoid arthritis"),
            ("MONDO:0005011", "Crohn disease"),
            ("MONDO:0007915", "systemic lupus erythematosus"),
        ),
        genes=("TNF", "IL6", "IL17A", "JAK1", "JAK2", "STAT3", "PTPN22",
               "NOD2", "IL23R", "CTLA4", "PDCD1", "IL10", "TYK2", "IRF5"),
        drugs=("adalimumab", "tofacitinib", "methotrexate", "ustekinumab",
               "baricitinib"),
    ),
    DomainAnchor(
        domain="neurodegeneration",
        diseases=(
            ("MONDO:0004975", "Alzheimer disease"),
            ("MONDO:0005180", "Parkinson disease"),
            ("MONDO:0004976", "amyotrophic lateral sclerosis"),
            ("MONDO:0007739", "Huntington disease"),
        ),
        genes=("APP", "PSEN1", "PSEN2", "APOE", "MAPT", "TREM2", "SNCA",
               "LRRK2", "GBA1", "SOD1", "TARDBP", "C9orf72", "HTT", "BACE1"),
        drugs=("donepezil", "memantine", "levodopa", "riluzole", "lecanemab"),
    ),
    DomainAnchor(
        domain="cardiometabolic",
        diseases=(
            ("MONDO:0005148", "type 2 diabetes mellitus"),
            ("MONDO:0007750", "familial hypercholesterolemia"),
            ("MONDO:0005010", "coronary artery disease"),
        ),
        genes=("PCSK9", "LDLR", "APOB", "TCF7L2", "PPARG", "SLC30A8",
               "KCNJ11", "INS", "LPA", "APOA5", "INSR", "IRS1", "GCK"),
        drugs=("metformin", "atorvastatin", "evolocumab", "empagliflozin",
               "semaglutide"),
    ),
    DomainAnchor(
        domain="gene_therapy",
        diseases=(
            ("MONDO:0001516", "spinal muscular atrophy"),
            ("MONDO:0010602", "hemophilia A"),
            ("MONDO:0011382", "sickle cell anemia"),
        ),
        genes=("SMN1", "SMN2", "F8", "F9", "HBB", "RPE65", "BCL11A", "CD19"),
        drugs=("nusinersen", "onasemnogene abeparvovec", "risdiplam"),
    ),
    DomainAnchor(
        domain="rare_disease",
        diseases=(
            ("MONDO:0009061", "cystic fibrosis"),
            ("MONDO:0010679", "Duchenne muscular dystrophy"),
            ("MONDO:0007947", "Marfan syndrome"),
        ),
        genes=("CFTR", "DMD", "FBN1", "NF1", "TSC1", "TSC2", "ATM", "SMPD1"),
        drugs=("ivacaftor", "elexacaftor", "ataluren"),
    ),
    DomainAnchor(
        domain="infectious_disease",
        diseases=(
            ("MONDO:0100096", "COVID-19"),
            ("MONDO:0018076", "tuberculosis"),
            ("MONDO:0005109", "HIV infectious disease"),
        ),
        genes=("ACE2", "TMPRSS2", "IFITM3", "CCR5", "TLR4", "NLRP3",
               "IFNAR2", "OAS1", "CXCL8"),
        drugs=("remdesivir", "nirmatrelvir", "isoniazid", "dolutegravir"),
    ),
    DomainAnchor(
        domain="aging",
        diseases=(
            ("MONDO:0008310", "Hutchinson-Gilford progeria syndrome"),
            ("MONDO:0005298", "age-related macular degeneration"),
            ("MONDO:0005147", "type 1 diabetes mellitus"),
        ),
        genes=("LMNA", "TERT", "TERC", "SIRT1", "FOXO3", "KL", "CDKN1A",
               "MTOR", "IGF1R", "ATG7"),
        drugs=("sirolimus", "rapamycin", "metformin", "dasatinib"),
    ),
)

# Tissues (UBERON) and cell types (CL) the expression / context layer is built on.
ANCHOR_TISSUES: tuple[tuple[str, str], ...] = (
    ("UBERON:0000955", "brain"),
    ("UBERON:0002107", "liver"),
    ("UBERON:0000948", "heart"),
    ("UBERON:0002048", "lung"),
    ("UBERON:0002113", "kidney"),
    ("UBERON:0001155", "colon"),
    ("UBERON:0000178", "blood"),
    ("UBERON:0001134", "skeletal muscle tissue"),
    ("UBERON:0002106", "spleen"),
    ("UBERON:0002370", "thymus"),
    ("UBERON:0000945", "stomach"),
    ("UBERON:0001264", "pancreas"),
)

ANCHOR_CELL_TYPES: tuple[tuple[str, str], ...] = (
    ("CL:0000235", "macrophage"),
    ("CL:0000084", "T cell"),
    ("CL:0000236", "B cell"),
    ("CL:0000129", "microglial cell"),
    ("CL:0000540", "neuron"),
    ("CL:0000127", "astrocyte"),
    ("CL:0000182", "hepatocyte"),
    ("CL:0000746", "cardiac muscle cell"),
    ("CL:0000623", "natural killer cell"),
    ("CL:0000775", "neutrophil"),
)

# Cross-species set for SCAN-12 / SCAN-16.
ANCHOR_SPECIES: tuple[tuple[int, str, str, bool], ...] = (
    (9606, "Homo sapiens", "human", False),
    (10090, "Mus musculus", "mouse", True),
    (10116, "Rattus norvegicus", "rat", True),
    (7955, "Danio rerio", "zebrafish", True),
    (7227, "Drosophila melanogaster", "fruit fly", True),
    (6239, "Caenorhabditis elegans", "roundworm", True),
)


def all_genes() -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for a in DOMAIN_ANCHORS:
        for g in a.genes:
            seen.setdefault(g, None)
    return tuple(seen)


def all_diseases() -> tuple[tuple[str, str, str], ...]:
    """(curie, label, domain)"""
    return tuple(
        (curie, label, a.domain) for a in DOMAIN_ANCHORS for curie, label in a.diseases
    )


def all_drugs() -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for a in DOMAIN_ANCHORS:
        for d in a.drugs:
            seen.setdefault(d, None)
    return tuple(seen)


def gene_domains() -> dict[str, tuple[str, ...]]:
    """Symbol -> domains it anchors. Multi-domain genes are the intended bridges."""
    out: dict[str, list[str]] = {}
    for a in DOMAIN_ANCHORS:
        for g in a.genes:
            out.setdefault(g, []).append(a.domain)
    return {k: tuple(v) for k, v in out.items()}
