# graph-data-gen — seed data

The ingestion code that used to live here has moved into
[`skygenic-agent-core`](skygenic-agent-core) as the **`ingestion` feature**:

| Was | Now |
|---|---|
| `app/integrations/providers/` | `features/ingestion/providers/` |
| `app/integrations/http.py` | `features/ingestion/providers/http.py` |
| `app/integrations/registry.py` | `features/ingestion/providers/source_registry.py` |
| `app/services/ingestion/fetch_all.py` | `features/ingestion/_internal/stages.py` + one activity per stage |
| `app/services/ingestion/check_clients.py` | `features/ingestion/_internal/source_report.py` + `probe_source_clients` |
| `app/configs/anchors.py` | `features/ingestion/_internal/anchors.py` |
| `app/constants.py` | `features/ingestion/settings.py` |
| `scripts/reproduce.sh` | `SeedIngestionWorkflow` (Temporal) |

It runs under Temporal now rather than as a script — see
`skygenic-agent-core/README.md`.

## What is still here

`data/seeds/` — the content-addressed response cache (one directory per source)
and the committed `seed_bundle.json`. It stays here rather than being copied into
the agent-core repo, which has no `data/` tree and no reason to carry 11 MB of
cached upstream responses in git.

Point the feature at it so a run reuses this warm cache instead of refetching:

```bash
INGESTION_SEED_DATA_DIR="$(pwd)/data/seeds"
```
