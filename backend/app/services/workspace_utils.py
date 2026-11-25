from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from app.services.platform import IS_WINDOWS

_cached_drives: list[str] | None = None


def get_effective_cwd(workspace: dict[str, Any]) -> str:
    if workspace.get("mode") == "local" and workspace.get("boundPath"):
        return str(workspace["boundPath"])
    return str(workspace["rootPath"])


def is_path_within(child: str, parent: str) -> bool:
    c = os.path.abspath(child)
    p = os.path.abspath(parent)
    if IS_WINDOWS:
        c = c.lower()
        p = p.lower()
    if c == p:
        return True
    sep = os.sep
    p_prefix = p if p.endswith(sep) else p + sep
    return c.startswith(p_prefix)


def resolve_safe_path(workspace: dict[str, Any], target: str) -> str | None:
    cwd = get_effective_cwd(workspace)
    if os.path.isabs(target):
        abs_path = os.path.abspath(target)
    else:
        abs_path = os.path.abspath(os.path.join(cwd, target))
    if not is_path_within(abs_path, cwd):
        return None
    return abs_path


def assert_path_within_workspace(workspace: dict[str, Any], target: str) -> str:
    resolved = resolve_safe_path(workspace, target)
    if not resolved:
        raise PermissionError(f'Path "{target}" is outside workspace')
    return resolved


def get_available_drives() -> list[str]:
    global _cached_drives
    if _cached_drives is not None:
        return _cached_drives
    if not IS_WINDOWS:
        _cached_drives = ["/"]
        return _cached_drives
    drives: list[str] = []
    for code in range(65, 91):
        root = f"{chr(code)}:\\"
        if os.path.exists(root):
            drives.append(root)
    _cached_drives = drives
    return drives


def _get_system_roots() -> list[str]:
    if not IS_WINDOWS:
        return ["/etc", "/System", "/usr", "/bin", "/sbin", "/var", "/private", "/Library/Keychains"]
    roots: list[str] = []
    for drive in get_available_drives():
        for name in (
            "Windows",
            "Program Files",
            "Program Files (x86)",
            "$Recycle.Bin",
            "System Volume Information",
            "Recovery",
        ):
            roots.append(os.path.join(drive, name))
    sys_drive = os.environ.get("SystemDrive", "C:")
    roots.append(os.path.join(sys_drive + "\\", "ProgramData"))
    return roots


def _get_sensitive_segments() -> list[str]:
    shared = [".ssh", ".aws", ".gcloud", ".kube", ".gnupg", ".docker", ".azure"]
    if IS_WINDOWS:
        return shared + [
            r"AppData\Roaming\Microsoft\Credentials",
            r"AppData\Local\Microsoft\Credentials",
            r"AppData\Roaming\Microsoft\Protect",
            r"AppData\Roaming\gh",
            r"AppData\Roaming\Claude",
        ]
    return shared + [
        ".config/gh",
        "Library/Keychains",
        "Library/Application Support/Code/User",
    ]


def is_path_safe(abs_path: str) -> bool:
    home = os.path.abspath(str(Path.home()))
    normalized = os.path.abspath(abs_path)

    if IS_WINDOWS and re.match(r"^\\\\[?.]\\", normalized):
        return False
    if IS_WINDOWS and normalized.startswith("\\\\"):
        return False

    home_key = home.lower() if IS_WINDOWS else home
    norm_key = normalized.lower() if IS_WINDOWS else normalized
    if norm_key == home_key:
        return False

    for seg in _get_sensitive_segments():
        sensitive = os.path.abspath(os.path.join(home, seg))
        if is_path_within(normalized, sensitive):
            return False

    for root in _get_system_roots():
        if is_path_within(normalized, root):
            return False

    return True
