"""Regression: a long-lived S3 reader must see pointer moves made by another process.

The default test fixture disables the s3fs listings cache, so it never reproduces the
production bug. Here the cache is ON and non-expiring (the real `open_s3` default), and two
distinct `S3FileSystem` instances model two processes over one bucket — reader A freezes its
listing, writer B advances the pointer, and A must still resolve the new value.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import pytest

from sartre import Coordinate, Entry, NotFound, S3Registry
from sartre.model import Alias, Pin

COORD = Coordinate("models", "prod")


def _entries(tag: str) -> tuple[Entry, ...]:
    return (Entry(f"{tag}.bin", f"sha256:{tag * 8}", 3),)


@pytest.fixture
def make_reg() -> Iterator[Callable[[], S3Registry]]:
    """Factory yielding fresh S3Registry handles (distinct fs instances) over one moto bucket."""
    pytest.importorskip("s3fs")
    moto_server = pytest.importorskip("moto.server")
    import boto3
    import s3fs

    bucket = f"reg-{uuid.uuid4().hex}"
    server = moto_server.ThreadedMotoServer(port=0)
    server.start()
    try:
        host, port = server.get_host_and_port()
        endpoint = f"http://{host}:{port}"
        boto3.client(
            "s3", endpoint_url=endpoint, aws_access_key_id="testing",
            aws_secret_access_key="testing", region_name="us-east-1",
        ).create_bucket(Bucket=bucket)

        def make() -> S3Registry:
            # PROD-LIKE: listings cache ON and non-expiring; a distinct instance each call so two
            # handles have independent dircaches (skip_instance_cache defeats url_to_fs memoizing).
            fs = s3fs.S3FileSystem(
                key="testing", secret="testing", skip_instance_cache=True,
                use_listings_cache=True, listings_expiry_time=None,
                client_kwargs={"endpoint_url": endpoint, "region_name": "us-east-1"},
            )
            return S3Registry(fs, f"{bucket}/repo")

        yield make
    finally:
        server.stop()


def test_long_lived_reader_sees_concurrent_publish(make_reg: Callable[[], S3Registry]) -> None:
    a, b = make_reg(), make_reg()  # two "processes" over the same bucket
    v1 = b.commit(COORD, _entries("a"), {})
    b.set_pointer(COORD, "head", v1, expected=None)

    assert a.head(COORD) == v1  # A resolves once, warming its listing cache

    v2 = b.commit(COORD, _entries("b"), {})
    b.set_pointer(COORD, "head", v2, expected=v1)  # B advances head in its own process

    # control: A's *cached* listing is frozen at v1 — this is exactly the production bug
    _seq, stale = a._pointer_scan(COORD, "head", refresh=False)  # noqa: SLF001
    assert stale == v1

    # the fix: A's read path re-lists and sees the live tail
    assert a.head(COORD) == v2
    assert a.resolve(COORD).version == v2


def test_list_pointers_reflects_a_concurrent_move(make_reg: Callable[[], S3Registry]) -> None:
    a, b = make_reg(), make_reg()
    v1 = b.commit(COORD, _entries("a"), {})
    b.set_pointer(COORD, "stable", v1, expected=None)
    assert dict(a.list_pointers(COORD)) == {"stable": v1}  # warm A

    v2 = b.commit(COORD, _entries("b"), {})
    b.set_pointer(COORD, "stable", v2, expected=v1)
    assert dict(a.list_pointers(COORD)) == {"stable": v2}  # A sees B's move


def test_pin_resolves_a_version_published_after_first_read(
    make_reg: Callable[[], S3Registry],
) -> None:
    a, b = make_reg(), make_reg()
    v1 = b.commit(COORD, _entries("a"), {})
    b.set_pointer(COORD, "head", v1, expected=None)
    assert a.head(COORD) == v1  # warm A

    v2 = b.commit(COORD, _entries("b"), {})
    b.set_pointer(COORD, "head", v2, expected=v1)
    assert a.resolve(COORD, Pin(v2)).version == v2  # A resolves the new version by id, not NotFound


def test_reader_honors_a_concurrent_tombstone(make_reg: Callable[[], S3Registry]) -> None:
    a, b = make_reg(), make_reg()
    v1 = b.commit(COORD, _entries("a"), {})
    v2 = b.commit(COORD, _entries("b"), {})
    b.set_pointer(COORD, "head", v1, expected=None)
    b.set_pointer(COORD, "head", v2, expected=v1)  # head=v2, v1 is droppable history
    assert v1 in set(a.list_versions(COORD))  # warm A's version + tombstone listings

    b.drop_version(v1)  # concurrent GC tombstones v1
    assert v1 not in set(a.list_versions(COORD))  # A sees the tombstone, not a frozen listing
    with pytest.raises(NotFound):
        a.resolve(COORD, Pin(v1))


def test_write_paths_unaffected(make_reg: Callable[[], S3Registry]) -> None:
    """Sanity: the CAS still rejects a stale expected, and aliases still resolve."""
    reg = make_reg()
    v1 = reg.commit(COORD, _entries("a"), {})
    v2 = reg.commit(COORD, _entries("b"), {})
    reg.set_pointer(COORD, "head", v1, expected=None)
    from sartre import Conflict

    with pytest.raises(Conflict):
        reg.set_pointer(COORD, "head", v2, expected=None)  # already exists → conflict
    reg.set_pointer(COORD, "head", v2, expected=v1)
    assert reg.head(COORD, Alias("head")) == v2
