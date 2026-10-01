import logging
from typing import Any, Dict, Optional

from zephyrex.extensions.media.PRV_Media import AbstractMediaProvider

AMAZON_API_BASE_URL = "https://api.amazon.com/v1"


class AmazonProvider(AbstractMediaProvider):
    """
    Media provider backed by Amazon Prime Video.

    A lightweight, directly-instantiated client. Search, info, and
    recommendation lookups are mocked pending a real Amazon Prime Video API
    integration; rental and purchase history follow the same shape.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_uri = self.settings.get("amazon_api_uri", AMAZON_API_BASE_URL)
        self.commands.update(
            {
                "Get Amazon Rentals": self.get_rentals,
                "Get Amazon Purchases": self.get_purchases,
            }
        )

    def get_platform_name(self) -> str:
        return "Amazon Prime Video"

    def search(self, query: str, service: Optional[str] = None) -> Dict[str, Any]:
        logging.info(f"Searching Amazon Prime Video for: {query}")

        return {
            "service": "Amazon Prime Video",
            "query": query,
            "results": [
                {
                    "id": "amzn123",
                    "title": f"Amazon Show about {query}",
                    "type": "series",
                },
                {
                    "id": "amzn456",
                    "title": f"Amazon Movie about {query}",
                    "type": "movie",
                },
            ],
        }

    def get_info(self, media_id: str) -> Dict[str, Any]:
        logging.info(f"Getting info for Amazon Prime Video item: {media_id}")

        return {
            "id": media_id,
            "title": f"Amazon Content {media_id}",
            "description": "This is a sample description for Amazon content.",
            "year": 2023,
            "rating": 4.5,
        }

    def get_recommendations(
        self, user_id: str, genre: Optional[str] = None
    ) -> Dict[str, Any]:
        logging.info(f"Getting Amazon Prime Video recommendations for user: {user_id}")

        filter_text = f" in {genre} genre" if genre else ""
        return {
            "service": "Amazon Prime Video",
            "user_id": user_id,
            "genre": genre,
            "recommendations": [
                {
                    "id": "amzn789",
                    "title": f"Recommended Show{filter_text}",
                    "type": "series",
                },
                {
                    "id": "amzn012",
                    "title": f"Recommended Movie{filter_text}",
                    "type": "movie",
                },
            ],
        }

    def get_rentals(self, user_id: str) -> Dict[str, Any]:
        logging.info(f"Getting rental history for user: {user_id}")

        return {
            "user_id": user_id,
            "rentals": [
                {
                    "id": "amzn345",
                    "title": "Rented Movie 1",
                    "rental_date": "2023-01-15",
                },
                {
                    "id": "amzn678",
                    "title": "Rented Movie 2",
                    "rental_date": "2023-02-20",
                },
            ],
        }

    def get_purchases(self, user_id: str) -> Dict[str, Any]:
        logging.info(f"Getting purchase history for user: {user_id}")

        return {
            "user_id": user_id,
            "purchases": [
                {
                    "id": "amzn901",
                    "title": "Purchased Movie 1",
                    "purchase_date": "2023-03-10",
                },
                {
                    "id": "amzn234",
                    "title": "Purchased Movie 2",
                    "purchase_date": "2023-04-05",
                },
            ],
        }
