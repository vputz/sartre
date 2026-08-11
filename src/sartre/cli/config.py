"""Repository addressing (framework-free): resolve *which* repo a command acts on, and
open it. Precedence, highest first: explicit flags › ``SARTRE_*`` env › named ``--profile``
(``~/.config/sartre/config.toml``) › cwd auto-detect › the ``default`` profile. The backend
is inferred — a path opens local, a registry DSN + blob URL opens cloud.
"""

from __future__ import annotations

import getpass
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sartre.cli.errors import CliError
from sartre.cloud import open_cloud
from sartre.local import open_local
from sartre.repository import Repository

_FALLBACK_ENV = "dev"
_REPO_MARKER = "registry.db"  # the open_local layout marker used for cwd auto-detect


@dataclass(frozen=True)
class RepoTarget:
    """A resolved, backend-inferred repository address plus the default env for bare names."""

    default_env: str = _FALLBACK_ENV
    path: str | None = None  # set → local
    registry_dsn: str | None = None  # set with blob_url → cloud
    blob_url: str | None = None
    cache_dir: str | None = None
    storage_options: Mapping[str, Any] = field(default_factory=dict)
    author: str | None = None  # profile default for change attribution (see resolve_author)

    @property
    def kind(self) -> str:
        return "local" if self.path is not None else "cloud"


def _config_path(environ: Mapping[str, str]) -> Path:
    base = environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "sartre" / "config.toml"


def _load_profiles(environ: Mapping[str, str]) -> Mapping[str, Mapping[str, Any]]:
    path = _config_path(environ)
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text())
    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        raise CliError(f"malformed config {path}: [profiles] must be a table")
    return profiles


def _detect_local(cwd: Path) -> str | None:
    for directory in (cwd, *cwd.parents):  # git-like walk up for a repo marker
        if (directory / _REPO_MARKER).exists():
            return str(directory)
    return None


def _target_from_profile(prof: Mapping[str, Any], default_env: str) -> RepoTarget:
    env = str(prof.get("env", default_env))
    author = str(prof["author"]) if "author" in prof else None
    if "repo" in prof:
        return RepoTarget(default_env=env, path=str(prof["repo"]), author=author)
    if "registry" in prof and "blobs" in prof:
        return RepoTarget(
            default_env=env,
            registry_dsn=str(prof["registry"]),
            blob_url=str(prof["blobs"]),
            cache_dir=str(prof["cache_dir"]) if "cache_dir" in prof else None,
            storage_options=dict(prof.get("storage_options", {})),
            author=author,
        )
    raise CliError("profile must set 'repo', or both 'registry' and 'blobs'")


def resolve_target(
    *,
    repo: str | None = None,
    registry: str | None = None,
    blobs: str | None = None,
    profile: str | None = None,
    env: str | None = None,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> RepoTarget:
    """Resolve a :class:`RepoTarget` by the precedence ladder. Injectable env/cwd for tests."""
    environ = os.environ if environ is None else environ
    cwd = Path.cwd() if cwd is None else cwd
    default_env = env or environ.get("SARTRE_ENV") or _FALLBACK_ENV

    if registry is not None and blobs is None or registry is None and blobs is not None:
        raise CliError("--registry and --blobs must be given together")

    # 1. explicit flags
    if repo is not None:
        return RepoTarget(default_env=default_env, path=repo)
    if registry is not None and blobs is not None:
        return RepoTarget(default_env=default_env, registry_dsn=registry, blob_url=blobs)

    # 2. environment
    if environ.get("SARTRE_REPO"):
        return RepoTarget(default_env=default_env, path=environ["SARTRE_REPO"])
    if environ.get("SARTRE_REGISTRY_DSN") and environ.get("SARTRE_BLOB_URL"):
        return RepoTarget(
            default_env=default_env,
            registry_dsn=environ["SARTRE_REGISTRY_DSN"],
            blob_url=environ["SARTRE_BLOB_URL"],
        )

    # 3. named profile
    profiles = _load_profiles(environ)
    selected = profile or environ.get("SARTRE_PROFILE")
    if selected is not None:
        if selected not in profiles:
            raise CliError(f"unknown profile {selected!r} in {_config_path(environ)}")
        return _target_from_profile(profiles[selected], default_env)

    # 4. cwd auto-detect (git-like)
    detected = _detect_local(cwd)
    if detected is not None:
        return RepoTarget(default_env=default_env, path=detected)

    # 5. the default profile
    if "default" in profiles:
        return _target_from_profile(profiles["default"], default_env)

    raise CliError(
        "no repository configured: pass --repo/--registry+--blobs, set SARTRE_REPO, "
        "run inside a repo, or define a profile in " + str(_config_path(environ))
    )


def resolve_author(
    *,
    flag: str | None = None,
    target: RepoTarget | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve the acting author for a mutating command by the precedence ladder.

    Order, highest first: ``--author/--as`` flag › ``SARTRE_AUTHOR`` env › the active
    profile's ``author`` › the OS user (``getpass.getuser()``). Raises :class:`CliError`
    if none resolves — attribution is required at the CLI edge (the library defaults to
    ``"unknown"``, but the CLI does not). A ``getpass`` lookup that raises (no passwd
    entry) is treated as unresolved, not a crash.
    """
    environ = os.environ if environ is None else environ
    candidate = flag or environ.get("SARTRE_AUTHOR") or (target.author if target else None)
    if candidate:
        return candidate
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no OS user available → fall through to the clean error
        user = ""
    if user:
        return user
    raise CliError(
        "no author for this change: pass --author/--as, set SARTRE_AUTHOR, "
        "or add 'author' to your profile"
    )


def open_target(target: RepoTarget) -> Repository:
    """Open the resolved target, inferring local vs cloud from which fields are set."""
    if target.path is not None:
        return open_local(target.path)
    assert target.registry_dsn is not None and target.blob_url is not None
    return open_cloud(
        target.registry_dsn,
        target.blob_url,
        cache_dir=target.cache_dir,
        storage_options=dict(target.storage_options),
    )
