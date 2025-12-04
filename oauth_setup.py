#!/usr/bin/env python3
"""
OAuth Setup Script - Run this ONCE to authorize the app.

This script will:
1. Open your browser to authorize with Google
2. Save the refresh token to youtube_token.json

Usage:
    python oauth_setup.py

After running, copy youtube_token.json to the server/container.
"""

import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials

# Scopes needed for YouTube subscriptions
SCOPES = ['https://www.googleapis.com/auth/youtube.readonly']

CLIENT_SECRETS_FILE = 'google-client.json'
TOKEN_FILE = 'youtube_token.json'


def main():
    print("=" * 50)
    print("YouTube OAuth Setup")
    print("=" * 50)
    print()

    # Check if client secrets exist
    if not os.path.exists(CLIENT_SECRETS_FILE):
        print(f"ERROR: {CLIENT_SECRETS_FILE} not found!")
        print("Download it from Google Cloud Console.")
        return

    # Check if already authorized
    if os.path.exists(TOKEN_FILE):
        print(f"Token file already exists: {TOKEN_FILE}")
        response = input("Do you want to re-authorize? (y/N): ")
        if response.lower() != 'y':
            print("Keeping existing token.")
            return

    print("Opening browser for authorization...")
    print("(If browser doesn't open, check the URL in terminal)")
    print()

    # Run OAuth flow
    flow = InstalledAppFlow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES
    )

    # This will open a browser and wait for authorization
    credentials = flow.run_local_server(
        port=8085,
        prompt='consent',
        access_type='offline'  # Important: gets refresh token
    )

    # Save credentials to file
    token_data = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

    with open(TOKEN_FILE, 'w') as f:
        json.dump(token_data, f, indent=2)

    print()
    print("=" * 50)
    print("SUCCESS!")
    print("=" * 50)
    print(f"Token saved to: {TOKEN_FILE}")
    print()
    print("Next steps:")
    print("1. The token file contains your refresh token")
    print("2. Copy this file to your server/container")
    print("3. The app will use it to access your subscriptions")
    print()
    print("The refresh token does NOT expire unless you revoke access.")


if __name__ == '__main__':
    main()
