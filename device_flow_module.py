import requests
import time
import os
import json
from datetime import datetime, timezone

# GitHub OAuth 2.0 Device Flow Endpoints
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"

# GitHub Copilot specific OAuth scope
# The 'copilot' scope is typically used for Copilot extensions.
# Other common scopes include 'repo', 'user', 'gist', etc.
# For a Copilot extension, 'copilot' is usually sufficient.
SCOPE = "copilot"

# GitHub Copilot Internal API Endpoint for Copilot Token
COPILOT_TOKEN_API_URL = "https://api.github.com/copilot_internal/v2/token"

# File to store token information
TOKEN_FILE = "github_copilot_token.json"

def initiate_device_flow(client_id: str) -> dict:
    """
    Initiates the GitHub device authorization flow.

    Args:
        client_id (str): Your GitHub OAuth Application's Client ID.

    Returns:
        dict: A dictionary containing 'device_code', 'user_code',
              'verification_uri', 'expires_in', and 'interval'.
              Returns an empty dict on failure.
    """
    print("Initiating GitHub device flow...")
    headers = {"Accept": "application/json"}
    data = {
        "client_id": client_id,
        "scope": SCOPE
    }
    try:
        response = requests.post(DEVICE_CODE_URL, headers=headers, data=data)
        response.raise_for_status()  # Raise an exception for HTTP errors
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error initiating device flow: {e}")
        return {}

def poll_for_token(client_id: str, device_code: str, interval: int, expires_in: int) -> dict:
    """
    Polls the GitHub token endpoint until the user authorizes or the code expires.

    Args:
        client_id (str): Your GitHub OAuth Application's Client ID.
        device_code (str): The device code obtained from initiate_device_flow.
        interval (int): The polling interval in seconds.
        expires_in (int): The lifetime of the device code in seconds.

    Returns:
        dict: A dictionary containing 'access_token', 'token_type', 'scope'
              on successful authorization. Returns an empty dict on failure
              or if the code expires.
    """
    print("Polling for token...")
    start_time = time.time()
    headers = {"Accept": "application/json"}
    data = {
        "client_id": client_id,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
    }

    while time.time() - start_time < expires_in:
        try:
            response = requests.post(ACCESS_TOKEN_URL, headers=headers, data=data)
            response.raise_for_status()
            response_data = response.json()

            if "access_token" in response_data:
                print("Authorization successful!")
                # Add the time when the token was obtained to calculate expiration
                response_data['obtained_at'] = time.time()
                return response_data
            elif response_data.get("error") == "authorization_pending":
                print(f"Authorization pending. Waiting {interval} seconds...")
                time.sleep(interval)
            elif response_data.get("error") == "slow_down":
                # If GitHub requests a slower polling interval
                new_interval = response_data.get("interval", interval)
                print(f"Slow down requested. Waiting {new_interval} seconds...")
                time.sleep(new_interval)
            elif response_data.get("error") == "expired_token":
                print("Device code expired. Please restart the login process.")
                return {}
            else:
                print(f"Error during polling: {response_data.get('error_description', response_data.get('error', 'Unknown error'))}")
                return {}
        except requests.exceptions.RequestError as e: # Catch a broader range of request errors
            print(f"Network error during polling: {e}")
            time.sleep(interval) # Wait before retrying on network errors
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return {}

    print("Device code expired without authorization.")
    return {}

def github_device_flow_login(client_id: str) -> dict | None:
    """
    Orchestrates the entire GitHub device flow login process.

    Args:
        client_id (str): Your GitHub OAuth Application's Client ID.

    Returns:
        dict | None: A dictionary containing 'access_token', 'token_type',
                     'scope', and 'obtained_at' (timestamp) if login is successful,
                     otherwise None.
    """
    if not client_id:
        print("Error: GitHub Client ID is required.")
        return None

    device_flow_info = initiate_device_flow(client_id)

    if not device_flow_info:
        return None

    user_code = device_flow_info.get("user_code")
    verification_uri = device_flow_info.get("verification_uri")
    device_code = device_flow_info.get("device_code")
    expires_in = device_flow_info.get("expires_in")
    interval = device_flow_info.get("interval", 5) # Default interval to 5 seconds

    if not all([user_code, verification_uri, device_code, expires_in]):
        print("Missing required information from device flow initiation.")
        return None

    print(f"\nTo authorize your device, please visit:")
    print(f"   {verification_uri}")
    print(f"And enter the following code:")
    print(f"   {user_code}")
    print(f"\nThis code will expire in {expires_in} seconds.")

    token_info = poll_for_token(client_id, device_code, interval, expires_in)

    if token_info and "access_token" in token_info:
        print("Login successful! Access Token obtained.")
        return token_info
    else:
        print("Login failed or timed out.")
        return None

def save_token_info(token_data: dict):
    """
    Saves the access token and its expiration information to a file.

    Args:
        token_data (dict): Dictionary containing 'access_token', 'expires_in',
                           'obtained_at', and optionally 'copilot_token_response'.
    """
    try:
        with open(TOKEN_FILE, 'w') as f:
            json.dump(token_data, f, indent=4) # Use indent for readability
        print(f"Access token information saved to {TOKEN_FILE}")
    except IOError as e:
        print(f"Error saving token information to file: {e}")

def load_token_info() -> dict | None:
    """
    Loads the access token and its expiration information from a file.

    Returns:
        dict | None: Dictionary containing 'access_token', 'expires_in',
                     'obtained_at', and optionally 'copilot_token_response'
                     if found and valid, otherwise None.
    """
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, 'r') as f:
            token_data = json.load(f)
        print(f"Access token information loaded from {TOKEN_FILE}")
        return token_data
    except (IOError, json.JSONDecodeError) as e:
        print(f"Error loading token information from file: {e}")
        # Optionally, delete corrupted file
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
            print(f"Removed corrupted token file: {TOKEN_FILE}")
        return None

def is_token_valid(token_data: dict) -> bool:
    """
    Checks if the given GitHub access token and Copilot internal token (if present)
    are still valid based on their expiration times.

    Args:
        token_data (dict): Dictionary containing 'access_token', 'expires_in',
                           'obtained_at', and optionally 'copilot_token_response'.

    Returns:
        bool: True if both tokens are still valid, False otherwise.
    """
    current_time = time.time()
    valid = True

    # 1. Check GitHub Access Token validity
    if not all(k in token_data for k in ['access_token', 'expires_in', 'obtained_at']):
        print("GitHub token data is incomplete. Re-authentication needed.")
        return False

    # Add a small buffer (e.g., 60 seconds) to account for network latency or clock skew
    github_expiration_time = token_data['obtained_at'] + token_data['expires_in'] - 60
    if current_time >= github_expiration_time:
        print("GitHub access token has expired or is close to expiring. Re-authentication needed.")
        valid = False

    # 2. Check Copilot Internal Token validity (if available)
    copilot_token_response = token_data.get('copilot_token_response')
    if copilot_token_response:
        copilot_expires_at_str = copilot_token_response.get('expires_at')
        if copilot_expires_at_str:
            try:
                # Parse the ISO 8601 timestamp (e.g., "2025-07-09T16:00:00Z")
                # Ensure it's treated as UTC
                copilot_expiration_dt = datetime.fromisoformat(copilot_expires_at_str.replace('Z', '+00:00'))
                # Convert to Unix timestamp for comparison
                copilot_expiration_timestamp = copilot_expiration_dt.timestamp()

                # Add a buffer for the Copilot token as well
                if current_time >= (copilot_expiration_timestamp - 60): # 1 minute buffer
                    print("Copilot internal token has expired or is close to expiring. Re-authentication needed.")
                    valid = False
            except ValueError as e:
                print(f"Warning: Could not parse Copilot token expiration date '{copilot_expires_at_str}': {e}. Assuming invalid.")
                valid = False
        else:
            print("Copilot internal token response missing 'expires_at'. Assuming invalid.")
            valid = False
    else:
        # If Copilot token is missing, but GitHub token is valid, we still need to fetch Copilot token.
        # This state should lead to a refresh, so we set valid to False here.
        print("No Copilot internal token found in saved data. Re-authentication needed.")
        valid = False # If Copilot token is missing, we need to fetch it, which means full re-auth.

    return valid

def fetch_copilot_internal_token(github_access_token: str) -> dict | None:
    """
    Calls the GitHub Copilot internal API to get a Copilot-specific token.

    Args:
        github_access_token (str): The access token obtained from the GitHub device flow.

    Returns:
        dict | None: The JSON response from the Copilot API if successful, otherwise None.
    """
    print(f"Calling Copilot internal token API: {COPILOT_TOKEN_API_URL}")
    headers = {
        "Authorization": f"token {github_access_token}",
        "Accept": "application/json",
        "User-Agent": "GitHubCopilotExtension/1.0" # Recommended to set a User-Agent
    }
    try:
        response = requests.get(COPILOT_TOKEN_API_URL, headers=headers)
        response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
        copilot_response = response.json()
        print("Successfully fetched Copilot internal token.")
        return copilot_response
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error fetching Copilot internal token: {e.response.status_code} - {e.response.text}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Network error fetching Copilot internal token: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred while fetching Copilot internal token: {e}")
        return None

def get_or_refresh_copilot_token(client_id: str) -> dict | None:
    """
    Attempts to load a saved GitHub access token and its associated Copilot token.
    If either the GitHub access token or the Copilot internal token is expired or not found,
    it initiates the device flow to get a new GitHub token, and then fetches a new Copilot token.

    Returns:
        dict | None: The full token_data dictionary (including 'access_token',
                     'expires_in', 'obtained_at', and 'copilot_token_response')
                     if successful, otherwise None.
    """
    token_data = load_token_info()
    github_access_token = None
    copilot_token_response = None

    # Check if we have valid saved tokens
    if token_data and is_token_valid(token_data):
        print("Using existing valid GitHub access token and Copilot internal token.")
        return token_data
    else:
        print("No valid saved tokens found or tokens expired. Initiating new login process.")
        # If is_token_valid returns False, it means either GitHub token or Copilot token is invalid
        # or missing. In this scenario, we must re-initiate the full device flow
        # as GitHub device flow does not provide refresh tokens.
        new_github_token_data = github_device_flow_login(client_id)
        if new_github_token_data:
            github_access_token = new_github_token_data['access_token']
            # Fetch Copilot token immediately after getting a new GitHub access token
            copilot_token_response = fetch_copilot_internal_token(github_access_token)
            if copilot_token_response:
                new_github_token_data['copilot_token_response'] = copilot_token_response
                save_token_info(new_github_token_data)
                return new_github_token_data
            else:
                print("Failed to fetch Copilot token after new GitHub access token. Login process aborted.")
                return None
        else:
            print("Failed to obtain a new GitHub access token. Cannot proceed.")
            return None


if __name__ == "__main__":
    # --- How to use this module ---
    # 1. Register a new OAuth App on GitHub:
    #    Go to your GitHub profile settings -> Developer settings -> OAuth Apps -> New OAuth App.
    #    - Application name: e.g., "My Copilot Extension Dev"
    #    - Homepage URL: e.g., "http://localhost:3000" (can be anything, not strictly used for device flow)
    #    - Authorization callback URL: e.g., "http://localhost:3000/callback" (not strictly used for device flow)
    #    Make sure to note down the "Client ID". The "Client Secret" is NOT used for device flow.
    # 2. Replace 'YOUR_GITHUB_CLIENT_ID' below with your actual Client ID.
    # 3. Run this script: python your_module_name.py

    # IMPORTANT: Replace with your actual GitHub OAuth App Client ID
    # For security, consider loading this from an environment variable or a config file.
    github_client_id = os.getenv("GITHUB_CLIENT_ID", "YOUR_GITHUB_CLIENT_ID")

    if github_client_id == "YOUR_GITHUB_CLIENT_ID":
        print("WARNING: Please replace 'YOUR_GITHUB_CLIENT_ID' with your actual GitHub OAuth App Client ID.")
        print("         Alternatively, set the GITHUB_CLIENT_ID environment variable.")
        exit("Client ID not configured.")


    print("Attempting to get or refresh GitHub Copilot Access Token and internal Copilot Token...")
    # This function now handles the entire logic: load, check, refresh GitHub token,
    # and then fetch the Copilot internal token.
    full_token_info = get_or_refresh_copilot_token(github_client_id)

    if full_token_info:
        github_access_token = full_token_info.get('access_token')
        copilot_token_response = full_token_info.get('copilot_token_response')

        if github_access_token:
            print(f"\nCurrently active GitHub Access Token: {github_access_token[:8]}... (truncated for display)")
        else:
            print("\nGitHub Access Token not found in the final token info.")

        if copilot_token_response:
            print("\nCopilot Internal Token Response:")
            # Print specific parts of the Copilot response for demonstration
            # The actual structure of this response might vary, inspect it.
            print(f"  Token: {copilot_token_response.get('token', 'N/A')[:8]}...")
            print(f"  Expires At: {copilot_token_response.get('expires_at', 'N/A')}")
            print(f"  Refresh In: {copilot_token_response.get('refresh_in', 'N/A')} seconds")
            # In a real Copilot extension, you would use 'copilot_token_response.get('token')'
            # for authenticating requests to the Copilot service.
        else:
            print("\nCopilot Internal Token was not obtained.")
    else:
        print("\nCould not obtain a valid GitHub Access Token or Copilot Internal Token.")

    # --- Example of re-authentication (simulating token expiration by deleting file) ---
    # Uncomment the following lines to test re-authentication by simulating
    # the token file being removed or corrupted.
    # print("\nSimulating token expiration by removing the token file...")
    # if os.path.exists(TOKEN_FILE):
    #     os.remove(TOKEN_FILE)
    #     print(f"Removed {TOKEN_FILE}. Next run will re-authenticate.")
    #
    # print("\nRunning get_or_refresh_copilot_token again after simulated expiration...")
    # access_token_after_simulated_refresh = get_or_refresh_copilot_token(github_client_id)
    # if access_token_after_simulated_refresh:
    #     print(f"New GitHub Access Token and Copilot Token obtained after simulated re-authentication.")
    # else:
    #     print("Simulated re-authentication failed.")
