"""AES-GCM payloads for the public repo.

The passphrase comes only from the SITE_PASSWORD environment variable
(GitHub Actions secret). Salt and iteration count are not secret; they live in
site/data/crypto.json so the browser can derive the same key with Web Crypto.
PBKDF2-HMAC-SHA256 is used because browsers can derive it natively. Each file
is 12-byte IV + AES-GCM ciphertext, with a fixed associated-data label.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ROOT = Path(__file__).resolve().parents[1]
SITE_DATA = ROOT / "site" / "data"
HEADER_PATH = SITE_DATA / "crypto.json"
AAD = b"sanghanga-v1"
ITERATIONS = 210_000
MISSING = (
    "SITE_PASSWORD 가 없습니다. 평문 시세·테마·차트는 저장하지 않습니다. "
    "GitHub 저장소 Settings → Secrets and variables → Actions 에서 "
    "저장소 비밀 SITE_PASSWORD 를 만든 뒤 워크플로를 다시 실행하세요."
)


def require_password() -> str:
    password = os.environ.get("SITE_PASSWORD", "")
    if not password.strip():
        raise SystemExit(MISSING)
    return password


def load_header() -> dict:
    if HEADER_PATH.exists():
        header = json.loads(HEADER_PATH.read_text(encoding="utf-8"))
        if header.get("kdf") != "PBKDF2-HMAC-SHA256" or header.get("alg") != "AES-256-GCM":
            raise SystemExit("site/data/crypto.json 형식을 알 수 없습니다.")
        return header
    header = {
        "v": 1,
        "alg": "AES-256-GCM",
        "kdf": "PBKDF2-HMAC-SHA256",
        "iterations": ITERATIONS,
        "salt": base64.b64encode(secrets.token_bytes(16)).decode("ascii"),
    }
    HEADER_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEADER_PATH.write_text(json.dumps(header, indent=2) + "\n", encoding="utf-8")
    return header


def derive_key(password: str, header: dict | None = None) -> bytes:
    header = header or load_header()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=base64.b64decode(header["salt"]),
        iterations=int(header["iterations"]),
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt_bytes(payload: bytes, key: bytes) -> bytes:
    iv = secrets.token_bytes(12)
    return iv + AESGCM(key).encrypt(iv, payload, AAD)


def decrypt_bytes(blob: bytes, key: bytes) -> bytes:
    if len(blob) < 13:
        raise ValueError("ciphertext too short")
    return AESGCM(key).decrypt(blob[:12], blob[12:], AAD)


def write_json(path: Path, obj, key: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(encrypt_bytes(raw, key))


def read_json(path: Path, key: bytes):
    try:
        raw = decrypt_bytes(path.read_bytes(), key)
    except Exception as exc:  # noqa: BLE001 - wrong passphrase or damaged file
        raise SystemExit(
            "암호화된 데이터를 SITE_PASSWORD 로 열 수 없습니다. 비밀 값이 이전 수집과 같은지 확인하세요."
        ) from exc
    return json.loads(raw.decode("utf-8"))


def purge_plaintext() -> None:
    """Delete market-data files that must never be committed in the clear."""
    manifest = SITE_DATA / "manifest.json"
    if manifest.exists():
        manifest.unlink()
    for folder in (SITE_DATA / "months", SITE_DATA / "charts", ROOT / "data" / "raw"):
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if path.is_file():
                path.unlink()
        for path in sorted(folder.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
        if folder.exists():
            folder.rmdir()
