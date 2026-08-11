"""CLI change provenance: the author-resolution ladder, `-m` → reason, and the
`history`/`log`/`show` surfaces. Ladder rules are unit-tested; the rest is CliRunner e2e.
"""

from __future__ import annotations

import getpass
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sartre.cli.app import app
from sartre.cli.config import RepoTarget, resolve_author
from sartre.cli.errors import CliError

runner = CliRunner()


def _ckpt(tmp_path: Path) -> Path:
    src = tmp_path / "ckpt"
    src.mkdir(parents=True, exist_ok=True)
    (src / "cfg.json").write_bytes(b"{}")
    return src


def _run(repo: Path, *args: str, env: dict[str, str] | None = None):  # noqa: ANN202
    return runner.invoke(app, ["--repo", str(repo), *args], env=env)


# --- author-resolution ladder (unit) ---


def test_flag_beats_env_and_profile() -> None:
    target = RepoTarget(author="from-profile")
    got = resolve_author(flag="alice", target=target, environ={"SARTRE_AUTHOR": "from-env"})
    assert got == "alice"


def test_env_beats_profile_and_os_user() -> None:
    target = RepoTarget(author="from-profile")
    assert resolve_author(target=target, environ={"SARTRE_AUTHOR": "from-env"}) == "from-env"


def test_profile_beats_os_user() -> None:
    target = RepoTarget(author="from-profile")
    assert resolve_author(target=target, environ={}) == "from-profile"


def test_falls_back_to_os_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(getpass, "getuser", lambda: "os-user")
    assert resolve_author(target=RepoTarget(), environ={}) == "os-user"


def test_unresolvable_author_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> str:
        raise OSError("no passwd entry")

    monkeypatch.setattr(getpass, "getuser", _boom)
    with pytest.raises(CliError):
        resolve_author(target=RepoTarget(), environ={})


# --- e2e ---


def test_publish_records_author_and_reason_not_metadata(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    r = _run(repo, "publish", "m/prod", str(_ckpt(tmp_path)),
             "--as", "alice", "-m", "retrain on Q3", "--meta", "stage=rc")
    assert r.exit_code == 0

    data = json.loads(_run(repo, "--json", "show", "m/prod").stdout)  # type: ignore[union-attr]
    assert data["actor"] == "alice"
    assert data["reason"] == "retrain on Q3"
    assert data["metadata"] == {"stage": "rc"}  # reason did NOT leak into metadata
    assert "message" not in data["metadata"]


def test_flag_beats_env_at_the_cli(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _run(repo, "publish", "m/prod", str(_ckpt(tmp_path)), "--as", "alice",
         env={"SARTRE_AUTHOR": "bob"})
    rows = json.loads(_run(repo, "--json", "log", "m/prod").stdout)  # type: ignore[union-attr]
    assert rows[-1]["actor"] == "alice"


def test_env_author_used_when_no_flag(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _run(repo, "publish", "m/prod", str(_ckpt(tmp_path)), env={"SARTRE_AUTHOR": "carol"})
    rows = json.loads(_run(repo, "--json", "log", "m/prod").stdout)  # type: ignore[union-attr]
    assert rows[-1]["actor"] == "carol"


def test_history_shows_moves_human_and_json(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    src = _ckpt(tmp_path)
    v1 = _run(repo, "publish", "m/prod", str(src), "--as", "alice").stdout.strip()  # type: ignore[union-attr]
    (src / "cfg.json").write_bytes(b'{"v":2}')
    _run(repo, "publish", "m/prod", str(src), "--as", "alice")
    _run(repo, "point", "m/prod:stable", v1, "--as", "bob", "-m", "passed eval")

    rows = json.loads(_run(repo, "--json", "history", "m/prod").stdout)  # type: ignore[union-attr]
    stable = [m for m in rows if m["pointer"] == "stable"]
    assert stable and stable[-1]["to_version"] == v1
    assert stable[-1]["actor"] == "bob" and stable[-1]["reason"] == "passed eval"
    assert rows == sorted(rows, key=lambda m: m["at"])  # JSON is oldest → newest

    human = _run(repo, "history", "m/prod")
    assert human.exit_code == 0 and "stable" in human.stdout and "bob" in human.stdout


def test_log_and_show_surface_provenance_in_human_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _run(repo, "publish", "m/prod", str(_ckpt(tmp_path)), "--as", "dana", "-m", "initial")
    assert "dana" in _run(repo, "log", "m/prod").stdout  # type: ignore[union-attr]
    show = _run(repo, "show", "m/prod")
    assert "dana" in show.stdout and "initial" in show.stdout  # type: ignore[union-attr]
