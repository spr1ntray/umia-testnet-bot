from __future__ import annotations

from typing import Any

import requests

from .accounts import AccountConfig
from .config import AppConfig
from .safety import assert_allowed_chain

class UmiaApi:
    def __init__(self, cfg: AppConfig, account: AccountConfig) -> None:
        self.cfg = cfg
        self.account = account
        self.access_token: str | None = None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": cfg.network.app_origin,
                "Referer": f"{cfg.network.app_origin}/",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
                ),
            }
        )
        if account.proxy:
            self.session.proxies.update({"http": account.proxy, "https": account.proxy})
            self.session.verify = False

    def set_access_token(self, token: str | None) -> None:
        self.access_token = token
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        else:
            self.session.headers.pop("Authorization", None)

    @property
    def base(self) -> str:
        return self.cfg.network.api_base.rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def _get(self, path: str, **kwargs: Any) -> Any:
        r = self.session.get(
            self._url(path),
            timeout=self.cfg.network.request_timeout_seconds,
            **kwargs,
        )
        return self._parse(r)

    def _post(self, path: str, json_body: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        r = self.session.post(
            self._url(path),
            json=json_body,
            timeout=self.cfg.network.request_timeout_seconds,
            **kwargs,
        )
        return self._parse(r)

    @staticmethod
    def _parse(response: requests.Response) -> Any:
        try:
            payload = response.json()
        except Exception:
            raise RuntimeError(f"HTTP {response.status_code}: non-JSON body") from None
        if not response.ok:
            err = payload.get("error") if isinstance(payload, dict) else payload
            code = payload.get("code") if isinstance(payload, dict) else None
            raise RuntimeError(f"HTTP {response.status_code} {code or ''}: {err}")
        if isinstance(payload, dict) and payload.get("status") == "success" and "data" in payload:
            return payload["data"]
        return payload

    def protocol_config(self) -> dict[str, Any]:
        return self._get("/api/v1/config")

    def claim_faucet(self, address: str, chain_id: int | None = None) -> dict[str, Any]:
        cid = int(chain_id or self.cfg.network.chain_id)
        assert_allowed_chain(cid, context="faucet")
        return self._post(
            "/api/v1/faucet",
            {"address": address, "chainId": cid},
        )

    def signup(self) -> dict[str, Any]:
        if not self.access_token:
            raise RuntimeError("signup requires Privy access token")
        return self._post("/api/v1/users/signup", {})

    def me(self) -> dict[str, Any]:
        if not self.access_token:
            raise RuntimeError("me requires Privy access token")
        return self._get("/api/v1/users/me")

    def tokens(self, address: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"chainId": self.cfg.network.chain_id}
        if address:
            params["address"] = address
        data = self._get("/api/v1/hub/tokens", params=params)
        return list(data.get("tokens") or [])

    def swap_quote(
        self,
        *,
        taker: str,
        sell_token: str,
        buy_token: str,
        sell_amount: int | str,
        slippage_bps: int,
    ) -> dict[str, Any]:
        assert_allowed_chain(self.cfg.network.chain_id, context="swap_quote")
        params = {
            "chainId": self.cfg.network.chain_id,
            "taker": taker,
            "sellToken": sell_token,
            "buyToken": buy_token,
            "sellAmount": str(sell_amount),
            "slippageBps": int(slippage_bps),
        }
        return self._get("/api/v1/swap/quote", params=params)

    def swap_build(
        self,
        *,
        route: str,
        quote: dict[str, Any],
        signature: str,
        permit_data: Any = None,
    ) -> dict[str, Any]:
        assert_allowed_chain(self.cfg.network.chain_id, context="swap_build")
        body = {
            "route": route,
            "quote": quote,
            "permitData": permit_data,
            "signature": signature,
        }
        return self._post("/api/v1/swap/build", body)

    def live_fundraises(self) -> list[dict[str, Any]]:
        data = self._get("/api/v1/hub/fundraises", params={"status": "live"})
        return list(data.get("fundraises") or [])

    def auction_bid_calldata(
        self,
        *,
        slug: str,
        max_price_x96: int | str,
        amount_raw: int | str,
        taker: str,
    ) -> dict[str, Any]:
        body = {
            "slug": slug,
            "maxPrice": str(max_price_x96),
            "amount": str(amount_raw),
            "taker": taker,
        }
        data = self._post("/api/v1/calldata/auction-bid", body)
                                                                                
        if isinstance(data, dict) and data.get("chainId") is not None:
            assert_allowed_chain(int(data["chainId"]), context="auction-bid response")
        return data

    def portfolio(self, address: str) -> dict[str, Any]:
        return self._get(f"/api/v1/hub/portfolio/{address}")

    def participated_slugs(self, address: str) -> list[str]:
        data = self._get(f"/api/v1/hub/portfolio/{address}/participated")
        if isinstance(data, dict):
            return list(data.get("slugs") or [])
        return []

    def lp_positions(self, address: str) -> dict[str, Any]:
        return self._get(f"/api/v1/hub/portfolio/{address}/lp-positions")

    def fundraise_bids(
        self, slug: str, wallet: str, *, limit: int = 50
    ) -> dict[str, Any]:
        return self._get(
            f"/api/v1/hub/fundraises/{slug}/bids",
            params={"walletAddress": wallet, "limit": int(limit)},
        )
