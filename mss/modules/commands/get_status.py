# ======================================================================================
# MSS Status Command Handler
# Purpose: Provides status query commands for PowerTwin Solver simulations
# ======================================================================================

from flask import jsonify
from .handle_request import handle_request

# =====================================================================================
# Helper: Mention Slack User
# Formats Slack user mention if user_id is provided
# =====================================================================================
def mention_user(user_id):
  # Return formatted Slack user mention (@username) or generic "User" if no ID provided
  return f"<@{user_id}>" if user_id else "User"

# =====================================================================================
# Helper: Generate Standard Response
# Formats response with Slack ephemeral message containing query results
# =====================================================================================
def generate_response(api_endpoint, token_type_id, user_id=None, entity="user count", description="",):
  # Construct payload with token type and optional user ID for API request
  payload = {
    "token_type_id": token_type_id,
    "user_id": user_id
  }

  # Call API endpoint via handle_request
  response_text = handle_request(api_endpoint, payload)
  
  # Format user mention for Slack message
  user_mention = mention_user(user_id)

  # Return formatted ephemeral (private) Slack message with results
  return jsonify(
    response_type='ephemeral',
    text=f"{user_mention}, here is the {entity}:\n{response_text}\n\n{description}"
  )

# =====================================================================================
# Status Query: Get Test Data
# Retrieves test status information from PowerTwin Solver
# =====================================================================================
def get_test(api_endpoint, token_type_id, user_id=None):
  # Define description for test data response
  description = "test"
  # Call generate_response with appropriate parameters
  return generate_response(api_endpoint, token_type_id, user_id, "test", description)
