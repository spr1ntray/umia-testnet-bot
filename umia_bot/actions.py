from __future__ import annotations

import random
import time
from typing import Any, Callable

from web3 import Web3

from .api import UmiaApi
from .auth import privy_siwe_login, safe_auth_error
from .chain import ChainClient
from .config import AppConfig
from .utils import (
    ensure_price_above_clearing,
    from_units,
    human_price_to_x96,
    random_delay,
    random_units,
    short_hash,
    to_units,
)

LogFn = Callable[[dict[str, Any]], None]

class WalletActionRunner:
    def __init__(
        self,
        cfg: AppConfig,
        chain: ChainClient,
        api: UmiaApi,
        log: LogFn,
        *,
        do_faucet: bool = True,
        do_activities: bool = True,
        capsolver_api_key: str = "",
    ) -> None:
        self.cfg = cfg
        self.chain = chain
        self.api = api
        self.log = log
        self.do_faucet = do_faucet
        self.do_activities = do_activities
        self.capsolver_api_key = capsolver_api_key
        self.address = chain.address
        self.usdc = cfg.network.usdc_address
        self.usdc_decimals = cfg.network.usdc_decimals
        self.permit2 = cfg.network.permit2
        self._tx_ok = 0
        self._authed = False

    def run(self) -> None:
        self._event("start", status="running")
        try:
            self.chain.assert_chain()
        except Exception as exc:
            self._event("chain_check", status="failed", details={"error": str(exc)})
            return

        self._refresh_token_meta()

                                                                                    
        need_auth = bool(
            self.do_faucet and self.cfg.faucet.enabled and self.cfg.faucet.auto_register
        )
        if need_auth:
            self._try_register_and_login()

        faucet_ok = False
        if self.do_faucet and self.cfg.faucet.enabled:
            if self._authed or not self.cfg.faucet.auto_register:
                faucet_ok = self._try_faucet()
            else:
                reason = (
                    "auth failed (see auth fail above) — faucet needs Privy session"
                    if self.capsolver_api_key
                    else "not authenticated — Capsolver key missing from encrypted DB"
                )
                self._event(
                    "faucet",
                    status="skipped",
                    details={"reason": reason},
                )

                                                                    
        if faucet_ok:
            eth, usdc = self._wait_for_balances(
                want_usdc=True,
                max_wait_seconds=45.0,
                poll_interval=2.0,
            )
        else:
            eth, usdc = self._read_balances()

        min_eth = to_units(self.cfg.run.min_eth_for_gas, 18)
        self._event(
            "balances",
            status="ok",
            details={
                "eth": from_units(eth, 18),
                "usdc": from_units(usdc, self.usdc_decimals),
                "usdc_raw": str(usdc),
                "usdc_token": self.usdc,
                "usdc_decimals": self.usdc_decimals,
            },
        )

        if not self.do_activities:
            self._event("finish", status="ok", details={"mode": "faucet_or_parse"})
            return

        if eth < min_eth or usdc <= 0:
            self._event(
                "skip",
                status="no_testnet_funds",
                details={
                    "eth": from_units(eth, 18),
                    "usdc": from_units(usdc, self.usdc_decimals),
                    "usdc_raw": str(usdc),
                    "usdc_token": self.usdc,
                    "min_eth": self.cfg.run.min_eth_for_gas,
                    "reason": "need ETH for gas and mUSDC for activity",
                },
            )
            return

        tx_min = max(1, int(self.cfg.run.tx_min))
        tx_max = max(tx_min, int(self.cfg.run.tx_max))
        target = random.randint(tx_min, tx_max)
        self._event("plan", status="ok", details={"target_txs": target})

        action_types: list[str] = []
        if self.cfg.swap.enabled:
            action_types.append("swap")
        if self.cfg.bid.enabled:
            action_types.append("bid")
        if not action_types:
            self._event("finish", status="no_actions_enabled")
            return

        attempts = 0
        max_attempts = target * 4 + 4
        while self._tx_ok < target and attempts < max_attempts:
            attempts += 1
            kind = random.choice(action_types)
            try:
                if kind == "swap":
                    self._do_swap()
                else:
                    self._do_bid()
            except Exception as exc:
                self._event(
                    kind,
                    status="failed",
                    details={"error": str(exc)[:400]},
                )
            if self._tx_ok < target:
                random_delay(
                    self.cfg.run.delay_min_seconds,
                    self.cfg.run.delay_max_seconds,
                )

        self._event(
            "finish",
            status="ok",
            details={"txs_ok": self._tx_ok, "target": target},
        )

                            

    def _try_register_and_login(self) -> None:
        secrets = [
            self.chain.account_cfg.private_key,
            self.chain.account_cfg.proxy,
            self.capsolver_api_key,
        ]
        if not self.capsolver_api_key:
            self._event(
                "auth",
                status="failed",
                details={
                    "error": "no Capsolver API key in encrypted DB — "
                    "put key in input/capsolver_api_key.txt and run "
                    "'Создать/обновить зашифрованную базу' (or unlock once to import+wipe)"
                },
            )
            return
        try:
            session = privy_siwe_login(
                self.cfg,
                self.chain.account_cfg,
                capsolver_api_key=self.capsolver_api_key,
            )
            self.api.set_access_token(session.access_token)
            self._authed = True
            self._event(
                "auth",
                status="ok",
                details={
                    "user_id": session.user_id,
                    "is_new_user": session.is_new_user,
                },
            )
        except Exception as exc:
            self._event(
                "auth",
                status="failed",
                details={"error": safe_auth_error(exc, secrets)},
            )
            return

                                                       
        try:
            profile = self.api.signup()
            self._event(
                "signup",
                status="ok",
                details={"profile": _safe_json(profile)},
            )
        except Exception as exc:
                                                                
            self._event(
                "signup",
                status="warn",
                details={"error": safe_auth_error(exc, secrets)},
            )

    def _try_faucet(self) -> bool:
        for attempt in range(1, int(self.cfg.faucet.retries) + 1):
            try:
                data = self.api.claim_faucet(self.address, self.cfg.network.chain_id)
                self._event(
                    "faucet",
                    status="ok",
                    details={"attempt": attempt, "response": _safe_json(data)},
                )
                                                          
                self._refresh_token_meta()
                return True
            except Exception as exc:
                self._event(
                    "faucet",
                    status="failed",
                    details={"attempt": attempt, "error": str(exc)[:300]},
                )
                                          
                if "401" in str(exc) and self.capsolver_api_key and attempt == 1:
                    self._try_register_and_login()
        return False

    def _read_balances(self) -> tuple[int, int]:
        eth = self.chain.native_balance()
        usdc = 0
                                                                           
        try:
            usdc = self.chain.erc20_balance(self.usdc)
        except Exception as exc:
            self._event(
                "balances",
                status="warn",
                details={"error": f"erc20_balance failed: {exc}"[:200], "token": self.usdc},
            )
        if usdc <= 0:
            api_bal = self._usdc_balance_from_api()
            if api_bal > usdc:
                usdc = api_bal
        return eth, usdc

    def _usdc_balance_from_api(self) -> int:
        try:
            tokens = self.api.tokens(self.address)
            for t in tokens:
                if t.get("kind") == "usdc" or str(t.get("symbol", "")).upper() in {
                    "MUSDC",
                    "USDC",
                }:
                    if t.get("address"):
                        self.usdc = Web3.to_checksum_address(t["address"])
                    if t.get("decimals") is not None:
                        self.usdc_decimals = int(t["decimals"])
                    bal = t.get("balance")
                    if bal is not None and str(bal).strip() != "":
                        return int(bal)
        except Exception:
            pass
        return 0

    def _wait_for_balances(
        self,
        *,
        want_usdc: bool,
        max_wait_seconds: float,
        poll_interval: float,
    ) -> tuple[int, int]:
        deadline = time.time() + max_wait_seconds
        eth, usdc = self._read_balances()
        min_eth = to_units(self.cfg.run.min_eth_for_gas, 18)
        while time.time() < deadline:
            if eth >= min_eth and (not want_usdc or usdc > 0):
                return eth, usdc
            time.sleep(poll_interval)
            eth, usdc = self._read_balances()
            self._event(
                "balances",
                status="waiting",
                details={
                    "eth": from_units(eth, 18),
                    "usdc": from_units(usdc, self.usdc_decimals),
                    "usdc_raw": str(usdc),
                    "usdc_token": self.usdc,
                },
            )
        return eth, usdc

    def _refresh_token_meta(self) -> None:
        try:
                                                                          
            conf = self.api.protocol_config()
            chains = (conf.get("chains") or conf) if isinstance(conf, dict) else {}
            if isinstance(chains, dict):
                base = chains.get("base-sepolia") or {}
                if base.get("usdc"):
                    self.usdc = Web3.to_checksum_address(base["usdc"])
                if base.get("usdcDecimals") is not None:
                    self.usdc_decimals = int(base["usdcDecimals"])
                contracts = (base.get("contracts") or {}).get("uniswap") or {}
                if contracts.get("permit2"):
                    self.permit2 = Web3.to_checksum_address(contracts["permit2"])

            tokens = self.api.tokens(self.address)
                                                                           
            chosen = None
            for t in tokens:
                if t.get("kind") == "usdc":
                    chosen = t
                    break
            if chosen is None:
                for t in tokens:
                    sym = str(t.get("symbol", "")).upper()
                    if sym in {"MUSDC", "USDC"} or t.get("isMock"):
                        chosen = t
                        break
            if chosen:
                if chosen.get("address"):
                    self.usdc = Web3.to_checksum_address(chosen["address"])
                if chosen.get("decimals") is not None:
                    self.usdc_decimals = int(chosen["decimals"])
        except Exception as exc:
            self._event("meta", status="warn", details={"error": str(exc)[:200]})
                                         
        try:
            self.usdc = Web3.to_checksum_address(self.usdc)
            self.permit2 = Web3.to_checksum_address(self.permit2)
        except Exception:
            pass

                  

    def _do_swap(self) -> None:
        tokens = self.api.tokens(self.address)
        usdc_meta = None
        ventures: list[dict[str, Any]] = []
        for t in tokens:
            if t.get("kind") == "usdc" or t.get("isMock"):
                usdc_meta = t
            elif t.get("kind") == "venture" or (t.get("pairsWith") and not t.get("isMock")):
                ventures.append(t)

        if usdc_meta and usdc_meta.get("address"):
            self.usdc = usdc_meta["address"]
            pairs = list(usdc_meta.get("pairsWith") or [])
        else:
            pairs = []

        if not pairs and ventures:
            pairs = [v["address"] for v in ventures if v.get("address")]

        if not pairs:
            raise RuntimeError("no swap pairs available")

        buy_token = random.choice(pairs)
        balance = self.chain.erc20_balance(self.usdc)
        if balance <= 0:
            raise RuntimeError("mUSDC balance is 0")

        want = random_units(self.cfg.swap.usdc_min, self.cfg.swap.usdc_max, self.usdc_decimals)
                                 
        max_spend = max(1, int(balance * 0.4))
        sell_amount = min(want, max_spend, balance)
        if sell_amount <= 0:
            raise RuntimeError("sell amount is 0")

                                    
        approve_hash = self.chain.ensure_erc20_allowance(
            self.usdc, self.permit2, sell_amount
        )
        if approve_hash:
            receipt = self.chain.wait_receipt(approve_hash)
            if int(receipt.get("status") or 0) != 1:
                raise RuntimeError(f"approve failed {approve_hash}")
            self._count_tx(
                "approve",
                approve_hash,
                details={"spender": "Permit2", "token": "mUSDC"},
            )

        quote = self.api.swap_quote(
            taker=self.address,
            sell_token=self.usdc,
            buy_token=buy_token,
            sell_amount=sell_amount,
            slippage_bps=self.cfg.swap.slippage_bps,
        )
        if not quote.get("liquidityAvailable", True):
            raise RuntimeError("no liquidity for swap")

        permit = quote.get("permit")
        if not permit:
            raise RuntimeError("quote missing permit typed data")

        signature = self.chain.sign_typed_data(permit)
        upstream = quote.get("upstreamQuote") or {}
        built = self.api.swap_build(
            route=quote.get("route") or "spotPool",
            quote=upstream,
            signature=signature,
            permit_data=quote.get("permitData"),
        )
        to = built.get("to")
        data = built.get("data")
        if not to or not data:
            raise RuntimeError(f"swap build incomplete: {list(built.keys())}")

        value = built.get("value") or 0
        tx_hash = self.chain.send_raw(
            to=to,
            data=data,
            value=int(value) if not isinstance(value, str) else int(value, 16),
            chain_id=self.cfg.network.chain_id,
        )
        receipt = self.chain.wait_receipt(tx_hash)
        if int(receipt.get("status") or 0) != 1:
            raise RuntimeError(f"swap tx failed {tx_hash}")

        buy_sym = self.chain.erc20_symbol(buy_token)
        self._count_tx(
            "swap",
            tx_hash,
            details={
                "sell": from_units(sell_amount, self.usdc_decimals),
                "sell_token": "mUSDC",
                "buy_token": buy_sym,
                "buy_amount": quote.get("buyAmount"),
            },
        )

                         

    def _do_bid(self) -> None:
        fundraises = self.api.live_fundraises()
        random.shuffle(fundraises)
        last_err: Exception | None = None

        for fr in fundraises:
            slug = fr.get("slug")
            auction = fr.get("auction") or {}
            if not slug or not auction:
                continue
            if auction.get("settled") or not auction.get("activated", True):
                continue
                            
            if int(fr.get("chainId") or auction.get("chainId") or 0) != self.cfg.network.chain_id:
                continue

            try:
                self._bid_one(slug, auction, fr)
                return
            except Exception as exc:
                last_err = exc
                msg = str(exc).lower()
                if "requiresverification" in msg or "verification" in msg:
                    self._event(
                        "bid",
                        status="skip_gated",
                        details={"slug": slug, "error": str(exc)[:200]},
                    )
                    continue
                self._event(
                    "bid",
                    status="retry",
                    details={"slug": slug, "error": str(exc)[:200]},
                )
                continue

        raise RuntimeError(
            f"no bid succeeded across {len(fundraises)} live auctions"
            + (f": {last_err}" if last_err else "")
        )

    def _bid_one(self, slug: str, auction: dict[str, Any], fr: dict[str, Any]) -> None:
        currency_decimals = int(auction.get("currencyDecimals") or self.usdc_decimals)
        token_decimals = int(auction.get("tokenDecimals") or 18)
        tick = int(auction.get("tickSpacingX96") or 1)
        floor_x96 = int(auction.get("floorPriceX96") or 0)
        clearing_x96 = int(auction.get("clearingPriceX96") or floor_x96)
        clearing_human = float(auction.get("clearingPrice") or 0) or None

        if clearing_human and clearing_human > 0:
            target_human = clearing_human * float(self.cfg.bid.price_multiplier)
        else:
                                             
            target_human = float(fr.get("initialPrice") or 0.05) * float(
                self.cfg.bid.price_multiplier
            )

        max_price_x96 = human_price_to_x96(
            target_human,
            currency_decimals=currency_decimals,
            token_decimals=token_decimals,
            tick_spacing_x96=tick,
        )
        max_price_x96 = ensure_price_above_clearing(max_price_x96, clearing_x96, tick)

        balance = self.chain.erc20_balance(self.usdc)
        want = random_units(self.cfg.bid.usdc_min, self.cfg.bid.usdc_max, currency_decimals)
        max_spend = max(1, int(balance * 0.35))
        amount = min(want, max_spend, balance)
        if amount <= 0:
            raise RuntimeError("bid amount is 0")

        built = self.api.auction_bid_calldata(
            slug=slug,
            max_price_x96=max_price_x96,
            amount_raw=amount,
            taker=self.address,
        )
        if built.get("requiresVerification"):
            raise RuntimeError(
                f"requiresVerification: {built.get('reason')} {built.get('hubUrl')}"
            )
        calls = built.get("calls") or []
        if not calls:
            raise RuntimeError(f"empty bid calls: {built}")

        chain_id = int(built.get("chainId") or self.cfg.network.chain_id)
        hashes = self.chain.send_calls(calls, chain_id=chain_id)
        for i, h in enumerate(hashes):
            self._count_tx(
                "bid" if i == len(hashes) - 1 else "bid_prep",
                h,
                details={
                    "slug": slug,
                    "amount_usdc": from_units(amount, currency_decimals),
                    "max_price_human": target_human,
                    "step": i + 1,
                    "of": len(hashes),
                },
            )

                     

    def _count_tx(self, action: str, tx_hash: str, details: dict[str, Any] | None = None) -> None:
        self._tx_ok += 1
        self._event(
            action,
            status="ok",
            tx_hash=tx_hash,
            details={**(details or {}), "txs_ok": self._tx_ok},
        )

    def _event(
        self,
        action: str,
        *,
        status: str,
        tx_hash: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.log(
            {
                "label": self.chain.account_cfg.label,
                "address": self.address,
                "action": action,
                "status": status,
                "tx_hash": tx_hash,
                "details": details or {},
            }
        )

def _safe_json(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k.lower() not in {"signature", "privatekey"}}
    return data
