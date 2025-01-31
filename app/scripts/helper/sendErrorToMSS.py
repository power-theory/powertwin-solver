import os
import requests


def send_error_to_mss(function_name, error_message):
    url = os.getenv('FLASK_MSS_BASE_URL')
    api_token = os.getenv('PG_DB_TOKEN_ADMIN')
    commit_branch = os.getenv('CI_COMMIT_BRANCH')

    combined_error_message = f"""
    Function: {function_name}
    Branch: {commit_branch}
    Error Message: {error_message}
    """

    try:
        response = requests.post(
            f"{url}:8000/slack/log-error",
            json={'error_message': combined_error_message},
            headers={'api_token': api_token}
        )

        if response.status_code != 200:
            print('Failed to log error in MSS:', response.json())
    except Exception as err:
        print('Error sending error to MSS:', str(err))
