from typing import Dict
from copy import deepcopy
from abc import ABC, abstractmethod
import threading
import asyncio
import re


class DownloadError(Exception):
    pass


class ExistentDownloadError(DownloadError):
    pass


class Download(ABC):
    def __init__(self):
        self._registered = False
        self._id = self._generate_id()

    def __init_subclass__(cls):
        super().__init_subclass__()
        if not hasattr(cls, "provider_name"):
            raise TypeError(
                f"{cls.__name__} is missing required class attribute 'provider_name'."
            )
        if not hasattr(cls, "download_dir"):
            raise TypeError(
                f"{cls.__name__} is missing required class attribute 'download_dir'."
            )

    @staticmethod
    def str_to_filename(text: str):
        return re.sub(r"[^a-zA-Z0-9.]", "_", text)

    @abstractmethod
    def _generate_id(self) -> str:
        pass

    @property
    def id(self) -> str:
        return self._id

    @property
    def registered(self) -> bool:
        return self._registered

    def register(self) -> str:
        self._id = self._generate_id()
        self._registered = True
        return self._id

    @abstractmethod
    async def _start(self, *args, **kwargs):
        pass

    async def start(self, *args, **kwargs):
        if not self._registered:
            raise DownloadError("Download must be registered before starting.")
        if self._id is None:
            raise DownloadError(
                "Download has no ID. Did you register it on DonwloadManager?"
            )
        await self._start(*args, **kwargs)

    @abstractmethod
    def to_dict(self):
        pass

    @classmethod
    @abstractmethod
    def list_all(cls):
        pass


class DownloadManager:
    _instance = None
    _instantiation_lock = threading.Lock()
    _lock = asyncio.Lock()
    _downloads: Dict[str, Download] = {}

    def __new__(cls):
        if cls._instance is None:
            with cls._instantiation_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "_initialized"):
            self._initialized = True

    @property
    async def downloads(self):
        return deepcopy(self._downloads)

    def get_download(self, download_id: str = None) -> Download:
        download_entry = self._downloads.get(download_id)
        return download_entry

    async def register(self, download: Download, force: bool = False):
        async with self._lock:
            existent_download = self._downloads.get(download.id)
            if existent_download:
                if not force:
                    raise ExistentDownloadError(
                        f"Download {download.id} already exists. Use get_download method instead."
                    )
                elif existent_download.status in ["downloading"]:
                    raise ExistentDownloadError(
                        f"Download {existent_download.id} in progress: {existent_download.progress}% completed."
                        + " Wait until it completes to force it again."
                    )
            download.register()
            self._downloads[download.id] = download

    async def delete_download(self):
        # TODO: Remove download entry from self._downloads to avoid polution
        pass
