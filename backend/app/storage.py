from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import boto3
from cryptography.fernet import Fernet

from .settings import Settings, StorageBackendName


class StorageBackend(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None: ...

    @abstractmethod
    def list_older_than(self, prefix: str, older_than: datetime) -> Iterable[str]: ...


class EncryptedStorage(StorageBackend):
    def __init__(self, backend: StorageBackend, key: str) -> None:
        self._backend = backend
        self._fernet = Fernet(key.encode("utf-8"))

    def put(self, key: str, data: bytes) -> None:
        self._backend.put(key, self._fernet.encrypt(data))

    def get(self, key: str) -> bytes:
        return self._fernet.decrypt(self._backend.get(key))

    def delete(self, key: str) -> None:
        self._backend.delete(key)

    def delete_prefix(self, prefix: str) -> None:
        self._backend.delete_prefix(prefix)

    def list_older_than(self, prefix: str, older_than: datetime) -> Iterable[str]:
        return self._backend.list_older_than(prefix, older_than)


class LocalStorage(StorageBackend):
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.base_dir / key).resolve()
        if not path.is_relative_to(self.base_dir.resolve()):
            raise ValueError("storage key escapes the storage root")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def delete_prefix(self, prefix: str) -> None:
        root = self._path(prefix)
        if not root.exists():
            return
        for file in sorted(root.rglob("*"), reverse=True):
            if file.is_file():
                file.unlink()
            else:
                file.rmdir()
        root.rmdir()

    def list_older_than(self, prefix: str, older_than: datetime) -> Iterable[str]:
        root = self._path(prefix)
        if not root.exists():
            return []
        if older_than.tzinfo is None:
            older_than = older_than.replace(tzinfo=UTC)
        threshold = older_than.timestamp()
        keys: list[str] = []
        for file in root.rglob("*"):
            if file.is_file() and file.stat().st_mtime < threshold:
                keys.append(str(file.relative_to(self.base_dir)))
        return keys


class S3Storage(StorageBackend):
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.NoSuchKey as exc:
            raise FileNotFoundError(key) from exc
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> None:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            contents = page.get("Contents", [])
            if not contents:
                continue
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": item["Key"]} for item in contents]},
            )

    def list_older_than(self, prefix: str, older_than: datetime) -> Iterable[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                if item["LastModified"] < older_than:
                    yield item["Key"]


def build_storage(settings: Settings) -> EncryptedStorage:
    if settings.storage_backend == StorageBackendName.S3:
        backend: StorageBackend = S3Storage(settings)
    else:
        backend = LocalStorage(settings.storage_dir)
    return EncryptedStorage(backend, settings.file_encryption_key)
