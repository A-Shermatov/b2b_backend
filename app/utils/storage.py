import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from pathlib import Path
from urllib.parse import quote, unquote, urlencode

from fastapi import UploadFile
from pydantic import BaseModel, field_serializer

from app.config import get_settings
from app.core.exceptions import AppError, ValidationError

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


# Files that only the people involved may open: identity documents, deal files,
# dispute evidence and support attachments. They are never served from the public /uploads mount or a public
# bucket; links to them are short-lived signed URLs to GET /api/v1/files/<key>.
PRIVATE_PREFIXES = ("identity/", "contracts/", "disputes/", "support/")
PRIVATE_URL_TTL_SECONDS = 6 * 3600
FILES_ROUTE = "/files"


def is_private_key(key: str) -> bool:
    return key.startswith(PRIVATE_PREFIXES)


def _file_signature(key: str, expires: int) -> str:
    message = f"{key}\n{expires}".encode()
    secret = f"files:{settings.secret_key}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:40]


def signed_file_url(key: str, ttl_seconds: int = PRIVATE_URL_TTL_SECONDS) -> str:
    expires = int(time.time()) + ttl_seconds
    query = urlencode({"exp": expires, "sig": _file_signature(key, expires)})
    api = settings.public_api_url.rstrip("/")
    return f"{api}{settings.api_v1_prefix}{FILES_ROUTE}/{quote(key)}?{query}"


def verify_file_signature(key: str, expires: str | None, signature: str | None) -> bool:
    try:
        expires_at = int(expires or "")
    except ValueError:
        return False
    if expires_at < time.time() or not signature:
        return False
    return hmac.compare_digest(_file_signature(key, expires_at), signature)


def public_file_url(stored: str | None) -> str:
    url = _public_file_url(stored)
    if not url or url.startswith(("blob:", "data:")):
        return url
    key = storage_key_from_url(url)
    if key and not key.startswith(("http://", "https://")) and is_private_key(key):
        return signed_file_url(key)
    return url


def _public_file_url(stored: str | None) -> str:
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
        # FILES_BASE_URL may already point at the /uploads mount.
        if base.endswith("/uploads"):
            return f"{base}/{path}"
        return f"{base}/uploads/{path}"
    return f"{base}/{path}"


def storage_key_from_url(value: str | None) -> str:
    if value is None:
        return ""
    url = value.strip()
    if not url or url == "#" or url.startswith(("blob:", "data:")):
        return url

    files_route = f"{settings.api_v1_prefix}{FILES_ROUTE}"
    prefixes = [
        f"{settings.public_api_url.rstrip('/')}{files_route}",
        files_route,
        _files_base(),
    ]
    endpoint = _trim(settings.s3_endpoint_url or "")
    if endpoint:
        prefixes.append(f"{endpoint}/{settings.s3_bucket}")
    prefixes.append(f"http://minio:9000/{settings.s3_bucket}")

    for prefix in prefixes:
        if prefix and url.startswith(prefix + "/"):
            # Our own links may carry a signature or cache-buster: the key has none.
            url = unquote(url.split("?", 1)[0].split("#", 1)[0])
            break

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


def stored_file_key(value: str | None) -> str | None:
    """A file reference sent by a client, reduced to a key in our own storage.

    Logos and similar fields must point at files uploaded through our upload
    endpoints. Anything else (a foreign URL, an internal address, ``../`` paths)
    is refused: the server later reads these files itself (Telegram cards).
    """
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return ""
    key = storage_key_from_url(raw)
    if (
        not key
        or key.startswith(("http://", "https://", "blob:", "data:", "/", "\\"))
        or ".." in key.replace("\\", "/").split("/")
        or "://" in key
    ):
        raise ValidationError("Загрузите файл через форму загрузки")
    return key


def files_base_url() -> str:
    """Public base of our stored files (S3 bucket or the local /uploads mount)."""
    base = _files_base()
    return f"{base}/uploads" if _using_local and not base.endswith("/uploads") else base


class FileUrlMixin(BaseModel):
    @field_serializer("file_url", check_fields=False)
    def serialize_file_url(self, value: str) -> str:
        return public_file_url(value)


class StorageUnavailableError(AppError):
    def __init__(self) -> None:
        super().__init__(503, "Хранилище файлов недоступно, попробуйте позже", "storage_unavailable")


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
                        # Only public media; private prefixes (PRIVATE_PREFIXES) are
                        # read by the API after a signature check.
                        "Resource": [f"arn:aws:s3:::{bucket}/{prefix}*" for prefix in PUBLIC_PREFIXES],
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
        if settings.storage_backend == "local":
            _using_local = True
            return None
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
            if settings.storage_backend == "s3":
                logger.error("S3 unavailable and STORAGE_BACKEND=s3: %s", exc)
                raise StorageUnavailableError() from exc
            # Files written now land on this container's disk only; they disappear
            # with it and are not in the bucket once S3 is back. Say so loudly.
            logger.error(
                "S3 unavailable, writing to the local folder (STORAGE_BACKEND=auto): %s", exc
            )
            _using_local = True
            return None

    async def _read_limited(self, file: UploadFile) -> bytes:
        """Read the upload in chunks; never keep more than the size limit in memory."""
        limit = settings.max_upload_bytes
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ValidationError(f"Файл больше {settings.max_upload_size_mb} МБ")
            chunks.append(chunk)
        await file.seek(0)
        return b"".join(chunks)

    async def validate_upload(self, file: UploadFile) -> tuple[bytes, str, str]:
        """Check size and the real file type; return (content, mime, extension).

        The type comes from the file signature, not from the client's Content-Type
        or file name: an HTML/SVG page sent as "image/png" or "logo.html" is refused,
        and the stored extension always matches the real content.
        """
        content = await self._read_limited(file)
        if not content:
            raise ValidationError("Файл пустой")
        declared = (file.content_type or "").split(";", 1)[0].strip().lower()
        detected = sniff_file_type(content, declared)
        if detected is None:
            raise ValidationError(UNSUPPORTED_FILE_MESSAGE)
        mime, ext = detected
        allowed = {m.lower() for m in settings.allowed_mime_types_list}
        if mime not in allowed and not (MIME_ALIASES.get(mime, set()) & allowed):
            raise ValidationError(UNSUPPORTED_FILE_MESSAGE)
        # A picture saved with the wrong extension (WEBP named .jpg) is still a
        # picture: it is stored under its real type. Anything else claiming to be
        # something it is not is refused.
        image_for_image = declared.startswith("image/") and mime.startswith("image/")
        if (
            declared
            and declared != "application/octet-stream"
            and declared != mime
            and declared not in MIME_ALIASES.get(mime, set())
            and not image_for_image
        ):
            raise ValidationError("Тип файла не совпадает с его содержимым")
        return content, mime, ext

    async def upload(self, file: UploadFile, prefix: str = "uploads") -> tuple[str, str]:
        content, content_type, ext = await self.validate_upload(file)
        key = f"{prefix}/{uuid.uuid4().hex}{ext}"

        # boto3 and disk writes block: run them off the event loop, otherwise one
        # 50 MB upload stalls every request of the (single-process) API.
        s3 = await asyncio.to_thread(self._get_s3)
        if s3:
            await asyncio.to_thread(
                s3.put_object,
                Bucket=settings.s3_bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
                ContentDisposition="inline" if is_inline_type(content_type) else "attachment",
            )
            return key, public_file_url(key)

        local_path = self._local_dir / key.replace("/", os.sep)
        await asyncio.to_thread(_write_local, local_path, content)
        return key, public_file_url(key)


PUBLIC_PREFIXES = ("logos/", "catalog/", "certificates/", "avatars/", "reviews/", "rfqs/", "uploads/")


async def read_stored_file(key: str) -> bytes | None:
    """Bytes of a stored file (local folder first, then S3); None if absent."""
    root = Path(settings.local_upload_dir).resolve()
    local = (root / key).resolve()
    if local.is_relative_to(root) and await asyncio.to_thread(local.is_file):
        return await asyncio.to_thread(local.read_bytes)
    s3 = await asyncio.to_thread(storage_service._get_s3)
    if not s3:
        return None

    def _get() -> bytes | None:
        try:
            return s3.get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()
        except Exception:  # missing key or S3 error: the caller answers 404
            return None

    return await asyncio.to_thread(_get)


def _write_local(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


UNSUPPORTED_FILE_MESSAGE = (
    "Формат файла не поддерживается: загрузите PDF, изображение (JPG, PNG, WEBP, GIF), "
    "документ Word/Excel, видео MP4/WEBM или ZIP"
)
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_ALIASES: dict[str, set[str]] = {
    "image/jpeg": {"image/jpg", "image/pjpeg"},
    "application/zip": {"application/x-zip-compressed", "application/x-zip"},
}
INLINE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".mp4", ".webm"})
_MP4_BRANDS = (b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V ", b"dash", b"qt  ")


def is_inline_type(mime: str) -> bool:
    return mime.startswith(("image/", "video/")) or mime == "application/pdf"


def sniff_file_type(content: bytes, declared: str = "") -> tuple[str, str] | None:
    """(mime, extension) from the file signature, or None for anything else."""
    head = content[:16]
    if head.startswith(b"%PDF-"):
        return "application/pdf", ".pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if head[4:8] == b"ftyp" and head[8:12] in _MP4_BRANDS:
        return "video/mp4", ".mp4"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm", ".webm"
    if head.startswith(b"PK\x03\x04"):
        if declared == DOCX_MIME:
            return DOCX_MIME, ".docx"
        if declared == XLSX_MIME:
            return XLSX_MIME, ".xlsx"
        return "application/zip", ".zip"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        if declared == "application/msword":
            return "application/msword", ".doc"
        if declared == "application/vnd.ms-excel":
            return "application/vnd.ms-excel", ".xls"
        return None
    return None


storage_service = StorageService()
