import logging
from typing import Any, Dict, Optional

from zephyrex.extensions.media.PRV_Media import AbstractMediaProvider

IMDB_API_BASE_URL = "https://api.imdb.com/v1"


class IMDBProvider(AbstractMediaProvider):
    """
    Media provider backed by IMDB.

    A lightweight, directly-instantiated client. Search, info, and
    recommendation lookups are mocked pending a real IMDB API integration;
    ratings and reviews follow the same shape.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_uri = self.settings.get("imdb_api_uri", IMDB_API_BASE_URL)
        self.commands.update(
            {
                "Get IMDB Ratings": self.get_ratings,
                "Get IMDB Reviews": self.get_reviews,
            }
        )

    def get_platform_name(self) -> str:
        return "IMDB"

    def search(self, query: str, service: Optional[str] = None) -> Dict[str, Any]:
        logging.info(f"Searching IMDB for: {query}")

        return {
            "service": "IMDB",
            "query": query,
            "results": [
                {
                    "id": "tt1234567",
                    "title": f"Movie about {query}",
                    "type": "movie",
                    "year": 2022,
                },
                {
                    "id": "tt7654321",
                    "title": f"TV Show about {query}",
                    "type": "series",
                    "year": 2021,
                },
            ],
        }

    def get_info(self, media_id: str) -> Dict[str, Any]:
        logging.info(f"Getting info for IMDB item: {media_id}")

        return {
            "id": media_id,
            "title": f"IMDB Content {media_id}",
            "description": "This is a sample description for IMDB content.",
            "year": 2023,
            "rating": 7.8,
            "director": "Sample Director",
            "stars": ["Actor 1", "Actor 2", "Actor 3"],
        }

    def get_recommendations(
        self, user_id: str, genre: Optional[str] = None
    ) -> Dict[str, Any]:
        logging.info(f"Getting IMDB recommendations for user: {user_id}")

        filter_text = f" in {genre} genre" if genre else ""
        return {
            "service": "IMDB",
            "user_id": user_id,
            "genre": genre,
            "recommendations": [
                {
                    "id": "tt9876543",
                    "title": f"Recommended Movie{filter_text}",
                    "type": "movie",
                    "rating": 8.1,
                },
                {
                    "id": "tt3456789",
                    "title": f"Recommended Series{filter_text}",
                    "type": "series",
                    "rating": 9.0,
                },
            ],
        }

    def get_ratings(self, media_id: str) -> Dict[str, Any]:
        logging.info(f"Getting ratings for media item: {media_id}")

        return {
            "id": media_id,
            "imdb_rating": 7.6,
            "metacritic": 72,
            "rotten_tomatoes": 85,
            "user_votes": 45678,
        }

    def get_reviews(self, media_id: str, limit: int = 10) -> Dict[str, Any]:
        logging.info(f"Getting reviews for media item: {media_id}")

        return {
            "id": media_id,
            "total_reviews": 1234,
            "reviews": [
                {
                    "user": "user123",
                    "rating": 8,
                    "title": "Great movie",
                    "content": "I really enjoyed this film.",
                },
                {
                    "user": "user456",
                    "rating": 6,
                    "title": "Decent but flawed",
                    "content": "Had some good moments but overall disappointing.",
                },
            ][:limit],
        }
