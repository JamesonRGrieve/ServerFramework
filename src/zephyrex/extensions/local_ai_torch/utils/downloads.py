import os
import datetime
import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Dict, Any

import tqdm
from huggingface_hub import snapshot_download

from zephyrex.lib.Environment import env
from zephyrex.extensions.local_ai.utils.downloads import Download


class TorchDownload(Download):
    provider_name = "torch"
    download_dir = Path(env("TORCH_MODELS_DIR"))

    def __init__(
        self,
        repo_id: str,
        revision: Optional[str] = None,
        file_pattern: Optional[str] = None,
    ):
        self.repo_id = repo_id
        self.revision = revision or "main"
        self.file_pattern = file_pattern
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
        return self.str_to_filename(
            f"{self.provider_name}_{self.repo_id}_{self.revision}"
        )

    @contextmanager
    def custom_progress_bar(self):
        """Context manager to handle tqdm progress bar with custom update."""
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
        """Start the download process."""
        async with self._lock:
            try:
                self.status = "downloading"
                self.message = "Downloading from Hugging Faces. Progress tracking might behave unexpectedly (TODO)"  # TODO: Progress tracking misbehaving due to multiple files

                model_dir = self.download_dir / self.id
                model_dir.mkdir(parents=True, exist_ok=True)

                with self.custom_progress_bar():
                    loop = asyncio.get_event_loop()
                    model_path = await loop.run_in_executor(
                        None,
                        lambda: snapshot_download(
                            repo_id=self.repo_id,
                            revision=self.revision,
                            local_dir=model_dir,  # Use the model-specific directory
                            allow_patterns=self.file_pattern,
                            **kwargs,
                        ),
                    )

                self.status = "completed"
                self.progress = 100.0
                self.message = "Download completed successfully."
                self.model_path = model_path
                return model_path

            except Exception as e:
                self.status = "failed"
                self.message = f"Download failed: {str(e)}"
                self.error = str(e)
                raise

    def to_dict(self) -> Dict[str, Any]:
        """Convert the download entry to a JSON-serializable dictionary."""
        return {
            "id": self.id,
            "repo_id": self.repo_id,
            "revision": self.revision,
            "file_pattern": self.file_pattern,
            "progress": self.progress,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message": self.message,
            "model_path": str(self.model_path) if self.model_path else None,
            "error": self.error,
        }

    @classmethod
    def list_all(cls):
        downloaded_models = []
        model_extensions = [".bin", ".safetensors", ".pth", ".pt", ".index.json"]

        for model_name in os.listdir(cls.download_dir):
            model_path = os.path.join(cls.download_dir, model_name)
            if not os.path.isdir(model_path):
                continue

            model_files = []
            total_size = 0
            last_modified = 0

            # Find all model files in the model directory
            for root, _, files in os.walk(model_path):
                for file in files:
                    if any(file.lower().endswith(ext) for ext in model_extensions):
                        file_path = os.path.join(root, file)

                        file_size = os.path.getsize(file_path)
                        file_mtime = os.path.getmtime(file_path)

                        model_files.append(
                            {
                                "filename": file,
                                "path": file_path,
                                "size_mb": round(file_size / (1024 * 1024), 2),
                                "modified": datetime.datetime.fromtimestamp(
                                    file_mtime
                                ).isoformat(),
                            }
                        )

                        total_size += file_size
                        last_modified = max(last_modified, file_mtime)

            if model_files:  # Only add if we found model files
                downloaded_models.append(
                    {
                        "name": model_name,
                        "path": model_path,
                        "files": model_files,
                        "total_size_mb": round(total_size / (1024 * 1024), 2),
                        "file_count": len(model_files),
                        "last_modified": (
                            datetime.datetime.fromtimestamp(last_modified).isoformat()
                            if last_modified > 0
                            else None
                        ),
                    }
                )

        return downloaded_models
