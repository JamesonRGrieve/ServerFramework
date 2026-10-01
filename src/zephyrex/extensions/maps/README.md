# Maps Extension for AGInfrastructure

This extension provides mapping services integration for various map providers, allowing AGInfrastructure to search for locations, get directions, geocode addresses, and perform other mapping operations.

## Maps Types and Classifications

The Maps extension supports multiple map service types, classified into the following categories:

| Classification | Map Providers                          |
| -------------- | -------------------------------------- |
| open_source    | OpenStreetMap                          |
| commercial     | Google Maps, Apple Maps                |
| routing        | OpenStreetMap, Google Maps             |
| geocoding      | OpenStreetMap, Google Maps, Apple Maps |
| satellite      | Google Maps, Apple Maps                |

## Supported Map Providers

### OpenStreetMap

An open-source mapping platform that provides free geographic data and mapping services.

```python
# Example configuration
maps_extension = Maps(
    maps_type="openstreetmap",
    user_agent="YourAppName/1.0"  # Required for OpenStreetMap API
)
```

### Google Maps

Google's mapping platform offering comprehensive mapping services, including directions, satellite imagery, and more.

```python
# Example configuration
maps_extension = Maps(
    maps_type="google",
    api_key="your-google-maps-api-key"  # Required for Google Maps API
)
```

### Apple Maps

Apple's mapping service with MapKit JS API for geocoding and search capabilities.

```python
# Example configuration
maps_extension = Maps(
    maps_type="apple",
    maps_id="your-maps-id",
    team_id="your-team-id",
    key_id="your-key-id",
    private_key_path="/path/to/private/key.p8"  # Private key for JWT authentication
)
```

## Common Operations

All map providers support these common operations:

1. **Search for locations**:
   Find locations matching a query string.

2. **Get directions**:
   Retrieve directions between two locations, with support for different transportation modes.

3. **Geocode addresses**:
   Convert text addresses to geographic coordinates.

4. **Reverse geocode coordinates**:
   Convert geographic coordinates to readable addresses.

## Provider-Specific Considerations

### OpenStreetMap

- Uses Nominatim for geocoding and search
- Uses OSRM for routing
- Free to use but requires a proper User-Agent header
- Has strict usage policy and rate limits

### Google Maps

- Comprehensive coverage and features
- Requires an API key with billing enabled
- Offers rich response data
- May have usage costs after exceeding free tier

### Apple Maps

- Requires Apple Developer account
- Uses JWT authentication with MapKit JS
- Limited public API functionality
- Directions API not available via REST API (only through MapKit JS SDK)

## Extension Configuration

The Maps extension can be configured in the AGInfrastructure config file:

```yaml
extensions:
  maps:
    enabled: true
    maps_type: openstreetmap
    user_agent: AGInfrastructure/1.0
    api_key: your-api-key  # For Google Maps
```

## API Access

The maps extension provides the following commands to the agent:

- `Search Location in {MAPS_TYPE} Maps`
- `Get Directions from {MAPS_TYPE} Maps`
- `Geocode Address with {MAPS_TYPE} Maps`
- `Reverse Geocode with {MAPS_TYPE} Maps`

## Example Usage

```python
# Search for a location
result = await maps_extension.search_location("Eiffel Tower, Paris")

# Get directions
result = await maps_extension.get_directions("New York, NY", "Boston, MA", mode="driving")

# Geocode an address
result = await maps_extension.geocode("1600 Amphitheatre Parkway, Mountain View, CA")

# Reverse geocode coordinates
result = await maps_extension.reverse_geocode(37.423021, -122.083739)
``` 