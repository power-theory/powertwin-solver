import os 
import requests
from modules.utils.format_json_as_table import format_json_as_table
from flask import jsonify
from dotenv import load_dotenv 

load_dotenv('../.env.local')

def handle_request(api_endpoint, payload):
  api_domain = os.getenv('MSS_FLASK_BASE_URL')
  api_token = os.getenv('API_SOLVER_TOKEN')
  FLASK_PORT = os.getenv('FLASK_PORT')

  if not api_domain or not FLASK_PORT or not api_token:
    print("Error: Missing one or more environment variables (MSS_FLASK_BASE_URL, FLASK_PORT, API_SOLVER_TOKEN)")
    return jsonify(response_type='ephemeral', text="Configuration error: Missing environment variables"), 500

  url = f'{api_domain}:{FLASK_PORT}{api_endpoint}'

  headers = {
    'Content-Type': 'application/json',
    'api_token': api_token
  }

  data = None

  # Send the POST request to the endpoint
  try:
    response = requests.post(url, json=payload, headers=headers)
    response_json = response.json()

    if response_json.get('status') == 'ok':
      data = response_json['data']
    else:
      raise ValueError("Bad Response")

    if isinstance(data, list):
      # Format the JSON as a table and return the formatted string
      formatted_table = format_json_as_table(data)
      return formatted_table

  except requests.exceptions.RequestException as e:
    data = f'Request failed: {e}'
    print(data, e)

  return data