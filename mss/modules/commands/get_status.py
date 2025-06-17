from flask import jsonify
from .handle_request import handle_request

def mention_user(user_id):
  return f"<@{user_id}>" if user_id else "User"

def generate_response(api_endpoint, token_type_id, user_id=None, entity="user count", description="",):
  payload = {
    "token_type_id": token_type_id,
    "user_id": user_id
  }

  response_text = handle_request(api_endpoint, payload)
  user_mention = mention_user(user_id)

  return jsonify(
    response_type='ephemeral',
    text=f"{user_mention}, here is the {entity}:\n{response_text}\n\n{description}"
  )

def get_test(api_endpoint, token_type_id, user_id=None):
  description = "test"
  return generate_response(api_endpoint, token_type_id, user_id, "test", description)
