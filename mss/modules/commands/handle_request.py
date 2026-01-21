# ======================================================================================
# MSS Command Handler - API Request Wrapper
# Purpose: Handles HTTP requests to PowerTwin Solver API with authentication,
#          response parsing, and table formatting for Slack display
# ======================================================================================

import os 
import requests
from modules.utils.format_json_as_table import format_json_as_table
from flask import jsonify
from dotenv import load_dotenv 

# Load environment variables from .env.local file
load_dotenv('../.env.local')

# =====================================================================================
# Main Handler: Dispatch API Request
# Makes authenticated HTTP POST request to PowerTwin Solver Flask API
# =====================================================================================
def handle_request(api_endpoint, payload):
  # Load configuration from environment variables
  api_domain = os.getenv('MSS_FLASK_BASE_URL')
  api_token = os.getenv('PG_DB_TOKEN_ADMIN')
  FLASK_PORT = os.getenv('FLASK_PORT')

  # Validate that all required environment variables are present
  if not api_domain or not FLASK_PORT or not api_token:
    print("Error: Missing one or more environment variables (MSS_FLASK_BASE_URL, FLASK_PORT, PG_DB_TOKEN_ADMIN)")
    return jsonify(response_type='ephemeral', text="Configuration error: Missing environment variables"), 500

  # Construct full API URL from domain, port, and endpoint
  url = f'{api_domain}:{FLASK_PORT}{api_endpoint}'

  # Prepare HTTP headers with content type and authentication token
  headers = {
    'Content-Type': 'application/json',
    'api_token': api_token
  }

  data = None

  # Send POST request to PowerTwin Solver API endpoint
  try:
    # Execute POST request with JSON payload and authentication headers
    response = requests.post(url, json=payload, headers=headers)
    response_json = response.json()

    # Check response status field for success
    if response_json.get('status') == 'ok':
      data = response_json['data']
    else:
      # Raise error if response indicates failure
      raise ValueError("Bad Response")

    # Format response data as table if it's a list
    if isinstance(data, list):
      # Format the JSON as a table and return the formatted string
      formatted_table = format_json_as_table(data)
      return formatted_table

  except requests.exceptions.RequestException as e:
    # Catch network-related exceptions
    data = f'Request failed: {e}'
    print(data, e)

  return data