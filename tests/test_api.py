from __future__ import annotations

from fastapi.testclient import TestClient

import main
from models import DocMetadata, FullDiff, GraphicDiff, TechReqDiff


def make_diff() -> FullDiff:
    return FullDiff(
        file_v1="v1.pdf",
        file_v2="v2.pdf",
        metadata_v1=DocMetadata(
            decimal_number="МЦША.101.19128.006.00 СБ",
            doc_type="Сборочный чертеж",
            title="Жгут 028.00-6",
            sheet_count=1,
        ),
        metadata_v2=DocMetadata(
            decimal_number="МЦША.101.19128.006.00 СБ",
            doc_type="Сборочный чертеж",
            title="Жгут 028.00-6",
            change_number="1",
            sheet_count=1,
        ),
        tech_requirements=[
            TechReqDiff(
                number=2,
                status="added",
                v2_number=2,
                v2_text="Маркировать провода по таблице соединений.",
            )
        ],
        graphics=GraphicDiff(has_changes=False),
    )


def test_health_masks_config(monkeypatch):
    monkeypatch.setenv("GIGACHAT_CLIENT_ID", "client-id-123456")
    monkeypatch.setenv("GIGACHAT_CLIENT_SECRET", "secret-abcdef")
    client = TestClient(main.app)

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["gigachat_client_id_configured"] is True
    assert payload["safe_config_preview"]["GIGACHAT_CLIENT_SECRET"].endswith("cdef")
    assert "secret-ab" not in payload["safe_config_preview"]["GIGACHAT_CLIENT_SECRET"]


def test_generate_notice_json_uses_fallback_without_real_pdf_parsing(monkeypatch):
    monkeypatch.delenv("GIGACHAT_CLIENT_ID", raising=False)
    monkeypatch.delenv("GIGACHAT_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(main.parser, "compare", lambda *_args, **_kwargs: make_diff())
    client = TestClient(main.app)

    response = client.post(
        "/api/v1/generate-change-notice-json",
        files={
            "v1": ("old.pdf", b"%PDF-1.4 old", "application/pdf"),
            "v2": ("new.pdf", b"%PDF-1.4 new", "application/pdf"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["block"][0]["decimal_number"] == "МЦША.101.19128.006.00 СБ"
    assert payload["block"][0]["change_number"] == "1"
    assert "Изменены ТТ" in payload["formatted_text"]


def test_non_pdf_upload_is_rejected():
    client = TestClient(main.app)

    response = client.post(
        "/api/v1/generate-change-notice-json",
        files={
            "v1": ("old.txt", b"not pdf", "text/plain"),
            "v2": ("new.pdf", b"%PDF-1.4 new", "application/pdf"),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Поддерживаются только PDF"


def test_compare_docs_full_returns_diff(monkeypatch):
    monkeypatch.setattr(main.parser, "compare", lambda *_args, **_kwargs: make_diff())
    client = TestClient(main.app)

    response = client.post(
        "/api/v1/compare-docs-full",
        files={
            "v1": ("old.pdf", b"%PDF-1.4 old", "application/pdf"),
            "v2": ("new.pdf", b"%PDF-1.4 new", "application/pdf"),
        },
    )

    assert response.status_code == 200
    assert response.json()["metadata_v2"]["decimal_number"] == "МЦША.101.19128.006.00 СБ"


def test_parse_doc_returns_parsed_document(monkeypatch):
    from models import ParsedDocument

    parsed = ParsedDocument(
        file_name="input.pdf",
        metadata=DocMetadata(decimal_number="РСПГ.000 СБ", doc_type="Сборочный чертеж"),
    )
    monkeypatch.setattr(main.parser, "parse_document", lambda *_args, **_kwargs: parsed)
    client = TestClient(main.app)

    response = client.post(
        "/api/v1/parse-doc",
        files={"pdf": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["metadata"]["decimal_number"] == "РСПГ.000 СБ"


def test_generate_change_notice_pdf_returns_attachment(monkeypatch):
    monkeypatch.setattr(main, "build_change_notice_pdf", lambda payload: b"%PDF-1.4 generated")
    monkeypatch.setattr(main, "make_change_notice_pdf_filename", lambda payload: "ИИ-test.pdf")
    client = TestClient(main.app)

    response = client.post(
        "/api/v1/generate-change-notice-pdf",
        json={"notice_id": "ИИ-001", "block": []},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert response.content == b"%PDF-1.4 generated"
