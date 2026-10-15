import os
from pathlib import Path

import yaml
from dotenv import dotenv_values
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Configuration Precedence API")

# Allow the assignment page to call this API from a browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=["Access-Control-Allow-Origin"],
)

DEFAULTS = {
    "port": 8000,
    "workers": 1,
    "debug": False,
    "log_level": "info",
    "api_key": "default-secret-000",
}


def normalize_key(key: str) -> str:
    """Convert APP_PORT to port and normalize key names."""
    key = key.strip()

    if key.upper().startswith("APP_"):
        key = key[4:]

    return key.lower()


def coerce_value(key: str, value):
    """Convert values to the required Python types."""
    key = normalize_key(key)

    if key in ("port", "workers"):
        return int(value)

    if key == "debug":
        if isinstance(value, bool):
            return value

        return str(value).strip().lower() in {
            "true", "1", "yes", "on"
        }

    return str(value)


def load_config():
    # Layer 1: hardcoded defaults
    config = DEFAULTS.copy()

    # Layer 2: environment-specific YAML
    environment = os.environ.get("APP_ENV", "development")
    yaml_path = BASE_DIR / f"config.{environment}.yaml"

    if yaml_path.exists():
        with yaml_path.open("r", encoding="utf-8") as file:
            yaml_config = yaml.safe_load(file) or {}

        for key, value in yaml_config.items():
            config[normalize_key(key)] = value

    # Layer 3: .env file (read separately from OS variables)
    env_path = BASE_DIR / ".env"
    file_env = dotenv_values(env_path)

    for key, value in file_env.items():
        if value is None:
            continue

        if key.upper() == "NUM_WORKERS":
            config["workers"] = value
        else:
            config[normalize_key(key)] = value

    # Layer 4: actual OS environment variables
    for key, value in os.environ.items():
        if key.upper().startswith("APP_"):
            config[normalize_key(key)] = value

    return config


@app.get("/effective-config")
def effective_config(
    set_values: list[str] = Query(default=[], alias="set")
):
    config = load_config()

    # Layer 5: query parameters have the highest precedence.
    for item in set_values:
        if "=" not in item:
            continue

        key, value = item.split("=", 1)
        key = normalize_key(key)

        config[key] = value

    # Apply the required types after all layers have been merged.
    result = {
        key: coerce_value(key, value)
        for key, value in config.items()
    }

    # Never return the actual secret.
    if "api_key" in result:
        result["api_key"] = "****"

    return result
