# LiveOption run protocol v1

Web and CLI submit the same `liveoption.run.v1` JSON/YAML body to `POST /v1/tasks`.
The server validates it, builds the requested replay config,
and launches the existing trading engine. Live execution mode, implementation
class, QFinZero endpoints and output paths are server-owned.

The three presets are Intraday (`intraday_spy_2025_q1`),
Overlay (`overlay_spy_qqq_2025_q1`), and Earnings (`earnings_2025`).
`GET /v1/tasks/presets` returns preset defaults in `items`, independent custom
categories in `custom_types`, and `supported_underlyings`. Each entry includes
the full per-stage `prompt_variables` catalog with meaning, source and example.
The web “New run” editor
uses that response; selecting a preset loads its dates, universe, strategy
parameters, risk and data policy. Users may change the supported fields and
write their own `context`. The context enters each family's model prompts.

The editor has four steps: scope, trading rules, agent instructions, and review.
Preset and custom-category drafts are kept separately while the editor is open.

Prompt variables are highlighted in both editable and read-only templates.
The prompt inspector separates System and User by call phase, with readable
text, field-data and raw JSON views. Raw JSON can be copied or downloaded.
New decision artifacts retain `_provenance` beside each call's exact prompt
strings: `version: 1`, `offset_encoding: unicode-codepoints`, and per-role
`start`/`end` spans with variable names and format specifications. This metadata
is captured during template rendering; it does not alter model input. Legacy
artifacts remain unchanged and show an explicit notice when field positions
were not recorded. Their missing provenance is never inferred from current
templates or coincidental matching values.

The response inspector separates model calls by phase. Complete JSON and a
single fenced JSON document are rendered as labeled fields and order tables;
long lists are paginated. Invalid JSON and mixed prose remain original text.
Empty lists, missing raw text, and structured-only legacy records are explicit.
The original per-call text and the complete saved response JSON remain available,
with an exact JSON download. Model proposals are not presented as confirmed fills.

Strategy analysis separates account P&L, gross option credits and the evaluator's
`total_pnl - overlay_income_total` proxy; the proxy is not labeled a measured
stock return. Greeks attribution uses a signed, zero-centred chart with a shared
linear scale and an explicitly labeled symmetric-log inspection mode. Its total
covers only recorded components, not a reconciliation to account net P&L. Missing
components remain missing, zero stays zero, and a detail table retains source
precision. The chart prefers the complete family block, falling back to the
behavioral summary only when no finite family components exist; sources are not
merged. Decision activity counts proposals separately from executed fills.

Overlay accepts an optional top-level `overlay_mode`:

| Value | Execution strategy | Behavior |
|---|---|---|
| `hedge` | `overlay_hedge` | Protective puts and put spreads; no calls |
| `covered_call` | `overlay_profit` | Covered calls and call spreads; no puts |
| `hybrid` | `overlay_hybrid` | Both existing sleeves in one account |

Omitting this field preserves existing Covered Call behavior. Non-overlay tasks
reject it. For example, use `preset_id: overlay_spy_qqq_2025_q1` with
`overlay_mode: hybrid` in the same spec submitted by Web or CLI.
Hybrid runs the two analyzer/executor pipelines sequentially on their existing
rebalance schedule, dividing the strategy's per-rebalance model budget equally.
It can use twice as many model calls. Each sleeve has independent option-side
management, separate auditable decisions, unique sequence numbers and spread
groups, and checkpointed state. Both retain the stock holdings and share the
account, execution services and account-level risk limits. Reports retain the
selected mode and effective strategy name.

Example YAML:

```yaml
protocol_version: liveoption.run.v1
preset_id: intraday_spy_2025_q1
title: SPY five-minute liquidity test
model_id: gemini-3-flash
context: >-
  Prefer liquid calls. Hold cash when price and volatility signals disagree.
experiment:
  start: "2025-01-06"
  end: "2025-01-06"
  initial_cash: 25000
  universe: [SPY]
strategy_params:
  decision_interval_minutes: 5
  max_positions: 2
  dte_min: 0
  dte_max: 1
risk:
  max_orders_per_day: 4
data_policy:
  news_enabled: true
```

## Custom tasks

Use `mode: custom` and `cadence: intraday` or `daily`, without `preset_id`.
Custom tasks start with their own cash-only account, generic prompt examples,
and ordinary risk defaults. They do not inherit preset stock positions or strategy
parameters. Daily tasks decide after each daily close and fill on the next daily
bar. Intraday tasks use minute bars and `decision_interval_minutes`.

```yaml
protocol_version: liveoption.run.v1
mode: custom
cadence: daily
model_id: gemini-3-flash
experiment:
  start: "2025-01-06"
  end: "2025-01-31"
  initial_cash: 100000
  universe: [QQQ, NVDA]
strategy_params:
  dte_min: 7
  dte_max: 30
  max_positions: 3
context: Hold cash when evidence conflicts.
execution:
  option_fee_per_contract: 0.65
  order_type_policy: market
```

`prompt_templates.system` and `.user` optionally replace the generic examples.
The User prompt must retain `{chain_sample_table}`. Only variables listed for
that category and stage are accepted. Changing strategy DTE bounds also changes
chain filtering unless explicit chain bounds are provided in `data_policy`.
Daily cadence does not accept a minute decision interval or before-close flattening.

The user-facing model ID points to a server-side model entry and API group.
`GET/PUT /v1/model-settings` manage groups, base URLs, provider protocols,
credentials and model names. `GET` never returns credentials; an empty key on
`PUT` retains the existing key. Keys are stored only on the run server in a
mode-0600 file beside the Web database (or `LIVEOPTION_MODEL_SETTINGS_PATH`).
The initial Gemini/OpenAI gateway groups read existing environment variables.

## CLI

The CLI uses the same public Web API as the browser. It accepts a Web access
token via `LIVEOPTION_ACCESS_TOKEN` or `--token-file`; avoid passing a token on
the command line. `--server` defaults to `LIVEOPTION_WEB_URL` or
`http://127.0.0.1:3001`.

```sh
python -m trading.cli task presets --server https://YOUR-DOMAIN --token-file /secure/token-file
python -m trading.cli task models --server https://YOUR-DOMAIN --token-file /secure/token-file
python -m trading.cli task submit --spec run.yaml --server https://YOUR-DOMAIN --token-file /secure/token-file
python -m trading.cli task status --run-id RUN_ID --server https://YOUR-DOMAIN --token-file /secure/token-file
python -m trading.cli task analysis --run-id RUN_ID --server https://YOUR-DOMAIN --token-file /secure/token-file
```

The corresponding API reads are `GET /v1/tasks/{run_id}` and
`GET /v1/tasks/{run_id}/analysis`. The analysis response (`liveoption.analysis.v1`)
contains run metadata, a portfolio performance block (compounded annual
return, Sharpe, equity drawdown, VaR/CVaR when observations suffice), and
generic, family-specific and behavioral evaluator blocks. The Web result page
reads that same response and exports JSON.

Current evaluator limitations are stated in the API response and Web report:
`win_rate` counts profitable days, `max_drawdown` is an absolute P&L amount,
some family metrics are fill-based proxies, and most behavioral metrics are
unmeasured (`null`). Proxies must not be presented as directly measured results.
The decision timeline, P&L chart, event history, family dashboard and run
comparison remain available in the Web interface.

Timeline selections use `?section=timeline&seq=N` and render the matching
inspector in place, including sequence zero. Legacy `/decisions/N` links remain
supported. The inspector's full-record action opens `?section=history&seq=N`,
where saved prompt and response viewers are available. Selection follows refresh
and browser back/forward navigation; unknown sequences show an explicit message.
