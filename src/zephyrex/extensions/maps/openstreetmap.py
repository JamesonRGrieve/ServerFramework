import json
import logging
import urllib.parse
from typing import List, Optional, Set

try:
    import requests
except ImportError:
    import subprocess
    import sys

    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

from zephyrex.extensions.maps.PRV_Maps import AbstractMapsProvider


class OpenStreetMapProvider(AbstractMapsProvider):
    """
    OpenStreetMap provider implementation for maps services.
    Uses the Nominatim API for geocoding and the OSRM API for routing.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "",
        user_agent: str = "AGInfrastructure/1.0",
        extension_id: Optional[str] = None,
        **kwargs,
    ):
        # Override API URIs if not provided
        self.nominatim_api = api_uri or "https://nominatim.openstreetmap.org"
        self.osrm_api = api_uri or "https://router.project-osrm.org"

        super().__init__(
            api_key=api_key,
            api_uri=api_uri,
            user_agent=user_agent,
            extension_id=extension_id,
            **kwargs,
        )

    def get_connection(self):
        """
        Get a connection to the OpenStreetMap services.
        For OpenStreetMap, we don't maintain a persistent connection,
        but we set up a session with the appropriate headers.
        """
        try:
            session = requests.Session()
            session.headers.update({"User-Agent": self.user_agent})
            return session
        except Exception as e:
            logging.error(f"Error creating OpenStreetMap session: {str(e)}")
            return None

    def search_location(self, query: str) -> str:
        """
        Search for a location using the Nominatim API.
        """
        logging.info(f"Searching for location: {query}")

        session = self.get_connection()
        if not session:
            return "Error connecting to OpenStreetMap Nominatim API"

        try:
            # URL encode the query
            encoded_query = urllib.parse.quote(query)

            # Make the request to the Nominatim API
            response = session.get(
                f"{self.nominatim_api}/search?q={encoded_query}&format=json&limit=5"
            )
            response.raise_for_status()
            results = response.json()

            if not results:
                return f"No results found for '{query}'"

            # Format the results
            formatted_results = []
            for result in results:
                formatted_results.append(
                    {
                        "name": result.get("display_name", "Unknown"),
                        "lat": float(result.get("lat", 0)),
                        "lon": float(result.get("lon", 0)),
                        "type": result.get("type", "Unknown"),
                        "importance": result.get("importance", 0),
                    }
                )

            return json.dumps(formatted_results, indent=2)

        except Exception as e:
            logging.error(f"Error searching for location: {str(e)}")
            return f"Error searching for location: {str(e)}"

    def get_directions(
        self, origin: str, destination: str, mode: str = "driving"
    ) -> str:
        """
        Get directions using the OSRM API.

        Args:
            origin: Origin location (address or latitude,longitude)
            destination: Destination location (address or latitude,longitude)
            mode: Transportation mode (driving, walking, bicycling, transit)
                  Maps to OSRM profiles: car, foot, bicycle
        """
        logging.info(f"Getting directions from {origin} to {destination}, mode: {mode}")

        session = self.get_connection()
        if not session:
            return "Error connecting to OpenStreetMap OSRM API"

        try:
            # Convert addresses to coordinates if needed
            origin_coords = self._get_coordinates(origin)
            destination_coords = self._get_coordinates(destination)

            if not origin_coords or not destination_coords:
                return "Error: Could not geocode one or both locations"

            # Map mode to OSRM profile
            profile = "car"
            if mode == "walking":
                profile = "foot"
            elif mode == "bicycling":
                profile = "bike"

            # Make the request to the OSRM API
            response = session.get(
                f"{self.osrm_api}/route/v1/{profile}/{origin_coords[1]},{origin_coords[0]};{destination_coords[1]},{destination_coords[0]}?overview=full&steps=true"
            )
            response.raise_for_status()
            result = response.json()

            if result.get("code") != "Ok":
                return f"Error: {result.get('message', 'Unknown error')}"

            # Format the results
            routes = result.get("routes", [])
            if not routes:
                return "No routes found"

            route = routes[0]
            distance = route.get("distance", 0) / 1000  # Convert to km
            duration = route.get("duration", 0) / 60  # Convert to minutes

            # Extract and format steps
            steps = []
            for leg in route.get("legs", []):
                for step in leg.get("steps", []):
                    steps.append(
                        {
                            "instruction": step.get("maneuver", {}).get(
                                "instruction", ""
                            ),
                            "distance": step.get("distance", 0) / 1000,  # Convert to km
                            "duration": step.get("duration", 0)
                            / 60,  # Convert to minutes
                        }
                    )

            formatted_result = {
                "distance": f"{distance:.2f} km",
                "duration": f"{duration:.2f} minutes",
                "steps": steps,
            }

            return json.dumps(formatted_result, indent=2)

        except Exception as e:
            logging.error(f"Error getting directions: {str(e)}")
            return f"Error getting directions: {str(e)}"

    def geocode(self, address: str) -> str:
        """
        Convert an address to coordinates using the Nominatim API.
        """
        logging.info(f"Geocoding address: {address}")

        session = self.get_connection()
        if not session:
            return "Error connecting to OpenStreetMap Nominatim API"

        try:
            # URL encode the address
            encoded_address = urllib.parse.quote(address)

            # Make the request to the Nominatim API
            response = session.get(
                f"{self.nominatim_api}/search?q={encoded_address}&format=json&limit=1"
            )
            response.raise_for_status()
            results = response.json()

            if not results:
                return f"No results found for '{address}'"

            result = results[0]
            formatted_result = {
                "address": result.get("display_name", "Unknown"),
                "lat": float(result.get("lat", 0)),
                "lon": float(result.get("lon", 0)),
                "type": result.get("type", "Unknown"),
                "osm_id": result.get("osm_id", "Unknown"),
            }

            return json.dumps(formatted_result, indent=2)

        except Exception as e:
            logging.error(f"Error geocoding address: {str(e)}")
            return f"Error geocoding address: {str(e)}"

    def reverse_geocode(self, lat: float, lng: float) -> str:
        """
        Convert coordinates to an address using the Nominatim API.
        """
        logging.info(f"Reverse geocoding coordinates: {lat}, {lng}")

        session = self.get_connection()
        if not session:
            return "Error connecting to OpenStreetMap Nominatim API"

        try:
            # Make the request to the Nominatim API
            response = session.get(
                f"{self.nominatim_api}/reverse?lat={lat}&lon={lng}&format=json"
            )
            response.raise_for_status()
            result = response.json()

            if not result:
                return f"No results found for coordinates {lat}, {lng}"

            formatted_result = {
                "address": result.get("display_name", "Unknown"),
                "lat": float(lat),
                "lon": float(lng),
                "type": result.get("type", "Unknown"),
                "address_details": result.get("address", {}),
            }

            return json.dumps(formatted_result, indent=2)

        except Exception as e:
            logging.error(f"Error reverse geocoding: {str(e)}")
            return f"Error reverse geocoding: {str(e)}"

    def _get_coordinates(self, location: str) -> Optional[List[float]]:
        """
        Helper method to get coordinates from a location string.
        The location can be either an address or a "lat,lng" string.

        Returns:
            A list [lat, lng] or None if geocoding failed
        """
        # Check if location is already coordinates
        if "," in location:
            try:
                lat, lng = location.split(",")
                return [float(lat.strip()), float(lng.strip())]
            except (ValueError, TypeError):
                pass

        # Otherwise geocode the address
        try:
            geocode_result = self.geocode(location)
            geocode_data = json.loads(geocode_result)
            return [geocode_data.get("lat"), geocode_data.get("lon")]
        except Exception:
            return None

    def get_maps_type(self) -> str:
        return "OpenStreetMap"

    def get_maps_classifications(self) -> Set[str]:
        return {"open_source", "routing", "geocoding"}
