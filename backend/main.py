import os
import shutil
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agent import ChangeNoticeAgent
from change_notice_pdf import build_change_notice_pdf, make_change_notice_pdf_filename
from masking import mask_mapping
from models import ChangeNoticeResponse, FullDiff, GenerateNoticeDebugResponse, ParsedDocument
from parser import EngineeringDocParser


BASE_DIR = Path(__file__).resolve().parent
DOTENV_PATH = os.getenv("APP_DOTENV_PATH", str(BASE_DIR / ".env"))
load_dotenv(DOTENV_PATH, override=False)

app = FastAPI(
    title="КД Анализатор API",
    description="Парсинг PDF КД, сравнение версий и генерация извещения об изменении через GigaChat",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://192.168.0.10:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

parser = EngineeringDocParser(dpi=220)


def _get_agent() -> ChangeNoticeAgent:
    return ChangeNoticeAgent(dotenv_path=DOTENV_PATH)


def _ensure_pdf_uploads(*uploads: UploadFile) -> None:
    for upload in uploads:
        filename = (upload.filename or "").lower()
        if not filename.endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Поддерживаются только PDF")


def _save_upload(upload: UploadFile, path: Path) -> None:
    with path.open("wb") as file:
        shutil.copyfileobj(upload.file, file)


def _compare_uploaded_documents(v1: UploadFile, v2: UploadFile, tmpdir: str) -> FullDiff:
    v1_path = Path(tmpdir) / "v1.pdf"
    v2_path = Path(tmpdir) / "v2.pdf"
    _save_upload(v1, v1_path)
    _save_upload(v2, v2_path)
    return parser.compare(str(v1_path), str(v2_path))


def _build_fallback_notice(diff: FullDiff) -> ChangeNoticeResponse:
    return _get_agent().build_fallback_notice(diff)


async def _generate_notice_debug(diff: FullDiff) -> GenerateNoticeDebugResponse:
    return await run_in_threadpool(_get_agent().generate_debug, diff)


@app.post("/api/v1/parse-doc", response_model=ParsedDocument)
async def parse_document(pdf: UploadFile = File(...)):
    _ensure_pdf_uploads(pdf)
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "input.pdf"
        try:
            _save_upload(pdf, pdf_path)
            return parser.parse_document(str(pdf_path))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Ошибка парсинга: {str(exc)}")


@app.post("/api/v1/compare-docs-full", response_model=FullDiff)
async def compare_documents_full(v1: UploadFile = File(...), v2: UploadFile = File(...)):
    _ensure_pdf_uploads(v1, v2)
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            return _compare_uploaded_documents(v1, v2, tmpdir)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Ошибка анализа: {str(exc)}")


@app.post("/api/v1/generate-change-notice-json", response_model=ChangeNoticeResponse)
async def generate_change_notice_json(v1: UploadFile = File(...), v2: UploadFile = File(...)):
    _ensure_pdf_uploads(v1, v2)
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            diff = _compare_uploaded_documents(v1, v2, tmpdir)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Ошибка анализа: {str(exc)}")

        try:
            debug_response = await _generate_notice_debug(diff)
            return debug_response.result
        except Exception:
            return _build_fallback_notice(diff)


@app.post("/api/v1/generate-change-notice-json-debug", response_model=GenerateNoticeDebugResponse)
async def generate_change_notice_json_debug(v1: UploadFile = File(...), v2: UploadFile = File(...)):
    _ensure_pdf_uploads(v1, v2)
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            diff = _compare_uploaded_documents(v1, v2, tmpdir)
            return await _generate_notice_debug(diff)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Ошибка анализа или генерации: {str(exc)}")


def _pdf_response(pdf_bytes: bytes, filename: str) -> StreamingResponse:
    safe_filename = quote(filename)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}"}
    return StreamingResponse(BytesIO(pdf_bytes), media_type="application/pdf", headers=headers)


@app.post("/api/v1/generate-change-notice-pdf")
async def generate_change_notice_pdf(notice: Dict[str, Any]):
    try:
        pdf_bytes = await run_in_threadpool(build_change_notice_pdf, notice)
        filename = make_change_notice_pdf_filename(notice)
        return _pdf_response(pdf_bytes, filename)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка формирования PDF: {str(exc)}")


@app.post("/api/v1/generate-change-notice-pdf-from-docs")
async def generate_change_notice_pdf_from_docs(v1: UploadFile = File(...), v2: UploadFile = File(...)):
    _ensure_pdf_uploads(v1, v2)
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            diff = _compare_uploaded_documents(v1, v2, tmpdir)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Ошибка анализа: {str(exc)}")

        try:
            debug_response = await _generate_notice_debug(diff)
            notice = debug_response.result
        except Exception:
            notice = _build_fallback_notice(diff)

        try:
            pdf_bytes = await run_in_threadpool(build_change_notice_pdf, notice)
            filename = make_change_notice_pdf_filename(notice)
            return _pdf_response(pdf_bytes, filename)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Ошибка формирования PDF: {str(exc)}")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "dotenv_path": DOTENV_PATH,
        "gigachat_client_id_configured": bool(os.getenv("GIGACHAT_CLIENT_ID")),
        "gigachat_client_secret_configured": bool(os.getenv("GIGACHAT_CLIENT_SECRET")),
        "gigachat_model": os.getenv("GIGACHAT_MODEL", "GigaChat"),
        "safe_config_preview": mask_mapping({
            "GIGACHAT_CLIENT_ID": os.getenv("GIGACHAT_CLIENT_ID", ""),
            "GIGACHAT_CLIENT_SECRET": os.getenv("GIGACHAT_CLIENT_SECRET", ""),
            "GIGACHAT_MODEL": os.getenv("GIGACHAT_MODEL", "GigaChat"),
        }),
    }
