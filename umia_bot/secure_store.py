from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from getpass import getpass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

INPUT_DIR = Path("input")
DB_FILE = INPUT_DIR / "database.enc"
SALT_FILE = INPUT_DIR / "database.salt"
PRIVATE_KEYS_FILE = INPUT_DIR / "private_keys.txt"
PROXIES_FILE = INPUT_DIR / "proxies.txt"
CAPSOLVER_API_KEY_FILE = INPUT_DIR / "capsolver_api_key.txt"

@dataclass(frozen=True)
class SecretBundle:
    private_keys: list[str]
    proxies: list[str]
    capsolver_api_key: str = ""

def ensure_input_files() -> None:
    INPUT_DIR.mkdir(exist_ok=True)
    _chmod_private(INPUT_DIR, directory=True)
    for path, template in (
        (
            PRIVATE_KEYS_FILE,
            "# Приватные ключи EVM, по одному на строку.\n"
            "# После создания базы файл очищается.\n",
        ),
        (
            PROXIES_FILE,
            "# HTTP-прокси 1:1 с ключами: host:port:user:pass\n"
            "# Пример: 1.2.3.4:8080:user:pass\n",
        ),
        (
            CAPSOLVER_API_KEY_FILE,
            "# Capsolver API key\n",
        ),
    ):
        if not path.exists():
            path.write_text(template, encoding="utf-8")
        _chmod_private(path)

def read_input_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text("utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

def database_exists() -> bool:
    return DB_FILE.exists() and SALT_FILE.exists()

def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))

def save_database(
    password: str,
    private_keys: list[str],
    proxies: list[str],
    capsolver_api_key: str = "",
) -> None:
    INPUT_DIR.mkdir(exist_ok=True)
    _chmod_private(INPUT_DIR, directory=True)
    salt = os.urandom(16)
    key = _derive_key(password, salt)
    payload = json.dumps(
        {
            "private_keys": private_keys,
            "proxies": proxies,
            "capsolver_api_key": capsolver_api_key,
        },
        ensure_ascii=False,
    )
    SALT_FILE.write_bytes(salt)
    DB_FILE.write_bytes(Fernet(key).encrypt(payload.encode("utf-8")))
    _chmod_private(SALT_FILE)
    _chmod_private(DB_FILE)

def load_database(password: str) -> SecretBundle:
    try:
        salt = SALT_FILE.read_bytes()
        encrypted = DB_FILE.read_bytes()
        key = _derive_key(password, salt)
        payload = json.loads(Fernet(key).decrypt(encrypted).decode("utf-8"))
    except InvalidToken:
        raise ValueError("wrong password") from None
    return SecretBundle(
        private_keys=list(payload.get("private_keys", [])),
        proxies=list(payload.get("proxies", [])),
        capsolver_api_key=str(payload.get("capsolver_api_key") or ""),
    )

def unlock_database() -> SecretBundle:
    for attempt in range(3):
        password = getpass("  Пароль базы: ")
        try:
            secrets = load_database(password)
            file_key = _read_capsolver_key()
                                                                          
            if file_key and file_key != secrets.capsolver_api_key:
                save_database(
                    password,
                    secrets.private_keys,
                    secrets.proxies,
                    file_key,
                )
                secrets = SecretBundle(
                    private_keys=secrets.private_keys,
                    proxies=secrets.proxies,
                    capsolver_api_key=file_key,
                )
                print("  Capsolver key из файла вшит в database.enc")
            if file_key or CAPSOLVER_API_KEY_FILE.exists():
                _wipe_capsolver_file()
            print(
                "  Загружено:"
                f" {len(secrets.private_keys)} ключей,"
                f" {len(secrets.proxies)} прокси,"
                f" Capsolver: {'есть (encrypted)' if secrets.capsolver_api_key else 'нет'}"
            )
            return secrets
        except ValueError:
            if attempt < 2:
                print("  Неверный пароль, попробуй снова.")
    raise SystemExit("  Неверный пароль.")

def _read_capsolver_key() -> str:
    lines = read_input_lines(CAPSOLVER_API_KEY_FILE)
    return lines[0].strip() if lines else ""

def _wipe_capsolver_file() -> None:
    CAPSOLVER_API_KEY_FILE.write_text(
        "# Capsolver API key\n",
        encoding="utf-8",
    )
    _chmod_private(CAPSOLVER_API_KEY_FILE)

def clear_plaintext_inputs() -> None:
    PRIVATE_KEYS_FILE.write_text(
        "# Приватные ключи EVM, по одному на строку.\n",
        encoding="utf-8",
    )
    PROXIES_FILE.write_text(
        "# HTTP-прокси 1:1 с ключами: host:port:user:pass\n",
        encoding="utf-8",
    )
    _wipe_capsolver_file()
    for path in (PRIVATE_KEYS_FILE, PROXIES_FILE, CAPSOLVER_API_KEY_FILE):
        _chmod_private(path)

def _chmod_private(path: Path, *, directory: bool = False) -> None:
    try:
        path.chmod(0o700 if directory else 0o600)
    except OSError:
        pass
