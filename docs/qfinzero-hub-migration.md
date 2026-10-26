# QFinZero Unified Hub Migration (2026-07-07)

For current installation instructions, follow [the setup guide](getting-started.md)
([中文](getting-started.zh-CN.md)). This page records the earlier service migration.

LiveOption uses the standalone [QFinZero](https://github.com/CityU-MLO/qfinzero)
backend, which exposes its services through one unified hub.

## What changed upstream

QFinZero consolidated everything behind a single public port (**19777**):

| Path on hub | Service | Old standalone port |
|---|---|---|
| `/svc/upq/*` | UPQ — market data, option chains + Greeks, rates | 19703 (later 19350) |
| `/svc/pmb/*` | PMB — paper broker (`/v1/*` API) | 19710 / 19701 |
| `/svc/esp/*` | ESP — events & news (**formerly NPP**, `/esp/*` paths) | 19702 / 19330 |
| `/mcp` | QFinZero MCP server; not required by LiveOption | — |
| `/health` | Hub + all children status | — |

The default hub address used by LiveOption is `http://127.0.0.1:19777`.
Current public QFinZero releases require `QFZ_API_KEY`; LiveOption attaches it
as a Bearer token to UPQ, ESP and PMB requests. Service lifecycle and internal
ports belong to QFinZero.

```bash
curl http://127.0.0.1:19777/health
```

## What changed in LiveOption

### Single source of truth: `trading/core/endpoints.py`

All service-URL defaults resolve through one module, overridable by env:

| Env var | Default |
|---|---|
| `QFZ_HUB_URL` | `http://127.0.0.1:19777` |
| `QFZ_UPQ_URL` | `{hub}/svc/upq` |
| `QFZ_PMB_URL` | `{hub}/svc/pmb` |
| `QFZ_ESP_URL` | `{hub}/svc/esp` |

`configs/services.yaml` is the config-file counterpart (its values win over
code defaults via `ServicesConfig`, as before). `ServicesConfig` gained an
`esp` service key; `npp` is still accepted as a legacy alias.

### NPP → ESP

The news/events service was renamed upstream. Request/response schemas are
**unchanged** — only the path prefix moved from `/npp/*` to `/esp/*`:

- `trading/tools/npp_context.py` — `/esp/news/search`, `/esp/calendar/econ`, `/esp/calendar/earnings`
- `trading/tools/earnings_loader.py` — `/esp/calendar/earnings`, `/esp/news/search`
- `trading/case_runner.py::query_earnings_cases_from_npp` — `/esp/calendar/earnings`

### PMB execution

`PMBClient()` points at `{hub}/svc/pmb`. Parallel runs share the hub and remain
isolated through separate accounts and sessions. LiveOption no longer contains
the PMB server or starts one process per worker.

### Notes / follow-ups

- New UPQ supports on-read corporate-action adjustment on `/stock` and
  `/stock/daily` (`adjust=none|split|total`, default `none` = raw, same as
  before). LiveOption does not pass `adjust` yet — adopt where split handling
  is done manually.
- UPQ health moved from `/v1/health` to `/health` (`{hub}/svc/upq/health`).
- QFinZero also ships importable clients (`qfinzero.clients.{upq,pmb,esp}`).
