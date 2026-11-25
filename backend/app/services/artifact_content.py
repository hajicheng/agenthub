from __future__ import annotations

import json
import re
from typing import Any

MAX_DIAGRAM_SOURCE_CHARS = 50_000

_WRAPPER_KEYS = {
    "format", "content", "markdown", "text", "files", "entry", "html", "css", "js",
    "code", "url", "source", "mermaid", "targetArtifactId", "targetId", "hunks",
    "diff", "patch", "workspacePath", "path", "language", "sizeBytes", "checksum",
    "slides", "blocks", "subtitle",
}

_PPT_LAYOUTS = {
    "title", "title-bullets", "section", "blank", "content",
    "two-column", "metrics", "timeline", "quote",
}

_MERMAID_THEMES = {"default", "base", "dark", "forest", "neutral"}


def _read_str(val: Any) -> str | None:
    return val if isinstance(val, str) and val.strip() else None


def _unwrap_stringified(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    trimmed = raw.strip()
    if not trimmed.startswith("{"):
        return raw
    try:
        parsed = json.loads(trimmed)
        if isinstance(parsed, dict) and any(k in parsed for k in _WRAPPER_KEYS):
            return parsed
    except Exception:
        pass
    return raw


def _normalize_mermaid(source: str) -> str | None:
    cleaned = source.strip()
    cleaned = re.sub(r"^```(?:mermaid)?\s*\n?", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    cleaned = cleaned.strip()
    if not cleaned or len(cleaned) > MAX_DIAGRAM_SOURCE_CHARS:
        return None
    return cleaned


def _contains_data_uri(val: Any) -> bool:
    if isinstance(val, str):
        return bool(re.match(r"^data:[^,]+;base64,", val.strip(), re.I))
    if isinstance(val, list):
        return any(_contains_data_uri(x) for x in val)
    if isinstance(val, dict):
        return any(_contains_data_uri(v) for v in val.values())
    return False


def _normalize_ppt_theme(val: Any) -> dict[str, str] | None:
    if not isinstance(val, dict):
        return None
    def hx(v: Any) -> str | None:
        s = _read_str(v)
        return s.lstrip("#") if s else None

    theme: dict[str, str] = {}
    for target, keys in [
        ("primary", ("primary", "primaryColor", "color")),
        ("background", ("background", "bg")),
        ("surface", ("surface", "card")),
        ("textBody", ("textBody", "text", "bodyColor")),
        ("textMuted", ("textMuted", "muted")),
        ("accentPositive", ("accentPositive", "positive", "success")),
        ("accentNegative", ("accentNegative", "negative", "danger", "warning")),
        ("divider", ("divider", "border")),
    ]:
        for k in keys:
            c = hx(val.get(k))
            if c:
                theme[target] = c
                break
    for target, keys in [
        ("fontHeading", ("fontHeading", "headingFont", "fontFace", "font")),
        ("fontBody", ("fontBody", "bodyFont", "font")),
    ]:
        for k in keys:
            s = _read_str(val.get(k))
            if s:
                theme[target] = s
                break
    return theme or None


def build_artifact_content(art_type: str, raw_input: Any) -> dict[str, Any] | None:
    raw = _unwrap_stringified(raw_input)

    if art_type == "web_app":
        if isinstance(raw, dict):
            if isinstance(raw.get("files"), dict):
                files = {k: v for k, v in raw["files"].items() if isinstance(v, str)}
                if not files:
                    return None
                entry = raw.get("entry") if isinstance(raw.get("entry"), str) else "index.html"
                return {"type": "web_app", "files": files, "entry": entry}
            if any(isinstance(raw.get(k), str) for k in ("html", "css", "js")):
                files = {}
                if isinstance(raw.get("html"), str):
                    files["index.html"] = raw["html"]
                if isinstance(raw.get("css"), str):
                    files["style.css"] = raw["css"]
                if isinstance(raw.get("js"), str):
                    files["script.js"] = raw["js"]
                return {"type": "web_app", "files": files, "entry": "index.html"}
            for key in ("content", "code"):
                if isinstance(raw.get(key), str):
                    return {"type": "web_app", "files": {"index.html": raw[key]}, "entry": "index.html"}
        if isinstance(raw, str):
            return {"type": "web_app", "files": {"index.html": raw}, "entry": "index.html"}
        return None

    if art_type == "document":
        if isinstance(raw, dict):
            for key in ("content", "markdown", "text"):
                if isinstance(raw.get(key), str):
                    return {"type": "document", "format": "markdown", "content": raw[key]}
        if isinstance(raw, str):
            return {"type": "document", "format": "markdown", "content": raw}
        return None

    if art_type == "diagram":
        if isinstance(raw, dict):
            syntax = _read_str(raw.get("syntax")) or _read_str(raw.get("format")) or "mermaid"
            if syntax.lower() != "mermaid":
                return None
            src = (
                _read_str(raw.get("source"))
                or _read_str(raw.get("mermaid"))
                or _read_str(raw.get("code"))
                or _read_str(raw.get("content"))
            )
            if not src:
                return None
            norm = _normalize_mermaid(src)
            if not norm:
                return None
            out: dict[str, Any] = {"type": "diagram", "syntax": "mermaid", "source": norm}
            theme = _read_str(raw.get("theme"))
            if theme in _MERMAID_THEMES:
                out["theme"] = theme
            return out
        if isinstance(raw, str):
            norm = _normalize_mermaid(raw)
            if not norm:
                return None
            return {"type": "diagram", "syntax": "mermaid", "source": norm}
        return None

    if art_type == "image":
        if isinstance(raw, dict) and isinstance(raw.get("url"), str):
            return {
                "type": "image",
                "url": raw["url"],
                "alt": raw.get("alt") if isinstance(raw.get("alt"), str) else "",
            }
        if isinstance(raw, str):
            return {"type": "image", "url": raw, "alt": ""}
        return None

    if art_type == "code_file":
        if isinstance(raw, dict):
            ws_path = _read_str(raw.get("workspacePath")) or _read_str(raw.get("path"))
            if not ws_path:
                return None
            ext = ws_path.rsplit(".", 1)[-1].lower() if "." in ws_path else "text"
            return {
                "type": "code_file",
                "workspacePath": ws_path,
                "language": _read_str(raw.get("language")) or ext,
                "sizeBytes": int(raw.get("sizeBytes") or 0),
                "checksum": _read_str(raw.get("checksum")) or "",
            }
        return None

    if art_type == "diff":
        if isinstance(raw, dict):
            target_id = _read_str(raw.get("targetArtifactId")) or _read_str(raw.get("targetId"))
            hunks = raw.get("hunks")
            if target_id and isinstance(hunks, list) and len(hunks) > 0:
                return {
                    "type": "diff",
                    "targetArtifactId": target_id,
                    "hunks": hunks,
                    "applied": bool(raw.get("applied", False)),
                }
        return None

    if art_type == "ppt":
        if _contains_data_uri(raw):
            return None
        raw_slides = raw if isinstance(raw, list) else (raw.get("slides") if isinstance(raw, dict) else None)
        if not isinstance(raw_slides, list):
            return None
        slides: list[dict[str, Any]] = []
        for item in raw_slides:
            if not isinstance(item, dict):
                continue
            slide: dict[str, Any] = {}
            if _read_str(item.get("title")):
                slide["title"] = item["title"]
            if _read_str(item.get("subtitle")):
                slide["subtitle"] = item["subtitle"]
            bullets = item.get("bullets") or item.get("points")
            if isinstance(bullets, list):
                cleaned_b = [b for b in bullets if isinstance(b, str)]
                if cleaned_b:
                    slide["bullets"] = cleaned_b
            elif isinstance(bullets, str):
                cleaned_b = [line.strip() for line in bullets.splitlines() if line.strip()]
                if cleaned_b:
                    slide["bullets"] = cleaned_b
            if isinstance(item.get("blocks"), list) and item["blocks"]:
                slide["blocks"] = item["blocks"]
            if _read_str(item.get("notes")):
                slide["notes"] = item["notes"]
            layout = _read_str(item.get("layout"))
            if layout in _PPT_LAYOUTS:
                slide["layout"] = layout
            if slide or layout == "blank":
                slides.append(slide)
        if not slides:
            return None
        out = {"type": "ppt", "slides": slides}
        if isinstance(raw, dict):
            if _read_str(raw.get("title")):
                out["title"] = raw["title"]
            theme = _normalize_ppt_theme(raw.get("theme"))
            if theme:
                out["theme"] = theme
        return out

    if art_type == "project":
        if isinstance(raw, dict) and isinstance(raw.get("files"), list):
            return {"type": "project", **raw}
        return None

    return None


def describe_artifact_content_error(art_type: str, raw_input: Any) -> str | None:
    if art_type == "diagram":
        return "Invalid Mermaid diagram content"
    return f"Invalid content for type {art_type}"
