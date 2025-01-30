from .get_users import (
  get_temp_users_by_industry_type, 
  get_temp_users_by_referral_type, 
  get_temp_users_count, 
  get_users_by_industry_type, 
  get_users_by_referral_type, 
  get_users_count
)

tokenTypes = {
  'refresh': 1,
  'reset': 2,
  'verify': 3,
  'temporary': 4,
  'api': 5
}

def create_command(func, endpoint, token_type, pass_subtype_id=False):
  if pass_subtype_id:
    return lambda user_id, user_referral_subtype_id: func(endpoint, token_type, user_id, user_referral_subtype_id)
  else:
    return lambda user_id, _: func(endpoint, token_type, user_id)

commands_index = {
  '/user_count': create_command(get_users_count, '/api/user/user-count', tokenTypes['refresh']),
  '/temp_user_count': create_command(get_temp_users_count, '/api/user/user-count', tokenTypes['temporary']),
  
  '/users_by_industry': create_command(get_users_by_industry_type, '/api/user/users-by-industry-type', tokenTypes['refresh']),
  '/temp_users_by_industry': create_command(get_temp_users_by_industry_type, '/api/user/users-by-industry-type', tokenTypes['temporary']),

  '/users_by_referral': create_command(get_users_by_referral_type, '/api/user/users-by-referral-type', tokenTypes['refresh'], pass_subtype_id=True),
  '/temp_users_by_referral': create_command(get_temp_users_by_referral_type, '/api/user/users-by-referral-type', tokenTypes['temporary'], pass_subtype_id=True)
}