"""MCP OAuth Manager — handles OAuth2 flows and token storage for MCP servers.

Supports the MCP 2025-03-26 OAuth specification:
- Authorization Server Metadata discovery (RFC 8414)
- Dynamic Client Registration (RFC 7591)
- PKCE (required by spec)
- Token storage and retrieval
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog
from mcp.client.auth import PKCEParameters
from mcp.client.auth.oauth2 import (
    build_oauth_authorization_server_metadata_discovery_urls,
    create_client_registration_request,
    create_oauth_metadata_request,
    handle_auth_metadata_response,
    handle_registration_response,
)
from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata, OAuthToken

log = structlog.get_logger()

_CLIENT_NAME = "Hermit WebUI"


class McpOAuthManager:
    """Manages OAuth2 authentication for MCP HTTP servers.

    Handles:
    - Token storage/retrieval per server in ~/.hermit/mcp-oauth/
    - OAuth2 Authorization Code flow with PKCE
    - Dynamic client registration
    - Server metadata discovery
    """

    def __init__(self, base_dir: Path) -> None:
        self._dir = base_dir / "mcp-oauth"
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Token storage
    # ------------------------------------------------------------------

    def get_stored_token(self, server_name: str) -> str | None:
        """Return the stored access token for *server_name*, or None."""
        path = self._dir / f"{server_name}_tokens.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            token = data.get("access_token", "")
            if not token:
                return None
            # Check expiry if available
            expires_at = data.get("expires_at")
            if expires_at and time.time() > expires_at:
                log.info("mcp_oauth_token_expired", server=server_name)
                return None
            return str(token)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("mcp_oauth_read_error", server=server_name, error=str(exc))
            return None

    def store_token(self, server_name: str, token: OAuthToken) -> None:
        """Persist an OAuth token for *server_name*."""
        path = self._dir / f"{server_name}_tokens.json"
        data: dict[str, Any] = {
            "access_token": token.access_token,
            "token_type": token.token_type,
            "stored_at": time.time(),
        }
        if token.refresh_token:
            data["refresh_token"] = token.refresh_token
        if token.expires_in:
            data["expires_at"] = time.time() + token.expires_in
            data["expires_in"] = token.expires_in
        if token.scope:
            data["scope"] = token.scope
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.info("mcp_oauth_token_stored", server=server_name)

    def clear_token(self, server_name: str) -> None:
        """Remove stored tokens for *server_name*."""
        for suffix in ("_tokens.json", "_client.json"):
            path = self._dir / f"{server_name}{suffix}"
            if path.is_file():
                path.unlink()
        log.info("mcp_oauth_token_cleared", server=server_name)

    def has_token(self, server_name: str) -> bool:
        """Check whether a (possibly expired) token file exists."""
        return (self._dir / f"{server_name}_tokens.json").is_file()

    # ------------------------------------------------------------------
    # OAuth2 flow — start
    # ------------------------------------------------------------------

    def start_oauth_flow(
        self,
        server_name: str,
        server_url: str,
        callback_url: str,
    ) -> str:
        """Start an OAuth2 Authorization Code + PKCE flow.

        Returns the authorization URL that should be opened in a browser.
        Raises if the server does not support OAuth.
        """
        # 1. Discover OAuth metadata
        metadata = self._discover_metadata(server_url)
        if metadata is None:
            raise ValueError(f"MCP server at {server_url} does not expose OAuth metadata")

        # 2. Dynamic client registration (if supported)
        client_info = self._register_client(metadata, server_url, callback_url)

        # 3. Generate PKCE
        pkce = PKCEParameters.generate()

        # 4. Generate state
        state = secrets.token_urlsafe(32)

        # 5. Build authorization URL
        auth_endpoint = str(metadata.authorization_endpoint)
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": client_info["client_id"],
            "redirect_uri": callback_url,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        if metadata.scopes_supported:
            params["scope"] = " ".join(metadata.scopes_supported)

        sep = "&" if "?" in auth_endpoint else "?"
        auth_url = f"{auth_endpoint}{sep}{urlencode(params)}"

        # 6. Store pending state for callback
        self._store_pending(
            state,
            {
                "server_name": server_name,
                "server_url": server_url,
                "code_verifier": pkce.code_verifier,
                "client_id": client_info["client_id"],
                "client_secret": client_info.get("client_secret"),
                "token_endpoint": str(metadata.token_endpoint),
                "redirect_uri": callback_url,
                "created_at": time.time(),
            },
        )

        log.info("mcp_oauth_flow_started", server=server_name, state=state[:8])
        return auth_url

    # ------------------------------------------------------------------
    # OAuth2 flow — complete
    # ------------------------------------------------------------------

    def complete_oauth_flow(self, state: str, code: str) -> str:
        """Exchange the authorization code for tokens.

        Returns the server_name.
        """
        pending = self._get_pending(state)
        if pending is None:
            raise ValueError("Unknown or expired OAuth state")

        server_name = pending["server_name"]
        token_endpoint = pending["token_endpoint"]
        code_verifier = pending["code_verifier"]
        client_id = pending["client_id"]
        client_secret = pending.get("client_secret")
        redirect_uri = pending["redirect_uri"]

        # Exchange code for token
        token_data: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
        if client_secret:
            token_data["client_secret"] = client_secret

        with httpx.Client(timeout=30) as client:
            resp = client.post(
                token_endpoint,
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if resp.status_code != 200:
            self._clear_pending(state)
            raise ValueError(f"Token exchange failed ({resp.status_code}): {resp.text}")

        token_json = resp.json()
        token = OAuthToken(
            access_token=token_json["access_token"],
            token_type=token_json.get("token_type", "Bearer"),
            expires_in=token_json.get("expires_in"),
            scope=token_json.get("scope"),
            refresh_token=token_json.get("refresh_token"),
        )

        self.store_token(server_name, token)
        self._clear_pending(state)

        log.info("mcp_oauth_flow_completed", server=server_name)
        return server_name

    # ------------------------------------------------------------------
    # Metadata discovery
    # ------------------------------------------------------------------

    @staticmethod
    def _run_async(coro):
        """Run an async coroutine from sync code."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result(timeout=30)
        return asyncio.run(coro)

    def _discover_metadata(self, server_url: str) -> OAuthMetadata | None:
        """Discover OAuth Authorization Server Metadata (RFC 8414)."""
        urls = build_oauth_authorization_server_metadata_discovery_urls(
            auth_server_url=None,
            server_url=server_url,
        )
        with httpx.Client(timeout=15) as client:
            for url in urls:
                try:
                    request = create_oauth_metadata_request(url)
                    resp = client.send(request)
                    success, metadata = self._run_async(handle_auth_metadata_response(resp))
                    if success and metadata is not None:
                        return metadata
                except Exception:
                    continue
        return None

    # ------------------------------------------------------------------
    # Dynamic client registration
    # ------------------------------------------------------------------

    def _register_client(
        self,
        metadata: OAuthMetadata,
        server_url: str,
        callback_url: str,
    ) -> dict[str, Any]:
        """Register the client dynamically (RFC 7591) or use defaults."""
        # Check for cached client info
        # (We derive a stable filename from server_url)
        safe_name = server_url.replace("://", "_").replace("/", "_").rstrip("_")
        cache_path = self._dir / f"client_{safe_name}.json"
        if cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if cached.get("client_id"):
                    return cached
            except (json.JSONDecodeError, OSError):
                pass

        # Build client metadata
        from pydantic import AnyUrl

        client_metadata = OAuthClientMetadata(
            redirect_uris=[AnyUrl(callback_url)],
            client_name=_CLIENT_NAME,
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        )

        # Derive auth base URL for registration
        from urllib.parse import urlparse

        parsed = urlparse(server_url)
        auth_base = f"{parsed.scheme}://{parsed.netloc}"

        try:
            request = create_client_registration_request(metadata, client_metadata, auth_base)
            with httpx.Client(timeout=15) as client:
                resp = client.send(request)
            info = self._run_async(handle_registration_response(resp))
            result: dict[str, Any] = {"client_id": info.client_id}
            if info.client_secret:
                result["client_secret"] = info.client_secret
            # Cache the registration
            cache_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result
        except Exception as exc:
            log.warning("mcp_oauth_registration_failed", error=str(exc))
            # Fall back: use redirect_uri as client_id (URL-based Client ID)
            return {"client_id": callback_url}

    # ------------------------------------------------------------------
    # Pending state management
    # ------------------------------------------------------------------

    def _store_pending(self, state: str, data: dict[str, Any]) -> None:
        path = self._dir / f"pending_{state[:16]}.json"
        data["_state"] = state
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _get_pending(self, state: str) -> dict[str, Any] | None:
        path = self._dir / f"pending_{state[:16]}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("_state") != state:
                return None
            # Expire after 10 minutes
            if time.time() - data.get("created_at", 0) > 600:
                path.unlink(missing_ok=True)
                return None
            return data
        except (json.JSONDecodeError, OSError):
            return None

    def _clear_pending(self, state: str) -> None:
        path = self._dir / f"pending_{state[:16]}.json"
        path.unlink(missing_ok=True)
