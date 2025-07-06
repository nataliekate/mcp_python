import gradio as gr # Import the Gradio library for creating web UIs
import requests # Import the requests library for making HTTP requests (e.g., to GitHub, OpenAI)
import time # Import time for delays (e.g., polling interval)
import json # Import json for working with JSON data
import os # Import os for operating system functionalities (though not heavily used in this simplified version)
from datetime import datetime, timedelta # Import datetime for handling dates and times (e.g., token expiration)
from openai import OpenAI # Import the OpenAI client library for interacting with OpenAI APIs

# --- GitHub OAuth Configuration ---
# IMPORTANT: Replace with your GitHub OAuth App Client ID
# You need to create a GitHub OAuth App and enable "Device Flow"
# Go to: https://github.com/settings/applications/new
# No client secret is needed for the Device Flow.
GITHUB_CLIENT_ID = "YOUR_GITHUB_CLIENT_ID_HERE" # <--- PASTE YOUR GITHUB CLIENT ID HERE
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token" # GitHub endpoint to exchange device code for access token
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code" # GitHub endpoint to get device/user codes
GITHUB_USER_API_URL = "https://api.github.com/user" # GitHub API endpoint to fetch user info

# --- OpenAI Configuration ---
OPENAI_MODEL = "gpt-3.5-turbo" # The default OpenAI model to use for the chatbot

async def refresh_github_token(refresh_token):
    """
    Refreshes the GitHub access token using the refresh token.
    This allows getting a new access token without requiring the user to re-authenticate
    via the full Device Flow, as long as the refresh token is valid.
    """
    headers = {'Accept': 'application/json'} # Request JSON response
    data = {
        'client_id': GITHUB_CLIENT_ID,
        'grant_type': 'refresh_token', # Specify the grant type for refreshing tokens
        'refresh_token': refresh_token # The refresh token obtained during initial authentication
    }
    try:
        response = requests.post(GITHUB_TOKEN_URL, headers=headers, data=data)
        response.raise_for_status() # Raise an exception for HTTP errors (e.g., 4xx or 5xx)
        token_data = response.json() # Parse the JSON response
        if 'access_token' in token_data and 'refresh_token' in token_data:
            print("Access token refreshed successfully.")
            return token_data # Return the new access and refresh tokens
        else:
            print(f"Token refresh failed: {token_data.get('error_description', token_data)}")
            return None # Indicate failure if tokens are not in response
    except requests.exceptions.RequestException as e:
        print(f"Request error during token refresh: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"JSON decode error during token refresh: {e}. Response: {response.text}")
        return None

async def authenticate_github_device_flow(client_id_input, state: gr.State):
    """
    Initiates the GitHub Device Flow authentication process.
    This is an asynchronous generator function, meaning it yields multiple outputs over time.
    """
    # Check if the user has provided their GitHub Client ID
    if client_id_input == "YOUR_GITHUB_CLIENT_ID_HERE" or not client_id_input:
        yield "Please replace 'YOUR_GITHUB_CLIENT_ID_HERE' with your actual GitHub OAuth App Client ID.", "", "", "", "", "", state
        return # Exit the generator

    # 1. Request device code from GitHub
    headers = {'Accept': 'application/json'}
    data = {'client_id': client_id_input, 'scope': 'user'} # Request 'user' scope to get basic user info
    try:
        response = requests.post(GITHUB_DEVICE_CODE_URL, headers=headers, data=data)
        response.raise_for_status()
        device_code_data = response.json()
    except requests.exceptions.RequestException as e:
        yield f"Error requesting device code: {e}", "", "", "", "", "", state
        return

    # Extract necessary codes and URLs from the response
    device_code = device_code_data.get('device_code')
    user_code = device_code_data.get('user_code')
    verification_uri = device_code_data.get('verification_uri')
    interval = device_code_data.get('interval', 5) # Polling interval in seconds

    # Check if all required data was received
    if not all([device_code, user_code, verification_uri]):
        yield "Failed to get device codes. Check Client ID and network.", "", "", "", "", "", state
        return

    # Prepare the message to show the user for authentication
    auth_message = (
        f"Please go to: [ {verification_uri} ]( {verification_uri} )\n"
        f"And enter the code: **{user_code}**\n"
        f"Waiting for authorization..."
    )
    # Yield the initial message to the Gradio UI
    yield auth_message, verification_uri, user_code, "Waiting...", "", "", state

    # 2. Poll GitHub for the access token
    start_time = time.time()
    timeout = 900 # Device code typically expires in 15 minutes (900 seconds)

    while time.time() - start_time < timeout:
        token_data = {
            'client_id': client_id_input,
            'device_code': device_code,
            'grant_type': 'urn:ietf:params:oauth:grant-type:device_code' # Grant type for device flow
        }
        try:
            token_response = requests.post(GITHUB_TOKEN_URL, headers=headers, data=token_data)
            token_response.raise_for_status()
            token_json = token_response.json()

            # Check if access token is received
            if 'access_token' in token_json:
                access_token = token_json['access_token']
                refresh_token = token_json.get('refresh_token') # Get refresh token if provided
                expires_in = token_json.get('expires_in', 3600 * 8) # Default expiration to 8 hours

                # Store tokens in Gradio's session state (in-memory)
                state.value['github_tokens'] = {
                    'access_token': access_token,
                    'refresh_token': refresh_token,
                    'expires_at': (datetime.now() + timedelta(seconds=expires_in)).isoformat() # Store expiration time
                }

                # Yield final success message and tokens
                yield f"Authentication successful!", verification_uri, user_code, "Authenticated!", access_token, refresh_token, state
                return # Exit the generator after successful authentication
            elif token_json.get('error') == 'authorization_pending':
                # User has not yet authorized the app
                yield f"{auth_message}\nStatus: Authorization pending...", verification_uri, user_code, "Pending...", "", "", state
            elif token_json.get('error') == 'slow_down':
                # GitHub requests to slow down polling
                interval = token_json.get('interval', interval + 5)
                yield f"{auth_message}\nStatus: Slow down, increasing poll interval to {interval}s...", verification_uri, user_code, "Slow Down...", "", "", state
            else:
                # Other errors during polling
                yield f"Authentication failed: {token_json.get('error_description', token_json.get('error', 'Unknown error'))}", "", "", "", "", "", state
                return
        except requests.exceptions.RequestException as e:
            yield f"Error polling for token: {e}", "", "", "", "", "", state
            return
        except json.JSONDecodeError as e:
            yield f"JSON decode error: {e}. Response: {token_response.text}", "", "", "", "", "", state
            return

        time.sleep(interval) # Wait for the specified interval before polling again

    # If timeout is reached without successful authentication
    yield "Authentication timed out. Please try again.", "", "", "", "", "", state
    return

async def get_fresh_github_token(state: gr.State):
    """
    Retrieves a fresh GitHub access token from the current session state.
    If the current access token is expired, it attempts to refresh it using the stored refresh token.
    """
    current_tokens = state.value.get('github_tokens', {}) # Get GitHub tokens from Gradio state
    access_token = current_tokens.get('access_token')
    refresh_token = current_tokens.get('refresh_token')
    expires_at_str = current_tokens.get('expires_at')
    expires_at = datetime.fromisoformat(expires_at_str) if expires_at_str else None

    # 1. Check if current access token in state is valid (not expired)
    if access_token and expires_at and datetime.now() < expires_at:
        print("Using valid access token from Gradio session state.")
        return access_token, "Using existing valid access token."

    # 2. Access token expired or not found, try to refresh using stored refresh token
    if refresh_token:
        print("Access token expired. Attempting to refresh token...")
        new_tokens = await refresh_github_token(refresh_token) # Call the refresh function
        if new_tokens:
            new_access_token = new_tokens['access_token']
            # Use new refresh token if provided, otherwise keep the old one
            new_refresh_token = new_tokens.get('refresh_token', refresh_token)
            new_expires_in = new_tokens.get('expires_in', 3600 * 8)
            new_expires_at = datetime.now() + timedelta(seconds=new_expires_in)

            # Update Gradio session state with the newly refreshed tokens
            state.value['github_tokens'] = {
                'access_token': new_access_token,
                'refresh_token': new_refresh_token,
                'expires_at': new_expires_at.isoformat()
            }
            print("Successfully refreshed access token.")
            return new_access_token, "Access token refreshed successfully!"
        else:
            print("Failed to refresh token. Refresh token might be invalid/expired.")
            # Clear potentially bad tokens from state if refresh failed
            if 'github_tokens' in state.value:
                del state.value['github_tokens']
            return None, "Failed to refresh token. Please re-authenticate via Device Flow."
    else:
        print("No refresh token available in session state.")

    # 3. No valid tokens or refresh token found, user needs to re-authenticate
    if 'github_tokens' in state.value:
        del state.value['github_tokens'] # Clear any stale state
    return None, "No valid GitHub tokens found. Please authenticate first using the Device Flow."

async def get_github_user_info(state: gr.State):
    """
    Fetches GitHub user information using a fresh access token.
    This function demonstrates using the authenticated GitHub token.
    """
    access_token, status_message = await get_fresh_github_token(state) # Get a valid access token

    if not access_token:
        return f"Error: {status_message}", state

    headers = {'Authorization': f'token {access_token}'} # Set Authorization header with the access token
    try:
        response = requests.get(GITHUB_USER_API_URL, headers=headers)
        response.raise_for_status()
        user_info = response.json()
        return f"User: **{user_info.get('login', 'N/A')}** (ID: {user_info.get('id', 'N/A')})", state
    except requests.exceptions.RequestException as e:
        return f"Error fetching GitHub user info: {e}. Re-authenticate needed.", state

def set_openai_api_key(api_key, state: gr.State):
    """Sets the OpenAI API key in the Gradio session state."""
    state.value['openai_api_key'] = api_key
    return "OpenAI API Key set successfully!" if api_key else "OpenAI API Key cleared.", state

async def openai_chat_completion(message, history, state: gr.State):
    """
    Handles OpenAI chat completions. This is the core chatbot logic.
    It's an asynchronous generator function to stream responses.
    """
    openai_api_key = state.value.get('openai_api_key') # Get OpenAI API key from session state

    if not openai_api_key:
        yield "Please set your OpenAI API Key first.", history # Yield error if key is missing
        return

    try:
        client = OpenAI(api_key=openai_api_key) # Initialize OpenAI client with the provided key

        # Prepare messages for the OpenAI API, including chat history
        messages = []
        for human, ai in history:
            messages.append({"role": "user", "content": human})
            messages.append({"role": "assistant", "content": ai})
        messages.append({"role": "user", "content": message}) # Add the current user message

        # Make a streaming call to OpenAI's chat completions API
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            stream=True # Enable streaming for real-time response generation
        )

        full_response = ""
        # Iterate over chunks received from the streaming response
        for chunk in response:
            if chunk.choices[0].delta.content is not None:
                full_response += chunk.choices[0].delta.content # Accumulate the response
            yield full_response, history # Yield the current accumulated response to Gradio
    except Exception as e:
        yield f"Error with OpenAI: {e}. Check API key and network.", history # Yield error if something goes wrong


# --- Gradio UI ---
with gr.Blocks(title="GitHub Auth & OpenAI Chatbot") as demo:
    # Gradio State object to hold dynamic data across user interactions
    app_state = gr.State(value={'github_tokens': None, 'openai_api_key': None})

    gr.Markdown("""
    # Simple GitHub Auth & OpenAI Chatbot

    ### Note on GitHub Copilot:
    This app authenticates with **GitHub's APIs**, not GitHub Copilot directly.
    For the chatbot, you need a separate **OpenAI API Key**.

    ### Token Persistence:
    GitHub tokens (including the refresh token) are stored **only in this browser session**.
    If you close the browser tab or restart the Gradio application, you will need to re-authenticate.
    However, while the session is active, the app will attempt to refresh the access token using the stored refresh token.
    """)

    # GitHub Authentication Tab
    with gr.Tab("GitHub Authentication"):
        gr.Markdown("## GitHub Device Flow")
        # Input field for GitHub Client ID
        gr.Textbox(
            label="Your GitHub OAuth App Client ID",
            value=GITHUB_CLIENT_ID,
            placeholder="Paste your GitHub OAuth App Client ID here",
            interactive=True,
            elem_id="github_client_id_input" # Unique ID for this element
        )
        # Button to start the authentication process
        auth_button = gr.Button("Start GitHub Authentication")
        # Output fields to display authentication status and codes
        auth_output = gr.Markdown("Status here.")
        verification_uri_output = gr.Textbox(label="Verification URL", interactive=False)
        user_code_output = gr.Textbox(label="User Code", interactive=False)
        status_output = gr.Textbox(label="Auth Status", interactive=False)
        access_token_display = gr.Textbox(label="GitHub Access Token", interactive=False, type="password")
        refresh_token_display = gr.Textbox(label="GitHub Refresh Token", interactive=False, type="password") # Display refresh token

        # Button to fetch GitHub user info using the obtained token
        fetch_user_info_button = gr.Button("Fetch GitHub User Info")
        user_info_output = gr.Textbox(label="GitHub User Info", interactive=False)

        # Event listener for the authentication button click
        auth_button.click(
            authenticate_github_device_flow, # Function to call
            inputs=[gr.Textbox(elem_id="github_client_id_input"), app_state], # Inputs to the function
            outputs=[auth_output, verification_uri_output, user_code_output, status_output, access_token_display, refresh_token_display, app_state] # Outputs from the function
        )
        # Event listener for fetching user info button click
        fetch_user_info_button.click(
            get_github_user_info,
            inputs=[app_state],
            outputs=[user_info_output, app_state]
        )

    # OpenAI Chatbot Tab
    with gr.Tab("OpenAI Chatbot"):
        gr.Markdown("## OpenAI Chatbot")
        # Input field for OpenAI API Key
        gr.Textbox(
            label="Your OpenAI API Key",
            type="password", # Hide the input for security
            placeholder="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            elem_id="openai_api_key_input"
        )
        # Button to set the OpenAI API Key
        set_openai_key_button = gr.Button("Set OpenAI API Key")
        openai_key_status = gr.Textbox(label="OpenAI Key Status", interactive=False)

        # Event listener for setting the OpenAI API Key
        set_openai_key_button.click(
            set_openai_api_key,
            inputs=[gr.Textbox(elem_id="openai_api_key_input"), app_state],
            outputs=[openai_key_status, app_state]
        )

        # Gradio ChatInterface for the chatbot UI
        gr.ChatInterface(
            fn=openai_chat_completion, # Function to handle chat messages
            chatbot=gr.Chatbot(height=400), # Chatbot display area
            textbox=gr.Textbox(placeholder="Ask me anything...", container=False, scale=7), # Input textbox
            clear_btn="Clear Chat", # Button to clear chat history
            submit_btn="Send", # Button to send message
            title="OpenAI Chatbot"
        ).submit( # Event listener for submitting a message
            openai_chat_completion,
            inputs=[gr.Textbox(), gr.Chatbot(), app_state], # Inputs: current message, chat history, app state
            outputs=[gr.Textbox(), gr.Chatbot()] # Outputs: updated textbox (cleared), updated chat history
        )

# Launch the Gradio application
demo.launch()
