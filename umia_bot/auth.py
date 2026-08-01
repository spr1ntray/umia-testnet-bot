from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from eth_account import Account
from eth_account.messages import encode_defunct

from .accounts import AccountConfig
from .config import AppConfig
from .safety import assert_allowed_chain, redact_text

                                                               
PRIVY_APP_ID = "cmo1j6mrc00bw0cjogi68sggx"
                                                                     
TURNSTILE_SITE_KEY = "0x4AAAAAAAM8ceq5KhP1uJBt"
PRIVY_BASE = "https://auth.privy.io"

@dataclass(frozen=True)
class PrivySession:
    access_token: str
    identity_token: str | None
    refresh_token: str | None
    user_id: str | None
    is_new_user: bool

class CapsolverError(RuntimeError):
    pass

class PrivyAuthError(RuntimeError):
    pass

def solve_turnstile(
    *,
    api_key: str,
    website_url: str,
    site_key: str = TURNSTILE_SITE_KEY,
    proxy: str | None = None,
    timeout_seconds: int = 180,
) -> str:
    if not api_key:
        raise CapsolverError("Capsolver API key is empty — put it in input/capsolver_api_key.txt")

    create_url = "https://api.capsolver.com/createTask"
    result_url = "https://api.capsolver.com/getTaskResult"

    if proxy:
        task: dict[str, Any] = {
            "type": "AntiTurnstileTask",
            "websiteURL": website_url,
            "websiteKey": site_key,
            "proxy": _proxy_for_capsolver(proxy),
        }
    else:
        task = {
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": website_url,
            "websiteKey": site_key,
        }

    create = requests.post(
        create_url,
        json={"clientKey": api_key, "task": task},
        timeout=60,
    )
    create_payload = create.json()
    if create_payload.get("errorId"):
        raise CapsolverError(
            f"createTask: {create_payload.get('errorDescription') or create_payload}"
        )
    task_id = create_payload.get("taskId")
    if not task_id:
        raise CapsolverError(f"createTask missing taskId: {create_payload}")

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        time.sleep(3)
        res = requests.post(
            result_url,
            json={"clientKey": api_key, "taskId": task_id},
            timeout=60,
        )
        payload = res.json()
        if payload.get("errorId"):
            raise CapsolverError(
                f"getTaskResult: {payload.get('errorDescription') or payload}"
            )
        status = payload.get("status")
        if status == "ready":
            token = (payload.get("solution") or {}).get("token")
            if not token:
                raise CapsolverError(f"no token in solution: {payload}")
            return str(token)
        if status == "failed":
            raise CapsolverError(f"task failed: {payload}")
    raise CapsolverError("Turnstile solve timeout")

def privy_siwe_login(
    cfg: AppConfig,
    account: AccountConfig,
    *,
    capsolver_api_key: str,
    chain_id: int | None = None,
) -> PrivySession:
    cid = int(chain_id or cfg.network.chain_id)
    assert_allowed_chain(cid, context="privy_login")

    acct = Account.from_key(account.private_key)
    address = acct.address
    origin = cfg.network.app_origin.rstrip("/")
    host = urlparse(origin).netloc or "app.testnet.umia.finance"

    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": origin,
            "Referer": f"{origin}/",
            "privy-app-id": PRIVY_APP_ID,
            "privy-client": "react:2.13.0",
            "privy-ca-id": str(uuid.uuid4()),
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
            ),
        }
    )
    if account.proxy:
        session.proxies.update({"http": account.proxy, "https": account.proxy})
        session.verify = False

    captcha_token = solve_turnstile(
        api_key=capsolver_api_key,
        website_url=origin + "/",
        site_key=TURNSTILE_SITE_KEY,
                                                              
        proxy=None,
    )

    init = session.post(
        f"{PRIVY_BASE}/api/v1/siwe/init",
        json={"address": address, "token": captcha_token},
        timeout=cfg.network.request_timeout_seconds,
    )
    init_data = _json_or_error(init, "siwe/init")
    nonce = init_data.get("nonce")
    if not nonce:
        raise PrivyAuthError(f"siwe/init missing nonce: {init_data}")

    issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    message = (
        f"{host} wants you to sign in with your Ethereum account:\n"
        f"{address}\n"
        f"\n"
        f"By signing, you are proving you own this wallet and logging in. "
        f"This does not initiate a transaction or cost any fees.\n"
        f"\n"
        f"URI: {origin}\n"
        f"Version: 1\n"
        f"Chain ID: {cid}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued_at}\n"
        f"Resources:\n"
        f"- https://privy.io"
    )

    signed = acct.sign_message(encode_defunct(text=message))
    signature = signed.signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature

    auth = session.post(
        f"{PRIVY_BASE}/api/v1/siwe/authenticate",
        json={
            "message": message,
            "signature": signature,
            "chainId": f"eip155:{cid}",
            "walletClientType": "metamask",
            "connectorType": "injected",
            "mode": "login-or-sign-up",
        },
        timeout=cfg.network.request_timeout_seconds,
    )
    auth_data = _json_or_error(auth, "siwe/authenticate")

    access = (
        auth_data.get("token")
        or auth_data.get("privy_access_token")
        or (auth_data.get("data") or {}).get("token")
    )
    if not access:
        raise PrivyAuthError(
            f"authenticate missing access token keys={list(auth_data.keys())}"
        )

    user = auth_data.get("user") or {}
    return PrivySession(
        access_token=str(access),
        identity_token=auth_data.get("identity_token"),
        refresh_token=auth_data.get("refresh_token"),
        user_id=str(user.get("id")) if user.get("id") else None,
        is_new_user=bool(auth_data.get("is_new_user")),
    )

def _json_or_error(response: requests.Response, label: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        raise PrivyAuthError(f"{label} HTTP {response.status_code}: non-JSON") from None
    if not response.ok:
        err = payload.get("error") if isinstance(payload, dict) else payload
        code = payload.get("code") if isinstance(payload, dict) else None
        raise PrivyAuthError(f"{label} HTTP {response.status_code} {code or ''}: {err}")
    return payload if isinstance(payload, dict) else {"data": payload}

def _proxy_for_capsolver(proxy_url: str) -> str:
    p = proxy_url
    if "://" in p:
        p = p.split("://", 1)[1]
    if "@" in p:
        creds, hostport = p.split("@", 1)
        if ":" in creds:
            user, password = creds.split(":", 1)
            host, port = hostport.split(":", 1) if ":" in hostport else (hostport, "80")
            return f"{host}:{port}:{user}:{password}"
    return p

def safe_auth_error(exc: Exception, secrets: list[str | None]) -> str:
    return redact_text(str(exc), secrets)[:400]
