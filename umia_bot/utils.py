from __future__ import annotations

import random
import time
from decimal import Decimal, ROUND_DOWN

def to_units(value: str | Decimal | int | float, decimals: int) -> int:
    amount = Decimal(str(value))
    scale = Decimal(10) ** int(decimals)
    return int((amount * scale).to_integral_value(rounding=ROUND_DOWN))

def from_units(value: int, decimals: int, places: int | None = None) -> str:
    amount = Decimal(value) / (Decimal(10) ** int(decimals))
    if places is None:
        places = min(8, int(decimals))
    quant = Decimal(1).scaleb(-places)
    text = format(amount.quantize(quant, rounding=ROUND_DOWN).normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"

def short_hash(value: str | None) -> str:
    if not value:
        return "-"
    return f"{value[:10]}...{value[-6:]}"

def short_address(value: str) -> str:
    if not value:
        return "-"
    if value.startswith("0x") and len(value) > 12:
        return f"{value[:6]}...{value[-4:]}"
    return value

def random_units(min_value: str, max_value: str, decimals: int) -> int:
    lo = Decimal(str(min_value))
    hi = Decimal(str(max_value))
    if hi < lo:
        lo, hi = hi, lo
    amount = lo + (hi - lo) * Decimal(str(random.random()))
    return to_units(amount, decimals)

def random_delay(min_seconds: float, max_seconds: float) -> None:
    if max_seconds <= 0:
        return
    lo = max(0.0, float(min_seconds))
    hi = max(lo, float(max_seconds))
    time.sleep(random.uniform(lo, hi))

def human_price_to_x96(
    human_price: float | str | Decimal,
    *,
    currency_decimals: int,
    token_decimals: int,
    tick_spacing_x96: int,
) -> int:
    human = Decimal(str(human_price))
    if human <= 0:
        raise ValueError("price must be positive")
    tick = int(tick_spacing_x96)
    if tick <= 0:
        raise ValueError("invalid tick spacing")
                                    
    raw = human * (Decimal(2) ** 96) * (Decimal(10) ** currency_decimals) / (
        Decimal(10) ** token_decimals
    )
    snapped = int(raw // tick) * tick
    if snapped <= 0:
        snapped = tick
    return snapped

def ensure_price_above_clearing(max_price_x96: int, clearing_x96: int, tick: int) -> int:
    price = int(max_price_x96)
    clear = int(clearing_x96)
    spacing = max(1, int(tick))
    if price <= clear:
        price = ((clear // spacing) + 1) * spacing
    return price
