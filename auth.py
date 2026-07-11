"""
Very lightweight PIN-based admin gate.

This is NOT enterprise-grade security — it's meant to stop casual viewers
from accidentally messing with the data connection or uploading files, not
to protect against a determined attacker. If this app is ever deployed
somewhere beyond trusted local/internal use, swap this for real auth
(e.g. streamlit-authenticator, or hosting behind SSO).
"""
import hashlib
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
_SALT = "nrbc-app-v1"  # fixed salt: obscures the PIN in config.json, not cryptographically hardened
DEFAULT_PIN = "1234"


def _hash(pin: str) -> str:
    return hashlib.sha256((_SALT + pin).encode()).hexdigest()


def _load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_config(data: dict) -> None:
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def get_pin_hash() -> str:
    data = _load_config()
    if "admin_pin_hash" not in data:
        # first run: initialize with the default PIN
        data["admin_pin_hash"] = _hash(DEFAULT_PIN)
        _save_config(data)
    return data["admin_pin_hash"]


def verify_pin(pin: str) -> bool:
    return _hash(pin) == get_pin_hash()


def set_pin(new_pin: str) -> None:
    data = _load_config()
    data["admin_pin_hash"] = _hash(new_pin)
    _save_config(data)


def is_default_pin_active() -> bool:
    return get_pin_hash() == _hash(DEFAULT_PIN)
