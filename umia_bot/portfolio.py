from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from web3 import Web3

from .accounts import AccountConfig
from .api import UmiaApi
from .chain import ChainClient
from .console import get_logger, setup_console_logger
from .safety import redact_text
from .utils import from_units, short_address

@dataclass
class WalletStats:
    label: str
    address: str = "-"
    proxy: str = "no"
    status: str = "ok"
    note: str = ""

    eth_raw: int = 0
    musdc_raw: int = 0
    nonce: int = 0

                           
    venture_tokens: int = 0                            
    venture_symbols: str = ""                        

                   
    holdings: int = 0
    holdings_usd: float = 0.0
    holding_tickers: str = ""

                     
    auctions: int = 0                      
    bids: int = 0
    bid_usdc: float = 0.0
    claimable: int = 0                                          

    lp: int = 0
    lp_usd: float = 0.0

    n: int = 0

    @property
    def eth(self) -> str:
        return from_units(self.eth_raw, 18)

    @property
    def musdc(self) -> str:
        return from_units(self.musdc_raw, 6)

def run_portfolio_report(cfg: Any, accounts: list[AccountConfig]) -> int:
    setup_console_logger()
    log = get_logger("SYSTEM")
    if not accounts:
        log.warning("Нет аккаунтов для парсинга")
        return 0

    log.info(
        f"Парсинг on-chain + Umia stats | кошельков: {len(accounts)} | "
        f"потоков: {cfg.run.max_workers}"
    )

    rows: list[WalletStats] = []
    max_workers = max(1, int(cfg.run.max_workers))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(collect_wallet_stats, cfg, account): account
            for account in accounts
        }
        for future in as_completed(futures):
            account = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                err = redact_text(str(exc), [account.private_key, account.proxy])
                rows.append(
                    WalletStats(
                        label=account.label,
                        proxy="yes" if account.proxy else "no",
                        status="error",
                        note=err[:80],
                    )
                )

    rows.sort(key=_label_sort)
    for i, row in enumerate(rows, start=1):
        row.n = i

    print_stats_table(rows)
    _print_totals(rows, log)
    _write_csv(rows, log)
    err = sum(1 for r in rows if r.status != "ok")
    return 1 if err else 0

def collect_wallet_stats(cfg: Any, account: AccountConfig) -> WalletStats:
    chain = ChainClient(cfg, account)
    api = UmiaApi(cfg, account)
    stats = WalletStats(
        label=account.label,
        address=chain.address,
        proxy="yes" if account.proxy else "no",
    )

                           
    try:
        chain.assert_chain()
        stats.eth_raw = chain.native_balance()
        stats.musdc_raw = chain.erc20_balance(cfg.network.usdc_address)
        stats.nonce = int(chain.w3.eth.get_transaction_count(chain.address, "latest"))
    except Exception as exc:
        stats.status = "error"
        stats.note = redact_text(str(exc), [account.private_key, account.proxy])[:80]
        return stats

                                           
    try:
        tokens = api.tokens(chain.address)
        ventures: list[tuple[str, int]] = []
        for t in tokens:
            bal = int(t.get("balance") or 0)
            if bal <= 0:
                continue
            kind = t.get("kind") or ""
            sym = str(t.get("symbol") or "?")
            if kind == "usdc" or t.get("isMock"):
                                                                             
                if kind == "usdc" and bal > stats.musdc_raw:
                    stats.musdc_raw = bal
                continue
            ventures.append((sym, bal))
        stats.venture_tokens = len(ventures)
        stats.venture_symbols = ",".join(s for s, _ in ventures[:4])
        if len(ventures) > 4:
            stats.venture_symbols += f"+{len(ventures) - 4}"
    except Exception as exc:
        stats.note = (stats.note + f" tokens:{exc}")[:80]

                                
    try:
        portfolio = api.portfolio(chain.address)
        holdings = portfolio.get("holdings") or []
        if not isinstance(holdings, list):
            holdings = []
        stats.holdings = len(holdings)
        stats.holdings_usd = float(portfolio.get("totalValue") or 0)
        tickers = [str(h.get("ticker") or h.get("slug") or "?") for h in holdings[:4]]
        stats.holding_tickers = ",".join(tickers)
        if len(holdings) > 4:
            stats.holding_tickers += f"+{len(holdings) - 4}"
        claimable = 0
        for h in holdings:
                                                            
            try:
                if int(h.get("amount") or 0) > 0:
                    claimable += 1
            except Exception:
                if h.get("amount"):
                    claimable += 1
        stats.claimable = claimable
    except Exception as exc:
        stats.note = (stats.note + f" pf:{exc}")[:80]

                                          
    try:
        slugs = api.participated_slugs(chain.address)
        stats.auctions = len(slugs)
        bid_count = 0
        bid_usdc = 0.0
        for slug in slugs:
            try:
                data = api.fundraise_bids(slug, chain.address, limit=50)
                bids = data.get("bids") or []
                summary = data.get("summary") or {}
                bid_count += len(bids)
                                                
                if summary.get("totalBidAmount") is not None:
                    bid_usdc += float(summary["totalBidAmount"])
                else:
                    for b in bids:
                        amt = b.get("amount")
                        if amt is None:
                            continue
                                                                 
                        try:
                            v = float(amt)
                            if v > 1_000_000:                    
                                v = v / 1_000_000
                            bid_usdc += v
                        except Exception:
                            pass
            except Exception:
                continue
        stats.bids = bid_count
        stats.bid_usdc = round(bid_usdc, 4)
    except Exception as exc:
        stats.note = (stats.note + f" bids:{exc}")[:80]

                
    try:
        lp = api.lp_positions(chain.address)
        positions = lp.get("positions") or []
        stats.lp = len(positions) if isinstance(positions, list) else 0
        stats.lp_usd = float(lp.get("totalUsd") or 0)
    except Exception:
        pass

                                
    min_eth = int(Web3.to_wei(float(cfg.run.min_eth_for_gas), "ether"))
    if stats.eth_raw < min_eth or stats.musdc_raw <= 0:
        if stats.status == "ok":
            stats.note = stats.note or "low_funds"

    return stats

def print_stats_table(rows: list[WalletStats]) -> None:
    headers = [
        "#",
        "Label",
        "Address",
        "ETH",
        "mUSDC",
        "Nonce",
        "Tok",
        "Hold",
        "Hold$",
        "Auct",
        "Bids",
        "Bid$",
        "LP",
        "Proxy",
        "Status",
        "Note",
    ]
    table: list[list[str]] = []
    for r in rows:
        table.append(
            [
                str(r.n),
                r.label,
                short_address(r.address) if r.address.startswith("0x") else r.address,
                r.eth,
                r.musdc,
                str(r.nonce),
                str(r.venture_tokens),
                str(r.holdings),
                _fmt_usd(r.holdings_usd),
                str(r.auctions),
                str(r.bids),
                _fmt_usd(r.bid_usdc),
                str(r.lp),
                r.proxy,
                r.status,
                (r.note or r.venture_symbols or r.holding_tickers or "")[:28],
            ]
        )

           
    table.append(
        [
            "",
            "TOTAL",
            f"{len(rows)} w",
            from_units(sum(r.eth_raw for r in rows), 18),
            from_units(sum(r.musdc_raw for r in rows), 6),
            str(sum(r.nonce for r in rows)),
            str(sum(r.venture_tokens for r in rows)),
            str(sum(r.holdings for r in rows)),
            _fmt_usd(sum(r.holdings_usd for r in rows)),
            str(sum(r.auctions for r in rows)),
            str(sum(r.bids for r in rows)),
            _fmt_usd(sum(r.bid_usdc for r in rows)),
            str(sum(r.lp for r in rows)),
            "",
            "",
            "",
        ]
    )

    widths = [len(h) for h in headers]
    for row in table:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: list[str]) -> str:
        return "  " + " | ".join(cells[i].ljust(widths[i]) for i in range(len(headers)))

    sep = "  " + "-+-".join("-" * w for w in widths)
    print()
    print(fmt_row(headers))
    print(sep)
    for i, row in enumerate(table):
        if i == len(table) - 1:
            print(sep)
        print(fmt_row(row))
    print()
    print(
        "  Legend: Tok=nonzero venture tokens | Hold=auction holdings | "
        "Auct=participated auctions | Bid$=sum bid USDC | Nonce=on-chain tx count"
    )
    print()

def _print_totals(rows: list[WalletStats], log: Any) -> None:
    ok = sum(1 for r in rows if r.status == "ok")
    err = len(rows) - ok
    funded = sum(1 for r in rows if r.musdc_raw > 0 and r.eth_raw > 0)
    log.info(
        f"Итого: {ok} ok / {err} err / {funded} funded | "
        f"bids={sum(r.bids for r in rows)} auctions={sum(r.auctions for r in rows)} | "
        f"hold$={_fmt_usd(sum(r.holdings_usd for r in rows))} "
        f"bid$={_fmt_usd(sum(r.bid_usdc for r in rows))}"
    )

def _write_csv(rows: list[WalletStats], log: Any) -> None:
    try:
        run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        path = Path("logs") / f"portfolio_{run_id}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        cols = [
            "n",
            "label",
            "address",
            "eth",
            "musdc",
            "nonce",
            "venture_tokens",
            "venture_symbols",
            "holdings",
            "holdings_usd",
            "holding_tickers",
            "auctions",
            "bids",
            "bid_usdc",
            "claimable",
            "lp",
            "lp_usd",
            "proxy",
            "status",
            "note",
        ]
        with path.open("w", encoding="utf-8") as fh:
            fh.write(",".join(cols) + "\n")
            for r in rows:
                vals = [
                    r.n,
                    r.label,
                    r.address,
                    r.eth,
                    r.musdc,
                    r.nonce,
                    r.venture_tokens,
                    r.venture_symbols,
                    r.holdings,
                    f"{r.holdings_usd:.6f}",
                    r.holding_tickers,
                    r.auctions,
                    r.bids,
                    f"{r.bid_usdc:.6f}",
                    r.claimable,
                    r.lp,
                    f"{r.lp_usd:.6f}",
                    r.proxy,
                    r.status,
                    (r.note or "").replace(",", ";"),
                ]
                fh.write(",".join(str(v) for v in vals) + "\n")
        log.info(f"CSV: {path}")
    except Exception as exc:
        log.warning(f"CSV не сохранён: {exc}")

def _label_sort(r: WalletStats) -> tuple:
    label = r.label or ""
    if label.startswith("wallet-"):
        try:
            return (0, int(label.split("-", 1)[1]))
        except Exception:
            pass
    return (1, label)

def _fmt_usd(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:.1f}"
    if abs(v) >= 1:
        return f"{v:.2f}"
    if abs(v) == 0:
        return "0"
    return f"{v:.4f}"
