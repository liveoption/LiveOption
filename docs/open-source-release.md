# Setup and operations

## Local installation

Follow the [end-to-end setup guide](getting-started.md) ([中文](getting-started.zh-CN.md))
for QFinZero installation, API keys, data preparation and the first task.
The [README](../README.md) gives the shorter command sequence. The supported source checkout
contains `configs/`, `prompts/`, `frontend/`, `trading/` and `liveoption_web/`.
Keep these together. The CLI can also be invoked with `python -m trading.app`.

`liveoption setup` exclusively creates `.liveoption/runtime.env` with randomly
generated Web and internal API credentials, mode `0600`. Re-running it preserves
the same values. It does not alter provider credentials in `.env`.

The launcher reads generated local credentials, then `.env`, then environment
variables (highest priority). Without explicit overrides, its SQLite database
and runs live under `.liveoption/`. The low-level API's standalone settings are
separate; use the launcher for consistent local paths.

`liveoption doctor` checks the QFinZero hub, UPQ `/health` and PMB `/v1/health`,
using `QFZ_API_KEY` for service access. `--with-news` also checks ESP `/esp/health`.
Use `--symbol QZSP --date 2025-03-24 --with-news` to query sample bars, a chain
and news; `--frequency 1d` skips the stock minute query. These read-only checks
do not guarantee quote coverage or fills. Run the opt-in broker smoke from the
README before larger experiments.

## Web

```bash
uv run liveoption web
# Rebuild after source updates:
uv run liveoption web --build
# Build without running the servers:
uv run liveoption web --prepare-only
```

Default ports: Web `3001`, API `19720`, external QFinZero hub `19777`. Both
LiveOption servers bind to `127.0.0.1`. The launcher checks for occupied ports
and cleans up only the children it starts. It never starts/stops QFinZero or
another existing Web/API deployment. Submitted worker jobs may continue after
Ctrl-C; their logs and status are available after restarting the same instance.

For public hosting use a TLS reverse proxy, long unique access/internal tokens,
persistent storage and a process supervisor. Never expose the internal API
directly: browser and CLI clients go through the authenticated Web proxy.
Rotate the Web token to revoke sessions. Back up the database, run directories,
model settings and credentials together. No personal server accounts or SSH
configuration are required for the local quick start.

## Models and tasks

API settings store groups and models server-side. Group credentials are never
returned by `GET /api/model-settings`. Refresh `/models` when supported; some
providers require manual model entries. A listed model may not support the
selected API protocol. Configure your own provider and test a short task first.

Web and `liveoption task` use the same `liveoption.run.v1` JSON/YAML
schema and `/api/tasks` endpoints. A task owns its context, prompt templates,
execution settings and risk rules. The engine still enforces execution/risk
checks. All PMB execution in this release is simulated.

Task examples under `examples/tasks/` contain configuration only, not market
data or results. Replace `model_id` with a configured model registry ID. Custom
daily decision times use America/New_York; intraday intervals use minutes.

## Release checks

### QFinZero compatibility check, 2026-10-26

Verified against public QFinZero v1.0.2 source commit `2d8dda5`, with a freshly
compiled UPQ binary, its generated synthetic dataset and authentication required.
The isolated key belonged to a researcher with `market-data`, `events` and
`trading` scopes.

- Read-only checks: QZSP/ACME bars and option chains; ACME news and earnings.
- Broker smoke: new simulated account/session, discovered option, order and fill.
- Fresh LiveOption checkout: frozen Python/frontend dependency install, setup,
  doctor, frontend build and local Web/API startup.
- Authenticated Web/CLI: model discovery, one-day custom QZSP task, six decisions,
  status/logs/analysis/returns, price chart and HTML report export. The model was
  a local deterministic HTTP fixture; this check incurred no model API charges.

QFinZero was launched from source for this check. The Docker recipe in the
guide follows upstream Compose; a Docker image build was not executed here.
Commercial vendor downloads, arbitrary user datasets and every external model
provider are outside this compatibility check.

Run Python, terminal-client and frontend tests from the README. Test clean
installation in a fresh checkout, run `doctor` against QFinZero, and verify
unauthenticated Web API requests are rejected. Keep `.env`, `.liveoption/`,
output, data exports and generated reports out of commits. The source is MIT;
market-data access and model API use follow their providers' terms.

The Secret scan workflow checks the complete Git history with Gitleaks and
redacts its output. To run the same check locally with Gitleaks 8.30.1:

```bash
gitleaks git . --config .gitleaks.toml --log-opts="--all --full-history" --redact=100
```

The configuration permits only two exact fake test credentials in their
respective test files. Never add a real credential to the allowlist. The
optional `.githooks/pre-push` hook also blocks common secret patterns without
printing their values (`git config core.hooksPath .githooks`). Automated secret
scans cannot determine whether prose, screenshots or datasets are appropriate
for publication; review those separately.

Source changes do not modify historical run records. Removing files in a new
commit does not remove them from earlier Git history; history cleanup must be
handled separately before distributing a fully sanitized repository.
