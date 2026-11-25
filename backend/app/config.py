from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Repo root is one level above backend/
BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

# Load .env.local then .env from repo root (matching Next.js precedence without overriding real env)
for env_file in (REPO_ROOT / ".env.local", REPO_ROOT / ".env"):
    if env_file.is_file():
        load_dotenv(env_file, override=False)


def get_data_dir() -> Path:
    env_dir = os.environ.get("AGENTHUB_DATA_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    return (REPO_ROOT / ".agenthub-data").resolve()


def get_db_path() -> Path:
    return get_data_dir() / "agenthub.db"


def get_workspaces_root() -> Path:
    return get_data_dir() / "workspaces"


def get_deployments_root() -> Path:
    return get_data_dir() / "deployments"


PIN_LIMIT_PER_CONVERSATION = 5
DEFAULT_COMPANION_PORT = 3001
APP_VERSION = "0.1.0"
