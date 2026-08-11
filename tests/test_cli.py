"""End-to-end CLI tests via Typer's CliRunner against a temp local repo, plus the
`point --force` retry and the missing-extra error path."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from sartre import Conflict, Coordinate, Repository
from sartre.cli import ops
from sartre.cli.app import app
from sartre.model import Pin

runner = CliRunner()  # click >= 8.2 keeps stderr separate (result.stderr)
COORD = Coordinate("models", "prod")


def _ckpt(tmp_path: Path) -> Path:
    src = tmp_path / "ckpt"
    (src / "w").mkdir(parents=True, exist_ok=True)
    (src / "w" / "model.bin").write_bytes(b"WEIGHTS")
    (src / "cfg.json").write_bytes(b"{}")
    return src


def _run(repo: Path, *args: str):  # noqa: ANN202 - test helper
    return runner.invoke(app, ["--repo", str(repo), *args])


def test_publish_show_ls_cat_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    src = _ckpt(tmp_path)

    r = _run(repo, "publish", "models/prod", str(src), "-m", "first", "--meta", "stage=rc")
    assert r.exit_code == 0
    version = r.stdout.strip()
    assert version.startswith("sha256:")

    r = _run(repo, "show", "models/prod")
    assert r.exit_code == 0 and version in r.stdout and "stage=rc" in r.stdout

    r = _run(repo, "ls", "models/prod")
    assert {line for line in r.stdout.split()} >= {"cfg.json", "w/model.bin"}

    r = _run(repo, "cat", "models/prod", "w/model.bin")
    assert r.exit_code == 0 and "WEIGHTS" in r.stdout

    dest = tmp_path / "out"
    r = _run(repo, "checkout", "models/prod", str(dest))
    assert r.exit_code == 0
    assert (dest / "w" / "model.bin").read_bytes() == b"WEIGHTS"


def test_json_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _run(repo, "publish", "models/prod", str(_ckpt(tmp_path)))
    r = runner.invoke(app, ["--repo", str(repo), "--json", "coords"])
    assert r.exit_code == 0
    assert json.loads(r.stdout) == [{"name": "models", "env": "prod"}]

    r = runner.invoke(app, ["--repo", str(repo), "--json", "ls", "models/prod"])
    rows = json.loads(r.stdout)
    assert {row["path"] for row in rows} == {"cfg.json", "w/model.bin"}


def test_point_promote_and_rollback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    src = _ckpt(tmp_path)
    v1 = _run(repo, "publish", "models/prod", str(src)).stdout.strip()
    (src / "cfg.json").write_bytes(b'{"v":2}')  # change content → a new version
    v2 = _run(repo, "publish", "models/prod", str(src)).stdout.strip()
    assert v1 != v2

    r = _run(repo, "point", "models/prod:stable", v1)  # promote v1 to stable
    assert r.exit_code == 0
    assert _run(repo, "head", "models/prod:stable").stdout.strip() == v1

    r = _run(repo, "point", "models/prod", v1)  # rollback head to v1
    assert r.exit_code == 0
    assert _run(repo, "head", "models/prod").stdout.strip() == v1


def test_error_exits_nonzero(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _run(repo, "publish", "models/prod", str(_ckpt(tmp_path)))
    r = _run(repo, "show", "models/nope")  # unknown coordinate
    assert r.exit_code == 1 and "error:" in r.stderr


class _RaceOnceRepo:
    """Wraps a Repository and makes the first `point` raise Conflict (simulating a race)."""

    def __init__(self, inner: Repository) -> None:
        self._inner = inner
        self._raced = False

    def head(self, *a: object, **k: object):  # noqa: ANN202
        return self._inner.head(*a, **k)  # type: ignore[arg-type]

    def point(self, coord, name, version, *, expected, actor="unknown", reason=None):  # noqa: ANN001, ANN202
        if not self._raced:
            self._raced = True
            raise Conflict("raced")
        self._inner.point(coord, name, version, expected=expected, actor=actor, reason=reason)


def test_move_pointer_force_retries_past_conflict(repo: Repository) -> None:
    version = repo.publish(COORD, {"a.bin": b"1"})
    racey = _RaceOnceRepo(repo)

    with pytest.raises(Conflict):  # without force, a race surfaces
        ops.move_pointer(cast(Repository, racey), COORD, "stable", Pin(version), force=False)

    racey2 = _RaceOnceRepo(repo)
    got = ops.move_pointer(cast(Repository, racey2), COORD, "stable", Pin(version), force=True)
    assert got == version


def test_missing_cli_extra_errors_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    import sartre.cli

    monkeypatch.setitem(sys.modules, "sartre.cli.app", None)  # force ImportError on app import
    with pytest.raises(SystemExit) as excinfo:
        sartre.cli.main()
    assert "sartre[cli]" in str(excinfo.value)
