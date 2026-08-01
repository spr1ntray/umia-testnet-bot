from __future__ import annotations

from dataclasses import dataclass

from .safety import ALLOWED_CHAIN_IDS

@dataclass(frozen=True)
class NetworkConfig:
    api_base: str = "https://api.testnet.umia.finance"
    app_origin: str = "https://app.testnet.umia.finance"
    chain_id: int = 84532
    rpc_url: str = "https://api.testnet.umia.finance/api/v1/rpc/84532"
    request_timeout_seconds: int = 45
                                            
    usdc_address: str = "0x49b7A040aFCBFfC4cb02F857feE7b55C9C41658a"
    usdc_decimals: int = 6
    permit2: str = "0x000000000022D473030F116dDEE9F6B43aC78BA3"

@dataclass(frozen=True)
class RunConfig:
    max_workers: int = 5
    tx_min: int = 5
    tx_max: int = 9
    shuffle_wallets: bool = True
    shuffle_actions: bool = True
    delay_min_seconds: float = 5.0
    delay_max_seconds: float = 18.0
    min_eth_for_gas: str = "0.00005"
    gas_multiplier: float = 1.25
    receipt_timeout_seconds: int = 180
    receipt_poll_interval_seconds: float = 2.0

@dataclass(frozen=True)
class FaucetConfig:
    enabled: bool = True
    retries: int = 2
    auto_register: bool = True                                                  

@dataclass(frozen=True)
class SwapConfig:
    enabled: bool = True
    usdc_min: str = "1"
    usdc_max: str = "50"
    slippage_bps: int = 50

@dataclass(frozen=True)
class BidConfig:
    enabled: bool = True
    usdc_min: str = "1"
    usdc_max: str = "25"
    price_multiplier: float = 1.5

@dataclass(frozen=True)
class AppConfig:
    network: NetworkConfig
    run: RunConfig
    faucet: FaucetConfig
    swap: SwapConfig
    bid: BidConfig

def load_config() -> AppConfig:
    import parameters as p

    chain_id = 84532
    if chain_id not in ALLOWED_CHAIN_IDS:
        raise RuntimeError("parameters chain must be Base Sepolia")

    return AppConfig(
        network=NetworkConfig(
            request_timeout_seconds=int(getattr(p, "REQUEST_TIMEOUT", 45)),
        ),
        run=RunConfig(
            max_workers=int(p.MAX_WORKERS),
            tx_min=int(p.TX_MIN),
            tx_max=int(p.TX_MAX),
            shuffle_wallets=bool(p.SHUFFLE_WALLETS),
            shuffle_actions=bool(getattr(p, "SHUFFLE_ACTIONS", True)),
            delay_min_seconds=float(p.DELAY_MIN),
            delay_max_seconds=float(p.DELAY_MAX),
            min_eth_for_gas=str(p.MIN_ETH_FOR_GAS),
            gas_multiplier=float(getattr(p, "GAS_MULTIPLIER", 1.25)),
            receipt_timeout_seconds=int(getattr(p, "RECEIPT_TIMEOUT", 180)),
        ),
        faucet=FaucetConfig(
            enabled=bool(p.FAUCET_ENABLED),
            retries=int(getattr(p, "FAUCET_RETRIES", 2)),
            auto_register=bool(getattr(p, "AUTO_REGISTER", True)),
        ),
        swap=SwapConfig(
            enabled=bool(p.SWAP_ENABLED),
            usdc_min=str(p.SWAP_USDC_MIN),
            usdc_max=str(p.SWAP_USDC_MAX),
            slippage_bps=int(p.SWAP_SLIPPAGE_BPS),
        ),
        bid=BidConfig(
            enabled=bool(p.BID_ENABLED),
            usdc_min=str(p.BID_USDC_MIN),
            usdc_max=str(p.BID_USDC_MAX),
            price_multiplier=float(getattr(p, "BID_PRICE_MULTIPLIER", 1.5)),
        ),
    )
