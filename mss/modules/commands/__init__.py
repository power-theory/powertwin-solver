from .get_status import (
  get_test
)

tokenTypes = {
  'refresh': 1,
  'reset': 2,
  'verify': 3,
  'temporary': 4,
  'api': 5
}

def create_command(func, endpoint, token_type):
  return lambda user_id, _: func(endpoint, token_type, user_id)

commands_index = {
  '/user_count': create_command(get_test, '/api/user/user-count', tokenTypes['refresh']),
}