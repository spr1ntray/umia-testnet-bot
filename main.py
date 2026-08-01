from __future__ import annotations

from getpass import getpass

from umia_bot.accounts import accounts_from_keys
from umia_bot.config import load_config
from umia_bot.interactive import choose, confirm
from umia_bot.runner import run_accounts, run_portfolio_report
from umia_bot.secure_store import (
    CAPSOLVER_API_KEY_FILE,
    PRIVATE_KEYS_FILE,
    PROXIES_FILE,
    clear_plaintext_inputs,
    database_exists,
    ensure_input_files,
    read_input_lines,
    save_database,
    unlock_database,
)

BANNER = r"""
        _.---.._             _.---...__
     .-'   /\   \          .'  /\     /
     `.   (  )   \        /   (  )   /
       `.  \/   .'\      /`.   \/  .'
         ``---''   )    (   ``---''
                 .';.--.;`.
               .' /_...._\ `.
             .'   `.a  a.'   `.
            (        \/        )
             `.___..-'`-..___.`
                \          /
 BY SPRINTRAY    `-.____.-'    UMIA TESTNET
"""

def print_banner() -> None:
    print(BANNER)

def main() -> int:
    print_banner()
    ensure_input_files()
    selected = show_menu()

    if selected.get("action") == "create_db":
        create_database()
        return 0

    if not database_exists():
        print("  База не найдена. Сначала выбери 'Создать зашифрованную базу'.")
        return 1

    cfg = load_config()
    secrets = unlock_database()
    accounts = accounts_from_keys(secrets.private_keys, secrets.proxies)
    if not accounts:
        print("  В базе нет валидных кошельков.")
        return 1

    cap_key = secrets.capsolver_api_key or ""
    mode = selected.get("mode", "full")
    print(f"  Кошельков: {len(accounts)} | потоков: {cfg.run.max_workers}")
    print(f"  On-chain tx на аккаунт: {cfg.run.tx_min}-{cfg.run.tx_max}")
    print("  Сеть: Base Sepolia (84532) ONLY — mainnet заблокирован")
    print(f"  Auto-reg (Privy+Capsolver): {'ON' if cap_key and cfg.faucet.auto_register else 'OFF'}")

    if mode == "parse":
        print("  Режим: парсинг балансов (без транзакций)")
        return run_portfolio_report(cfg, accounts)

    if mode == "faucet":
        print("  Режим: только faucet (+ auto-reg)")
        return run_accounts(
            cfg, accounts, do_faucet=True, do_activities=False, capsolver_api_key=cap_key
        )

    if mode == "activities":
        print("  Режим: только активности (без faucet)")
        return run_accounts(
            cfg, accounts, do_faucet=False, do_activities=True, capsolver_api_key=cap_key
        )

          
    print("  Режим: полный цикл (auto-reg → faucet soft → activities)")
    return run_accounts(
        cfg, accounts, do_faucet=True, do_activities=True, capsolver_api_key=cap_key
    )

def show_menu() -> dict:
    return choose(
        "UMIA TESTNET BOT",
        [
            ("Полный цикл (faucet → activities)", {"mode": "full"}),
            ("Только faucet", {"mode": "faucet"}),
            ("Только активности (без faucet)", {"mode": "activities"}),
            ("Парсинг статистики (балансы + on-chain)", {"mode": "parse"}),
            ("Создать/обновить зашифрованную базу", {"action": "create_db"}),
        ],
    )

def create_database() -> None:
    keys = read_input_lines(PRIVATE_KEYS_FILE)
    proxies = read_input_lines(PROXIES_FILE)
    cap_lines = read_input_lines(CAPSOLVER_API_KEY_FILE)
    capsolver_api_key = cap_lines[0] if cap_lines else ""

    print("=" * 52)
    print("  СОЗДАНИЕ ЗАШИФРОВАННОЙ БАЗЫ")
    print("=" * 52)
    print(f"  Найдено ключей: {len(keys)}")
    print(f"  Найдено прокси: {len(proxies)}")
    print(
        f"  Capsolver API key: "
        f"{'найден (будет зашифрован в database.enc)' if capsolver_api_key else 'не найден'}"
    )

    if not keys and database_exists():
        print("  private_keys.txt пустой, беру ключи из текущей базы.")
        existing = unlock_database()
        keys = existing.private_keys
        if not proxies:
            proxies = existing.proxies
        if not capsolver_api_key:
            capsolver_api_key = existing.capsolver_api_key

    if not keys:
        print(f"  Добавь приватные ключи в {PRIVATE_KEYS_FILE} и повтори.")
        return

    if len(proxies) and len(proxies) != len(keys):
        print(
            f"  Внимание: прокси ({len(proxies)}) != ключей ({len(keys)}). "
            "Прокси будут ротироваться по кругу."
        )
    if not capsolver_api_key:
        print(
            "  Внимание: без Capsolver faucet auto-reg не сработает "
            f"(положи ключ в {CAPSOLVER_API_KEY_FILE} перед create DB)."
        )

    if database_exists() and not confirm("База уже существует. Перезаписать?", default=False):
        print("  Отмена.")
        return

    while True:
        password = getpass("  Придумай пароль: ")
        password2 = getpass("  Повтори пароль:  ")
        if not password:
            print("  Пароль не может быть пустым.")
            continue
        if password != password2:
            print("  Пароли не совпадают.")
            continue
        break

    save_database(password, keys, proxies, capsolver_api_key)
    clear_plaintext_inputs()
    print("  База создана: input/database.enc")
    print("  Plaintext keys/proxies/capsolver очищены — секреты только в database.enc.")
    print(
        f"  Чтобы обновить Capsolver: положи ключ в {CAPSOLVER_API_KEY_FILE}, "
        "запусти бота и введи пароль — ключ вошьётся в enc и файл сотрётся."
    )

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n  Прервано пользователем.")
