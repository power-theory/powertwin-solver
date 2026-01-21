# ======================================================================================
# Weather Station Matching and Download Module
# Purpose: Finds nearest weather station for building coordinates, downloads weather
#          files (EPW, DDY, STAT), and manages weather data caching for simulations
# ======================================================================================

import os
import json
import math
import re
import urllib.request
from modules.utils import initialize_logger

# Setup logging with external log directory support (for HPC logging)
external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Weather', external_log_dir)

# Cache weather stations in memory to avoid reloading GeoJSON multiple times
_weather_stations_cache = None

# Determine weather file directories based on HPC environment
# In HPC mode, use shared storage to avoid read-only container filesystem issues
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
    # Calculate the great circle distance between two points on Earth (in kilometers)
    # Uses Haversine formula for accurate geographic distance calculation
    # 
    # Args:
    #   lat1, lon1: Latitude and longitude of first point in decimal degrees
    #   lat2, lon2: Latitude and longitude of second point in decimal degrees
    # Returns:
    #   Distance in kilometers
    
    # Convert decimal degrees to radians for trigonometric calculations
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    
    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    # Haversine formula: a = sin²(Δφ/2) + cos φ1 ⋅ cos φ2 ⋅ sin²(Δλ/2)
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    
    # Radius of Earth in kilometers
    r = 6371
    
    # Return distance in kilometers
    return c * r

def extract_state_from_weather_filename(weather_title):
    parts = weather_title.split('_')
    if len(parts) >= 2 and parts[0] == "USA":
        return parts[1]  # Return state code (e.g., "AZ")
    return None


# =====================================================================================
# Helper: Load Weather Stations from Master GeoJSON
# Caches weather station coordinates, URLs, and metadata for lookups
# =====================================================================================
def _load_weather_stations():
    # Return cached weather stations if already loaded
    global _weather_stations_cache
    
    if _weather_stations_cache is not None:
        return _weather_stations_cache
    
    # Load master weather station GeoJSON file
    with open(MASTER_WEATHER_GEOJSON, 'r') as f:
        weather_data = json.load(f)
    
    # Parse features and extract coordinates, URLs, and metadata
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
        
        # Add to weather stations list with all required information
        weather_stations.append({
            'lon': coords[0],
            'lat': coords[1],
            'title': title,
            'state': state,
            'epw_url': epw_url
        })
    
    # Cache in global variable for subsequent calls
    _weather_stations_cache = weather_stations
    return weather_stations


def download_weather_files(weather_title, epw_url):
    # Validate URL is provided
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
    # USA_ID_Soda.Springs-Tigert.AP.725868_TMY3,USA_WY_Rawlins.Muni.AP.725745_TMY3,USA_WY_Riverton.Rgnl.AP.725765_TMY3
    file_extensions = ['.epw', '.ddy', '.stat']
    base_url = epw_url.replace('.epw', '')

    success = True
    # Download each file type
    for ext in file_extensions:
        url = base_url + ext
        filename = weather_title + ext
        filepath = os.path.join(weather_dir, filename)
        
        # Skip if file already exists
        if os.path.exists(filepath):
            logger.debug(f"Weather file already exists: {filepath}")
            continue
        
        try:
            # Download file from repository
            logger.info(f"Downloading {filename} from {url}")
            urllib.request.urlretrieve(url, filepath)
            logger.info(f"Successfully downloaded {filename}")
        except Exception as e:
            # Log error but continue with other files
            logger.error(f"Failed to download {filename}: {e}")
            success = False
    
    return success


# =====================================================================================
# Main: Find Nearest Weather Station for Building Location
# Matches building coordinates to nearest weather station and downloads weather data
# =====================================================================================
def get_location(asset_metadata):
    # Match a single building's coordinates to the nearest weather station
    # Automatically downloads weather files if not already cached locally
    # 
    # Args:
    #   asset_metadata: Dictionary containing building metadata with 'latitude' and 'longitude'
    # Returns:
    #   tuple: (State, WeatherFile) - State abbreviation and weather file name
    #          Returns (None, None) if coordinates are missing or invalid
    
    # Load weather stations from GeoJSON (cached in memory after first load)
    weather_stations = _load_weather_stations()
    
    # Extract building coordinates from metadata
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