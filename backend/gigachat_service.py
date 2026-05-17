from __future__ import annotations


import base64
import json
import os
import re
import uuid
from collections import defaultdict
from json import JSONDecoder, JSONDecodeError
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from models import (
    ChangeFact,
    ChangeFactsBundle,
    ChangeNoticeResponse,
    DocMetadata,
    FullDiff,
    GenerateNoticeDebugResponse,
    NotesOnlyPayload,
    NoticeBlock,
)


DEFAULT_DRAWING_TEMPLATE = """Сборочный чертеж

МЦША.101.19121.001.00 СБ
Заменить (Листы x, x (х-х) заменить, лист x+n вводится вновь, лист x+n аннулировать)

Примечания (е - )
1 Изменена графика чертежа:
- изменена топология жгута;
- выполнен в зеркальном отражении;
- введены (аннулированы) отводы Экран, GND, ХХХ;
- изменена длина жгута на ХХХ мм;
- изменена длина отвода ХХХ на ХХХ мм;
- изменена длина участка жгута длиной ХХХ мм на ХХХ мм.
2 Нумерация позиций приведена в соответствие со спецификацией.
3 Изменены виды:
- введен вид Х;
- аннулирован вид Х.
4 Изменены таблицы:
- изменена таблица Х;
- в таблице Х изменены значения в графе "ХХХ";
- в таблицу Х введена строка;
- из таблицы Х аннулирована строка.
5 Изменены ТТ:
- аннулирован п. 1 о …;
- аннулирован п. 2 о …;
- изменена формулировка в п. 1 о …;
- изменена формулировка в п. 2 о …;
- уточнено требование в п. 1 о …;
- уточнено требование в п. 2 о …;
- изменено требование в п. 1 о …;
- изменено требование в п. 2 о …;
- исключено требование в п. 1 о …;
- исключено требование в п. 2 о …;
- введено требование в п. 1 о …;
- введено требование в п. 2 о …;
- изменена нумерация пунктов;
- введено требование о … .
6 Изменен формат документа на Аn.
7 В основной надписи в графе "Масса" изменено значение на x кг.
8 В основной надписи в графе "Масштаб" изменено значение на 1:х.
9 В основной надписи изменено наименование на "".
10 В основной надписи в графе "Листов" изменено значение на x+n.
Журнал № XX, запись № XX.
""".strip()

DEFAULT_SPEC_TEMPLATE = """Спецификация

МЦША.101.19121.001.00
Заменить (Листы x, x (х-х) заменить, лист x+n вводится вновь, лист x+n аннулировать)

Примечания (е - )
1 Спецификация преобразована в групповую.
2 Введено исполнение МЦША.101.19121.001.00-01.
3 В разделе \"Документация\" изменен формат документа МЦША.101.19121.001.00 ПЭ4/СБ/Э4 на Аn.
4 Введен раздел \"Детали\".
5 В разделе \"Детали\":
- аннулирована применяемость xx;
- введена применяемость хх;
- изменено количество хх на хх шт.
6 Введен раздел \"Стандартные изделия\".
7 В разделе \"Стандартные изделия\":
- аннулирована применяемость xx;
- введена применяемость хх;
- изменено количество хх на хх шт.
8 В разделе \"Прочие изделия\":
- аннулирована применяемость xx;
- введена применяемость хх;
- изменено количество хх на хх шт.
9 В разделе \"Материалы\":
- аннулирована применяемость xx;
- введена применяемость хх;
- изменено количество хх на хх шт/м.
10 Изменена нумерация позиций.
11 В графе \"Примечания\" изменены поз. обозначения.
12 В основной надписи изменено наименование на \"\".
13 В основной надписи в графе \"Листов\" изменено значение на x+n.

Применен документ (Наименование документа из радела \"Детали/Стандартные изделия\", если документа не было в предыдущих версиях СП).
Журнал № XX, запись № XX.
""".strip()

DEFAULT_PE4_TEMPLATE = """Перечень элементов

МЦША.101.19121.001.00 ПЭ4
Заменить (Листы x, x (х-х) заменить, лист x+n вводится вновь, лист x+n аннулировать)

Примечания (е - )
1 Наименования элементов обновлены в соответствии с ХХХ
2 Изменены позиционные обозначения.
3 Аннулированы ХХХ, ХХХ, ХХХ.
4 Введены ХХХ, ХХХ, ХХХ.
5 Аннулированы хх ТУ хх, хх ТУ хх.
6 Введены хх ТУ хх, хх ТУ хх.
7 Наименования и количество элементов приведены в соответствие со спецификацией.
8 В основной надписи изменено наименование на \"\".
9 В основной надписи в графе \"Листов\" изменено значение на x+n.
Журнал № XX, запись № XX.
""".strip()


DEFAULT_E4_TEMPLATE = """Схема электрическая соединений

МЦША.101.19121.001.00 Э4
Заменить (Листы x, x (х-х) заменить, лист x+n вводится вновь, лист x+n аннулировать)

Примечания (е -)
1 Схема изменена с учетом исходных данных.
2 Изменена графика схемы:
- введены ХХХ, ХХХ;
- аннулированы ХХХ, ХХХ;
- введены проводники ХХХ - ХХХ;
- аннулированы проводники ХХХ - ХХХ;
- изменено подключение проводников ХХХ - ХХХ;
- откорректирован на полке-выноске номер пункта ТТ.
3 Изменены ТТ:
- аннулирован п. 1 о …;
- аннулирован п. 2 о …;
- изменена формулировка в п. 1 о …;
- изменена формулировка в п. 2 о …;
- уточнено требование в п. 1 о …;
- уточнено требование в п. 2 о …;
- исключено требование в п. 1 о …;
- исключено требование в п. 2 о …;
- введено требование в п. 1 о …;
- введено требование в п. 2 о …;
- изменена нумерация пунктов;
- введено требование о ….
4 Изменен формат документа на Аn.
5 В основной надписи изменено наименование на "".
6 В основной надписи в графе "Листов" изменено значение на x+n.
Журнал № XX, запись № XX.
""".strip()

class GigaChatNoticeService:

    def __init__(self, dotenv_path: Optional[str] = None, template_path: Optional[str] = None,
                 spec_template_path: Optional[str] = None, pe4_template_path: Optional[str] = None,
                 e4_template_path: Optional[str] = None, require_credentials: bool = True):
        load_dotenv(dotenv_path=dotenv_path)

        self.client_id = os.getenv("GIGACHAT_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("GIGACHAT_CLIENT_SECRET", "").strip()
        self.scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()
        self.model = os.getenv("GIGACHAT_MODEL", "GigaChat").strip()
        self.verify_ssl_certs = os.getenv("GIGACHAT_VERIFY_SSL_CERTS", "true").lower() == "true"
        self.ca_bundle_file = os.getenv("GIGACHAT_CA_BUNDLE_FILE", "").strip() or None
        self.timeout = int(os.getenv("GIGACHAT_TIMEOUT", "60").strip())
        self.oauth_url = os.getenv("GIGACHAT_OAUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth").strip()
        self.base_url = os.getenv("GIGACHAT_BASE_URL", "https://gigachat.devices.sberbank.ru/api").strip().rstrip("/")

        if require_credentials and (not self.client_id or not self.client_secret):
            raise ValueError("Не заданы GIGACHAT_CLIENT_ID и/или GIGACHAT_CLIENT_SECRET")

        base_dir = Path(__file__).resolve().parent
        self.template_path = Path(template_path) if template_path else base_dir / "gost_template.txt"
        self.spec_template_path = Path(spec_template_path) if spec_template_path else base_dir / "gost_template_spec.txt"
        self.pe4_template_path = Path(pe4_template_path) if pe4_template_path else base_dir / "gost_template_pe4.txt"
        self.e4_template_path = Path(e4_template_path) if e4_template_path else base_dir / "gost_template_e4.txt"

    def generate_notice_from_diff(self, diff: FullDiff, additional_instructions: Optional[str] = None) -> GenerateNoticeDebugResponse:
        facts = self.build_change_facts(diff)
        llm_raw_text: Optional[str] = None
        llm_raw_json: Optional[str] = None
        usage: Dict[str, Any] = {}
        llm_error: Optional[str] = None

        try:
            token = self._get_access_token()
            prompt = self._build_user_prompt(
                facts=facts,
                template_text=self._load_template_for_doc_type(facts.document_type),
                additional_instructions=additional_instructions,
            )
            response_json = self._chat_completion(
                token=token,
                system_prompt=self._system_prompt(facts.document_type),
                user_prompt=prompt,
            )
            usage = response_json.get("usage", {}) if isinstance(response_json, dict) else {}
            llm_raw_text = self._extract_content(response_json)
            llm_raw_json = self._extract_first_json_string(llm_raw_text)
            parsed = json.loads(llm_raw_json)
            if self._is_schema_echo(parsed):
                raise ValueError("LLM вернула JSON schema вместо данных")

            if self._uses_semantic_topics_contract(facts.document_type):
                # Новый контракт: GigaChat не пишет финальные notes.
                # Он возвращает смысловые темы изменённых ТТ, а backend сам собирает ЕСКД-текст.
                notes = self._notes_from_semantic_topics_response(facts, parsed)
            else:
                # Для СП/ПЭ4/Э4 пока оставляем старый notes-контракт.
                notes_payload = NotesOnlyPayload.model_validate(parsed)
                notes = self._sanitize_notes(notes_payload.notes)
                if self._is_specification_type(facts.document_type):
                    notes = self._postprocess_grouped_notes(notes)
                    notes = self._finalize_spec_notes_from_facts(facts, notes)
                elif self._is_pe4_type(facts.document_type):
                    notes = self._postprocess_pe4_notes(notes)
                    notes = self._finalize_pe4_notes_from_facts(facts, notes)
                elif self._is_e4_type(facts.document_type):
                    notes = self._postprocess_e4_notes(notes)
                notes = self._format_multiline_notes(notes)
                if not notes:
                    notes = self._fallback_notes_from_facts(facts)
                    notes = self._format_multiline_notes(notes)
                else:
                    validation_error = self._validate_llm_notes_against_facts(facts, notes)
                    if validation_error:
                        raise ValueError(f"Ответ GigaChat отклонён: {validation_error}")
        except Exception as exc:
            llm_error = f"{type(exc).__name__}: {exc}"
            notes = self._fallback_notes_from_facts(facts)

        result = self._build_notice_response(diff, facts, notes)
        return GenerateNoticeDebugResponse(
            result=result,
            facts=facts,
            llm_raw_text=llm_raw_text,
            llm_raw_json=llm_raw_json,
            llm_error=llm_error,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )

    def build_change_facts(self, diff: FullDiff) -> ChangeFactsBundle:
        meta = self._choose_effective_metadata(diff.metadata_v2, diff.metadata_v1)
        document_type = meta.doc_type or "Сборочный чертеж"
        decimal_number = meta.decimal_number or ""
        title = meta.title
        facts: List[ChangeFact] = []

        if diff.metadata_v1.mass_kg != diff.metadata_v2.mass_kg and diff.metadata_v2.mass_kg is not None:
            facts.append(ChangeFact(
                fact_type="mass_changed",
                description="Изменено значение массы в основной надписи",
                old_value=self._float_to_str(diff.metadata_v1.mass_kg),
                new_value=self._float_to_str(diff.metadata_v2.mass_kg),
                source="metadata",
            ))
        if self._norm(diff.metadata_v1.scale) != self._norm(diff.metadata_v2.scale) and diff.metadata_v2.scale:
            facts.append(ChangeFact(
                fact_type="scale_changed",
                description="Изменено значение масштаба в основной надписи",
                old_value=diff.metadata_v1.scale,
                new_value=diff.metadata_v2.scale,
                source="metadata",
            ))
        if self._norm(diff.metadata_v1.title) != self._norm(diff.metadata_v2.title) and diff.metadata_v2.title:
            facts.append(ChangeFact(
                fact_type="title_changed",
                description="Изменено наименование в основной надписи",
                old_value=diff.metadata_v1.title,
                new_value=diff.metadata_v2.title,
                source="metadata",
            ))

        numbering_shift_detected = False
        for item in diff.tech_requirements:
            if item.status == "added":
                facts.append(ChangeFact(
                    fact_type="tech_requirement_added",
                    description=self._summarize_requirement(item.v2_text),
                    number_new=item.v2_number or item.number,
                    new_value=item.v2_text,
                    source="tech_requirements",
                    payload={"v2_text": item.v2_text},
                ))
            elif item.status == "removed":
                facts.append(ChangeFact(
                    fact_type="tech_requirement_removed",
                    description=self._summarize_requirement(item.v1_text),
                    number_old=item.v1_number or item.number,
                    old_value=item.v1_text,
                    source="tech_requirements",
                    payload={"v1_text": item.v1_text},
                ))
            elif item.status == "modified":
                if item.v1_number and item.v2_number and item.v1_number != item.v2_number:
                    numbering_shift_detected = True
                facts.append(ChangeFact(
                    fact_type="tech_requirement_modified",
                    description=self._summarize_requirement(item.v2_text or item.v1_text),
                    number_old=item.v1_number or item.number,
                    number_new=item.v2_number or item.number,
                    old_value=item.v1_text,
                    new_value=item.v2_text,
                    source="tech_requirements",
                    payload={"v1_text": item.v1_text, "v2_text": item.v2_text},
                ))
        if numbering_shift_detected:
            facts.append(ChangeFact(
                fact_type="tech_requirement_renumbered",
                description="Изменена нумерация пунктов технических требований",
                source="tech_requirements",
            ))

        spec_items = getattr(diff, "specification_items", []) or []
        is_spec_document = self._is_specification_type(document_type)
        for item in spec_items:
            payload = {
                "section": item.section,
                "position": item.position,
                "designation": item.designation,
                "name": item.name,
                "status": item.status,
            }
            desc = item.name or item.designation or f"позиция {item.position}"
            section = self._clean_spec_item_name(item.section or "")
            name = self._clean_spec_item_name(desc)

            if is_spec_document and self._is_bad_spec_item_name(name):
                continue

            if item.status == "added":
                qty = self._norm(item.v2_item.quantity if item.v2_item else None)
                if is_spec_document and self._is_bad_spec_added_removed(section, name, qty):
                    continue
                facts.append(ChangeFact(
                    fact_type="spec_item_added",
                    description=desc,
                    new_value=qty,
                    source="specification_items",
                    payload=payload,
                ))
            elif item.status == "removed":
                qty = self._norm(item.v1_item.quantity if item.v1_item else None)
                if is_spec_document and self._is_bad_spec_added_removed(section, name, qty):
                    continue
                facts.append(ChangeFact(
                    fact_type="spec_item_removed",
                    description=desc,
                    old_value=qty,
                    source="specification_items",
                    payload=payload,
                ))
            elif item.status == "modified":
                for ch in item.field_changes:
                    field_name = str(ch.field_name or "").strip().lower()
                    old_val = self._norm(ch.v1_val)
                    new_val = self._norm(ch.v2_val)

                    if is_spec_document and self._should_skip_spec_field_change(section, name, field_name, old_val, new_val):
                        continue

                    if field_name == "quantity":
                        facts.append(ChangeFact(
                            fact_type="spec_item_quantity_changed",
                            description=desc,
                            old_value=old_val,
                            new_value=new_val,
                            source="specification_items",
                            payload={**payload, "field_name": "quantity"},
                        ))
                    elif field_name == "note":
                        facts.append(ChangeFact(
                            fact_type="spec_item_note_changed",
                            description=desc,
                            old_value=old_val,
                            new_value=new_val,
                            source="specification_items",
                            payload={**payload, "field_name": "note"},
                        ))
                    else:
                        facts.append(ChangeFact(
                            fact_type="spec_item_modified",
                            description=desc,
                            old_value=old_val,
                            new_value=new_val,
                            source="specification_items",
                            payload={**payload, "field_name": field_name},
                        ))

        element_items = getattr(diff, "element_items", []) or []
        for item in element_items:
            payload = {
                "position_designation": item.position_designation,
                "name": item.name,
                "status": item.status,
            }
            desc = item.name or item.position_designation
            if item.status == "added":
                facts.append(ChangeFact(
                    fact_type="element_item_added",
                    description=desc,
                    new_value=self._norm(item.v2_item.quantity if item.v2_item else None),
                    source="element_items",
                    payload=payload,
                ))
            elif item.status == "removed":
                facts.append(ChangeFact(
                    fact_type="element_item_removed",
                    description=desc,
                    old_value=self._norm(item.v1_item.quantity if item.v1_item else None),
                    source="element_items",
                    payload=payload,
                ))
            elif item.status == "modified":
                for ch in item.field_changes:
                    if ch.field_name == "name":
                        facts.append(ChangeFact(
                            fact_type="element_item_name_changed",
                            description=item.position_designation,
                            old_value=self._norm(ch.v1_val),
                            new_value=self._norm(ch.v2_val),
                            source="element_items",
                            payload={**payload, "field_name": "name"},
                        ))
                    elif ch.field_name == "quantity":
                        facts.append(ChangeFact(
                            fact_type="element_item_quantity_changed",
                            description=desc,
                            old_value=self._norm(ch.v1_val),
                            new_value=self._norm(ch.v2_val),
                            source="element_items",
                            payload={**payload, "field_name": "quantity"},
                        ))
                    elif ch.field_name == "note":
                        facts.append(ChangeFact(
                            fact_type="element_item_note_changed",
                            description=desc,
                            old_value=self._norm(ch.v1_val),
                            new_value=self._norm(ch.v2_val),
                            source="element_items",
                            payload={**payload, "field_name": "note"},
                        ))
                    else:
                        facts.append(ChangeFact(
                            fact_type="element_item_modified",
                            description=desc,
                            old_value=self._norm(ch.v1_val),
                            new_value=self._norm(ch.v2_val),
                            source="element_items",
                            payload={**payload, "field_name": ch.field_name},
                        ))

        if self._is_e4_type(document_type):
            facts.extend(self._build_e4_table_facts(diff.tables))
        else:
            facts.extend(self._build_drawing_table_facts(diff.tables))


        ignore_graphics_for_doc = self._is_specification_type(document_type) or self._is_pe4_type(document_type)

        if diff.graphics and diff.graphics.has_changes and not ignore_graphics_for_doc:
            if self._is_e4_type(document_type):
                facts.append(ChangeFact(
                    fact_type="graphics_changed",
                    description="Изменена графика схемы",
                    new_value=str(diff.graphics.change_percentage),
                    source="graphics",
                    payload={"change_percentage": diff.graphics.change_percentage},
                ))
            else:
                facts.append(ChangeFact(
                    fact_type="graphics_changed",
                    description="Изменена графика чертежа",
                    new_value=str(diff.graphics.change_percentage),
                    source="graphics",
                    payload={"change_percentage": diff.graphics.change_percentage},
                ))

        return ChangeFactsBundle(
            document_type=document_type,
            decimal_number=decimal_number,
            title=title,
            facts=facts,
        )

    @staticmethod
    def _ensure_sentence_period(text: str) -> str:
        value = re.sub(r"\s+", " ", str(text or "").strip())
        if not value:
            return value
        return value if value.endswith((".", ";", ":", "!", "?")) else value + "."

    @staticmethod
    def _table_number_from_name(table_name: Optional[str]) -> Optional[str]:
        text = re.sub(r"\s+", " ", str(table_name or "").strip())
        match = re.search(r"таблиц[аы]?\s*(\d+)", text, flags=re.IGNORECASE)
        if match:
            return match.group(1)


        low = text.lower().replace("ё", "е")
        if "провод" in low and ("соединител" in low or "длина" in low or "кол" in low):
            return "1"

        return None

    @staticmethod
    def _canonical_table_key(table_name: Optional[str]) -> str:
        number = GigaChatNoticeService._table_number_from_name(table_name)
        if number:
            return f"table:{number}"
        text = re.sub(r"\s+", " ", str(table_name or "").strip()).lower().replace("ё", "е")
        return text or "table:1"

    @staticmethod
    def _table_short_name(table_name: Optional[str]) -> str:
        number = GigaChatNoticeService._table_number_from_name(table_name)
        return f"таблице {number or '1'}"

    @staticmethod
    def _clean_table_subject(value: Any) -> str:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        text = text.strip(' "«»|/;:')
        return text

    @staticmethod
    def _is_bad_table_subject(value: Any, column_names: Optional[List[str]] = None) -> bool:
        text = GigaChatNoticeService._clean_table_subject(value)
        if not text:
            return True
        low = text.lower().replace("ё", "е")
        compact = re.sub(r"\s+", "", low)
        bad_exact = {
            "поз", "поз.", "провод", "провода", "маркировка", "код", "кол", "кол.",
            "длина", "длина,мм", "обозначение", "обозначениепровода/кабеля",
            "отсоединителя", "ксоединителю", "позиционноеобозначение", "соединитель",
            "исполнение", "исп.", "наименование", "примечание", "-", "—", "–",
        }
        if compact in bad_exact:
            return True
        for name in column_names or []:
            name_compact = re.sub(r"\s+", "", str(name or "").lower().replace("ё", "е"))
            if name_compact and compact == name_compact:
                return True
        return False

    @staticmethod
    def _column_subject_name(col_name: Optional[str]) -> str:
        low = str(col_name or "").lower().replace("ё", "е")
        if "масса" in low:
            return "массы"
        if "длина" in low:
            return "длины"
        if "от соедин" in low:
            return "места присоединения от соединителя"
        if "к соедин" in low:
            return "места присоединения к соединителю"
        if "обозначение провода" in low or "обозначение кабеля" in low:
            return "обозначения провода/кабеля"
        if "провод" in low:
            return "номера провода"
        if "поз" in low:
            return "позиции"
        if "кол" in low:
            return "количества"
        if "маркиров" in low:
            return "маркировки"
        clean = re.sub(r"\s+", " ", str(col_name or "значения")).strip(' .:;')
        return clean.lower() if clean else "значения"

    @staticmethod
    def _numeric_subject_match(value: str):
        """Returns a regex match for a pure numeric table subject, preserving leading zeros."""
        return re.fullmatch(r"(\d+)", str(value or "").strip())

    @classmethod
    def _compress_table_subject_ranges(cls, subjects: List[str], min_range_len: int = 3) -> List[tuple[str, int]]:
        """
        Converts sequential numeric subjects into compact ranges.

        Example: ["001", "002", "003", "005"] -> [("001-003", 3), ("005", 1)].
        The second value is the number of original records represented by the display item;
        it keeps the "и еще N записей" suffix honest after compression.
        """
        result: List[tuple[str, int]] = []
        run: List[str] = []

        def flush_run() -> None:
            nonlocal run
            if not run:
                return
            if len(run) >= min_range_len:
                result.append((f"{run[0]}-{run[-1]}", len(run)))
            else:
                result.extend((item, 1) for item in run)
            run = []

        prev_num = None
        for item in subjects:
            m = cls._numeric_subject_match(item)
            if not m:
                flush_run()
                result.append((item, 1))
                prev_num = None
                continue

            num = int(m.group(1))
            if run and prev_num is not None and num == prev_num + 1:
                run.append(item)
            else:
                flush_run()
                run = [item]
            prev_num = num

        flush_run()
        return result

    def _format_table_subjects(self, subjects: List[str], max_items: int = 12) -> str:
        clean = self._unique_preserve_order([
            self._clean_table_subject(item)
            for item in subjects
            if not self._is_bad_table_subject(item)
        ])
        if not clean:
            return "записей таблицы"

        compressed = self._compress_table_subject_ranges(clean)
        if len(compressed) > max_items:
            visible = compressed[:max_items]
            hidden_count = sum(count for _, count in compressed[max_items:])
            return ", ".join(f'"{item}"' for item, _ in visible) + f" и еще {hidden_count} записей"
        return ", ".join(f'"{item}"' for item, _ in compressed)

    def _format_wire_subjects(self, subjects: List[str], max_items: int = 12) -> str:

        clean = self._unique_preserve_order([
            self._clean_table_subject(item)
            for item in subjects
            if item is not None and self._clean_table_subject(item)
        ])
        if not clean:
            return "проводов"

        compressed = [item for item, _count in self._compress_table_subject_ranges(clean)]
        if len(compressed) > max_items:
            visible = compressed[:max_items]
            hidden_count = len(compressed[max_items:])
            return ", ".join(visible) + f" и еще {hidden_count} проводов"
        if len(compressed) == 1:
            return compressed[0]
        if len(compressed) == 2:
            return f"{compressed[0]} и {compressed[1]}"
        return ", ".join(compressed[:-1]) + f" и {compressed[-1]}"

    @staticmethod
    def _is_wire_table_name(table_name: Optional[str]) -> bool:
        low = str(table_name or "").lower().replace("ё", "е")
        return (
            "таблица присоедин" in low
            or ("провод" in low and "соединител" in low)
            or ("провод" in low and "длина" in low and "кол" in low)
        )

    @staticmethod
    def _is_wire_column_name(col_name: Optional[str]) -> bool:
        low = str(col_name or "").lower().replace("ё", "е")
        return "провод" in low and "обозначение провода" not in low and "обозначение кабеля" not in low

    def _is_wire_connection_table_item(self, item: Any) -> bool:
        table_name = self._norm(getattr(item, "table_name", None)) or ""
        if self._is_wire_table_name(table_name):
            return True

        columns = [self._norm(value) or "" for value in (getattr(item, "column_names", None) or [])]
        joined = " ".join(columns).lower().replace("ё", "е")
        has_wire_payload = "обозначение провода" in joined or "обозначение кабеля" in joined
        has_length = "длина" in joined
        has_connection = "от соедин" in joined or "к соедин" in joined or "гильз" in joined or "наконечник" in joined
        return bool(has_wire_payload and has_length and has_connection)

    def _has_explicit_wire_key_column(self, item: Any) -> bool:
        for name in (getattr(item, "column_names", None) or []):
            if self._is_wire_column_name(name):
                return True
        return False

    def _is_wire_position_key_artifact(self, item: Any) -> bool:
        status = str(getattr(item, "status", "") or "").strip().lower()
        if status != "row_added":
            return False
        if not self._is_wire_connection_table_item(item):
            return False

        col_name = self._norm(getattr(item, "col_name", None)) or ""
        low_col = col_name.lower().replace("ё", "е")
        first_col = self._norm((getattr(item, "column_names", None) or [""])[0]) or ""
        low_first_col = first_col.lower().replace("ё", "е")

        if "поз" not in low_col and "поз" not in low_first_col:
            return False
        if self._has_explicit_wire_key_column(item):
            return False

        subject = self._table_row_subject_from_diff(item)
        return bool(re.fullmatch(r"\d{1,2}", subject or ""))

    def _is_wire_removed_counterpart_for_position_artifact(self, item: Any) -> bool:
        status = str(getattr(item, "status", "") or "").strip().lower()
        if status != "row_removed":
            return False
        if not self._is_wire_connection_table_item(item):
            return False
        if not self._is_wire_column_name(getattr(item, "col_name", None)):
            return False
        subject = self._table_row_subject_from_diff(item)
        return bool(re.fullmatch(r"\d{3,}", subject or ""))

    def _drop_wire_position_key_artifacts(self, raw_items: List[Any]) -> List[Any]:
        artifact_counts: Dict[str, int] = defaultdict(int)
        for item in raw_items:
            if self._is_wire_position_key_artifact(item):
                table_key = self._canonical_table_key(getattr(item, "table_name", None))
                artifact_counts[table_key] += 1

        if not artifact_counts:
            return raw_items

        skipped_removed: Dict[str, int] = defaultdict(int)
        filtered: List[Any] = []
        for item in raw_items:
            table_key = self._canonical_table_key(getattr(item, "table_name", None))
            if self._is_wire_position_key_artifact(item):
                continue
            if (
                artifact_counts.get(table_key, 0)
                and self._is_wire_removed_counterpart_for_position_artifact(item)
                and skipped_removed[table_key] < artifact_counts[table_key]
            ):
                skipped_removed[table_key] += 1
                continue
            filtered.append(item)
        return filtered

    @staticmethod
    def _is_unnumbered_wire_subject(value: Any) -> bool:
        text = GigaChatNoticeService._clean_table_subject(value).lower().replace("ё", "е")
        compact = re.sub(r"[\s./\-]+", "", text)
        return compact in {"бн", "безномера"}

    def _table_row_subject_from_diff(self, item: Any) -> str:
        row_key = self._clean_table_subject(getattr(item, "row_key", "") or "")
        column_names = list(getattr(item, "column_names", []) or [])
        if row_key and not self._is_bad_table_subject(row_key, column_names):
            return row_key.split("|")[0].strip()

        # Резерв: если row_key не пришел, берем значение из ключевой графы строки.
        key_col_index = getattr(item, "key_col_index", None)
        if key_col_index:
            idx = int(key_col_index) - 1
            for row in (getattr(item, "row_values_v2", None), getattr(item, "row_values_v1", None)):
                row = list(row or [])
                if 0 <= idx < len(row):
                    candidate = self._clean_table_subject(row[idx])
                    if candidate and not self._is_bad_table_subject(candidate, column_names):
                        return candidate

        return ""

    @staticmethod
    def _table_group_status(items: List[Any]) -> str:
        statuses = {str(getattr(item, "status", "") or "").lower() for item in items}
        if statuses and statuses.issubset({"row_added"}):
            return "row_added"
        if statuses and statuses.issubset({"row_removed"}):
            return "row_removed"
        return "changed"

    def _is_whole_column_change(self, items: List[Any]) -> bool:
        if not items:
            return False
        total_candidates = []
        for item in items:
            try:
                total = int(getattr(item, "table_data_rows", 0) or 0)
            except (TypeError, ValueError):
                total = 0
            if total > 0:
                total_candidates.append(total)
        if not total_candidates:
            return False
        total_rows = max(total_candidates)
        subjects = self._unique_preserve_order([self._table_row_subject_from_diff(item) for item in items])
        return total_rows >= 3 and len(subjects) >= max(3, int(total_rows * 0.8))

    @staticmethod
    def _table_out_name(table_name: Optional[str]) -> str:
        number = GigaChatNoticeService._table_number_from_name(table_name)
        return f"таблицы {number or '1'}"

    @staticmethod
    def _is_connection_column(col_name: Optional[str]) -> bool:
        low = str(col_name or "").lower().replace("ё", "е")
        return "от соедин" in low or "к соедин" in low

    @staticmethod
    def _column_change_phrase(col_name: Optional[str]) -> str:
        low = str(col_name or "").lower().replace("ё", "е")
        if "кол" in low:
            return "изменилось количество"
        if "длина" in low:
            return "изменилась длина"
        if "поз" in low:
            return "изменилась позиция"
        if "масса" in low:
            return "изменено значение массы"
        if "обозначение провода" in low or "обозначение кабеля" in low:
            return "изменилось обозначение провода/кабеля"
        if "маркиров" in low:
            return "изменилась маркировка"
        return "изменилось значение"

    def _drawing_table_note_for_group(self, table_name: str, col_name: str, items: List[Any]) -> str:
        table_ref = self._table_short_name(table_name)
        table_ref_out = self._table_out_name(table_name)
        clean_col = re.sub(r"\s+", " ", str(col_name or "значение")).strip()
        status = self._table_group_status(items)
        subjects = [self._table_row_subject_from_diff(item) for item in items]

        if status == "row_added":
            return self._ensure_sentence_period(f"В {table_ref} введены записи для {self._format_table_subjects(subjects)}")
        if status == "row_removed":
            return self._ensure_sentence_period(f"Из {table_ref_out} аннулированы записи для {self._format_table_subjects(subjects)}")

        if self._is_connection_column(clean_col):
            if self._is_whole_column_change(items):
                return self._ensure_sentence_period(
                    f'В {table_ref} в графе "{clean_col}" изменилось место присоединения'
                )
            formatted_subjects = self._format_table_subjects(subjects)
            if formatted_subjects == "записей таблицы":
                return self._ensure_sentence_period(
                    f'В {table_ref} в графе "{clean_col}" изменилось место присоединения'
                )
            return self._ensure_sentence_period(
                f'В {table_ref} в графе "{clean_col}" изменилось место присоединения для {formatted_subjects}'
            )

        change_phrase = self._column_change_phrase(clean_col)


        if self._is_whole_column_change(items):
            if change_phrase == "изменилось значение":
                return self._ensure_sentence_period(
                    f'В {table_ref} в графе "{clean_col}" изменилось значение {self._column_subject_name(clean_col)}'
                )
            return self._ensure_sentence_period(
                f'В {table_ref} в графе "{clean_col}" {change_phrase}'
            )

        formatted_subjects = self._format_table_subjects(subjects)
        if formatted_subjects == "записей таблицы":
            if change_phrase == "изменилось значение":
                return self._ensure_sentence_period(
                    f'В {table_ref} в графе "{clean_col}" изменилось значение {self._column_subject_name(clean_col)}'
                )
            return self._ensure_sentence_period(
                f'В {table_ref} в графе "{clean_col}" {change_phrase}'
            )
        return self._ensure_sentence_period(
            f'В {table_ref} в графе "{clean_col}" {change_phrase} для {formatted_subjects}'
        )

    def _build_drawing_table_facts(self, table_diffs: List[Any]) -> List[ChangeFact]:

        raw_items = list(table_diffs or [])
        raw_items = self._drop_wire_position_key_artifacts(raw_items)


        add_keys: Dict[tuple[str, str], int] = defaultdict(int)
        remove_keys: Dict[tuple[str, str], int] = defaultdict(int)

        for item in raw_items:
            status = self._table_group_status([item])
            if status not in {"row_added", "row_removed"}:
                continue
            table_key = self._canonical_table_key(getattr(item, "table_name", None))
            subject = self._table_row_subject_from_diff(item)
            if not subject:
                continue
            key = (table_key, subject.lower().replace("ё", "е"))
            if status == "row_added":
                add_keys[key] += 1
            else:
                remove_keys[key] += 1

        cancel_keys: Dict[tuple[str, str], int] = {
            key: min(add_keys.get(key, 0), remove_keys.get(key, 0))
            for key in set(add_keys) & set(remove_keys)
        }
        used_cancel: Dict[tuple[str, str, str], int] = defaultdict(int)

        filtered_items: List[Any] = []
        for item in raw_items:
            status = self._table_group_status([item])
            if status in {"row_added", "row_removed"}:
                table_key = self._canonical_table_key(getattr(item, "table_name", None))
                subject = self._table_row_subject_from_diff(item)
                key = (table_key, subject.lower().replace("ё", "е")) if subject else None
                if key and cancel_keys.get(key, 0) > 0:
                    side = "added" if status == "row_added" else "removed"
                    used_key = (key[0], key[1], side)
                    if used_cancel[used_key] < cancel_keys[key]:
                        used_cancel[used_key] += 1
                        continue
            filtered_items.append(item)

        table_items_by_key: Dict[str, List[Any]] = defaultdict(list)
        table_display_name: Dict[str, str] = {}

        for item in filtered_items:
            raw_table_name = self._norm(getattr(item, "table_name", None)) or "Таблица 1"
            table_key = self._canonical_table_key(raw_table_name)
            table_display_name.setdefault(table_key, raw_table_name)
            table_items_by_key[table_key].append(item)

        facts: List[ChangeFact] = []
        skip_regular_table_keys: set[str] = set()


        for table_key, items in table_items_by_key.items():
            table_name = table_display_name.get(table_key, "Таблица 1")
            if not self._is_wire_table_name(table_name):
                continue

            added_wire_subjects: List[str] = []
            extra_unnumbered_subjects: List[str] = []

            for item in items:
                status = self._table_group_status([item])
                col_name = self._norm(getattr(item, "col_name", None)) or ""
                subject = self._table_row_subject_from_diff(item)
                if not subject or self._is_bad_table_subject(subject):
                    continue

                if status == "row_added" and self._is_wire_column_name(col_name):
                    added_wire_subjects.append(subject)
                    continue


                if status != "row_removed" and self._is_unnumbered_wire_subject(subject):
                    extra_unnumbered_subjects.append(subject)

            added_wire_subjects = self._unique_preserve_order(added_wire_subjects)
            if len(added_wire_subjects) >= 3:
                introduced_subjects = self._unique_preserve_order(added_wire_subjects + extra_unnumbered_subjects)
                note = self._ensure_sentence_period(
                    f"В {self._table_short_name(table_name)} введены провода {self._format_wire_subjects(introduced_subjects)}"
                )
                facts.append(ChangeFact(
                    fact_type="table_column_changed",
                    description=note,
                    source="tables",
                    payload={
                        "table_name": table_name,
                        "table_key": table_key,
                        "col_name": "Провод",
                        "row_keys": introduced_subjects,
                        "old_values": [],
                        "new_values": introduced_subjects,
                        "note": note,
                    },
                ))
                skip_regular_table_keys.add(table_key)

        grouped: Dict[tuple[str, str, str], List[Any]] = defaultdict(list)

        for table_key, items in table_items_by_key.items():
            if table_key in skip_regular_table_keys:
                continue
            for item in items:
                col_name = self._norm(getattr(item, "col_name", None)) or f"Графа {getattr(item, 'col_index', '')}".strip()
                status_group = self._table_group_status([item])
                grouped[(table_key, col_name, status_group)].append(item)

        for (table_key, col_name, _status_group), items in grouped.items():
            table_name = table_display_name.get(table_key, "Таблица 1")
            note = self._drawing_table_note_for_group(table_name, col_name, items)
            facts.append(ChangeFact(
                fact_type="table_column_changed",
                description=note,
                source="tables",
                payload={
                    "table_name": table_name,
                    "table_key": table_key,
                    "col_name": col_name,
                    "row_keys": [self._table_row_subject_from_diff(x) for x in items if self._table_row_subject_from_diff(x)],
                    "old_values": [getattr(x, "v1_val", None) for x in items if getattr(x, "v1_val", None) is not None],
                    "new_values": [getattr(x, "v2_val", None) for x in items if getattr(x, "v2_val", None) is not None],
                    "note": note,
                },
            ))
        return facts

    def _build_e4_table_facts(self, table_diffs: List[Any]) -> List[ChangeFact]:

        facts: List[ChangeFact] = []
        grouped: Dict[str, List[Any]] = defaultdict(list)

        for item in table_diffs or []:
            table_name = self._norm(getattr(item, "table_name", None)) or "Таблица 1"
            grouped[table_name].append(item)

        for table_name, items in grouped.items():
            note = self._format_e4_table_note(table_name, items)
            facts.append(ChangeFact(
                fact_type="e4_table_changed",
                description=note,
                source="tables",
                payload={"table_name": table_name, "note": note},
            ))

        return facts

    def _format_e4_table_note(self, table_name: str, items: List[Any]) -> str:
        table_label = self._table_label_for_notice(table_name)
        bullets: List[str] = []

        marking_connectors: List[str] = []
        generic_marking_changed = False

        for item in items or []:
            status = str(getattr(item, "status", "") or "").strip().lower()
            if status not in {"modified", "added", "removed"}:
                continue
            if not self._is_e4_marking_diff(item):
                continue

            connector = self._extract_e4_connector_designation(item)
            if connector:
                marking_connectors.append(connector)
            elif status == "modified":
                generic_marking_changed = True

        for connector in self._unique_preserve_order(marking_connectors):
            bullets.append(f'изменена маркировка "{connector}"')

        if generic_marking_changed and not bullets:
            bullets.append("изменена маркировка")


        added_rows: List[str] = []
        removed_rows: List[str] = []
        for item in items or []:
            status = str(getattr(item, "status", "") or "").strip().lower()
            if status not in {"row_added", "row_removed"}:
                continue
            if self._is_probable_e4_header_artifact(item):
                continue
            row_key = self._clean_e4_row_key(getattr(item, "row_key", None))
            if not row_key:
                continue
            if status == "row_added":
                added_rows.append(row_key)
            else:
                removed_rows.append(row_key)

        if removed_rows:
            bullets.append("аннулированы записи для " + self._quote_join(self._unique_preserve_order(removed_rows)))
        if added_rows:
            bullets.append("введены записи для " + self._quote_join(self._unique_preserve_order(added_rows)))

        if not bullets:
            return f"Изменена {table_label}."

        return self._format_bulleted_table_note(f"Изменена {table_label}:", bullets)

    @staticmethod
    def _table_label_for_notice(table_name: Optional[str]) -> str:
        text = re.sub(r"\s+", " ", str(table_name or "")).strip()
        m = re.search(r"таблиц[аые]?\s*(\d+)", text, flags=re.IGNORECASE)
        if m:
            return f"таблица {m.group(1)}"
        return "таблица"

    def _is_e4_marking_diff(self, item: Any) -> bool:
        col_name = self._norm(getattr(item, "col_name", None)) or ""
        if "маркиров" in col_name.lower().replace("ё", "е"):
            return True

        col_index0 = self._safe_int(getattr(item, "col_index", None), default=0) - 1
        for attr in ("column_names", "prev_row_values_v1", "prev_row_values_v2"):
            values = getattr(item, attr, None) or []
            if 0 <= col_index0 < len(values):
                value = self._norm(values[col_index0]) or ""
                if "маркиров" in value.lower().replace("ё", "е"):
                    return True
        return False

    def _extract_e4_connector_designation(self, item: Any) -> Optional[str]:
        candidates: List[str] = []
        col_name = self._norm(getattr(item, "col_name", None))
        if col_name:
            candidates.append(col_name)

        col_index0 = self._safe_int(getattr(item, "col_index", None), default=0) - 1
        for attr in ("column_names", "prev_row_values_v2", "prev_row_values_v1"):
            values = getattr(item, attr, None) or []
            if 0 <= col_index0 < len(values):
                value = self._norm(values[col_index0])
                if value:
                    candidates.append(value)

        for text in candidates:
            connector = self._extract_connector_designation_from_text(text)
            if connector:
                return connector
        return None

    def _extract_connector_designation_from_text(self, text: Optional[str]) -> Optional[str]:
        if not text:
            return None

        normalized = self._normalize_connector_designation_text(str(text))
        parts = [part.strip() for part in re.split(r"[/|]", normalized) if part.strip()]
        parts = list(reversed(parts)) if parts else [normalized]

        for part in parts:
            # Берем именно позиционные обозначения соединителей, а не значения маркировки
            # вроде LAN 4 или 12V.
            for match in re.finditer(r"\b((?:X|XP|XS|XT|XR|XC|XW)\d{1,4})\b", part, flags=re.IGNORECASE):
                return match.group(1).upper()
        return None

    @staticmethod
    def _normalize_connector_designation_text(text: str) -> str:
        translate = str.maketrans({
            "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
            "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
            "а": "A", "в": "B", "е": "E", "к": "K", "м": "M", "н": "H",
            "о": "O", "р": "P", "с": "C", "т": "T", "х": "X",
        })
        return str(text or "").translate(translate)

    def _is_probable_e4_header_artifact(self, item: Any) -> bool:
        values: List[str] = []
        for attr in ("row_values_v1", "row_values_v2"):
            raw_values = getattr(item, attr, None) or []
            values.extend(self._norm(value) or "" for value in raw_values)

        values = [value for value in values if value]
        if not values:
            return True

        lowered = " ".join(values).lower().replace("ё", "е")
        if any(word in lowered for word in ("обозначение", "маркировка", "код")):
            return True

        normalized_values = [self._normalize_connector_designation_text(value).upper() for value in values]
        has_connector = any(re.fullmatch(r"(?:X|XP|XS|XT|XR|XC|XW)\d{1,4}", value) for value in normalized_values)
        has_only_small_numbers = any(re.fullmatch(r"\d{1,2}", value) for value in normalized_values)
        has_real_code = any(re.fullmatch(r"[A-ZА-ЯЁ]\d{3,5}", value, flags=re.IGNORECASE) for value in values)

        return has_connector and has_only_small_numbers and not has_real_code

    def _clean_e4_row_key(self, value: Optional[str]) -> str:
        text = self._norm(value) or ""
        text = text.strip(" .;:-")
        if not text:
            return ""
        if self._looks_like_service_row_key(text):
            return ""
        return text

    @staticmethod
    def _looks_like_service_row_key(text: str) -> bool:
        low = str(text or "").lower().replace("ё", "е")
        return any(word in low for word in ("маркировка", "обозначение", "код"))

    @staticmethod
    def _quote_join(items: List[str]) -> str:
        return ", ".join(f'"{item}"' for item in items if item)

    @staticmethod
    def _format_bulleted_table_note(header: str, bullets: List[str]) -> str:
        clean_bullets = []
        seen = set()
        for bullet in bullets:
            value = re.sub(r"\s+", " ", str(bullet or "")).strip(" .;:")
            key = value.lower()
            if not value or key in seen:
                continue
            seen.add(key)
            clean_bullets.append(value)

        if not clean_bullets:
            return header.rstrip(":") + "."

        lines = [header]
        for idx, bullet in enumerate(clean_bullets):
            ending = "." if idx == len(clean_bullets) - 1 else ";"
            lines.append(f"- {bullet}{ending}")
        return "\n".join(lines)

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    
    def _uses_semantic_topics_contract(self, document_type: str) -> bool:

        return not (
            self._is_specification_type(document_type)
            or self._is_pe4_type(document_type)
        )

    def _system_prompt(self, document_type: str) -> str:
        if self._uses_semantic_topics_contract(document_type):
            return (
                "Ты выполняешь только семантическую интерпретацию изменений технических требований. "
                "Не формируй финальный текст извещения и не возвращай notes. "
                "Для каждого переданного пункта ТТ верни короткую предметную тему изменения. "
                "Не выдумывай номера, не копируй полный текст пункта, не меняй статус. "
                "Ответ должен быть только валидным JSON-объектом."
            )

        base = (
            "Ты формируешь только примечания для извещения об изменении по ЕСКД. "
            "Работай как строгий форматтер: не анализируй заново PDF, не придумывай изменения, "
            "не копируй сырой текст документа. Используй только канонические факты из user prompt. "
            "Ответ должен быть только валидным JSON-объектом."
        )
        if self._is_specification_type(document_type):
            return base + " Для спецификации группируй изменения по разделам. Количество оформляй списком: наименование + \"на новое значение\", без старого значения."
        if self._is_pe4_type(document_type):
            return base + " Для перечня элементов используй формулировки: \"Введена применяемость ...\" и \"Изменено позиционное обозначение ...\". Не используй кавычки вокруг наименований."
        if self._is_e4_type(document_type):
            return base + " Для Э4 отдельно отражай графику схемы, таблицы и ТТ."
        return base

    def _build_user_prompt(self, facts: ChangeFactsBundle, template_text: str, additional_instructions: Optional[str]) -> str:
        if self._uses_semantic_topics_contract(facts.document_type):
            return self._build_semantic_topics_prompt(facts, additional_instructions)
        return self._build_legacy_notes_prompt(facts, template_text, additional_instructions)

    def _build_semantic_topics_prompt(self, facts: ChangeFactsBundle, additional_instructions: Optional[str]) -> str:
        extra = additional_instructions.strip() if additional_instructions else ""

        tt_items: List[Dict[str, Any]] = []
        non_tt_context: List[Dict[str, Any]] = []

        for fact in facts.facts:
            if fact.fact_type in {"tech_requirement_added", "tech_requirement_modified", "tech_requirement_removed"}:
                if fact.fact_type == "tech_requirement_added":
                    status = "added"
                    number = fact.number_new or fact.number_old
                elif fact.fact_type == "tech_requirement_removed":
                    status = "removed"
                    number = fact.number_old or fact.number_new
                else:
                    status = "modified"
                    number = fact.number_new or fact.number_old

                tt_items.append(
                    {
                        "number": number,
                        "status": status,
                        "topic_hint": self._topic_from_requirement_fact(fact),
                        "old_text": self._compact_prompt_text(fact.old_value),
                        "new_text": self._compact_prompt_text(fact.new_value),
                    }
                )

            elif fact.fact_type == "graphics_changed":
                non_tt_context.append({"kind": "graphics", "changed": True})

            elif fact.fact_type in {"table_changed", "table_column_changed"} or fact.fact_type.startswith("e4_table_"):
                non_tt_context.append({"kind": "table", "note": self._short_table_note_from_text(fact.description)})

            elif fact.fact_type in {"mass_changed", "scale_changed", "title_changed"}:
                non_tt_context.append(
                    {
                        "kind": "metadata",
                        "fact_type": fact.fact_type,
                        "old_value": fact.old_value,
                        "new_value": fact.new_value,
                    }
                )

        payload = {
            "task": "semantic_topics_for_changed_technical_requirements",
            "document_type": facts.document_type,
            "decimal_number": facts.decimal_number,
            "title": facts.title,
            "technical_requirements": tt_items,
            "non_tt_context_for_awareness_only": non_tt_context,
        }

        response_schema = {
            "tech_requirement_topics": [
                {
                    "number": 0,
                    "status": "modified",
                    "prepositional_phrase": "о краткой теме изменения в предложном падеже",
                }
            ]
        }

        lines = [
            "Определи краткие смысловые темы изменений технических требований.",
            "Ты НЕ формируешь финальный текст извещения. Финальный текст соберёт backend.",
            "Твоя задача — только помочь выбрать предметную тему для каждого изменённого пункта ТТ.",
            "",
            "ВХОДНЫЕ ДАННЫЕ:",
            json.dumps(payload, ensure_ascii=False, indent=2),
            "",
            "ВЕРНИ ТОЛЬКО JSON строго такого вида:",
            json.dumps(response_schema, ensure_ascii=False, indent=2),
            "",
            "ЖЕСТКИЕ ПРАВИЛА:",
            "1. Верни только JSON-объект, без markdown и пояснений.",
            "2. Верни ровно один объект в tech_requirement_topics на каждый объект из technical_requirements.",
            "3. number и status должны совпадать с входными данными. Не меняй их.",
            "4. prepositional_phrase — короткая тема в предложном падеже С предлогом 'о' или 'об'.",
            "5. Значение prepositional_phrase всегда должно начинаться с 'о ' или 'об '. Нельзя возвращать именительный падеж.",
            "6. Хорошо: 'о разделении электрических цепей', 'об экранировании кабеля', 'о прокладке жгута в плетенках'.",
            "7. Хорошо: 'о компенсации зазора между стволом жгута и кожухом соединителей', 'о маркировке проводников'.",
            "8. Плохо: 'разделение электрических цепей', 'соединение экранов', 'прокладка жгута', 'компенсация зазора'.",
            "9. Плохо: 'о разделение электрических цепей', 'о соединение экранов', 'о прокладка жгута', 'о компенсация зазора'.",
            "10. Плохо: 'заделку выполнить...', 'маркировать...', 'бирки поз. 1, 2 установить...', полный текст пункта.",
            "11. Если topic_hint уже выглядит корректно и кратко, переведи его в форму с предлогом 'о/об' и предложным падежом.",
            "12. Не обобщай важные слова из topic_hint: 'защитной намотке' нельзя заменять на 'защите'.",
            "13. Не указывай таблицы, графику, массу, масштаб и основную надпись в ответе.",
            "14. Не возвращай notes. Возвращай только tech_requirement_topics.",
        ]

        if extra:
            lines.extend(["", "Дополнительные указания пользователя:", extra])

        return "\n".join(lines)

    @staticmethod
    def _compact_prompt_text(text: Optional[str], max_len: int = 1200) -> Optional[str]:
        if text is None:
            return None
        value = re.sub(r"\s+", " ", str(text)).strip()
        if len(value) <= max_len:
            return value
        return value[: max_len - 3].rstrip() + "..."

    def _build_legacy_notes_prompt(self, facts: ChangeFactsBundle, template_text: str, additional_instructions: Optional[str]) -> str:
        extra = additional_instructions.strip() if additional_instructions else ""

        canonical_facts: List[Dict[str, Any]] = []
        table_required_notes: List[str] = []
        tt_lines: List[str] = []

        for fact in facts.facts:
            if fact.fact_type == "graphics_changed":
                canonical_facts.append(
                    {
                        "kind": "graphics",
                        "required_note": "Изменена графика чертежа.",
                        "rule": "Добавь эту note только если этот canonical fact присутствует.",
                    }
                )

            elif fact.fact_type in {"table_changed", "table_column_changed"}:
                table_required_notes.append(self._short_table_note_from_text(fact.description))

            elif fact.fact_type.startswith("spec_item_"):
                section = self._clean_spec_item_name((fact.payload or {}).get("section") or "Раздел")
                name = self._clean_spec_item_name((fact.payload or {}).get("name") or fact.description)
                field_name = self._clean_spec_item_name((fact.payload or {}).get("field_name") or "")
                new_value = self._normalize_quantity(fact.new_value) or self._norm(fact.new_value)
                old_value = self._normalize_quantity(fact.old_value) or self._norm(fact.old_value)

                if self._is_specification_type(facts.document_type):
                    if self._should_skip_spec_field_change(section, name, field_name or fact.fact_type, old_value, new_value):
                        if fact.fact_type not in {"spec_item_added", "spec_item_removed"}:
                            continue
                    if fact.fact_type == "spec_item_quantity_changed" and not self._is_plausible_spec_quantity(new_value):
                        continue
                    if fact.fact_type in {"spec_item_added", "spec_item_removed"} and self._is_bad_spec_added_removed(section, name, new_value or old_value):
                        continue

                canonical_facts.append(
                    {
                        "kind": "specification_item",
                        "fact_type": fact.fact_type,
                        "section": section,
                        "name": name,
                        "old_value": old_value,
                        "new_value": new_value,
                        "format_rule": "Для изменения количества пиши только новое значение: '<наименование> на <new_value>'. Старое значение не писать.",
                    }
                )

            elif fact.fact_type.startswith("element_item_"):
                name = self._clean_spec_item_name((fact.payload or {}).get("name") or fact.description)
                new_value = self._normalize_quantity(fact.new_value) or self._norm(fact.new_value)
                old_value = self._normalize_quantity(fact.old_value) or self._norm(fact.old_value)
                canonical_facts.append(
                    {
                        "kind": "element_item",
                        "fact_type": fact.fact_type,
                        "position_designation": (fact.payload or {}).get("position_designation") or fact.description,
                        "name": name,
                        "old_value": old_value,
                        "new_value": new_value,
                        "format_rule": "Для изменения наименования пиши 'Изменено наименование элемента для позиционного обозначения <position_designation>'. Для введенного элемента пиши 'Введена применяемость <name>'.",
                    }
                )

            elif fact.fact_type == "tech_requirement_added":
                num = fact.number_new
                topic = self._topic_from_requirement_fact(fact)
                about = self._about_topic(topic)
                line = f"введен пункт {about}"
                line = self._fix_russian_preposition(line.strip())
                tt_lines.append(line)
                canonical_facts.append({"kind": "tech_requirement", "status": "added", "number": num, "required_line": line})

            elif fact.fact_type == "tech_requirement_modified":
                num = fact.number_new or fact.number_old
                topic = self._topic_from_requirement_fact(fact)
                about = self._about_topic(topic)
                line = f"изменен пункт {num} {about}" if num else f"изменен пункт {about}"
                line = self._fix_russian_preposition(line.strip())
                tt_lines.append(line)
                canonical_facts.append({"kind": "tech_requirement", "status": "modified", "number": num, "required_line": line})

            elif fact.fact_type == "tech_requirement_removed":
                num = fact.number_old
                topic = self._topic_from_requirement_fact(fact)
                about = self._about_topic(topic)
                line = f"аннулирован пункт {about}"
                line = self._fix_russian_preposition(line.strip())
                tt_lines.append(line)
                canonical_facts.append({"kind": "tech_requirement", "status": "removed", "number": num, "required_line": line})

            elif fact.fact_type == "tech_requirement_renumbered":
                canonical_facts.append({"kind": "tech_requirement_renumbered", "required_note": None})

            elif fact.fact_type == "mass_changed" and fact.new_value:
                canonical_facts.append({"kind": "metadata", "required_note": f'В основной надписи в графе "Масса" изменено значение на {fact.new_value} кг.'})

            elif fact.fact_type == "scale_changed" and fact.new_value:
                canonical_facts.append({"kind": "metadata", "required_note": f'В основной надписи в графе "Масштаб" изменено значение на {fact.new_value}.'})

            elif fact.fact_type == "title_changed" and fact.new_value:
                canonical_facts.append({"kind": "metadata", "required_note": f'В основной надписи изменено наименование на "{fact.new_value}".'})

        for table_note in self._collapse_table_notes(table_required_notes):
            canonical_facts.append({"kind": "table", "required_note": table_note})

        prompt_payload = {
            "document_type": facts.document_type,
            "decimal_number": facts.decimal_number,
            "title": facts.title,
            "canonical_facts": canonical_facts,
            "required_tt_lines": tt_lines,
        }

        lines = [
            "Сформируй notes для извещения об изменении.",
            "Используй только канонические факты ниже.",
            "",
            "КАНОНИЧЕСКИЕ ФАКТЫ:",
            json.dumps(prompt_payload, ensure_ascii=False, indent=2),
            "",
            "ВЕРНИ ТОЛЬКО JSON строго такого вида:",
            json.dumps({"notes": []}, ensure_ascii=False, indent=2),
            "",
            "ПРАВИЛА:",
            "1. Верни только JSON-объект, без markdown и пояснений.",
            "2. Поле notes — массив строк.",
            "3. Не выдумывай изменения.",
            "4. Не копируй old_value/new_value целиком.",
        ]
        if extra:
            lines.extend(["", "Дополнительные указания пользователя:", extra])
        return "\n".join(lines)

    def _expected_tt_lines_from_facts(self, facts: ChangeFactsBundle) -> List[str]:

        lines: List[str] = []

        for fact in facts.facts:
            if fact.fact_type == "tech_requirement_added":
                num = fact.number_new
                topic = self._topic_from_requirement_fact(fact)
                about = self._about_topic(topic)
                line = f"введен пункт {about}"
                lines.append(self._fix_russian_preposition(line.strip()))

            elif fact.fact_type == "tech_requirement_modified":
                num = fact.number_new or fact.number_old
                topic = self._topic_from_requirement_fact(fact)
                about = self._about_topic(topic)
                line = f"изменен пункт {num} {about}" if num else f"изменен пункт {about}"
                lines.append(self._fix_russian_preposition(line.strip()))

            elif fact.fact_type == "tech_requirement_removed":
                num = fact.number_old
                topic = self._topic_from_requirement_fact(fact)
                about = self._about_topic(topic)
                line = f"аннулирован пункт {about}"
                lines.append(self._fix_russian_preposition(line.strip()))

        return self._unique_preserve_order([line for line in lines if line])

    @staticmethod
    def _normalize_line_for_validation(text: str) -> str:
        text = str(text or "").lower().replace("ё", "е")
        text = re.sub(r"[\s\u00a0]+", " ", text)
        text = text.replace(";", "").replace(".", "")
        text = text.replace('"', "").replace("«", "").replace("»", "")
        return text.strip()

    def _validate_llm_notes_against_facts(self, facts: ChangeFactsBundle, notes: List[str]) -> Optional[str]:
        if not notes:
            return None

        fact_types = {fact.fact_type for fact in facts.facts}
        fact_sources = {fact.source for fact in facts.facts if fact.source}
        notes_blob = "\n".join(str(note or "") for note in notes).lower()

        has_tt_added = "tech_requirement_added" in fact_types
        has_tt_removed = "tech_requirement_removed" in fact_types
        has_tt_modified = "tech_requirement_modified" in fact_types
        has_tt_any = has_tt_added or has_tt_removed or has_tt_modified or "tech_requirement_renumbered" in fact_types
        has_table = any(ft.startswith("table_") or ft in {"table_changed", "table_column_changed"} for ft in fact_types) or "tables" in fact_sources
        ignore_graphics_for_doc = self._is_specification_type(facts.document_type) or self._is_pe4_type(facts.document_type)
        has_graphics = ("graphics_changed" in fact_types or "graphics" in fact_sources) and not ignore_graphics_for_doc
        has_mass = "mass_changed" in fact_types

        graphics_words = ("графика", "графику", "графики", "графикой", "графическое", "графические")
        mentions_graphics = any(word in notes_blob for word in graphics_words)

        if mentions_graphics and not has_graphics:
            return "модель написала про графику, но во facts нет изменения графики"

        if "масса" in notes_blob and not has_mass and "таблиц" not in notes_blob:
            return "модель написала про массу как отдельное изменение, но во facts нет изменения массы в основной надписи"

        if has_table and "таблиц" not in notes_blob:
            return "модель не отразила изменения таблиц"

        if has_graphics and not mentions_graphics:
            return "модель не отразила изменения графики"

        if has_tt_any and not ("тт" in notes_blob or "тех" in notes_blob or "пункт" in notes_blob or "п." in notes_blob):
            return "модель не отразила изменения технических требований"

        raw_tt_markers = [
            "заделку выполнить",
            "маркировку по пп.",
            "бирки термоусадить",
            "неуказанные предельные отклонения",
            "позиционные обозначения согласно",
            "заделку проводов в соединители",
            "приемку кабеля выполнять",
        ]
        if has_tt_any:
            for note in notes:
                low_note = str(note or "").lower().strip()
                if (
                    low_note.startswith("изменены тт")
                    or low_note.startswith("изменены технические требования")
                    or low_note.startswith("- изменен пункт")
                    or low_note.startswith("- введен пункт")
                    or low_note.startswith("- аннулирован пункт")
                    or low_note.startswith("изменен пункт")
                    or low_note.startswith("введен пункт")
                    or low_note.startswith("аннулирован пункт")
                ):
                    continue
                if any(marker in low_note for marker in raw_tt_markers):
                    return "модель вернула сырой текст технических требований вместо формулировки для извещения"

        bad_tt_fragments = [
            "...",
            "…",
            " о неуказанные ",
            " об экранирование ",
            " о бирки ",
            " о маркировать ",
            " выполнить пле",
        ]
        if has_tt_any and any(fragment in notes_blob for fragment in bad_tt_fragments):
            return "модель вернула обрезанную или грамматически неверную формулировку ТТ"

        missing_numbers: List[str] = []
        for fact in facts.facts:
            if fact.fact_type not in {"tech_requirement_added", "tech_requirement_removed", "tech_requirement_modified"}:
                continue
            num = fact.number_new or fact.number_old
            if not num:
                continue
            num_text = str(num)
            patterns = [
                rf"пункт\s+{re.escape(num_text)}\b",
                rf"п\.\s*{re.escape(num_text)}\b",
            ]
            if not any(re.search(pattern, notes_blob, flags=re.IGNORECASE) for pattern in patterns):
                missing_numbers.append(num_text)

        if missing_numbers:
            return "модель не отразила пункты ТТ: " + ", ".join(self._unique_preserve_order(missing_numbers))

        expected_tt_lines = self._expected_tt_lines_from_facts(facts)
        if expected_tt_lines:
            normalized_blob = self._normalize_line_for_validation(notes_blob)
            missing_expected_lines = [
                line for line in expected_tt_lines
                if self._normalize_line_for_validation(line) not in normalized_blob
            ]
            if missing_expected_lines:
                return "модель исказила формулировки ТТ: " + "; ".join(missing_expected_lines)

        if "введено требование" in notes_blob and has_tt_added:
            return "модель использовала слабую формулировку 'введено требование' вместо 'введен пункт N'"

        if "введено требование" in notes_blob and not has_tt_added:
            return "модель написала про введенное требование, но во facts нет tech_requirement_added"

        if "аннулирован" in notes_blob and "п" in notes_blob and not has_tt_removed:
            return "модель написала про аннулированный пункт, но во facts нет tech_requirement_removed"

        return None
    def _chat_completion(self, token: str, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        url = f"{self.base_url}/v1/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        response = requests.post(url, headers=headers, json=payload, timeout=self.timeout, verify=self._verify_value())
        response.raise_for_status()
        return response.json()

    def _get_access_token(self) -> str:
        credentials = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        basic = base64.b64encode(credentials).decode("utf-8")
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "X-IBM-Client-Id": self.client_id,
        }
        response = requests.post(self.oauth_url, headers=headers, data={"scope": self.scope}, timeout=self.timeout, verify=self._verify_value())
        response.raise_for_status()
        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise ValueError("В ответе OAuth отсутствует access_token")
        return access_token

    def _load_template_for_doc_type(self, document_type: str) -> str:
        if self._is_specification_type(document_type):
            if self.spec_template_path.exists():
                return self.spec_template_path.read_text(encoding="utf-8").strip()
            return DEFAULT_SPEC_TEMPLATE
        if self._is_pe4_type(document_type):
            if self.pe4_template_path.exists():
                return self.pe4_template_path.read_text(encoding="utf-8").strip()
            return DEFAULT_PE4_TEMPLATE
        if self._is_e4_type(document_type):
            if self.e4_template_path.exists():
                return self.e4_template_path.read_text(encoding="utf-8").strip()
            return DEFAULT_E4_TEMPLATE
        if self.template_path.exists():
            return self.template_path.read_text(encoding="utf-8").strip()
        return DEFAULT_DRAWING_TEMPLATE

    @staticmethod
    def _extract_content(response_json: Dict[str, Any]) -> str:
        choices = response_json.get("choices", [])
        if not choices:
            raise ValueError("Пустой ответ от GigaChat")
        message = choices[0].get("message", {})
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("В ответе GigaChat отсутствует content")
        return content.strip()

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        return text.strip()

    def _extract_first_json_string(self, text: str) -> str:
        text = self._strip_code_fences(text)
        decoder = JSONDecoder()
        for idx, char in enumerate(text):
            if char not in "[{":
                continue
            try:
                parsed, _end = decoder.raw_decode(text[idx:])
                return json.dumps(parsed, ensure_ascii=False)
            except JSONDecodeError:
                continue
        raise ValueError("Не удалось выделить JSON из ответа модели")

    @staticmethod
    def _is_schema_echo(obj: Dict[str, Any]) -> bool:
        if not isinstance(obj, dict):
            return False
        schema_keys = {"type", "properties", "required", "additionalProperties"}
        return len(schema_keys.intersection(obj.keys())) >= 2 and "notes" not in obj

    def _notes_from_semantic_topics_response(self, facts: ChangeFactsBundle, parsed: Dict[str, Any]) -> List[str]:

        topics_by_key = self._semantic_topics_by_key(parsed)
        is_e4 = self._is_e4_type(facts.document_type)

        graphics_notes: List[str] = []
        table_notes: List[str] = []
        metadata_notes: List[str] = []
        tt_lines: List[str] = []

        for fact in facts.facts:
            if fact.fact_type == "graphics_changed" or fact.fact_type.startswith("e4_graphic_"):
                graphics_notes.append("Изменена графика схемы." if is_e4 else "Изменена графика чертежа.")

            elif (
                fact.fact_type in {"table_changed", "table_column_changed"}
                or fact.fact_type.startswith("e4_table_")
            ):
                table_text = str((fact.payload or {}).get("note") or fact.description or "Изменена таблица.").strip()
                table_notes.append(table_text)

            elif fact.fact_type == "mass_changed" and fact.new_value:
                metadata_notes.append(f'В основной надписи в графе "Масса" изменено значение на {fact.new_value} кг.')

            elif fact.fact_type == "scale_changed" and fact.new_value:
                metadata_notes.append(f'В основной надписи в графе "Масштаб" изменено значение на {fact.new_value}.')

            elif fact.fact_type == "title_changed" and fact.new_value:
                metadata_notes.append(f'В основной надписи изменено наименование на "{fact.new_value}".')

            elif fact.fact_type in {"tech_requirement_added", "tech_requirement_modified", "tech_requirement_removed"}:
                line = self._tt_line_from_semantic_topic(fact, topics_by_key)
                if line:
                    tt_lines.append(line)

        notes: List[str] = []
        notes.extend(self._unique_preserve_order(graphics_notes))
        if is_e4:
            notes.extend(self._sanitize_notes(table_notes))
        else:
            notes.extend(self._collapse_table_notes(table_notes))

        tt_parts = self._unique_preserve_order(tt_lines)
        if tt_parts:
            notes.append(
                "Изменены ТТ:\n"
                + "\n".join(
                    f"- {self._strip_trailing_period(line)};"
                    if idx < len(tt_parts) - 1
                    else f"- {self._strip_trailing_period(line)}."
                    for idx, line in enumerate(tt_parts)
                )
            )

        notes.extend(self._unique_preserve_order(metadata_notes))
        return self._sanitize_notes(notes) or ["Изменения требуют уточнения."]

    def _semantic_topics_by_key(self, parsed: Dict[str, Any]) -> Dict[tuple, str]:

        if not isinstance(parsed, dict):
            return {}

        raw_topics = parsed.get("tech_requirement_topics") or parsed.get("technical_requirement_topics") or []
        if not isinstance(raw_topics, list):
            return {}

        result: Dict[tuple, str] = {}
        for item in raw_topics:
            if not isinstance(item, dict):
                continue

            number = item.get("number")
            status = str(item.get("status") or "").strip().lower()

            phrase_raw = item.get("prepositional_phrase")
            if phrase_raw is not None:
                phrase = self._clean_prepositional_phrase(str(phrase_raw))
            else:
                # Старый контракт: topic без предлога. Превращаем в корректное "о/об ...".
                topic = self._clean_semantic_topic(item.get("topic"))
                phrase = self._about_topic(topic) if topic else ""

            try:
                number_key = int(number) if number is not None else None
            except (TypeError, ValueError):
                number_key = None

            if number_key is None or status not in {"added", "modified", "removed"} or not phrase:
                continue

            result[(number_key, status)] = phrase
        return result

    def _tt_line_from_semantic_topic(self, fact: ChangeFact, topics_by_key: Dict[tuple, str]) -> str:
        if fact.fact_type == "tech_requirement_added":
            status = "added"
            number = fact.number_new or fact.number_old
            verb = "введен пункт"
            include_number = False
        elif fact.fact_type == "tech_requirement_removed":
            status = "removed"
            number = fact.number_old or fact.number_new
            verb = "аннулирован пункт"
            include_number = False
        else:
            status = "modified"
            number = fact.number_new or fact.number_old
            verb = "изменен пункт"
            include_number = True

        fallback_topic = self._topic_from_requirement_fact(fact)
        fallback_phrase = self._about_topic(fallback_topic)
        candidate_phrase = topics_by_key.get((int(number), status)) if number is not None else None
        phrase = self._validated_prepositional_phrase(candidate_phrase, fallback_phrase)

        if include_number and number:
            line = f"{verb} {number} {phrase}"
        else:
            line = f"{verb} {phrase}"

        return self._fix_russian_preposition(line.strip())

    def _validated_prepositional_phrase(self, candidate: Optional[str], fallback_phrase: str) -> str:
        fallback_clean = self._clean_prepositional_phrase(fallback_phrase)
        candidate_clean = self._clean_prepositional_phrase(candidate)

        if not fallback_clean:
            fallback_clean = "о содержании пункта"

        if not candidate_clean:
            return fallback_clean

        if self._prepositional_phrase_is_bad(candidate_clean):
            return fallback_clean

        candidate_topic = self._strip_about_preposition(candidate_clean)
        fallback_topic = self._strip_about_preposition(fallback_clean)

        # Если локальный fallback не смог выделить смысл и вернул общий текст
        # "о содержании пункта", нельзя затирать нормальную тему от GigaChat
        # вроде "о замене сечения кабеля".
        if fallback_topic in {"содержании пункта", "требовании"}:
            return candidate_clean

        if not self._semantic_topic_is_compatible(candidate_topic, fallback_topic):
            return fallback_clean

        return candidate_clean

    def _clean_prepositional_phrase(self, phrase: Optional[str]) -> str:
        text = str(phrase or "").replace("ё", "е").strip()
        text = re.sub(r"\s+", " ", text)
        text = text.strip(" .;:-")
        if not text:
            return ""

        # Если модель по старой привычке вернула тему без предлога, не склеиваем
        # её как есть. Прогоняем через _about_topic(), где есть базовая нормализация
        # частых именительных форм: "разделение" -> "о разделении".
        if not re.match(r"^(о|об)\s+", text, flags=re.IGNORECASE):
            return self._about_topic(text)

        text = self._fix_known_prepositional_phrase(text)
        text = self._fix_russian_preposition(text)
        return text.strip()

    @staticmethod
    def _strip_about_preposition(text: str) -> str:
        return re.sub(r"^(о|об)\s+", "", str(text or "").strip(), flags=re.IGNORECASE).strip()

    @staticmethod
    def _prepositional_phrase_is_bad(phrase: str) -> bool:
        low = str(phrase or "").lower().strip()
        if not low:
            return True

        if not re.match(r"^(о|об)\s+", low):
            return True

        # Частые ошибки GigaChat: предлог есть, но тема осталась в именительном.
        bad_fragments = [
            "о разделение ",
            "о соединение ",
            "о выполнение ",
            "о экранирование ",
            "о прокладка ",
            "о заделка ",
            "о компенсация ",
            "о скрутка ",
            "о маркировка ",
            "о размещение ",
            "о контровка ",
            "о защитная намотка",
            "о предельные отклонения",
            "об экранирование ",
            "...",
            "…",
            "заделку выполнить",
            "маркировать ",
            "бирки поз.",
            "установить в соответствии",
            "выполнить пле",
            ").replace(",
            "йоколоворп",
            "ьтаворижаднаб",
            "икнетелп",
        ]
        if any(fragment in low for fragment in bad_fragments):
            return True

        if len(low) > 150:
            return True

        return False

    def _validated_semantic_topic(self, candidate: Optional[str], fallback: str) -> str:
        fallback_clean = self._clean_semantic_topic(fallback)
        candidate_clean = self._clean_semantic_topic(candidate)

        if not candidate_clean:
            return fallback_clean

        if self._semantic_topic_is_bad(candidate_clean):
            return fallback_clean

        if not self._semantic_topic_is_compatible(candidate_clean, fallback_clean):
            return fallback_clean

        return candidate_clean

    @staticmethod
    def _clean_semantic_topic(topic: Optional[str]) -> str:
        text = str(topic or "").replace("ё", "е").strip()
        text = re.sub(r"\s+", " ", text)
        text = text.strip(" .;:-")
        text = re.sub(r"^(о|об)\s+", "", text, flags=re.IGNORECASE)
        return text.strip()

    @staticmethod
    def _semantic_topic_is_bad(topic: str) -> bool:
        low = str(topic or "").lower().strip()
        if not low:
            return True

        bad_fragments = [
            "...",
            "…",
            "заделку выполнить",
            "маркировать ",
            "бирки поз.",
            "установить в соответствии",
            "выполнить пле",
            "полный текст",
        ]
        if any(fragment in low for fragment in bad_fragments):
            return True

        # Слишком длинная тема почти всегда является куском сырого ТТ.
        if len(low) > 140:
            return True

        return False

    @staticmethod
    def _semantic_topic_tokens(topic: str) -> List[str]:
        stop = {
            "изменение", "изменении", "пункт", "пункта", "требование", "требования",
            "данных", "значений", "значения", "выполнить", "согласно", "соответствии",
            "между", "после", "перед", "кроме", "путем", "поз", "гост", "ост",
        }
        words = re.findall(r"[а-яa-z0-9]+", str(topic or "").lower().replace("ё", "е"))
        return [w for w in words if len(w) >= 5 and w not in stop]

    def _semantic_topic_is_compatible(self, candidate: str, fallback: str) -> bool:
        fallback_clean = self._clean_semantic_topic(fallback).lower()
        candidate_clean = self._clean_semantic_topic(candidate).lower()

        if not fallback_clean:
            return True


        anchor_groups = [
            ("намот", "намот"),
            ("экранир", "экранир"),
            ("компенсац", "компенсац"),
            ("зазор", "зазор"),
            ("размещ", "размещ"),
            ("бир", "бир"),
            ("маркиров", "маркиров"),
            ("скрутк", "скрутк"),
            ("скручив", "скручив"),
            ("предельн", "предельн"),
            ("отклонен", "отклонен"),
        ]
        for fallback_anchor, candidate_anchor in anchor_groups:
            if fallback_anchor in fallback_clean and candidate_anchor not in candidate_clean:
                return False


        if "защитной намот" in fallback_clean and "защит" in candidate_clean and "намот" not in candidate_clean:
            return False

        fallback_tokens = self._semantic_topic_tokens(fallback_clean)
        candidate_tokens = self._semantic_topic_tokens(candidate_clean)

        if not fallback_tokens:
            return True

        def same_root(a: str, b: str) -> bool:
            a = str(a or "").lower()
            b = str(b or "").lower()
            if not a or not b:
                return False
            if a == b:
                return True

            roots = (
                "провод", "кабел", "жгут", "скруч", "скручив", "проклад",
                "прокладыв", "маркиров", "соедин", "экран", "экранир",
                "задел", "обжим", "наконеч", "бир", "контров", "резьб",
                "компенсац", "зазор", "кожух", "ствол", "цеп",
            )
            if any(root in a and root in b for root in roots):
                return True
            return len(a) >= 6 and len(b) >= 6 and (a.startswith(b[:6]) or b.startswith(a[:6]))

        matches = sum(
            1
            for fallback_token in fallback_tokens
            if any(same_root(fallback_token, candidate_token) for candidate_token in candidate_tokens)
        )


        if len(fallback_tokens) <= 2:
            return matches >= 1


        if matches >= 2:
            return True

        return (matches / max(len(fallback_tokens), 1)) >= 0.5

    def _force_canonical_tt_notes(self, facts: ChangeFactsBundle, notes: List[str]) -> List[str]:

        expected_tt_lines = self._expected_tt_lines_from_facts(facts)
        if not expected_tt_lines:
            return notes or []

        cleaned_notes: List[str] = []
        tt_insert_index: Optional[int] = None

        for note in notes or []:
            text = str(note or "").strip()
            if not text:
                continue

            low = text.lower().strip()
            is_tt_note = (
                low.startswith("изменены тт")
                or low.startswith("изменены технические требования")
                or low.startswith("- изменен пункт")
                or low.startswith("- введен пункт")
                or low.startswith("- аннулирован пункт")
                or low.startswith("изменен пункт")
                or low.startswith("введен пункт")
                or low.startswith("аннулирован пункт")
                or "изменен пункт" in low
                or "введен пункт" in low
                or "аннулирован пункт" in low
            )

            if is_tt_note:
                if tt_insert_index is None:
                    tt_insert_index = len(cleaned_notes)
                continue

            cleaned_notes.append(text)

        canonical_tt_note = (
            "Изменены ТТ:\n"
            + "\n".join(
                f"- {self._strip_trailing_period(line)};"
                if idx < len(expected_tt_lines) - 1
                else f"- {self._strip_trailing_period(line)}."
                for idx, line in enumerate(expected_tt_lines)
            )
        )

        if tt_insert_index is None:
            cleaned_notes.append(canonical_tt_note)
        else:
            cleaned_notes.insert(tt_insert_index, canonical_tt_note)

        return self._sanitize_notes(cleaned_notes)

    def _build_notice_response(self, diff: FullDiff, facts: ChangeFactsBundle, notes: List[str]) -> ChangeNoticeResponse:

        notes = self._sort_notice_notes(self._normalize_final_notice_notes(notes, facts.document_type))

        block = NoticeBlock(
            doc_type=facts.document_type,
            decimal_number=facts.decimal_number,
            action=self._build_action_from_diff(diff),
            notes=notes,
            journal_entry="Журнал № XX, запись № XX.",
            change_number=self._resolve_change_number_from_diff(diff),
        )
        return ChangeNoticeResponse(
            notice_id=self._generate_notice_id(),
            block=[block],
            formatted_text=self._format_notice_text([block]),
            message="Анализ завершён. Блок извещения сформирован.",
        )

    @staticmethod
    def _generate_notice_id() -> str:
        return f"CN-{uuid.uuid4().hex[:6].upper()}"

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

    def _resolve_change_number_from_diff(self, diff: FullDiff) -> Optional[str]:

        return self._normalize_change_number_value(getattr(diff.metadata_v2, "change_number", None))

    def _build_action_from_diff(self, diff: FullDiff) -> str:

        old_count = self._metadata_sheet_count(diff.metadata_v1) or self._extract_sheet_count(diff.metadata_v1.raw_stamp_snippet)
        new_count = self._metadata_sheet_count(diff.metadata_v2) or self._extract_sheet_count(diff.metadata_v2.raw_stamp_snippet)
        sheet_count = new_count or old_count or 1

        old_statuses = self._metadata_sheet_statuses(diff.metadata_v1)
        new_statuses = self._metadata_sheet_statuses(diff.metadata_v2)

        replaced_pages = {page for page, status in new_statuses.items() if status == "replace"}
        introduced_pages = {page for page, status in new_statuses.items() if status == "new"}


        if old_count and new_count and new_count > old_count:
            introduced_pages.update(range(old_count + 1, new_count + 1))


        annulled_pages = set()
        if old_count and new_count and old_count > new_count:
            annulled_pages.update(range(new_count + 1, old_count + 1))


        replaced_pages.difference_update(introduced_pages)

        if sheet_count <= 1 and not introduced_pages and not annulled_pages:
            return "Заменить"

        comparable_count = min(old_count or sheet_count, new_count or sheet_count)
        all_existing_pages = set(range(1, comparable_count + 1)) if comparable_count else set()

        if all_existing_pages and replaced_pages.issuperset(all_existing_pages) and not introduced_pages and not annulled_pages:
            return "Заменить"


        if not replaced_pages and not introduced_pages and not annulled_pages:
            replaced_pages = self._collect_changed_pages_from_diff(diff, max(old_count or sheet_count, new_count or sheet_count))
            if all_existing_pages and replaced_pages.issuperset(all_existing_pages):
                return "Заменить"

        parts: List[str] = []
        if replaced_pages:
            parts.append(self._format_replace_sheets_action(replaced_pages))
        if introduced_pages:
            parts.append(self._format_intro_sheets_action(introduced_pages))
        if annulled_pages:
            parts.append(self._format_annul_sheets_action(annulled_pages))

        return ", ".join(parts) if parts else "Заменить"

    @staticmethod
    def _extract_sheet_count(text: Optional[str]) -> Optional[int]:

        if not text:
            return None

        normalized = re.sub(r"\s+", " ", str(text)).strip()
        patterns = [
            r"Лист\s+Листов\s+(\d{1,3})\s+(\d{1,3})",
            r"Листов\s+(\d{1,3})",
        ]

        for pattern in patterns:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue

            value = match.group(2) if len(match.groups()) >= 2 else match.group(1)
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue

            if 1 <= number <= 999:
                return number

        return None

    @staticmethod
    def _metadata_sheet_count(metadata: DocMetadata) -> Optional[int]:
        value = getattr(metadata, "sheet_count", None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _metadata_sheet_statuses(metadata: DocMetadata) -> Dict[int, str]:
        raw = getattr(metadata, "sheet_statuses", None) or {}
        result: Dict[int, str] = {}
        if not isinstance(raw, dict):
            return result
        for key, value in raw.items():
            try:
                page = int(key)
            except (TypeError, ValueError):
                continue
            status = str(value or "").strip().lower()
            if status in {"replace", "new"}:
                result[page] = status
        return result

    @staticmethod
    def _format_page_ranges(pages: set[int]) -> str:
        values = sorted(int(p) for p in pages if int(p) > 0)
        if not values:
            return ""
        ranges = []
        start = prev = values[0]
        for value in values[1:]:
            if value == prev + 1:
                prev = value
                continue
            ranges.append(f"{start}" if start == prev else f"{start}-{prev}")
            start = prev = value
        ranges.append(f"{start}" if start == prev else f"{start}-{prev}")
        return ", ".join(ranges)

    def _format_replace_sheets_action(self, pages: set[int]) -> str:
        ranges = self._format_page_ranges(pages)
        return f"Лист {ranges} заменить" if len(pages) == 1 else f"Листы {ranges} заменить"

    def _format_intro_sheets_action(self, pages: set[int]) -> str:
        ranges = self._format_page_ranges(pages)
        return f"лист {ranges} вводится вновь" if len(pages) == 1 else f"листы {ranges} вводятся вновь"

    def _format_annul_sheets_action(self, pages: set[int]) -> str:
        ranges = self._format_page_ranges(pages)
        return f"лист {ranges} аннулировать" if len(pages) == 1 else f"листы {ranges} аннулировать"

    def _collect_changed_pages_from_diff(self, diff: FullDiff, max_page: int) -> set[int]:
        pages: set[int] = set()

        for item in getattr(diff, "tables", []) or []:
            page_num = getattr(item, "page_num", None)
            if page_num:
                pages.add(int(page_num))

        for region in getattr(getattr(diff, "graphics", None), "changed_regions", []) or []:
            page_num = getattr(region, "page_num", None)
            if page_num:
                pages.add(int(page_num))

        for item in getattr(diff, "specification_items", []) or []:
            for attr in ("v1_item", "v2_item"):
                src = getattr(item, attr, None)
                page_num = getattr(src, "page_num", None) if src is not None else None
                if page_num:
                    pages.add(int(page_num))

        for item in getattr(diff, "element_items", []) or []:
            for attr in ("v1_item", "v2_item"):
                src = getattr(item, attr, None)
                page_num = getattr(src, "page_num", None) if src is not None else None
                if page_num:
                    pages.add(int(page_num))


        if getattr(diff, "tech_requirements", None):
            pages.add(1)

        return {p for p in pages if 1 <= p <= max_page}

    @staticmethod
    def _format_notice_text(blocks: List[NoticeBlock]) -> str:
        parts: List[str] = []

        for block in blocks:
            parts.append(block.decimal_number)
            parts.append(block.action)

            if block.notes:
                parts.append("Примечания:")
                note_number = 1

                for note in block.notes:
                    note_lines = [
                        line.rstrip()
                        for line in str(note).splitlines()
                        if line.strip()
                    ]
                    if not note_lines:
                        continue

                    if note_lines[0].lstrip().startswith("-"):
                        parts.extend(note_lines)
                        continue

                    parts.append(f"{note_number} {note_lines[0]}")
                    note_number += 1

                    for extra_line in note_lines[1:]:
                        parts.append(extra_line)

            parts.append(block.journal_entry)

        return "\n".join(parts).strip()

    @staticmethod
    def _sort_notice_notes(notes: List[str]) -> List[str]:

        def priority(note: str) -> int:
            low = str(note or "").lower().strip()

            if "график" in low or "графика" in low:
                return 10

            if "таблиц" in low:
                return 20

            if (
                low.startswith("изменены тт")
                or low.startswith("изменены технические требования")
                or "пункт" in low
            ):
                return 30

            if "основной надписи" in low:
                return 40

            return 90

        # sorted в Python стабильный: notes с одинаковым приоритетом сохраняют исходный порядок.
        return sorted(notes, key=priority)

    def _normalize_final_notice_notes(self, notes: List[str], document_type: str = "") -> List[str]:
        result: List[str] = []
        collapse_table_notes = not (
            self._is_specification_type(document_type)
            or self._is_pe4_type(document_type)
            or self._is_e4_type(document_type)
        )
        current_tt_lines: List[str] = []

        def flush_tt() -> None:
            nonlocal current_tt_lines
            if current_tt_lines:
                result.append(self._format_tt_note_multiline("\n".join(current_tt_lines)))
                current_tt_lines = []

        for note in notes or []:
            text = str(note or "").strip()
            if not text:
                continue

            low = text.lower()

            if low.startswith("изменены тт:") or low.startswith("изменены технические требования:"):
                flush_tt()
                current_tt_lines = ["Изменены ТТ:"]
                tail = text.split(":", 1)[1].strip() if ":" in text else ""
                if tail:
                    current_tt_lines.append(tail)
                continue

            if text.lstrip().startswith("-") and current_tt_lines:
                current_tt_lines.append(text)
                continue

            flush_tt()

            if collapse_table_notes and "таблиц" in low:
                # Уже сгруппированный многострочный блок нельзя прогонять через
                # _short_table_note_from_text: он схлопнет его в "Изменена таблица".
                if re.match(r"^(?:В\s+таблице|Изменена\s+таблица)\s+\d+\s*:", text, flags=re.IGNORECASE):
                    result.append(text)
                else:
                    result.append(self._short_table_note_from_text(text))
                continue

            result.append(text)

        flush_tt()
        if collapse_table_notes:
            result = self._collapse_table_notes(result)
        return self._sanitize_notes(result)

    def _collapse_table_notes(self, notes: List[str]) -> List[str]:

        if not notes:
            return []

        result: List[str] = []
        grouped: Dict[str, List[str]] = {}
        group_placeholders: Dict[str, str] = {}
        generic_table_notes: List[str] = []
        generic_placeholder = "__GENERIC_TABLE_NOTES_PLACEHOLDER__"
        generic_placeholder_added = False

        def normalize_body(body: str) -> str:
            body = re.sub(r"\s+", " ", str(body or "").strip()).strip(".; ")
            if not body:
                return ""
            return body[:1].lower() + body[1:]

        def add_group(table_number: str, body: str) -> None:
            body = normalize_body(body)
            if not body:
                return
            placeholder = f"__TABLE_GROUP_{table_number}__"
            if table_number not in group_placeholders:
                group_placeholders[table_number] = placeholder
                result.append(placeholder)
                grouped[table_number] = []
            if body not in grouped[table_number]:
                grouped[table_number].append(body)

        def is_detailed_table_note(text: str) -> bool:
            low = str(text or "").lower().replace("ё", "е")
            return (
                ("в графе" in low and "измен" in low)
                or "введены записи" in low
                or "введены провода" in low
                or "аннулированы записи" in low
                or low.startswith("для ")
                or "изменена маркировка" in low
                or low.startswith("изменилась маркировка для")
            )

        for note in notes:
            text = str(note or "").strip()
            if not text:
                continue

            text_low = text.lower().replace("ё", "е")
            is_table_related = (
                "таблиц" in text_low
                or text_low.startswith("изменилась маркировка для")
                or "изменена маркировка" in text_low
            )
            if not is_table_related:
                result.append(text)
                continue

            # Уже сгруппированный блок нормализуем под финальный стиль:
            # "Изменена таблица 1 - ..." для одного подпункта и
            # "Изменена таблица 1:" + список для нескольких.
            grouped_block_match = re.match(
                r"^(?:В\s+таблице|Изменена\s+таблица)\s+(\d+)\s*:\s*(.*)$",
                text,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if grouped_block_match:
                table_number = grouped_block_match.group(1)
                tail = grouped_block_match.group(2) or ""
                block_bodies = []
                for raw_line in tail.splitlines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    line = re.sub(r"^-\s*", "", line).strip()
                    if line:
                        block_bodies.append(line)
                for body in block_bodies:
                    add_group(table_number, body)
                continue

            short = self._short_table_note_from_text(text)
            low = short.lower().replace("ё", "е")

            match_in = re.match(r"^В\s+таблице(?:\s+(\d+))?\s+(.+)$", short, flags=re.IGNORECASE)
            if match_in:
                table_number, body = match_in.group(1) or "1", match_in.group(2)
                body_low = body.lower().replace("ё", "е")
                # Группируем только содержательные подробности, а не общий текст.
                if (
                    "в графе" in body_low
                    or body_low.startswith("введены")
                    or body_low.startswith("изменена маркировка")
                    or body_low.startswith("изменилась маркировка")
                ):
                    add_group(table_number, body)
                    continue

            match_out = re.match(r"^Из\s+таблицы(?:\s+(\d+))?\s+(.+)$", short, flags=re.IGNORECASE)
            if match_out:
                table_number, body = match_out.group(1) or "1", match_out.group(2)
                if body.lower().replace("ё", "е").startswith("аннулированы"):
                    add_group(table_number, body)
                    continue

            if "таблиц" in low and not is_detailed_table_note(short):
                generic_table_notes.append(short)
                if not generic_placeholder_added:
                    result.append(generic_placeholder)
                    generic_placeholder_added = True
            else:
                result.append(short)

        table_blocks: Dict[str, str] = {}
        for table_number, bodies in grouped.items():
            unique_bodies = self._unique_preserve_order(bodies)
            if len(unique_bodies) == 1:
                body = unique_bodies[0].rstrip(".;")
                table_blocks[group_placeholders[table_number]] = f"Изменена таблица {table_number} - {body}."
            else:
                lines = [f"Изменена таблица {table_number}:"]
                for idx, body in enumerate(unique_bodies):
                    ending = "." if idx == len(unique_bodies) - 1 else ";"
                    lines.append(f"- {body.rstrip('.;')}{ending}")
                table_blocks[group_placeholders[table_number]] = "\n".join(lines)

        generic_collapsed: List[str] = []
        table_numbers: List[str] = []
        detailed_table_numbers = set(grouped.keys())
        has_generic = False
        for note in generic_table_notes:
            match = re.search(r"таблиц[аые]?\s+(\d+)", note, flags=re.IGNORECASE)
            if match:
                number = match.group(1)
                # Если по этой же таблице уже есть подробный блок, общий пункт
                # "Изменена таблица N" только засоряет примечания.
                if number not in detailed_table_numbers and number not in table_numbers:
                    table_numbers.append(number)
            else:
                has_generic = True
        if table_numbers:
            generic_collapsed = [f"Изменена таблица {number}." for number in table_numbers]
        elif has_generic and not detailed_table_numbers:
            generic_collapsed = ["Изменена таблица."]

        out: List[str] = []
        for item in result:
            if item in table_blocks:
                out.append(table_blocks[item])
            elif item == generic_placeholder:
                out.extend(generic_collapsed)
            else:
                out.append(item)

        return out

    @staticmethod
    def _short_table_note_from_text(text: str) -> str:
        text = re.sub(r"\s+", " ", str(text or "").strip())
        if not text:
            return "Изменена таблица."

        low = text.lower().replace("ё", "е")

        if (
            ("в графе" in low and "измен" in low)
            or "введены записи" in low
            or "введены провода" in low
            or "аннулированы записи" in low
            or low.startswith("для ")
            or "изменена маркировка" in low
            or low.startswith("изменилась маркировка для")
        ):
            # Если номер таблицы потерян из-за имени-заголовка, подставляем таблицу 1.
            text = re.sub(r"^В\s+таблице\s+(?=введены|в графе)", "В таблице 1 ", text, flags=re.IGNORECASE)
            text = re.sub(r"^Из\s+таблицы\s+(?=аннулированы)", "Из таблицы 1 ", text, flags=re.IGNORECASE)
            return text if text.endswith(".") else text + "."

        m = re.search(r"таблиц[аые]?\s+(\d+)", text, flags=re.IGNORECASE)

        if m:
            return f"Изменена таблица {m.group(1)}."

        return "Изменена таблица."

    def _format_tt_note_multiline(self, note: str) -> str:
        text = str(note or "").strip()
        low = text.lower()

        if low.startswith("изменены технические требования:"):
            text = "Изменены ТТ:" + text.split(":", 1)[1]
            low = text.lower()

        if not low.startswith("изменены тт:"):
            return text

        body = text.split(":", 1)[1].strip()
        if not body:
            return "Изменены ТТ."

        if "\n" in body:
            raw_parts = []
            for line in body.splitlines():
                line = line.strip()
                if not line:
                    continue
                line = line[1:].strip() if line.startswith("-") else line
                raw_parts.append(line)
        else:
            raw_parts = body.split(";")

        parts = [
            self._fix_russian_preposition(
                self._replace_old_tt_phrase(part.strip().lstrip("-").strip().rstrip(".;"))
            )
            for part in raw_parts
            if part.strip().strip("-").strip()
        ]
        if not parts:
            return "Изменены ТТ."

        lines = ["Изменены ТТ:"]
        for idx, part in enumerate(parts):
            ending = "." if idx == len(parts) - 1 else ";"
            lines.append(f"- {part}{ending}")

        return "\n".join(lines)

    @staticmethod
    def _replace_old_tt_phrase(text: str) -> str:
        text = str(text or "").strip()

        return re.sub(
            r"изменена\s+формулировка\s+в\s+п\.\s*(\d+)",
            r"изменен пункт \1",
            text,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _fix_russian_preposition(text: str) -> str:
        text = str(text or "").strip()

        replacements = {
            " о экранировании": " об экранировании",
            " о отводах": " об отводах",
            " о изменении": " об изменении",
            " о аннулировании": " об аннулировании",
            " о уточнении": " об уточнении",
            " о экранирование": " об экранировании",
            " о неуказанные": " о предельных отклонениях",
            " о бирки": " о размещении бирок",
            " о маркировать": " о маркировке",
            " о разделение": " о разделении",
            " о соединение": " о соединении",
            " о выполнение": " о выполнении",
            " о прокладка": " о прокладке",
            " о заделка": " о заделке",
            " о компенсация": " о компенсации",
            " о скрутка": " о скрутке",
            " о маркировка": " о маркировке",
            " о размещение": " о размещении",
            " о контровка": " о контровке",
            " о защитная намотка": " о защитной намотке",
            " о предельные отклонения": " о предельных отклонениях",
        }

        for old, new in replacements.items():
            text = text.replace(old, new)

        return text

    @staticmethod
    def _clean_spec_item_name(value: Optional[str]) -> str:
        """Нормализует наименования позиций СП/ПЭ4 без потери смысла."""
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        text = text.strip(" .;:\"'«»")

        text = re.sub(r"(?<=[A-Za-zА-Яа-я0-9])-\s+(?=[A-Za-zА-Яа-я0-9])", "-", text)

        text = re.sub(r"\s*/\s*", "/", text)
        text = re.sub(r"\s+\)", ")", text)
        text = re.sub(r"\(\s+", "(", text)
        text = re.sub(r"\s+,", ",", text)
        return text

    @staticmethod
    def _has_reversed_or_parser_garbage(value: Optional[str]) -> bool:
        """Отсекает явные артефакты PDF-парсинга, а не реальные позиции СП."""
        text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
        if not text:
            return False

        garbage_markers = (
            ".лбуд", ".мазв", ".лдоп", "йоколоворп", "ьтаворижаднаб",
            ").replace", "replace(»,", "мцша.101.19128.006.00 жгут",
            ".82191.101", "00.000",
        )
        if any(marker in text for marker in garbage_markers):
            return True

        # Строки вида "Нов. 611.01.047-25 4 .лбуд ..." — не позиция, а хвост штампа/служебной записи.
        if re.search(r"\bнов\.\s*\d", text) and not any(word in text for word in ("труб", "провод", "кабель", "плетен", "розетка", "соединитель", "кожух", "наконечник", "профиль")):
            return True

        return False

    def _is_bad_spec_item_name(self, name: Optional[str]) -> bool:
        text = self._clean_spec_item_name(name)
        if not text:
            return True
        if self._has_reversed_or_parser_garbage(text):
            return True
        if len(text) < 2:
            return True
        return False

    def _is_plausible_spec_quantity(self, value: Optional[str]) -> bool:
        qty = self._normalize_quantity(value)
        if not qty:
            return False
        if self._has_reversed_or_parser_garbage(qty):
            return False

        low = qty.lower().strip()
        if low in {"—", "-"}:
            return False


        if re.fullmatch(r"\d{2,}\.\d{2,}(?:\.\d+)?", low):
            return False

        return bool(re.fullmatch(r"\d+(?:[,.]\d+)?\s*(?:м|мм|шт\.?|компл\.?|к-т)?", low))

    def _is_bad_spec_added_removed(self, section: str, name: str, qty: Optional[str]) -> bool:
        if self._is_bad_spec_item_name(name):
            return True
        if self._has_reversed_or_parser_garbage(section) or self._has_reversed_or_parser_garbage(qty):
            return True

        clean_name = self._clean_spec_item_name(name)
        low_name = clean_name.lower().replace(" ", "")

        if low_name.startswith("нов.") or "зам." in low_name or "лбуд" in low_name or "доп" in low_name:
            return True


        if "мцша" in low_name and ("ттпок" in low_name or "трубкатермоусаживаемая" in low_name):
            return True


        if qty and not self._is_plausible_spec_quantity(qty):
            allowed_prefixes = ("соединитель", "розетка", "вилка", "кожух", "наконечник", "пробка")
            if not clean_name.lower().startswith(allowed_prefixes):
                return True

        return False

    def _should_skip_spec_field_change(self, section: str, name: str, field_name: str, old_value: Optional[str], new_value: Optional[str]) -> bool:
        """Фильтр для СП: оставляем содержательные изменения, режем шум от неверного сопоставления строк."""
        if self._is_bad_spec_item_name(name):
            return True
        if any(self._has_reversed_or_parser_garbage(x) for x in (section, old_value, new_value)):
            return True

        field = (field_name or "").strip().lower()

        if field == "quantity":
            return not self._is_plausible_spec_quantity(new_value)


        if field == "format" and "документац" in (section or "").lower():
            return not bool(new_value)


        if field == "note":
            return True


        if field in {"name", "designation"}:
            return True

        return True

    def _format_spec_item_display_name(self, name: str, designation: Optional[str] = None) -> str:
        clean_name = self._clean_spec_item_name(name)
        clean_designation = self._clean_spec_item_name(designation) if designation else ""

        if not clean_designation or self._has_reversed_or_parser_garbage(clean_designation):
            return clean_name


        generic_patterns = (
            "трубка термоусаживаемая ттпок-",
            "трубка термоусаживаемая",
        )
        low_name = clean_name.lower()
        if any(low_name == p or low_name.endswith("ттпок-") for p in generic_patterns):
            return f"{clean_name}{clean_designation}" if clean_name.endswith("-") else f"{clean_name} {clean_designation}"

        return clean_name

    def _clean_spec_designation_for_added_item(self, designation: Optional[str]) -> str:

        text = self._clean_spec_item_name(designation)
        if not text or self._has_reversed_or_parser_garbage(text):
            return ""

        # Берем код до "ТУ" включительно, если он есть: АСЛР.434430.002ТУ, НКЦС.434410.506ТУ и т.п.
        m = re.search(r"([A-Za-zА-Яа-яЁё0-9.\-/]+\s*ТУ)", text)
        if m:
            return self._clean_spec_item_name(m.group(1))

        # Если это нормальное обозначение без явных хвостов количества/применяемости, оставляем его.
        if not re.search(r"\b(?:россыпью|количество|шт\.?|\d+\s+0?\d{2,}[.-])\b", text.lower()):
            return text
        return ""

    def _format_spec_added_removed_display_name(self, name: str, designation: Optional[str] = None) -> str:
        clean_name = self._clean_spec_item_name(name)
        clean_designation = self._clean_spec_designation_for_added_item(designation)
        if clean_designation and clean_designation.lower() not in clean_name.lower():
            return f"{clean_name} {clean_designation}"
        return clean_name

    def _format_spec_quantity_change_item(self, name: str, new_value: Optional[str], designation: Optional[str] = None) -> Optional[str]:
        clean_name = self._format_spec_item_display_name(name, designation)
        qty = self._normalize_quantity(new_value)
        if self._is_bad_spec_item_name(clean_name) or not self._is_plausible_spec_quantity(qty):
            return None
        return f"{clean_name} на {qty}"

    def _format_bullet_note(self, header: str, items: List[str]) -> str:
        uniq = self._unique_preserve_order([self._clean_spec_item_name(item) for item in items if item])
        if not uniq:
            return self._strip_trailing_period(header) + "."

        header_text = self._strip_trailing_period(header).rstrip(":").strip()
        lines = [header_text + ":"]
        for idx, item in enumerate(uniq):
            ending = "." if idx == len(uniq) - 1 else ";"
            lines.append(f"- {self._strip_trailing_period(item)}{ending}")
        return "\n".join(lines)

    def _fallback_notes_from_facts(self, facts: ChangeFactsBundle) -> List[str]:
        if self._is_specification_type(facts.document_type):
            return self._fallback_spec_notes_from_facts(facts)

        if self._is_pe4_type(facts.document_type):
            return self._fallback_pe4_notes_from_facts(facts)

        if self._is_e4_type(facts.document_type):
            return self._fallback_e4_notes_from_facts(facts)

        metadata_notes: List[str] = []
        table_notes: List[str] = []
        graphics_notes: List[str] = []
        tt_added: List[str] = []
        tt_removed: List[str] = []
        tt_modified: List[str] = []

        for fact in facts.facts:
            if fact.fact_type == "mass_changed" and fact.new_value:
                metadata_notes.append(f'В основной надписи в графе "Масса" изменено значение на {fact.new_value} кг.')

            elif fact.fact_type == "scale_changed" and fact.new_value:
                metadata_notes.append(f'В основной надписи в графе "Масштаб" изменено значение на {fact.new_value}.')

            elif fact.fact_type == "title_changed" and fact.new_value:
                metadata_notes.append(f'В основной надписи изменено наименование на "{fact.new_value}".')

            elif fact.fact_type == "tech_requirement_added":
                num = fact.number_new
                topic = self._topic_from_requirement_fact(fact)
                about = self._about_topic(topic)

                tt_added.append(f"введен пункт {about}".strip())

            elif fact.fact_type == "tech_requirement_removed":
                num = fact.number_old
                topic = self._topic_from_requirement_fact(fact)
                about = self._about_topic(topic)

                tt_removed.append(f"аннулирован пункт {about}".strip())

            elif fact.fact_type == "tech_requirement_modified":
                num = fact.number_new or fact.number_old
                topic = self._topic_from_requirement_fact(fact)
                about = self._about_topic(topic)

                if num:
                    tt_modified.append(f"изменен пункт {num} {about}".strip())
                else:
                    tt_modified.append(f"изменен пункт {about}".strip())

            elif fact.fact_type == "tech_requirement_renumbered":
                if not (tt_added or tt_removed or tt_modified):
                    tt_modified.append("изменена нумерация пунктов")

            elif fact.fact_type in {"table_changed", "table_column_changed"}:
                table_notes.append(self._short_table_note_from_text(fact.description))

            elif fact.fact_type == "graphics_changed":
                graphics_notes.append("Изменена графика чертежа.")

        notes: List[str] = []
        notes.extend(metadata_notes)
        notes.extend(self._unique_preserve_order(graphics_notes))
        notes.extend(self._unique_preserve_order(table_notes))

        tt_parts = self._unique_preserve_order(tt_added + tt_removed + tt_modified)

        if tt_parts:
            notes.append(
                "Изменены ТТ:\n"
                + "\n".join(
                    f"- {self._strip_trailing_period(x)};" if idx < len(tt_parts) - 1 else f"- {self._strip_trailing_period(x)}."
                    for idx, x in enumerate(tt_parts)
                )
            )

        return self._sanitize_notes(notes) or ["Изменения требуют уточнения."]

    def _fallback_spec_notes_from_facts(self, facts: ChangeFactsBundle) -> List[str]:
        grouped_qty: Dict[str, List[str]] = defaultdict(list)
        grouped_added: Dict[str, List[str]] = defaultdict(list)
        grouped_removed: Dict[str, List[str]] = defaultdict(list)
        grouped_note_changed: Dict[str, List[str]] = defaultdict(list)
        grouped_other: Dict[str, List[str]] = defaultdict(list)
        other_notes: List[str] = []

        for fact in facts.facts:
            section = self._clean_spec_item_name((fact.payload or {}).get("section") or "Раздел")
            name = self._clean_spec_item_name((fact.payload or {}).get("name") or fact.description)
            field_name = self._clean_spec_item_name((fact.payload or {}).get("field_name") or "данные")
            designation = self._clean_spec_item_name((fact.payload or {}).get("designation") or "")

            # СП сильно страдает от сдвига строк PDF: сюда попадают хвосты штампа,
            # зеркальный текст и коды вместо количества. Лучше пропустить такой факт,
            # чем выпустить мусор в извещение.
            if self._is_bad_spec_item_name(name) or self._has_reversed_or_parser_garbage(section):
                continue

            if fact.fact_type == "spec_item_quantity_changed":
                item = self._format_spec_quantity_change_item(name, fact.new_value, designation)
                if item:
                    grouped_qty[section].append(item)

            elif fact.fact_type == "spec_item_added":
                qty = self._normalize_quantity(fact.new_value)
                if self._is_bad_spec_added_removed(section, name, qty):
                    continue
                display_name = self._format_spec_added_removed_display_name(name, designation)
                suffix = f", количество {qty}" if self._is_plausible_spec_quantity(qty) else ""
                grouped_added[section].append(f"{display_name}{suffix}")

            elif fact.fact_type == "spec_item_removed":
                qty = self._normalize_quantity(fact.old_value)
                if self._is_bad_spec_added_removed(section, name, qty):
                    continue
                display_name = self._format_spec_added_removed_display_name(name, designation)
                suffix = f", количество {qty}" if self._is_plausible_spec_quantity(qty) else ""
                grouped_removed[section].append(f"{display_name}{suffix}")

            elif fact.fact_type == "spec_item_note_changed":

                continue

            elif fact.fact_type == "spec_item_modified":
                field = (field_name or "").lower()
                new_value = self._normalize_quantity(fact.new_value) or self._norm(fact.new_value)
                if not new_value or self._has_reversed_or_parser_garbage(new_value):
                    continue

                if field == "format" and "документац" in section.lower():
                    grouped_other[section].append(f"{name} на {new_value}")

            elif fact.fact_type == "title_changed" and fact.new_value:
                other_notes.append(f'В основной надписи изменено наименование на "{fact.new_value}".')

        notes: List[str] = []

        for section, changes in grouped_qty.items():
            notes.append(self._format_bullet_note(f'В разделе "{section}" изменено количество:', changes))

        for section, changes in grouped_added.items():
            notes.append(self._format_bullet_note(f'В разделе "{section}" введена применяемость:', changes))

        for section, changes in grouped_removed.items():
            notes.append(self._format_bullet_note(f'В разделе "{section}" аннулирована применяемость:', changes))

        for section, changes in grouped_note_changed.items():
            notes.append(self._format_bullet_note(f'В разделе "{section}" в графе "Примечание" изменены данные:', changes))

        for section, changes in grouped_other.items():
            header = f'В разделе "{section}" изменен формат документа:' if "документац" in section.lower() else f'В разделе "{section}" изменены данные:'
            notes.append(self._format_bullet_note(header, changes))

        notes.extend(other_notes)
        return self._sanitize_notes(notes) or ["Изменения требуют уточнения."]

    def _finalize_spec_notes_from_facts(self, facts: ChangeFactsBundle, llm_notes: List[str]) -> List[str]:
        """Финальный текст СП делаем из facts, чтобы LLM не теряла позиции и не возвращала старые значения."""
        fact_types = {fact.fact_type for fact in facts.facts}
        has_spec_facts = any(ft.startswith("spec_item_") for ft in fact_types)
        if not has_spec_facts:
            return llm_notes
        return self._fallback_spec_notes_from_facts(facts)

    def _split_position_designations(self, value: Optional[str]) -> List[str]:
        text = self._norm(value)
        if not text:
            return []
        text = text.replace("–", "-").replace("—", "-").replace("−", "-")
        parts = [p.strip(" ;") for p in re.split(r"\s*,\s*", text) if p.strip(" ;")]
        return self._unique_preserve_order(parts)

    def _format_position_designations(self, value: Optional[str]) -> str:
        positions = self._split_position_designations(value)
        if not positions:
            return ""
        return ", ".join(f'"{pos}"' for pos in positions)

    def _position_wording(self, value: Optional[str]) -> str:
        positions = self._split_position_designations(value)
        return "позиционных обозначений" if len(positions) > 1 else "позиционного обозначения"


    @staticmethod
    def _unique_pairs_preserve_order(items: List[tuple[str, str]]) -> List[tuple[str, str]]:
        result: List[tuple[str, str]] = []
        seen = set()
        for first, second in items:
            key = (re.sub(r"\s+", " ", str(first or "")).strip().lower(), re.sub(r"\s+", " ", str(second or "")).strip().lower())
            if key in seen:
                continue
            seen.add(key)
            result.append((first, second))
        return result

    def _fallback_pe4_notes_from_facts(self, facts: ChangeFactsBundle) -> List[str]:
        added: List[tuple[str, str]] = []
        removed: List[tuple[str, str]] = []
        name_changed: List[str] = []
        position_changed: List[tuple[str, str]] = []
        qty_changed: List[str] = []
        note_changed = False
        explicit_pos_changed = False
        other_notes: List[str] = []

        for fact in facts.facts:
            payload = fact.payload or {}
            name = self._clean_spec_item_name(payload.get("name") or fact.description)
            position_designation = self._norm(payload.get("position_designation") or fact.description)

            if fact.fact_type == "element_item_added":
                added.append((name, position_designation))

            elif fact.fact_type == "element_item_removed":
                removed.append((name, position_designation))

            elif fact.fact_type == "element_item_name_changed":
                # Здесь меняется именно наименование элемента в строке ПЭ4,
                # а позиционное обозначение является адресом этой строки.
                if position_designation:
                    name_changed.append(position_designation)

            elif fact.fact_type == "element_item_quantity_changed":
                new_qty = self._normalize_quantity(fact.new_value) or "—"
                qty_changed.append(f"{name} на {new_qty}")

            elif fact.fact_type == "element_item_note_changed":
                note_changed = True

            elif fact.fact_type == "element_item_position_changed":
                explicit_pos_changed = True
                if name:
                    position_changed.append((name, position_designation))

            elif fact.fact_type == "title_changed" and fact.new_value:
                other_notes.append(f'В основной надписи изменено наименование на "{fact.new_value}".')

        notes: List[str] = []

        for position in self._unique_preserve_order(name_changed):
            positions_text = self._format_position_designations(position)
            if positions_text:
                notes.append(f"Изменено наименование элемента для {self._position_wording(position)} {positions_text}.")

        if explicit_pos_changed and not position_changed:
            notes.append("Изменены позиционные обозначения.")

        for name, position in self._unique_pairs_preserve_order(removed):
            positions_text = self._format_position_designations(position)
            if positions_text:
                notes.append(f'Аннулирована применяемость элемента "{name}" для {self._position_wording(position)} {positions_text}.')
            else:
                notes.append(f'Аннулирована применяемость элемента "{name}".')

        for name, _position in self._unique_pairs_preserve_order(added):
            if name:
                notes.append(f"Введена применяемость {name}.")
            else:
                notes.append("Введена применяемость.")

        for name, position in self._unique_pairs_preserve_order(position_changed):
            positions_text = self._format_position_designations(position)
            if positions_text:
                notes.append(f'Изменено позиционное обозначение элемента "{name}" на {positions_text}.')

        if qty_changed:
            notes.append(self._format_bullet_note("Изменено количество элементов:", qty_changed))

        if note_changed:
            notes.append('В графе "Примечание" изменены данные элементов.')

        notes.extend(other_notes)
        return self._sanitize_notes(notes) or ["Изменения требуют уточнения."]

    def _finalize_pe4_notes_from_facts(self, facts: ChangeFactsBundle, llm_notes: List[str]) -> List[str]:

        fact_types = {fact.fact_type for fact in facts.facts}
        has_pe4_facts = any(ft.startswith("element_item_") for ft in fact_types)
        if not has_pe4_facts:
            return llm_notes
        return self._fallback_pe4_notes_from_facts(facts)

    def _fallback_e4_notes_from_facts(self, facts: ChangeFactsBundle) -> List[str]:
        graphics_notes: List[str] = []
        table_notes: List[str] = []
        tt_changes: List[str] = []
        other: List[str] = []

        for fact in facts.facts:
            if fact.fact_type == "graphics_changed" or fact.fact_type.startswith("e4_graphic_"):
                graphics_notes.append("Изменена графика схемы.")

            elif (
                fact.fact_type in {"table_changed", "table_column_changed"}
                or fact.fact_type.startswith("e4_table_")
            ):
                table_notes.append(self._short_table_note_from_text(fact.description))

            elif fact.fact_type == "tech_requirement_added":
                num = fact.number_new or fact.number_old
                topic = self._topic_from_requirement_fact(fact)
                about = self._about_topic(topic)
                tt_changes.append(f"введен пункт {about}")

            elif fact.fact_type == "tech_requirement_removed":
                num = fact.number_old or fact.number_new
                topic = self._topic_from_requirement_fact(fact)
                about = self._about_topic(topic)
                tt_changes.append(f"аннулирован пункт {about}")

            elif fact.fact_type == "tech_requirement_modified":
                num = fact.number_new or fact.number_old
                topic = self._topic_from_requirement_fact(fact)
                about = self._about_topic(topic)
                tt_changes.append(f"изменен пункт {num} {about}".strip() if num else f"изменен пункт {about}".strip())

            elif fact.fact_type == "tech_requirement_renumbered":
                if not tt_changes:
                    tt_changes.append("изменена нумерация пунктов")

            elif fact.fact_type == "title_changed" and fact.new_value:
                other.append(f'В основной надписи изменено наименование на "{fact.new_value}".')

            elif fact.fact_type == "scale_changed" and fact.new_value:
                other.append(f'В основной надписи в графе "Масштаб" изменено значение на {fact.new_value}.')

        notes: List[str] = []
        notes.extend(self._unique_preserve_order(graphics_notes))
        notes.extend(self._collapse_table_notes(table_notes))

        uniq_tt = self._unique_preserve_order(tt_changes)
        if uniq_tt:
            notes.append(
                "Изменены ТТ:\n"
                + "\n".join(
                    f"- {self._strip_trailing_period(x)};" if idx < len(uniq_tt) - 1 else f"- {self._strip_trailing_period(x)}."
                    for idx, x in enumerate(uniq_tt)
                )
            )

        notes.extend(other)
        return self._sanitize_notes(notes) or ["Изменения требуют уточнения."]

    
    def _postprocess_grouped_notes(self, notes: List[str]) -> List[str]:
        groups: Dict[str, List[str]] = defaultdict(list)
        passthrough: List[str] = []
        pattern = re.compile(r'^В разделе "([^"]+)"\s+(.+)$')

        for note in notes:
            m = pattern.match(note.strip())

            if not m:
                passthrough.append(note)
                continue

            section, rest = m.group(1), m.group(2).rstrip(".")
            groups[section].append(rest)

        merged: List[str] = []

        for section, parts in groups.items():
            uniq = self._unique_preserve_order(parts)

            if len(uniq) == 1:
                merged.append(f'В разделе "{section}" {uniq[0]}.')
            else:
                merged.append(f'В разделе "{section}" ' + "; ".join(uniq) + ".")

        merged.extend(passthrough)

        return self._sanitize_notes(merged)

    def _postprocess_pe4_notes(self, notes: List[str]) -> List[str]:
        merged: List[str] = []
        buckets: Dict[str, List[str]] = defaultdict(list)
        patterns = [
            r"^(Наименования элементов[^:]*):\s*(.+)$",
            r"^(Введены)\s+(.+)$",
            r"^(Аннулированы)\s+(.+)$",
            r"^(Наименования и количество элементов приведены в соответствие со спецификацией):\s*(.+)$",
        ]

        for note in notes:
            matched = False
            stripped = note.strip().rstrip(".")

            for pat in patterns:
                m = re.match(pat, stripped)

                if m:
                    buckets[m.group(1)].append(m.group(2))
                    matched = True
                    break

            if not matched:
                merged.append(note)

        for lead, tails in buckets.items():
            uniq = self._unique_preserve_order(tails)

            if lead in {"Введены", "Аннулированы"}:
                merged.append(f"{lead} " + ", ".join(uniq) + ".")
            else:
                merged.append(f"{lead}: " + "; ".join(uniq) + ".")

        return self._sanitize_notes(merged)

    def _postprocess_e4_notes(self, notes: List[str]) -> List[str]:
        graphics: List[str] = []
        tables: List[str] = []
        tech: List[str] = []
        other: List[str] = []

        for note in notes:
            stripped = note.strip().rstrip(".")
            low = stripped.lower()

            if low == "схема изменена с учетом исходных данных":
                continue

            if low.startswith("изменена графика схемы") or "проводник" in low or "подключен" in low or "подключение" in low:
                graphics.append(stripped)

            elif "таблиц" in low or low.startswith("в таблице") or low.startswith("из таблицы"):
                tables.append(stripped)

            elif (
                "тт" in low
                or low.startswith("аннулирован п.")
                or low.startswith("изменена формулировка")
                or low.startswith("изменен пункт")
                or low.startswith("введено требование")
                or low.startswith("введен пункт")
            ):
                tech.append(stripped)

            else:
                other.append(stripped)

        merged: List[str] = []

        if graphics:
            normalized = []

            for g in graphics:
                g = re.sub(r"^Изменена графика схемы:\s*", "", g, flags=re.I)
                normalized.append(g)

            merged.append("Изменена графика схемы: " + "; ".join(self._unique_preserve_order(normalized)) + ".")

        if tables:
            for t in tables:
                merged.append(self._short_table_note_from_text(t))

        if tech:
            normalized = []

            for t in tech:
                t = re.sub(r"^Изменены ТТ:\s*", "", t, flags=re.I)
                normalized.append(t)

            merged.append("Изменены ТТ: " + "; ".join(self._unique_preserve_order(normalized)) + ".")

        merged.extend(other)

        return self._sanitize_notes(merged)

    def _topic_from_requirement_fact(self, fact: ChangeFact) -> str:
        candidates = [
            getattr(fact, "description", None),
            getattr(fact, "new_value", None),
            getattr(fact, "old_value", None),
        ]

        for candidate in candidates:
            topic = self._summarize_requirement(candidate)
            if self._is_good_requirement_topic(topic):
                return self._lower_first(self._strip_trailing_period(topic))

        return "содержании пункта"

    @staticmethod
    def _is_good_requirement_topic(topic: Optional[str]) -> bool:
        if not topic:
            return False

        low = re.sub(r"\s+", " ", str(topic)).strip().lower()
        if not low:
            return False

        if "..." in low or "…" in low:
            return False

        if len(low) > 90:
            return False

        bad_starts = (
            "неуказанные ",
            "экранирование ",
            "бирки поз.",
            "маркировать ",
            "разделение электрических цепей выполнить",
            "жгут ",
        )
        if low.startswith(bad_starts):
            return False

        bad_fragments = (
            " выполнить ",
            " установить в соответствии",
            " расстоянии от ",
            "ост 1 ",
            "гост ",
            "поз. ",
        )
        if any(fragment in low for fragment in bad_fragments):
            return False

        return True

    def _about_topic(self, topic: str) -> str:
        topic = re.sub(r"\s+", " ", (topic or "").strip().rstrip("."))

        if not topic:
            return ""

        topic = self._topic_to_prepositional_case(topic)
        topic = self._strip_about_preposition(topic)

        first = topic[0].lower()
        prep = "об" if first in "аеёиоуыэюяэ" else "о"

        return self._fix_russian_preposition(f"{prep} {topic}")

    @staticmethod
    def _topic_to_prepositional_case(topic: str) -> str:

        text = re.sub(r"\s+", " ", str(topic or "").strip().rstrip(".;:"))
        low = text.lower().replace("ё", "е")

        prefix_map = [
            ("неуказанные предельные отклонения", "предельных отклонениях размеров"),
            ("предельные отклонения", "предельных отклонениях"),
            ("разделение ", "разделении "),
            ("соединение ", "соединении "),
            ("выполнение ", "выполнении "),
            ("экранирование ", "экранировании "),
            ("размещение ", "размещении "),
            ("прокладка ", "прокладке "),
            ("заделка ", "заделке "),
            ("компенсация ", "компенсации "),
            ("скрутка ", "скрутке "),
            ("маркировка ", "маркировке "),
            ("контровка ", "контровке "),
            ("замена ", "замене "),
            ("защитная намотка ", "защитной намотке "),
        ]
        for src, dst in prefix_map:
            if low.startswith(src):
                return dst + text[len(src):]

        exact_map = {
            "разделение": "разделении",
            "соединение": "соединении",
            "выполнение": "выполнении",
            "экранирование": "экранировании",
            "размещение": "размещении",
            "прокладка": "прокладке",
            "заделка": "заделке",
            "компенсация": "компенсации",
            "скрутка": "скрутке",
            "маркировка": "маркировке",
            "контровка": "контровке",
            "замена": "замене",
            "защитная намотка": "защитной намотке",
            "бирки": "размещении бирок",
        }
        if low in exact_map:
            return exact_map[low]

        if low.startswith("маркировать "):
            return "маркировке " + text[len("маркировать "):]
        if low.startswith("скручивать "):
            return "скрутке " + text[len("скручивать "):]
        if low.startswith("бирки "):
            return "размещении бирок"

        return text

    def _fix_known_prepositional_phrase(self, phrase: str) -> str:
        text = re.sub(r"\s+", " ", str(phrase or "").strip().rstrip(".;:"))
        if not text:
            return ""

        prep_match = re.match(r"^(о|об)\s+(.+)$", text, flags=re.IGNORECASE)
        if not prep_match:
            return self._about_topic(text)

        body = prep_match.group(2).strip()
        return self._about_topic(body)

    @staticmethod
    def _normalize_modified_tt_phrasing(text: str) -> str:
        if not text:
            return text

        text = re.sub(
            r"изменена\s+формулировка\s+в\s+п\.\s*(\d+)\s+(о|об)\s+",
            r"изменен пункт \1 \2 ",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"изменена\s+формулировка\s+п\.\s*(\d+)\s+(о|об)\s+",
            r"изменен пункт \1 \2 ",
            text,
            flags=re.IGNORECASE,
        )

        return text

    @staticmethod
    def _strip_trailing_period(text: str) -> str:
        return (text or "").strip().rstrip(".").strip()

    @staticmethod
    def _unique_preserve_order(items: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()

        for item in items:
            key = re.sub(r"\s+", " ", item).strip().lower()

            if not key or key in seen:
                continue

            seen.add(key)
            out.append(re.sub(r"\s+", " ", item).strip())

        return out

    def _format_multiline_notes(self, notes: List[str]) -> List[str]:
        return self._sanitize_notes([self._maybe_multilineize_note(note) for note in notes])

    @staticmethod
    def _maybe_multilineize_note(note: str) -> str:
        if not note:
            return note

        raw = str(note).strip()
        prefixes = [
            "Изменены ТТ:",
            "Изменены технические требования:",
        ]

        for prefix in prefixes:
            if raw.startswith(prefix):
                tail = raw[len(prefix):].strip()

                if not tail:
                    return prefix

                if "\n" in tail and re.search(r"(^|\n)\s*-\s+", tail):
                    return prefix + "\n" + "\n".join(line.rstrip() for line in tail.splitlines() if line.strip())

                items = [part.strip().rstrip(".") for part in tail.split(";") if part.strip()]

                if len(items) <= 1:
                    return raw

                return prefix + "\n" + "\n".join(
                    f"- {item};" if idx < len(items) - 1 else f"- {item}."
                    for idx, item in enumerate(items)
                )

        return raw

    @staticmethod
    def _sanitize_notes(notes: List[str]) -> List[str]:
        result: List[str] = []
        seen = set()

        for note in notes:
            if not note:
                continue

            lines = []

            for line in str(note).splitlines():
                stripped = line.strip()

                if not stripped:
                    continue

                if stripped.startswith("- "):
                    cleaned_line = "- " + re.sub(r"\s+", " ", stripped[2:]).strip()
                else:
                    cleaned_line = re.sub(r"\s+", " ", stripped).strip()

                lines.append(cleaned_line)

            clean = "\n".join(lines).strip()
            clean = GigaChatNoticeService._normalize_modified_tt_phrasing(clean)
            dedupe_key = re.sub(r"\s+", " ", clean.replace("\n", " ")).strip().lower()

            if clean and dedupe_key not in seen:
                seen.add(dedupe_key)
                result.append(clean)

        return result

    def _verify_value(self):
        if self.verify_ssl_certs:
            return self.ca_bundle_file or True
        return False

    @staticmethod
    def _choose_effective_metadata(primary: DocMetadata, fallback: DocMetadata) -> DocMetadata:
        return DocMetadata(
            decimal_number=primary.decimal_number or fallback.decimal_number,
            doc_type=primary.doc_type or fallback.doc_type,
            title=primary.title or fallback.title,
            mass_kg=primary.mass_kg if primary.mass_kg is not None else fallback.mass_kg,
            scale=primary.scale or fallback.scale,
            litera=primary.litera or fallback.litera,
            raw_stamp_snippet=primary.raw_stamp_snippet or fallback.raw_stamp_snippet,
        )

    @staticmethod
    def _norm(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        return re.sub(r"\s+", " ", str(value)).strip() or None

    @staticmethod
    def _float_to_str(value: Optional[float]) -> Optional[str]:
        if value is None:
            return None

        text = f"{value:g}"
        return text.replace(".", ",")

    def _summarize_requirement(self, text: Optional[str]) -> str:
        if not text:
            return "требовании"

        clean = re.sub(r"\s+", " ", text).strip().rstrip(".")
        low = clean.lower()

        topic_rules = [
            ("неуказанные предельные отклонения размеров", "предельных отклонениях размеров"),
            ("экранирование цепей", "экранировании цепей"),
            ("бирки поз. 1, 2 установить", "размещении бирок"),
            ("установить в соответствии с ост 1 00031", "размещении бирок"),
            ("на расстоянии от 50 до 70 мм", "размещении бирок"),
            ("жгут обмотать тканью", "защитной намотке жгута и отводов Экран, GND"),
            ("защитную намотку", "защитной намотке жгута и отводов Экран, GND"),
            ("заделку выполнить на борту", "заделке на борту"),
            ("со стороны соединителя, заделываемого на борту", "размещении бирок со стороны соединителя, заделываемого на борту"),
            ("маркировку по пп. 7 - 9", "маркировке по пп. 7-9"),
            ("маркировку по пп. 7-9", "маркировке по пп. 7-9"),
            ("заделку проводов", "заделке проводов в соединители"),
            ("разделение электр", "разделении электрических цепей"),
            ("цепь 004", "выполнении цепи 004"),
            ("экраны проводов", "соединении экранов"),
            ("экранирование кабеля", "экранировании кабеля"),
            ("жгут по всей длине", "прокладке жгута в плетенках"),
            ("отводов экран", "заделке отводов Экран и GND"),
            ("провода со стороны отводов", "заделке отводов Экран и GND"),
            ("зазор между стволом", "компенсации зазора между стволом жгута и кожухом соединителей"),
            ("скручивать провода", "скрутке проводов"),
            ("маркировать каждый проводник", "маркировке проводников"),
            ("маркировать наименование", "маркировке наименования и обозначения жгута"),
            ("маркировать позиционное обозначение соединителей в соответствии с таблицей", "маркировке позиционного обозначения соединителей"),
            ("маркировать позиционное обозначение соединителя", "маркировке позиционного обозначения соединителей"),
            ("s, не сгибать", "маркировке позиционного обозначения соединителей"),
            ("маркировку по пп", "маркировке по пп."),
            ("бирки термоусадить", "размещении бирок"),
            ("резьбовые соединения", "контровке резьбовых соединений"),
            ("монтаж проводников выполнить кабелем", "замене сечения кабеля"),
            ("cat5e", "замене сечения кабеля"),
        ]

        for marker, topic in topic_rules:
            if marker in low:
                return topic

        if len(clean) <= 90 and not any(token in low for token in (" выполнить ", " установить ", " расстоянии от ", "поз. ", "гост ", "ост ")):
            return clean

        return "содержании пункта"

    @staticmethod
    def _lower_first(text: str) -> str:
        if not text:
            return text

        return text[0].lower() + text[1:]

    @staticmethod
    def _is_specification_type(doc_type: str) -> bool:
        return "специфик" in (doc_type or "").lower()

    @staticmethod
    def _is_pe4_type(doc_type: str) -> bool:
        return "перечень элементов" in (doc_type or "").lower() or (doc_type or "").strip().upper().endswith("ПЭ4")

    @staticmethod
    def _is_e4_type(doc_type: str) -> bool:
        low = (doc_type or "").lower().strip()
        up = (doc_type or "").strip().upper()
        return "схема электрическая соединений" in low or up.endswith("Э4")

    @staticmethod
    def _normalize_quantity(value: Optional[str]) -> Optional[str]:
        if not value:
            return value

        v = re.sub(r"\s+", " ", value).strip()
        v = re.sub(r"(?<=\d)([а-яА-Яa-zA-Z]+)$", r" \1", v)

        return v
