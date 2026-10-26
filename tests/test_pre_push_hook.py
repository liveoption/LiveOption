from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / ".githooks" / "pre-push"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")

    (repo / "README.md").write_text("test\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def _run_hook(repo: Path, local_sha: str, remote_sha: str) -> subprocess.CompletedProcess[str]:
    stdin_payload = (
        f"refs/heads/main {local_sha} refs/heads/main {remote_sha}\n"
    )
    return subprocess.run(
        [str(HOOK_PATH)],
        cwd=repo,
        input=stdin_payload,
        capture_output=True,
        text=True,
    )


def test_pre_push_allows_env_comment_in_file_content(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    script = repo / "scripts" / "run_earnings_bet.py"
    script.parent.mkdir()
    script.write_text(
        "#!/usr/bin/env python3\n"
        "# Load .env\n"
        "from dotenv import load_dotenv\n"
        'load_dotenv(".env")\n'
    )
    _git(repo, "add", "scripts/run_earnings_bet.py")
    _git(repo, "commit", "-m", "add script")

    head = _git(repo, "rev-parse", "HEAD")
    prev = _git(repo, "rev-parse", "HEAD~1")
    result = _run_hook(repo, head, prev)

    assert result.returncode == 0, result.stdout + result.stderr


def test_pre_push_allows_example_env_file(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".env.example").write_text("OPENAI_API_KEY=your-key-here\n")
    _git(repo, "add", ".env.example")
    _git(repo, "commit", "-m", "add example")

    head = _git(repo, "rev-parse", "HEAD")
    prev = _git(repo, "rev-parse", "HEAD~1")
    result = _run_hook(repo, head, prev)
    assert result.returncode == 0, result.stdout + result.stderr


def test_pre_push_blocks_actual_dotenv_file(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    env_file = repo / ".env"
    env_file.write_text("OPENAI_API_KEY=not-a-real-key\n")
    _git(repo, "add", ".env")
    _git(repo, "commit", "-m", "add env")

    head = _git(repo, "rev-parse", "HEAD")
    prev = _git(repo, "rev-parse", "HEAD~1")
    result = _run_hook(repo, head, prev)

    assert result.returncode == 1
    assert "SECRET LEAK DETECTED" in result.stdout


def test_pre_push_checks_root_commit_on_new_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / ".env").write_text("OPENAI_API_KEY=not-a-real-key\n")
    _git(repo, "add", ".env")
    _git(repo, "commit", "-m", "initial")

    head = _git(repo, "rev-parse", "HEAD")
    result = _run_hook(repo, head, "0" * 40)
    assert result.returncode == 1
    assert "SECRET LEAK DETECTED" in result.stdout


def test_pre_push_blocks_credentials_without_echoing_them(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    credential = "sk-" + "notarealcredential" * 3
    (repo / "settings.py").write_text(f'api_key = "{credential}"\n')
    _git(repo, "add", "settings.py")
    _git(repo, "commit", "-m", "add credential fixture")
    result = _run_hook(repo, _git(repo, "rev-parse", "HEAD"), _git(repo, "rev-parse", "HEAD~1"))
    assert result.returncode == 1
    assert "settings.py" in result.stdout and "line 1" in result.stdout
    assert "[REDACTED]" in result.stdout
    assert credential not in result.stdout + result.stderr


def test_pre_push_blocks_encrypted_private_keys_without_echoing_them(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    marker = "-----BEGIN " + "ENCRYPTED PRIVATE KEY-----"
    (repo / "credentials.txt").write_text(marker + "\nfixture\n")
    _git(repo, "add", "credentials.txt")
    _git(repo, "commit", "-m", "add private-key fixture")
    result = _run_hook(repo, _git(repo, "rev-parse", "HEAD"), _git(repo, "rev-parse", "HEAD~1"))
    assert result.returncode == 1
    assert marker not in result.stdout + result.stderr
