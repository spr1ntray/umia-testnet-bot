# Umia Testnet Bot

Бот для фарма активности в **Umia testnet** (Base Sepolia) с нескольких EVM-кошельков:
faucet, swap mUSDC, ставки на аукционах и таблица on-chain статистики.
Ключи, прокси и Capsolver хранятся в локальной зашифрованной базе.

Только **testnet**. Mainnet chainId заблокирован.

## Запуск

Нужен Python 3.12+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Первый старт

1. Заполни файлы в `input/`:
   - `private_keys.txt` - ключи;
   - `proxies.txt` - прокси 1:1 с ключами;
   - `capsolver_api_key.txt` - Capsolver API key (для auto-reg / faucet).
2. `python main.py` → **Создать/обновить зашифрованную базу**.
3. Снова `python main.py` → **Полный цикл**.

Прокси: `host:port:login:password`.

После создания базы plaintext-файлы очищаются. Capsolver при unlock тоже
вшивается в `database.enc` и файл стирается.

## Меню

| Пункт | Поведение |
|---|---|
| Полный цикл | auto-reg → faucet → swap / auction bids |
| Только faucet | регистрация + кран |
| Только активности | без faucet |
| Парсинг статистики | балансы, nonce, holdings, bids - таблица + CSV |
| Создать базу | encrypt keys / proxies / capsolver |

## Расходники

- EVM-кошельки (testnet);
- HTTP-прокси 1:1;
- Capsolver (Turnstile для Privy);
- testnet ETH на газ и mUSDC (faucet после login).

## Настройки

Потоки, диапазон tx (`TX_MIN` / `TX_MAX`), суммы swap/bid, задержки —
в **`parameters.py`**.

