import gradio as gr
import requests
import time
import json
import os
from datetime import datetime, timedelta
from openai import OpenAI # Using the official OpenAI Python client

# --- GitHub OAuth Configuration ---
# IMPORTANT: Replace with your GitHub OAuth App Client ID
# You need to create a GitHub OAuth App and enable "Device Flow"
# No client secret is needed for the Device Flow.
# Go to: https://github.com/settings/applications/new
# - Application name: (e.g., My Gradio App)
# - Homepage URL: (e.g., your Gradio share URL)
# - Authorization callback URL: (e.g., your Gradio share URL - not strictly used for Device Flow but good practice)
# Make sure to enable "Enable Device Flow" in the OAuth App settings.
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "YOUR_GITHUB_CLIENT_ID_HERE")
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_USER_API_URL = "https://api.github.com/user"

# --- OpenAI Configuration ---
# You will provide this via Gradio UI for security
OPENAI_MODEL = "gpt-3.5-turbo" # Or "gpt-4", etc.

async def authenticate_github_device_flow(client_id_input, state: gr.State):
    """Initiates the GitHub Device Flow and gets tokens."""
    if client_id_input == "YOUR_GITHUB_CLIENT_ID_HERE" or not client_id_input:
        return (
            "Please replace 'YOUR_GITHUB_CLIENT_ID_HERE' with your actual GitHub OAuth App Client ID.",
            "", "", "", "", "", state
        )

    # 1. Request device code
    headers = {'Accept': 'application/json'}
    data = {'client_id': client_id_input, 'scope': 'user'} # Request 'user' scope to get user info
    try:
        response = requests.post(GITHUB_DEVICE_CODE_URL, headers=headers, data=data)
        response.raise_for_status()
        device_code_data = response.json()
    except requests.exceptions.RequestException as e:
        return (
            f"Error requesting device code: {e}",
            "", "", "", "", "", state
        )

    device_code = device_code_data.get('device_code')
    user_code = device_code_data.get('user_code')
    verification_uri = device_code_data.get('verification_uri')
    interval = device_code_data.get('interval', 5) # Polling interval in seconds

    if not all([device_code, user_code, verification_uri]):
        return (
            "Failed to get device codes. Please check your Client ID and network.",
            "", "", "", "", "", state
        )

    auth_message = (
        f"Please go to: [ {verification_uri} ]( {verification_uri} )\n"
        f"And enter the code: **{user_code}**\n"
        f"Waiting for authorization..."
    )
    yield (
        auth_message,
        verification_uri,
        user_code,
        "Waiting...", # Status
        "", # Access Token
        "", # Refresh Token
        state # Pass state through
    )

    # 2. Poll for access token
    start_time = time.time()
    # GitHub device flow has a 15-minute expiration for the device code itself
    timeout = 900 # 15 minutes * 60 seconds

    while time.time() - start_time < timeout:
        token_data = {
            'client_id': client_id_input,
            'device_code': device_code,
            'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'
        }
        try:
            token_response = requests.post(GITHUB_TOKEN_URL, headers=headers, data=token_data)
            token_response.raise_for_status()
            token_json = token_response.json()

            if 'access_token' in token_json:
                access_token = token_json['access_token']
                refresh_token = token_json.get('refresh_token')
                expires_in = token_json.get('expires_in', 3600 * 8) # Default to 8 hours if not specified

                # Update Gradio state - tokens are now in-memory only
                state.value['github_tokens'] = {
                    'access_token': access_token,
                    'refresh_token': refresh_token,
                    'expires_at': (datetime.now() + timedelta(seconds=expires_in)).isoformat()
                }

                return (
                    f"Authentication successful! Access Token obtained.",
                    verification_uri,
                    user_code,
                    "Authenticated!",
                    access_token,
                    refresh_token,
                    state
                )
            elif token_json.get('error') == 'authorization_pending':
                yield (
                    f"{auth_message}\nStatus: Authorization pending...",
                    verification_uri,
                    user_code,
                    "Pending...",
                    "", "", state
                )
            elif token_json.get('error') == 'slow_down':
                interval = token_json.get('interval', interval + 5) # Increase interval if requested
                yield (
                    f"{auth_message}\nStatus: Slow down, increasing poll interval to {interval}s...",
                    verification_uri,
                    user_code,
                    "Slow Down...",
                    "", "", state
                )
            else:
                return (
                    f"Authentication failed: {token_json.get('error_description', token_json.get('error', 'Unknown error'))}",
                    "", "", "", "", "", state
                )
        except requests.exceptions.RequestException as e:
            return (
                f"Error polling for token: {e}",
                "", "", "", "", "", state
            )
        except json.JSONDecodeError as e:
            return (
                f"JSON decode error during token polling: {e}. Response: {token_response.text}",
                "", "", "", "", "", state
            )

        time.sleep(interval)

    return (
        "Authentication timed out. Please try again.",
        "", "", "", "", "", state
    )

async def get_fresh_github_token(state: gr.State):
    """
    Retrieves a fresh GitHub access token from the current session state.
    If expired, it will attempt to refresh it.
    NOTE: With no persistence (like Firestore), if the app restarts,
    the user will need to re-authenticate via the Device Flow.
    """
    current_tokens = state.value.get('github_tokens', {})
    access_token = current_tokens.get('access_token')
    refresh_token = current_tokens.get('refresh_token')
    expires_at_str = current_tokens.get('expires_at')
    expires_at = datetime.fromisoformat(expires_at_str) if expires_at_str else None

    # 1. Check if current access token in state is valid
    if access_token and expires_at and datetime.now() < expires_at:
        print("Using valid access token from Gradio session state.")
        return access_token, "Using existing valid access token."

    # 2. Access token expired or not found, try to refresh using stored refresh token (if available)
    if refresh_token:
        print("Access token expired. Attempting to refresh token...")
        new_tokens = await refresh_github_token(refresh_token)
        if new_tokens:
            new_access_token = new_tokens['access_token']
            new_refresh_token = new_tokens.get('refresh_token', refresh_token) # Use new refresh token if provided
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
            # Clear potentially bad tokens from state
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
    """Fetches GitHub user information using the access token."""
    access_token, status_message = await get_fresh_github_token(state)

    if not access_token:
        return f"Error: {status_message}", state

    headers = {'Authorization': f'token {access_token}'}
    try:
        response = requests.get(GITHUB_USER_API_URL, headers=headers)
        response.raise_for_status()
        user_info = response.json()
        return f"Successfully fetched GitHub user info for: **{user_info.get('login', 'N/A')}** (ID: {user_info.get('id', 'N/A')})\n\n{json.dumps(user_info, indent=2)}", state
    except requests.exceptions.RequestException as e:
        return f"Error fetching GitHub user info: {e}. You might need to re-authenticate.", state
    except json.JSONDecodeError as e:
        return f"JSON decode error fetching GitHub user info: {e}. Response: {response.text}", state

async def clear_github_tokens(state: gr.State):
    """Clears GitHub tokens from session state."""
    if 'github_tokens' in state.value:
        del state.value['github_tokens']
        print("GitHub tokens cleared from session state.")
        return "GitHub tokens cleared from session state.", state
    return "No GitHub tokens to clear.", state

def set_openai_api_key(api_key, state: gr.State):
    """Sets the OpenAI API key in the session state."""
    state.value['openai_api_key'] = api_key
    if api_key:
        return "OpenAI API Key set successfully!", state
    else:
        return "OpenAI API Key cleared.", state

async def openai_chat_completion(message, history, state: gr.State):
    """Handles OpenAI chat completions."""
    openai_api_key = state.value.get('openai_api_key')

    if not openai_api_key:
        yield "Please set your OpenAI API Key in the 'OpenAI Chatbot' section first.", history
        return

    try:
        client = OpenAI(api_key=openai_api_key)

        messages = []
        for human, ai in history:
            messages.append({"role": "user", "content": human})
            messages.append({"role": "assistant", "content": ai})
        messages.append({"role": "user", "content": message})

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            stream=True
        )

        full_response = ""
        for chunk in response:
            if chunk.choices[0].delta.content is not None:
                full_response += chunk.choices[0].delta.content
            yield full_response, history
    except Exception as e:
        yield f"Error communicating with OpenAI: {e}. Please check your API key and network connection.", history


# --- Gradio UI ---
with gr.Blocks(title="GitHub Auth & OpenAI Chatbot") as demo:
    # State to hold GitHub tokens and OpenAI API key
    app_state = gr.State(value={
        'github_tokens': None,
        'openai_api_key': None
    })

    gr.Markdown(f"""
    # GitHub Authentication & OpenAI Chatbot
    This application demonstrates GitHub Device Flow authentication
    and a separate OpenAI-powered chatbot.

    ---
    ### Important Note on GitHub Copilot:
    The GitHub Device Flow provides an access token for **GitHub's APIs** (e.g., to read your profile, repositories).
    It does **NOT** provide a direct "GitHub Copilot access token" that can be used to call external AI models like OpenAI.
    For the chatbot functionality, you will need to provide your **OpenAI API Key** separately.

    ### Token Persistence Note:
    GitHub tokens are currently stored **only in the browser session (in-memory)**.
    This means if you close the browser tab or restart the Gradio application,
    you will need to re-authenticate with GitHub.
    If you need persistent storage, please let me know, and we can explore options like local files or databases.
    ---
    """)

    with gr.Tab("GitHub Authentication"):
        gr.Markdown("## GitHub Device Flow Authentication")
        gr.Markdown("""
        1.  Go to [GitHub Developer Settings](https://github.com/settings/applications/new) and create a new OAuth App.
        2.  Fill in Application name, Homepage URL (can be your Gradio share URL), and Authorization callback URL (can also be your Gradio share URL).
        3.  **Crucially, enable "Enable Device Flow"** in the OAuth App settings.
        4.  Copy your **Client ID** and paste it below.
        """)
        github_client_id_input = gr.Textbox(
            label="Your GitHub OAuth App Client ID",
            value=GITHUB_CLIENT_ID,
            placeholder="Paste your GitHub OAuth App Client ID here"
        )
        auth_button = gr.Button("Start GitHub Device Flow Authentication")
        auth_output = gr.Markdown("Authentication status will appear here.")
        verification_uri_output = gr.Textbox(label="Verification URL", interactive=False)
        user_code_output = gr.Textbox(label="User Code", interactive=False)
        status_output = gr.Textbox(label="Status", interactive=False)
        access_token_display = gr.Textbox(label="GitHub Access Token (for debugging)", interactive=False, type="password")
        refresh_token_display = gr.Textbox(label="GitHub Refresh Token (for debugging)", interactive=False, type="password")

        fetch_user_info_button = gr.Button("Fetch GitHub User Info (requires authentication)")
        user_info_output = gr.Textbox(label="GitHub User Information", interactive=False, lines=10)

        clear_tokens_button = gr.Button("Clear Stored GitHub Tokens")
        clear_tokens_status = gr.Textbox(label="Clear Status", interactive=False)

        auth_button.click(
            authenticate_github_device_flow,
            inputs=[github_client_id_input, app_state],
            outputs=[auth_output, verification_uri_output, user_code_output, status_output, access_token_display, refresh_token_display, app_state]
        )
        fetch_user_info_button.click(
            get_github_user_info,
            inputs=[app_state],
            outputs=[user_info_output, app_state]
        )
        clear_tokens_button.click(
            clear_github_tokens,
            inputs=[app_state],
            outputs=[clear_tokens_status, app_state]
        )

    with gr.Tab("OpenAI Chatbot"):
        gr.Markdown("## OpenAI Chatbot")
        gr.Markdown("""
        Enter your OpenAI API Key below to enable the chatbot.
        You can get your API key from [OpenAI Platform](https://platform.openai.com/account/api-keys).
        """)
        openai_api_key_input = gr.Textbox(
            label="Your OpenAI API Key",
            type="password",
            placeholder="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        )
        set_openai_key_button = gr.Button("Set OpenAI API Key")
        openai_key_status = gr.Textbox(label="OpenAI Key Status", interactive=False)

        set_openai_key_button.click(
            set_openai_api_key,
            inputs=[openai_api_key_input, app_state],
            outputs=[openai_key_status, app_state]
        )

        chatbot = gr.ChatInterface(
            fn=openai_chat_completion,
            chatbot=gr.Chatbot(height=400),
            textbox=gr.Textbox(placeholder="Ask me anything...", container=False, scale=7),
            clear_btn="Clear Chat",
            submit_btn="Send",
            examples=["What is the capital of France?", "Explain quantum computing simply.", "Write a short poem about a cat."],
            title="OpenAI Chatbot"
        )
        # Pass the state to the chat interface function
        chatbot.submit(
            openai_chat_completion,
            inputs=[chatbot.textbox, chatbot.chatbot, app_state],
            outputs=[chatbot.textbox, chatbot.chatbot]
        )

# Launch the Gradio app
demo.launch()
