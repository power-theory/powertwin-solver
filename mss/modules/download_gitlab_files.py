import requests
import urllib.parse

# Function to download the secure file from GitLab
def download_secure_file(api_url, project_id, file_id, token):
  try:
    url = f'{api_url}/projects/{project_id}/secure_files/{file_id}/download'
    headers = {'PRIVATE-TOKEN': token}
    response = requests.get(url, headers=headers)

    # Check for successful response
    if response.status_code == 200:
      return response.content  # Return the content of the file
    else:
      print(f'Failed to download secure file. Status code: {response.status_code}')
      return None
  except requests.exceptions.RequestException as e:
    print(f'Error during request: {e}')
    return None
  
# Function to download the secure file from GitLab
def download_repository_file(api_url, project_id, file_path, token, ref='main'):
  try:
    encoded_file_path = urllib.parse.quote(file_path, safe='')
    url = f'{api_url}/projects/{project_id}/repository/files/{encoded_file_path}/raw'
    headers = {'PRIVATE-TOKEN': token}
    params = {'ref': ref, 'lfs': 'true'}  # 'lfs=true' requests the full LFS file

    response = requests.get(url, headers=headers, params=params)

    if response.status_code != 200:
      print(f'Failed to download file. Status code: {response.status_code}')
      return None

    return response.content

  except requests.exceptions.RequestException as e:
    print(f'Error during request: {e}')
    return None
  
# def download_repository_file(api_url, project_id, file_path, token, ref='main'):
#   try:
#     encoded_file_path = urllib.parse.quote(file_path, safe='')
#     url = f'{api_url}/projects/{project_id}/repository/files/{encoded_file_path}/raw?ref={ref}'
#     headers = {'PRIVATE-TOKEN': token}
#     response = requests.get(url, headers=headers)

#     # Check for successful response
#     if response.status_code == 200:
#       return response.content  # Return the content of the file
#     else:
#       print(f'Failed to download secure file. Status code: {response.status_code}')
#       return None
#   except requests.exceptions.RequestException as e:
#     print(f'Error during request: {e}')
#     return None
