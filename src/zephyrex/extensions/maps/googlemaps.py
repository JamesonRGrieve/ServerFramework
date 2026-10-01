import json
import logging
from typing import Optional, Set

try:
    import googlemaps
    import polyline
except ImportError:
    import subprocess
    import sys

    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "googlemaps polyline"]
    )
    import googlemaps
    import polyline

from zephyrex.extensions.maps.PRV_Maps import AbstractMapsProvider


class GoogleMapsProvider(AbstractMapsProvider):
    """
    Google Maps provider implementation for maps services.
    Uses the Google Maps API for geocoding, directions, and location search.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "",
        api_version: str = "latest",
        extension_id: Optional[str] = None,
        **kwargs,
    ):
        # API key is required for Google Maps
        if not api_key:
            logging.error("API key is required for Google Maps")

        super().__init__(
            api_key=api_key,
            api_uri=api_uri,
            api_version=api_version,
            extension_id=extension_id,
            **kwargs,
        )

    def get_connection(self):
        """
        Get a connection to the Google Maps API.
        """
        try:
            if not self.api_key:
                return None

            client = googlemaps.Client(key=self.api_key)
            return client
        except Exception as e:
            logging.error(f"Error connecting to Google Maps API: {str(e)}")
            return None

    def search_location(self, query: str) -> str:
        """
        Search for a location using the Google Maps Places API.
        """
        logging.info(f"Searching for location: {query}")

        client = self.get_connection()
        if not client:
            return "Error connecting to Google Maps API: API key is required"

        try:
            # Use the places API to search for the query
            results = client.places(query=query, language="en")

            if not results or not results.get("results"):
                return f"No results found for '{query}'"

            # Format the results
            formatted_results = []
            for result in results.get("results", []):
                formatted_result = {
                    "name": result.get("name", "Unknown"),
                    "address": result.get("formatted_address", "Unknown"),
                    "place_id": result.get("place_id", ""),
                    "types": result.get("types", []),
                    "location": result.get("geometry", {}).get("location", {}),
                }
                formatted_results.append(formatted_result)

            return json.dumps(formatted_results, indent=2)

        except Exception as e:
            logging.error(f"Error searching for location: {str(e)}")
            return f"Error searching for location: {str(e)}"

    def get_directions(
        self, origin: str, destination: str, mode: str = "driving"
    ) -> str:
        """
        Get directions using the Google Maps Directions API.

        Args:
            origin: Origin location (address or latitude,longitude)
            destination: Destination location (address or latitude,longitude)
            mode: Transportation mode (driving, walking, bicycling, transit)
        """
        logging.info(f"Getting directions from {origin} to {destination}, mode: {mode}")

        client = self.get_connection()
        if not client:
            return "Error connecting to Google Maps API: API key is required"

        try:
            # Validate mode
            valid_modes = ["driving", "walking", "bicycling", "transit"]
            if mode not in valid_modes:
                mode = "driving"

            # Get directions from the API
            directions = client.directions(
                origin=origin, destination=destination, mode=mode, language="en"
            )

            if not directions:
                return f"No directions found from {origin} to {destination}"

            # Format the results
            route = directions[0]
            legs = route.get("legs", [])

            if not legs:
                return "No route legs found"

            leg = legs[0]  # Get the first leg
            steps = []

            # Format the steps
            for step in leg.get("steps", []):
                steps.append(
                    {
                        "instruction": step.get("html_instructions", "")
                        .replace("<b>", "")
                        .replace("</b>", "")
                        .replace("<div>", " - ")
                        .replace("</div>", ""),
                        "distance": step.get("distance", {}).get("text", ""),
                        "duration": step.get("duration", {}).get("text", ""),
                        "start_location": step.get("start_location", {}),
                        "end_location": step.get("end_location", {}),
                    }
                )

            # Create the formatted result
            formatted_result = {
                "summary": route.get("summary", ""),
                "distance": leg.get("distance", {}).get("text", ""),
                "duration": leg.get("duration", {}).get("text", ""),
                "start_address": leg.get("start_address", ""),
                "end_address": leg.get("end_address", ""),
                "steps": steps,
            }

            # Add polyline if available
            if route.get("overview_polyline", {}).get("points"):
                points = polyline.decode(
                    route.get("overview_polyline", {}).get("points")
                )
                formatted_result["polyline_points"] = points

            return json.dumps(formatted_result, indent=2)

        except Exception as e:
            logging.error(f"Error getting directions: {str(e)}")
            return f"Error getting directions: {str(e)}"

    def geocode(self, address: str) -> str:
        """
        Convert an address to coordinates using the Google Maps Geocoding API.
        """
        logging.info(f"Geocoding address: {address}")

        client = self.get_connection()
        if not client:
            return "Error connecting to Google Maps API: API key is required"

        try:
            # Geocode the address
            results = client.geocode(address=address, language="en")

            if not results:
                return f"No results found for '{address}'"

            # Format the results
            result = results[0]
            formatted_result = {
                "address": result.get("formatted_address", "Unknown"),
                "location": result.get("geometry", {}).get("location", {}),
                "place_id": result.get("place_id", ""),
                "types": result.get("types", []),
                "address_components": result.get("address_components", []),
            }

            return json.dumps(formatted_result, indent=2)

        except Exception as e:
            logging.error(f"Error geocoding address: {str(e)}")
            return f"Error geocoding address: {str(e)}"

    def reverse_geocode(self, lat: float, lng: float) -> str:
        """
        Convert coordinates to an address using the Google Maps Geocoding API.
        """
        logging.info(f"Reverse geocoding coordinates: {lat}, {lng}")

        client = self.get_connection()
        if not client:
            return "Error connecting to Google Maps API: API key is required"

        try:
            # Reverse geocode the coordinates
            results = client.reverse_geocode((lat, lng), language="en")

            if not results:
                return f"No results found for coordinates {lat}, {lng}"

            # Format the results
            result = results[0]
            formatted_result = {
                "address": result.get("formatted_address", "Unknown"),
                "location": {"lat": lat, "lng": lng},
                "place_id": result.get("place_id", ""),
                "types": result.get("types", []),
                "address_components": result.get("address_components", []),
            }

            return json.dumps(formatted_result, indent=2)

        except Exception as e:
            logging.error(f"Error reverse geocoding: {str(e)}")
            return f"Error reverse geocoding: {str(e)}"

    def get_maps_type(self) -> str:
        return "Google Maps"

    def get_maps_classifications(self) -> Set[str]:
        return {"commercial", "routing", "geocoding", "satellite"}
