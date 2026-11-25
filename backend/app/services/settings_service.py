from __future__ import annotations

import json
import os
import secrets
import time
from typing import Any

from app.config import DEFAULT_COMPANION_PORT, get_data_dir
from app.db.client import db_read, db_tx, row_to_settings

SINGLETON_ID = "singleton"

EMPTY_SETTINGS: dict[str, Any] = {
    "id": SINGLETON_ID,
    "anthropicApiKey": None,
    "anthropicBaseUrl": None,
    "openaiApiKey": None,
    "deepseekApiKey": None,
    "arkApiKey": None,
    "companionMode": "off",
    "mobileDeviceToken": None,
    "deploymentPublishEnabled": False,
    "deploymentPublishDir": None,
    "deploymentPublicBaseUrl": None,
    "updatedAt": 0,
}


def new_mobile_device_token() -> str:
    return secrets.token_urlsafe(24)


def get_app_settings() -> dict[str, Any]:
    with db_read() as conn:
        row = conn.execute("SELECT * FROM app_settings WHERE id = ?", (SINGLETON_ID,)).fetchone()
        return row_to_settings(row) if row else dict(EMPTY_SETTINGS)


def _normalize(val: Any) -> Any:
    if val is None or isinstance(val, bool):
        return val
    if isinstance(val, str):
        trimmed = val.strip()
        return None if trimmed == "" else trimmed
    return val


def update_app_settings(patch: dict[str, Any]) -> dict[str, Any]:
    current = get_app_settings()
    nxt = dict(current)
    for k, v in patch.items():
        if k in EMPTY_SETTINGS and k != "id":
            nxt[k] = _normalize(v)
    nxt["id"] = SINGLETON_ID
    nxt["updatedAt"] = int(time.time() * 1000)

    if nxt["companionMode"] != "off" and not nxt["mobileDeviceToken"]:
        nxt["mobileDeviceToken"] = new_mobile_device_token()

    with db_tx() as conn:
        conn.execute(
            """INSERT INTO app_settings (
                id, anthropic_api_key, anthropic_base_url, openai_api_key,
                deepseek_api_key, ark_api_key, companion_mode, mobile_device_token,
                deployment_publish_enabled, deployment_publish_dir,
                deployment_public_base_url, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                anthropic_api_key = excluded.anthropic_api_key,
                anthropic_base_url = excluded.anthropic_base_url,
                openai_api_key = excluded.openai_api_key,
                deepseek_api_key = excluded.deepseek_api_key,
                ark_api_key = excluded.ark_api_key,
                companion_mode = excluded.companion_mode,
                mobile_device_token = excluded.mobile_device_token,
                deployment_publish_enabled = excluded.deployment_publish_enabled,
                deployment_publish_dir = excluded.deployment_publish_dir,
                deployment_public_base_url = excluded.deployment_public_base_url,
                updated_at = excluded.updated_at""",
            (
                SINGLETON_ID,
                nxt["anthropicApiKey"],
                nxt["anthropicBaseUrl"],
                nxt["openaiApiKey"],
                nxt["deepseekApiKey"],
                nxt["arkApiKey"],
                nxt["companionMode"],
                nxt["mobileDeviceToken"],
                1 if nxt["deploymentPublishEnabled"] else 0,
                nxt["deploymentPublishDir"],
                nxt["deploymentPublicBaseUrl"],
                nxt["updatedAt"],
            ),
        )

    sync_companion_runtime(nxt)
    return nxt


def regenerate_mobile_device_token() -> dict[str, Any]:
    current = get_app_settings()
    return update_app_settings(
        {
            "mobileDeviceToken": new_mobile_device_token(),
            "companionMode": current["companionMode"],
        }
    )


def sync_companion_runtime(settings: dict[str, Any]) -> None:
    try:
        cfg_path = get_data_dir() / "companion-config.json"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(
            json.dumps(
                {
                    "companionMode": settings["companionMode"],
                    "mobileDeviceToken": settings["mobileDeviceToken"],
                    "companionPort": DEFAULT_COMPANION_PORT,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

    if settings["companionMode"] != "off" and settings["mobileDeviceToken"]:
        os.environ["AGENTHUB_MOBILE_TOKEN"] = str(settings["mobileDeviceToken"])
    else:
        os.environ.pop("AGENTHUB_MOBILE_TOKEN", None)


def get_effective_api_key(provider: str) -> str | None:
    settings = get_app_settings()
    mapping = {
        "anthropic": ("anthropicApiKey", "ANTHROPIC_API_KEY"),
        "openai": ("openaiApiKey", "OPENAI_API_KEY"),
        "deepseek": ("deepseekApiKey", "DEEPSEEK_API_KEY"),
        "ark": ("arkApiKey", "ARK_API_KEY"),
        "volcano-ark": ("arkApiKey", "ARK_API_KEY"),
    }
    if provider not in mapping:
        return None
    field, env_key = mapping[provider]
    return settings.get(field) or os.environ.get(env_key) or None


def get_effective_anthropic_base_url() -> str | None:
    settings = get_app_settings()
    return settings.get("anthropicBaseUrl") or os.environ.get("ANTHROPIC_BASE_URL") or None
