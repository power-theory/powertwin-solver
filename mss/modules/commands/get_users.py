from flask import jsonify
from .handle_request import handle_request

def mention_user(user_id):
  return f"<@{user_id}>" if user_id else "User"

def generate_response(api_endpoint, token_type_id, user_id=None, entity="user count", description="", user_referral_subtype_id=None):
  payload = {
    "token_type_id": token_type_id,
    "user_id": user_id
  }

  if user_referral_subtype_id is not None:
    payload["user_referral_subtype_id"] = user_referral_subtype_id

  response_text = handle_request(api_endpoint, payload)
  user_mention = mention_user(user_id)

  return jsonify(
    response_type='ephemeral',
    text=f"{user_mention}, here is the {entity}:\n{response_text}\n\n{description}"
  )

def get_users_count(api_endpoint, token_type_id, user_id=None):
  description = "Fetches the total count of fully signed-up users, specifically those who have completed signup."
  return generate_response(api_endpoint, token_type_id, user_id, "user count", description)

def get_temp_users_count(api_endpoint, token_type_id, user_id=None):
  description = "Fetches the total count of users who are still using temporary tokens and have not yet completed the full signup process."
  return generate_response(api_endpoint, token_type_id, user_id, "temporary users count", description)

def get_users_by_industry_type(api_endpoint, token_type_id, user_id=None):
  description = "Retrieves fully signed-up users, grouped by their industry type, who have completed signup."
  return generate_response(api_endpoint, token_type_id, user_id, "users by industry", description)

def get_temp_users_by_industry_type(api_endpoint, token_type_id, user_id=None):
  description = "Retrieves users currently using temporary tokens (those who haven't completed signup), grouped by their industry type."
  return generate_response(api_endpoint, token_type_id, user_id, "temporary users by industry", description)

def get_users_by_referral_type(api_endpoint, token_type_id, user_id=None, user_referral_subtype_id=None):
  description = "Retrieves fully signed-up users, grouped by their referral method, who have completed signup."
  return generate_response(api_endpoint, token_type_id, user_id, "users by referral type", description, user_referral_subtype_id)

def get_temp_users_by_referral_type(api_endpoint, token_type_id, user_id=None, user_referral_subtype_id=None):
  description = "Retrieves users currently using temporary tokens (those who haven't completed signup), grouped by their referral method."
  return generate_response(api_endpoint, token_type_id, user_id, "temporary users by referral type", description, user_referral_subtype_id)

