from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocMetadata(BaseModel):
    decimal_number: Optional[str] = None
    doc_type: Optional[str] = None
    title: Optional[str] = None
    mass_kg: Optional[float] = None
    scale: Optional[str] = None
    litera: Optional[str] = None
    change_number: Optional[str] = None
    sheet_count: Optional[int] = None
    sheet_statuses: Dict[int, str] = Field(default_factory=dict)
    raw_stamp_snippet: str = ""


class TechRequirement(BaseModel):
    number: int
    text: str


class ParsedTable(BaseModel):
    page_num: int
    name: str
    bbox: Tuple[float, float, float, float]
    rows: List[List[str]] = Field(default_factory=list)
    column_names: List[str] = Field(default_factory=list)
    header_rows_count: int = 1


class SpecificationItem(BaseModel):
    page_num: int
    section: Optional[str] = None
    format: Optional[str] = None
    zone: Optional[str] = None
    position: Optional[str] = None
    designation: Optional[str] = None
    name: Optional[str] = None
    quantity: Optional[str] = None
    note: Optional[str] = None
    key: str


class ElementItem(BaseModel):
    page_num: int
    position_designation: str
    name: Optional[str] = None
    quantity: Optional[str] = None
    note: Optional[str] = None
    key: str


class ParsedDocument(BaseModel):
    file_name: str
    metadata: DocMetadata
    tech_requirements: List[TechRequirement] = Field(default_factory=list)
    tables: List[ParsedTable] = Field(default_factory=list)
    specification_items: List[SpecificationItem] = Field(default_factory=list)
    element_items: List[ElementItem] = Field(default_factory=list)


class TechReqDiff(BaseModel):
    number: int
    status: Literal["added", "removed", "modified", "unchanged"]
    v1_number: Optional[int] = None
    v2_number: Optional[int] = None
    v1_text: Optional[str] = None
    v2_text: Optional[str] = None


class TableDiff(BaseModel):
    page_num: int
    table_name: str
    row_index: int
    col_index: int
    col_name: Optional[str] = None
    row_key: Optional[str] = None
    status: str
    v1_val: Optional[str] = None
    v2_val: Optional[str] = None
    key_col_index: Optional[int] = None
    table_data_rows: Optional[int] = None
    column_names: List[str] = Field(default_factory=list)
    row_values_v1: List[str] = Field(default_factory=list)
    row_values_v2: List[str] = Field(default_factory=list)
    prev_row_values_v1: List[str] = Field(default_factory=list)
    prev_row_values_v2: List[str] = Field(default_factory=list)
    next_row_values_v1: List[str] = Field(default_factory=list)
    next_row_values_v2: List[str] = Field(default_factory=list)


class SpecificationFieldChange(BaseModel):
    field_name: str
    v1_val: Optional[str] = None
    v2_val: Optional[str] = None


class SpecificationItemDiff(BaseModel):
    key: str
    section: Optional[str] = None
    position: Optional[str] = None
    designation: Optional[str] = None
    name: Optional[str] = None
    status: Literal["added", "removed", "modified", "unchanged"]
    field_changes: List[SpecificationFieldChange] = Field(default_factory=list)
    v1_item: Optional[SpecificationItem] = None
    v2_item: Optional[SpecificationItem] = None


class ElementFieldChange(BaseModel):
    field_name: str
    v1_val: Optional[str] = None
    v2_val: Optional[str] = None


class ElementItemDiff(BaseModel):
    key: str
    position_designation: str
    name: Optional[str] = None
    status: Literal["added", "removed", "modified", "unchanged"]
    field_changes: List[ElementFieldChange] = Field(default_factory=list)
    v1_item: Optional[ElementItem] = None
    v2_item: Optional[ElementItem] = None


class GraphicRegion(BaseModel):
    page_num: int
    x: int
    y: int
    w: int
    h: int
    change_type: Optional[str] = None
    description: Optional[str] = None
    key: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None


class GraphicDiff(BaseModel):
    has_changes: bool = False
    changed_regions: List[GraphicRegion] = Field(default_factory=list)
    change_percentage: float = 0.0


class FullDiff(BaseModel):
    file_v1: str
    file_v2: str
    metadata_v1: DocMetadata
    metadata_v2: DocMetadata
    tech_requirements: List[TechReqDiff] = Field(default_factory=list)
    specification_items: List[SpecificationItemDiff] = Field(default_factory=list)
    element_items: List[ElementItemDiff] = Field(default_factory=list)
    tables: List[TableDiff] = Field(default_factory=list)
    graphics: GraphicDiff = Field(default_factory=GraphicDiff)

    @model_validator(mode="after")
    def filter_unchanged(self):
        self.tech_requirements = [t for t in self.tech_requirements if t.status != "unchanged"]
        self.specification_items = [t for t in self.specification_items if t.status != "unchanged"]
        self.element_items = [t for t in self.element_items if t.status != "unchanged"]
        self.tables = [t for t in self.tables if t.status != "unchanged"]
        return self


class ChangeFact(BaseModel):
    fact_type: str
    description: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    number_old: Optional[int] = None
    number_new: Optional[int] = None
    source: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class ChangeFactsBundle(BaseModel):
    document_type: str
    decimal_number: str
    title: Optional[str] = None
    facts: List[ChangeFact] = Field(default_factory=list)


class NotesOnlyPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    notes: List[str] = Field(default_factory=list)


class NoticeBlock(BaseModel):
    doc_type: str
    decimal_number: str
    action: str
    notes: List[str] = Field(default_factory=list)
    journal_entry: str = "Журнал № XX, запись № XX."
    change_number: Optional[str] = None


class ChangeNoticeResponse(BaseModel):
    notice_id: str
    block: List[NoticeBlock] = Field(default_factory=list)
    formatted_text: str
    message: str = "Анализ завершён. Блок извещения сформирован."


class GenerateNoticeDebugResponse(BaseModel):
    result: ChangeNoticeResponse
    facts: ChangeFactsBundle
    llm_raw_text: Optional[str] = None
    llm_raw_json: Optional[str] = None
    llm_error: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
