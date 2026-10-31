import hashlib
import json
import secrets


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_message(message: dict) -> str:
    return hashlib.sha256(canonical(message).encode()).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(16)}"
