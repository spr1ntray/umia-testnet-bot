from __future__ import annotations

import re
from typing import Iterable

                                                           
ALLOWED_CHAIN_IDS: frozenset[int] = frozenset({84532})
BLOCKED_CHAIN_IDS: frozenset[int] = frozenset({1, 8453, 10, 42161, 137})

_HEX_SECRET = re.compile(r"(0x)?[a-fA-F0-9]{64,}")

class MainnetBlockedError(RuntimeError):
    pass

def assert_allowed_chain(chain_id: int, *, context: str = "") -> None:
    cid = int(chain_id)
    if cid in BLOCKED_CHAIN_IDS or cid not in ALLOWED_CHAIN_IDS:
        where = f" ({context})" if context else ""
        raise MainnetBlockedError(
            f"chainId {cid} blocked{where}: only Base Sepolia (84532) is allowed"
        )

def is_allowed_chain(chain_id: int) -> bool:
    return int(chain_id) in ALLOWED_CHAIN_IDS

def redact_text(text: str, secrets: Iterable[str | None] = ()) -> str:
    out = str(text)
    for secret in secrets:
        if not secret:
            continue
        s = str(secret)
        if len(s) >= 8:
            out = out.replace(s, "***")
                          
        if "://" in s:
            try:
                                            
                after = s.split("://", 1)[1]
                if "@" in after:
                    creds = after.split("@", 1)[0]
                    if creds:
                        out = out.replace(creds, "***:***")
            except Exception:
                pass
                                  
    out = _HEX_SECRET.sub(lambda m: m.group(0)[:6] + "…" + m.group(0)[-4:], out)
    return out
