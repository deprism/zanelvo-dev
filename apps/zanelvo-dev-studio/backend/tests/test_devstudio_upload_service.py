"""Deterministic, DB-free tests for upload_service.

Blob storage moved from pod-local disk to MongoDB GridFS (so uploads survive a redeploy and the
tool stays self-contained — its own datastore, no external object store). GridFS itself needs a
live Mongo, exercised in the live round-trip check; these cover the branches that don't:

- input validation (rejects before any storage call, so no DB needed), and
- the legacy on-disk read fallback for uploads saved before the GridFS migration.
"""
import asyncio
import os
import tempfile

import pytest

from app.devstudio.models import Upload
from app.devstudio.services import upload_service
from app.devstudio.services.upload_service import UploadRejected


def test_rejects_disallowed_content_type():
    with pytest.raises(UploadRejected):
        asyncio.run(upload_service.save_upload("x.exe", "application/x-msdownload", b"data"))


def test_rejects_oversized_file():
    too_big = b"\x00" * (upload_service._MAX_BYTES + 1)
    with pytest.raises(UploadRejected):
        asyncio.run(upload_service.save_upload("big.png", "image/png", too_big))


def test_read_upload_bytes_reads_legacy_on_disk_path():
    # An upload saved before blobs moved into GridFS still has an absolute filesystem path in
    # `path`; read_upload_bytes must transparently read it rather than treat it as a GridFS id.
    payload = b"legacy-on-disk-bytes"
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as fh:
        fh.write(payload)
        legacy_path = fh.name
    try:
        up = Upload(filename="old.png", content_type="image/png", size_bytes=len(payload),
                    path=legacy_path, is_image=True)
        assert asyncio.run(upload_service.read_upload_bytes(up)) == payload
    finally:
        os.remove(legacy_path)
