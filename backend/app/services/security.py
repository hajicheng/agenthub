from __future__ import annotations

import re
from app.services.platform import Platform, current_platform

POSIX_BANNED: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-rf\s+/"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bchmod\s+\d{3,4}\s+/"),
    re.compile(r":\(\)\{\s*:\|:&\s*\}"),
    re.compile(r"curl\s+[^|]*\|\s*(bash|sh)"),
    re.compile(r"wget\s+[^|]*\|\s*(bash|sh)"),
    re.compile(r"\beval\b"),
    re.compile(r"\bexec\b\s+"),
]

WINDOWS_BANNED: list[re.Pattern[str]] = [
    re.compile(r"\b(del|erase)\s+/[fsq\s/]*[a-z]:\\?", re.I),
    re.compile(r"\brd\s+/[sq\s/]*[a-z]:\\?", re.I),
    re.compile(r"\bRemove-Item\b[^|;]*-Recurse[^|;]*-Force", re.I),
    re.compile(r"\bRemove-Item\b[^|;]*-Force[^|;]*-Recurse", re.I),
    re.compile(r"\bri\b[^|;]*-Recurse[^|;]*-Force", re.I),
    re.compile(r"\brm\b[^|;]*-Recurse[^|;]*-Force", re.I),
    re.compile(r"\brm\b[^|;]*-Force[^|;]*-Recurse", re.I),
    re.compile(r"\brmdir\b[^|;]*-Recurse[^|;]*-Force", re.I),
    re.compile(r"\brmdir\b[^|;]*-Force[^|;]*-Recurse", re.I),
    re.compile(r"\bformat\s+[a-z]:", re.I),
    re.compile(r"\bshutdown\b", re.I),
    re.compile(r"\brestart-computer\b", re.I),
    re.compile(r"\bstop-computer\b", re.I),
    re.compile(r"\breg\s+delete\b", re.I),
    re.compile(r"\bRemove-ItemProperty\b", re.I),
    re.compile(r"\btaskkill\b[^|;]*/im\s*\*", re.I),
    re.compile(r"\bStop-Process\b[^|;]*-Force[^|;]*\*", re.I),
    re.compile(r"Invoke-Expression\s*\(\s*(Invoke-WebRequest|iwr|curl|wget)", re.I),
    re.compile(r"\biex\b\s*\(\s*(iwr|curl|wget|Invoke-WebRequest)", re.I),
    re.compile(r"Set-ExecutionPolicy\s+(Unrestricted|Bypass)", re.I),
    re.compile(r"\bbcdedit\b", re.I),
    re.compile(r"\bdiskpart\b", re.I),
    re.compile(r"\bcipher\s+/w", re.I),
]


def get_banned_patterns(platform: Platform | None = None) -> list[re.Pattern[str]]:
    plat = platform or current_platform()
    return list(WINDOWS_BANNED if plat == "windows" else POSIX_BANNED)


def find_banned_pattern(command: str, platform: Platform | None = None) -> re.Pattern[str] | None:
    for pat in get_banned_patterns(platform):
        if pat.search(command):
            return pat
    return None
