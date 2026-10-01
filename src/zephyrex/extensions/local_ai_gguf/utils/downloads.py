import os
import datetime
import asyncio
from contextlib import contextmanager
from pathlib import Path
import tqdm
from huggingface_hub import hf_hub_download

from zephyrex.lib.Environment import env
from zephyrex.extensions.local_ai.utils.downloads import Download


class GGUFDownload(Download):
    provider_name = "gguf"
    download_dir = Path(env("GGUF_MODELS_DIR"))

    def __init__(self, repo_id: str, filename: str):
        self.repo_id = repo_id
        self.filename = filename
        self.progress = 0
        self.status = "pending"
        self.created_at = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
        self.updated_at = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
        self.message = "Download entry created."
        self._lock = asyncio.Lock()
        self.model_path = None
        self.error = None
        super().__init__()

    def _generate_id(self):
        return f"{self.provider_name}/{self.repo_id}/{self.filename}"

    @contextmanager
    def custom_progress_bar(self):
        original_update = tqdm.tqdm.update

        def custom_update(self_, n=1):
            original_update(self_, n)
            if self_.total:
                percent = 100 * self_.n / self_.total
                self.progress = round(percent, 2)
                self.updated_at = datetime.datetime.now(
                    tz=datetime.timezone.utc
                ).isoformat()

        try:
            tqdm.tqdm.update = custom_update
            yield
        finally:
            tqdm.tqdm.update = original_update

    async def _start(self, **kwargs):
        async with self._lock:
            try:
                self.status = "downloading"
                self.message = "Starting download from Hugging Face Hub..."

                with self.custom_progress_bar():
                    loop = asyncio.get_event_loop()
                    model_path = await loop.run_in_executor(
                        None,
                        lambda: hf_hub_download(
                            repo_id=self.repo_id,
                            filename=self.filename,
                            local_dir=self.download_dir,
                            **kwargs,
                        ),
                    )

                self.status = "completed"
                self.progress = 100.00
                self.message = "Download completed."
                self.model_path = model_path
                return model_path
            except Exception as e:
                self.status = "failed"
                self.message = f"Download failed: {str(e)}"
                self.error = e

    def to_dict(self) -> dict:
        """Convert the download entry to a JSON-serializable dictionary.

        Returns:
            dict: A dictionary containing the download entry data.
        """
        return {
            "id": self.id,
            "repo_id": self.repo_id,
            "filename": self.filename,
            "progress": self.progress,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message": self.message,
            "model_path": str(self.model_path) if self.model_path else None,
            "error": str(self.error) if self.error else None,
        }

    @classmethod
    def list_all(cls):
        # Find downloaded models in configured directories
        downloaded_models = []

        for root, _, files in os.walk(cls.download_dir):
            for file in files:
                if file.lower().endswith(".gguf"):
                    file_path = os.path.join(root, file)

                    file_size = os.path.getsize(file_path)
                    downloaded_models.append(
                        {
                            "filename": file,
                            "path": file,
                            "size_mb": round(file_size / (1024 * 1024), 2),
                            "modified": datetime.datetime.fromtimestamp(
                                os.path.getmtime(file_path)
                            ).isoformat(),
                        }
                    )

        return downloaded_models
