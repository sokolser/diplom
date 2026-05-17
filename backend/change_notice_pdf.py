from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Sequence

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas



class PdfFontSet:
    def __init__(self, regular: str, bold: str):
        self.regular = regular
        self.bold = bold


@dataclass
class TextLine:
    text: str
    font_name: str
    font_size: float
    line_height: float
    indent: float = 0
    align: str = "left"  # left | center | right
    underline: bool = False


@dataclass
class PageSegment:
    change_number: str
    lines: list[TextLine]
    is_continuation: bool = False

    @property
    def height(self) -> float:
        # small "Изм." cell + text + spacing. The vertical column must NOT
        # continue through the whole text area; only this small cell is drawn.
        return 8 * mm + sum(line.line_height for line in self.lines) + 4 * mm


@dataclass
class PlannedPage:
    segments: list[PageSegment]


def _first_existing(paths: Sequence[str]) -> str | None:
    for raw_path in paths:
        if not raw_path:
            continue
        path = Path(raw_path).expanduser()
        if path.exists() and path.is_file():
            return str(path)
    return None


def _register_pdf_fonts() -> PdfFontSet:
    """Register a Cyrillic TTF font for ReportLab. Font files are not shipped."""
    regular_from_env = os.getenv("CHANGE_NOTICE_PDF_FONT_REGULAR")
    bold_from_env = os.getenv("CHANGE_NOTICE_PDF_FONT_BOLD")

    regular_candidates = [
        regular_from_env or "",
        "C:/Windows/Fonts/times.ttf",
        "C:/Windows/Fonts/Times.ttf",
        "/Library/Fonts/Times New Roman.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    bold_candidates = [
        bold_from_env or "",
        "C:/Windows/Fonts/timesbd.ttf",
        "C:/Windows/Fonts/Timesbd.ttf",
        "/Library/Fonts/Times New Roman Bold.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman_Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]

    regular_path = _first_existing(regular_candidates)
    bold_path = _first_existing(bold_candidates) or regular_path
    if not regular_path:
        raise RuntimeError(
            "Не найден ыTTF-шрифт для PDF. "
            "Установите DejaVu/Liberation/Times New Roman или задайте "
            "CHANGE_NOTICE_PDF_FONT_REGULAR и CHANGE_NOTICE_PDF_FONT_BOLD."
        )

    regular_name = "ChangeNoticeRegular"
    bold_name = "ChangeNoticeBold"
    if regular_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(regular_name, regular_path))
    if bold_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(bold_name, bold_path))
    return PdfFontSet(regular=regular_name, bold=bold_name)


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    if hasattr(value, "dict"):
        return value.dict()
    raise TypeError(f"Ожидался dict или Pydantic-модель, получено: {type(value)!r}")


def _extract_notice_payload(data: Any) -> dict[str, Any]:
    payload = _as_dict(data)
    # Debug response support: {"result": {"notice_id": ..., "block": [...]}, ...}
    if isinstance(payload.get("result"), Mapping):
        result = dict(payload["result"])
        if "block" in result or "blocks" in result:
            # Preserve outer metadata when result contains only block fields.
            merged = dict(payload)
            merged.update(result)
            return merged
    return payload


def _optional_text(payload: Mapping[str, Any], key: str, default: str = "") -> str:
    value = payload.get(key)
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _clean_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _notice_date(payload: Mapping[str, Any]) -> str:
    return _optional_text(payload, "notice_date") or datetime.now().strftime("%d.%m.%Y")


def _normalize_filename_part(value: Any) -> str:
    text = str(value or "ИИ").strip()
    for ch in '<>:"/\\|?*\n\r\t':
        text = text.replace(ch, "_")
    text = " ".join(text.split())
    return text[:120] or "ИИ"


def _natural_decimal_key(text: str) -> list[Any]:
    chunks = re.split(r"(\d+)", str(text or ""))
    return [int(chunk) if chunk.isdigit() else chunk.lower() for chunk in chunks]


def _doc_type_rank(block: Mapping[str, Any]) -> tuple[int, list[Any], str]:
    doc_type = str(block.get("doc_type") or "").lower()
    decimal = str(block.get("decimal_number") or "")
    if "комплект" in doc_type:
        rank = 0
    elif "специфика" in doc_type:
        rank = 1
    elif "переч" in doc_type or "пэ4" in decimal.lower() or " пэ" in decimal.lower():
        rank = 2
    elif "сбор" in doc_type or decimal.endswith(" СБ"):
        rank = 3
    elif "схема" in doc_type or decimal.endswith(" Э4"):
        rank = 4
    elif "ввод" in doc_type:
        rank = 9
    else:
        rank = 8
    return rank, _natural_decimal_key(decimal), doc_type


def _sorted_blocks(blocks_raw: Any) -> list[dict[str, Any]]:
    if not isinstance(blocks_raw, Sequence) or isinstance(blocks_raw, (str, bytes)):
        return []
    blocks = [_as_dict(block) for block in blocks_raw]
    # Do not render empty start blocks from the frontend.
    blocks = [
        b
        for b in blocks
        if _clean_text(b.get("decimal_number"))
        or _clean_text(b.get("action"))
        or any(_clean_text(n) for n in (b.get("notes") or []))
    ]
    return sorted(blocks, key=_doc_type_rank)


def make_change_notice_pdf_filename(data: Any) -> str:
    payload = _extract_notice_payload(data)
    notice_id = _normalize_filename_part(payload.get("notice_id") or "change-notice")
    blocks = _sorted_blocks(payload.get("block") or payload.get("blocks") or [])
    if blocks:
        decimal = _normalize_filename_part(blocks[0].get("decimal_number") or "")
        if decimal:
            return f"ИИ_{notice_id}_{decimal}.pdf"
    return f"ИИ_{notice_id}.pdf"


def _resolve_journal_entry(payload: Mapping[str, Any], block: Mapping[str, Any]) -> str:
    journal_number = _optional_text(payload, "journal_number")
    journal_entry_number = _optional_text(payload, "journal_entry_number")
    if journal_number and journal_entry_number:
        return f"Журнал № {journal_number}, запись № {journal_entry_number}."
    block_entry = _clean_text(block.get("journal_entry"))
    return block_entry or "Журнал № XX, запись № XX."


def _resolve_change_number(payload: Mapping[str, Any], block: Mapping[str, Any]) -> str:
    return _optional_text(block, "change_number") or _optional_text(payload, "change_number", "1")


# -----------------------------------------------------------------------------
# Text wrapping
# -----------------------------------------------------------------------------

def _string_width(text: str, font_name: str, font_size: float) -> float:
    return pdfmetrics.stringWidth(text, font_name, font_size)


def _wrap_single_line(text: str, width: float, font_name: str, font_size: float) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return [""]
    words = text.split()
    lines: list[str] = []
    current = ""

    def fits(candidate: str) -> bool:
        return _string_width(candidate, font_name, font_size) <= width

    for word in words:
        candidate = word if not current else f"{current} {word}"
        if fits(candidate):
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        if fits(word):
            current = word
        else:
            part = ""
            for ch in word:
                candidate_part = part + ch
                if part and not fits(candidate_part):
                    lines.append(part)
                    part = ch
                else:
                    part = candidate_part
            current = part
    if current:
        lines.append(current)
    return lines or [""]


def _wrap_text(text: Any, width: float, font_name: str, font_size: float) -> list[str]:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    result: list[str] = []
    for part in raw.split("\n"):
        if not part.strip():
            result.append("")
        else:
            result.extend(_wrap_single_line(part, width, font_name, font_size))
    return result or [""]


def _add_wrapped_lines(
    target: list[TextLine],
    text: Any,
    width: float,
    font_name: str,
    font_size: float,
    line_height: float,
    indent: float = 0,
    align: str = "left",
    underline: bool = False,
) -> None:
    for wrapped in _wrap_text(text, max(5, width - indent), font_name, font_size):
        target.append(TextLine(wrapped, font_name, font_size, line_height, indent, align, underline))


def _block_lines(block: Mapping[str, Any], payload: Mapping[str, Any], fonts: PdfFontSet, content_width: float) -> list[TextLine]:
    lines: list[TextLine] = []
    decimal_number = _clean_text(block.get("decimal_number") or "Без обозначения")
    action = _clean_text(block.get("action") or "")
    notes = block.get("notes") or []
    journal_entry = _resolve_journal_entry(payload, block)

    font_size = 8.8
    line_h = 10.2
    title_size = 9.0

    _add_wrapped_lines(lines, decimal_number, content_width, fonts.regular, title_size, 11.0, align="center", underline=True)
    if action:
        _add_wrapped_lines(lines, action, content_width, fonts.regular, font_size, line_h)

    # Keep the domain structure the user expects: Примечания + numbered items.
    lines.append(TextLine("Примечания", fonts.regular, font_size, line_h))
    if notes:
        for note_index, note in enumerate(notes, start=1):
            raw_lines = str(note or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
            while raw_lines and not raw_lines[-1].strip():
                raw_lines.pop()
            if not raw_lines:
                continue
            _add_wrapped_lines(lines, f"{note_index} {raw_lines[0].strip()}", content_width, fonts.regular, font_size, line_h)
            for raw_line in raw_lines[1:]:
                clean = raw_line.rstrip()
                if clean.strip():
                    stripped = clean.strip()
                    # Lines that start with '-' are subitems of a numbered note.
                    # They should look like " - ...", not like a deeply nested
                    # paragraph. Use only the visual width of one regular space.
                    indent = _string_width(" ", fonts.regular, font_size) if stripped.startswith("-") else 5 * mm
                    _add_wrapped_lines(lines, stripped, content_width, fonts.regular, font_size, line_h, indent=indent)
                else:
                    lines.append(TextLine("", fonts.regular, font_size, 3.5))
    else:
        _add_wrapped_lines(lines, "1 Изменения не сформированы.", content_width, fonts.regular, font_size, line_h)

    _add_wrapped_lines(lines, journal_entry, content_width, fonts.regular, font_size, line_h)
    return lines


@dataclass(frozen=True)
class Geometry:
    page_w: float
    page_h: float
    outer_left: float
    outer_right: float
    outer_top: float
    outer_bottom: float
    form_left: float
    form_right: float
    form_top: float
    form_bottom: float
    right_panel_left: float
    content_left: float
    content_right: float
    content_header_top: float
    content_header_bottom: float
    content_body_top: float
    content_body_bottom: float
    change_col_w: float
    content_capacity: float


PAGE_W, PAGE_H = landscape(A4)

REF = {
    "form_left": 56.5,
    "form_right": 827.0,
    "form_top_t": 13.5,
    "form_bottom_t": 580.5,
    "right_x": 628.5,
    "content_header_top_t": 152.5,
    "content_header_bottom_t": 169.5,
    "content_number_bottom_t": 186.5,
    "content_body_bottom_t": 524.0,
    "change_split_x": 98.5,
    "footer_top_t": 524.0,
    "footer_bottom_t": 580.5,
}


def _pdf_y_from_top(y_top: float) -> float:
    return PAGE_H - y_top


def _rect_from_top(c: pdf_canvas.Canvas, x: float, y_top: float, w: float, h: float, lw: float = 0.85) -> None:
    _draw_rect(c, x, PAGE_H - y_top - h, w, h, lw=lw)


def _line_from_top(c: pdf_canvas.Canvas, x1: float, y1_top: float, x2: float, y2_top: float, lw: float = 0.85) -> None:
    _line(c, x1, PAGE_H - y1_top, x2, PAGE_H - y2_top, lw=lw)


def _cell_from_top(
    c: pdf_canvas.Canvas,
    x: float,
    y_top: float,
    w: float,
    h: float,
    text: Any = "",
    font_name: str = "Helvetica",
    font_size: float = 8.0,
    align: str = "center",
    valign: str = "middle",
    border: bool = True,
    padding: float = 1.2 * mm,
    lw: float = 0.85,
    bold_font_name: str | None = None,
) -> None:
    _draw_cell(
        c,
        x,
        PAGE_H - y_top - h,
        w,
        h,
        text=text,
        font_name=font_name,
        font_size=font_size,
        align=align,
        valign=valign,
        border=border,
        padding=padding,
        lw=lw,
        bold_font_name=bold_font_name,
    )


def _page_geometry(page_number: int) -> Geometry:
    if page_number == 1:
        form_left = REF["form_left"]
        form_right = REF["form_right"]
        form_top = PAGE_H - REF["form_top_t"]
        form_bottom = PAGE_H - REF["form_bottom_t"]
        right_panel_left = REF["right_x"]
        content_left = form_left
        content_right = right_panel_left
        content_header_top = PAGE_H - REF["content_header_top_t"]
        content_header_bottom = PAGE_H - REF["content_header_bottom_t"]
        content_body_top = content_header_bottom
        content_body_bottom = PAGE_H - REF["content_body_bottom_t"]
        change_col_w = REF["change_split_x"] - form_left
    else:
        # Form 2 (subsequent sheets) is copied from the ГОСТ 2.503 sample.
        # Top row: Извещение | номер | Обозначение ПИ (ДПИ, ПР) | значение | Лист
        # Second row: Изм. | Содержание изменения | номер листа
        form_left = 56.5
        form_right = 827.0
        form_top = PAGE_H - 13.5
        form_bottom = PAGE_H - 580.5
        right_panel_left = form_right
        content_left = form_left
        content_right = form_right
        content_header_top = PAGE_H - 30.5
        content_header_bottom = PAGE_H - 56.0
        content_body_top = content_header_bottom
        content_body_bottom = PAGE_H - 580.5
        change_col_w = 42.0

    return Geometry(
        page_w=PAGE_W,
        page_h=PAGE_H,
        outer_left=0.0,
        outer_right=PAGE_W,
        outer_top=PAGE_H,
        outer_bottom=0.0,
        form_left=form_left,
        form_right=form_right,
        form_top=form_top,
        form_bottom=form_bottom,
        right_panel_left=right_panel_left,
        content_left=content_left,
        content_right=content_right,
        content_header_top=content_header_top,
        content_header_bottom=content_header_bottom,
        content_body_top=content_body_top,
        content_body_bottom=content_body_bottom,
        change_col_w=change_col_w,
        content_capacity=content_body_top - content_body_bottom,
    )


def _split_lines_to_fit(lines: list[TextLine], capacity: float) -> tuple[list[TextLine], list[TextLine]]:
    used = 0.0
    chunk: list[TextLine] = []
    for idx, line in enumerate(lines):
        if chunk and used + line.line_height > capacity:
            return chunk, lines[idx:]
        if not chunk and line.line_height > capacity:
            return [line], lines[idx + 1 :]
        chunk.append(line)
        used += line.line_height
    return chunk, []


def _plan_pages(payload: Mapping[str, Any], blocks: list[dict[str, Any]], fonts: PdfFontSet) -> list[PlannedPage]:
    pages: list[PlannedPage] = [PlannedPage(segments=[])]
    page_number = 1
    remaining = _page_geometry(page_number).content_capacity

    for block in blocks:
        change_number = _resolve_change_number(payload, block)
        while True:
            geom = _page_geometry(page_number)
            content_width = geom.content_right - geom.content_left - 8 * mm
            lines = _block_lines(block, payload, fonts, content_width)
            total_height = 10 * mm + sum(line.line_height for line in lines) + 4 * mm
            if total_height > remaining and remaining < geom.content_capacity * 0.30:
                pages.append(PlannedPage(segments=[]))
                page_number += 1
                remaining = _page_geometry(page_number).content_capacity
                continue
            break

        pending = lines
        first_piece = True
        while pending:
            available_for_lines = max(0, remaining - 15 * mm)
            chunk, rest = _split_lines_to_fit(pending, available_for_lines)
            if not chunk:
                pages.append(PlannedPage(segments=[]))
                page_number += 1
                remaining = _page_geometry(page_number).content_capacity
                new_width = _page_geometry(page_number).content_right - _page_geometry(page_number).content_left - 8 * mm
                pending = _block_lines(block, payload, fonts, new_width)
                first_piece = False
                continue

            segment = PageSegment(change_number=change_number, lines=chunk, is_continuation=not first_piece)
            pages[-1].segments.append(segment)
            remaining -= segment.height
            pending = rest
            first_piece = False

            if pending:
                pages.append(PlannedPage(segments=[]))
                page_number += 1
                remaining = _page_geometry(page_number).content_capacity

    return pages or [PlannedPage(segments=[])]



def _draw_rect(c: pdf_canvas.Canvas, x: float, y: float, w: float, h: float, lw: float = 0.8) -> None:
    c.saveState()
    c.setLineWidth(lw)
    c.rect(x, y, w, h, stroke=1, fill=0)
    c.restoreState()


def _line(c: pdf_canvas.Canvas, x1: float, y1: float, x2: float, y2: float, lw: float = 0.8) -> None:
    c.saveState()
    c.setLineWidth(lw)
    c.line(x1, y1, x2, y2)
    c.restoreState()


def _draw_cell(
    c: pdf_canvas.Canvas,
    x: float,
    y: float,
    w: float,
    h: float,
    text: Any = "",
    font_name: str = "Helvetica",
    font_size: float = 8.0,
    align: str = "center",
    valign: str = "middle",
    border: bool = True,
    padding: float = 1.4 * mm,
    lw: float = 0.8,
    bold_font_name: str | None = None,
) -> None:
    if border:
        _draw_rect(c, x, y, w, h, lw=lw)
    raw = _clean_text(text)
    if not raw:
        return
    used_font = bold_font_name or font_name
    lines = _wrap_text(raw, max(4, w - 2 * padding), used_font, font_size)
    line_h = font_size + 2
    total_h = len(lines) * line_h
    if valign == "top":
        text_y = y + h - padding - font_size
    elif valign == "bottom":
        text_y = y + padding + (len(lines) - 1) * line_h
    else:
        text_y = y + (h + total_h) / 2 - font_size
    c.setFont(used_font, font_size)
    for line in lines:
        if align == "left":
            c.drawString(x + padding, text_y, line)
        elif align == "right":
            c.drawRightString(x + w - padding, text_y, line)
        else:
            c.drawCentredString(x + w / 2, text_y, line)
        text_y -= line_h


def _draw_line_item(c: pdf_canvas.Canvas, line: TextLine, x: float, y: float, w: float) -> None:
    c.setFont(line.font_name, line.font_size)
    baseline_y = y - line.font_size
    if line.align == "center":
        c.drawCentredString(x + w / 2, baseline_y, line.text)
        text_width = _string_width(line.text, line.font_name, line.font_size)
        underline_x1 = x + w / 2 - text_width / 2
        underline_x2 = x + w / 2 + text_width / 2
    elif line.align == "right":
        c.drawRightString(x + w, baseline_y, line.text)
        text_width = _string_width(line.text, line.font_name, line.font_size)
        underline_x1 = x + w - text_width
        underline_x2 = x + w
    else:
        tx = x + line.indent
        c.drawString(tx, baseline_y, line.text)
        text_width = _string_width(line.text, line.font_name, line.font_size)
        underline_x1 = tx
        underline_x2 = tx + text_width
    if line.underline and line.text:
        _line(c, underline_x1, baseline_y - 1.1, underline_x2, baseline_y - 1.1, lw=0.45)


def _draw_outer_frame(c: pdf_canvas.Canvas, page_number: int) -> None:
    # The first page frame is drawn exactly by _draw_header_first/_draw_signature_footer.
    # Drawing an additional page rectangle is what previously created the visible
    # "frame inside frame" defect.
    if page_number == 1:
        return
    g = _page_geometry(page_number)
    _draw_rect(c, g.form_left, g.form_bottom, g.form_right - g.form_left, g.form_top - g.form_bottom, lw=1.0)




def _draw_header_first(c: pdf_canvas.Canvas, payload: Mapping[str, Any], fonts: PdfFontSet, page_number: int, total_pages: int) -> None:
    notice_id = _optional_text(payload, "notice_id", "")
    designation = _optional_text(payload, "designation", "См. ниже")
    reason = _optional_text(payload, "change_reason", "")
    code = _optional_text(payload, "change_code", _optional_text(payload, "code", ""))
    department = _optional_text(payload, "department", "")
    org = _optional_text(payload, "organization", 'АО "ЭЙРБУРГ"')

    _rect_from_top(c, 56.5, 13.5, 770.5, 567.0, lw=1.05)


    _cell_from_top(c, 56.5, 13.5, 76.0, 42.5, org, fonts.regular, 7.0, lw=0.95)
    for x1, x2, label, value, fs in [
        (132.5, 251.5, "Извещение", notice_id, 9.0),
        (251.5, 427.5, "Обозначение", designation, 8.0),
        (427.5, 694.0, "Причина", reason, 7.6),
        (694.0, 736.5, "Код", code, 8.6),
        (736.5, 781.5, "Лист", str(page_number), 8.6),
        (781.5, 827.0, "Листов", str(total_pages), 8.6),
    ]:
        _cell_from_top(c, x1, 13.5, x2 - x1, 14.0, label, fonts.bold, 7.3, lw=0.95)
        _cell_from_top(c, x1, 27.5, x2 - x1, 28.5, value, fonts.regular, fs, lw=0.95)


    _cell_from_top(c, 56.5, 56.0, 76.0, 28.5, f"Отдел\n{department}", fonts.regular, 7.0, lw=0.95)
    _cell_from_top(c, 132.5, 56.0, 62.5, 28.5, "Дата\nвыпуска", fonts.regular, 6.7, lw=0.95)
    _cell_from_top(c, 195.0, 56.0, 56.5, 28.5, _notice_date(payload), fonts.regular, 7.0, lw=0.95)
    _cell_from_top(c, 251.5, 56.0, 42.5, 28.5, "", fonts.regular, 7.0, lw=0.95)
    _cell_from_top(c, 294.0, 56.0, 42.5, 28.5, "Срок\nизм.", fonts.regular, 6.7, lw=0.95)
    _cell_from_top(c, 336.5, 56.0, 57.0, 28.5, _optional_text(payload, 'change_term', ''), fonts.regular, 7.0, lw=0.95)
    _cell_from_top(c, 393.5, 56.0, 59.5, 28.5, "", fonts.regular, 7.0, lw=0.95)
    _cell_from_top(c, 453.0, 56.0, 76.5, 28.5, "Обозначение\nПИ (ДПИ, ПР)", fonts.regular, 6.2, lw=0.95)
    _cell_from_top(c, 529.5, 56.0, 99.0, 28.5, _optional_text(payload, 'pi_designation', ''), fonts.regular, 6.8, lw=0.95)
    _cell_from_top(c, 628.5, 56.0, 108.0, 28.5, "Срок действия ПИ", fonts.regular, 6.8, lw=0.95)
    _cell_from_top(c, 736.5, 56.0, 90.5, 28.5, _optional_text(payload, 'pi_validity', ''), fonts.regular, 7.0, lw=0.95)

    _cell_from_top(c, 56.5, 84.5, 76.0, 68.0, "Указание\nо заделе", fonts.regular, 7.2, lw=0.95)
    _cell_from_top(c, 132.5, 84.5, 496.0, 34.0, _optional_text(payload, "backlog_instruction", ""), fonts.regular, 7.2, lw=0.95)
    _cell_from_top(c, 132.5, 118.5, 496.0, 34.0, "", fonts.regular, 7.0, lw=0.95)
    _cell_from_top(c, 628.5, 84.5, 198.5, 17.0, "Указание о внедрении", fonts.regular, 7.2, lw=0.95)
    _cell_from_top(c, 628.5, 101.5, 198.5, 39.5, _optional_text(payload, "implementation_instruction", ""), fonts.regular, 7.2, lw=0.95)
    _cell_from_top(c, 628.5, 141.0, 198.5, 34.0, "", fonts.regular, 7.0, lw=0.95)


def _draw_right_panel_first(c: pdf_canvas.Canvas, payload: Mapping[str, Any], fonts: PdfFontSet) -> None:
    x = 628.5
    w = 198.5
    _cell_from_top(c, x, 175.0, w, 20.0, "Применяемость", fonts.regular, 7.2, lw=0.95)
    _cell_from_top(c, x, 195.0, w, 139.0, _optional_text(payload, "applicability", ""), fonts.regular, 7.0, lw=0.95)
    _cell_from_top(c, x, 334.0, w, 19.5, "Разослать", fonts.regular, 7.2, lw=0.95)
    _cell_from_top(c, x, 353.5, w, 110.5, _optional_text(payload, "mailing_list", _optional_text(payload, "send_to", "")), fonts.regular, 7.0, lw=0.95)
    _cell_from_top(c, x, 464.0, w, 17.0, "Приложение", fonts.regular, 7.2, lw=0.95)
    _cell_from_top(c, x, 481.0, w, 99.5, _optional_text(payload, "attachment", ""), fonts.regular, 7.0, lw=0.95)


def _draw_content_header(c: pdf_canvas.Canvas, fonts: PdfFontSet, page_number: int) -> None:
    if page_number == 1:
        _cell_from_top(c, 56.5, 152.5, 42.0, 17.0, "Изм.", fonts.regular, 7.5, lw=0.95)
        _cell_from_top(c, 98.5, 152.5, 530.0, 17.0, "Содержание изменения", fonts.regular, 7.5, lw=0.95)
    else:
        _cell_from_top(c, 56.5, 30.5, 42.0, 25.5, "Изм.", fonts.regular, 7.7, lw=0.95)
        _cell_from_top(c, 98.5, 30.5, 677.5, 25.5, "Содержание изменения", fonts.regular, 7.7, lw=0.95)
        _cell_from_top(c, 776.0, 30.5, 51.0, 25.5, str(page_number), fonts.regular, 8.0, lw=0.95)


def _draw_content_body_base(c: pdf_canvas.Canvas, page_number: int) -> None:
    if page_number == 1:
        _rect_from_top(c, 56.5, 169.5, 572.0, 354.5, lw=0.95)
        _line_from_top(c, 98.5, 169.5, 98.5, 186.5, lw=0.95)
        _line_from_top(c, 56.5, 186.5, 98.5, 186.5, lw=0.95)
    else:
        g = _page_geometry(page_number)
        _draw_rect(c, g.content_left, g.content_body_bottom, g.content_right - g.content_left, g.content_body_top - g.content_body_bottom, lw=0.95)


def _draw_signature_footer(c: pdf_canvas.Canvas, payload: Mapping[str, Any], fonts: PdfFontSet) -> None:
    _rect_from_top(c, 0.0, 368.0, 56.5, 212.5, lw=0.95)
    for x in (13.5, 27.5, 42.0):
        _line_from_top(c, x, 368.0, x, 580.5, lw=0.85)
    for y in (410.5, 453.0, 524.0):
        _line_from_top(c, 0.0, y, 56.5, y, lw=0.85)
    form_code = _optional_text(payload, "form_code", "D000625307")
    if form_code:
        c.setFont(fonts.regular, 5.2)
        c.drawString(57.5, PAGE_H - 589.0, form_code)

    y1, y2, y3, y4, y5 = 524.0, 538.0, 552.0, 566.5, 580.5
    xs = [56.5, 149.5, 243.0, 336.5, 430.0, 524.0, 628.5]
    labels = [
        ("Составил", _optional_text(payload, "developer", "")),
        ("Проверил", _optional_text(payload, "checker", "")),
        ("Т.контроль", _optional_text(payload, "technical_control", _optional_text(payload, "tech_control", ""))),
        ("Н.контроль", _optional_text(payload, "norm_control", "")),
        ("Утвердил", _optional_text(payload, "approver", "")),
        ("Пред. заказ.", _optional_text(payload, "customer_representative", "")),
    ]
    for i, (label, value) in enumerate(labels):
        _cell_from_top(c, xs[i], y1, xs[i+1]-xs[i], y2-y1, label, fonts.regular, 7.0, lw=0.85)
        _cell_from_top(c, xs[i], y2, xs[i+1]-xs[i], y3-y2, value, fonts.regular, 7.0, lw=0.85)
        _cell_from_top(c, xs[i], y3, xs[i+1]-xs[i], y4-y3, "", fonts.regular, 6.0, lw=0.85)


    for x in (121.0, 215.0, 308.5, 402.0, 495.5):
        _line_from_top(c, x, y2, x, y4, lw=0.85)

    # Bottom service row.
    _cell_from_top(c, 56.5, y4, 118.5, y5-y4, "Изменения внес", fonts.regular, 6.7, lw=0.85)
    _cell_from_top(c, 175.0, y4, 133.5, y5-y4, "", fonts.regular, 6.0, lw=0.85)
    _cell_from_top(c, 308.5, y4, 255.0, y5-y4, "Контр. копию исправил", fonts.regular, 6.7, lw=0.85)
    _cell_from_top(c, 563.5, y4, 65.0, y5-y4, "", fonts.regular, 6.0, lw=0.85)




def _draw_header_later(c: pdf_canvas.Canvas, payload: Mapping[str, Any], fonts: PdfFontSet, page_number: int) -> None:
    notice_id = _optional_text(payload, "notice_id", "")
    pi_designation = _optional_text(payload, "pi_designation", "")


    _cell_from_top(c, 56.5, 13.5, 141.0, 17.0, "Извещение", fonts.regular, 7.4, lw=0.95)
    _cell_from_top(c, 197.5, 13.5, 142.0, 17.0, notice_id, fonts.regular, 7.8, lw=0.95)
    _cell_from_top(c, 339.5, 13.5, 204.0, 17.0, "Обозначение ПИ (ДПИ, ПР)", fonts.regular, 7.2, lw=0.95)
    _cell_from_top(c, 543.5, 13.5, 232.5, 17.0, pi_designation, fonts.regular, 7.6, lw=0.95)
    _cell_from_top(c, 776.0, 13.5, 51.0, 17.0, "Лист", fonts.regular, 7.2, lw=0.95)




def _draw_segment(c: pdf_canvas.Canvas, segment: PageSegment, y_top: float, page_number: int) -> float:
    g = _page_geometry(page_number)
    row_h = max(segment.height, 10 * mm)
    row_bottom = y_top - row_h
    number_h = 17.0 if page_number == 1 else 16.0


    is_first_segment_on_page = abs(y_top - g.content_body_top) < 2
    if not is_first_segment_on_page:
        _line(c, g.content_left, y_top, g.content_right, y_top, lw=0.85)

    if page_number == 1:
        if is_first_segment_on_page:
            cell_top = REF["content_header_bottom_t"]
        else:
            cell_top = PAGE_H - y_top
        cell_h = min(number_h, max(6, PAGE_H - row_bottom - cell_top))
        _cell_from_top(c, 56.5, cell_top, 42.0, cell_h, segment.change_number, "ChangeNoticeRegular", 8.0, lw=0.85)
        text_x = g.content_left + 10.0
        text_w = g.content_right - g.content_left - 18.0
        y = y_top - number_h - 7.0
    else:
        _draw_rect(c, g.content_left, y_top - number_h, g.change_col_w, number_h, lw=0.85)
        c.setFont("ChangeNoticeRegular", 8.0)
        c.drawCentredString(g.content_left + g.change_col_w / 2, y_top - number_h / 2 - 2.6, segment.change_number)
        text_x = g.content_left + 8.0
        text_w = g.content_right - g.content_left - 16.0
        y = y_top - number_h - 7.0

    for line in segment.lines:
        if y - line.line_height < row_bottom + 3.0:
            break
        _draw_line_item(c, line, text_x, y, text_w)
        y -= line.line_height
    return row_h


def _draw_page(c: pdf_canvas.Canvas, payload: Mapping[str, Any], fonts: PdfFontSet, page: PlannedPage, page_number: int, total_pages: int) -> None:
    _draw_outer_frame(c, page_number)
    if page_number == 1:
        _draw_header_first(c, payload, fonts, page_number, total_pages)
        _draw_right_panel_first(c, payload, fonts)
        _draw_signature_footer(c, payload, fonts)
    else:
        _draw_header_later(c, payload, fonts, page_number)

    _draw_content_header(c, fonts, page_number)
    _draw_content_body_base(c, page_number)

    g = _page_geometry(page_number)
    y = g.content_body_top
    for segment in page.segments:
        y -= _draw_segment(c, segment, y, page_number)




def build_change_notice_pdf(data: Any) -> bytes:
    payload = _extract_notice_payload(data)
    blocks = _sorted_blocks(payload.get("block") or payload.get("blocks") or [])
    fonts = _register_pdf_fonts()

    pages = _plan_pages(payload, blocks, fonts)
    if not pages:
        pages = [PlannedPage(segments=[])]
    total_pages = len(pages)

    buffer = BytesIO()
    c = pdf_canvas.Canvas(buffer, pagesize=landscape(A4))
    c.setTitle(f"Извещение об изменении {_optional_text(payload, 'notice_id', 'Без номера')}")
    c.setAuthor("KD Analyzer")

    for idx, page in enumerate(pages, start=1):
        _draw_page(c, payload, fonts, page, idx, total_pages)
        if idx < total_pages:
            c.showPage()

    c.save()
    return buffer.getvalue()
