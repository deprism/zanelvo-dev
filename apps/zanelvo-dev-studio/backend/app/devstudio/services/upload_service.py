"""UploadService — task/project attachments. Images are exposed to vision-capable agents on
request only (never auto-injected into every context, per spec).

Blobs are stored in MongoDB via GridFS — Dev Studio's own datastore, which the desktop build
ships as an embedded local Mongo. This keeps the tool self-contained (no external object-storage
service or credentials, works fully offline) while avoiding any dependency on pod-local files that
don't survive a redeploy. The Upload document's `path` field holds the GridFS file id (a legacy
on-disk absolute path is still read transparently for any upload saved before this change)."""
from __future__ import annotations

import base64
import os
import uuid
from typing import List, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from ...db import get_db
from ..models import Upload

_ALLOWED_TYPES = {
    "image/png": True, "image/jpeg": True, "image/webp": True,
    "application/pdf": False, "text/plain": False, "text/markdown": False,
    "application/json": False, "text/x-log": False, "text/csv": False,
}
_MAX_BYTES = 15 * 1024 * 1024

_GRIDFS_BUCKET = "ds_uploads_blobs"


def _bucket() -> AsyncIOMotorGridFSBucket:
    return AsyncIOMotorGridFSBucket(get_db(), bucket_name=_GRIDFS_BUCKET)


class UploadRejected(Exception):
    pass


async def save_upload(filename: str, content_type: str, data: bytes,
                       task_id: Optional[str] = None, project_id: Optional[str] = None) -> Upload:
    if content_type not in _ALLOWED_TYPES:
        raise UploadRejected(f"Content type not allowed: {content_type}")
    if len(data) > _MAX_BYTES:
        raise UploadRejected(f"File too large ({len(data)} bytes, max {_MAX_BYTES})")
    safe_name = f"{uuid.uuid4().hex}-{os.path.basename(filename)}"
    file_id = await _bucket().upload_from_stream(
        safe_name, data, metadata={"content_type": content_type, "task_id": task_id,
                                     "project_id": project_id})
    up = Upload(task_id=task_id, project_id=project_id, filename=filename, content_type=content_type,
                size_bytes=len(data), path=str(file_id), is_image=_ALLOWED_TYPES[content_type])
    res = await get_db().ds_uploads.insert_one(up.to_mongo())
    up.id = str(res.inserted_id)
    return up


async def list_uploads(task_id: str) -> List[Upload]:
    docs = get_db().ds_uploads.find({"task_id": task_id}).sort("created_at", -1)
    return [Upload.from_mongo(d) async for d in docs]


async def get_upload(upload_id: str) -> Optional[Upload]:
    doc = await get_db().ds_uploads.find_one({"_id": ObjectId(upload_id)})
    return Upload.from_mongo(doc) if doc else None


async def read_upload_bytes(upload: Upload) -> bytes:
    """Fetch an upload's raw bytes (used to serve downloads and to base64-encode images for
    vision-capable agents on request). Reads from GridFS by the stored file id; falls back to a
    legacy on-disk path for any upload saved before blobs moved into GridFS."""
    if os.path.isabs(upload.path) and os.path.isfile(upload.path):
        with open(upload.path, "rb") as fh:
            return fh.read()
    stream = await _bucket().open_download_stream(ObjectId(upload.path))
    return await stream.read()


async def read_upload_image_b64(upload: Upload) -> str:
    """Base64-encode an image upload for a provider's generate_with_vision(images_b64=...)."""
    return base64.b64encode(await read_upload_bytes(upload)).decode()


async def set_vision_attachment(upload_id: str, attach: bool) -> Optional[Upload]:
    """Mark/unmark an image upload for delivery to vision-capable agents (e.g. Design). Non-images
    can never be attached to vision — the toggle is a no-op guarded here."""
    up = await get_upload(upload_id)
    if up is None:
        return None
    if attach and not up.is_image:
        raise UploadRejected("Only image uploads can be attached to a vision model")
    await get_db().ds_uploads.update_one({"_id": ObjectId(upload_id)},
                                         {"$set": {"attach_to_vision": bool(attach)}})
    return await get_upload(upload_id)


async def list_vision_images(task_id: str) -> List[str]:
    """Base64-encoded bytes of every image on this task flagged attach_to_vision — supplied to a
    vision-capable agent ON REQUEST only (never auto-injected into every context)."""
    out: List[str] = []
    docs = get_db().ds_uploads.find({"task_id": task_id, "is_image": True, "attach_to_vision": True})
    async for d in docs:
        up = Upload.from_mongo(d)
        out.append(await read_upload_image_b64(up))
    return out
