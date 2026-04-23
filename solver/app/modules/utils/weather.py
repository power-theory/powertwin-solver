import os
import csv
import json
import math
import re
import time
import urllib.request
import urllib.error
from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Weather', external_log_dir)


def fetch_url(url, timeout=5, retries=3, backoff=1.0):
    """Fetch a URL with retry and exponential backoff. Returns the response body as bytes."""
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < retries - 1:
                wait = backoff * (2 ** attempt)
                logger.debug(f"Fetch failed for {url} (attempt {attempt + 1}/{retries}): {e}, retrying in {wait:.1f}s")
                time.sleep(wait)
            else:
                raise


def download_url(url, filepath, timeout=30, retries=3, backoff=1.0):
    """Download a file from URL to filepath with retry and exponential backoff."""
    for attempt in range(retries):
        try:
            urllib.request.urlretrieve(url, filepath)
            return True
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < retries - 1:
                wait = backoff * (2 ** attempt)
                logger.debug(f"Download failed for {url} (attempt {attempt + 1}/{retries}): {e}, retrying in {wait:.1f}s")
                time.sleep(wait)
            else:
                raise

_weather_stations_cache = None
_climate_zone_by_fips = None
_county_lookup = None
_fips_cache = {}

if os.environ.get('SLURM_JOB_ID'):
    CLIMATE_ZONES_CSV = os.path.join('/solver', 'app', 'urbanopt', 'ClimateZones.csv')
    US_COUNTIES_GEOJSON = os.path.join('/solver', 'app', 'urbanopt', 'us_counties.geojson')
else:
    CLIMATE_ZONES_CSV = os.path.join('app', 'urbanopt', 'ClimateZones.csv')
    US_COUNTIES_GEOJSON = os.path.join('app', 'urbanopt', 'us_counties.geojson')

if os.environ.get('SLURM_JOB_ID'):  # Check if running in HPC environment
    MASTER_WEATHER_GEOJSON = os.path.join('/solver','app','urbanopt','master_weather.geojson')
    # Use HPC shared storage for weather files to avoid read-only container filesystem
    hpc_shared_storage = os.environ.get('HPC_SHARED_STORAGE')
    if hpc_shared_storage:
        WEATHER_FILES_DIR = os.path.join(hpc_shared_storage, 'weather_files')
    else:
        WEATHER_FILES_DIR = os.path.join('/solver','app','urbanopt','weather_files')
else:
    MASTER_WEATHER_GEOJSON = os.path.join('app','urbanopt','master_weather.geojson')
    WEATHER_FILES_DIR = os.path.join('app','urbanopt','weather_files')

def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance between two points on Earth (in kilometers).
    
    Args:
        lat1, lon1: Latitude and longitude of first point in decimal degrees
        lat2, lon2: Latitude and longitude of second point in decimal degrees
        
    Returns:
        Distance in kilometers
    """
    # Convert decimal degrees to radians
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    
    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    
    # Radius of Earth in kilometers
    r = 6371
    
    return c * r

def extract_state_from_weather_filename(weather_title):
    parts = weather_title.split('_')
    if len(parts) >= 2 and parts[0] == "USA":
        return parts[1]  # Return state code (e.g., "AZ")
    return None


def get_epw_utc_offset(weather_title):
    """
    Return the UTC offset (float hours) declared in the EPW file's LOCATION
    header. The header is line 1 of the file, comma-separated, with the
    timezone field at index 8 (0-based). EPW timestamps are always in this
    local standard time (no DST).

    Raises FileNotFoundError if the EPW is missing and ValueError if the
    header cannot be parsed. No silent fallback -- a wrong offset is worse
    than a loud failure.
    """
    epw_file = os.path.join(WEATHER_FILES_DIR, weather_title, f"{weather_title}.epw")
    if not os.path.exists(epw_file):
        raise FileNotFoundError(f"EPW file not found: {epw_file}")
    with open(epw_file, 'r') as f:
        header = f.readline().strip()
    parts = header.split(',')
    if len(parts) < 9 or not parts[0].upper().startswith('LOCATION'):
        raise ValueError(f"Malformed EPW LOCATION header in {epw_file}: {header!r}")
    try:
        return float(parts[8])
    except ValueError as e:
        raise ValueError(f"Bad UTC offset field in {epw_file}: {parts[8]!r}") from e


def _load_weather_stations():
    global _weather_stations_cache
    
    if _weather_stations_cache is not None:
        return _weather_stations_cache
    
    with open(MASTER_WEATHER_GEOJSON, 'r') as f:
        weather_data = json.load(f)
    
    weather_stations = []
    for feature in weather_data['features']:
        coords = feature['geometry']['coordinates']  # [longitude, latitude]
        title = feature['properties']['title']
        state = extract_state_from_weather_filename(title)
        
        # Extract EPW URL from the HTML anchor tag
        epw_html = feature['properties'].get('epw', '')
        epw_url = None
        if epw_html:
            # Extract URL from <a href=URL>text</a>
            match = re.search(r'href=([^\s>]+)', epw_html)
            if match:
                epw_url = match.group(1)
        
        weather_stations.append({
            'lon': coords[0],
            'lat': coords[1],
            'title': title,
            'state': state,
            'epw_url': epw_url
        })
    
    _weather_stations_cache = weather_stations
    return weather_stations


def download_weather_files(weather_title, epw_url):
    if not epw_url:
        logger.error(f"No EPW URL provided for {weather_title}")
        return False
    
    # Create directory for this weather station
    weather_dir = os.path.join(WEATHER_FILES_DIR, weather_title)
    os.makedirs(weather_dir, exist_ok=True)
    
    # Download all three file types: .epw, .ddy, .stat
    
    # NOTE Some weather stations may result in errors if data within files is missing. 
    # If found delete the station from the master_weather.geojson.
    # Removed list: USA_AZ_Scottsdale.Muni.AP.722789_TMY3, USA_AZ_Phoenix-Deer.Valley.AP.722784_TMY3, USA_AR_Springdale.Muni.AP.723434_TMY3, USA_WY_Evanston-Uinta.County.AP-Burns.Field.725775_TMY3
    # USA_ID_Soda.Springs-Tigert.AP.725868_TMY3,USA_WY_Rawlins.Muni.AP.725745_TMY3,USA_WY_Riverton.Rgnl.AP.725765_TMY3, USA_WY_Casper-Natrona.County.Intl.AP.725690_TMY3
    file_extensions = ['.epw', '.ddy', '.stat']
    base_url = epw_url.replace('.epw', '')
    
    success = True
    for ext in file_extensions:
        url = base_url + ext
        filename = weather_title + ext
        filepath = os.path.join(weather_dir, filename)
        
        # Skip if file already exists
        if os.path.exists(filepath):
            logger.debug(f"Weather file already exists: {filepath}")
            continue
        
        try:
            logger.info(f"Downloading {filename} from {url}")
            download_url(url, filepath)
            logger.info(f"Successfully downloaded {filename}")
        except Exception as e:
            logger.error(f"Failed to download {filename} after retries: {e}")
            success = False
    
    return success


def _load_climate_zones():
    global _climate_zone_by_fips

    if _climate_zone_by_fips is not None:
        return _climate_zone_by_fips

    _climate_zone_by_fips = {}
    with open(CLIMATE_ZONES_CSV, 'r') as f:
        for row in csv.DictReader(f, delimiter=';'):
            iecc = row.get('IECC21', '').strip()
            if not iecc:
                continue
            fips = row['GEOID'].lstrip('G')
            moisture = row.get('MOISTURE21', '').strip()
            # OpenStudio requires a moisture suffix for ASHRAE 169-2013 format.
            # Some counties (zones 7, 8, and rare zone 4 cases) have empty
            # MOISTURE21. Derive from BA21: Humid/Very Cold -> A, Dry -> B, Marine -> C.
            if not moisture and iecc:
                ba = row.get('BA21', '').strip().lower()
                if 'dry' in ba:
                    moisture = 'B'
                elif 'marine' in ba:
                    moisture = 'C'
                else:
                    moisture = 'A'
            _climate_zone_by_fips[fips] = iecc + moisture

    logger.debug(f"Loaded {len(_climate_zone_by_fips)} county climate zones from ClimateZones.csv")
    return _climate_zone_by_fips


def _load_county_boundaries():
    global _county_lookup

    if _county_lookup is not None:
        return _county_lookup

    from shapely.geometry import shape
    from shapely import STRtree

    with open(US_COUNTIES_GEOJSON, 'r') as f:
        data = json.load(f)

    geometries = []
    fips_list = []
    for feature in data['features']:
        geom = shape(feature['geometry'])
        geometries.append(geom)
        fips_list.append(feature['id'])

    tree = STRtree(geometries)
    _county_lookup = (tree, geometries, fips_list)
    logger.debug(f"Loaded {len(fips_list)} county boundaries for spatial lookup")
    return _county_lookup


def get_county_fips(lat, lon):
    cache_key = (round(lat, 3), round(lon, 3))
    if cache_key in _fips_cache:
        return _fips_cache[cache_key]

    try:
        from shapely.geometry import Point

        tree, geometries, fips_list = _load_county_boundaries()
        point = Point(lon, lat)  # GeoJSON is lon, lat
        idx = tree.nearest(point)
        # Verify the point is actually inside the nearest polygon
        if geometries[idx].contains(point):
            fips = fips_list[idx]
        else:
            # Point outside all polygons (e.g. offshore), find by query
            fips = None
            for i in tree.query(point):
                if geometries[i].contains(point):
                    fips = fips_list[i]
                    break
        _fips_cache[cache_key] = fips
        if fips is None:
            logger.warning(f"No county found for coordinates ({lat}, {lon})")
        return fips
    except Exception as e:
        logger.warning(f"County FIPS lookup failed for ({lat}, {lon}): {e}")
        _fips_cache[cache_key] = None
        return None


def get_climate_zone(lat, lon):
    fips = get_county_fips(lat, lon)
    if fips is None:
        return None
    zones = _load_climate_zones()
    zone = zones.get(fips)
    if zone is None:
        logger.warning(f"No climate zone found for county FIPS {fips}")
    return zone


def get_location(asset_metadata):
    """
    Match a single building's coordinates to the nearest weather station.
    Downloads weather files if they don't exist locally.

    Args:
        asset_metadata: Dictionary containing building metadata with 'latitude' and 'longitude' keys

    Returns:
        tuple: (State, WeatherFile, ClimateZone) - State abbreviation, weather file name, and IECC climate zone
               Returns (None, None, None) if coordinates are missing or invalid
    """
    # Load weather stations (will use cache if already loaded)
    weather_stations = _load_weather_stations()
    
    # Get coordinates from asset metadata
    try:
        building_lat = asset_metadata.get('latitude')
        building_lon = asset_metadata.get('longitude')
        
        # Skip if coordinates are missing
        if building_lat is None or building_lon is None:
            return None, None, None
        
        # Find nearest weather station
        min_distance = float('inf')
        nearest_station = None
        
        for station in weather_stations:
            distance = haversine_distance(
                building_lat, building_lon,
                station['lat'], station['lon']
            )
            
            if distance < min_distance:
                min_distance = distance
                nearest_station = station
        
        # Download weather files if needed
        if nearest_station:
            weather_title = nearest_station['title']
            epw_file = os.path.join(WEATHER_FILES_DIR, weather_title, f"{weather_title}.epw")
            
            # Check if weather files exist, download if not
            if not os.path.exists(epw_file):
                logger.info(f"Weather files not found for {weather_title}, downloading...")
                download_success = download_weather_files(weather_title, nearest_station['epw_url'])
                if not download_success:
                    logger.warning(f"Failed to download weather files for {weather_title}")
            
            climate_zone = get_climate_zone(building_lat, building_lon)
            return nearest_station['state'], nearest_station['title'], climate_zone
        else:
            return None, None, None

    except (KeyError, ValueError, TypeError, AttributeError) as e:
        logger.error(f"Error processing asset metadata: {e}")
        return None, None, None