"""Tests for the domain model: ref semantics and frozen value types."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from sartre.model import Alias, Coordinate, Entry, Head, Pin, Snapshot


def test_distinct_envs_are_distinct_coordinates() -> None:
    assert Coordinate("models", "dev") != Coordinate("models", "release")
    assert Coordinate("models", "dev") == Coordinate("models", "dev")


def test_ref_value_equality() -> None:
    assert Head() == Head()
    assert Pin("v1") == Pin("v1")
    assert Pin("v1") != Pin("v2")
    assert Alias("production") != Alias("best")


def test_value_types_are_frozen() -> None:
    coord = Coordinate("models", "dev")
    with pytest.raises(dataclasses.FrozenInstanceError):
        coord.name = "other"  # type: ignore[misc]


def test_entry_inline_defaults_to_none() -> None:
    entry = Entry(path="a/b.txt", content_hash="sha256:deadbeef", size=3)
    assert entry.inline is None


def test_single_entry_and_many_entry_snapshots_share_entry_shape() -> None:
    one = Entry("only.bin", "sha256:aa", 1)
    snap_one = Snapshot(
        coord=Coordinate("backtest-db", "release"),
        version="v0.1.0",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        metadata={},
        entries=(one,),
    )
    many = Snapshot(
        coord=Coordinate("models", "release"),
        version="abc123",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        metadata={"note": "x"},
        entries=(one, Entry("second.bin", "sha256:bb", 2)),
    )
    assert len(snap_one.entries) == 1
    assert type(snap_one.entries[0]) is type(many.entries[0])
