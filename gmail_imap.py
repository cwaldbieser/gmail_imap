#! /usr/bin/env python

import argparse
import datetime
import http.server
import imaplib
import json
import pathlib
import socket
import socketserver
import sys
import time
import traceback
import urllib
import urllib.parse
from collections import OrderedDict
from io import StringIO
from itertools import islice

import requests
import tomllib
from dateutil.parser import parse as parse_date
from dateutil.tz import tzlocal
from imap_tools import A, MailBox, MailboxLoginError, MailboxLogoutError

# The URL root for accessing Google Accounts.
GOOGLE_ACCOUNTS_BASE_URL = "https://accounts.google.com"
# Hardcoded redirect URI.
# REDIRECT_URI = "https://oauth2.dance/"
REDIRECT_URI = "http://localhost:10077/"


class Server(socketserver.TCPServer):

    serving = False
    captured_path = None
    # Avoid "address already used" error when frequently restarting the script
    allow_reuse_address = True


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200, "OK")
        self.end_headers()
        self.wfile.write(
            "Authentication code has been delivered to the application.".encode("utf-8")
        )
        self.server.captured_path = self.path
        self.server.serving = False


def main(args):
    """
    The main program entrypoint.
    """
    config = get_config()
    imap_config = config.get("imap", {})
    email = imap_config.get("email")
    expired = True
    client_id, client_secret = get_client_config(imap_config)
    token_path = pathlib.Path("~/.gmail_tui/access-tokens.json").expanduser()
    if (not args.reauthenticate) and token_path.exists():
        with open(token_path, "r") as f:
            tokens = json.load(f)
        expires_at = tokens["expires_at"]
        dt_expires = parse_date(expires_at)
        dt = datetime.datetime.today().replace(tzinfo=tzlocal())
        if dt < dt_expires:
            print("Access token is still valid.", file=sys.stderr)
            expired = False
        else:
            print("Refreshing tokens ...", file=sys.stderr)
            new_tokens = refresh_tokens(
                client_id, client_secret, tokens["refresh_token"]
            )
            tokens.update(new_tokens)
            print(tokens)
            print("Tokens refreshed.", file=sys.stderr)
            expired = False
    if expired:
        url = get_access_token_url(client_id)
        print(f"Browse to {url} to obtain an access token.")
        # authorization_code = input("Authorization code: ")
        authorization_code = get_authorization_code_from_web_server()
        print(authorization_code)
        tokens = get_tokens(client_id, client_secret, authorization_code)
    refresh_token = tokens["refresh_token"]
    access_token = tokens["access_token"]
    expires_in = tokens["expires_in"]
    issued_at = tokens["issued_at"]
    dt = parse_date(issued_at)
    expires_at = dt + datetime.timedelta(seconds=expires_in)
    print(f"Refresh Token: {refresh_token}")
    print(f"Access Token: {access_token}")
    print(f"Access Token issued at: {issued_at}")
    print(f"Access Token Expiration Seconds: {expires_in}")
    print(f"Access token expires at: {expires_at.isoformat()}")
    tokens["expires_at"] = expires_at.isoformat()
    with open(token_path, "w") as f:
        json.dump(tokens, f, indent=4)
    if email is None:
        email = input("email: ")
    try:
        do_imap(email, access_token)
    except imaplib.IMAP4.error as ex:
        print(ex.args[0])


def get_authorization_code_from_web_server():
    """
    Create a web server on localhost:10077.
    Wait for authorization code.
    """
    with Server(("", 10077), Handler) as httpd:
        httpd.serving = True
        while httpd.serving:
            httpd.handle_request()
    print(f"Captured path: {httpd.captured_path}")
    p = urllib.parse.urlparse(httpd.captured_path)
    qs = urllib.parse.parse_qs(p.query)
    access_code = qs["code"][0]
    return access_code


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
    credentials_file = imap_config.get("credentials_file", default_credentials_file)
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
    tokens = response.json()
    issued_at = datetime.datetime.today().replace(tzinfo=tzlocal())
    tokens["issued_at"] = issued_at.isoformat()
    return tokens


def parse_fetch_google_ids_response(response):
    """
    Parse fetch response.
    """
    status = response[0]
    if status != "OK":
        return []
    pieces = response[1]
    buffer = StringIO()
    results = {}
    for piece in pieces:
        buffer.write(piece.decode())
        value = buffer.getvalue()
        if value.endswith(")"):
            uid, gmessage_id, gthread_id = parse_g_result(value)
            results[uid] = (gmessage_id, gthread_id)
            buffer.seek(0)
            buffer.truncate()
    return results


def parse_g_result(value):
    """
    Parse an individual fetch result for Google message and thread IDs.
    """
    parts = value.split(" ", 1)
    components = parts[1][1:-1].split()
    part_map = {}
    for n in range(1, len(components), 2):
        part_map[components[n - 1]] = components[n]
    return part_map["UID"], part_map["X-GM-MSGID"], part_map["X-GM-THRID"]


def batched(iterable, n):
    "Batch data into tuples of length n. The last batch may be shorter."
    # batched('ABCDEFG', 3) --> ABC DEF G
    if n < 1:
        raise ValueError("n must be at least one")
    it = iter(iterable)
    while batch := tuple(islice(it, n)):
        yield batch


def fetch_gmail_messages_in_batches(
    mailbox, batch_size=100, headers_only=True, limit=None
):
    """
    Fetch messages in batches and decorate with Google IDs.
    """
    msg_generator = mailbox.fetch(
        reverse=True,
        headers_only=headers_only,
        mark_seen=False,
        bulk=batch_size,
        limit=limit,
    )
    for msg_batch in batched(msg_generator, batch_size):
        messages = OrderedDict()
        for msg in msg_batch:
            messages[msg.uid] = dict(msg=msg)
        uids = list(int(uid) for uid in messages.keys())
        max_uid = max(uids)
        min_uid = min(uids)
        client = mailbox.client
        response = client.uid(
            "fetch", f"{min_uid}:{max_uid}", "(X-GM-MSGID X-GM-THRID)"
        )
        results = parse_fetch_google_ids_response(response)
        for uid, (gmessage_id, gthread_id) in results.items():
            msg_wrapper = messages.get(uid)
            if msg_wrapper:
                msg_wrapper["gmessage_id"] = gmessage_id
                msg_wrapper["gthread_id"] = gthread_id
        for msg_wrapper in messages.values():
            msg = msg_wrapper["msg"]
            gmessage_id = msg_wrapper.get("gmessage_id")
            gthread_id = msg_wrapper["gthread_id"]
            yield gmessage_id, gthread_id, msg


def do_imap(user, access_token):
    """
    Do IMAP stuff.
    """
    if False:
        with MailBox("imap.gmail.com").xoauth2(user, access_token) as mailbox:
            for gmessage_id, gthread_id, msg in fetch_gmail_messages_in_batches(
                mailbox, batch_size=500, headers_only=True, limit=500
            ):
                print(gmessage_id, gthread_id, msg.uid, msg.date, msg.subject)
                print(msg.flags)

        # flags = (imap_tools.MailMessageFlags.FLAGGED,)
        # result = mailbox.flag("81180", flags, True)
        # print(f"flag() result: {result}")
        # result = mailbox.copy(["81180"], "[Gmail]/Starred")
        # result = mailbox.copy(["81180"], "[Gmail]/Important")
        # print(f"copy() result: {result}")
        # pprint.pprint(mailbox.folder.list())

        # import pprint
        # client = mailbox.client
        # response = client.fetch(b"1:20", "(X-GM-MSGID X-GM-THRID)")
        # pprint.pprint(response)

        # import pprint
        # client = mailbox.client
        # response = client.uid("fetch", "81207:*", "(X-GM-MSGID X-GM-THRID)")
        # pprint.pprint(response)

        # Full GMail search - works
        # import pprint
        # result = mailbox.uids("X-GM-RAW in:starred")
        # pprint.pprint(result)

    done = False
    while not done:
        connection_start_time = time.monotonic()
        connection_live_time = 0.0
        try:
            print("Connecting to GMail IMAP ...")
            with MailBox("imap.gmail.com").xoauth2(user, access_token) as mailbox:
                print("@@ new connection", time.asctime())
                while connection_live_time < 29 * 60:
                    try:
                        responses = mailbox.idle.wait(timeout=3 * 60)
                        print(time.asctime(), "IDLE responses:", responses)
                        if responses:
                            for msg in mailbox.fetch(A(seen=False), mark_seen=False):
                                print("->", msg.date, msg.subject)
                    except KeyboardInterrupt:
                        print("~KeyboardInterrupt")
                        done = True
                        break
                    connection_live_time = time.monotonic() - connection_start_time
        except (
            TimeoutError,
            ConnectionError,
            imaplib.IMAP4.abort,
            MailboxLoginError,
            MailboxLogoutError,
            socket.herror,
            socket.gaierror,
            socket.timeout,
        ) as e:
            print(f"## Error\n{e}\n{traceback.format_exc()}\nreconnect in a minute...")
            time.sleep(60)

        try:
            while True:
                with mailbox.idle as idle:
                    responses = idle.poll(timeout=60)
                if responses:
                    print(f"responses: {responses}\n")
                    # for msg in mailbox.fetch(
                    #     A(seen=False),
                    #     reverse=True,
                    #     mark_seen=False,
                    #     headers_only=True,
                    #     bulk=False,
                    # ):
                    #     print(msg.date, msg.subject)
                else:
                    print("no any updates")
                time.sleep(2)
        except KeyboardInterrupt:
            pass


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
    tokens = response.json()
    issued_at = datetime.datetime.today().replace(tzinfo=tzlocal())
    tokens["issued_at"] = issued_at.isoformat()
    return tokens


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
    parser = argparse.ArgumentParser("IMAP test for GMail")
    parser.add_argument(
        "-r",
        "--reauthenticate",
        action="store_true",
        help="Force Oauth2 reauthentication.")
    args = parser.parse_args()
    main(args)
