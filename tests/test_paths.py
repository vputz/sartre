"""Tests for the canonical path model: normalization and case-collision rules."""

from __future__ import annotations

import unicodedata

import pytest

from sartre.errors import PathError
from sartre.paths import check_no_case_collisions, normalize_path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("models/x", "models/x"),
        ("/models/x/", "models/x"),
        ("models//x", "models/x"),
        ("\\Models\\x\\", "Models/x"),
        ("a/b/c", "a/b/c"),
    ],
)
def test_normalize_coerces_formatting(raw: str, expected: str) -> None:
    assert normalize_path(raw) == expected


def test_normalize_applies_nfc() -> None:
    decomposed = "café/x"  # 'e' + combining acute
    result = normalize_path(decomposed)
    assert result == unicodedata.normalize("NFC", "café/x")
    assert "́" not in result


@pytest.mark.parametrize("bad", ["../etc", "a/../b", "./a", "a/.", "a/../../b"])
def test_normalize_rejects_traversal(bad: str) -> None:
    with pytest.raises(PathError):
        normalize_path(bad)


@pytest.mark.parametrize("bad", ["a\x00b", "a\tb", "a\nb"])
def test_normalize_rejects_control_chars(bad: str) -> None:
    with pytest.raises(PathError):
        normalize_path(bad)


def test_normalize_rejects_empty() -> None:
    for bad in ["", "/", "///"]:
        with pytest.raises(PathError):
            normalize_path(bad)


def test_portable_charset_rejected_by_default_but_allowed_posix_only() -> None:
    with pytest.raises(PathError):
        normalize_path("a<b")
    assert normalize_path("a<b", posix_only=True) == "a<b"


def test_trailing_dot_or_space_rejected() -> None:
    with pytest.raises(PathError):
        normalize_path("foo./bar")
    with pytest.raises(PathError):
        normalize_path("foo /bar")


def test_case_distinct_paths_in_separate_dirs_are_allowed() -> None:
    # No exception: distinct keys, no collision within the tree.
    check_no_case_collisions(["dir1/README", "dir2/readme", "a/b", "a/c"])


def test_case_only_collision_same_leaf_rejected() -> None:
    with pytest.raises(PathError):
        check_no_case_collisions(["README", "readme"])


def test_case_only_collision_in_directory_component_rejected() -> None:
    with pytest.raises(PathError):
        check_no_case_collisions(["Foo/a", "foo/b"])


def test_file_and_directory_name_clash_rejected() -> None:
    with pytest.raises(PathError):
        check_no_case_collisions(["foo", "foo/bar"])


def test_duplicate_identical_path_is_not_a_collision() -> None:
    check_no_case_collisions(["a/b", "a/b"])
