"""Local setup, dependency checks and managed Web startup."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from zoneinfo import ZoneInfo

from trading.core.endpoints import qfz_headers

ROOT = Path(__file__).resolve().parents[1]


def setup(root: Path = ROOT) -> Path:
    """Create local credentials once; never modify provider keys or old state."""
    state = root / ".liveoption"
    state.mkdir(mode=0o700, exist_ok=True)
    target = state / "runtime.env"
    if not target.exists():
        values = {"LIVEOPTION_INTERNAL_SECRET": secrets.token_urlsafe(32),
                  "LIVEOPTION_ACCESS_TOKEN": secrets.token_urlsafe(32)}
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("# Generated local credentials. Do not commit or share.\n")
                for key, value in values.items():
                    handle.write(f"{key}={value}\n")
    print(f"Local credentials: {target} (values are not printed)")
    return target


def environment(root: Path = ROOT) -> dict[str, str]:
    from dotenv import dotenv_values
    state = root / ".liveoption"
    env = {"LIVEOPTION_DB_PATH": str(state / "web" / "liveoption.db"),
           "LIVEOPTION_RUNS_DIR": str(state / "runs"),
           "LIVEOPTION_MODEL_SETTINGS_PATH": str(state / "model-settings.json"),
           "QFZ_HUB_URL": "http://127.0.0.1:19777"}
    for source in (dotenv_values(state / "runtime.env"), dotenv_values(root / ".env"), os.environ):
        for key, value in source.items():
            if value is not None and (value or key not in {"LIVEOPTION_ACCESS_TOKEN", "LIVEOPTION_INTERNAL_SECRET"}):
                env[key] = value
    return env


def health(url: str, timeout: float = 5, headers: dict | None = None) -> bool:
    try:
        request = urllib.request.Request(url, headers=headers) if headers else url
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def doctor(env: dict[str, str], *, symbol: str | None = None,
           day: str | None = None, frequency: str = "1m", with_news: bool = False) -> bool:
    hub = env.get("QFZ_HUB_URL") or "http://127.0.0.1:19777"
    urls = {"QFinZero hub": hub.rstrip("/") + "/health",
            "UPQ market data": (env.get("QFZ_UPQ_URL") or hub.rstrip("/") + "/svc/upq").rstrip("/") + "/health",
            "PMB simulated broker": (env.get("QFZ_PMB_URL") or hub.rstrip("/") + "/svc/pmb").rstrip("/") + "/v1/health"}
    headers = qfz_headers(env.get("QFZ_API_KEY", ""))
    if with_news:
        urls["ESP news/events"] = (env.get("QFZ_ESP_URL") or hub.rstrip("/") + "/svc/esp").rstrip("/") + "/esp/health"
    passed = True
    for name, url in urls.items():
        ready = health(url, headers=headers)
        print(f"{'OK' if ready else 'FAIL'}  {name}")
        passed &= ready
    if not passed:
        print("Check QFZ_HUB_URL and QFZ_API_KEY (market-data/trading scopes; events for news).\n"
              "Start QFinZero separately: https://github.com/CityU-MLO/qfinzero\n"
              "See docs/getting-started.md for setup and troubleshooting.")
    if (symbol is None) != (day is None):
        raise ValueError("use --symbol and --date together")
    if symbol and day and passed:
        passed = check_data(env, symbol, day, frequency=frequency, with_news=with_news)
    elif passed and not symbol:
        print("Service access passed. Historical coverage is not checked; use --symbol TICKER --date YYYY-MM-DD.")
    return passed


def check_data(env: dict[str, str], symbol: str, day: str, *, frequency: str,
               with_news: bool) -> bool:
    """Read-only coverage checks; never create accounts, orders or model calls."""
    day = date.fromisoformat(day).isoformat()
    hub = (env.get("QFZ_HUB_URL") or "http://127.0.0.1:19777").rstrip("/")
    upq = (env.get("QFZ_UPQ_URL") or hub + "/svc/upq").rstrip("/")
    esp = (env.get("QFZ_ESP_URL") or hub + "/svc/esp").rstrip("/")
    start = datetime.fromisoformat(day + "T09:30:00").replace(tzinfo=ZoneInfo("America/New_York"))
    utc_start = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    utc_end = (start + timedelta(minutes=15)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    queries = [
        ("Stock daily bars", upq + "/stock/daily", {"tickers": symbol, "start": day, "end": day}, None),
        ("Option chain (0–60 DTE)", upq + "/option/chain_query",
         {"underlying": symbol, "date": day, "expiry_min": day,
          "expiry_max": (date.fromisoformat(day) + timedelta(days=60)).isoformat(), "include_greeks": "true"}, None),
    ]
    if frequency == "1m":
        queries.append(("Stock minute bars", upq + "/stock",
                        {"tickers": symbol, "start": utc_start, "end": utc_end, "limit": 100}, None))
    if with_news:
        queries.append(("ESP news query", esp + "/esp/news/search", None,
                        {"tickers": [symbol], "start_utc": (start - timedelta(days=7)).astimezone(timezone.utc).isoformat(),
                         "end_utc": utc_end, "limit": 10}))
    passed = True
    for label, url, params, body in queries:
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = qfz_headers(env.get("QFZ_API_KEY", ""))
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                value = json.load(response)
            rows = value.get("events") if body is not None and isinstance(value, dict) else value
            if not isinstance(rows, list):
                raise ValueError("unexpected response schema")
            if not rows:
                # A quiet news period is valid, but missing prices/chains is not.
                ready = body is not None
                print(f"{'WARN' if ready else 'FAIL'}  {label}: no records for {symbol} {day}")
            else:
                ready = True
                print(f"OK  {label}: {len(rows)} records")
        except urllib.error.HTTPError as exc:
            ready = False
            print(f"FAIL  {label}: HTTP {exc.code}; check API key/scopes and QFinZero logs")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            ready = False
            print(f"FAIL  {label}: unable to read valid QFinZero data")
        passed &= ready
    print("Coverage checks do not guarantee option quote coverage or fills; run the opt-in broker smoke before a larger task.")
    return passed


def node_command() -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required. Install Node 22.12+ or Node 20.19+.")
    version = subprocess.check_output([node, "-p", "process.versions.node"], text=True).strip()
    major, minor, *_ = map(int, version.split("."))
    if major < 20 or (major == 20 and minor < 19) or (major == 22 and minor < 12) or major == 21:
        raise RuntimeError("Node is too old. Use Node 22.12+ or Node 20.19+.")
    return node


def pnpm_command() -> list[str]:
    if pnpm := shutil.which("pnpm"):
        return [pnpm]
    if corepack := shutil.which("corepack"):
        return [corepack, "pnpm"]
    raise RuntimeError("pnpm is required: enable Corepack or install the version pinned in frontend/package.json.")


def assert_port_free(port: int) -> None:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"Port {port} is in use; stop that service or choose another port.") from exc


def stop_children(children) -> None:
    for process in reversed(children):
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 10
    for process in reversed(children):
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def web(args, root: Path = ROOT) -> None:
    setup(root)
    env = environment(root)
    # Strip bypasses before both build-time and runtime subprocesses.
    for name in ("E2E_TEST_AUTH", "E2E_PLAYWRIGHT", "VITE_E2E_TEST_AUTH"):
        env.pop(name, None)
    for key in ("LIVEOPTION_ACCESS_TOKEN", "LIVEOPTION_INTERNAL_SECRET"):
        if len(env.get(key, "").strip()) < 32:
            raise RuntimeError(f"{key} must be at least 32 characters; update your .env or run setup.")
    node = node_command()
    if not args.skip_check and not doctor(env):
        raise RuntimeError("QFinZero checks failed; Web was not started.")
    frontend = root / "frontend"
    if not (frontend / "node_modules").is_dir():
        subprocess.run([*pnpm_command(), "install", "--frozen-lockfile"], cwd=frontend, env=env, check=True)
    if args.build or not (frontend / "dist/server/server.js").is_file():
        subprocess.run([*pnpm_command(), "build"], cwd=frontend, env=env, check=True)
    if args.prepare_only:
        return
    for port in (args.api_port, args.port):
        assert_port_free(port)
    if args.port == args.api_port:
        raise RuntimeError("Web and API ports must be different")
    env.update({"LIVEOPTION_HOST": "127.0.0.1", "LIVEOPTION_PORT": str(args.api_port),
                "LIVEOPTION_INGEST_URL": f"http://127.0.0.1:{args.api_port}/_ingest/events",
                "LIVEOPTION_UPSTREAM_URL": f"http://127.0.0.1:{args.api_port}",
                "HOST": "127.0.0.1", "PORT": str(args.port)})
    children = []
    try:
        children.append(subprocess.Popen([sys.executable, "-m", "liveoption_web"], cwd=root, env=env))
        deadline = time.monotonic() + 30
        while not health(f"http://127.0.0.1:{args.api_port}/healthz", timeout=1):
            if children[0].poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("API did not become ready; see its log output above.")
            time.sleep(0.2)
        children.append(subprocess.Popen([node, "server/node-entry.mjs"], cwd=frontend, env=env))
        print(f"\nLiveOption: http://127.0.0.1:{args.port}\nSign in with LIVEOPTION_ACCESS_TOKEN from .env or .liveoption/runtime.env.\nCtrl-C stops this local Web/API pair. Running jobs may continue in the background.", flush=True)
        while all(process.poll() is None for process in children):
            time.sleep(0.2)
        raise RuntimeError("A Web/API process exited; see its log output above.")
    except KeyboardInterrupt:
        pass
    finally:
        stop_children(children)


def main(argv=None) -> None:
    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in and args_in[0] == "task":
        os.environ.update(environment())
        from trading.cli import main as cli
        return cli(args_in)
    parser = argparse.ArgumentParser(prog="liveoption", description="Options replay studio powered by QFinZero")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="Create private local Web credentials once")
    check = commands.add_parser("doctor", help="Check QFinZero authentication/services and optional historical coverage")
    check.add_argument("--symbol", help="Underlying ticker to check, e.g. QZSP on the synthetic sample")
    check.add_argument("--date", dest="day", help="Trading date, YYYY-MM-DD")
    check.add_argument("--frequency", choices=("1m", "1d"), default="1m")
    check.add_argument("--with-news", action="store_true", help="Also check ESP and query news when a symbol/date is given")
    commands.add_parser("task", help="Scriptable task commands (liveoption task --help)")
    launch = commands.add_parser("web", help="Build and start a loopback-only local Web/API pair")
    launch.add_argument("--port", type=int, default=3001)
    launch.add_argument("--api-port", type=int, default=19720)
    launch.add_argument("--build", action="store_true", help="Rebuild the frontend")
    launch.add_argument("--prepare-only", action="store_true", help="Install/build without starting servers")
    launch.add_argument("--skip-check", action="store_true", help="Skip QFinZero health checks (for offline UI work)")
    try:
        args = parser.parse_args(args_in)
        if args.command == "setup":
            setup()
        elif args.command == "doctor":
            if not doctor(environment(), symbol=args.symbol, day=args.day, frequency=args.frequency, with_news=args.with_news):
                raise SystemExit(1)
        elif args.command == "web":
            web(args)
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"liveoption: {exc}\n")


if __name__ == "__main__":
    main()
