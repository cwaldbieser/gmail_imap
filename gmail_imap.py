#! /usr/bin/env python

import datetime
import json
import pathlib
import urllib.parse

import imap_tools
import requests
import tomllib
from dateutil.parser import parse as parse_date
from dateutil.tz import tzlocal

# The URL root for accessing Google Accounts.
GOOGLE_ACCOUNTS_BASE_URL = "https://accounts.google.com"
# Hardcoded redirect URI.
REDIRECT_URI = "https://oauth2.dance/"


def main():
    """
    The main program entrypoint.
    """
    config = get_config()
    imap_config = config.get("imap", {})
    email = imap_config.get("email")
    expired = True
    client_id, client_secret = get_client_config(imap_config)
    token_path = pathlib.Path("~/.gmail_tui/access-tokens.json").expanduser()
    if token_path.exists():
        with open(token_path, "r") as f:
            tokens = json.load(f)
        expires_at = tokens["expires_at"]
        dt_expires = parse_date(expires_at)
        dt = datetime.datetime.today().replace(tzinfo=tzlocal())
        if dt < dt_expires:
            expired = False
        else:
            new_tokens = refresh_tokens(
                client_id, client_secret, tokens["refresh_token"]
            )
            tokens.update(new_tokens)
            print(tokens)
            expired = False
    if expired:
        url = get_access_token_url(client_id)
        print(f"Browse to {url} to obtain an access token.")
        authorization_code = input("Authorization code: ")
        print(authorization_code)
        tokens = get_tokens(client_id, client_secret, authorization_code)
    refresh_token = tokens["refresh_token"]
    access_token = tokens["access_token"]
    expires_in = tokens["expires_in"]
    dt = datetime.datetime.today().replace(tzinfo=tzlocal())
    expires_at = dt + datetime.timedelta(seconds=expires_in)
    print(f"Refresh Token: {refresh_token}")
    print(f"Access Token: {access_token}")
    print(f"Access Token Expiration Seconds: {expires_in}")
    print(f"Access token expires at: {expires_at.isoformat()}")
    tokens["expires_at"] = expires_at.isoformat()
    with open(token_path, "w") as f:
        json.dump(tokens, f, indent=4)
    if email is None:
        email = input("email: ")
    do_imap(email, access_token)


def get_config():
    """
    Get the main config.
    """
    config_path = pathlib.Path("~/.gmail_tui/conf.toml").expanduser()
    with open(config_path, "rb") as f:
        config = tomllib.load(f)
    return config


def get_client_config(imap_config):
    """
    Get Oauth2 Client ID and Client Secret.
    """
    default_credentials_file = "~/.gmail_tui/gmail-imap-client-secret.json"
    credentials_file = imap_config.get(
        "credentials_file", default_credentials_file
    )
    credentials_file = pathlib.Path(credentials_file).expanduser()
    with open(credentials_file) as f:
        o = json.load(f)
    web = o["web"]
    client_id = web["client_id"]
    client_secret = web["client_secret"]
    return client_id, client_secret


def refresh_tokens(client_id, client_secret, refresh_token):
    params = {}
    params["client_id"] = client_id
    params["client_secret"] = client_secret
    params["refresh_token"] = refresh_token
    params["grant_type"] = "refresh_token"
    request_url = accounts_url("o/oauth2/token")
    response = requests.post(request_url, data=params)
    return response.json()


def do_imap(user, access_token):
    """
    Do IMAP stuff.
    """
    with imap_tools.MailBox("imap.gmail.com").xoauth2(user, access_token) as mailbox:
        for msg in mailbox.fetch():
            print(msg.date, msg.subject, len(msg.text or msg.html))


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
