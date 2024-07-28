#! /usr/bin/env python

import json
import pathlib
import urllib.parse

import requests

# The URL root for accessing Google Accounts.
GOOGLE_ACCOUNTS_BASE_URL = "https://accounts.google.com"
# Hardcoded redirect URI.
REDIRECT_URI = "https://oauth2.dance/"


def main():
    with open(
        pathlib.Path("~/.gmail_tui/gmail-imap-client-secret.json").expanduser()
    ) as f:
        o = json.load(f)
    web = o["web"]
    client_id = web["client_id"]
    client_secret = web["client_secret"]
    url = get_access_token_url(client_id)
    print(f"Browse to {url} to obtain an access token.")
    authorization_code = input("Authorization code: ")
    print(authorization_code)
    tokens = get_tokens(client_id, client_secret, authorization_code)
    refresh_token = tokens["refresh_token"]
    access_token = tokens["access_token"]
    expires_in = tokens["expires_in"]
    print(f"Refresh Token: {refresh_token}")
    print(f"Access Token: {access_token}")
    print(f"Access Token Expiration Seconds: {expires_in}")


def get_tokens(client_id, client_secret, authorization_code):
    """
    Get authorization tokens.
    """
    params = {}
    params["client_id"] = client_id
    params["client_secret"] = client_secret
    params["code"] = authorization_code
    params["redirect_uri"] = REDIRECT_URI
    params["grant_type"] = "authorization_code"
    request_url = accounts_url("o/oauth2/token")

    print(f"request url: {request_url}")
    print(f"params: {params}")
    response = requests.post(request_url, data=params)
    print(f"status: {response.status_code}")
    print(f"text: {response.text}")
    return response.json()


def accounts_url(command):
    """
    Generate Google Accounts URL.
    """
    return f"{GOOGLE_ACCOUNTS_BASE_URL}/{command}"


def format_url_params(params):
    """
    Format URL params.
    """
    param_fragments = []
    for param in sorted(params.items(), key=lambda x: x[0]):
        param_fragments.append(f"{param[0]}={url_escape(param[1])}")
    return "&".join(param_fragments)


def url_escape(text):
    return urllib.parse.quote(text, safe="~-._")


def get_access_token_url(client_id):
    """
    Get authorization tokens.
    """
    scope = "https://mail.google.com/"
    params = {}
    params["client_id"] = client_id
    params["redirect_uri"] = REDIRECT_URI
    params["scope"] = scope
    params["response_type"] = "code"
    params["access_type"] = "offline"
    params["prompt"] = "consent"
    return f"{accounts_url('o/oauth2/auth')}?{format_url_params(params)}"


if __name__ == "__main__":
    main()
