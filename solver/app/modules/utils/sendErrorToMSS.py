# ======================================================================================
# Error Reporting to Mission Support System (MSS)
# Purpose: Sends error messages from PowerTwin Solver to MSS Slack channel for alerts
# ======================================================================================

import os
import requests

############################################################################################################
# Name: send_error_to_mss(function_name, error_message)
# Description: This function sends the error message to the MSS.
############################################################################################################
def send_error_to_mss(function_name, error_message):
    # Send error message to MSS 
    # Includes function name and git branch for context
    
    # Load configuration from environment variables
    url = os.getenv('FLASK_MSS_BASE_URL')
    api_token = os.getenv('PG_DB_TOKEN_ADMIN')
    commit_branch = os.getenv('CI_COMMIT_BRANCH')

    # Format error message with context information
    combined_error_message = f"""
    Function: {function_name}
    Branch: {commit_branch}
    Error Message: {error_message}
    """

    try:
        # Send error to MSS error logging endpoint
        response = requests.post(
            f"{url}:8080/slack/log-error",
            json={'error_message': combined_error_message},
            headers={'api_token': api_token}
        )

        # Check response status
        if response.status_code != 200:
            print('Failed to log error in MSS:', response.json())
    except Exception as err:
        # Catch network errors and print to console as fallback
        print('Error sending error to MSS:', str(err))
