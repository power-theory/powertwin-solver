# ======================================================================================
# MSS (Mission Support System) Slack Integration Application
# Purpose: Serves as Slack bot backend for command dispatch, error logging, and 
#          communication with PowerTwin Solver Flask API. Handles slash commands,
#          request verification, and error notifications to Slack channels.
# ======================================================================================

import logging
import os
import hashlib
import hmac
import time
import requests
from flask import Flask, request, jsonify
from modules.commands import commands_index
from dotenv import load_dotenv

# Load environment variables from .env.local file
load_dotenv('../.env.local')

# Configure logging for MSS application
logging.getLogger().setLevel(logging.INFO)

# Initialize Flask application
app = Flask(__name__)

# Load configuration from environment variables
PG_DB_TOKEN_ADMIN = os.getenv('PG_DB_TOKEN_ADMIN')
MSS_SLACK_VERIFICATION_TOKEN = os.getenv('MSS_SLACK_VERIFICATION_TOKEN')
MSS_SLACK_SIGNING_SECRET = os.getenv('MSS_SLACK_SIGNING_SECRET')
MSS_SLACK_WEBHOOK_URL = os.getenv('MSS_SLACK_WEBHOOK_URL')
MSS_SLACK_CHANNEL = os.getenv('MSS_SLACK_CHANNEL')

# =====================================================================================
# Slack Command Handler Route
# Handles incoming slash commands from Slack with request verification and command dispatch
# =====================================================================================
@app.route('/slack/commands', methods=['POST'])
def handle_slash_command():
  # Verify the request is coming from Slack (optional but recommended)
  slack_signature = request.headers.get('X-Slack-Signature')
  slack_request_timestamp = request.headers.get('X-Slack-Request-Timestamp')

  # Reject request if Slack signature verification fails
  if not verify_slack_request(slack_signature, slack_request_timestamp, request.get_data()):
    app.logger.warning("Slack request verification failed")
    return jsonify({'error': 'Request verification failed'}), 400

  # Extract the slash command and user info
  command = request.form.get('command')
  user_id = request.form.get('user_id')
  user_name = request.form.get('user_name')
  text = request.form.get('text')

  # Log the user and the route being accessed
  if command and user_id and user_name:
    app.logger.info(f"User {user_name} <@{user_id}> accessed the route: {request.path}, command: {command}")

  # Check if the text contains an optional integer argument
  user_referral_subtype_id = None
  if text:
    try:
      user_referral_subtype_id = int(text.strip())
    except ValueError:
      # Log invalid argument and return error message
      app.logger.info(f"User {user_name} <@{user_id}> provided a non-integer argument: {text}")
      return jsonify({
        'response_type': 'ephemeral',
        'text': "Please provide a valid integer argument."
      }), 400
    
  # Dispatch command to the correct handler
  handler = commands_index.get(command)
  if handler:
    try:
      # Execute the command handler function
      return handler(user_id, user_referral_subtype_id)
    except Exception as e:
      # Log the error and return an error message
      app.logger.error(f"Error handling command {command} by user {user_name} <@{user_id}>: {str(e)}")
      return jsonify({
        'response_type': 'ephemeral',
        'text': f"An error occurred: {str(e)}"
      }), 500
  else:
    # Handle unknown command
    app.logger.info(f"Unknown command '{command}' received from user {user_name} <@{user_id}>")
    return jsonify({
      'response_type': 'ephemeral',
      'text': "Unknown command."
    }), 400

# =====================================================================================
# Database Error Logging Route
# Accepts database errors from PowerTwin Solver and sends notifications to Slack
# =====================================================================================
@app.route('/slack/log-error', methods=['POST'])
def log_db_error():
  # Handles DB error logging with authentication and Slack notification
  try:
    # Get api_token from the request header
    api_token = request.headers.get('api_token')

    # Verify the api_token matches PG_DB_TOKEN_ADMIN
    if not api_token or api_token != PG_DB_TOKEN_ADMIN:
      app.logger.warning(f"Unauthorized access attempt with token: {api_token}")
      return jsonify({'error': 'Unauthorized'}), 403

    # Get error message from JSON payload
    error_message = request.json.get('error_message')
    if not error_message:
      return jsonify({'error': 'No error message provided'}), 400

    # Log the error locally
    app.logger.error(f"Database error: {error_message}")

    # Send the error message to Slack
    send_error_to_slack(error_message)

    return jsonify({'status': 'Error logged successfully'}), 200
  except Exception as e:
    # Log any errors that occur during error logging (meta-error handling)
    app.logger.error(f"Error logging DB error: {str(e)}")
    return jsonify({'error': 'Failed to log error'}), 500

# =====================================================================================
# Helper: Send Error to Slack
# Sends formatted error message to designated Slack channel via webhook
# =====================================================================================
def send_error_to_slack(error_message):
  # Send error message to Slack channel using webhook URL
  try:
    # Construct Slack message payload with warning emoji and error details
    payload = {
      'channel': MSS_SLACK_CHANNEL,
      'text': f":warning: Database Error: {error_message}",
      'username': 'Error Logger',
      'icon_emoji': ':rotating_light:'
    }
    
    # Post payload to Slack webhook URL
    response = requests.post(MSS_SLACK_WEBHOOK_URL, json=payload)
    
    # Log if Slack response indicates failure
    if response.status_code != 200:
      app.logger.error(f"Failed to send message to Slack: {response.text}")
  except Exception as e:
    # Log any exceptions during Slack communication
    app.logger.error(f"Error sending to Slack: {str(e)}")

# =====================================================================================
# Helper: Verify Slack Request Authenticity
# Validates incoming Slack requests using HMAC signature verification
# =====================================================================================
def verify_slack_request(slack_signature, slack_request_timestamp, request_body):
  # Verify timestamp is recent (within 5 minutes) to prevent replay attacks
  if abs(time.time() - int(slack_request_timestamp)) > 60 * 5:
    app.logger.warning(f"Request timestamp {slack_request_timestamp} is too old.")
    return False

  # Create signature basestring following Slack verification protocol (v0:timestamp:body)
  sig_basestring = f'v0:{slack_request_timestamp}:{request_body.decode("utf-8")}'
  
  # Generate HMAC SHA256 signature using Slack signing secret
  my_signature = 'v0=' + hmac.new(
    bytes(MSS_SLACK_SIGNING_SECRET, 'utf-8'),
    bytes(sig_basestring, 'utf-8'),
    hashlib.sha256
  ).hexdigest()

  # Compare computed signature with Slack-provided signature using constant-time comparison
  if not hmac.compare_digest(my_signature, slack_signature):
    app.logger.warning(f"Signature mismatch. My signature: {my_signature}, Slack signature: {slack_signature}")
    return False

  return True


# =====================================================================================
# Application Entry Point
# =====================================================================================
if __name__ == '__main__':
  # Start Flask development server on port 8080
  app.run(port=8080)
