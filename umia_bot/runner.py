from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .accounts import AccountConfig
from .actions import WalletActionRunner
from .api import UmiaApi
from .chain import ChainClient
from .console import get_logger, setup_console_logger
from .logger import JsonlLogger
from .portfolio import run_portfolio_report
from .safety import redact_text
from .utils import short_address, short_hash

def run_accounts(
    cfg: Any,
    accounts: list[AccountConfig],
    *,
    do_faucet: bool = True,
    do_activities: bool = True,
    capsolver_api_key: str = "",
) -> int:
    setup_console_logger()
    system_log = get_logger("SYSTEM")
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    logger = JsonlLogger(Path("logs") / f"umia_{run_id}.jsonl")

    wallet_list = list(accounts)
    if cfg.run.shuffle_wallets and wallet_list:
        random.shuffle(wallet_list)

    system_log.info(
        f"Старт | кошельков: {len(wallet_list)} | "
        f"faucet={do_faucet} activities={do_activities} | "
        f"auto_reg={bool(cfg.faucet.auto_register and capsolver_api_key)} | "
        f"tx={cfg.run.tx_min}-{cfg.run.tx_max} | jsonl: {logger.path}"
    )
    if do_faucet and cfg.faucet.auto_register and not capsolver_api_key:
        system_log.warning(
            "Capsolver API key не найден — auto-reg/faucet auth недоступны. "
            "Добавь ключ в input/capsolver_api_key.txt"
        )
    if not wallet_list:
        system_log.warning("Нет кошельков")
        return 0

    max_workers = max(1, int(cfg.run.max_workers))
    failed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                run_account,
                cfg,
                account,
                logger,
                do_faucet=do_faucet,
                do_activities=do_activities,
                capsolver_api_key=capsolver_api_key,
            ): account
            for account in wallet_list
        }
        for future in as_completed(futures):
            account = futures[future]
            try:
                future.result()
            except Exception as exc:
                failed += 1
                error = redact_text(str(exc), [account.private_key, account.proxy])
                logger.emit(
                    {
                        "label": account.label,
                        "address": None,
                        "action": "account_error",
                        "status": "failed",
                        "tx_hash": None,
                        "details": {"error": error},
                    }
                )
                get_logger(account.label).error(f"Ошибка аккаунта: {error}")

    system_log.info(f"Готово | ошибок аккаунтов: {failed}/{len(wallet_list)}")
    return 1 if failed else 0

def run_account(
    cfg: Any,
    account: AccountConfig,
    logger: JsonlLogger,
    *,
    do_faucet: bool,
    do_activities: bool,
    capsolver_api_key: str = "",
) -> None:
    chain = ChainClient(cfg, account)
    api = UmiaApi(cfg, account)

    def emit(event: dict[str, Any]) -> None:
                               
        logger.emit(event)
        _console_event(event)

    WalletActionRunner(
        cfg,
        chain,
        api,
        emit,
        do_faucet=do_faucet,
        do_activities=do_activities,
        capsolver_api_key=capsolver_api_key,
    ).run()

def _console_event(event: dict[str, Any]) -> None:
    address = short_address(event["address"]) if event.get("address") else "-"
    wallet = f"{event.get('label', '?')} {address}"
    log = get_logger(wallet)
    action = event.get("action")
    status = event.get("status")
    details = event.get("details") or {}
    tx = short_hash(event.get("tx_hash"))
    tx_s = "" if tx == "-" else f" | tx {tx}"

    if action == "start":
        log.info("Старт кошелька")
    elif action == "auth" and status == "ok":
        log.success(
            f"auth/login ok | new={details.get('is_new_user')} user={details.get('user_id')}"
        )
    elif action == "auth" and status == "failed":
        log.warning(f"auth fail | {details.get('error')}")
    elif action == "signup" and status == "ok":
        log.success("signup/sync ok")
    elif action == "signup" and status == "warn":
        log.warning(f"signup warn | {details.get('error')}")
    elif action == "skip" and status == "no_testnet_funds":
        log.warning(
            f"skip | no_testnet_funds | eth={details.get('eth')} usdc={details.get('usdc')}"
        )
    elif action == "faucet" and status == "ok":
        log.success(f"faucet ok | {details.get('response')}")
    elif action == "faucet" and status == "failed":
        log.warning(f"faucet fail | {details.get('error')}")
    elif action == "faucet" and status == "skipped":
        log.warning(f"faucet skip | {details.get('reason')}")
    elif action == "balances" and status == "waiting":
        log.info(
            f"ждём токены… eth={details.get('eth')} usdc={details.get('usdc')} "
            f"raw={details.get('usdc_raw')}"
        )
    elif action == "balances":
        log.info(
            f"баланс eth={details.get('eth')} usdc={details.get('usdc')} "
            f"(raw={details.get('usdc_raw')} token={short_address(str(details.get('usdc_token') or ''))})"
        )
    elif action == "plan":
        log.info(f"план: {details.get('target_txs')} on-chain tx")
    elif action == "swap" and status == "ok":
        log.success(
            f"swap {details.get('sell')} {details.get('sell_token')} → "
            f"{details.get('buy_token')}{tx_s} | txs={details.get('txs_ok')}"
        )
    elif action == "bid" and status == "ok":
        log.success(
            f"bid {details.get('slug')} amount={details.get('amount_usdc')} mUSDC{tx_s} "
            f"| txs={details.get('txs_ok')}"
        )
    elif action in {"approve", "bid_prep"} and status == "ok":
        log.info(f"{action} ok{tx_s} | txs={details.get('txs_ok')}")
    elif action == "finish":
        log.success(f"finish | txs_ok={details.get('txs_ok')} target={details.get('target')}")
    elif status == "failed":
        log.error(f"{action} failed | {details.get('error')}")
    else:
        log.info(f"{action} {status}{tx_s}")
