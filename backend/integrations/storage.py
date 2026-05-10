from __future__ import annotations

import hashlib
import hmac
import os
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlsplit

import httpx

from backend.utils.formatting import mask_phone
from backend.utils.logging import get_logger

logger = get_logger("backend.integrations.storage")


@dataclass(frozen=True)
class StorageUploadResult:
    storage_key: str
    file_size: int
    status: str
    error_message: Optional[str] = None


class S3StorageProvider:
    def __init__(
        self,
        *,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        bucket: Optional[str] = None,
        region: Optional[str] = None,
        force_path_style: bool = True,
    ) -> None:
        self.endpoint = (endpoint if endpoint is not None else os.environ.get("S3_ENDPOINT", "")).rstrip("/")
        self.access_key = access_key if access_key is not None else os.environ.get("S3_ACCESS_KEY", "")
        self.secret_key = secret_key if secret_key is not None else os.environ.get("S3_SECRET_KEY", "")
        self.bucket = bucket if bucket is not None else os.environ.get("S3_BUCKET", "")
        self.region = region if region is not None else os.environ.get("S3_REGION", "ap-south-1")
        self.force_path_style = force_path_style

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.access_key and self.secret_key and self.bucket and self.region)

    def livekit_s3_config(self) -> dict:
        self._require_configured()
        return {
            "access_key": self.access_key,
            "secret": self.secret_key,
            "bucket": self.bucket,
            "region": self.region,
            "endpoint": self.endpoint,
            "force_path_style": self.force_path_style,
        }

    def object_url(self, storage_key: str) -> str:
        self._require_configured()
        return self._object_url(storage_key)

    async def upload_file(
        self,
        *,
        local_path: str,
        storage_key: str,
        content_type: str = "audio/wav",
        timeout_seconds: float = 30.0,
        retries: int = 1,
    ) -> StorageUploadResult:
        self._require_configured()
        path = Path(local_path)
        if not path.exists() or not path.is_file():
            return StorageUploadResult(storage_key=storage_key, file_size=0, status="failed", error_message="file_missing")

        signed_url = self.generate_signed_url(storage_key, method="PUT", expires_seconds=900)
        file_size = path.stat().st_size
        last_error = None

        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                    response = await client.put(
                        signed_url,
                        content=_read_file_chunks(path),
                        headers={"Content-Type": content_type},
                    )
                if response.status_code < 400:
                    return StorageUploadResult(storage_key=storage_key, file_size=file_size, status="uploaded")
                last_error = f"http_{response.status_code}"
            except httpx.TimeoutException:
                last_error = "timeout"
            except httpx.RequestError as exc:
                last_error = type(exc).__name__

            logger.warning(
                "recording.upload.retry" if attempt < retries else "recording.upload.failed",
                extra={
                    "storage_key": _safe_storage_key(storage_key),
                    "attempt": attempt + 1,
                    "error": last_error,
                },
            )

        return StorageUploadResult(
            storage_key=storage_key,
            file_size=file_size,
            status="failed",
            error_message=last_error,
        )

    def generate_signed_url(
        self,
        storage_key: str,
        *,
        method: str = "GET",
        expires_seconds: int = 3600,
    ) -> str:
        self._require_configured()
        if expires_seconds < 1 or expires_seconds > 604800:
            raise ValueError("expires_seconds must be between 1 and 604800")

        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        credential_scope = f"{datestamp}/{self.region}/s3/aws4_request"
        credential = f"{self.access_key}/{credential_scope}"
        object_url = self._object_url(storage_key)
        parsed = urlsplit(object_url)
        host = parsed.netloc
        canonical_uri = parsed.path or "/"
        query_pairs = [
            ("X-Amz-Algorithm", "AWS4-HMAC-SHA256"),
            ("X-Amz-Credential", credential),
            ("X-Amz-Date", amz_date),
            ("X-Amz-Expires", str(expires_seconds)),
            ("X-Amz-SignedHeaders", "host"),
        ]
        canonical_query = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(query_pairs))
        canonical_headers = f"host:{host}\n"
        signed_headers = "host"
        canonical_request = "\n".join(
            [
                method.upper(),
                canonical_uri,
                canonical_query,
                canonical_headers,
                signed_headers,
                "UNSIGNED-PAYLOAD",
            ]
        )
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signature = hmac.new(
            _signing_key(self.secret_key, datestamp, self.region),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{object_url}?{canonical_query}&X-Amz-Signature={signature}"

    def _object_url(self, storage_key: str) -> str:
        endpoint = self.endpoint.rstrip("/")
        key = quote(storage_key.lstrip("/"), safe="/")
        if self.force_path_style:
            return f"{endpoint}/{quote(self.bucket, safe='')}/{key}"
        parsed = urlsplit(endpoint)
        return f"{parsed.scheme}://{quote(self.bucket, safe='')}.{parsed.netloc}/{key}"

    def _require_configured(self) -> None:
        if not self.configured:
            raise RuntimeError("s3_storage_not_configured")


def build_recording_storage_key(
    *,
    tenant_id: Optional[str],
    call_id: str,
    filename: str = "recording.wav",
) -> str:
    tenant_part = _path_part(tenant_id or "legacy")
    call_part = _path_part(call_id)
    return f"{tenant_part}/{call_part}/{filename}"


def cleanup_local_file(local_path: str) -> bool:
    try:
        path = Path(local_path)
        if path.exists() and path.is_file():
            path.unlink()
            return True
    except Exception as exc:
        logger.warning("recording.cleanup.failed", extra={"error_type": type(exc).__name__})
    return False


async def _read_file_chunks(path: Path, chunk_size: int = 1024 * 1024):
    with path.open("rb") as handle:
        while True:
            chunk = await asyncio.to_thread(handle.read, chunk_size)
            if not chunk:
                break
            yield chunk


def _path_part(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in str(value))
    return safe.strip("-")[:96] or "unknown"


def _safe_storage_key(storage_key: str) -> str:
    parts = storage_key.split("/")
    if len(parts) >= 3:
        return f"{mask_phone(parts[0])}/{parts[1][:12]}/{parts[-1]}"
    return storage_key[-80:]


def _signing_key(secret_key: str, datestamp: str, region: str) -> bytes:
    key = ("AWS4" + secret_key).encode("utf-8")
    date_key = hmac.new(key, datestamp.encode("utf-8"), hashlib.sha256).digest()
    region_key = hmac.new(date_key, region.encode("utf-8"), hashlib.sha256).digest()
    service_key = hmac.new(region_key, b"s3", hashlib.sha256).digest()
    return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()
