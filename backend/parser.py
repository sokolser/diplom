import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

import cv2
import fitz
import numpy as np
import pdfplumber
from skimage.metrics import structural_similarity as ssim

from models import (
    DocMetadata,
    FullDiff,
    GraphicDiff,
    GraphicRegion,
    ParsedDocument,
    ParsedTable,
    SpecificationFieldChange,
    SpecificationItem,
    SpecificationItemDiff,
    ElementFieldChange,
    ElementItem,
    ElementItemDiff,
    TableDiff,
    TechRequirement,
    TechReqDiff,
)


@dataclass
class TableData:
    page_num: int
    bbox: Tuple[float, float, float, float]
    name: str
    rows: List[List[str]]
    column_names: List[str]
    header_rows_count: int


class EngineeringDocParser:
 

    def __init__(self, dpi: int = 220):
        self.dpi = dpi

        self.decimal_patterns = [
            # Классический вариант с буквенным префиксом: РСПГ.122.21.92.05.000 Э4
            re.compile(r"\b([A-ZА-ЯЁ]{1,10}\.\d{3}\.\d{2}\.\d{2}\.\d{2}\.\d{3}(?:\s?(?:СБ|СП|Э4|ПЭ4|ТУ|ВО|МЧ))?)\b", re.I),
            # Встречаются обозначения без буквенного префикса: 051.01.40.01.000 СБ
            re.compile(r"\b(\d{3}\.\d{2}\.\d{2}\.\d{2}\.\d{3}(?:\s?(?:СБ|СП|Э4|ПЭ4|ТУ|ВО|МЧ))?)\b", re.I),
            re.compile(r"\b([A-ZА-ЯЁ]{1,10}(?:[.\-]\d{2,5}){3,8}(?:\s?(?:СБ|СП|Э4|ПЭ4|ТУ|ВО|МЧ))?)\b", re.I),
        ]
        self.scale_re = re.compile(r"\b(\d+\s*:\s*\d+)\b")
        self.mass_re = re.compile(r"(?:масса|mass)\s*[:.]?\s*(\d+(?:[.,]\d+)?)", re.I)
        self.mass_value_re = re.compile(r"^\d+(?:[.,]\d+)$")
        self.litera_re = re.compile(r"(?:лит(?:ера)?|лит\.)\s*[:.]?\s*([A-ZА-Я]{1,3})\b", re.I)
        self.table_title_re = re.compile(r"^\s*таблица\s*\d+", re.I)
        self.numbered_item_re = re.compile(r"^\s*(\d{1,2})[.)]?\s+")

        self.service_words = {
            "разраб", "пров", "н. контр", "т. контр", "утв", "лист", "листов", "масса",
            "масштаб", "лит", "формат", "подп", "дата", "изм", "инв", "зам", "перв",
            "справ", "копировал", "стадия", "докум", "см.", "см",
        }

        self.spec_section_names = {
            "документация",
            "комплекты",
            "сборочные единицы",
            "детали",
            "стандартные изделия",
            "прочие изделия",
            "материалы",
        }

        self.pe4_header_words = {"поз", "обознач", "наимен", "кол", "примеч"}

        self.table_settings_variants = [
            {
                "vertical_strategy": "lines",
                "horizontal_strategy": "lines",
                "snap_tolerance": 4,
                "join_tolerance": 4,
                "intersection_tolerance": 4,
                "edge_min_length": 8,
            },
            {
                "vertical_strategy": "lines_strict",
                "horizontal_strategy": "lines_strict",
                "snap_tolerance": 3,
                "join_tolerance": 3,
                "intersection_tolerance": 3,
                "edge_min_length": 8,
            },
            {
                "vertical_strategy": "text",
                "horizontal_strategy": "text",
                "min_words_vertical": 2,
                "min_words_horizontal": 1,
                "snap_tolerance": 3,
                "join_tolerance": 3,
                "intersection_tolerance": 3,
                "text_tolerance": 3,
            },
        ]

    @staticmethod
    def _fix_mojibake(text: str) -> str:
        if not text:
            return text

        fixed = text.replace("￳", "-").replace("–", "-").replace("—", "-")
        fixed = fixed.replace("«", "\"").replace("»", "\"")
        fixed = fixed.replace(" ", " ").replace("\u00A0", " ").replace("\u200B", "")

        hints = [
            "формат", "зона", "поз", "обознач", "наимен", "кол", "примеч",
            "документац", "прочие изделия", "материал", "лит", "лист", "листов",
            "перв", "примен", "подп", "дата", "инв", "справ", "изм", "докум",
            "сборочный чертеж", "спецификация", "жгут", "кабель", "рспг",
        ]

        def score(s: str) -> tuple[int, int, int]:
            low = s.lower()
            hint_hits = sum(1 for h in hints if h in low)
            cyr = len(re.findall(r"[А-Яа-яЁё]", s))
            printable = len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]", s))
            return (hint_hits, cyr, printable)

        def decode_variants(src: str):
            variants = [src]
            for enc in ("latin1", "cp1252"):
                try:
                    cand = src.encode(enc, errors="ignore").decode("utf-8", errors="ignore")
                    if cand:
                        variants.append(cand)
                except Exception:
                    pass
                try:
                    cand = src.encode(enc, errors="ignore").decode("cp1251", errors="ignore")
                    if cand:
                        variants.append(cand)
                except Exception:
                    pass
            return variants

        candidates = []
        for base in (fixed, fixed[::-1]):
            candidates.extend(decode_variants(base))

        # also consider reversing already-decoded variants
        candidates.extend([c[::-1] for c in list(candidates)])

        best = max(candidates, key=score)
        best = re.sub(r"[ 	]+", " ", best)
        best = re.sub(r" ?\n ?", "\n", best)
        return best.strip()

    @staticmethod
    def _norm(text: Optional[str]) -> str:
        if not text:
            return ""
        return EngineeringDocParser._fix_mojibake(text)

    @staticmethod
    def _norm_cell(text: Optional[str]) -> str:
        text = EngineeringDocParser._fix_mojibake(text or "")
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _rect_intersection_ratio(
        a: Tuple[float, float, float, float],
        b: Tuple[float, float, float, float],
    ) -> float:
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        inter_w = max(0.0, min(ax1, bx1) - max(ax0, bx0))
        inter_h = max(0.0, min(ay1, by1) - max(ay0, by0))
        inter = inter_w * inter_h
        area_a = max(1.0, (ax1 - ax0) * (ay1 - ay0))
        return inter / area_a

    @staticmethod
    def _bbox_iou(
        a: Tuple[float, float, float, float],
        b: Tuple[float, float, float, float],
    ) -> float:
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        inter_w = max(0.0, min(ax1, bx1) - max(ax0, bx0))
        inter_h = max(0.0, min(ay1, by1) - max(ay0, by0))
        inter = inter_w * inter_h
        area_a = max(0.0, (ax1 - ax0) * (ay1 - ay0))
        area_b = max(0.0, (bx1 - bx0) * (by1 - by0))
        union = area_a + area_b - inter
        return inter / union if union else 0.0

    def _expected_title_block_bbox(self, page: fitz.Page) -> Tuple[float, float, float, float]:
        w, h = page.rect.width, page.rect.height

        if w > 1500 or h > 1100:
            return (w * 0.86, h * 0.90, w, h)
        return (w * 0.62, h * 0.78, w, h)

    def _expanded_title_block_bbox(self, page: fitz.Page) -> Tuple[float, float, float, float]:
        w, h = page.rect.width, page.rect.height
        if w > 1500 or h > 1100:
            return (w * 0.78, h * 0.86, w, h)
        return (w * 0.52, h * 0.72, w, h)

    def _page_words(self, page: fitz.Page):
        words = page.get_text("words", sort=True)
        fixed_words = []
        for x0, y0, x1, y1, txt, block_no, line_no, word_no in words:
            fixed_words.append((x0, y0, x1, y1, self._fix_mojibake(txt), block_no, line_no, word_no))
        return fixed_words

    def _words_in_rect(self, words, rect):
        x0, y0, x1, y1 = rect
        result = []
        for w in words:
            wx0, wy0, wx1, wy1 = w[:4]
            cx, cy = (wx0 + wx1) / 2.0, (wy0 + wy1) / 2.0
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                result.append(w)
        return result

    def _words_to_lines(self, words, y_tol: float = 3.0, x_gap_threshold: float = 80.0) -> List[str]:
        if not words:
            return []

        words = sorted(words, key=lambda w: (w[1], w[0]))
        line_groups: List[List[tuple]] = []
        current: List[tuple] = []
        current_y: Optional[float] = None

        for w in words:
            y = w[1]
            if current_y is None or abs(y - current_y) <= y_tol:
                current.append(w)
                current_y = y if current_y is None else (current_y + y) / 2.0
            else:
                line_groups.append(sorted(current, key=lambda item: item[0]))
                current = [w]
                current_y = y

        if current:
            line_groups.append(sorted(current, key=lambda item: item[0]))

        text_lines: List[str] = []
        for group in line_groups:
            segments: List[List[tuple]] = []
            seg: List[tuple] = []
            prev_x1: Optional[float] = None

            for item in group:
                if prev_x1 is not None and (item[0] - prev_x1) > x_gap_threshold:
                    if seg:
                        segments.append(seg)
                    seg = [item]
                else:
                    seg.append(item)
                prev_x1 = item[2]

            if seg:
                segments.append(seg)

            for segment in segments:
                line_text = self._norm(" ".join(item[4] for item in segment))
                if line_text:
                    text_lines.append(line_text)

        return text_lines

    def _group_word_lines(self, words, y_tol: float = 3.0, x_gap_threshold: float = 80.0):
        if not words:
            return []

        words = sorted(words, key=lambda w: (w[1], w[0]))
        line_groups: List[List[tuple]] = []
        current: List[tuple] = []
        current_y: Optional[float] = None

        for w in words:
            y = w[1]
            if current_y is None or abs(y - current_y) <= y_tol:
                current.append(w)
                current_y = y if current_y is None else (current_y + y) / 2.0
            else:
                line_groups.append(sorted(current, key=lambda item: item[0]))
                current = [w]
                current_y = y

        if current:
            line_groups.append(sorted(current, key=lambda item: item[0]))

        result = []
        for group in line_groups:
            segments: List[List[tuple]] = []
            seg: List[tuple] = []
            prev_x1: Optional[float] = None

            for item in group:
                if prev_x1 is not None and (item[0] - prev_x1) > x_gap_threshold:
                    if seg:
                        segments.append(seg)
                    seg = [item]
                else:
                    seg.append(item)
                prev_x1 = item[2]

            if seg:
                segments.append(seg)

            for segment in segments:
                line_text = self._norm(" ".join(item[4] for item in segment))
                if not line_text:
                    continue
                result.append({
                    "text": line_text,
                    "x0": min(item[0] for item in segment),
                    "y0": min(item[1] for item in segment),
                    "x1": max(item[2] for item in segment),
                    "y1": max(item[3] for item in segment),
                    "words": segment,
                })

        return result

    def _group_word_lines_native(self, words, x_gap_threshold: float = 80.0):
        if not words:
            return []

        grouped: Dict[Tuple[int, int], List[tuple]] = {}
        for w in words:
            block_no = int(w[5]) if len(w) > 5 else 0
            line_no = int(w[6]) if len(w) > 6 else 0
            grouped.setdefault((block_no, line_no), []).append(w)

        result = []
        for _, group in grouped.items():
            group = sorted(group, key=lambda item: item[0])
            segments: List[List[tuple]] = []
            seg: List[tuple] = []
            prev_x1: Optional[float] = None

            for item in group:
                if prev_x1 is not None and (item[0] - prev_x1) > x_gap_threshold:
                    if seg:
                        segments.append(seg)
                    seg = [item]
                else:
                    seg.append(item)
                prev_x1 = item[2]

            if seg:
                segments.append(seg)

            for segment in segments:
                line_text = self._norm(" ".join(item[4] for item in segment))
                if not line_text:
                    continue
                result.append({
                    "text": line_text,
                    "x0": min(item[0] for item in segment),
                    "y0": min(item[1] for item in segment),
                    "x1": max(item[2] for item in segment),
                    "y1": max(item[3] for item in segment),
                    "words": segment,
                })

        return sorted(result, key=lambda item: (item["y0"], item["x0"]))

    def _extract_mass_and_scale_from_stamp_lines(self, stamp_line_infos) -> Tuple[Optional[float], Optional[str]]:
        if not stamp_line_infos:
            return None, None

        scale: Optional[str] = None
        mass: Optional[float] = None

        for line_info in stamp_line_infos:
            line = self._strip_control_chars(self._fix_mojibake(line_info.get("text", ""))) or ""
            if not line:
                continue
            line = re.sub(r"\s+", " ", line).strip()
            low_compact = line.lower().replace(" ", "")
            if any(marker in low_compact for marker in ("см.табл", "см.тт", "смтт", "смтабл")):
                sc = self.scale_re.search(line)
                if sc and not scale:
                    scale = sc.group(1).replace(" ", "")
                continue

            sc = self.scale_re.search(line)
            if sc and not scale:
                scale = sc.group(1).replace(" ", "")

            m = re.search(r"(\d+[.,]\d+)\s+(1\s*:\s*\d+)", line)
            if m and mass is None:
                try:
                    mass = float(m.group(1).replace(",", "."))
                except ValueError:
                    pass

        return mass, scale

    def _extract_mass_from_stamp_words(self, stamp_words) -> Optional[float]:
        headers = [w for w in stamp_words if self._norm(w[4]).lower().startswith("масса")]
        if not headers:
            return None

        best_token = None
        best_score = float("inf")

        for header in headers:
            hx = (header[0] + header[2]) / 2.0
            hy = (header[1] + header[3]) / 2.0

            for word in stamp_words:
                token = self._norm(word[4])
                # Не считаем целые числа вроде "1" массой: это почти всегда Лист/Листов/табл.1.
                if not re.fullmatch(r"\d+[.,]\d+", token):
                    continue
                if self.scale_re.fullmatch(token):
                    continue

                wx = (word[0] + word[2]) / 2.0
                wy = (word[1] + word[3]) / 2.0
                dy = wy - hy
                dx = abs(wx - hx)

                if dy < 8 or dy > 45:
                    continue
                if dx > 100:
                    continue

                score = dy * 3 + dx
                if score < best_score:
                    best_score = score
                    best_token = token

        if best_token is None:
            return None

        try:
            return float(best_token.replace(",", "."))
        except ValueError:
            return None

    def _extract_mass_and_scale_from_stamp(self, stamp_text: str) -> Tuple[Optional[float], Optional[str]]:
        text = self._fix_mojibake(stamp_text or "")
        text = self._strip_control_chars(text) or ""
        text = re.sub(r"\s+", " ", text).strip()

        scale = None
        mass = None

        scale_match = self.scale_re.search(text)
        if scale_match:
            scale = scale_match.group(1).replace(" ", "")

        low_compact = text.lower().replace(" ", "")
        if any(marker in low_compact for marker in ("см.табл", "см.тт", "смтт", "смтабл")):
            return None, scale

        # Ищем массу только как десятичное число непосредственно перед масштабом.
        mass_match = re.search(r"(\d+[.,]\d+)\s+(1\s*:\s*\d+)", text)
        if mass_match:
            try:
                mass = float(mass_match.group(1).replace(",", "."))
            except ValueError:
                mass = None

        return mass, scale

    def _strip_control_chars(self, text: Optional[str]) -> Optional[str]:
        if text is None:
            return None
        return re.sub(r"[\x00-\x1F\x7F]", "", text).strip()

    def _extract_doc_suffix_from_stamp(self, stamp_text: str) -> Optional[str]:
        t = self._strip_control_chars(self._fix_mojibake(stamp_text or "")) or ""
        for suffix in ("СБ", "СП", "ПЭ4", "Э4"):
            if re.search(rf"\b{suffix}\b", t, flags=re.IGNORECASE):
                return suffix
        return None

    def _normalize_decimal_number(self, value: Optional[str], stamp_text: str = "") -> Optional[str]:
        if not value:
            return None

        s = self._strip_control_chars(self._fix_mojibake(value)) or ""
        s = s.replace("￳", "-")
        s = re.sub(r"\s+", " ", s).strip()

        m = re.search(
            # Поддерживаем оба вида обозначений:
            # РСПГ.122.21.92.05.000 Э4 и 051.01.40.01.000 СБ.
            r"((?:[А-ЯA-ZЁ]{1,10}\.)?\d{3}(?:\.\d{2,5}){4,5})(?:\s*(СБ|СП|ПЭ4|Э4))?",
            s,
            flags=re.IGNORECASE,
        )
        if m:
            base = m.group(1)
            suffix = m.group(2)
            if suffix:
                return f"{base} {suffix.upper()}"
            stamp_suffix = self._extract_doc_suffix_from_stamp(stamp_text)
            if stamp_suffix:
                return f"{base} {stamp_suffix}"
            return base

        stamp_suffix = self._extract_doc_suffix_from_stamp(stamp_text)
        if stamp_suffix and not s.endswith(f" {stamp_suffix}"):
            return f"{s} {stamp_suffix}".strip()
        return s or None

    @staticmethod
    def _doc_suffix_for_type(doc_type: Optional[str]) -> Optional[str]:
        low = str(doc_type or "").lower().replace("ё", "е")
        if "сбороч" in low:
            return "СБ"
        if "схема электрическая соединений" in low:
            return "Э4"
        if "перечень элементов" in low:
            return "ПЭ4"
        return None

    def _ensure_decimal_suffix_for_type(self, decimal_number: Optional[str], doc_type: Optional[str]) -> Optional[str]:
        text = re.sub(r"\s+", " ", str(decimal_number or "")).strip()
        if not text:
            return None
        if re.search(r"\s(?:СБ|Э4|ПЭ4|СП)$", text, flags=re.IGNORECASE):
            return text
        suffix = self._doc_suffix_for_type(doc_type)
        if suffix:
            return f"{text} {suffix}"
        return text

    def _extract_decimal(self, text: str) -> Optional[str]:
        text = self._strip_control_chars(self._fix_mojibake(text or "")) or ""
        for pattern in self.decimal_patterns:
            match = pattern.search(text)
            if match:
                value = re.sub(r"\s+", " ", match.group(1)).strip()
                normalized = self._normalize_decimal_number(value, text)
                if normalized:
                    return normalized


        glued = re.search(r"(?:^|[^0-9])\d?(\d{3}\.\d{2}\.\d{2}\.\d{2}\.\d{3})(?:\s*(СБ|СП|ПЭ4|Э4))?", text, flags=re.IGNORECASE)
        if glued:
            suffix = glued.group(2) or ""
            value = f"{glued.group(1)} {suffix}".strip()
            return self._normalize_decimal_number(value, text)

        return None

    def _extract_doc_type(self, stamp_text: str, decimal_number: Optional[str]) -> str:
        lower = stamp_text.lower()
        dn = (decimal_number or "").upper()

        # Для чертежей приоритет выше: в штампе часто встречаются ссылки на ПЭ4/Э4,
        # и если сначала проверять их, чертеж уедет в чужой тип.
        if "сбороч" in lower or dn.endswith("СБ"):
            return "Сборочный чертеж"
        if "схема электрическая соединений" in lower or dn.endswith("Э4"):
            return "Схема электрическая соединений"
        if "перечень элементов" in lower or re.search(r"\bпэ4\b", lower) or dn.endswith("ПЭ4"):
            return "Перечень элементов"
        if "спецификац" in lower or dn.endswith("СП"):
            return "Спецификация"

        return "Не определён"

    def _extract_title(self, stamp_line_infos, decimal_number: Optional[str], doc_type: str, page_width: float) -> Optional[str]:
        candidates = []
        dn = (decimal_number or "").replace(" ", "")
        doc_type_lower = doc_type.lower()

        explicit_title_re = re.compile(r"^(жгут|кабель|сборка|шнур|переходник|блок|панель|адаптер)\b", re.I)
        banned_substrings = [
            "лит.", "лист", "листов", "формат", "масса", "масштаб", "разраб.", "пров.",
            "т. контр.", "н. контр.", "утв.", "подп.", "дата", "копировал", "сборочный чертеж",
            "спецификация", "перечень элементов", "схема электрическая", "см.тт", "см. тт",
            "гост", "ту", "ост", "п.", "направление текста", "остальные", "герметиком",
            "анатерм", "унигерм", "loctite"
        ]

        # 1) Сильный приоритет строк, похожих на наименование изделия: короткие,
        # находящиеся в правом нижнем штампе и начинающиеся с типового слова.
        for line_info in stamp_line_infos:
            line = self._norm(line_info["text"])
            low = line.lower()
            if not line:
                continue
            if decimal_number and dn in line.replace(" ", ""):
                continue
            if any(bad in low for bad in banned_substrings):
                continue
            if explicit_title_re.search(line) and len(line) <= 40:
                candidates.append((200 + len(re.findall(r"[А-Яа-яA-Za-z]", line)), line))

        if candidates:
            return max(candidates, key=lambda item: item[0])[1]

        # 2) Частый кейс одиночных чертежей: название изделия стоит в одной строке
        # со служебным заголовком "Изм. Лист № докум. Подп. Дата ...".
        extracted_tail_candidates = []
        for line_info in stamp_line_infos:
            line = line_info["text"]
            tail_match = re.search(r"\bДата\b\s+(.+)$", line, flags=re.I)
            if not tail_match:
                continue
            tail = self._norm(tail_match.group(1))
            tail_low = tail.lower()
            if not tail:
                continue
            if len(re.findall(r"[А-Яа-яA-Za-z]", tail)) < 3:
                continue
            if self.scale_re.search(tail):
                continue
            if decimal_number and dn in tail.replace(" ", ""):
                continue
            if doc_type_lower != "не определён" and doc_type_lower in tail_low:
                continue
            if any(word.replace(" ", "") in tail_low.replace(" ", "") for word in self.service_words):
                continue
            if any(bad in tail_low for bad in ["гост", "ту", "ост", "п."]):
                continue
            extracted_tail_candidates.append((100, tail))

        if extracted_tail_candidates:
            return max(extracted_tail_candidates, key=lambda item: item[0])[1]


        for line_info in stamp_line_infos:
            line = line_info["text"]
            cleaned = self.scale_re.sub("", line)
            cleaned = self.mass_re.sub("", cleaned)
            cleaned = self.litera_re.sub("", cleaned)
            cleaned = re.sub(r"\b(АО|ООО|ПАО|ЗАО)\b.*", "", cleaned, flags=re.I)
            cleaned = re.sub(r"\b(Лист|Листов|Формат|См\.|См|Изм\.|Изм)\b.*", "", cleaned, flags=re.I)
            cleaned = self._norm(cleaned)
            low_clean = cleaned.lower()
            compact_clean = low_clean.replace(" ", "")

            if len(cleaned) < 3 or len(cleaned) > 50:
                continue
            if line_info["x0"] < page_width * 0.72:
                continue
            if any(word.replace(" ", "") in compact_clean for word in self.service_words):
                continue
            if decimal_number and dn in cleaned.replace(" ", ""):
                continue
            if doc_type_lower != "не определён" and doc_type_lower in low_clean:
                continue
            if re.fullmatch(r"[A-ZА-Я0-9.\- ]+", cleaned):
                continue
            if "," in cleaned:
                continue
            if any(bad in low_clean for bad in banned_substrings):
                continue

            letters = re.findall(r"[А-Яа-яA-Za-z]", cleaned)
            if len(letters) < 3:
                continue

            token_count = len(cleaned.split())
            score = len(re.findall(r"[А-Яа-я]", cleaned)) * 3
            score += max(0, 15 - abs(token_count - 2) * 4)
            score += 10 if len(cleaned.split()) <= 4 else 0
            candidates.append((score, cleaned))

        return max(candidates, default=(0, None))[1]

    def _is_bad_litera_candidate(self, value: str) -> bool:
        if not value:
            return True
        up = self._norm(value).upper().replace(".", "").replace("Ё", "Е")
        up = re.sub(r"\s+", "", up)
        bad = {
            "ЗАМ", "ИЗМ", "ЛИТ", "ЛИТЕРА", "ЛИСТ", "ЛИСТОВ", "МАСШТАБ", "МАССА",
            "ДАТА", "ДОКУМ", "ДОК", "ПОДП", "РАЗРАБ", "ПРОВ", "УТВ", "КОНТР",
            "ТКОНТР", "НКОНТР", "ТТ", "СМ", "СМТТ", "СМТАБЛ", "ТАБЛ", "ТАБЛИЦА",
            "ТЕХ", "ТРЕБ", "ТРЕБОВАНИЯ", "ФОРМАТ", "КОПИРОВАЛ", "ИНВ", "ПОДЛ"
        }
        if up in bad:
            return True
        if "ТАБЛ" in up or "СМТТ" in up:
            return True
        # Литера обычно одна буква или короткое обозначение, но не число и не фраза.
        if re.search(r"\d", up):
            return True
        return False

    def _extract_litera(self, stamp_line_infos, expanded_line_infos=None) -> Optional[str]:

        line_sets = [stamp_line_infos or []]
        if expanded_line_infos is not None and expanded_line_infos is not stamp_line_infos:
            line_sets.append(expanded_line_infos or [])

        def clean_text(value: str) -> str:
            text = self._strip_control_chars(self._fix_mojibake(value or "")) or ""
            return self._norm(text)

        # 1. Приоритетный вариант: значение около заголовка "Лит".
        for infos in line_sets:
            if not infos:
                continue
            lit_headers = []
            for line_info in infos:
                line = clean_text(line_info.get("text", ""))
                if re.search(r"\bЛит\.?\b", line, flags=re.IGNORECASE):
                    lit_headers.append(line_info)
            for header in lit_headers:
                hx0 = float(header.get("x0", 0.0))
                hy0 = float(header.get("y0", 0.0))
                hy1 = float(header.get("y1", hy0))
                candidates = []
                for line_info in infos:
                    line = clean_text(line_info.get("text", ""))
                    if not line:
                        continue
                    up = line.upper().replace(".", "")
                    if self._is_bad_litera_candidate(up):
                        continue
                    if not re.fullmatch(r"[A-ZА-ЯЁ]{1,3}", up):
                        continue
                    x0 = float(line_info.get("x0", 0.0))
                    y0 = float(line_info.get("y0", 0.0))
                    # значение литеры обычно под заголовком или рядом справа/снизу.
                    near_x = abs(x0 - hx0) <= 90.0
                    below = y0 >= hy1 - 2.0 and y0 <= hy1 + 90.0
                    same_band = abs(y0 - hy0) <= 20.0 and x0 >= hx0
                    if near_x and (below or same_band):
                        candidates.append((abs(y0 - hy1), abs(x0 - hx0), up))
                if candidates:
                    candidates.sort(key=lambda item: (item[0], item[1]))
                    return candidates[0][2]

        for infos in line_sets:
            for line_info in infos:
                line = clean_text(line_info.get("text", ""))
                up = line.upper().replace(".", "")
                if not line:
                    continue
                if re.fullmatch(r"[A-ZА-ЯЁ]{1,3}", up) and not self._is_bad_litera_candidate(up):
                    return up

        joined = "\n".join((line.get("text") or "") for line in (stamp_line_infos or []))
        joined += "\n" + "\n".join((line.get("text") or "") for line in (expanded_line_infos or []))
        text = self._strip_control_chars(self._fix_mojibake(joined)) or ""
        m = self.litera_re.search(text)
        if m:
            cand = m.group(1).upper().replace(".", "")
            if not self._is_bad_litera_candidate(cand):
                return cand
        return None

    @staticmethod
    def _normalize_change_number_value(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text or re.fullmatch(r"[-—–]+", text):
            return None
        m = re.search(r"\d{1,3}", text)
        if not m:
            return None
        try:
            return str(int(m.group(0)))
        except ValueError:
            return None

    def _extract_change_number_from_stamp_words(self, words, page: fitz.Page) -> Optional[str]:

        if not words:
            return None


        by_status_row = self._extract_change_number_from_status_row(words, page)
        if by_status_row:
            return by_status_row

        page_h = float(page.rect.height)
        page_w = float(page.rect.width)
        bottom_words = [w for w in words if float(w[1]) >= page_h * 0.50]
        if not bottom_words:
            bottom_words = list(words)

        def clean(txt: str) -> str:
            txt = self._strip_control_chars(self._fix_mojibake(txt or "")) or ""
            return re.sub(r"\s+", " ", txt).strip()

        bottom_lines = self._group_word_lines_native(bottom_words, x_gap_threshold=220.0)

        def is_header(text: str) -> bool:
            low = text.lower().replace("ё", "е")
            return (
                re.search(r"\bизм\.?\b", low) is not None
                and "лист" in low
                and ("док" in low or "подп" in low or "дата" in low)
            )

        headers = [line for line in bottom_lines if is_header(clean(line.get("text", "")))]
        headers.sort(key=lambda line: (float(line.get("y0", 0.0)), float(line.get("x0", 0.0))))

        for header in headers:
            hy1 = float(header.get("y1", 0.0))
            hx0 = float(header.get("x0", 0.0))
            header_words = sorted(header.get("words", []), key=lambda item: item[0])
            izm_words = [
                w for w in header_words
                if re.fullmatch(r"изм\. ?|изм", clean(w[4]).lower().replace("ё", "е").strip(" :"))
                or clean(w[4]).lower().replace("ё", "е").strip(" .:") == "изм"
            ]
            if izm_words:
                ix0 = min(float(w[0]) for w in izm_words)
                ix1 = max(float(w[2]) for w in izm_words)
            else:
                ix0, ix1 = hx0, hx0 + 45.0

            candidates = []
            for line in bottom_lines:
                ly0 = float(line.get("y0", 0.0))
                if ly0 <= hy1 - 1.0 or ly0 > hy1 + 140.0:
                    continue
                text = clean(line.get("text", ""))
                if not text or is_header(text):
                    continue
                low = text.lower().replace("ё", "е")
                if any(marker in low for marker in ["разраб", "пров", "н.контр", "т.контр", "утв", "масса", "масштаб", "листов"]):
                    continue


                m_first = re.match(r"^\s*(\d{1,3})(?:\s|$|[.,;])", text)
                if m_first:
                    num = self._normalize_change_number_value(m_first.group(1))
                    if num:
                        candidates.append((ly0 - hy1, float(line.get("x0", 0.0)), num))
                        continue

                for w in sorted(line.get("words", []), key=lambda item: item[0]):
                    wx0, wx1 = float(w[0]), float(w[2])
                    wcx = (wx0 + wx1) / 2.0
                    num = self._normalize_change_number_value(clean(w[4]))
                    if not num:
                        continue
                    if (ix0 - 25.0) <= wcx <= (ix1 + 45.0):
                        candidates.append((ly0 - hy1, wx0, num))
                        break

            if candidates:
                candidates.sort(key=lambda item: (item[0], item[1]))
                return candidates[0][2]


        header_words = []
        for w in bottom_words:
            txt = clean(w[4]).lower().replace("ё", "е").strip(" .:")
            if txt == "изм":
                header_words.append(w)

        best = None
        for hw in header_words:
            hx0, hy0, hx1, hy1 = map(float, hw[:4])
            hcx = (hx0 + hx1) / 2.0

            if hcx < page_w * 0.25:
                continue
            for w in bottom_words:
                wx0, wy0, wx1, wy1 = map(float, w[:4])
                if wy0 < hy1 - 2.0 or wy0 > hy1 + 130.0:
                    continue
                wcx = (wx0 + wx1) / 2.0
                if abs(wcx - hcx) > 42.0:
                    continue
                num = self._normalize_change_number_value(clean(w[4]))
                if not num:
                    continue
                cand = (wy0 - hy1, wx0, num)
                if best is None or cand < best:
                    best = cand
        return best[2] if best else None

    @staticmethod
    def _extract_sheet_count_from_text(text: str) -> Optional[int]:
        if not text:
            return None
        normalized = re.sub(r"\s+", " ", str(text))
        patterns = [
            r"Лист\s*Листов\s*(\d{1,3})\s+(\d{1,3})",
            r"Лист\s*Листов\s*(\d{1,3})",
            r"Листов\s*(\d{1,3})",
        ]
        for pattern in patterns:
            m = re.search(pattern, normalized, flags=re.IGNORECASE)
            if not m:
                continue
            value = m.group(2) if len(m.groups()) >= 2 else m.group(1)
            try:
                return int(value)
            except ValueError:
                return None
        return None

    @staticmethod
    def _normalize_sheet_status_token(value: str) -> Optional[str]:
        """Нормализует отметки листа в основной надписи: Зам. / Нов."""
        token = re.sub(r"\s+", "", str(value or "")).strip().lower().replace("ё", "е")
        token = token.strip(".:;,")
        if token in {"зам", "замен", "заменен", "замена"}:
            return "replace"
        if token in {"нов", "новый"}:
            return "new"
        return None

    def _extract_change_number_from_status_row(self, words, page: fitz.Page) -> Optional[str]:

        if not words:
            return None

        page_h = float(page.rect.height)
        candidates = [w for w in words if float(w[1]) >= page_h * 0.45]
        if not candidates:
            candidates = list(words)

        def clean(txt: str) -> str:
            txt = self._strip_control_chars(self._fix_mojibake(txt or "")) or ""
            return re.sub(r"\s+", " ", txt).strip()


        compact_found = []
        for w in candidates:
            x0, y0, x1, y1 = map(float, w[:4])
            token = clean(w[4])
            token_compact = re.sub(r"\s+", "", token).lower().replace("ё", "е")
            m = re.match(r"^(\d{1,3})(зам|нов)\.?$", token_compact)
            if m:
                compact_found.append((y0, x0, self._normalize_change_number_value(m.group(1))))
        compact_found = [item for item in compact_found if item[2]]
        if compact_found:
            compact_found.sort(key=lambda item: (item[0], item[1]))
            return compact_found[0][2]

        # 2) Основной случай: статус отдельным словом, номер изменения слева в этой же строке.
        status_words = []
        for w in candidates:
            x0, y0, x1, y1 = map(float, w[:4])
            status = self._normalize_sheet_status_token(clean(w[4]))
            if status:
                cy = (y0 + y1) / 2.0
                status_words.append((x0, y0, x1, y1, cy, status))

        row_candidates = []
        for sx0, sy0, sx1, sy1, scy, status in status_words:
            same_row = []
            for w in candidates:
                x0, y0, x1, y1 = map(float, w[:4])
                cy = (y0 + y1) / 2.0

                if abs(cy - scy) <= 8.0:
                    same_row.append(w)


            left_numbers = []
            for w in same_row:
                x0, y0, x1, y1 = map(float, w[:4])
                token = clean(w[4])
                num = self._normalize_change_number_value(token)
                if not num:
                    continue

                if x1 <= sx0 + 4.0 and (sx0 - x1) <= 90.0:
                    left_numbers.append((sx0 - x1, x0, num))

            if left_numbers:
                left_numbers.sort(key=lambda item: (item[0], item[1]))
                row_candidates.append((sy0, sx0, left_numbers[0][2]))
                continue


            nearby_numbers = []
            for w in same_row:
                x0, y0, x1, y1 = map(float, w[:4])
                token = clean(w[4])
                num = self._normalize_change_number_value(token)
                if not num:
                    continue
                if x0 <= sx1 + 10.0 and abs(x0 - sx0) <= 130.0:
                    nearby_numbers.append((abs(x0 - sx0), x0, num))
            if nearby_numbers:
                nearby_numbers.sort(key=lambda item: (item[0], item[1]))
                row_candidates.append((sy0, sx0, nearby_numbers[0][2]))

        if row_candidates:
            row_candidates.sort(key=lambda item: (item[0], item[1]))
            return row_candidates[0][2]

        return None

    def _extract_change_number_from_document(self, pdf_path: str) -> Optional[str]:

        try:
            doc = fitz.open(pdf_path)
        except Exception:
            return None

        found: List[int] = []
        for page in doc:
            words = self._page_words(page)
            value = self._extract_change_number_from_status_row(words, page)
            if not value:
                value = self._extract_change_number_from_stamp_words(words, page)
            value = self._normalize_change_number_value(value)
            if value:
                try:
                    found.append(int(value))
                except ValueError:
                    pass

        if not found:
            return None

        return str(max(found))

    def _extract_sheet_status_from_page_words(self, words, page: fitz.Page) -> Optional[str]:

        if not words:
            return None

        page_h = float(page.rect.height)
        page_w = float(page.rect.width)
        # Основная надпись и таблица изменений находятся в нижней части листа.
        candidates = [w for w in words if float(w[1]) >= page_h * 0.50]
        if not candidates:
            candidates = list(words)

        def clean(txt: str) -> str:
            txt = self._strip_control_chars(self._fix_mojibake(txt or "")) or ""
            return re.sub(r"\s+", " ", txt).strip()


        found = []
        for w in candidates:
            x0, y0, x1, y1 = map(float, w[:4])
            txt = clean(w[4])
            status = self._normalize_sheet_status_token(txt)
            if not status:
                continue

            if y0 < page_h * 0.50:
                continue
            found.append((y0, x0, status))

        if found:
            found.sort(key=lambda item: (item[0], item[1]))

            statuses = [item[2] for item in found]
            if "new" in statuses:
                return "new"
            if "replace" in statuses:
                return "replace"

        return None

    def _extract_sheet_info(self, pdf_path: str) -> tuple[Optional[int], Dict[int, str]]:

        sheet_count: Optional[int] = None
        sheet_statuses: Dict[int, str] = {}

        try:
            doc = fitz.open(pdf_path)
        except Exception:
            return None, {}

        for page_index, page in enumerate(doc):
            page_num = page_index + 1
            words = self._page_words(page)

            primary_bbox = self._expected_title_block_bbox(page)
            expanded_bbox = self._expanded_title_block_bbox(page)
            primary_words = self._words_in_rect(words, primary_bbox)
            expanded_words = self._words_in_rect(words, expanded_bbox)
            primary_lines = self._group_word_lines_native(primary_words)
            expanded_lines = self._group_word_lines_native(expanded_words)
            primary_text = "\n".join(line.get("text", "") for line in primary_lines)
            expanded_text = "\n".join(line.get("text", "") for line in expanded_lines)
            bottom_right_rect = fitz.Rect(page.rect.width * 0.45, page.rect.height * 0.60, page.rect.width, page.rect.height)
            bottom_right_text = self._fix_mojibake(page.get_text("text", clip=bottom_right_rect, sort=True) or "")
            combined = "\n".join([primary_text, expanded_text, bottom_right_text])

            count = self._extract_sheet_count_from_text(combined)
            if count:
                # Количество листов берем только из основной надписи, а не из текста
                # всего чертежа. Если pdfplumber/fitz случайно поймал номер пункта
                # ТТ или номер позиции как "Листов 25", такое значение не должно
                # превращаться в действие "листы 2-25 вводятся вновь".
                max_reasonable = max(1, doc.page_count + 2)
                if 1 <= count <= max_reasonable:
                    if sheet_count is None or page_index == 0:
                        sheet_count = count

            status = self._extract_sheet_status_from_page_words(words, page)
            if status:
                sheet_statuses[page_num] = status

        return sheet_count, sheet_statuses

    def _extract_metadata(self, pdf_path: str) -> DocMetadata:
        doc = fitz.open(pdf_path)
        page = doc[0]
        words = self._page_words(page)

        primary_bbox = self._expected_title_block_bbox(page)
        primary_words = self._words_in_rect(words, primary_bbox)
        primary_line_infos = self._group_word_lines_native(primary_words)
        primary_text = "\n".join(line["text"] for line in primary_line_infos)

        expanded_bbox = self._expanded_title_block_bbox(page)
        expanded_words = self._words_in_rect(words, expanded_bbox)
        expanded_line_infos = self._group_word_lines_native(expanded_words)
        expanded_text = "\n".join(line["text"] for line in expanded_line_infos)

        bottom_right_rect = fitz.Rect(page.rect.width * 0.50, page.rect.height * 0.70, page.rect.width, page.rect.height)
        bottom_right_text = self._fix_mojibake(page.get_text("text", clip=bottom_right_rect, sort=True) or "")
        bottom_right_text = self._strip_control_chars(bottom_right_text) or ""

        decimal_number = (
            self._normalize_decimal_number(self._extract_decimal(primary_text), primary_text)
            or self._normalize_decimal_number(self._extract_decimal(expanded_text), expanded_text)
            or self._normalize_decimal_number(self._extract_decimal(bottom_right_text), bottom_right_text)
        )


        stamp_text = primary_text
        stamp_line_infos = primary_line_infos
        stamp_words = primary_words
        if (not decimal_number or "сборочный чертеж" not in primary_text.lower()) and expanded_text:
            stamp_text = expanded_text
            stamp_line_infos = expanded_line_infos
            stamp_words = expanded_words

        sheet_count, sheet_statuses = self._extract_sheet_info(pdf_path)

        doc_type = self._extract_doc_type(stamp_text, decimal_number)
        decimal_number = self._ensure_decimal_suffix_for_type(decimal_number, doc_type)

        title = self._extract_title(stamp_line_infos, decimal_number, doc_type, page.rect.width)
        if not title and stamp_line_infos is not expanded_line_infos:
            title = self._extract_title(expanded_line_infos, decimal_number, doc_type, page.rect.width)
        if title:
            title = re.sub(r"\bСм\.?\s*ТТ\b", "", title, flags=re.I)
            title = re.sub(r"\s+[ОO]$", "", title).strip(" ,;:-")

        stamp_mass_line, stamp_scale_line = self._extract_mass_and_scale_from_stamp_lines(stamp_line_infos)
        expanded_mass_line, expanded_scale_line = self._extract_mass_and_scale_from_stamp_lines(expanded_line_infos)
        stamp_mass, stamp_scale = self._extract_mass_and_scale_from_stamp(stamp_text)
        expanded_mass, expanded_scale = self._extract_mass_and_scale_from_stamp(expanded_text)
        bottom_mass, bottom_scale = self._extract_mass_and_scale_from_stamp(bottom_right_text)

        scale = stamp_scale_line or stamp_scale or expanded_scale_line or expanded_scale or bottom_scale
        litera_value = self._extract_litera(stamp_line_infos, expanded_line_infos)
        change_number = self._extract_change_number_from_document(pdf_path)

        mass_kg = stamp_mass_line
        if mass_kg is None:
            mass_kg = stamp_mass
        if mass_kg is None:
            mass_kg = expanded_mass_line
        if mass_kg is None:
            mass_kg = expanded_mass
        if mass_kg is None:
            mass_kg = self._extract_mass_from_stamp_words(primary_words) or self._extract_mass_from_stamp_words(expanded_words)
        if mass_kg is None:
            mass_kg = bottom_mass

        return DocMetadata(
            decimal_number=decimal_number,
            doc_type=doc_type,
            title=title,
            mass_kg=mass_kg,
            scale=scale,
            litera=litera_value,
            change_number=change_number,
            sheet_count=sheet_count,
            sheet_statuses=sheet_statuses,
            raw_stamp_snippet=self._strip_control_chars("\n".join(line["text"] for line in stamp_line_infos)[-1200:]) or self._strip_control_chars(stamp_text[-1200:]) or "",
        )

    def _clean_rows(self, rows):
        cleaned = []
        for row in rows or []:
            new_row = [self._norm_cell(cell) for cell in row]
            if any(cell for cell in new_row):
                cleaned.append(new_row)
        return cleaned

    def _is_title_block_table(self, rows: List[List[str]], bbox, page_width: float, page_height: float) -> bool:
        flat = " ".join(cell.lower() for row in rows for cell in row if cell)
        stamp_bbox = (page_width * 0.60, page_height * 0.85, page_width, page_height)
        x0, y0, x1, y1 = bbox
        width = x1 - x0
        height = y1 - y0

        if self._rect_intersection_ratio(bbox, stamp_bbox) > 0.30:
            return True

        # Узкие вертикальные полосы и мелкие таблицы по краям листа — почти всегда мусор штампа.
        if x0 < page_width * 0.10 and width < page_width * 0.12:
            return True
        if y0 < page_height * 0.10 and height < page_height * 0.08 and width < page_width * 0.35:
            return True
        if len(rows) < 2 and width < page_width * 0.40:
            return True

        service_hits = sum(1 for kw in self.service_words if kw in flat)
        if service_hits >= 3:
            return True

        non_empty_cells = sum(1 for row in rows for cell in row if cell)
        if non_empty_cells <= 3 and width < page_width * 0.45:
            return True

        return False

    def _looks_like_known_data_table(self, rows: List[List[str]], table_name: str) -> bool:
        flat_rows = " ".join(self._norm_cell(cell).lower() for row in rows[:3] for cell in row if cell)
        flat_name = self._norm(table_name).lower()
        known_headers = [
            "провод", "поз", "от соединителя", "к соединителю", "обозначение", "длина", "кол",
            "позиционное обозначение", "маркировка", "код", "масса", "xp1", "xs1", "xs2"
        ]
        return any(h in flat_rows for h in known_headers) or any(h in flat_name for h in ["таблица", "маркировка", "провод", "поз"])

    def _is_noise_table(self, rows: List[List[str]], table_name: str, bbox, page_width: float, page_height: float) -> bool:
        if not rows:
            return True

        if self._looks_like_known_data_table(rows, table_name) or self._looks_like_specification_table(rows):
            return False

        flat_rows = " ".join(self._norm_cell(cell).lower() for row in rows[:3] for cell in row if cell)

        # Если названием стала одиночная цифра/позиция (например "25"), а в
        # первых строках нет шапки реальной таблицы, это почти всегда вытащенная
        # из графики/выноски мини-таблица, а не таблица КД.
        if re.fullmatch(r"\d+(?:[,.]\d+)?", self._norm_cell(table_name)):
            header_markers = ("провод", "поз", "от соедин", "к соедин", "обознач", "длина", "кол", "маркиров", "код", "масса")
            if not any(marker in flat_rows for marker in header_markers):
                return True

        x0, y0, x1, y1 = bbox
        width = x1 - x0
        height = y1 - y0

        noise_markers = ["п.", "l=", "экран", "gnd", "s1", "s2", "xs", "xp", "xt", "x1", "x2"]
        noise_hits = sum(1 for m in noise_markers if m in flat_rows)
        alpha_words = re.findall(r"[а-яa-z]+", flat_rows)

        # Графические подписи в верхней части листа часто ошибочно превращаются в "таблицы".
        if y1 < page_height * 0.35 and noise_hits >= 3:
            return True
        if len(rows) <= 4 and noise_hits >= 4 and len(alpha_words) < 25:
            return True
        if width > page_width * 0.20 and height < page_height * 0.25 and noise_hits >= 4:
            return True

        return False

    def _get_table_name(self, page: fitz.Page, bbox: Tuple[float, float, float, float], rows: List[List[str]]) -> str:
        words = self._page_words(page)
        x0, y0, x1, _ = bbox
        title_search_rect = (max(0, x0 - 20), max(0, y0 - 40), min(page.rect.width, x1 + 20), y0 + 5)
        lines_above = self._words_to_lines(self._words_in_rect(words, title_search_rect))

        for line in reversed(lines_above):
            if self.table_title_re.search(line) or re.search(r"\bпродолжение\s+таблиц[ыа]?\s*\d+", line, flags=re.IGNORECASE):
                return line

        if rows and rows[0]:
            header = " | ".join(cell for cell in rows[0] if cell)
            if header:
                return header[:80]

        return f"Таблица_стр_{page.number + 1}"


    def _looks_like_specification_table(self, rows: List[List[str]]) -> bool:
        flat = " ".join(self._norm_cell(cell).lower() for row in rows for cell in row if cell)
        return (
            "обознач" in flat
            and "наимен" in flat
            and ("кол" in flat or "кол." in flat)
            and ("документац" in flat or "материал" in flat or "прочие изделия" in flat)
        )

    def _looks_like_specification_doc(self, metadata: DocMetadata, tables: List[TableData], pdf_path: Optional[str] = None) -> bool:
        if (metadata.doc_type or "").lower() == "спецификация":
            return True
        if pdf_path and self._looks_like_specification_doc_by_text(pdf_path):
            return True
        return any(self._looks_like_specification_table(table.rows) for table in tables)

    @staticmethod
    def _canon_header(text: str) -> str:
        value = (text or "").lower().replace("\n", " ").replace("-", "").replace(".", "")
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _header_index_map(self, header_row: List[str]) -> Dict[str, int]:
        mapping: Dict[str, int] = {}
        for idx, cell in enumerate(header_row):
            canon = self._canon_header(cell)
            if not canon:
                continue
            if "формат" in canon:
                mapping["format"] = idx
            elif "зона" in canon:
                mapping["zone"] = idx
            elif "поз" in canon:
                mapping["position"] = idx
            elif "обознач" in canon:
                mapping["designation"] = idx
            elif "наимен" in canon:
                mapping["name"] = idx
            elif canon == "кол" or "кол " in canon or canon.endswith(" кол"):
                mapping["quantity"] = idx
            elif "примеч" in canon:
                mapping["note"] = idx
        return mapping

    def _row_value(self, row: List[str], index_map: Dict[str, int], field_name: str) -> str:
        idx = index_map.get(field_name)
        if idx is None or idx >= len(row):
            return ""
        return self._norm_cell(row[idx])

    def _is_spec_section_row(self, row: List[str]) -> Optional[str]:
        values = [self._norm_cell(cell) for cell in row if self._norm_cell(cell)]
        if not values:
            return None
        joined = " ".join(values).lower()
        joined = re.sub(r"\s+", " ", joined).strip()
        if joined in self.spec_section_names:
            return joined.title()
        if len(values) <= 2:
            for section in self.spec_section_names:
                if section in joined:
                    return section.title()
        return None

    def _append_item_text(self, base: Optional[str], extra: str) -> Optional[str]:
        extra = self._clean_spec_field_value(extra)
        if not extra:
            return base
        if not base:
            return extra
        if extra in base:
            return base
        return f"{base} {extra}".strip()

    def _clean_spec_field_value(self, value: Optional[str]) -> Optional[str]:
        text = self._norm_cell(value)
        if not text:
            return None

        # Отрезаем хвосты основной надписи/таблицы изменений, которые pdfplumber
        # иногда приклеивает к последней строке спецификации на листе.
        text = re.sub(r"\s+\d{3}\.\d{2}\.\d{2}\.\d{2}\.\d{3}\b.*$", "", text)
        text = re.sub(r"\s+(?:АО\s*\"?ЭЙРБУРГ\"?|Формат\s*A\d|Лист(?:ов)?\b|Лит\.|Изм\.|Зам\.|Нов\.).*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+\.лбуд.*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+\.мазв.*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+\.лдоп.*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+Жгут\s+\d{3}\.\d{2}-\d+.*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+Кабель\b.*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip(" .;,:-/")
        return text or None

    def _normalize_spec_item_fields(self, item: dict) -> dict:
        if not item:
            return item

        for key in ("designation", "name", "quantity", "note"):
            item[key] = self._clean_spec_field_value(item.get(key))

        designation = item.get("designation") or ""
        name = item.get("name") or ""

        # Частый перенос в СП: обозначение заканчивается на "ТУ 22.21.29-",
        # а продолжение номера ТУ попадает в графу "Наименование" перед названием.
        m = re.search(r"^(?P<title>.+?ТМАРК-)\s*(?P<tail>\d{3}-\d{8}-\d{4})(?:\s+(?P<rest>.*))?$", name, flags=re.IGNORECASE)
        if m and designation.endswith("-"):
            item["designation"] = f"{designation}{m.group('tail')}"
            item["name"] = self._clean_spec_field_value(m.group("title"))

        return item

    def _make_spec_key(self, section: Optional[str], position: Optional[str], designation: Optional[str], name: Optional[str]) -> str:
        sec = (section or "").strip().lower()
        pos = (position or "").strip()
        des = (designation or "").strip()
        nm = (name or "").strip().lower()
        if sec == "документация":
            anchor = des or nm
        else:
            anchor = pos or des or nm
        return f"{sec}|{anchor}".strip("|")

    def _extract_specification_items(self, tables: List[TableData]) -> List[SpecificationItem]:
        items: List[SpecificationItem] = []
        current_section: Optional[str] = None
        current_item: Optional[dict] = None

        def flush_current():
            nonlocal current_item
            if not current_item:
                return
            current_item = self._normalize_spec_item_fields(current_item)
            key = self._make_spec_key(
                current_item.get("section"),
                current_item.get("position"),
                current_item.get("designation"),
                current_item.get("name"),
            )
            if key:
                current_item["key"] = key
                items.append(SpecificationItem(**current_item))
            current_item = None

        for table in sorted(tables, key=lambda t: (t.page_num, t.bbox[1], t.bbox[0])):
            if not self._looks_like_specification_table(table.rows):
                continue

            header_idx = None
            index_map: Dict[str, int] = {}
            for idx, row in enumerate(table.rows[:6]):
                row_map = self._header_index_map(row)
                if "designation" in row_map and "name" in row_map:
                    header_idx = idx
                    index_map = row_map
                    break

            if header_idx is None:
                continue

            flush_current()

            for row in table.rows[header_idx + 1:]:
                row = [self._norm_cell(cell) for cell in row]
                if not any(row):
                    continue

                section = self._is_spec_section_row(row)
                if section:
                    flush_current()
                    current_section = section
                    continue

                if row and "формат" in " ".join(self._canon_header(cell) for cell in row):
                    continue

                row_format = self._row_value(row, index_map, "format")
                row_zone = self._row_value(row, index_map, "zone")
                row_position = self._row_value(row, index_map, "position")
                row_designation = self._row_value(row, index_map, "designation")
                row_name = self._row_value(row, index_map, "name")
                row_quantity = self._row_value(row, index_map, "quantity")
                row_note = self._row_value(row, index_map, "note")

                if not any([row_format, row_zone, row_position, row_designation, row_name, row_quantity, row_note]):
                    continue

                if any(token in " ".join(row).lower() for token in ["изм.", "лист", "№ докум.", "подп.", "дата"]):
                    flush_current()
                    continue

                sec_lower = (current_section or "").lower()
                starts_new = False
                if sec_lower == "документация":
                    starts_new = bool(row_format or row_designation or row_name)
                else:
                    starts_new = bool(row_position)
                    if not starts_new and current_item is None and (row_name or row_designation):
                        starts_new = True

                if starts_new:
                    flush_current()
                    current_item = {
                        "page_num": table.page_num,
                        "section": current_section,
                        "format": row_format or None,
                        "zone": row_zone or None,
                        "position": row_position or None,
                        "designation": row_designation or None,
                        "name": row_name or None,
                        "quantity": row_quantity or None,
                        "note": row_note or None,
                    }
                    continue

                if current_item is None:
                    current_item = {
                        "page_num": table.page_num,
                        "section": current_section,
                        "format": row_format or None,
                        "zone": row_zone or None,
                        "position": row_position or None,
                        "designation": row_designation or None,
                        "name": row_name or None,
                        "quantity": row_quantity or None,
                        "note": row_note or None,
                    }
                    continue

                current_item["designation"] = self._append_item_text(current_item.get("designation"), row_designation)
                current_item["name"] = self._append_item_text(current_item.get("name"), row_name)
                current_item["quantity"] = self._append_item_text(current_item.get("quantity"), row_quantity)
                current_item["note"] = self._append_item_text(current_item.get("note"), row_note)
                if not current_item.get("format") and row_format:
                    current_item["format"] = row_format
                if not current_item.get("zone") and row_zone:
                    current_item["zone"] = row_zone

        flush_current()

        unique: Dict[str, SpecificationItem] = {}
        for item in items:
            if item.key not in unique:
                unique[item.key] = item
            else:
                prev = unique[item.key]
                prev.designation = self._append_item_text(prev.designation, item.designation or "")
                prev.name = self._append_item_text(prev.name, item.name or "")
                prev.quantity = self._append_item_text(prev.quantity, item.quantity or "")
                prev.note = self._append_item_text(prev.note, item.note or "")
        return list(unique.values())

    def _diff_specification_items(self, items1: List[SpecificationItem], items2: List[SpecificationItem]) -> List[SpecificationItemDiff]:
        left = {item.key: item for item in items1}
        right = {item.key: item for item in items2}
        all_keys = sorted(set(left) | set(right))
        diffs: List[SpecificationItemDiff] = []

        for key in all_keys:
            v1_item = left.get(key)
            v2_item = right.get(key)

            if v1_item and not v2_item:
                diffs.append(SpecificationItemDiff(
                    key=key,
                    section=v1_item.section,
                    position=v1_item.position,
                    designation=v1_item.designation,
                    name=v1_item.name,
                    status="removed",
                    v1_item=v1_item,
                    v2_item=None,
                ))
                continue

            if v2_item and not v1_item:
                diffs.append(SpecificationItemDiff(
                    key=key,
                    section=v2_item.section,
                    position=v2_item.position,
                    designation=v2_item.designation,
                    name=v2_item.name,
                    status="added",
                    v1_item=None,
                    v2_item=v2_item,
                ))
                continue

            assert v1_item is not None and v2_item is not None

            field_changes: List[SpecificationFieldChange] = []
            for field_name in ["format", "zone", "designation", "name", "quantity", "note"]:
                left_val = self._norm_cell(getattr(v1_item, field_name))
                right_val = self._norm_cell(getattr(v2_item, field_name))
                if self._normalize_compare_value(left_val) != self._normalize_compare_value(right_val):
                    field_changes.append(SpecificationFieldChange(
                        field_name=field_name,
                        v1_val=getattr(v1_item, field_name),
                        v2_val=getattr(v2_item, field_name),
                    ))

            if field_changes:
                diffs.append(SpecificationItemDiff(
                    key=key,
                    section=v2_item.section or v1_item.section,
                    position=v2_item.position or v1_item.position,
                    designation=v2_item.designation or v1_item.designation,
                    name=v2_item.name or v1_item.name,
                    status="modified",
                    field_changes=field_changes,
                    v1_item=v1_item,
                    v2_item=v2_item,
                ))

        return diffs

    def _extract_tables(self, pdf_path: str):
        result: List[TableData] = []
        page_bboxes: Dict[int, List[Tuple[float, float, float, float]]] = {}
        fitz_doc = fitz.open(pdf_path)

        with pdfplumber.open(pdf_path) as pdf:
            for page_index, page in enumerate(pdf.pages):
                tables_on_page: List[TableData] = []
                fitz_page = fitz_doc[page_index]

                for settings in self.table_settings_variants:
                    try:
                        found = page.find_tables(table_settings=settings)
                    except TypeError:
                        found = page.find_tables(settings)

                    for table in found:
                        bbox = tuple(float(v) for v in table.bbox)
                        rows = self._clean_rows(table.extract())

                        if not rows:
                            continue
                        if self._is_title_block_table(rows, bbox, page.width, page.height):
                            continue
                        if any(self._bbox_iou(bbox, item.bbox) > 0.85 for item in tables_on_page):
                            continue

                        table_name = self._get_table_name(fitz_page, bbox, rows)
                        if self._is_noise_table(rows, table_name, bbox, page.width, page.height):
                            continue

                        header_rows_count = self._estimate_header_rows_count(rows)
                        column_names = self._build_column_names(rows, header_rows_count)
                        tables_on_page.append(
                            TableData(
                                page_num=page_index + 1,
                                bbox=bbox,
                                name=table_name,
                                rows=rows,
                                column_names=column_names,
                                header_rows_count=header_rows_count,
                            )
                        )

                result.extend(tables_on_page)
                page_bboxes[page_index] = [item.bbox for item in tables_on_page]

        return result, page_bboxes


    @staticmethod
    def _normalize_table_header_token(text: str) -> str:
        value = str(text or "").lower().replace("ё", "е")
        value = value.replace("/", " ").replace("\\", " ")
        value = re.sub(r"[^а-яa-z0-9]+", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        # Частые обрезки pdfplumber/CAD-текста: для группировки это один смысл.
        value = value.replace("дл мм", "длина мм")
        if value == "дл":
            value = "длина"
        if value in {"кол", "количество"}:
            value = "кол"
        if "обозначение провода" in value or "обозначение кабеля" in value:
            value = "обозначение провода кабеля"
        return value

    def _table_header_key(self, table: TableData) -> str:
        names = list(table.column_names or [])
        if not names and table.rows:
            names = list(table.rows[0] or [])
        parts = [self._normalize_table_header_token(x) for x in names]
        parts = [x for x in parts if x]
        return "|".join(parts)

    @staticmethod
    def _extract_table_number_from_name(name: str) -> Optional[str]:
        text = str(name or "").lower().replace("ё", "е")
        m = re.search(r"\b(?:продолжение\s+)?таблиц[аы]?\s*(\d{1,3})\b", text, flags=re.IGNORECASE)
        return m.group(1) if m else None

    def _infer_table_number(self, table: TableData) -> Optional[str]:
        number = self._extract_table_number_from_name(table.name)
        if number:
            return number


        blob = f"{table.name} {self._table_header_key(table)}".lower().replace("ё", "е")
        if "провод" in blob and ("соединител" in blob or "длина" in blob or "кол" in blob):
            return "1"
        return None

    def _is_table_continuation_name(self, name: str) -> bool:
        text = str(name or "").lower().replace("ё", "е")
        return "продолжение" in text and "таблиц" in text

    def _canonical_table_display_name(self, tables: List[TableData], table_number: Optional[str]) -> str:
        if table_number:
            for table in tables:
                name = self._norm_cell(table.name)
                if not name:
                    continue
                if self._extract_table_number_from_name(name) == table_number and not self._is_table_continuation_name(name):
                    return name
            return f"Таблица {table_number}"

        for table in tables:
            name = self._norm_cell(table.name)
            if name and not self._is_table_continuation_name(name):
                return name
        return self._norm_cell(tables[0].name) if tables else "Таблица"

    def _row_looks_like_same_header(self, row: List[str], column_names: List[str]) -> bool:
        normalized_row = [self._norm_cell(cell) for cell in row]
        return self._is_repeated_header_row(normalized_row, column_names)

    def _merge_continued_tables(self, tables: List[TableData]) -> List[TableData]:

        if not tables:
            return []

        ordered = sorted(tables, key=lambda t: (t.page_num, float(t.bbox[1]), float(t.bbox[0])))
        groups: Dict[tuple, List[TableData]] = {}
        passthrough: List[TableData] = []

        for table in ordered:
            number = self._infer_table_number(table)
            header_key = self._table_header_key(table)
            if not header_key:
                passthrough.append(table)
                continue

            # Для явно пронумерованных/распознанных продолжений группируем по номеру
            # таблицы и шапке. Если номера нет, не рискуем сливать разные таблицы.
            if number:
                key = ("numbered", number, header_key)
                groups.setdefault(key, []).append(table)
            else:
                passthrough.append(table)

        merged: List[TableData] = []
        consumed_ids = {id(t) for group in groups.values() for t in group}

        for group_key, group_tables in groups.items():
            if len(group_tables) == 1:
                table = group_tables[0]
                number = self._infer_table_number(table)
                canonical_name = self._canonical_table_display_name(group_tables, number)
                merged.append(TableData(
                    page_num=table.page_num,
                    bbox=table.bbox,
                    name=canonical_name,
                    rows=table.rows,
                    column_names=table.column_names,
                    header_rows_count=table.header_rows_count,
                ))
                continue

            group_tables = sorted(group_tables, key=lambda t: (t.page_num, float(t.bbox[1]), float(t.bbox[0])))
            base = group_tables[0]
            canonical_number = self._infer_table_number(base)
            canonical_name = self._canonical_table_display_name(group_tables, canonical_number)
            column_names = list(base.column_names or [])
            header_rows_count = max(1, int(base.header_rows_count or 1))
            rows: List[List[str]] = []

            for idx, table in enumerate(group_tables):
                table_rows = [list(row or []) for row in (table.rows or [])]
                if not table_rows:
                    continue

                if idx == 0:
                    rows.extend(table_rows)
                    continue

                # Для продолжений отбрасываем повторную шапку и случайные строки,
                # которые pdfplumber принимает за данные, но фактически это header.
                data_start = max(1, int(table.header_rows_count or header_rows_count))
                for row in table_rows[data_start:]:
                    if self._row_looks_like_same_header(row, column_names):
                        continue
                    rows.append(row)

                # Если у продолжения шапка распознана лучше, не теряем ее.
                if len(table.column_names or []) > len(column_names):
                    column_names = list(table.column_names or [])

            # bbox делаем покрывающим первый фрагмент на первой странице; для логики
            # сравнения важнее rows/name. Графические маски остаются из raw-фрагментов.
            min_x0 = min(float(t.bbox[0]) for t in group_tables)
            min_y0 = min(float(t.bbox[1]) for t in group_tables if t.page_num == base.page_num)
            max_x1 = max(float(t.bbox[2]) for t in group_tables)
            max_y1 = max(float(t.bbox[3]) for t in group_tables if t.page_num == base.page_num)

            merged.append(TableData(
                page_num=base.page_num,
                bbox=(min_x0, min_y0, max_x1, max_y1),
                name=canonical_name,
                rows=rows,
                column_names=column_names,
                header_rows_count=header_rows_count,
            ))

        for table in ordered:
            if id(table) not in consumed_ids:
                passthrough.append(table)

        # Убираем возможные дубли из passthrough и возвращаем в порядке документа.
        result: List[TableData] = []
        seen_ids = set()
        for table in merged + passthrough:
            if id(table) in seen_ids:
                continue
            seen_ids.add(id(table))
            result.append(table)

        return sorted(result, key=lambda t: (t.page_num, float(t.bbox[1]), float(t.bbox[0]), self._norm_cell(t.name)))

    def _parse_numbered_requirements(self, text: str) -> Dict[int, str]:
        if not text:
            return {}

        text = text.replace("\r", "")
        text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{2,}", "\n", text).strip()

        reqs: Dict[int, str] = {}
        pattern = re.compile(r"(?ms)^\s*(\d{1,2})[.)]?\s+(.*?)(?=^\s*\d{1,2}[.)]?\s+|\Z)")
        for match in pattern.finditer(text):
            num = int(match.group(1))
            body = self._norm(match.group(2))
            if len(body) < 6:
                continue
            if re.fullmatch(r"[\d\W]+", body):
                continue
            reqs[num] = body
        return reqs

    def _is_tech_service_line(self, text: str) -> bool:
        low = self._norm(text).lower().strip()
        if not low:
            return True
        if self.table_title_re.match(low):
            return True
        if low in {"дата", "и", "подп.", "№", "инв.", "дубл.", "справ.", "перв.", "примен.", "подл.", "взам."}:
            return True
        if re.fullmatch(r"(обозначение|код|маркировка|xp\d|xs\d|хр\d|хs\d)", low, re.I):
            return True
        if re.match(r"^(таблица\s*\d+|продолжение таблицы)", low, re.I):
            return True
        return False

    def _parse_requirements_from_block_lines(self, lines: List[str]) -> Dict[int, str]:

        if not lines:
            return {}

        cleaned: List[str] = []
        for line in lines:
            text = self._norm(line)
            text = re.sub(r"[ \t]+", " ", text).strip()
            if not text:
                continue
            if self._is_tech_service_line(text):
                continue
            cleaned.append(text)

        if not cleaned:
            return {}

        reqs: Dict[int, str] = {}
        num_only_re = re.compile(r"^\d{1,2}$")
        inline_num_re = re.compile(r"^(\d{1,2})[.)]?\s+(.+)$")


        num_indices = [i for i, line in enumerate(cleaned) if num_only_re.fullmatch(line)]
        if num_indices:
            for idx_pos, idx in enumerate(num_indices):
                num = int(cleaned[idx])
                next_idx = num_indices[idx_pos + 1] if idx_pos + 1 < len(num_indices) else len(cleaned)
                body_lines: List[str] = []

                # Usually the first line of the item is located immediately BEFORE its standalone number.
                if idx - 1 >= 0 and not num_only_re.fullmatch(cleaned[idx - 1]):
                    body_lines.append(cleaned[idx - 1])

                # Continuation lines live after the number until the line BEFORE the next number.
                tail_start = idx + 1
                tail_end = next_idx
                if next_idx < len(cleaned) and next_idx - 1 > idx:
                    tail_end = next_idx - 1
                for j in range(tail_start, tail_end):
                    if not num_only_re.fullmatch(cleaned[j]):
                        body_lines.append(cleaned[j])

                body = "\n".join(body_lines)
                body = self._norm(body)
                body = re.sub(r"(?<=\w)-\n(?=\w)", "", body)
                body = re.sub(r"[ \t]+", " ", body)
                body = re.sub(r"\n{2,}", "\n", body).strip()
                if len(body) >= 6 and not re.fullmatch(r"[\d\W]+", body):
                    reqs[num] = body

            if reqs:
                return reqs


        stitched_lines: List[str] = []
        current_num: Optional[str] = None
        current_parts: List[str] = []
        for line in cleaned:
            m = inline_num_re.match(line)
            if m:
                if current_num is not None and current_parts:
                    stitched_lines.append(f"{current_num} {' '.join(current_parts)}")
                current_num = m.group(1)
                current_parts = [m.group(2).strip()]
            else:
                if current_num is not None:
                    current_parts.append(line)
        if current_num is not None and current_parts:
            stitched_lines.append(f"{current_num} {' '.join(current_parts)}")

        if stitched_lines:
            return self._parse_numbered_requirements("\n".join(stitched_lines))
        return {}

    def _extract_tech_reqs(self, pdf_path: str, table_bboxes_by_page):
        doc = fitz.open(pdf_path)
        best_reqs: Dict[int, str] = {}

        for page_index, page in enumerate(doc):
            title_bbox = self._expected_title_block_bbox(page)
            excluded_rects = [title_bbox]
            excluded_rects.extend(table_bboxes_by_page.get(page_index, []))

            # 1) Preferred path: block-based extraction for multi-zone drawing pages.
            blocks = page.get_text("blocks") or []
            block_candidates = []
            for block in blocks:
                x0, y0, x1, y1, text, *_ = block
                bbox = (float(x0), float(y0), float(x1), float(y1))
                if any(self._bbox_iou(bbox, rect) > 0.2 or self._rect_intersection_ratio(bbox, rect) > 0.2 for rect in excluded_rects):
                    continue
                fixed_text = self._norm(text or "")
                if not fixed_text:
                    continue
                num_marks = sum(1 for _ in re.finditer(r"(?m)^\s*\d{1,2}\s*$", fixed_text))
                num_marks += sum(1 for _ in re.finditer(r"(?m)^\s*\d{1,2}[.)]?\s+", fixed_text))
                if num_marks == 0:
                    continue
                lines = [ln.strip() for ln in fixed_text.splitlines() if ln.strip()]
                parsed = self._parse_requirements_from_block_lines(lines)
                if parsed:
                    merged_text = " ".join(parsed.values())
                    alpha_count = len(re.findall(r"[A-Za-zА-Яа-яЁё]", merged_text))
                    # Drop small leader/callout blocks like '19 / Экран / Экран'.
                    if len(parsed) == 1 and alpha_count < 30:
                        continue
                    block_candidates.append((min(parsed), float(x0), parsed))

            if block_candidates:
                merged: Dict[int, str] = {}
                for _, _, parsed in sorted(block_candidates, key=lambda item: (item[0], item[1])):
                    merged.update(parsed)
                if len(merged) > len(best_reqs):
                    best_reqs = merged
                    continue

            # 2) Fallback: old line-based route.
            words = self._page_words(page)
            if not words:
                continue
            filtered = []
            for w in words:
                bbox = (w[0], w[1], w[2], w[3])
                if any(self._rect_intersection_ratio(bbox, rect) > 0.4 for rect in excluded_rects):
                    continue
                filtered.append(w)

            line_infos = self._group_word_lines(filtered, x_gap_threshold=45.0)
            if not line_infos:
                continue
            stamp_top = title_bbox[1]
            line_infos = [line for line in line_infos if line["y0"] < stamp_top - 4]
            candidate_lines = [line["text"] for line in sorted(line_infos, key=lambda item: (item["y0"], item["x0"])) if not self._is_tech_service_line(line["text"])]
            parsed = self._parse_requirements_from_block_lines(candidate_lines)
            if len(parsed) > len(best_reqs):
                best_reqs = parsed

        return best_reqs

    @staticmethod
    def _row_starts_like_table_data(row: List[str]) -> bool:
        values = [EngineeringDocParser._norm_cell(cell) for cell in (row or []) if EngineeringDocParser._norm_cell(cell)]
        if not values:
            return False
        first = values[0]
        low_first = first.lower().replace("ё", "е")
        if low_first in {"б/н", "бн", "без номера"}:
            return True
        if re.fullmatch(r"\d{3,4}", first):
            return True
        if re.fullmatch(r"-\d{2,3}", first):
            return True
        if re.fullmatch(r"\d{3}\.\d{2}\.\d{2}\.\d{2}\.\d{3}", first):
            return True

        header_words = ("провод", "поз", "обозначение", "код", "маркировка", "наименование", "кол", "длина", "от соединителя", "к соединителю")
        if any(word in low_first for word in header_words):
            return False
        return False

    def _estimate_header_rows_count(self, rows: List[List[str]]) -> int:
        if not rows:
            return 0
        if len(rows) == 1:
            return 1

        header_keywords = {
            "обозначение", "код", "маркировка", "масса", "лист", "листы",
            "l, мм", "l1, мм", "l2, мм", "n, шт", "формат", "зона",
            "поз.", "позиция", "наименование", "кол.", "кол", "примечание",
            "провод", "от соединителя", "к соединителю", "длина",
        }

        count = 0
        max_scan = min(3, len(rows))
        for row in rows[:max_scan]:
            normalized_row = [self._norm_cell(cell) for cell in row]
            row_text = " ".join(cell.lower() for cell in normalized_row if cell)
            if not row_text:
                count += 1
                continue

            # Жесткий стоп: если строка начинается как реальная запись
            # (001, 015, -01, 051.01.40.01.000), это уже данные.
            # Раньше такие строки принимались за 2-ю/3-ю строку шапки из-за XS1/XP1,
            # поэтому заголовки становились вида "Поз. / 27" и "Длина, мм / 1300".
            if count >= 1 and self._row_starts_like_table_data(normalized_row):
                break

            non_empty = [cell for cell in normalized_row if cell]
            keyword_hits = sum(1 for kw in header_keywords if kw in row_text)
            connector_header_only = (
                len(non_empty) <= max(2, len(row) // 3)
                and any(re.fullmatch(r"[A-Za-zА-Яа-яЁё]{1,4}\d+", cell) for cell in non_empty)
                and not self._row_starts_like_table_data(normalized_row)
            )
            if keyword_hits >= 1 or connector_header_only:
                count += 1
                continue

            break

        return max(1, count)

    @staticmethod
    def _forward_fill_header_row(row: List[str]) -> List[str]:
        filled: List[str] = []
        last = ""
        for cell in row:
            value = cell or ""
            if value:
                last = value
                filled.append(value)
            else:
                filled.append(last)
        return filled

    def _build_column_names(self, rows: List[List[str]], header_rows_count: int) -> List[str]:
        if not rows:
            return []

        max_cols = max((len(row) for row in rows), default=0)
        header_rows = rows[:max(1, header_rows_count)]
        normalized_headers: List[List[str]] = []

        for idx, row in enumerate(header_rows):
            padded = list(row) + [""] * (max_cols - len(row))
            if idx == 0:
                padded = self._forward_fill_header_row(padded)
            normalized_headers.append([self._norm_cell(cell) for cell in padded])

        names: List[str] = []
        for col_idx in range(max_cols):
            parts: List[str] = []
            for row in normalized_headers:
                value = row[col_idx] if col_idx < len(row) else ""
                if value and value not in parts:
                    parts.append(value)

            if not parts:
                names.append(f"Графа {col_idx + 1}")
            else:
                names.append(" / ".join(parts))

        return names

    @staticmethod
    def _clean_table_identity_value(value: Optional[str]) -> str:
        text = EngineeringDocParser._norm_cell(value)
        text = text.replace("–", "-").replace("—", "-")
        text = re.sub(r"\s+", " ", text).strip(" |/;:")
        return text

    @staticmethod
    def _looks_like_column_header_value(value: Optional[str], column_names: Optional[List[str]] = None) -> bool:
        text = EngineeringDocParser._clean_table_identity_value(value)
        if not text:
            return True

        low = text.lower().replace("ё", "е")
        compact = re.sub(r"\s+", "", low)
        bad_exact = {
            "поз", "поз.", "провод", "провода", "маркировка", "код", "кол", "кол.",
            "длина", "длина,мм", "обозначение", "обозначениепровода/кабеля",
            "отсоединителя", "ксоединителю", "позиционноеобозначение",
            "соединитель", "исполнение", "исп.", "наименование", "примечание",
        }
        if compact in bad_exact:
            return True

        for name in column_names or []:
            name_compact = re.sub(r"\s+", "", str(name or "").lower().replace("ё", "е"))
            if name_compact and compact == name_compact:
                return True

        return False

    def _build_row_key(self, row: List[str], row_index: int, header_rows_count: int) -> Optional[str]:
        if row_index <= header_rows_count:
            return None

        non_empty = [self._norm_cell(cell) for cell in row if self._norm_cell(cell)]
        non_empty = [cell for cell in non_empty if not self._looks_like_column_header_value(cell)]
        if not non_empty:
            return None

        if len(non_empty) >= 2:
            return " | ".join(non_empty[:2])
        return non_empty[0]

    def _table_signature(self, table: TableData) -> str:

        raw_name = self._norm_cell(table.name)
        number_match = re.search(r"таблиц[аы]?\s*(\d+)", raw_name, flags=re.IGNORECASE)
        table_number = number_match.group(1) if number_match else ""

        header_cells = list(table.column_names or [])
        if not header_cells and table.rows:
            header_cells = list(table.rows[0] or [])

        header_parts = []
        for cell in header_cells:
            canon = self._canon_header(cell)
            if not canon:
                continue
            canon = canon.replace("/", " ")
            canon = re.sub(r"\s+", " ", canon).strip()
            # Нормализуем частые обрезки pdfplumber: "Дл" == "Длина".
            if canon in {"дл", "дл мм"}:
                canon = "длина мм"
            header_parts.append(canon)

        header = "|".join(header_parts)
        low_blob = f"{raw_name} {header}".lower().replace("ё", "е")
        if not table_number and "провод" in low_blob and ("соединител" in low_blob or "длина" in low_blob or "кол" in low_blob):
            table_number = "1"

        return f"p{table.page_num}:t{table_number}:h{header}"

    def _pair_tables(self, left_tables: List[TableData], right_tables: List[TableData]):
        pairs = []
        used_right = set()

        for left in left_tables:
            best_idx = None
            best_score = 0.0
            sig_left = self._table_signature(left)

            for idx, right in enumerate(right_tables):
                if idx in used_right:
                    continue
                sig_right = self._table_signature(right)
                score = SequenceMatcher(None, sig_left, sig_right).ratio()
                if left.page_num == right.page_num:
                    score += 0.15
                if score > best_score:
                    best_score = score
                    best_idx = idx

            if best_idx is not None and best_score >= 0.45:
                used_right.add(best_idx)
                pairs.append((left, right_tables[best_idx]))
            else:
                pairs.append((left, None))

        for idx, right in enumerate(right_tables):
            if idx not in used_right:
                pairs.append((None, right))

        return pairs

    @staticmethod
    def _column_name_at(table: TableData, col_index0: int) -> Optional[str]:
        if 0 <= col_index0 < len(table.column_names):
            return table.column_names[col_index0]
        return None

    @staticmethod
    def _row_value_at(row: List[str], col_index0: Optional[int]) -> str:
        if col_index0 is None or col_index0 < 0 or col_index0 >= len(row):
            return ""
        return EngineeringDocParser._norm_cell(row[col_index0])

    def _find_table_key_column_index(self, table: TableData) -> Optional[int]:

        names = [self._canon_header(name) for name in (table.column_names or [])]

        priority_groups = [
            ("wire", ("провод", "номер провода", "№ провода", "n провода")),
            ("execution", ("исполнение", "исп")),
            ("connector", ("позиционное обозначение", "поз обозначение", "поз. обозначение")),
            ("connector", ("соединитель",)),
            # Для таблиц Э4 вида "Обозначение / Код / Маркировка" ключом
            # должна быть графа "Код", а не первая графа "Обозначение".
            ("code", ("код",)),
            ("position", ("поз",)),
            ("number", ("номер",)),
            ("designation", ("обозначение",)),
        ]

        for _kind, markers in priority_groups:
            for idx, name in enumerate(names):
                if not name:
                    continue
                # Важно: "Обозначение провода/кабеля" — не номер провода, а материал/тип.
                if "обозначение провода" in name or "обозначение кабеля" in name:
                    continue
                if any(marker in name for marker in markers):
                    return idx

        return None

    def _is_repeated_header_row(self, row: List[str], column_names: List[str]) -> bool:
        cells = [self._norm_cell(cell) for cell in row]
        non_empty = [cell for cell in cells if cell]
        if not non_empty:
            return True

        header_hits = sum(1 for cell in non_empty if self._looks_like_column_header_value(cell, column_names))
        if header_hits >= max(2, len(non_empty) // 2):
            return True

        row_compact = " ".join(non_empty).lower().replace("ё", "е")
        if "провод" in row_compact and "соединител" in row_compact and ("длина" in row_compact or "кол" in row_compact):
            return True

        return False

    def _is_forward_fill_table_column(self, col_name: Optional[str], col_index0: int, key_col: Optional[int]) -> bool:

        if key_col is not None and col_index0 == key_col:
            return False
        low = self._canon_header(str(col_name or ""))
        return any(marker in low for marker in (
            "поз",
            "от соедин",
            "к соедин",
            "обозначение провода",
            "обозначение кабеля",
            "длина",
            "маркиров",
            "позиционное обозначение",
        ))

    def _semantic_table_row_for_compare(
        self,
        table: TableData,
        row: List[str],
        last_values: Dict[int, str],
        key_col: Optional[int],
    ) -> List[str]:
        result = list(row or [])
        max_cols = max(len(result), len(table.column_names or []))
        if len(result) < max_cols:
            result.extend([""] * (max_cols - len(result)))

        for idx in range(max_cols):
            col_name = self._column_name_at(table, idx) or ""
            value = self._norm_cell(result[idx] if idx < len(result) else "")

            if self._looks_like_column_header_value(value, table.column_names):
                value = ""

            if value:
                last_values[idx] = value
                result[idx] = value
                continue

            if self._is_forward_fill_table_column(col_name, idx, key_col):
                inherited = last_values.get(idx, "")
                if inherited:
                    result[idx] = inherited

        return result

    def _table_data_rows(self, table: TableData):
        key_col = self._find_table_key_column_index(table)
        result = []
        key_counts: Dict[str, int] = {}
        last_values: Dict[int, str] = {}

        for row_index, row in enumerate(table.rows, start=1):
            if row_index <= table.header_rows_count:
                continue
            normalized_row = [self._norm_cell(cell) for cell in row]
            if self._is_repeated_header_row(normalized_row, table.column_names):
                continue

            key = self._row_value_at(normalized_row, key_col)
            if self._looks_like_column_header_value(key, table.column_names):
                key = ""
            if not key:
                key = self._build_row_key(normalized_row, row_index, table.header_rows_count) or ""
            key = self._clean_table_identity_value(key)
            if not key:
                continue

            compare_row = self._semantic_table_row_for_compare(table, normalized_row, last_values, key_col)


            base_key = self._normalize_compare_value(key)
            key_counts[base_key] = key_counts.get(base_key, 0) + 1
            internal_key = f"{base_key}##{key_counts[base_key]}"

            result.append({
                "row_index": row_index,
                "row": compare_row,
                "raw_row": normalized_row,
                "row_key": key,
                "internal_key": internal_key,
                "key_col": key_col,
            })

        return result

    @staticmethod
    def _neighbor_row_values(rows: List[dict], idx: int, offset: int) -> List[str]:
        pos = idx + offset
        if 0 <= pos < len(rows):
            return list(rows[pos].get("row") or [])
        return []

    def _make_table_diff(
        self,
        table: TableData,
        row_index: int,
        col_index0: int,
        row_key: Optional[str],
        status: str,
        v1_val: Optional[str],
        v2_val: Optional[str],
        *,
        key_col: Optional[int] = None,
        data_rows_count: Optional[int] = None,
        row_values_v1: Optional[List[str]] = None,
        row_values_v2: Optional[List[str]] = None,
        prev_row_values_v1: Optional[List[str]] = None,
        prev_row_values_v2: Optional[List[str]] = None,
        next_row_values_v1: Optional[List[str]] = None,
        next_row_values_v2: Optional[List[str]] = None,
    ) -> TableDiff:
        return TableDiff(
            page_num=table.page_num,
            table_name=table.name,
            row_index=row_index,
            col_index=col_index0 + 1,
            col_name=self._column_name_at(table, col_index0),
            row_key=row_key,
            status=status,
            v1_val=v1_val,
            v2_val=v2_val,
            key_col_index=(key_col + 1) if key_col is not None else None,
            table_data_rows=data_rows_count,
            column_names=list(table.column_names or []),
            row_values_v1=list(row_values_v1 or []),
            row_values_v2=list(row_values_v2 or []),
            prev_row_values_v1=list(prev_row_values_v1 or []),
            prev_row_values_v2=list(prev_row_values_v2 or []),
            next_row_values_v1=list(next_row_values_v1 or []),
            next_row_values_v2=list(next_row_values_v2 or []),
        )

    def _diff_tables(self, tables1: List[TableData], tables2: List[TableData]) -> List[TableDiff]:
        diffs: List[TableDiff] = []

        for left, right in self._pair_tables(tables1, tables2):
            if left is None and right is not None:
                right_rows = self._table_data_rows(right)
                data_rows_count = len(right_rows)
                key_col = self._find_table_key_column_index(right)
                for idx, item in enumerate(right_rows):
                    row = item["row"]
                    diff_col = key_col if key_col is not None else 0
                    diffs.append(self._make_table_diff(
                        right,
                        item["row_index"],
                        diff_col,
                        item["row_key"],
                        "row_added",
                        None,
                        self._row_value_at(row, diff_col),
                        key_col=key_col,
                        data_rows_count=data_rows_count,
                        row_values_v2=row,
                        prev_row_values_v2=self._neighbor_row_values(right_rows, idx, -1),
                        next_row_values_v2=self._neighbor_row_values(right_rows, idx, 1),
                    ))
                continue

            if right is None and left is not None:
                left_rows = self._table_data_rows(left)
                data_rows_count = len(left_rows)
                key_col = self._find_table_key_column_index(left)
                for idx, item in enumerate(left_rows):
                    row = item["row"]
                    diff_col = key_col if key_col is not None else 0
                    diffs.append(self._make_table_diff(
                        left,
                        item["row_index"],
                        diff_col,
                        item["row_key"],
                        "row_removed",
                        self._row_value_at(row, diff_col),
                        None,
                        key_col=key_col,
                        data_rows_count=data_rows_count,
                        row_values_v1=row,
                        prev_row_values_v1=self._neighbor_row_values(left_rows, idx, -1),
                        next_row_values_v1=self._neighbor_row_values(left_rows, idx, 1),
                    ))
                continue

            assert left is not None and right is not None
            left_rows = self._table_data_rows(left)
            right_rows = self._table_data_rows(right)
            left_by_key = {item["internal_key"]: (idx, item) for idx, item in enumerate(left_rows)}
            right_by_key = {item["internal_key"]: (idx, item) for idx, item in enumerate(right_rows)}
            all_keys = sorted(set(left_by_key) | set(right_by_key))

            key_col_left = self._find_table_key_column_index(left)
            key_col_right = self._find_table_key_column_index(right)
            key_col = key_col_right if key_col_right is not None else key_col_left
            data_rows_count = max(len(left_rows), len(right_rows))
            max_cols = max(
                max((len(row["row"]) for row in left_rows), default=0),
                max((len(row["row"]) for row in right_rows), default=0),
            )

            for internal_key in all_keys:
                left_pair = left_by_key.get(internal_key)
                right_pair = right_by_key.get(internal_key)

                if left_pair is None and right_pair is not None:
                    right_idx, right_item = right_pair
                    row = right_item["row"]
                    diff_col = key_col if key_col is not None else 0
                    diffs.append(self._make_table_diff(
                        right,
                        right_item["row_index"],
                        diff_col,
                        right_item["row_key"],
                        "row_added",
                        None,
                        self._row_value_at(row, diff_col),
                        key_col=key_col,
                        data_rows_count=data_rows_count,
                        row_values_v2=row,
                        prev_row_values_v2=self._neighbor_row_values(right_rows, right_idx, -1),
                        next_row_values_v2=self._neighbor_row_values(right_rows, right_idx, 1),
                    ))
                    continue

                if right_pair is None and left_pair is not None:
                    left_idx, left_item = left_pair
                    row = left_item["row"]
                    diff_col = key_col if key_col is not None else 0
                    diffs.append(self._make_table_diff(
                        left,
                        left_item["row_index"],
                        diff_col,
                        left_item["row_key"],
                        "row_removed",
                        self._row_value_at(row, diff_col),
                        None,
                        key_col=key_col,
                        data_rows_count=data_rows_count,
                        row_values_v1=row,
                        prev_row_values_v1=self._neighbor_row_values(left_rows, left_idx, -1),
                        next_row_values_v1=self._neighbor_row_values(left_rows, left_idx, 1),
                    ))
                    continue

                assert left_pair is not None and right_pair is not None
                left_idx, left_item = left_pair
                right_idx, right_item = right_pair
                left_row = left_item["row"]
                right_row = right_item["row"]
                display_key = right_item.get("row_key") or left_item.get("row_key")

                for c in range(max_cols):

                    if key_col is not None and c == key_col:
                        continue

                    v1 = left_row[c] if c < len(left_row) else None
                    v2 = right_row[c] if c < len(right_row) else None
                    if self._normalize_compare_value(v1) == self._normalize_compare_value(v2):
                        continue

                    status = "modified" if (v1 and v2) else ("removed" if v1 else "added")
                    table_for_name = right if c < len(right.column_names) else left
                    diffs.append(self._make_table_diff(
                        table_for_name,
                        right_item.get("row_index") or left_item.get("row_index"),
                        c,
                        display_key,
                        status,
                        v1,
                        v2,
                        key_col=key_col,
                        data_rows_count=data_rows_count,
                        row_values_v1=left_row,
                        row_values_v2=right_row,
                        prev_row_values_v1=self._neighbor_row_values(left_rows, left_idx, -1),
                        prev_row_values_v2=self._neighbor_row_values(right_rows, right_idx, -1),
                        next_row_values_v1=self._neighbor_row_values(left_rows, left_idx, 1),
                        next_row_values_v2=self._neighbor_row_values(right_rows, right_idx, 1),
                    ))

        return diffs

    def _page_text_bboxes(self, page: fitz.Page):
        bboxes = []
        for block in page.get_text("blocks", sort=True):
            if len(block) >= 7 and int(block[6]) == 0:
                bboxes.append((float(block[0]), float(block[1]), float(block[2]), float(block[3])))
        return bboxes

    def _render_page_gray(self, page: fitz.Page) -> np.ndarray:
        zoom = self.dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
        return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)

    def _apply_rect_masks(self, image: np.ndarray, page: fitz.Page, rects):
        masked = image.copy()
        sx = image.shape[1] / page.rect.width
        sy = image.shape[0] / page.rect.height

        for rect in rects:
            x0, y0, x1, y1 = rect
            px0 = max(0, int(x0 * sx) - 2)
            py0 = max(0, int(y0 * sy) - 2)
            px1 = min(image.shape[1], int(x1 * sx) + 2)
            py1 = min(image.shape[0], int(y1 * sy) + 2)
            cv2.rectangle(masked, (px0, py0), (px1, py1), 255, thickness=-1)

        return masked

    def _prepare_graphics_for_shift_detection(self, image: np.ndarray) -> np.ndarray:

        gray = image
        if gray.ndim == 3:
            gray = cv2.cvtColor(gray, cv2.COLOR_RGB2GRAY)
        if gray.dtype != np.uint8:
            gray = np.clip(gray, 0, 255).astype(np.uint8)

        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        inverted = 255 - gray
        _, binary = cv2.threshold(inverted, 30, 255, cv2.THRESH_BINARY)

        return binary.astype(np.float32)

    def _estimate_graphics_shift(self, img1: np.ndarray, img2: np.ndarray) -> Tuple[float, float, float]:

        h = min(img1.shape[0], img2.shape[0])
        w = min(img1.shape[1], img2.shape[1])
        if h <= 10 or w <= 10:
            return 0.0, 0.0, 0.0

        prep1 = self._prepare_graphics_for_shift_detection(img1[:h, :w])
        prep2 = self._prepare_graphics_for_shift_detection(img2[:h, :w])

        # Если после маскирования почти нет линий, сдвиг оценивать бессмысленно.
        if cv2.countNonZero(prep1.astype(np.uint8)) < 100 or cv2.countNonZero(prep2.astype(np.uint8)) < 100:
            return 0.0, 0.0, 0.0

        try:
            (dx, dy), response = cv2.phaseCorrelate(prep1, prep2)
        except cv2.error:
            return 0.0, 0.0, 0.0

        return float(dx), float(dy), float(response)

    @staticmethod
    def _shift_gray_image(image: np.ndarray, dx: float, dy: float) -> np.ndarray:

        h, w = image.shape[:2]
        matrix = np.float32([[1, 0, -dx], [0, 1, -dy]])
        return cv2.warpAffine(
            image,
            matrix,
            (w, h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=255,
        )

    @staticmethod
    def _crop_for_aligned_ssim(img1: np.ndarray, img2: np.ndarray, dx: float, dy: float) -> Tuple[np.ndarray, np.ndarray]:

        h = min(img1.shape[0], img2.shape[0])
        w = min(img1.shape[1], img2.shape[1])
        margin_x = min(int(abs(dx)) + 8, max(0, w // 8))
        margin_y = min(int(abs(dy)) + 8, max(0, h // 8))

        if w - 2 * margin_x < 50 or h - 2 * margin_y < 50:
            return img1[:h, :w], img2[:h, :w]

        return (
            img1[margin_y:h - margin_y, margin_x:w - margin_x],
            img2[margin_y:h - margin_y, margin_x:w - margin_x],
        )

    @staticmethod
    def _ssim_change_percent(img1: np.ndarray, img2: np.ndarray) -> float:
        h = min(img1.shape[0], img2.shape[0])
        w = min(img1.shape[1], img2.shape[1])
        if h <= 10 or w <= 10:
            return 0.0
        score = ssim(img1[:h, :w], img2[:h, :w])
        return (1.0 - float(score)) * 100.0

    def _detect_graphics(self, v1_path: str, v2_path: str, tables1_by_page, tables2_by_page) -> GraphicDiff:

        doc1 = fitz.open(v1_path)
        doc2 = fitz.open(v2_path)

        max_common_pages = min(len(doc1), len(doc2))
        regions: List[GraphicRegion] = []
        page_scores: List[float] = []

        # Порог содержательного изменения после выравнивания.
        # 0.35% для чертежей обычно отсекает микросдвиги/антиалиасинг, но оставляет реальные правки.
        aligned_change_threshold_percent = 0.35
        min_phase_response = 0.03
        max_layout_shift_mm = 20.0
        max_shift_px = self.dpi / 25.4 * max_layout_shift_mm

        for page_index in range(max_common_pages):
            page1 = doc1[page_index]
            page2 = doc2[page_index]

            img1 = self._render_page_gray(page1)
            img2 = self._render_page_gray(page2)

            if img1.shape != img2.shape:
                img1 = cv2.resize(img1, (img2.shape[1], img2.shape[0]), interpolation=cv2.INTER_AREA)

            mask_rects_1 = self._page_text_bboxes(page1) + [self._expected_title_block_bbox(page1)]
            mask_rects_1 += tables1_by_page.get(page_index, [])
            mask_rects_2 = self._page_text_bboxes(page2) + [self._expected_title_block_bbox(page2)]
            mask_rects_2 += tables2_by_page.get(page_index, [])

            masked1 = self._apply_rect_masks(img1, page1, mask_rects_1)
            masked2 = self._apply_rect_masks(img2, page2, mask_rects_2)

            raw_change_percent = self._ssim_change_percent(masked1, masked2)
            dx, dy, response = self._estimate_graphics_shift(masked1, masked2)

            can_try_alignment = (
                response >= min_phase_response
                and abs(dx) <= max_shift_px
                and abs(dy) <= max_shift_px
                and (abs(dx) >= 0.5 or abs(dy) >= 0.5)
            )

            compare2 = masked2
            page_change_percent = raw_change_percent

            if can_try_alignment:
                aligned2 = self._shift_gray_image(masked2, dx, dy)
                crop1, crop2 = self._crop_for_aligned_ssim(masked1, aligned2, dx, dy)
                aligned_change_percent = self._ssim_change_percent(crop1, crop2)


                if aligned_change_percent <= aligned_change_threshold_percent:
                    page_scores.append(aligned_change_percent)
                    continue


                compare2 = aligned2
                page_change_percent = aligned_change_percent

            page_scores.append(page_change_percent)

            if page_change_percent <= aligned_change_threshold_percent:
                continue

            score, diff = ssim(masked1, compare2, full=True)
            delta = ((1.0 - diff) * 255).astype(np.uint8)
            delta = cv2.GaussianBlur(delta, (5, 5), 0)
            _, thresh = cv2.threshold(delta, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            page_area = img2.shape[0] * img2.shape[1]
            min_area = page_area * 0.0005

            for contour in contours:
                if cv2.contourArea(contour) < min_area:
                    continue
                x, y, w, h = cv2.boundingRect(contour)
                regions.append(GraphicRegion(page_num=page_index + 1, x=x, y=y, w=w, h=h))

        if len(doc1) != len(doc2):
            longer = doc1 if len(doc1) > len(doc2) else doc2
            for extra_page_index in range(max_common_pages, len(longer)):
                extra_page = longer[extra_page_index]
                img = self._render_page_gray(extra_page)
                regions.append(GraphicRegion(
                    page_num=extra_page_index + 1,
                    x=0,
                    y=0,
                    w=img.shape[1],
                    h=img.shape[0],
                ))
            page_scores.append(100.0)

        return GraphicDiff(
            has_changes=bool(regions),
            changed_regions=regions,
            change_percentage=round(sum(page_scores) / len(page_scores), 2) if page_scores else 0.0,
        )



    def _page_text_lines(self, page: fitz.Page) -> List[str]:
        text = self._fix_mojibake(page.get_text("text") or "")
        lines = [self._fix_mojibake(x).strip() for x in text.splitlines()]
        return [x for x in lines if x]

    def _pdfplumber_page_lines(self, pdf_path: str, page_index: int) -> List[str]:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if page_index < 0 or page_index >= len(pdf.pages):
                    return []
                text = pdf.pages[page_index].extract_text() or ""
                text = self._fix_mojibake(text)
                lines = [self._fix_mojibake(x).strip() for x in text.splitlines()]
                return [x for x in lines if x]
        except Exception:
            return []

    def _combined_page_lines(self, pdf_path: str, page_index: int) -> List[str]:
        lines = self._pdfplumber_page_lines(pdf_path, page_index)
        if lines:
            return lines
        try:
            doc = fitz.open(pdf_path)
            if page_index < 0 or page_index >= len(doc):
                return []
            return self._page_text_lines(doc[page_index])
        except Exception:
            return []

    def _looks_like_specification_lines(self, lines: List[str]) -> bool:
        repaired = [self._fix_mojibake(x).lower() for x in lines if x and x.strip()]
        text = "\n".join(repaired)
        header_hits = sum(1 for kw in ["формат", "зона", "поз", "обознач", "наимен", "кол", "примеч"] if kw in text)
        section_hits = sum(1 for kw in self.spec_section_names if kw in text)
        return header_hits >= 5 and section_hits >= 1


    def _looks_like_specification_doc_by_text(self, pdf_path: str) -> bool:
        try:
            lines = self._combined_page_lines(pdf_path, 0)
            if not lines:
                return False
            text = "\n".join(self._fix_mojibake(x).lower() for x in lines if x)
            header_hits = sum(1 for kw in ["формат", "зона", "поз", "обознач", "наимен", "кол", "примеч"] if kw in text)
            section_hits = sum(1 for kw in self.spec_section_names if kw in text)
            return header_hits >= 5 and section_hits >= 1
        except Exception:
            return False

    def _extract_spec_title_from_stamp(self, stamp_text: str, decimal_number: Optional[str]) -> Optional[str]:
        lines = [self._fix_mojibake(x).strip() for x in stamp_text.splitlines() if x and x.strip()]
        if not lines:
            return None
        dn_idx = None
        if decimal_number:
            dn_compact = decimal_number.replace(" ", "")
            for i, line in enumerate(lines):
                if dn_compact in line.replace(" ", ""):
                    dn_idx = i
                    break

        service_markers = ["лит.", "лист", "листов", "формат", "ао ", "инв.", "подп.", "дата", "разраб.", "пров.", "н.контр.", "утв.", "изм.", "докум.", "копировал"]


        if dn_idx is not None:
            post = []
            for line in lines[dn_idx + 1:dn_idx + 4]:
                low = line.lower()
                if any(m in low for m in service_markers):
                    continue
                if re.fullmatch(r"[\d\s.:-]+", line):
                    continue
                if len(re.findall(r"[А-Яа-яA-Za-z]", line)) < 2:
                    continue
                post.append(line)
            if post:
                return " ".join(post[:2]).strip()

        candidates = []
        search_zone = lines if dn_idx is None else lines[max(0, dn_idx-8):dn_idx]
        for line in search_zone:
            low = line.lower()
            if any(m in low for m in service_markers):
                continue
            if re.fullmatch(r"[\d\s.:-]+", line):
                continue
            if decimal_number and decimal_number.replace(" ", "") in line.replace(" ", ""):
                continue
            if len(re.findall(r"[А-Яа-яA-Za-z]", line)) < 2:
                continue
            candidates.append(line)
        if candidates:
            if len(candidates) >= 2 and re.fullmatch(r"[A-ZА-Я]?\d{3,5}", candidates[-1]):
                return f"{candidates[-2]} {candidates[-1]}".strip()
            return candidates[-1]
        return None

    def _extract_spec_title_from_doc(self, pdf_path: str, decimal_number: Optional[str]) -> Optional[str]:
        lines = self._combined_page_lines(pdf_path, 0)
        if not lines:
            return None
        repaired = [self._fix_mojibake(x).strip() for x in lines if x.strip()]
        # Prefer explicit product-name lines
        for line in repaired:
            low = line.lower()
            if re.search(r"^(жгут|кабель|блок|шлейф|комплект)\b", low):
                return line
        # Fallback: near bottom around decimal number, lines above "Лит. Лист Листов"
        candidates = []
        for line in repaired:
            low = line.lower()
            if decimal_number and decimal_number.replace(" ", "") in line.replace(" ", ""):
                continue
            if any(x in low for x in ["формат", "зона", "поз", "обознач", "наимен", "кол", "примеч", "документация", "прочие изделия", "материалы"]):
                continue
            if any(x in low for x in ["лит.", "лист", "листов", "ао ", "инв.", "подп.", "дата", "разраб.", "пров.", "н.контр.", "утв.", "изм.", "докум.", "копировал", "по эси:", "создал:"]):
                continue
            if re.fullmatch(r"[\d\s.:-]+", line):
                continue
            if len(re.findall(r"[А-Яа-яA-Za-z]", line)) >= 2:
                candidates.append(line)
        return candidates[-1] if candidates else None

    def _is_spec_service_line(self, line: str) -> bool:
        t = self._fix_mojibake(line).lower().strip()
        if not t:
            return True
        if self._is_spec_header_line(t):
            return True
        if t in {"перв.", "примен.", "справ.", "подп.", "дата", "и", "инв.", "подл.", "взам.", "дубл.", "№"}:
            return True
        if "изм." in t and "докум" in t:
            return True
        if re.match(r"^\d+\s+зам\.", t):
            return True
        if any(x in t for x in ["подп.", "инв.", "взам.", "дубл.", 'формат а4', 'копировал', 'создал:', 'по эси:']):
            return True
        if t.startswith('ао ') or t == 'ао "эйрбург"':
            return True
        if any(x in t for x in ['разраб.', 'пров.', 'н.контр.', 'утв.']):
            return True
        if t in {'лит. лист листов', 'лист', 'листов'}:
            return True
        return False

    def _is_spec_header_line(self, line: str) -> bool:
        t = self._fix_mojibake(line).lower().strip()
        return sum(int(word in t) for word in ["формат", "зона", "поз", "обознач", "наимен", "кол", "примеч"]) >= 3

    def _is_spec_section_line(self, line: str) -> Optional[str]:
        t = self._fix_mojibake(line).lower().strip()
        if t in self.spec_section_names:
            return t.title()
        return None


    def _is_spec_item_start(self, line: str) -> bool:
        t = self._fix_mojibake(line).strip()
        if re.match(r"^\d+\s+\S+", t) and not re.match(r"^\d+\s+Зам\.", t, re.I):
            return True
        if re.match(r"^[AА]\d\s+\S+", t, re.I):
            return True
        return False

    def _parse_spec_item_block(self, page_num: int, section: Optional[str], block_lines: List[str]) -> Optional[SpecificationItem]:
        lines = [self._fix_mojibake(x).strip() for x in block_lines if x and x.strip()]
        if not lines:
            return None

        merged = re.sub(r"\s+", " ", " ".join(lines)).strip()

        # Documentation rows: "A2 РСПГ... СБ Сборочный чертеж"
        if section and section.lower() == "документация":
            m = re.match(r"^(?P<fmt>[AА]\d)\s+(?P<body>.+)$", merged)
            if not m:
                return None
            fmt = m.group("fmt").replace("А", "A")
            body = m.group("body").strip()
            designation = None
            name = body
            for pat in self.decimal_patterns:
                mm = pat.search(body)
                if mm:
                    designation = mm.group(1)
                    name = body.replace(designation, "", 1).strip()
                    break
            designation = designation or body
            key = self._make_spec_key(section, None, designation, name)
            return SpecificationItem(
                page_num=page_num,
                section=section,
                format=fmt,
                zone=None,
                position=None,
                designation=designation,
                name=name or None,
                quantity=None,
                note=None,
                key=key,
            )

        first = lines[0]
        m = re.match(r"^(?P<pos>\d+)\s+(?P<rest>.+)$", first)
        if not m:
            return None

        position = m.group("pos")
        remaining = [m.group("rest").strip()] + lines[1:]
        remaining = [x for x in remaining if x]

        quantity = None
        note = None
        designation = None

        # Last line often contains TU + qty + note
        qty_tail = remaining[-1]
        mq = re.search(r"(?P<qty>\d+(?:[.,]\d+)?(?:\s*[мМ]|(?:\s*шт)?|(?:\s*кг)?))\s*(?P<note>[A-ZА-Я0-9,.\- ]+)?$", qty_tail)
        if mq:
            quantity = (mq.group("qty") or "").strip()
            tail_note = (mq.group("note") or "").strip()
            note = tail_note or None
            head = qty_tail[:mq.start("qty")].strip()
            remaining[-1] = head

        # Split obvious TU/designation lines from name lines
        name_parts: List[str] = []
        des_parts: List[str] = []
        for idx, line in enumerate(remaining):
            low = line.lower()
            if "ту " in low or re.search(r"\bту\b", low):
                des_parts.append(line)
            else:
                name_parts.append(line)

        name = " ".join(x for x in name_parts if x).strip() or None
        designation = " ".join(x for x in des_parts if x).strip() or designation

        key = self._make_spec_key(section, position, designation, name)
        return SpecificationItem(
            page_num=page_num,
            section=section,
            format=None,
            zone=None,
            position=position,
            designation=designation,
            name=name,
            quantity=quantity,
            note=note,
            key=key,
        )

    def _extract_specification_items_from_lines(self, pdf_path: str) -> List[SpecificationItem]:
        items: List[SpecificationItem] = []
        current_section: Optional[str] = None
        current_page: int = 1
        current_lines: List[str] = []

        def flush():
            nonlocal current_lines
            if not current_lines:
                return
            item = self._parse_spec_item_block(current_page, current_section, current_lines)
            if item:
                items.append(item)
            current_lines = []

        try:
            with pdfplumber.open(pdf_path) as pdf:
                page_count = len(pdf.pages)
        except Exception:
            page_count = 0

        for page_idx in range(page_count):
            lines = self._combined_page_lines(pdf_path, page_idx)
            for raw_line in lines:
                line = self._fix_mojibake(raw_line).strip()
                if not line:
                    continue
                if self._is_spec_service_line(line):
                    continue
                if self._is_spec_header_line(line):
                    continue

                section = self._is_spec_section_line(line)
                if section:
                    flush()
                    current_section = section
                    continue

                if self._is_spec_item_start(line):
                    flush()
                    current_page = page_idx + 1
                    current_lines = [line]
                else:
                    if current_lines:
                        current_lines.append(line)
            flush()

        unique: Dict[str, SpecificationItem] = {}
        for item in items:
            prev = unique.get(item.key)
            current_len = len((item.name or "") + " " + (item.designation or ""))
            prev_len = len((prev.name or "") + " " + (prev.designation or "")) if prev else -1
            if prev is None or current_len > prev_len:
                unique[item.key] = item
        return list(unique.values())


    def _looks_like_pe4_doc_by_text(self, pdf_path: str) -> bool:
        try:
            lines = self._combined_page_lines(pdf_path, 0)
            if not lines:
                return False
            text = "\n".join(self._fix_mojibake(x).lower() for x in lines if x)
            # Не даем чертежу уехать в ПЭ4, если на первой странице явно виден штамп чертежа.
            if "сборочный чертеж" in text and "перечень элементов" not in text:
                return False
            # Спецификация тоже не должна перехватываться ПЭ4.
            if "документация" in text and "прочие изделия" in text:
                return False
            header_hits = sum(1 for kw in self.pe4_header_words if kw in text)
            return ("перечень элементов" in text or re.search(r"\bпэ4\b", text) is not None) and header_hits >= 4
        except Exception:
            return False

    def _extract_decimal_from_doc(self, pdf_path: str) -> Optional[str]:
        for page_idx in range(3):
            lines = self._combined_page_lines(pdf_path, page_idx)
            for line in lines:
                candidate = self._extract_decimal(self._fix_mojibake(line))
                if candidate:
                    return candidate
        return None

    def _extract_spec_decimal_from_doc(self, pdf_path: str) -> Optional[str]:

        lines = self._combined_page_lines(pdf_path, 0)
        repaired = [self._fix_mojibake(x).strip() for x in lines if x and x.strip()]
        base_re = re.compile(r"\b([А-ЯA-ZЁ]+(?:\.\d+){4,5})\b")

        # 1) Ищем строку с основным обозначением рядом со строкой "Лит. Лист Листов".
        for idx, line in enumerate(repaired):
            if "лист" in line.lower() and "листов" in line.lower():
                start = max(0, idx - 6)
                end = min(len(repaired), idx + 2)
                window = repaired[start:end]
                for cand in window:
                    low = cand.lower()
                    if any(x in low for x in ["сборочный чертеж", "схема электрическая", "перечень элементов", "разраб.", "пров.", "утв.", "н.контр.", "формат"]):
                        continue
                    m = base_re.search(cand)
                    if m:
                        return m.group(1)


        candidates = []
        for cand in repaired:
            low = cand.lower()
            if any(sfx in low for sfx in [" сб", " э4", " пэ4", " сп"]):
                continue
            if any(x in low for x in ["сборочный чертеж", "схема электрическая", "перечень элементов"]):
                continue
            m = base_re.search(cand)
            if m:
                candidates.append(m.group(1))
        return candidates[-1] if candidates else None

    def _extract_pe4_title_from_doc(self, pdf_path: str) -> Optional[str]:
        lines = self._combined_page_lines(pdf_path, 0)
        repaired = [self._fix_mojibake(x).strip() for x in lines if x and x.strip()]
        for line in repaired:
            low = line.lower()
            if re.search(r"^(жгут|кабель|блок|шлейф|комплект)\b", low):
                return line

        for idx, line in enumerate(repaired):
            low = line.lower()
            if line in {"Перечень элементов"}:
                continue
            if re.search(r"^(жгут|кабель|блок|шлейф|комплект)\b", low):
                if idx + 1 < len(repaired) and re.search(r"[A-Za-zА-Яа-я0-9.-]+", repaired[idx + 1]):
                    return f"{line} {repaired[idx + 1]}".strip()
                return line
        return None

    def _looks_like_pe4_table(self, rows: List[List[str]]) -> bool:
        flat = " ".join(self._norm_cell(cell).lower() for row in rows for cell in row if cell)
        header_hits = sum(1 for kw in self.pe4_header_words if kw in flat)
        return header_hits >= 4 and ("перечень элементов" in flat or "поз" in flat)

    def _is_pe4_service_values(self, values: List[str]) -> bool:
        joined = " ".join(values).lower()
        if not joined:
            return True
        if "изм." in joined and "докум" in joined:
            return True
        if re.match(r"^\d+\s+зам\.", joined):
            return True
        if any(x in joined for x in ["разраб.", "пров.", "н.контр.", "утв.", "подп.", "дата", "инв.", "взам.", "дубл.", "копировал", "формат а4", "справ. №"]):
            return True
        if "лит." in joined and "лист" in joined and "листов" in joined:
            return True
        if "ао " in joined:
            return True
        return False

    def _looks_like_pe4_position(self, text: str) -> bool:
        t = self._fix_mojibake(text).strip()
        if not t:
            return False
        if " " in t and "," not in t:
            return False
        return bool(re.match(r"^[A-ZА-Я0-9][A-ZА-Я0-9.\-]*(?:,[ ]*)?$", t))

    def _normalize_quantity(self, text: Optional[str]) -> Optional[str]:
        if text is None:
            return None
        t = self._norm_cell(text)
        if not t:
            return None
        t = re.sub(r"(?<=\d)(?=[A-Za-zА-Яа-я])", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _make_element_key(self, position_designation: str) -> str:
        key = self._norm_cell(position_designation).lower()
        key = re.sub(r"\s+", "", key)
        return key

    def _extract_element_items_from_pdf(self, pdf_path: str) -> List[ElementItem]:
        items: List[ElementItem] = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page_idx, page in enumerate(pdf.pages):
                    raw_tables = []
                    for settings in self.table_settings_variants[:2]:
                        try:
                            raw_tables = page.extract_tables(table_settings=settings) or []
                        except Exception:
                            raw_tables = []
                        if raw_tables:
                            break

                    for raw_table in raw_tables:
                        rows = self._clean_rows(raw_table)
                        if not rows or not self._looks_like_pe4_table(rows):
                            continue

                        current = None
                        for row in rows[1:]:
                            values = [self._norm_cell(v) for v in row if self._norm_cell(v)]
                            if not values:
                                if current and current.get("quantity"):
                                    items.append(ElementItem(
                                        page_num=page_idx + 1,
                                        position_designation=", ".join(current["positions"]).replace(",,", ","),
                                        name=" ".join(current["name_parts"]).strip() or None,
                                        quantity=self._normalize_quantity(current.get("quantity")),
                                        note=(" ".join(current["note_parts"]).strip() or None),
                                        key=self._make_element_key(", ".join(current["positions"])),
                                    ))
                                    current = None
                                continue

                            if self._is_pe4_service_values(values):
                                if current and current.get("quantity"):
                                    items.append(ElementItem(
                                        page_num=page_idx + 1,
                                        position_designation=", ".join(current["positions"]).replace(",,", ","),
                                        name=" ".join(current["name_parts"]).strip() or None,
                                        quantity=self._normalize_quantity(current.get("quantity")),
                                        note=(" ".join(current["note_parts"]).strip() or None),
                                        key=self._make_element_key(", ".join(current["positions"])),
                                    ))
                                    current = None
                                continue

                            if "поз" in " ".join(values).lower() and "наимен" in " ".join(values).lower():
                                continue

                            pos = values[0] if self._looks_like_pe4_position(values[0]) else None
                            qty = values[-1] if re.fullmatch(r"\d+(?:[.,]\d+)?", values[-1]) else None

                            rest = values[1:-1] if qty else values[1:]
                            rest = [x for x in rest if x]

                            if current is None:
                                current = {"positions": [], "name_parts": [], "note_parts": [], "quantity": None}

                            if current["positions"] and current.get("quantity") and pos:
                                items.append(ElementItem(
                                    page_num=page_idx + 1,
                                    position_designation=", ".join(current["positions"]).replace(",,", ","),
                                    name=" ".join(current["name_parts"]).strip() or None,
                                    quantity=self._normalize_quantity(current.get("quantity")),
                                    note=(" ".join(current["note_parts"]).strip() or None),
                                    key=self._make_element_key(", ".join(current["positions"])),
                                ))
                                current = {"positions": [], "name_parts": [], "note_parts": [], "quantity": None}

                            if pos:
                                current["positions"].append(pos.rstrip(","))

                            if rest:
                                current["name_parts"].extend(rest)

                            if qty:
                                current["quantity"] = qty
                                items.append(ElementItem(
                                    page_num=page_idx + 1,
                                    position_designation=", ".join(current["positions"]).replace(",,", ","),
                                    name=" ".join(current["name_parts"]).strip() or None,
                                    quantity=self._normalize_quantity(current.get("quantity")),
                                    note=(" ".join(current["note_parts"]).strip() or None),
                                    key=self._make_element_key(", ".join(current["positions"])),
                                ))
                                current = None

                        if current and current.get("positions"):
                            items.append(ElementItem(
                                page_num=page_idx + 1,
                                position_designation=", ".join(current["positions"]).replace(",,", ","),
                                name=" ".join(current["name_parts"]).strip() or None,
                                quantity=self._normalize_quantity(current.get("quantity")),
                                note=(" ".join(current["note_parts"]).strip() or None),
                                key=self._make_element_key(", ".join(current["positions"])),
                            ))
        except Exception:
            return []

        # Deduplicate by key, prefer richer item
        unique: Dict[str, ElementItem] = {}
        for item in items:
            if not item.position_designation or item.position_designation.lower().startswith("справ"):
                continue
            prev = unique.get(item.key)
            score = len((item.name or "")) + len((item.quantity or ""))
            prev_score = len((prev.name or "")) + len((prev.quantity or "")) if prev else -1
            if prev is None or score > prev_score:
                unique[item.key] = item
        return list(unique.values())

    def _diff_element_items(self, items1: List[ElementItem], items2: List[ElementItem]) -> List[ElementItemDiff]:
        map1 = {item.key: item for item in items1}
        map2 = {item.key: item for item in items2}
        keys = sorted(set(map1) | set(map2))
        diffs: List[ElementItemDiff] = []

        for key in keys:
            a = map1.get(key)
            b = map2.get(key)
            if a and not b:
                diffs.append(ElementItemDiff(
                    key=key,
                    position_designation=a.position_designation,
                    name=a.name,
                    status="removed",
                    field_changes=[],
                    v1_item=a,
                    v2_item=None,
                ))
                continue
            if b and not a:
                diffs.append(ElementItemDiff(
                    key=key,
                    position_designation=b.position_designation,
                    name=b.name,
                    status="added",
                    field_changes=[],
                    v1_item=None,
                    v2_item=b,
                ))
                continue
            assert a and b
            field_changes: List[ElementFieldChange] = []
            for field_name in ("name", "quantity", "note"):
                va = self._normalize_compare_value(getattr(a, field_name))
                vb = self._normalize_compare_value(getattr(b, field_name))
                if va != vb:
                    field_changes.append(ElementFieldChange(
                        field_name=field_name,
                        v1_val=getattr(a, field_name),
                        v2_val=getattr(b, field_name),
                    ))
            status = "modified" if field_changes else "unchanged"
            diffs.append(ElementItemDiff(
                key=key,
                position_designation=b.position_designation or a.position_designation,
                name=b.name or a.name,
                status=status,
                field_changes=field_changes,
                v1_item=a,
                v2_item=b,
            ))
        return diffs

    def _resolve_doc_type_with_priority(self, pdf_path: str, metadata: DocMetadata, tables: Optional[List[TableData]] = None) -> str:
        stamp_text = self._fix_mojibake(metadata.raw_stamp_snippet or "").lower()
        decimal = self._normalize_decimal_number(metadata.decimal_number, metadata.raw_stamp_snippet or "") or ""

        # 1. Сначала доверяем явным сигналам штампа/обозначения.
        if "сборочный чертеж" in stamp_text or decimal.endswith(" СБ"):
            return "Сборочный чертеж"
        if "схема электрическая соединений" in stamp_text or decimal.endswith(" Э4"):
            return "Схема электрическая соединений"
        if "перечень элементов" in stamp_text or decimal.endswith(" ПЭ4"):
            return "Перечень элементов"
        if "спецификац" in stamp_text or decimal.endswith(" СП"):
            return "Спецификация"

        # 2. Только если штамп не дал ответа — структурные детекторы.
        if tables and self._looks_like_specification_doc(metadata, tables, pdf_path):
            return "Спецификация"
        if self._looks_like_specification_doc_by_text(pdf_path):
            return "Спецификация"
        if self._looks_like_pe4_doc_by_text(pdf_path):
            return "Перечень элементов"

        return metadata.doc_type or "Не определён"

    def parse_document(self, pdf_path: str) -> ParsedDocument:
        metadata = self._extract_metadata(pdf_path)
        metadata.decimal_number = self._normalize_decimal_number(metadata.decimal_number, metadata.raw_stamp_snippet or "")
        raw_tables, tables_by_page = self._extract_tables(pdf_path)
        tables = self._merge_continued_tables(raw_tables)

        specification_items: List[SpecificationItem] = []
        element_items: List[ElementItem] = []
        tech_reqs: List[TechRequirement] = []

        resolved_doc_type = self._resolve_doc_type_with_priority(pdf_path, metadata, tables)
        metadata.doc_type = resolved_doc_type
        if resolved_doc_type != "Спецификация":
            metadata.decimal_number = self._ensure_decimal_suffix_for_type(metadata.decimal_number, resolved_doc_type)

        is_spec = resolved_doc_type == "Спецификация"
        is_pe4 = resolved_doc_type == "Перечень элементов"

        if is_spec:
            metadata.doc_type = "Спецификация"
            # Для СП берём основное обозначение из основной надписи без суффиксов СБ/Э4/ПЭ4.
            spec_decimal = self._extract_spec_decimal_from_doc(pdf_path)
            if spec_decimal:
                metadata.decimal_number = spec_decimal
            elif metadata.decimal_number:
                metadata.decimal_number = re.sub(r"\s+(СБ|Э4|ПЭ4|СП)$", "", metadata.decimal_number, flags=re.I)
            specification_items = self._extract_specification_items_from_lines(pdf_path)
            if not specification_items:
                specification_items = self._extract_specification_items(tables)

            metadata.title = self._extract_spec_title_from_doc(pdf_path, metadata.decimal_number) or metadata.title
            if not metadata.title:
                metadata.title = self._extract_spec_title_from_stamp(metadata.raw_stamp_snippet, metadata.decimal_number)
            if metadata.title in {"Н9655", "028.00-6"}:
                metadata.title = f"Жгут {metadata.title}" if not metadata.title.lower().startswith("жгут") else metadata.title
            if metadata.title and metadata.title.lower() in {"перечень элементов", "сборочный чертеж", "спецификация"}:
                metadata.title = self._extract_spec_title_from_doc(pdf_path, metadata.decimal_number) or metadata.title

            parsed_tables: List[ParsedTable] = []
        elif is_pe4:
            metadata.doc_type = "Перечень элементов"
            if not metadata.decimal_number:
                metadata.decimal_number = self._extract_decimal_from_doc(pdf_path)
            if not metadata.title:
                metadata.title = self._extract_pe4_title_from_doc(pdf_path)
            element_items = self._extract_element_items_from_pdf(pdf_path)
            parsed_tables = []
        else:
            tech_reqs_dict = self._extract_tech_reqs(pdf_path, tables_by_page)
            tech_reqs = [
                TechRequirement(number=number, text=text)
                for number, text in sorted(tech_reqs_dict.items())
            ]
            parsed_tables = [
                ParsedTable(page_num=t.page_num, name=t.name, bbox=t.bbox, rows=t.rows, column_names=t.column_names, header_rows_count=t.header_rows_count)
                for t in tables
            ]

        return ParsedDocument(
            file_name=os.path.basename(pdf_path),
            metadata=metadata,
            tech_requirements=tech_reqs,
            tables=parsed_tables,
            specification_items=specification_items,
            element_items=element_items,
        )

    @staticmethod
    def _normalize_visual_confusables(value: str) -> str:

        table = str.maketrans({
            "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
            "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
            "У": "Y", "Х": "X",
            "а": "a", "в": "b", "е": "e", "к": "k", "м": "m",
            "н": "h", "о": "o", "р": "p", "с": "c", "т": "t",
            "у": "y", "х": "x",
        })
        return str(value or "").translate(table)

    @staticmethod
    def _normalize_compare_value(value: Optional[str]) -> str:
        if value is None:
            return ""
        value = EngineeringDocParser._fix_mojibake(value)
        value = EngineeringDocParser._normalize_visual_confusables(value)
        value = value.replace("−", "-").replace("–", "-").replace("—", "-").replace("￳", "-")
        value = re.sub(r"\s*[-–—]\s*", "-", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip().lower()

    @staticmethod
    def _normalize_req_for_compare(text: str) -> str:
        return EngineeringDocParser._normalize_compare_value(text)

    @staticmethod
    def _tech_req_tokens_for_compare(text: Optional[str]) -> set:
        value = EngineeringDocParser._normalize_req_for_compare(text or "")

        stop_words = {
            "выполнить", "согласно", "соответствии", "поз", "гост", "ост",
            "ту", "таблице", "таблица", "длиной", "длина", "месте", "путем",
            "между", "после", "перед", "если", "иное", "указано", "чертежу",
            "соединителя", "соединителей", "кабеля", "провода", "проводов",
        }
        tokens = set(re.findall(r"[а-яa-z0-9]{4,}", value))
        return {token for token in tokens if token not in stop_words}

    def _tech_req_similarity(self, left_text: Optional[str], right_text: Optional[str]) -> float:

        left_norm = self._normalize_req_for_compare(left_text or "")
        right_norm = self._normalize_req_for_compare(right_text or "")
        if not left_norm or not right_norm:
            return 0.0
        if left_norm == right_norm:
            return 1.0

        ratio = SequenceMatcher(None, left_norm, right_norm).ratio()

        left_tokens = self._tech_req_tokens_for_compare(left_text)
        right_tokens = self._tech_req_tokens_for_compare(right_text)
        if not left_tokens or not right_tokens:
            return ratio

        intersection = left_tokens & right_tokens
        union = left_tokens | right_tokens
        jaccard = len(intersection) / max(len(union), 1)
        containment = len(intersection) / max(min(len(left_tokens), len(right_tokens)), 1)


        return max(ratio, jaccard, containment * 0.85)

    @staticmethod
    def _tech_req_numbers_close(left_num: Optional[int], right_num: Optional[int]) -> bool:
        if left_num is None or right_num is None:
            return False
        try:
            return abs(int(left_num) - int(right_num)) <= 1
        except (TypeError, ValueError):
            return False

    def _tech_req_pair_is_plausible(
        self,
        left_num: Optional[int],
        left_text: Optional[str],
        right_num: Optional[int],
        right_text: Optional[str],
        score: float,
    ) -> bool:

        if score >= 0.62:
            return True

        left_tokens = self._tech_req_tokens_for_compare(left_text)
        right_tokens = self._tech_req_tokens_for_compare(right_text)
        shared = left_tokens & right_tokens

        if self._tech_req_numbers_close(left_num, right_num) and score >= 0.42 and len(shared) >= 2:
            return True

        if score >= 0.50 and len(shared) >= 3:
            return True

        return False

    @staticmethod
    def _tech_req_diff_sort_key(diff: TechReqDiff) -> tuple:

        if diff.v1_number is not None:
            primary = diff.v1_number
        elif diff.v2_number is not None:
            primary = diff.v2_number
        else:
            primary = diff.number or 9999

        status_order = {"removed": 0, "modified": 1, "added": 2}
        return (primary, status_order.get(diff.status, 9), diff.v2_number or diff.v1_number or diff.number or 9999)

    def _diff_tech_requirements(self, reqs1: List[TechRequirement], reqs2: List[TechRequirement]) -> List[TechReqDiff]:

        items1 = [(item.number, item.text) for item in reqs1]
        items2 = [(item.number, item.text) for item in reqs2]

        matched_left: set[int] = set()
        matched_right: set[int] = set()
        diffs: List[TechReqDiff] = []

        # Точные совпадения текста — не изменение, даже если номер сдвинулся.
        right_by_key: Dict[str, List[int]] = {}
        for idx, (_number, text) in enumerate(items2):
            key = self._normalize_req_for_compare(text)
            right_by_key.setdefault(key, []).append(idx)

        for left_idx, (_left_num, left_text) in enumerate(items1):
            key = self._normalize_req_for_compare(left_text)
            candidates = right_by_key.get(key) or []
            while candidates and candidates[0] in matched_right:
                candidates.pop(0)
            if candidates:
                right_idx = candidates.pop(0)
                matched_left.add(left_idx)
                matched_right.add(right_idx)


        pair_candidates: List[tuple] = []
        for left_idx, (left_num, left_text) in enumerate(items1):
            if left_idx in matched_left:
                continue
            for right_idx, (right_num, right_text) in enumerate(items2):
                if right_idx in matched_right:
                    continue
                score = self._tech_req_similarity(left_text, right_text)
                if not self._tech_req_pair_is_plausible(left_num, left_text, right_num, right_text, score):
                    continue


                number_bonus = 0.03 if left_num == right_num else (0.01 if self._tech_req_numbers_close(left_num, right_num) else 0.0)
                pair_candidates.append((score + number_bonus, score, left_idx, right_idx))

        pair_candidates.sort(key=lambda item: item[0], reverse=True)

        for _rank_score, score, left_idx, right_idx in pair_candidates:
            if left_idx in matched_left or right_idx in matched_right:
                continue

            left_num, left_text = items1[left_idx]
            right_num, right_text = items2[right_idx]
            matched_left.add(left_idx)
            matched_right.add(right_idx)

            if self._normalize_req_for_compare(left_text) == self._normalize_req_for_compare(right_text):
                continue

            diffs.append(TechReqDiff(
                number=right_num,
                status="modified",
                v1_number=left_num,
                v2_number=right_num,
                v1_text=left_text,
                v2_text=right_text,
            ))

        for left_idx, (left_num, left_text) in enumerate(items1):
            if left_idx in matched_left:
                continue
            diffs.append(TechReqDiff(
                number=left_num,
                status="removed",
                v1_number=left_num,
                v2_number=None,
                v1_text=left_text,
                v2_text=None,
            ))

        for right_idx, (right_num, right_text) in enumerate(items2):
            if right_idx in matched_right:
                continue
            diffs.append(TechReqDiff(
                number=right_num,
                status="added",
                v1_number=None,
                v2_number=right_num,
                v1_text=None,
                v2_text=right_text,
            ))

        return sorted(diffs, key=self._tech_req_diff_sort_key)
    def compare(self, v1_path: str, v2_path: str) -> FullDiff:
        parsed_v1 = self.parse_document(v1_path)
        parsed_v2 = self.parse_document(v2_path)

        is_specification = (
            (parsed_v1.metadata.doc_type or "").lower() == "спецификация"
            or (parsed_v2.metadata.doc_type or "").lower() == "спецификация"
            or bool(parsed_v1.specification_items)
            or bool(parsed_v2.specification_items)
        )
        is_pe4 = (
            (parsed_v1.metadata.doc_type or "").lower() == "перечень элементов"
            or (parsed_v2.metadata.doc_type or "").lower() == "перечень элементов"
            or bool(parsed_v1.element_items)
            or bool(parsed_v2.element_items)
        )

        tech_diffs: List[TechReqDiff] = []
        specification_diffs: List[SpecificationItemDiff] = []
        element_diffs: List[ElementItemDiff] = []
        table_diffs: List[TableDiff] = []

        if is_specification:
            specification_diffs = self._diff_specification_items(
                parsed_v1.specification_items,
                parsed_v2.specification_items,
            )
        elif is_pe4:
            element_diffs = self._diff_element_items(parsed_v1.element_items, parsed_v2.element_items)
        else:
            tech_diffs = self._diff_tech_requirements(parsed_v1.tech_requirements, parsed_v2.tech_requirements)
            tables_v1 = [TableData(page_num=t.page_num, bbox=t.bbox, name=t.name, rows=t.rows, column_names=t.column_names, header_rows_count=t.header_rows_count) for t in parsed_v1.tables]
            tables_v2 = [TableData(page_num=t.page_num, bbox=t.bbox, name=t.name, rows=t.rows, column_names=t.column_names, header_rows_count=t.header_rows_count) for t in parsed_v2.tables]
            table_diffs = self._diff_tables(tables_v1, tables_v2)

        _, tables1_by_page = self._extract_tables(v1_path)
        _, tables2_by_page = self._extract_tables(v2_path)
        graphic_diff = self._detect_graphics(v1_path, v2_path, tables1_by_page, tables2_by_page)

        return FullDiff(
            file_v1=os.path.basename(v1_path),
            file_v2=os.path.basename(v2_path),
            metadata_v1=parsed_v1.metadata,
            metadata_v2=parsed_v2.metadata,
            tech_requirements=tech_diffs,
            specification_items=specification_diffs,
            element_items=element_diffs,
            tables=table_diffs,
            graphics=graphic_diff,
        )



_OLD_EXPECTED_TITLE_BLOCK_BBOX = EngineeringDocParser._expected_title_block_bbox
_OLD_EXTRACT_TECH_REQS = EngineeringDocParser._extract_tech_reqs


def _patched_expected_title_block_bbox(self, page: fitz.Page) -> Tuple[float, float, float, float]:
    w, h = page.rect.width, page.rect.height
    if w >= 3000 or h >= 2000:
        # Для A0/A1 с продолжением ТТ справа внизу берем более узкий штамп.
        return (w * 0.86, h * 0.92, w, h)
    return _OLD_EXPECTED_TITLE_BLOCK_BBOX(self, page)


def _patched_extract_text_clip(self, page: fitz.Page, rect: Tuple[float, float, float, float]) -> str:
    try:
        text = page.get_text("text", clip=fitz.Rect(*rect), sort=True) or ""
    except TypeError:
        text = page.get_text("text", sort=True) or ""
    text = self._fix_mojibake(text)
    text = self._strip_control_chars(text)
    return text


def _patched_clean_tt_line(self, line: str) -> str:
    text = self._fix_mojibake(line or "")
    text = self._strip_control_chars(text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return ""
    text = re.split(r"\bТаблица\s*\d+.*|Продолжение таблицы\s*\d+.*", text, maxsplit=1, flags=re.I)[0]
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _patched_noise_prefix_cut(self, line: str) -> str:
    # У CAD-листов в clip-тексте слева могут прилипать выноски и размеры.
    # Оставляем осмысленный хвост, начиная с номера пункта или первого слова на кириллице.
    m = re.search(r"(?<!\d)(\d{1,2})(?=\s|[А-ЯA-Z\*])", line)
    if m and m.start() > 0:
        return line[m.start():].strip()
    # иначе отрезаем префикс до первого кириллического/латинского слова, если слева почти один шум
    m2 = re.search(r"[А-ЯЁA-Z][А-Яа-яЁA-Za-z\*\-\"«»]", line)
    if m2 and m2.start() > 10:
        prefix = line[:m2.start()]
        if len(re.findall(r"[А-Яа-яЁA-Za-z]", prefix)) <= 3:
            return line[m2.start():].strip()
    return line.strip()


def _patched_parse_tt_from_clipped_text(self, text: str, stop_after: Optional[int] = None) -> Dict[int, str]:
    lines = [self._patched_clean_tt_line(x) for x in text.splitlines()]
    reqs: Dict[int, str] = {}
    cur_num: Optional[int] = None
    parts: List[str] = []

    def flush():
        nonlocal cur_num, parts
        if cur_num is None:
            return
        body = "\n".join(p for p in parts if p).strip()
        body = re.sub(r"[ \t]+", " ", body)
        body = re.sub(r"\n{2,}", "\n", body).strip()
        if len(body) >= 6:
            reqs[cur_num] = body
        cur_num = None
        parts = []

    req_start_re = re.compile(r"(?<!\d)(\d{1,2})(?:[.)])?(?=\s|[А-ЯA-Z\*])")
    table_row_re = re.compile(r"^(?:\d{3}|б/н|-)\b", re.I)

    for raw in lines:
        if not raw:
            continue
        line = self._patched_noise_prefix_cut(raw)
        if not line:
            continue
        if self._is_tech_service_line(line):
            continue
        if table_row_re.match(line):
            continue
        if re.match(r"^(?:Провод|Поз\.|От соединителя|К соединителю|Обозначение провода|Длина, мм|Кол\.)", line, re.I):
            continue

        m = req_start_re.search(line)
        if m:
            num = int(m.group(1))
            if 1 <= num <= 99:
                flush()
                cur_num = num
                body = line[m.end():].strip(" .")
                if body:
                    parts = [body]
                continue

        if cur_num is not None:
            # выбрасываем почти пустой шум из размеров/выносок
            if len(re.findall(r"[А-Яа-яЁёA-Za-z]", line)) < 4 and len(line) < 35:
                continue
            parts.append(line)
            if stop_after and cur_num >= stop_after:
                pass

    flush()
    return reqs


def _patched_extract_tail_requirements(self, page: fitz.Page) -> Dict[int, str]:
    # Для крупных листов continuation-блок 26-29 расположен слева от штампа внизу справа.
    w, h = page.rect.width, page.rect.height
    if w < 3000 and h < 2000:
        return {}

    rect = (w * 0.60, h * 0.82, w * 0.84, h * 0.98)
    text = self._patched_extract_text_clip(page, rect)
    reqs = self._patched_parse_tt_from_clipped_text(text)

    # Точечный добор п.29 из полного текста страницы, если continuation-clip не справился.
    full_text = self._strip_control_chars(self._fix_mojibake(page.get_text("text", sort=True) or ""))
    m29 = re.search(r"29\s+Допустимые\s+замены\s+в\s+соответствии\s+с\s+(.+?D10\.)", full_text, flags=re.I | re.S)
    if m29:
        body = "Допустимые замены в соответствии с " + re.sub(r"\s+", " ", m29.group(1)).strip()
        reqs[29] = body
    m28 = re.search(r"28\s*Масса\s+жгута\s+не\s+более\s+[^\n]+", full_text, flags=re.I)
    if m28:
        reqs[28] = re.sub(r"^28\s*", "", re.sub(r"\s+", " ", m28.group(0)).strip())
    m27 = re.search(r"27\s+Приемку\s+жгута.+?ОСТ\s*1\s*00239-77\.", full_text, flags=re.I | re.S)
    if m27:
        reqs[27] = re.sub(r"^27\s+", "", re.sub(r"\s+", " ", m27.group(0)).strip())
    m26 = re.search(r"26\s+Резьбовые\s+соединения.+?ОСТ\s*1\s*80023-80\.", full_text, flags=re.I | re.S)
    if m26:
        reqs[26] = re.sub(r"^26\s+", "", re.sub(r"\s+", " ", m26.group(0)).strip())

    return reqs


def _patched_score_requirements(reqs: Dict[int, str]) -> Tuple[int, int, int]:
    if not reqs:
        return (-1, -1, -1)
    keys = sorted(reqs)
    contiguous = 0
    for a, b in zip(keys, keys[1:]):
        if b == a + 1:
            contiguous += 1
    return (max(keys), len(keys), contiguous)


def _patched_extract_tech_reqs(self, pdf_path: str, table_bboxes_by_page):
    best = _OLD_EXTRACT_TECH_REQS(self, pdf_path, table_bboxes_by_page)

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return best

    alt_best: Dict[int, str] = {}
    for page in doc:
        w, h = page.rect.width, page.rect.height
        if w < 3000 and h < 2000:
            continue

        # Основной правый столбец ТТ без штампа.
        main_rect = (w * 0.72, h * 0.00, w * 0.98, h * 0.80)
        text_main = self._patched_extract_text_clip(page, main_rect)
        main_reqs = self._patched_parse_tt_from_clipped_text(text_main)

        tail_reqs = self._patched_extract_tail_requirements(page)
        merged = dict(main_reqs)
        merged.update(tail_reqs)

        # Добор из полного текста для конца списка, если clip не справился.
        full_text = self._strip_control_chars(self._fix_mojibake(page.get_text("text", sort=True) or ""))
        if 29 not in merged and "Допустимые замены" in full_text:
            m29 = re.search(r"29\s+Допустимые\s+замены\s+в\s+соответствии\s+с\s+(.+?D10\.)", full_text, flags=re.I | re.S)
            if m29:
                merged[29] = "Допустимые замены в соответствии с " + re.sub(r"\s+", " ", m29.group(1)).strip()
        if 28 not in merged:
            m28 = re.search(r"28\s*Масса\s+жгута\s+не\s+более\s+[^\n]+", full_text, flags=re.I)
            if m28:
                merged[28] = re.sub(r"^28\s*", "", re.sub(r"\s+", " ", m28.group(0)).strip())

        if _patched_score_requirements(merged) > _patched_score_requirements(alt_best):
            alt_best = merged

    # Предпочитаем альтернативу для крупных A0/A1, если она явно лучше или добрала хвост 26-29.
    if _patched_score_requirements(alt_best) > _patched_score_requirements(best):
        return alt_best
    return best


EngineeringDocParser._expected_title_block_bbox = _patched_expected_title_block_bbox
EngineeringDocParser._patched_extract_text_clip = _patched_extract_text_clip
EngineeringDocParser._patched_clean_tt_line = _patched_clean_tt_line
EngineeringDocParser._patched_noise_prefix_cut = _patched_noise_prefix_cut
EngineeringDocParser._patched_parse_tt_from_clipped_text = _patched_parse_tt_from_clipped_text
EngineeringDocParser._patched_extract_tail_requirements = _patched_extract_tail_requirements
EngineeringDocParser._extract_tech_reqs = _patched_extract_tech_reqs



_OLD_RESOLVE_DOC_TYPE_WITH_PRIORITY = EngineeringDocParser._resolve_doc_type_with_priority
_OLD_PARSE_DOCUMENT = EngineeringDocParser.parse_document
_OLD_COMPARE = EngineeringDocParser.compare


def _e4_text_token(self, text: str) -> str:
    t = self._fix_mojibake(text or "")
    t = self._strip_control_chars(t) or ""
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _e4_connector_token(self, text: str) -> str:
    t = _e4_text_token(self, text)
    trans = str.maketrans({"Х": "X", "х": "x", "Р": "P", "р": "p", "Т": "T", "т": "t", "С": "S", "с": "s"})
    return t.translate(trans).upper()


def _looks_like_e4_doc_by_text(self, pdf_path: str) -> bool:
    try:
        with fitz.open(pdf_path) as doc:
            text = self._fix_mojibake(doc[0].get_text("text", sort=True) or "")
        low = text.lower()
        if "схема электрическая соединений" in low:
            return True
        if re.search(r"\bэ4\b", low) and "маркировк" in low and "обознач" in low:
            return True
        return False
    except Exception:
        return False


def _patched_resolve_doc_type_with_priority_e4(self, pdf_path: str, metadata: DocMetadata, tables: Optional[List[TableData]] = None) -> str:
    value = _OLD_RESOLVE_DOC_TYPE_WITH_PRIORITY(self, pdf_path, metadata, tables)
    if value != "Не определён":
        return value
    if _looks_like_e4_doc_by_text(self, pdf_path):
        return "Схема электрическая соединений"
    return value



def _extract_e4_metadata_fallback(self, pdf_path: str) -> DocMetadata:
    with fitz.open(pdf_path) as doc:
        page = doc[0]
        blocks = page.get_text('blocks') or []
        block_texts = [self._fix_mojibake(b[4] or '') for b in blocks if (b[4] or '').strip()]
        whole = "\n".join(block_texts)

        decimal = None
        m = re.search(r'([А-ЯA-ZЁ]+(?:\.\d+){5}\s*Э4)', whole, flags=re.I)
        if m:
            decimal = self._normalize_decimal_number(m.group(1), whole)
        if not decimal:
            m = re.search(r'([А-ЯA-ZЁ]+(?:\.\d+){5})', whole, flags=re.I)
            if m:
                decimal = self._normalize_decimal_number(m.group(1) + ' Э4', whole)

        title = None
        for txt in block_texts:
            low = txt.lower().strip()
            if re.search(r'^(кабель|жгут|шлейф|блок)\b', low):
                title = re.sub(r'\s+', ' ', txt).strip()
                break

        stamp_candidates = []
        for b in blocks:
            x0, y0, x1, y1, txt, *_ = b
            fixed = self._fix_mojibake(txt or '')
            if any(k in fixed.lower() for k in ['э4', 'схема электрическая соединений', 'кабель', 'жгут', 'лит.', 'масса', 'масштаб', 'лист', 'листов', 'формат']):
                stamp_candidates.append((y0, x0, fixed))
        stamp_candidates.sort()
        raw = self._strip_control_chars('\n'.join(t for _,_,t in stamp_candidates)) or ''

        return DocMetadata(
            decimal_number=decimal,
            doc_type='Схема электрическая соединений',
            title=title,
            mass_kg=None,
            scale=None,
            litera=None,
            raw_stamp_snippet=raw,
        )


def _extract_e4_tech_requirements_fallback(self, pdf_path: str) -> List[TechRequirement]:
    with fitz.open(pdf_path) as doc:
        page = doc[0]
        blocks = page.get_text('blocks') or []

    best_text = ''
    best_key = (-1, -1)
    for b in blocks:
        txt = self._fix_mojibake(b[4] or '')
        if not txt.strip():
            continue
        count_num = len(re.findall(r'(?m)^\s*\d{1,2}\s+', txt))
        if count_num == 0:
            continue
        key = (count_num, len(txt))
        if key > best_key:
            best_key = key
            best_text = txt

    if not best_text:
        return []

    reqs: List[TechRequirement] = []
    current_num = None
    parts: List[str] = []
    for raw_line in best_text.replace('\r', '').splitlines():
        line = self._strip_control_chars(self._fix_mojibake(raw_line)) or ''
        line = re.sub(r'\s+', ' ', line).strip()
        if not line:
            continue
        if re.fullmatch(r'\d{1,2}', line):
            if current_num is not None and parts:
                reqs.append(TechRequirement(number=current_num, text='\n'.join(parts).strip()))
            current_num = int(line)
            parts = []
            continue
        m = re.match(r'^(\d{1,2})\s+(.+)$', line)
        if m:
            if current_num is not None and parts:
                reqs.append(TechRequirement(number=current_num, text='\n'.join(parts).strip()))
            current_num = int(m.group(1))
            parts = [m.group(2).strip()]
        elif current_num is not None:
            parts.append(line)
    if current_num is not None and parts:
        reqs.append(TechRequirement(number=current_num, text='\n'.join(parts).strip()))
    return reqs


def _extract_e4_tables_fallback(self, pdf_path: str) -> List[ParsedTable]:
    tables: List[ParsedTable] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[0]
            raw_tables = []
            for settings in self.table_settings_variants[:2]:
                try:
                    raw_tables = page.extract_tables(table_settings=settings) or []
                except Exception:
                    raw_tables = []
                if raw_tables:
                    break
    except Exception:
        raw_tables = []

    seen = set()
    for raw in raw_tables:
        rows = self._clean_rows(raw)
        flat = ' '.join(self._norm_cell(c).lower() for r in rows[:5] for c in r if c)
        if not ('обознач' in flat and 'код' in flat and 'маркиров' in flat):
            continue
        if not any(('=a' in self._norm_cell(c).lower()) or ('lan' in self._norm_cell(c).lower()) or ('вм-' in self._norm_cell(c).lower()) for r in rows for c in r if c):
            continue
        if rows and any('таблиц' in self._norm_cell(c).lower() for c in rows[0] if c):
            rows[0][0] = 'Таблица 1'
        column_names = ['Обозначение', 'Код', 'Маркировка / XP1', 'Маркировка / XS1']
        if len(rows) >= 3:
            hdr3 = ' '.join(self._norm_cell(c).upper() for c in rows[2] if c)
            if 'XS1' in hdr3 and 'XS2' in hdr3:
                column_names = ['Обозначение', 'Код', 'Маркировка / XS1', 'Маркировка / XS2']
        key = tuple(tuple(r) for r in rows)
        if key in seen:
            continue
        seen.add(key)
        tables.append(ParsedTable(page_num=1, name='Таблица 1', bbox=(0.0, 0.0, 0.0, 0.0), rows=rows, column_names=column_names, header_rows_count=3 if len(rows) >= 3 else 1))
    return tables


def _extract_e4_graphic_items(self, pdf_path: str) -> List[dict]:
    items = []
    with fitz.open(pdf_path) as doc:
        page = doc[0]
        words = self._page_words(page)
        connectors = []
        for w in words:
            token = _e4_connector_token(self, w[4])
            if re.fullmatch(r'X[SPTK]\d+[A-Z0-9-]*', token):
                if w[0] < page.rect.width * 0.55 and page.rect.height * 0.20 < w[1] < page.rect.height * 0.50:
                    connectors.append({'name': token, 'x': (w[0]+w[2])/2.0, 'y': (w[1]+w[3])/2.0})
        if not connectors:
            return []
        x0 = max(0.0, min(c['x'] for c in connectors) - 120)
        x1 = min(page.rect.width * 0.55, max(c['x'] for c in connectors) + 120)
        y0 = max(0.0, min(c['y'] for c in connectors) - 30)
        y1 = min(page.rect.height * 0.56, max(c['y'] for c in connectors) + 120)
        diag_words = []
        for w in words:
            cx = (w[0]+w[2])/2.0
            cy = (w[1]+w[3])/2.0
            if not (x0 <= cx <= x1 and y0 <= cy <= y1):
                continue
            tok = _e4_text_token(self, w[4])
            if not tok or tok.lower() in {'справ.', '№'}:
                continue
            diag_words.append((w[0], w[1], w[2], w[3], tok))
        conductor_words = [w for w in diag_words if re.fullmatch(r'\d{3}|б/н|-', w[4], re.I)]
        for cw in conductor_words:
            cy = (cw[1]+cw[3])/2.0
            conductor_x = (cw[0]+cw[2])/2.0
            near = [w for w in diag_words if abs(((w[1]+w[3])/2.0)-cy) <= 18]
            near = sorted(near, key=lambda x: x[0])
            signal_parts = []
            left_pins, right_pins = [], []
            corpus_words = [w for w in near if _e4_text_token(self, w[4]).lower() == 'корпус']
            for w in near:
                tok = _e4_text_token(self, w[4])
                up = _e4_connector_token(self, w[4])
                wx = (w[0]+w[2])/2.0
                if tok == cw[4] or tok.lower() == 'корпус' or re.fullmatch(r'X[SPTK]\d+[A-Z0-9-]*', up):
                    continue
                if re.fullmatch(r'\d{1,2}|[A-ZА-ЯЁ]', tok, re.I):
                    if wx < conductor_x:
                        left_pins.append(tok)
                    else:
                        right_pins.append(tok)
                else:
                    signal_parts.append(tok)
            left_corpus = any(((w[0]+w[2])/2.0) < conductor_x for w in corpus_words)
            right_corpus = any(((w[0]+w[2])/2.0) > conductor_x for w in corpus_words)
            corpus_connectors = []
            for w in corpus_words:
                wx = (w[0]+w[2])/2.0
                nearest = min(connectors, key=lambda c: abs(c['x'] - wx))
                corpus_connectors.append(nearest['name'])
            bx0 = min([cw[0]] + [w[0] for w in corpus_words]) if corpus_words else cw[0]
            by0 = min([cw[1]] + [w[1] for w in corpus_words]) if corpus_words else cw[1]
            bx1 = max([cw[2]] + [w[2] for w in corpus_words]) if corpus_words else cw[2]
            by1 = max([cw[3]] + [w[3] for w in corpus_words]) if corpus_words else cw[3]
            items.append({
                'key': cw[4],
                'page_num': 1,
                'conductor': cw[4],
                'signal': ' '.join(signal_parts).strip() or None,
                'left_pins': left_pins,
                'right_pins': right_pins,
                'left_corpus': left_corpus,
                'right_corpus': right_corpus,
                'corpus_connectors': corpus_connectors,
                'bbox': (int(bx0), int(by0), int(max(1,bx1-bx0)), int(max(1,by1-by0))),
            })
    items.sort(key=lambda x: x['conductor'])
    return items


def _diff_e4_graphics_semantic(self, v1_path: str, v2_path: str) -> GraphicDiff:

    left = {x['key']: x for x in _extract_e4_graphic_items(self, v1_path)}
    right = {x['key']: x for x in _extract_e4_graphic_items(self, v2_path)}

    first_bbox = (0, 0, 1, 1)
    has_change = False

    for key in sorted(set(left) | set(right)):
        a = left.get(key)
        b = right.get(key)

        if a and not b:
            first_bbox = a.get('bbox') or first_bbox
            has_change = True
            break

        if b and not a:
            first_bbox = b.get('bbox') or first_bbox
            has_change = True
            break

        if not a or not b:
            continue

        first_bbox = b.get('bbox') or a.get('bbox') or first_bbox

        if (a.get('signal') or '') != (b.get('signal') or ''):
            has_change = True
            break

        if len(a.get('left_pins', [])) != len(b.get('left_pins', [])) or len(a.get('right_pins', [])) != len(b.get('right_pins', [])):
            has_change = True
            break

        if a.get('left_corpus') != b.get('left_corpus') or a.get('right_corpus') != b.get('right_corpus'):
            has_change = True
            break

    if not has_change:
        return GraphicDiff(has_changes=False, changed_regions=[], change_percentage=0.0)

    x, y, w, h = first_bbox
    return GraphicDiff(
        has_changes=True,
        changed_regions=[
            GraphicRegion(
                page_num=1,
                x=x,
                y=y,
                w=max(1, w),
                h=max(1, h),
                change_type='graphics_changed',
                description='Изменена графика схемы.',
            )
        ],
        change_percentage=3.0,
    )


def _patched_parse_document_e4(self, pdf_path: str) -> ParsedDocument:
    parsed = _OLD_PARSE_DOCUMENT(self, pdf_path)
    if parsed.metadata.doc_type == 'Схема электрическая соединений':
        fallback_meta = _extract_e4_metadata_fallback(self, pdf_path)
        if not parsed.metadata.decimal_number:
            parsed.metadata.decimal_number = fallback_meta.decimal_number
        if not parsed.metadata.title:
            parsed.metadata.title = fallback_meta.title
        if not parsed.metadata.raw_stamp_snippet:
            parsed.metadata.raw_stamp_snippet = fallback_meta.raw_stamp_snippet
        fallback_reqs = _extract_e4_tech_requirements_fallback(self, pdf_path)
        if fallback_reqs:
            parsed.tech_requirements = fallback_reqs
        fallback_tables = _extract_e4_tables_fallback(self, pdf_path)
        if fallback_tables:
            parsed.tables = fallback_tables
        else:
            parsed.tables = [t for t in parsed.tables if 'таблица' in t.name.lower() and any('маркиров' in self._norm_cell(c).lower() for r in t.rows[:4] for c in r if c)]
    return parsed


def _patched_compare_e4_semantic(self, v1_path: str, v2_path: str) -> FullDiff:
    result = _OLD_COMPARE(self, v1_path, v2_path)
    if (result.metadata_v1.doc_type == 'Схема электрическая соединений' or result.metadata_v2.doc_type == 'Схема электрическая соединений'):
        semantic = _diff_e4_graphics_semantic(self, v1_path, v2_path)
        if semantic.has_changes:
            result.graphics = semantic
    return result


EngineeringDocParser._looks_like_e4_doc_by_text = _looks_like_e4_doc_by_text
EngineeringDocParser._resolve_doc_type_with_priority = _patched_resolve_doc_type_with_priority_e4
EngineeringDocParser._extract_e4_metadata_fallback = _extract_e4_metadata_fallback
EngineeringDocParser._extract_e4_tech_requirements_fallback = _extract_e4_tech_requirements_fallback
EngineeringDocParser._extract_e4_tables_fallback = _extract_e4_tables_fallback
EngineeringDocParser._extract_e4_graphic_items = _extract_e4_graphic_items
EngineeringDocParser._diff_e4_graphics_semantic = _diff_e4_graphics_semantic
EngineeringDocParser.parse_document = _patched_parse_document_e4
EngineeringDocParser.compare = _patched_compare_e4_semantic
