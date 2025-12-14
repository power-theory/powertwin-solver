import os
import json
import csv
import math
import re
import urllib.request
from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Weather', external_log_dir)

_weather_stations_cache = None

if os.environ.get('SLURM_JOB_ID'):  # Check if running in HPC environment
    MASTER_WEATHER_GEOJSON = os.path.join('/solver','app','urbanopt','master_weather.geojson')
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
    # Removed list: USA_AZ_Scottsdale.Muni.AP.722789_TMY3, USA_AZ_Phoenix-Deer.Valley.AP.722784_TMY3, USA_AR_Springdale.Muni.AP.723434_TMY3
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
            #TODO for HPC mode change location to be within the HPC shared directory
            logger.info(f"Downloading {filename} from {url}")
            urllib.request.urlretrieve(url, filepath)
            logger.info(f"Successfully downloaded {filename}")
        except Exception as e:
            logger.error(f"Failed to download {filename}: {e}")
            success = False
    
    return success


def get_location(asset_metadata):
    """
    Match a single building's coordinates to the nearest weather station.
    Downloads weather files if they don't exist locally.
    
    Args:
        asset_metadata: Dictionary containing building metadata with 'latitude' and 'longitude' keys
        
    Returns:
        tuple: (State, WeatherFile) - State abbreviation and weather file name
               Returns (None, None) if coordinates are missing or invalid
    """
    # Load weather stations (will use cache if already loaded)
    weather_stations = _load_weather_stations()
    
    # Get coordinates from asset metadata
    try:
        building_lat = asset_metadata.get('latitude')
        building_lon = asset_metadata.get('longitude')
        
        # Skip if coordinates are missing
        if building_lat is None or building_lon is None:
            return None, None
        
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
            
            return nearest_station['state'], nearest_station['title']
        else:
            return None, None
            
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        logger.error(f"Error processing asset metadata: {e}")
        return None, None