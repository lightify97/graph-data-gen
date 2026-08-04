# Reproducing this build from scratch

```bash
./scripts/reproduce.sh
```

That is the whole thing. The rest of this document explains what it does, how long
each step takes, and the three failure modes that cost time during the original build.

---

## Prerequisites

| | |
|---|---|
| Docker | 29.x, with **≥ 6 GB** allocated to the VM |
| Python | 3.11+ (built on 3.14.5) |
| Network | Only for `--refetch`; the default path is offline |

The Docker memory figure matters. Neo4j is configured for 3 GB heap + 1 GB
pagecache, and GDS projections live off-heap on top of that. The original build
was configured for 4 GB + 2 GB against a 7.7 GB VM and the bulk load died with
`MemoryPoolOutOfMemoryError` part-way through.

---

## The three modes

```bash
./scripts/reproduce.sh              # committed seed bundle      ~12 min
./scripts/reproduce.sh --smoke      # ~3k-node graph             ~2 min
./scripts/reproduce.sh --refetch    # re-pull all 15 APIs        ~50 min
```

Use `--smoke` when changing the schema, loader or validators — it exercises every
code path at 1/20th the size. Use `--refetch` only when you actually want newer
upstream data; the committed bundle pins exactly which records the findings came from.

---

## Timings, measured

| Step | Full | Smoke | Notes |
|---|---|---|---|
| Fetch seeds | 37 min | — | Only with `--refetch`. Cached and resumable. |
| Recreate DB + constraints | ~40 s | ~40 s | 343 constraints/indexes |
| Generate graph | ~4 min | ~5 s | 53,393 nodes / 232,215 edges |
| Load into Neo4j | ~50 s | ~11 s | Self-verifying against the manifest |
| Scan readiness | ~30 s | ~10 s | 94 scans × capability checks |
| Gap impact | ~1 min | ~10 s | WCC + shortest paths |
| Conflict impact | ~5 min | ~20 s | **Slowest** — exact betweenness, twice |
| **Total** | **~12 min** | **~75 s** | measured end-to-end via `reproduce.sh` |

Conflict impact dominates because `PRIM_N02` is specified as exact betweenness,
which is O(V·E), and it runs on two projections. The primitive's own note to
*"cache aggressively per graph_scope + Tv"* suggests the spec authors knew. If you
need it faster, GDS `betweenness` accepts a `samplingSize`.

---

## What is committed vs regenerated

**Committed** — small, and pins the findings:

- `data/seeds/seed_bundle.json` (9.4 MB) — the normalised upstream records
- `data/generated/*.json` — manifest and the four reports
- `schema/ontology.yaml`, `schema/scans.json`

**Regenerated, gitignored** — large and derivable:

- `data/seeds/<source>/` — 2,979 raw API responses, 54 MB
- `data/generated/nodes.jsonl`, `edges.jsonl` — 389 MB

The raw response cache is worth keeping locally even though it is ignored: it is
what makes every `source_url` and `source_retrieved_at` claim in the graph
checkable against the actual bytes the API returned.

---

## Determinism — two levels

`synth/config.py` sets `RANDOM_SEED = 20260801`, so all *structural* choices are
fixed. But by default the build is **content-stable, not byte-stable**: the
contract requires `created_at`, `updated_at`, `ingested_at`, `valid_from` and
`promotion_ts` on every record, and those read the wall clock. Two runs minutes
apart produce identical counts and identical topology with different bytes.

### Content stability (default)

Re-running always yields the same shape. Verify with counts, not checksums:

```bash
./.venv/bin/python -c "import json; m=json.load(open('data/generated/manifest.json')); print(m['totals']); print(m['relationship_coverage'])"
```

Expected every time: `53,393` nodes, `232,215` edges, `97` edge types,
`124 / 124` endpoint pairs.

### Byte stability (pin the clock)

Pass `--now`, or set `SKYGENIC_BUILD_NOW`, to make the output reproducible to the
byte — useful for diffing two builds, or proving a refactor changed nothing:

```bash
./.venv/bin/python -m skygenic_scans.synth.build --now 2026-08-01T00:00:00Z
shasum data/generated/nodes.jsonl data/generated/edges.jsonl
```

Two runs with the same pinned instant produce identical hashes. Verified.

> An earlier draft of this document claimed byte-identical output from the
> default path and offered a bare `shasum` as the check. That was wrong — the
> check would have failed every time and looked like a corrupted build. `--now`
> was added so the claim can be true when you need it to be.

### What legitimately breaks reproducibility

- `--refetch` — upstream databases change; that is the point of refetching.
- Changing `RANDOM_SEED` or any `ScaleConfig` field.

Note that `record_hash` deliberately excludes `updated_at`, so re-loading
unchanged upstream data is a genuine no-op rather than churning every hash. That
is a separate mechanism from build determinism and is unaffected by the clock.

---

## Three things that cost time originally

### 1. A partial load reports confident wrong numbers

The worst failure of the build. A killed load left 546k relationships against a
232k manifest, and every validator ran happily against the mix and produced
plausible findings that were wrong — including a G-01 conclusion that reversed
once the graph was clean.

The loader now compares its row counts against `manifest.json` and exits non-zero
with *"Do NOT run the validators against this graph"*. **Do not remove that check.**
A load that fails loudly costs ten minutes; one that fails quietly costs a day and
can put a wrong conclusion in a document.

### 2. Wipe by recreating the volume, not `DETACH DELETE`

`--wipe` exists on the loader but is unreliable above a few hundred thousand
relationships: `DETACH DELETE` on a high-degree node pulls all of its
relationships into one transaction, and a tight per-transaction cap makes it
impossible rather than slower. `apoc.periodic.iterate` is also deprecated and was
observed returning success while leaving 40,000 nodes behind.

`reproduce.sh` uses `docker compose down -v`. It is faster and cannot half-succeed.

### 3. The edge budget is load-bearing

`ScaleConfig.target_edges` caps the graph at 250k. Without it the per-family
degrees compound: they apply per `(type, from_label, to_label)` endpoint pair, and
five distinct `Gene→Gene` types at degree 4.5 over 6,000 genes is 135k edges from
that one shape alone. The first build overshot to 691k, which also pushed exact
betweenness out of reach.

---

## Verifying a rebuild matches

```bash
./.venv/bin/python -m pytest tests/ -q
```

79 tests, including one that reproduces the requirements doc's worked SCAN-01
example (Metformin/AMPK → 0.8925) exactly. Then check the headline figures:

```bash
./.venv/bin/python -c "import json; m=json.load(open('data/generated/manifest.json')); print(m['totals'], m['relationship_coverage']['built_endpoint_pairs'], '/', m['relationship_coverage']['declared_endpoint_pairs'])"
```

Expected: `53,393 nodes / 232,215 edges`, `124 / 124` endpoint pairs, and
`75 READY / 0 BLOCKED / 19 UNSUPPORTED` from `scan_readiness.json`.
