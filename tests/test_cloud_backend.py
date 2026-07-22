"""Cloud-path tests: S3 blob backend (moto) and open_cloud (Postgres + blobs).

Both groups skip cleanly when their infra (moto/s3fs, or docker/psycopg via the
`postgres_dsn` fixture) is unavailable.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest

from sartre import CasStore, Coordinate, FsspecBlobBackend, open_cloud

COORD = Coordinate("models", "cloud")


@pytest.fixture
def s3_endpoint() -> Iterator[str]:
    pytest.importorskip("s3fs")
    moto_server = pytest.importorskip("moto.server")
    server = moto_server.ThreadedMotoServer(port=0)
    server.start()
    try:
        host, port = server.get_host_and_port()
        yield f"http://{host}:{port}"
    finally:
        server.stop()


def _s3fs(endpoint: str):  # noqa: ANN202 - test helper
    import s3fs

    return s3fs.S3FileSystem(
        key="testing",
        secret="testing",
        client_kwargs={"endpoint_url": endpoint, "region_name": "us-east-1"},
        skip_instance_cache=True,
    )


def test_s3_blob_backend_round_trip_and_atomicity(s3_endpoint: str) -> None:
    import boto3

    boto3.client(
        "s3",
        endpoint_url=s3_endpoint,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        region_name="us-east-1",
    ).create_bucket(Bucket="sartre-bucket")  # us-east-1: no LocationConstraint

    fs = _s3fs(s3_endpoint)
    store = CasStore(FsspecBlobBackend(fs, root="sartre-bucket/blobs"))

    key = store.put(io.BytesIO(b"CLOUD-BLOB"))
    assert store.has(key)
    assert store.open(key).read() == b"CLOUD-BLOB"
    assert set(store.list()) == {key}  # only the real blob, no .tmp staging object
    assert store.put(io.BytesIO(b"CLOUD-BLOB")) == key  # idempotent


def test_open_cloud_publish_resolve(postgres_dsn: str, tmp_path: Path) -> None:
    blob_url = (tmp_path / "blobs").as_uri()  # file:// blobs; registry is real Postgres
    repo = open_cloud(postgres_dsn, blob_url)
    version = repo.publish(COORD, {"w.bin": b"CLOUD-WEIGHTS", "cfg.json": b"{}"})

    snap = repo.resolve(COORD)
    assert snap.version == version
    assert repo.open(snap, "w.bin").read_bytes() == b"CLOUD-WEIGHTS"
