"""Object storage (Cloudflare R2 over the S3 API — zero egress fees).

Keys:
  outputs/<jobId>/...         final outputs the server presigns for users (30-day lifecycle)
  work/<jobId>/frames/...     intermediate frames between render chunks and the encoder (1-day lifecycle)
  cache/<cacheKey>.json       result-cache manifest: list of output objects for a cache key
"""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Protocol

CONTENT_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".exr": "image/x-exr", ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".gif": "image/gif", ".json": "application/json", ".zip": "application/zip",
}


def content_type(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    return CONTENT_TYPES.get(ext) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"


class Storage(Protocol):
    def put_file(self, path: Path, key: str, content_type: str | None = None) -> int: ...
    def get_file(self, key: str, dest: Path) -> None: ...
    def put_json(self, key: str, value: Any) -> None: ...
    def get_json(self, key: str) -> Any | None: ...
    def copy(self, src_key: str, dst_key: str) -> None: ...


class R2Storage:
    def __init__(self, bucket: str | None = None, endpoint: str | None = None, access_key: str | None = None, secret_key: str | None = None):
        import boto3
        from botocore.config import Config

        self.bucket = bucket or os.environ["R2_BUCKET"]
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint or os.environ.get("R2_ENDPOINT") or f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key or os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=secret_key or os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
            config=Config(retries={"max_attempts": 8, "mode": "adaptive"}, s3={"addressing_style": "path"}),
        )

    def put_file(self, path: Path, key: str, content_type_: str | None = None) -> int:
        self.s3.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type_ or content_type(path)})
        return Path(path).stat().st_size

    def get_file(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.s3.download_file(self.bucket, key, str(dest))

    def put_json(self, key: str, value: Any) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(value).encode(), ContentType="application/json")

    def get_json(self, key: str) -> Any | None:
        try:
            return json.loads(self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())
        except self.s3.exceptions.NoSuchKey:
            return None
        except Exception as exc:  # R2 returns 404 ClientError for some SDK paths
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return None
            raise

    def copy(self, src_key: str, dst_key: str) -> None:
        # Server-side copy: no bytes flow through the worker.
        self.s3.copy_object(Bucket=self.bucket, Key=dst_key, CopySource={"Bucket": self.bucket, "Key": src_key})


class MemoryStorage:
    """In-memory Storage for tests and local dry runs."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_file(self, path: Path, key: str, content_type_: str | None = None) -> int:
        data = Path(path).read_bytes()
        self.objects[key] = (data, content_type_ or content_type(path))
        return len(data)

    def get_file(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.objects[key][0])

    def put_json(self, key: str, value: Any) -> None:
        self.objects[key] = (json.dumps(value).encode(), "application/json")

    def get_json(self, key: str) -> Any | None:
        obj = self.objects.get(key)
        return json.loads(obj[0]) if obj else None

    def copy(self, src_key: str, dst_key: str) -> None:
        self.objects[dst_key] = self.objects[src_key]


class DirStorage(MemoryStorage):
    """Filesystem-backed Storage for local runs (`scripts/submit_local.py --local`)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ValueError(f"bad key {key}")
        return p

    def put_file(self, path: Path, key: str, content_type_: str | None = None) -> int:
        dest = self._p(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Path(path).read_bytes())
        return dest.stat().st_size

    def get_file(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._p(key).read_bytes())

    def put_json(self, key: str, value: Any) -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value))

    def get_json(self, key: str) -> Any | None:
        p = self._p(key)
        return json.loads(p.read_text()) if p.exists() else None

    def copy(self, src_key: str, dst_key: str) -> None:
        self.put_file(self._p(src_key), dst_key)
