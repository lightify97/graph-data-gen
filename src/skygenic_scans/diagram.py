"""Generates Mermaid schema diagrams directly from schema/ontology.yaml.

Run:  python -m skygenic_scans.diagram

Generated, never drawn by hand. A hand-drawn schema diagram is wrong the moment
the schema changes and nobody notices — this one regenerates, and a stale diagram
becomes a diff rather than a quiet inaccuracy.

Emits three views into docs/schema-diagram.md, because one diagram of 28 labels
and 107 relationships is unreadable:

  1. Cluster overview   — the four subsystems and how they connect
  2. Biological core    — the mechanism graph the scans traverse
  3. Evidence + governance — provenance chain and hypothesis lifecycle

Live edge counts are pulled from data/generated/manifest.json when present, so
the diagram shows which relationships actually carry volume.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schema" / "ontology.yaml"
MANIFEST = ROOT / "data" / "generated" / "manifest.json"
OUT = ROOT / "docs" / "schema-diagram.md"

# Which subsystem each label belongs to. Drives both clustering and colour.
CLUSTERS: dict[str, tuple[str, list[str]]] = {
    "molecular": ("Molecular", ["Gene", "Protein", "ProteinComplex", "Variant"]),
    "functional": ("Functional", ["BiologicalProcess", "Pathway"]),
    "clinical": ("Clinical", ["Disease", "Phenotype", "Trait", "Drug", "Compound"]),
    "context": ("Context", ["Tissue", "CellType", "Species", "OntologyTerm"]),
    "evidence": ("Evidence & Provenance",
                 ["Evidence", "Assertion", "Dataset", "Cohort", "Publication"]),
    "governance": ("Hypothesis & Governance",
                   ["SkygenicHypothesis", "HypothesisVersion", "LifecycleState",
                    "ReasoningChain", "ConfidenceScore", "BiologicalState",
                    "ScanResult", "AuditEvent"]),
}
COLOURS = {
    "molecular": "#2563eb", "functional": "#059669", "clinical": "#dc2626",
    "context": "#d97706", "evidence": "#7c3aed", "governance": "#0891b2",
}


def cluster_of(label: str) -> str:
    for key, (_name, labels) in CLUSTERS.items():
        if label in labels:
            return key
    return "other"


def load() -> tuple[dict, dict[str, int]]:
    schema = yaml.safe_load(SCHEMA.read_text())
    counts: dict[str, int] = {}
    if MANIFEST.exists():
        counts = json.loads(MANIFEST.read_text()).get("edges_by_type", {})
    return schema, counts


def active_edges(schema: dict) -> list[dict]:
    out = []
    for r in schema["relationships"]:
        if r.get("status") == "retired":
            continue
        for t in [r["to"], *(r.get("also_to") or [])]:
            out.append({**r, "to": t})
    return out


def _style_lines() -> list[str]:
    return [f"    classDef {k} fill:{v}22,stroke:{v},stroke-width:2px,color:#111;"
            for k, v in COLOURS.items()]


def cluster_overview(schema: dict, counts: dict[str, int]) -> str:
    edges = active_edges(schema)
    between: dict[tuple[str, str], int] = defaultdict(int)
    for e in edges:
        a, b = cluster_of(e["from"]), cluster_of(e["to"])
        if a != b:
            between[(a, b)] += counts.get(e["type"], 0)

    lines = ["flowchart TB"]
    for key, (name, labels) in CLUSTERS.items():
        present = [l for l in labels if l in schema["nodes"]]
        lines.append(f'    {key}["<b>{name}</b><br/>{"  ".join(present)}"]:::{key}')
    lines.append("")
    seen: set[tuple[str, str]] = set()
    for (a, b), n in sorted(between.items(), key=lambda kv: -kv[1]):
        if (a, b) in seen or a == "other" or b == "other":
            continue
        seen.add((a, b))
        label = f"{n/1000:.0f}k" if n >= 1000 else (str(n) if n else "")
        lines.append(f"    {a} -->|{label}| {b}" if label else f"    {a} --> {b}")
    lines += [""] + _style_lines()
    return "\n".join(lines)


def cluster_detail(schema: dict, counts: dict[str, int], keys: list[str],
                   min_count: int = 0) -> str:
    labels = {l for k in keys for l in CLUSTERS[k][1] if l in schema["nodes"]}
    edges = [e for e in active_edges(schema)
             if e["from"] in labels and e["to"] in labels]

    lines = ["flowchart LR"]
    for l in sorted(labels):
        lines.append(f"    {l}[{l}]:::{cluster_of(l)}")
    lines.append("")

    drawn: set[tuple[str, str, str]] = set()
    for e in sorted(edges, key=lambda x: -counts.get(x["type"], 0)):
        key = (e["from"], e["type"], e["to"])
        if key in drawn:
            continue
        n = counts.get(e["type"], 0)
        if n < min_count:
            continue
        drawn.add(key)
        arrow = "-.->" if e.get("layer") == "extension" else "-->"
        lines.append(f"    {e['from']} {arrow}|{e['type']}| {e['to']}")
    lines += [""] + _style_lines()
    return "\n".join(lines)


def stats(schema: dict, counts: dict[str, int]) -> dict[str, Any]:
    edges = active_edges(schema)
    retired = [r for r in schema["relationships"] if r.get("status") == "retired"]
    return {
        "version": schema["meta"]["version"],
        "labels": len(schema["nodes"]),
        "endpoint_pairs": len(edges),
        "types": len({e["type"] for e in edges}),
        "retired": len(retired),
        "extension": len([e for e in edges if e.get("layer") == "extension"]),
        "total_edges": sum(counts.values()),
    }


def build() -> str:
    schema, counts = load()
    s = stats(schema, counts)

    doc = [
        "# Skygenic Graph — schema diagrams",
        "",
        "> **Generated** by `python -m skygenic_scans.diagram` from",
        "> `schema/ontology.yaml`. Do not hand-edit — regenerate instead, so a",
        "> schema change shows up as a diff rather than a quietly wrong picture.",
        "",
        f"Schema **v{s['version']}** · {s['labels']} node labels · {s['types']} "
        f"relationship types · {s['endpoint_pairs']} endpoint pairs "
        f"({s['extension']} extension) · {s['retired']} retired",
        "",
        "Edge labels show live counts from the generated graph where available.",
        "Dotted arrows are `extension`-layer relationships — required by a primitive",
        "but absent from the source schema document.",
        "",
        "---",
        "",
        "## 1. Subsystem overview",
        "",
        "The four biological clusters plus the two operational ones. Numbers are",
        "edge volumes between subsystems.",
        "",
        "```mermaid",
        cluster_overview(schema, counts),
        "```",
        "",
        "---",
        "",
        "## 2. Biological core",
        "",
        "The mechanism graph the scans actually traverse: molecular entities,",
        "functional units and clinical endpoints.",
        "",
        "```mermaid",
        cluster_detail(schema, counts, ["molecular", "functional", "clinical"]),
        "```",
        "",
        "---",
        "",
        "## 3. Context and ontology",
        "",
        "Anatomical, cellular and taxonomic context. **These are annotation, not",
        "mechanism** — Tissue, Species and CellType are extreme hubs (mean degree",
        "534, 392 and 316) and should be excluded from path-finding and",
        "link-prediction projections.",
        "",
        "```mermaid",
        cluster_detail(schema, counts, ["molecular", "functional", "clinical", "context"],
                       min_count=1),
        "```",
        "",
        "---",
        "",
        "## 4. Evidence, provenance and governance",
        "",
        "The audit chain (`EVIDENCED_BY` → Publication, Assertion → Dataset →",
        "Cohort) and the hypothesis lifecycle that the temporal scan layer operates on.",
        "",
        "```mermaid",
        cluster_detail(schema, counts, ["evidence", "governance"]),
        "```",
        "",
        "---",
        "",
        "## Why not a rendered image",
        "",
        "The graph holds ~53k nodes and ~234k edges. No diagram of the *instance*",
        "data is readable or useful — it renders as a hairball. What is worth",
        "drawing is the **schema**: 28 labels and 72 relationship types, which is",
        "exactly what these diagrams show, generated from the schema file so they",
        "cannot drift.",
        "",
        "For instance-level exploration use Neo4j Browser at <http://localhost:7475>",
        "with a bounded query, e.g.:",
        "",
        "```cypher",
        "MATCH p = (g:Gene {symbol:'TP53'})-[r]-(n)",
        "WHERE r.is_synthetic = false",
        "RETURN p LIMIT 100",
        "```",
    ]
    return "\n".join(doc) + "\n"


def main() -> int:
    OUT.write_text(build())
    schema, counts = load()
    s = stats(schema, counts)
    print(f"wrote {OUT}")
    print(f"  schema v{s['version']}: {s['labels']} labels, {s['types']} types, "
          f"{s['endpoint_pairs']} endpoint pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
