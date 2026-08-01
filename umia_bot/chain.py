from __future__ import annotations

import threading
import time
from typing import Any

import urllib3
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

from .accounts import AccountConfig
from .config import AppConfig
from .safety import assert_allowed_chain

ERC20_ABI = [
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "allowance",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "decimals",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
    },
    {
        "name": "symbol",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "string"}],
    },
]

MAX_UINT256 = 2**256 - 1

_NONCE_LOCKS: dict[str, threading.Lock] = {}
_NONCE_LOCKS_GUARD = threading.Lock()

def _nonce_lock(address: str) -> threading.Lock:
    key = address.lower()
    with _NONCE_LOCKS_GUARD:
        if key not in _NONCE_LOCKS:
            _NONCE_LOCKS[key] = threading.Lock()
        return _NONCE_LOCKS[key]

class ChainClient:
    def __init__(self, cfg: AppConfig, account: AccountConfig) -> None:
        self.cfg = cfg
        self.account_cfg = account
        self.account = Account.from_key(account.private_key)
        self.address = Web3.to_checksum_address(self.account.address)

        request_kwargs: dict[str, Any] = {
            "timeout": cfg.network.request_timeout_seconds,
        }
        if account.proxy:
            request_kwargs["proxies"] = {"http": account.proxy, "https": account.proxy}
            try:
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass

        self.w3 = Web3(Web3.HTTPProvider(cfg.network.rpc_url, request_kwargs=request_kwargs))

    def assert_chain(self) -> None:
        expected = int(self.cfg.network.chain_id)
        assert_allowed_chain(expected, context="config")
        onchain = int(self.w3.eth.chain_id)
        assert_allowed_chain(onchain, context="rpc")
        if onchain != expected:
            raise RuntimeError(f"RPC chain_id {onchain} != config {expected}")

    def native_balance(self) -> int:
        return int(self.w3.eth.get_balance(self.address))

    def erc20_balance(self, token: str) -> int:
        c = self.w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)
        return int(c.functions.balanceOf(self.address).call())

    def erc20_allowance(self, token: str, spender: str) -> int:
        c = self.w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)
        return int(
            c.functions.allowance(
                self.address, Web3.to_checksum_address(spender)
            ).call()
        )

    def erc20_symbol(self, token: str) -> str:
        try:
            c = self.w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)
            return str(c.functions.symbol().call())
        except Exception:
            return token[:10]

    def ensure_erc20_allowance(self, token: str, spender: str, amount: int) -> str | None:
        current = self.erc20_allowance(token, spender)
        if current >= amount:
            return None
        c = self.w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)
        data = c.encode_abi("approve", args=[Web3.to_checksum_address(spender), MAX_UINT256])
        return self.send_raw(
            to=Web3.to_checksum_address(token),
            data=data,
            value=0,
            chain_id=self.cfg.network.chain_id,
        )

    def sign_typed_data(self, typed: dict[str, Any]) -> str:
        domain = dict(typed.get("domain") or {})
        if "chainId" in domain:
            domain["chainId"] = int(domain["chainId"])
        types = dict(typed.get("types") or {})
        types.pop("EIP712Domain", None)
        message = _coerce_permit_message(dict(typed.get("message") or {}))

        signable = encode_typed_data(
            domain_data=domain,
            message_types=types,
            message_data=message,
        )
        signed = self.account.sign_message(signable)
        sig = signed.signature.hex()
        return sig if sig.startswith("0x") else "0x" + sig

    def send_raw(
        self,
        *,
        to: str,
        data: str,
        value: int = 0,
        chain_id: int | None = None,
    ) -> str:
        cid = int(chain_id if chain_id is not None else self.cfg.network.chain_id)
        assert_allowed_chain(cid, context="send_raw")
        self.assert_chain()

        to_addr = Web3.to_checksum_address(to)
        data_hex = data if data.startswith("0x") else "0x" + data
        value_int = int(value)

        with _nonce_lock(self.address):
            nonce = int(self.w3.eth.get_transaction_count(self.address, "pending"))
            tx: dict[str, Any] = {
                "from": self.address,
                "to": to_addr,
                "data": data_hex,
                "value": value_int,
                "nonce": nonce,
                "chainId": cid,
            }
            try:
                gas = int(self.w3.eth.estimate_gas(tx))
            except Exception:
                gas = 500_000
            gas = int(gas * float(self.cfg.run.gas_multiplier))
            tx["gas"] = gas

                                   
            try:
                latest = self.w3.eth.get_block("latest")
                base = int(latest.get("baseFeePerGas") or 0)
                if base > 0:
                    tip = int(self.w3.eth.max_priority_fee)
                    tx["maxPriorityFeePerGas"] = tip
                    tx["maxFeePerGas"] = base * 2 + tip
                    tx.pop("gasPrice", None)
                else:
                    tx["gasPrice"] = int(self.w3.eth.gas_price)
            except Exception:
                tx["gasPrice"] = int(self.w3.eth.gas_price)

            signed = self.account.sign_transaction(tx)
            raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
            tx_hash = self.w3.eth.send_raw_transaction(raw)
            return tx_hash.hex() if hasattr(tx_hash, "hex") else Web3.to_hex(tx_hash)

    def wait_receipt(self, tx_hash: str) -> dict[str, Any]:
        deadline = time.time() + float(self.cfg.run.receipt_timeout_seconds)
        h = tx_hash if tx_hash.startswith("0x") else "0x" + tx_hash
        while time.time() < deadline:
            try:
                receipt = self.w3.eth.get_transaction_receipt(h)
                if receipt is not None:
                    return dict(receipt)
            except Exception:
                pass
            time.sleep(float(self.cfg.run.receipt_poll_interval_seconds))
        raise TimeoutError(f"receipt timeout for {h}")

    def send_call(self, call: dict[str, Any]) -> str:
        to = call["to"]
        data = call.get("data") or "0x"
        value_raw = call.get("value") or 0
        if isinstance(value_raw, str):
            value = int(value_raw, 16) if value_raw.startswith("0x") else int(value_raw)
        else:
            value = int(value_raw)
        chain_id = call.get("chainId") or self.cfg.network.chain_id
        return self.send_raw(to=to, data=data, value=value, chain_id=int(chain_id))

    def send_calls(self, calls: list[dict[str, Any]], *, chain_id: int | None = None) -> list[str]:
        cid = int(chain_id if chain_id is not None else self.cfg.network.chain_id)
        assert_allowed_chain(cid, context="send_calls")
        hashes: list[str] = []
        for call in calls:
            c = dict(call)
            c["chainId"] = cid
            txh = self.send_call(c)
            hashes.append(txh)
            receipt = self.wait_receipt(txh)
            status = int(receipt.get("status") or 0)
            if status != 1:
                raise RuntimeError(f"tx failed status={status} hash={txh}")
        return hashes

def _coerce_permit_message(message: dict[str, Any]) -> dict[str, Any]:
    out = dict(message)
    if "sigDeadline" in out:
        out["sigDeadline"] = int(out["sigDeadline"])
    details = out.get("details")
    if isinstance(details, dict):
        d = dict(details)
        if "amount" in d:
            d["amount"] = int(d["amount"])
        if "expiration" in d:
            d["expiration"] = int(d["expiration"])
        if "nonce" in d:
            d["nonce"] = int(d["nonce"])
        out["details"] = d
    return out
