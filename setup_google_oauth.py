from google_auth import get_google_credentials


if __name__ == "__main__":
    credentials, auth_type = get_google_credentials(allow_interactive=True)
    if credentials and auth_type == "OAuth user":
        print("Google OAuth setup complete. Token saved to data/google_token.json.")
    else:
        print("Google OAuth setup failed. Make sure oauth_credentials.json exists.")
