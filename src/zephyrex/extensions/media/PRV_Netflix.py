import logging
from typing import Any, Dict, Optional

from zephyrex.extensions.media.PRV_Media import AbstractMediaProvider

NETFLIX_API_BASE_URL = "https://api.netflix.com/v1"


class NetflixProvider(AbstractMediaProvider):
    """
    Media provider backed by Netflix.

    A lightweight, directly-instantiated client. Search, info, and
    recommendation lookups are mocked pending a real Netflix API
    integration; watch history and "My List" management follow the same
    shape.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_uri = self.settings.get("netflix_api_uri", NETFLIX_API_BASE_URL)
        self.commands.update(
            {
                "Get Netflix Watch History": self.get_watch_history,
                "Get Netflix My List": self.get_my_list,
                "Add To Netflix My List": self.add_to_my_list,
            }
        )

    def get_platform_name(self) -> str:
        return "Netflix"

    def search(self, query: str, service: Optional[str] = None) -> Dict[str, Any]:
        logging.info(f"Searching Netflix for: {query}")

        return {
            "service": "Netflix",
            "query": query,
            "results": [
                {
                    "id": "nflx123",
                    "title": f"Netflix Show about {query}",
                    "type": "series",
                },
                {
                    "id": "nflx456",
                    "title": f"Netflix Movie about {query}",
                    "type": "movie",
                },
            ],
        }

    def get_info(self, media_id: str) -> Dict[str, Any]:
        logging.info(f"Getting info for Netflix item: {media_id}")

        return {
            "id": media_id,
            "title": f"Netflix Content {media_id}",
            "description": "This is a sample description for Netflix content.",
            "year": 2023,
            "rating": "TV-MA",
            "seasons": 3 if media_id.endswith("123") else None,
        }

    def get_recommendations(
        self, user_id: str, genre: Optional[str] = None
    ) -> Dict[str, Any]:
        logging.info(f"Getting Netflix recommendations for user: {user_id}")

        filter_text = f" in {genre} genre" if genre else ""
        return {
            "service": "Netflix",
            "user_id": user_id,
            "genre": genre,
            "recommendations": [
                {
                    "id": "nflx789",
                    "title": f"Recommended Show{filter_text}",
                    "type": "series",
                },
                {
                    "id": "nflx012",
                    "title": f"Recommended Movie{filter_text}",
                    "type": "movie",
                },
            ],
        }

    def get_watch_history(self, user_id: str, limit: int = 20) -> Dict[str, Any]:
        logging.info(f"Getting watch history for user: {user_id}")

        return {
            "user_id": user_id,
            "history": [
                {
                    "id": "nflx345",
                    "title": "Watched Show 1",
                    "last_watched": "2023-01-15",
                    "progress": 0.8,
                },
                {
                    "id": "nflx678",
                    "title": "Watched Movie 1",
                    "last_watched": "2023-02-20",
                    "progress": 1.0,
                },
            ][:limit],
        }

    def get_my_list(self, user_id: str) -> Dict[str, Any]:
        logging.info(f"Getting My List for user: {user_id}")

        return {
            "user_id": user_id,
            "items": [
                {"id": "nflx901", "title": "Saved Show 1", "added_date": "2023-03-10"},
                {"id": "nflx234", "title": "Saved Movie 1", "added_date": "2023-04-05"},
            ],
        }

    def add_to_my_list(self, user_id: str, media_id: str) -> Dict[str, Any]:
        logging.info(f"Adding item {media_id} to My List for user: {user_id}")

        return {
            "status": "success",
            "message": f"Added {media_id} to My List for user {user_id}",
        }
