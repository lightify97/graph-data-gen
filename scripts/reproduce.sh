#!/usr/bin/env bash
#
# Rebuilds the entire graph and all findings from scratch.
#
#   ./scripts/reproduce.sh              # uses the committed seed bundle (fast)
#   ./scripts/reproduce.sh --refetch    # re-pulls from all 15 public APIs (~37 min)
#   ./scripts/reproduce.sh --smoke      # ~3k-node graph, whole run in ~2 min
#
# Deterministic: the generator is seeded (synth/config.py RANDOM_SEED), so the
# same seed bundle always produces byte-identical nodes.jsonl / edges.jsonl.

set -euo pipefail
cd "$(dirname "$0")/.."

REFETCH=0
BUILD_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --refetch) REFETCH=1 ;;
    --smoke)   BUILD_ARGS+=(--smoke) ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

PY=./.venv/bin/python
step() { printf '\n\033[1m=== %s ===\033[0m\n' "$1"; }

# --- 0. toolchain ------------------------------------------------------------
step "0/6  environment"
command -v docker >/dev/null || { echo "docker not found" >&2; exit 1; }
if [ ! -x "$PY" ]; then
  echo "creating venv…"
  python3 -m venv .venv
  ./.venv/bin/pip install -q -e ".[dev]"
fi
$PY -c "import neo4j, httpx, yaml, networkx" || { echo "deps missing" >&2; exit 1; }
echo "ok: $($PY --version), $(docker --version | cut -d, -f1)"

# --- 1. seeds ----------------------------------------------------------------
step "1/6  real biology seeds"
if [ "$REFETCH" = "1" ] || [ ! -f data/seeds/seed_bundle.json ]; then
  echo "fetching from 15 public APIs — expect ~37 min on a cold cache."
  echo "responses are cached under data/seeds/<source>/, so this is resumable."
  $PY -m skygenic_scans.sources.fetch_all
else
  echo "using committed data/seeds/seed_bundle.json ($(wc -c < data/seeds/seed_bundle.json | tr -d ' ') bytes)"
  echo "pass --refetch to re-pull from the live APIs."
fi

# --- 2. database -------------------------------------------------------------
step "2/6  neo4j"
# Recreating the volume, NOT wiping. DETACH DELETE over a few hundred thousand
# relationships is slow and can half-succeed; a half-cleared graph silently
# corrupts every measurement downstream. See docs/decisions.md ADR-006.
( cd infra && docker compose down -v >/dev/null 2>&1 || true; docker compose up -d )
printf 'waiting for healthy'
until [ "$(docker inspect --format='{{.State.Health.Status}}' skygenic-scans-neo4j 2>/dev/null)" = "healthy" ]; do
  printf '.'; sleep 5
done
echo " ok"
$PY -m skygenic_scans.graph.constraints

# --- 3. generate -------------------------------------------------------------
step "3/6  generate graph"
$PY -m skygenic_scans.synth.build "${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"}"

# --- 4. load -----------------------------------------------------------------
step "4/6  load into neo4j"
# The loader compares its row counts against manifest.json and exits non-zero on
# any mismatch. Do not remove that check: a partial load is worse than a failed
# one, because the validators will happily report confident numbers against it.
$PY -m skygenic_scans.graph.loader 2>&1 | grep -vE "Received notification|GqlStatusObject"

# --- 5/6. validate -----------------------------------------------------------
step "5/6  scan readiness"
$PY -m skygenic_scans.validate.scan_readiness

step "6/6  conflict + gap impact"
$PY -m skygenic_scans.validate.gap_impact
# Exact betweenness (PRIM_N02) on two projections — the slowest step, ~5 min at
# full scale.
$PY -m skygenic_scans.validate.conflict_regression
# Regenerate the schema diagrams so they can never drift from the schema.
$PY -m skygenic_scans.diagram

step "done"
cat <<'EOF'
Reports written to data/generated/:
  manifest.json          node/edge counts, provenance split, relationship coverage
  scan_readiness.json    per-scan verdict for all 94 in-scope scans
  conflict_regression.json  guard: no retired relationship has reappeared
  gap_impact.json        measured consequences of gaps G-01 and G-10

Neo4j browser: http://localhost:7475  (neo4j / skygenic-scans-dev)
EOF
