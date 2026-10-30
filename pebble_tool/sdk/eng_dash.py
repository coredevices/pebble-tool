"""
eng-dash (dash.repebble.com) SDK release client.

eng-dash hosts SDK releases on R2 the same way firmware .pbz artifacts
live there, and resolves which SDK version a given user should install
from their watch tester channel (internal/alpha-testers/beta-testers/
default -> OTA track internal/alpha/beta/production). pebble-tool queries
these endpoints so `pebble sdk install latest` matches the user's channel
without a separate CLI flag.

Env:
  PEBBLE_ENG_DASH_BASE   base URL (default https://dash.repebble.com)
  PEBBLE_SDK_SERVER      legacy sdk.repebble.com override — when set, the
                         caller is on the legacy path and should not go
                         through this module at all.
"""

__author__ = "eric"

import logging
import os

import requests

from pebble_tool.exceptions import SDKInstallError

logger = logging.getLogger("pebble_tool.sdk.eng_dash")

DEFAULT_ENG_DASH_BASE = "https://dash.repebble.com"


def eng_dash_base():
    return os.environ.get("PEBBLE_ENG_DASH_BASE", DEFAULT_ENG_DASH_BASE).rstrip("/")


def _get(path, token=None, timeout=30):
    """GET a JSON path on eng-dash. Firebase id_token is attached as a
    bearer when provided; anonymous callers get the production SDK."""
    headers = {}
    if token:
        headers["Authorization"] = "Bearer {}".format(token)
    try:
        response = requests.get("{}{}".format(eng_dash_base(), path), headers=headers, timeout=timeout)
    except requests.RequestException as e:
        raise SDKInstallError("eng-dash request to {} failed: {}".format(path, e))
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        detail = response.text[:200]
        raise SDKInstallError(
            "eng-dash {} returned HTTP {}: {}".format(path, response.status_code, detail)
        )
    try:
        return response.json()
    except ValueError:
        raise SDKInstallError("eng-dash {} returned non-JSON body".format(path))


def latest(token=None):
    """GET /api/sdk/latest. None when no SDK is deployed anywhere the
    caller's track can see — pebble-tool falls back to the legacy server."""
    return _get("/api/sdk/latest", token=token)


def list_releases(token=None):
    return _get("/api/sdk/list", token=token)


def release(version, token=None):
    return _get("/api/sdk/releases/{}".format(version), token=token)


def firebase_token_if_logged_in():
    """Return the signed-in user's Firebase id_token, or None when the
    user hasn't run `pebble login`. Never raises — a login failure here
    should drop the caller to the anonymous (production) SDK, not break
    `pebble sdk install`."""
    try:
        from pebble_tool.account import get_account
    except Exception:
        return None
    try:
        account = get_account(auth_provider="firebase")
    except Exception:
        return None
    if not getattr(account, "is_logged_in", False):
        return None
    try:
        return account.get_access_token()
    except Exception as e:
        logger.debug("firebase get_access_token failed: %s", e)
        return None


def toolchain_kind_for_host():
    """Map (platform.system(), platform.machine()) to the sdk_artifacts
    kind pebble-tool should pull. Returns (kind, platform_label) where
    platform_label matches what SDKManager._install_toolchain_from_handle
    expects for its tar directory, so the caller can reuse the existing
    extract path without a translation layer. None when the current host
    isn't one eng-dash ships a toolchain for."""
    import platform
    system = platform.system()
    machine = platform.machine()
    if system == "Darwin":
        if machine == "arm64":
            return "mac-arm64", "mac-arm64"
        if machine == "x86_64":
            return "mac-x86_64", "mac-x86_64"
    elif system == "Linux":
        if machine == "x86_64":
            return "linux-x86_64", "linux-x86_64"
        if machine in ("aarch64", "arm64"):
            return "linux-aarch64", "linux-aarch64"
    return None, None


def pick_toolchain(toolchains):
    """Pick the toolchain entry from an /api/sdk/latest response that
    matches this host. Prefers the arch-specific entry, then any entry
    whose key starts with the host's platform (so the server can serve a
    legacy platform-only toolchain under e.g. 'mac' if it wants). None
    when none of the published toolchains fit."""
    if not toolchains:
        return None, None
    kind, platform_label = toolchain_kind_for_host()
    if kind and kind in toolchains:
        return toolchains[kind], platform_label
    # Fallback: the first toolchain whose key matches the host OS prefix.
    import platform
    prefix = "mac" if platform.system() == "Darwin" else "linux"
    for key, value in toolchains.items():
        if key.startswith(prefix + "-") or key == prefix:
            return value, key
    return None, None
