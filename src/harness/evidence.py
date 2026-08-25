"""Large evidence/artifact storage (diff snapshots, run artifacts) — Blob Storage."""

from __future__ import annotations

import os


class EvidenceStore:
    """Blob-backed store keyed by change id; container per repo is overkill in V1."""

    CONTAINER = "evidence"

    def _container(self):  # type: ignore[no-untyped-def]
        endpoint = os.environ.get("HARNESS_BLOB_ENDPOINT")
        if not endpoint:
            raise RuntimeError("HARNESS_BLOB_ENDPOINT not configured — evidence store unavailable")
        from azure.storage.blob.aio import ContainerClient

        return ContainerClient(f"{endpoint}", self.CONTAINER)

    async def put(self, name: str, content: str | bytes) -> str:
        async with self._container() as container:
            blob = container.get_blob_client(name)
            await blob.upload_blob(content, overwrite=True)
            return blob.url

    async def get_text(self, name: str) -> str:
        async with self._container() as container:
            blob = container.get_blob_client(name)
            downloader = await blob.download_blob()
            data = await downloader.readall()
            return data.decode("utf-8")
