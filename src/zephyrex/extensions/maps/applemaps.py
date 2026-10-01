import json
import logging
import time
import urllib.parse
from typing import Dict, Optional, Set

import jwt

try:
    import requests
except ImportError:
    import subprocess
    import sys

    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests pyjwt"])
    import requests
    import jwt

from zephyrex.extensions.maps.PRV_Maps import AbstractMapsProvider


class AppleMapsProvider(AbstractMapsProvider):
    """
    Apple Maps provider implementation for maps services.
    Uses the Apple MapKit JS API for geocoding and location search.

    Note: Apple Maps requires a MapKit JS key, which is different from a standard API key.
    It requires several steps to set up properly:
    1. Developer account with Apple
    2. MapKit JS Maps ID configured in developer account
    3. Private key for signing JWT tokens
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "",
        api_version: str = "latest",
        maps_id: str = "",
        team_id: str = "",
        key_id: str = "",
        private_key_path: str = "",
        private_key: str = "",
        extension_id: Optional[str] = None,
        **kwargs,
    ):
        self.maps_id = maps_id
        self.team_id = team_id
        self.key_id = key_id
        self.private_key_path = private_key_path
        self.private_key = private_key
        self.token = None
        self.token_expiry = 0

        # Base URL for MapKit JS API
        self.mapkit_base_url = api_uri or "https://maps-api.apple.com"

        super().__init__(
            api_key=api_key,
            api_uri=api_uri,
            api_version=api_version,
            extension_id=extension_id,
            **kwargs,
        )

    def get_connection(self):
        """
        Get a connection to the Apple Maps API.
        For Apple Maps, we don't maintain a persistent connection,
        but we set up a session with the appropriate JWT token.
        """
        try:
            # Create a session
            session = requests.Session()

            # Get a JWT token
            token = self._get_jwt_token()
            if not token:
                return None

            # Set the token in the headers
            session.headers.update(
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )

            return session
        except Exception as e:
            logging.error(f"Error creating Apple Maps session: {str(e)}")
            return None

    def search_location(self, query: str) -> str:
        """
        Search for a location using Apple Maps.
        Uses the MapKit JS Geocoder service.
        """
        logging.info(f"Searching for location: {query}")

        session = self.get_connection()
        if not session:
            return (
                "Error connecting to Apple Maps API: JWT token could not be generated"
            )

        try:
            # URL encode the query
            encoded_query = urllib.parse.quote(query)

            # Make the request to the MapKit JS API
            url = f"{self.mapkit_base_url}/v1/geocode?q={encoded_query}&lang=en"
            response = session.get(url)
            response.raise_for_status()
            results = response.json()

            if not results or not results.get("results"):
                return f"No results found for '{query}'"

            # Format the results
            formatted_results = []
            for result in results.get("results", []):
                formatted_result = {
                    "name": result.get("displayMapRegion", {}).get("name", "Unknown"),
                    "address": self._format_address(result.get("address", {})),
                    "coordinate": result.get("coordinate", {}),
                    "country": result.get("country", "Unknown"),
                    "countryCode": result.get("countryCode", "Unknown"),
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
        Get directions using Apple Maps.

        Note: Apple Maps does not provide a public Directions API.
        This is a limited implementation using the Geocoding API to get
        the coordinates and returning them with a notice about limitations.

        Args:
            origin: Origin location (address or latitude,longitude)
            destination: Destination location (address or latitude,longitude)
            mode: Transportation mode (not fully supported in this implementation)
        """
        logging.info(f"Getting directions from {origin} to {destination}, mode: {mode}")

        session = self.get_connection()
        if not session:
            return (
                "Error connecting to Apple Maps API: JWT token could not be generated"
            )

        try:
            # Get coordinates for origin and destination
            origin_coords = self._get_coordinates(origin)
            destination_coords = self._get_coordinates(destination)

            if not origin_coords or not destination_coords:
                return "Error: Could not geocode one or both locations"

            # Format the results - Note: actual directions not available via API
            formatted_result = {
                "notice": "Apple Maps does not provide a public Directions API. For actual directions, consider using the MapKit JS SDK in a web application or another provider.",
                "origin": {"address": origin, "coordinate": origin_coords},
                "destination": {
                    "address": destination,
                    "coordinate": destination_coords,
                },
                "mode": mode,
            }

            return json.dumps(formatted_result, indent=2)

        except Exception as e:
            logging.error(f"Error getting directions: {str(e)}")
            return f"Error getting directions: {str(e)}"

    def geocode(self, address: str) -> str:
        """
        Convert an address to coordinates using Apple Maps.
        Uses the MapKit JS Geocoder service.
        """
        logging.info(f"Geocoding address: {address}")

        session = self.get_connection()
        if not session:
            return (
                "Error connecting to Apple Maps API: JWT token could not be generated"
            )

        try:
            # URL encode the address
            encoded_address = urllib.parse.quote(address)

            # Make the request to the MapKit JS API
            url = f"{self.mapkit_base_url}/v1/geocode?q={encoded_address}&lang=en"
            response = session.get(url)
            response.raise_for_status()
            results = response.json()

            if not results or not results.get("results"):
                return f"No results found for '{address}'"

            # Format the result
            result = results.get("results", [])[0]
            formatted_result = {
                "address": self._format_address(result.get("address", {})),
                "coordinate": result.get("coordinate", {}),
                "country": result.get("country", "Unknown"),
                "countryCode": result.get("countryCode", "Unknown"),
                "displayMapRegion": result.get("displayMapRegion", {}),
            }

            return json.dumps(formatted_result, indent=2)

        except Exception as e:
            logging.error(f"Error geocoding address: {str(e)}")
            return f"Error geocoding address: {str(e)}"

    def reverse_geocode(self, lat: float, lng: float) -> str:
        """
        Convert coordinates to an address using Apple Maps.
        Uses the MapKit JS Geocoder service.
        """
        logging.info(f"Reverse geocoding coordinates: {lat}, {lng}")

        session = self.get_connection()
        if not session:
            return (
                "Error connecting to Apple Maps API: JWT token could not be generated"
            )

        try:
            # Make the request to the MapKit JS API
            url = f"{self.mapkit_base_url}/v1/reverseGeocode?loc={lat},{lng}&lang=en"
            response = session.get(url)
            response.raise_for_status()
            results = response.json()

            if not results or not results.get("results"):
                return f"No results found for coordinates {lat}, {lng}"

            # Format the result
            result = results.get("results", [])[0]
            formatted_result = {
                "address": self._format_address(result.get("address", {})),
                "coordinate": {"latitude": lat, "longitude": lng},
                "country": result.get("country", "Unknown"),
                "countryCode": result.get("countryCode", "Unknown"),
                "name": result.get("name", ""),
                "administrativeArea": result.get("administrativeArea", ""),
            }

            return json.dumps(formatted_result, indent=2)

        except Exception as e:
            logging.error(f"Error reverse geocoding: {str(e)}")
            return f"Error reverse geocoding: {str(e)}"

    def _get_jwt_token(self) -> Optional[str]:
        """
        Generate a JWT token for Apple Maps authentication.
        The token is required for all API calls.
        """
        # Check if we have a valid token
        current_time = time.time()
        if self.token and self.token_expiry > current_time:
            return self.token

        try:
            # Load private key
            if self.private_key:
                # Key provided directly
                key = self.private_key
            elif self.private_key_path:
                # Key provided as file path
                with open(self.private_key_path, "r") as f:
                    key = f.read()
            else:
                logging.error("No private key provided for Apple Maps JWT token")
                return None

            # JWT token options
            now = int(time.time())
            expiry = now + 15 * 60  # 15 minutes

            # Create a JWT token
            payload = {
                "iss": self.team_id,
                "iat": now,
                "exp": expiry,
                "sub": self.maps_id,
            }

            # Sign the token
            token = jwt.encode(
                payload,
                key,
                algorithm="ES256",
                headers={"kid": self.key_id},
            )

            # Store the token and expiry
            self.token = token
            self.token_expiry = expiry

            return token

        except Exception as e:
            logging.error(f"Error generating JWT token: {str(e)}")
            return None

    def _format_address(self, address_components: Dict) -> str:
        """
        Format an address from components returned by the Apple Maps API.
        """
        components = []

        # Add components in order
        for component in ["street", "subLocality", "locality", "postCode", "country"]:
            if component in address_components and address_components[component]:
                components.append(address_components[component])

        return ", ".join(components)

    def _get_coordinates(self, location: str) -> Optional[Dict]:
        """
        Helper method to get coordinates from a location string.
        The location can be either an address or a "lat,lng" string.

        Returns:
            A dict {"latitude": lat, "longitude": lng} or None if geocoding failed
        """
        # Check if location is already coordinates
        if "," in location:
            try:
                lat, lng = location.split(",")
                return {"latitude": float(lat.strip()), "longitude": float(lng.strip())}
            except (ValueError, TypeError):
                pass

        # Otherwise geocode the address
        try:
            geocode_result = self.geocode(location)
            geocode_data = json.loads(geocode_result)
            return geocode_data.get("coordinate", {})
        except Exception:
            return None

    def get_maps_type(self) -> str:
        return "Apple Maps"

    def get_maps_classifications(self) -> Set[str]:
        return {"commercial", "geocoding", "satellite"}
