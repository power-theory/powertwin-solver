# ======================================================================================
# MSS Commands Module
# Purpose: Registers and indexes all Slack command handlers for command dispatch
# ======================================================================================

# Import status query command function
from .get_status import (
  get_test
)

# Define token types with their numeric identifiers for database operations
tokenTypes = {
  'refresh': 1,
  'reset': 2,
  'verify': 3,
  'temporary': 4,
  'api': 5
}

# =====================================================================================
# Helper: Create Command Handler
# Wraps a handler function with endpoint and token type binding
# =====================================================================================
def create_command(func, endpoint, token_type):
  # Return lambda that binds function to specific endpoint and token type
  # Ignores second parameter from Slack (user_referral_subtype_id) via underscore
  return lambda user_id, _: func(endpoint, token_type, user_id)

# =====================================================================================
# Commands Index
# Dictionary mapping Slack slash commands to their handler functions
# =====================================================================================
commands_index = {
  # /user_count command: queries user count from PowerTwin Solver API
  '/user_count': create_command(get_test, '/api/user/user-count', tokenTypes['refresh']),
}