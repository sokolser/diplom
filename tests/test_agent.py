from __future__ import annotations

from agent import ChangeNoticeAgent
from models import DocMetadata, FullDiff, GraphicDiff, TableDiff, TechReqDiff


def make_diff() -> FullDiff:
    return FullDiff(
        file_v1="old.pdf",
        file_v2="new.pdf",
        metadata_v1=DocMetadata(
            decimal_number="РСПГ.122.04.11.01.000 СБ",
            doc_type="Сборочный чертеж",
            title="Кабель",
            mass_kg=0.04,
            scale="1:2",
            sheet_count=1,
        ),
        metadata_v2=DocMetadata(
            decimal_number="РСПГ.122.04.11.01.000 СБ",
            doc_type="Сборочный чертеж",
            title="Кабель модернизированный",
            mass_kg=0.05,
            scale="1:1",
            change_number="2",
            sheet_count=1,
        ),
        tech_requirements=[
            TechReqDiff(
                number=1,
                status="modified",
                v1_number=1,
                v2_number=1,
                v1_text="Провод проложить без скручивания.",
                v2_text="Провод проложить без скручивания и перегибов.",
            )
        ],
        tables=[
            TableDiff(
                page_num=1,
                table_name="Таблица 1 - Таблица присоединений",
                row_index=3,
                col_index=2,
                col_name="Длина, мм",
                row_key="004",
                status="modified",
                v1_val="100",
                v2_val="120",
                table_data_rows=10,
                column_names=["Провод", "Длина, мм"],
            )
        ],
        graphics=GraphicDiff(has_changes=False),
    )


def test_agent_builds_fallback_notice_without_gigachat_credentials(monkeypatch):
    monkeypatch.delenv("GIGACHAT_CLIENT_ID", raising=False)
    monkeypatch.delenv("GIGACHAT_CLIENT_SECRET", raising=False)

    notice = ChangeNoticeAgent().build_fallback_notice(make_diff())

    assert notice.block
    block = notice.block[0]
    assert block.decimal_number == "РСПГ.122.04.11.01.000 СБ"
    assert block.action == "Заменить"
    assert block.change_number == "2"
    assert any("Масса" in note for note in block.notes)
    assert "Примечания:" in notice.formatted_text


def test_agent_generate_debug_falls_back_when_credentials_missing(monkeypatch):
    monkeypatch.delenv("GIGACHAT_CLIENT_ID", raising=False)
    monkeypatch.delenv("GIGACHAT_CLIENT_SECRET", raising=False)

    response = ChangeNoticeAgent().generate_debug(make_diff())

    assert response.result.block[0].decimal_number.endswith("СБ")
    assert response.llm_error is not None
