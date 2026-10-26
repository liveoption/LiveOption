# LiveOption

[中文安装指南](docs/getting-started.zh-CN.md) · [End-to-end setup guide](docs/getting-started.md)

An options replay studio with a Web interface and scriptable CLI.
Configure trading rules and prompts, run agents, inspect their
decisions and export reports. All interfaces share `liveoption.run.v1`.

Licensed under [MIT](LICENSE). This repository contains software and examples,
not published research materials or historical experiment results.

![LiveOption English Web interface: synthetic candles and Agent decision checkpoints](docs/images/web-guide/liveoption-run-chart.png)

[Follow the illustrated English setup guide](docs/getting-started.md) for
QFinZero data preparation, model settings, task creation and result inspection.
The screenshots use synthetic data and a scripted HOLD demo model.

## QFinZero is the backend

Start [QFinZero](https://github.com/CityU-MLO/qfinzero) separately. LiveOption
defaults to its hub at **`http://127.0.0.1:19777`**:

| Service | Used for |
| --- | --- |
| `/svc/upq` | Historical prices, option chains and Greeks |
| `/svc/pmb` | Simulated accounts, sessions, orders and fills |
| `/svc/esp` | News and earnings events |

Current public QFinZero releases require a **`QFZ_API_KEY`** with market-data
and simulated-trading access (plus events access for news/earnings). LiveOption
Web has its own separate access token. LiveOption does not bundle QFinZero,
redistribute market data or connect to a live-money brokerage.

## Quick start

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node 22.12+ (or
20.19+) and pnpm 11.25.0 (pinned in `frontend/package.json`). The operating system must support the QFinZero services you
choose to deploy.

Start QFinZero and create its API key as described in the
[complete setup guide](docs/getting-started.md#1-start-qfinzero). For your first
run, use its included synthetic data: `QZSP` on `2025-03-24`. The SPY/QQQ presets
require separately imported real market data.

```bash
git clone https://github.com/liveoption/LiveOption.git
cd LiveOption
uv sync --frozen --extra dev --extra web-dev
cp .env.example .env
# Edit .env: set QFZ_HUB_URL and QFZ_API_KEY from your QFinZero instance.
uv run liveoption setup
uv run liveoption doctor --symbol QZSP --date 2025-03-24 --with-news
uv run liveoption web
```

Open **http://127.0.0.1:3001**. Sign in with `LIVEOPTION_ACCESS_TOKEN` from
`.liveoption/runtime.env`; `setup` generates this private file once. Tokens are
not printed to logs. An explicit token in `.env` or the environment takes
precedence. Keep local credentials private.

On first launch, `web` installs the locked frontend dependencies and builds the
UI. Both servers listen on loopback. Ctrl-C stops this Web/API pair; submitted
jobs can continue in the background. Run `uv run liveoption web --build` after
updating frontend source. The browser supports English, 中文 and 日本語.

### Configure models

In **API settings**, add a group with its provider, base URL and API key, then
refresh `/models`. Select the group and model when creating a run. Keys remain
on the API server; task specs and browser responses do not contain them.

Alternatively, copy `.env.example` to `.env` and set the credentials for your
own OpenAI-compatible or Anthropic-compatible endpoint before starting Web.
Do not commit `.env`. Model availability depends on the configured provider.

### Start a task

Choose a preset: **Intraday**, **Overlay** (hedge, covered call or hybrid), or
**Earnings**. Custom tasks support intraday intervals or a daily ET decision
time. Configure dates, assets, fees, order policy, risk rules, news, context and
system/user prompts. Variable documentation and previews are available in the
editor. Verify the requested dates/assets are covered by your QFinZero data.

With the bundled QFinZero sample, choose **Custom task → Intraday**, set
`QZSP` and `2025-03-24` for both dates, and start with a 60-minute interval.
See [the walkthrough](docs/getting-started.md#4-start-web-and-run-a-short-task)
and [matching CLI spec](examples/tasks/qfinzero-sample.yaml).

Run results include price/return charts, decision history, saved prompts and
model outputs, execution logs, attribution and downloadable reports. Missing
metrics remain unavailable; proposals do not imply executed fills.

## CLI

With Web running, use the same account and task history from another terminal:

```bash
uv run liveoption task --help
uv run liveoption task presets
uv run liveoption task models
uv run liveoption task list
uv run liveoption task submit --spec examples/tasks/intraday.yaml
uv run liveoption task status --run-id RUN_ID
uv run liveoption task logs --run-id RUN_ID
uv run liveoption task analysis --run-id RUN_ID
uv run liveoption task report --run-id RUN_ID --format html --output report.html
```

Edit `model_id` in the example to one returned by `task models`. Review the
YAML/JSON task before submitting: `task submit` starts the job immediately and
may incur model API charges. The spec controls prompts and trading rules.
Report export never overwrites an existing file.

For a remote instance set `LIVEOPTION_WEB_URL` and `LIVEOPTION_ACCESS_TOKEN`,
or use `--server` and `--token-file`. `python -m trading.cli task ...` remains
supported. Low-level engine commands are available through
`python -m trading.cli --help`.

## Configuration and local files

- `QFZ_HUB_URL`: defaults to `http://127.0.0.1:19777`.
- `QFZ_API_KEY`: QFinZero API key; sent only to its market/event/broker services.
- `QFZ_UPQ_URL`, `QFZ_PMB_URL`, `QFZ_ESP_URL`: optional service overrides.
- `.env`: optional operator/provider settings, never committed.
- `.liveoption/`: generated local credentials, database, model groups and runs;
  ignored by Git. Deleting it destroys your local history and credentials.
- `uv run liveoption web --port 3002 --api-port 19722`: alternate local ports.
- `--skip-check`: offline UI work only; it does not disable authentication or
  make trading possible without market/broker services.

## Tests

```bash
uv run pytest tests liveoption_web/tests
cd frontend
pnpm install --frozen-lockfile
pnpm orval
pnpm typecheck
pnpm test
pnpm exec playwright install chromium
pnpm test:e2e
pnpm build
```

Browser tests use isolated API fixtures and test-only authentication. Normal
`liveoption web` startup removes these authentication-bypass flags. Real
QFinZero integration checks require a running hub and your own historical data.
Model calls may incur charges; ordinary unit/browser tests require no paid API.

For an opt-in integration smoke on a real hub (creates a simulated account and
places one simulated option order; no model calls):

```bash
LIVEOPTION_TEST_QFINZERO=1 uv run --env-file .env pytest tests/trading/test_qfinzero_integration.py
```

This test defaults to QFinZero's synthetic `QZSP` data on `2025-03-24`.
Set `LIVEOPTION_TEST_SYMBOL` and `LIVEOPTION_TEST_DATE` for your own covered
market data. Coverage failures and missing fills fail the test.

See [setup and operations](docs/open-source-release.md) and the
[task protocol](docs/RUN_PROTOCOL.md).
