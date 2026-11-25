from __future__ import annotations

import io
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt


def _hex_to_rgb(hex_str: str | None, fallback: str = "1E293B") -> RGBColor:
    cleaned = (hex_str or fallback).lstrip("#")
    if len(cleaned) != 6:
        cleaned = fallback
    try:
        return RGBColor(int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))
    except ValueError:
        return RGBColor(0x1E, 0x29, 0x3B)


def slides_to_pptx_bytes(content: dict[str, Any], fallback_title: str = "Presentation") -> bytes:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank_layout = prs.slide_layouts[6]

    theme = content.get("theme") or {}
    primary = _hex_to_rgb(theme.get("primary"), "1A3C6E")
    bg_color = _hex_to_rgb(theme.get("background"), "F8FAFC")
    body_color = _hex_to_rgb(theme.get("textBody"), "1E293B")
    muted_color = _hex_to_rgb(theme.get("textMuted"), "64748B")

    slides = content.get("slides") or []
    for idx, s in enumerate(slides):
        slide = prs.slides.add_slide(blank_layout)
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = bg_color

        title_text = s.get("title") or (fallback_title if idx == 0 else "")
        subtitle_text = s.get("subtitle") or ""

        y_cursor = 0.6
        if title_text:
            tb = slide.shapes.add_textbox(Inches(0.8), Inches(y_cursor), Inches(11.7), Inches(0.8))
            tf = tb.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = str(title_text)
            p.font.size = Pt(32 if idx == 0 else 26)
            p.font.bold = True
            p.font.color.rgb = primary
            y_cursor += 0.9

        if subtitle_text:
            tb = slide.shapes.add_textbox(Inches(0.8), Inches(y_cursor), Inches(11.7), Inches(0.5))
            tf = tb.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = str(subtitle_text)
            p.font.size = Pt(16)
            p.font.color.rgb = muted_color
            y_cursor += 0.6

        lines: list[str] = []
        for b in s.get("bullets") or []:
            lines.append(f"• {b}")

        for blk in s.get("blocks") or []:
            if not isinstance(blk, dict):
                continue
            btype = blk.get("type")
            if btype == "heading":
                lines.append(str(blk.get("text") or ""))
            elif btype == "paragraph":
                lines.append(str(blk.get("text") or ""))
            elif btype == "bullets":
                for item in blk.get("items") or []:
                    lines.append(f"• {item}")
            elif btype == "metric":
                label = blk.get("label") or ""
                val = blk.get("value") or ""
                chg = f" ({blk['change']})" if blk.get("change") else ""
                lines.append(f"{label}: {val}{chg}")
            elif btype == "quote":
                attr = f" — {blk['attribution']}" if blk.get("attribution") else ""
                lines.append(f'"{blk.get("text") or ""}"{attr}')
            elif btype == "callout":
                t = f"{blk['title']}: " if blk.get("title") else ""
                lines.append(f"{t}{blk.get('text') or ''}")
            elif btype == "timeline":
                for item in blk.get("items") or []:
                    if isinstance(item, dict):
                        lines.append(f"• {item.get('label', '')} {item.get('title', '')} {item.get('text', '')}".strip())

        if lines:
            tb = slide.shapes.add_textbox(
                Inches(0.8), Inches(y_cursor), Inches(11.7), Inches(max(1.0, 6.8 - y_cursor))
            )
            tf = tb.text_frame
            tf.word_wrap = True
            for i, line in enumerate(lines):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.text = line
                p.font.size = Pt(16)
                p.font.color.rgb = body_color
                p.space_after = Pt(8)

        if s.get("notes"):
            notes_slide = slide.notes_slide
            notes_slide.notes_text_frame.text = str(s["notes"])

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
