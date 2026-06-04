"""
GitHub Integration helper for Flask
- GitHub App flow: create signed JWT (app identity) and exchange for installation access tokens
- OAuth flow: redirect to GitHub, exchange code for user access token
Requirements: PyJWT, requests, cryptography
Environment variables:
- GITHUB_APP_ID: numeric GitHub App ID
- GITHUB_APP_PRIVATE_KEY_PATH (or GITHUB_APP_PRIVATE_KEY): path to PEM file OR raw PEM content
- GITHUB_CLIENT_ID: OAuth App client id (for OAuth flow)
- GITHUB_CLIENT_SECRET: OAuth App client secret (for OAuth flow)
- OAUTH_REDIRECT_URI: callback URI registered in GitHub OAuth App
"""
import os
import time
import json
from flask import Blueprint, current_app, request, jsonify, redirect, url_for
import jwt  # PyJWT
import requests

bp = Blueprint("github_integration", __name__, url_prefix="/api/github")

GITHUB_API_BASE = "https://api.github.com"
GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"


def load_private_key():
    """
    Load GitHub App private key either from env var or file path.
    Return PEM string.
    """
    pem = os.getenv("GITHUB_APP_PRIVATE_KEY")
    if pem:
        return pem
    path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
    if path and os.path.exists(path):
        with open(path, "r") as f:
            return f.read()
    raise RuntimeError("GitHub App private key not configured. Set GITHUB_APP_PRIVATE_KEY or GITHUB_APP_PRIVATE_KEY_PATH.")


def make_jwt():
    """
    Create a signed JWT for the GitHub App using RS256.
    JWT valid for a short time (e.g. 10 minutes).
    """
    app_id = os.getenv("GITHUB_APP_ID")
    if not app_id:
        raise RuntimeError("GITHUB_APP_ID not set")

    private_key = load_private_key()
    now = int(time.time())
    payload = {
        "iat": now - 60,            # issued at (allow small clock skew)
        "exp": now + (10 * 60),     # expires after 10 minutes
        "iss": str(app_id)
    }
    token = jwt.encode(payload, private_key, algorithm="RS256")
    # In PyJWT >=2.x, encode returns str
    return token


def app_api_get(path, jwt_token=None, **kwargs):
    """
    Generic GET to GitHub API authenticated as the app (JWT).
    """
    if jwt_token is None:
        jwt_token = make_jwt()
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json"
    }
    return requests.get(GITHUB_API_BASE + path, headers=headers, **kwargs)


def app_api_post(path, jwt_token=None, data=None, **kwargs):
    if jwt_token is None:
        jwt_token = make_jwt()
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json"
    }
    return requests.post(GITHUB_API_BASE + path, headers=headers, json=data, **kwargs)


@bp.route("/app/installations", methods=["GET"])
def list_installations():
    """
    List installations of the GitHub App.
    Requires the service to have a valid app private key and APP ID.
    """
    try:
        r = app_api_get("/app/installations")
        r.raise_for_status()
        return jsonify({"success": True, "installations": r.json()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "details": getattr(e, "response", None) and getattr(e.response, "text", None)}), 500


@bp.route("/app/installation-token", methods=["POST"])
def create_installation_token():
    """
    Create an installation access token for a given installation.
    POST body: { "installation_id": 12345, "permissions": { ... } (optional), "repository_ids": [ ... ] (optional) }
    """
    data = request.get_json() or {}
    installation_id = data.get("installation_id") or request.args.get("installation_id")
    if not installation_id:
        return jsonify({"success": False, "error": "installation_id required"}), 400

    # Optionally pass permissions/repositories to limit token scope
    payload = {}
    if "permissions" in data:
        payload["permissions"] = data["permissions"]
    if "repository_ids" in data:
        payload["repository_ids"] = data["repository_ids"]

    try:
        path = f"/app/installations/{installation_id}/access_tokens"
        r = app_api_post(path, data=payload)
        r.raise_for_status()
        return jsonify({"success": True, "token": r.json()})
    except Exception as e:
        text = getattr(e, "response", None) and getattr(e.response, "text", None)
        return jsonify({"success": False, "error": str(e), "details": text}), 500


# Optional helper: exchange App JWT for installation id list for a given account.
@bp.route("/app/find-installation", methods=["GET"])
def find_installation_for_account():
    """
    Query installations and find the one for a given account (owner) or repo.
    Query params:
    - owner: account or org login
    """
    owner = request.args.get("owner")
    if not owner:
        return jsonify({"success": False, "error": "owner query parameter required"}), 400
    try:
        # List installations then filter
        r = app_api_get("/app/installations")
        r.raise_for_status()
        items = r.json()
        for inst in items:
            target = inst.get("account", {}).get("login")
            if target and target.lower() == owner.lower():
                return jsonify({"success": True, "installation": inst})
        return jsonify({"success": False, "error": "installation not found for owner"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ----------------------------
# OAuth web app flow endpoints
# ----------------------------
@bp.route("/oauth/login", methods=["GET"])
def oauth_login():
    """
    Redirect user to GitHub to authorize the OAuth App.
    Query params:
    - scope: optional scopes (default: repo,user)
    - state: optional CSRF state
    """
    client_id = os.getenv("GITHUB_CLIENT_ID")
    redirect_uri = os.getenv("OAUTH_REDIRECT_URI")  # must match OAuth app setting
    if not client_id or not redirect_uri:
        return jsonify({"success": False, "error": "OAuth client not configured (GITHUB_CLIENT_ID or OAUTH_REDIRECT_URI missing)"}), 500

    scope = request.args.get("scope", "repo")  # choose minimal scopes needed
    state = request.args.get("state", "")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
    }
    if state:
        params["state"] = state
    url = requests.Request("GET", GITHUB_OAUTH_AUTHORIZE_URL, params=params).prepare().url
    return redirect(url)


@bp.route("/oauth/callback", methods=["GET"])
def oauth_callback():
    """
    Callback endpoint to exchange code for access token.
    Query params: code, state
    """
    code = request.args.get("code")
    state = request.args.get("state")
    client_id = os.getenv("GITHUB_CLIENT_ID")
    client_secret = os.getenv("GITHUB_CLIENT_SECRET")
    if not code or not client_id or not client_secret:
        return jsonify({"success": False, "error": "Missing code or client credentials"}), 400

    headers = {"Accept": "application/json"}
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
    }
    # Optionally include redirect_uri if used in initial request
    redirect_uri = os.getenv("OAUTH_REDIRECT_URI")
    if redirect_uri:
        payload["redirect_uri"] = redirect_uri

    try:
        r = requests.post(GITHUB_OAUTH_TOKEN_URL, headers=headers, data=payload, timeout=10)
        r.raise_for_status()
        token_data = r.json()
        # token_data: { access_token, scope, token_type }
        # For security, do NOT return raw token in production; instead, create a server session/cookie.
        return jsonify({"success": True, "token": token_data, "state": state})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "details": getattr(e, "response", None) and getattr(e.response, "text", None)}), 500


@bp.route("/oauth/user", methods=["GET"])
def oauth_get_user():
    """
    Given a bearer token (pass as Authorization header), fetch the authenticated user.
    """
    auth = request.headers.get("Authorization") or request.args.get("access_token")
    if not auth:
        return jsonify({"success": False, "error": "Authorization header or access_token param required"}), 400

    if not auth.startswith("token") and not auth.startswith("Bearer"):
        # support raw token in query param
        if "access_token" in request.args:
            token = request.args.get("access_token")
            headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        else:
            return jsonify({"success": False, "error": "Invalid Authorization format"}), 400
    else:
        headers = {"Authorization": auth, "Accept": "application/vnd.github+json"}

    try:
        r = requests.get(GITHUB_API_BASE + "/user", headers=headers)
        r.raise_for_status()
        return jsonify({"success": True, "user": r.json()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "details": getattr(e, "response", None) and getattr(e.response, "text", None)}), 500
