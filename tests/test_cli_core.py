"""Framework-free CLI core: reference grammar, duration parser, addressing resolution."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sartre.cli.config import resolve_target
from sartre.cli.duration import format_duration, parse_duration
from sartre.cli.errors import CliError
from sartre.cli.refs import parse_ref, parse_source, render_ref
from sartre.model import Alias, Coordinate, Head, Pin

# --- reference grammar ---

_seg = st.from_regex(r"[a-z][a-z0-9._-]{0,6}", fullmatch=True)
_ver = st.from_regex(r"sha256:[0-9a-f]{4,12}", fullmatch=True)
_refs = st.one_of(st.just(Head()), _seg.map(Alias), _ver.map(Pin))


@given(name=_seg, env=_seg, ref=_refs)
def test_ref_roundtrip(name: str, env: str, ref: object) -> None:
    coord = Coordinate(name, env)
    assert parse_ref(render_ref(coord, ref)) == (coord, ref)  # type: ignore[arg-type]


def test_ref_forms() -> None:
    assert parse_ref("resnet/prod") == (Coordinate("resnet", "prod"), Head())
    assert parse_ref("resnet/prod:stable") == (Coordinate("resnet", "prod"), Alias("stable"))
    assert parse_ref("resnet/prod@sha256:abc123") == (
        Coordinate("resnet", "prod"),
        Pin("sha256:abc123"),
    )


def test_default_env_fills_bare_name() -> None:
    assert parse_ref("resnet", default_env="prod") == (Coordinate("resnet", "prod"), Head())


def test_missing_env_without_default_errors() -> None:
    with pytest.raises(CliError):
        parse_ref("resnet")


def test_bad_version_errors() -> None:
    with pytest.raises(CliError):
        parse_ref("m/prod@not-a-version")


def test_parse_source_distinguishes_version_head_alias() -> None:
    coord = Coordinate("m", "prod")
    assert parse_source(coord, "sha256:abcd") == Pin("sha256:abcd")
    assert parse_source(coord, "head") == Head()
    assert parse_source(coord, "stable") == Alias("stable")


# --- duration parser ---


@given(seconds=st.integers(min_value=0, max_value=10**8))
def test_duration_roundtrip(seconds: int) -> None:
    td = timedelta(seconds=seconds)
    assert parse_duration(format_duration(td)) == td


def test_duration_forms_and_errors() -> None:
    assert parse_duration("30d") == timedelta(days=30)
    assert parse_duration("7d12h") == timedelta(days=7, hours=12)
    for bad in ("", "10", "1.5h", "3x", "d"):
        with pytest.raises(CliError):
            parse_duration(bad)


# --- addressing resolution ---


def test_flags_beat_env_and_profile(tmp_path: Path) -> None:
    t = resolve_target(
        repo=str(tmp_path / "a"),
        environ={"SARTRE_REPO": str(tmp_path / "b")},
        cwd=tmp_path,
    )
    assert t.kind == "local" and t.path == str(tmp_path / "a")


def test_env_used_when_no_flags(tmp_path: Path) -> None:
    t = resolve_target(environ={"SARTRE_REPO": str(tmp_path / "b")}, cwd=tmp_path)
    assert t.path == str(tmp_path / "b")


def test_cloud_inferred_from_registry_and_blobs(tmp_path: Path) -> None:
    t = resolve_target(registry="postgresql://x", blobs="s3://b/blobs", environ={}, cwd=tmp_path)
    assert t.kind == "cloud" and t.registry_dsn == "postgresql://x" and t.blob_url == "s3://b/blobs"


def test_registry_without_blobs_errors(tmp_path: Path) -> None:
    with pytest.raises(CliError):
        resolve_target(registry="postgresql://x", blobs=None, environ={}, cwd=tmp_path)


def test_profile_from_config(tmp_path: Path) -> None:
    cfg = tmp_path / "sartre" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[profiles.prod]\nregistry = "pg://x"\nblobs = "s3://b"\nenv = "staging"\n')
    t = resolve_target(
        profile="prod", environ={"XDG_CONFIG_HOME": str(tmp_path)}, cwd=tmp_path
    )
    assert t.kind == "cloud" and t.default_env == "staging"


def test_cwd_autodetect_walks_up(tmp_path: Path) -> None:
    (tmp_path / "registry.db").write_text("")  # a local repo marker
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    t = resolve_target(environ={"XDG_CONFIG_HOME": str(tmp_path / "empty")}, cwd=sub)
    assert t.kind == "local" and t.path == str(tmp_path)


def test_no_repository_configured_errors(tmp_path: Path) -> None:
    with pytest.raises(CliError):
        resolve_target(environ={"XDG_CONFIG_HOME": str(tmp_path / "empty")}, cwd=tmp_path)
