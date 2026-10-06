import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import UploadFile
from pydantic import BaseModel, field_serializer

from app.config import get_settings
from app.core.exceptions import ValidationError

logger = logging.getLogger(__name__)
settings = get_settings()

_using_local = False


def _trim(url: str) -> str:
    return url.rstrip("/")


def _files_base() -> str:
    explicit = _trim(settings.files_base_url or "")
    if _using_local:
        bucket_suffix = f"/{settings.s3_bucket}"
        if explicit.endswith(bucket_suffix):
            return "http://localhost:8000"
        return explicit or "http://localhost:8000"
    if explicit:
        return explicit
    endpoint = _trim(settings.s3_endpoint_url or "")
    if endpoint:
        return f"{endpoint}/{settings.s3_bucket}"
    return "http://localhost:8000"


def public_file_url(stored: str | None) -> str:
    if stored is None:
        return ""
    value = stored.strip()
    if not value or value == "#":
        return value
    if value.startswith(("blob:", "data:")):
        return value

    base = _files_base()
    endpoint = _trim(settings.s3_endpoint_url or "")
    bucket = settings.s3_bucket

    if value.startswith(("http://", "https://")):
        prefixes = []
        if endpoint:
            prefixes.append(f"{endpoint}/{bucket}")
            prefixes.append(endpoint)
        docker_minio = "http://minio:9000"
        prefixes.append(f"{docker_minio}/{bucket}")
        prefixes.append(docker_minio)
        for prefix in prefixes:
            if value == prefix or value.startswith(prefix + "/"):
                rest = value[len(prefix) :].lstrip("/")
                if rest.startswith(f"{bucket}/"):
                    rest = rest[len(bucket) + 1 :]
                if rest.startswith("uploads/"):
                    rest = rest[len("uploads/") :]
                return f"{base}/{rest}" if rest else base
        return value

    if base.startswith("/") and (value == base or value.startswith(base + "/")):
        return value

    path = value[1:] if value.startswith("/") else value
    if path.startswith("uploads/"):
        path = path[len("uploads/") :]

    if _using_local:
        return f"{base}/uploads/{path}"
    return f"{base}/{path}"


def storage_key_from_url(value: str | None) -> str:
    if value is None:
        return ""
    url = value.strip()
    if not url or url == "#" or url.startswith(("blob:", "data:")):
        return url

    prefixes = [_files_base()]
    endpoint = _trim(settings.s3_endpoint_url or "")
    if endpoint:
        prefixes.append(f"{endpoint}/{settings.s3_bucket}")
    prefixes.append(f"http://minio:9000/{settings.s3_bucket}")

    for prefix in prefixes:
        if prefix and url.startswith(prefix + "/"):
            url = url[len(prefix) + 1 :]
            break
    else:
        if url.startswith(("http://", "https://")):
            return url

    url = url.lstrip("/")
    if url.startswith("uploads/"):
        url = url[len("uploads/") :]
    return url


class FileUrlMixin(BaseModel):
    @field_serializer("file_url", check_fields=False)
    def serialize_file_url(self, value: str) -> str:
        return public_file_url(value)


class StorageService:
    def __init__(self) -> None:
        self._s3 = None
        self._local_dir = Path(settings.local_upload_dir)
        self._local_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_bucket(self, s3) -> None:
        bucket = settings.s3_bucket
        try:
            s3.head_bucket(Bucket=bucket)
        except Exception:
            s3.create_bucket(Bucket=bucket)
        policy = json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "PublicReadGetObject",
                        "Effect": "Allow",
                        "Principal": {"AWS": ["*"]},
                        "Action": ["s3:GetObject"],
                        "Resource": [f"arn:aws:s3:::{bucket}/*"],
                    }
                ],
            }
        )
        try:
            s3.put_bucket_policy(Bucket=bucket, Policy=policy)
        except Exception as exc:
            logger.warning("Could not set public-read bucket policy: %s", exc)

    def _get_s3(self):
        global _using_local
        if self._s3 is not None:
            return self._s3
        try:
            import boto3
            from botocore.client import Config

            client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                region_name=settings.s3_region,
                use_ssl=settings.s3_use_ssl,
                config=Config(signature_version="s3v4"),
            )
            self._ensure_bucket(client)
            self._s3 = client
            _using_local = False
            return self._s3
        except Exception as exc:
            logger.warning("S3 unavailable, using local storage: %s", exc)
            _using_local = True
            return None

    async def validate_upload(self, file: UploadFile) -> None:
        if not file.content_type or file.content_type not in settings.allowed_mime_types_list:
            raise ValidationError(f"File type not allowed: {file.content_type}")
        content = await file.read()
        await file.seek(0)
        if len(content) > settings.max_upload_bytes:
            raise ValidationError(f"File exceeds {settings.max_upload_size_mb}MB limit")

    async def upload(self, file: UploadFile, prefix: str = "uploads") -> tuple[str, str]:
        await self.validate_upload(file)
        ext = Path(file.filename or "file").suffix
        key = f"{prefix}/{uuid.uuid4().hex}{ext}"
        content = await file.read()
        content_type = file.content_type or "application/octet-stream"

        s3 = self._get_s3()
        if s3:
            s3.put_object(
                Bucket=settings.s3_bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
            )
            return key, public_file_url(key)

        local_path = self._local_dir / key.replace("/", os.sep)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content)
        return key, public_file_url(key)


storage_service = StorageService()
