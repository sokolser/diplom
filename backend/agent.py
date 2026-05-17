

from __future__ import annotations

from typing import Optional

from gigachat_service import GigaChatNoticeService
from models import ChangeNoticeResponse, FullDiff, GenerateNoticeDebugResponse


class ChangeNoticeAgent:
    def __init__(self, dotenv_path: Optional[str] = None):
        self.dotenv_path = dotenv_path

    def _service(self, require_credentials: bool = True) -> GigaChatNoticeService:
        return GigaChatNoticeService(
            dotenv_path=self.dotenv_path,
            require_credentials=require_credentials,
        )

    def build_fallback_notice(self, diff: FullDiff) -> ChangeNoticeResponse:
        service = self._service(require_credentials=False)
        facts = service.build_change_facts(diff)
        notes = service._fallback_notes_from_facts(facts)
        return service._build_notice_response(diff, facts, notes)

    def generate_debug(self, diff: FullDiff) -> GenerateNoticeDebugResponse:
        try:
            return self._service(require_credentials=True).generate_notice_from_diff(diff)
        except Exception as exc:
            service = self._service(require_credentials=False)
            facts = service.build_change_facts(diff)
            notes = service._fallback_notes_from_facts(facts)
            result = service._build_notice_response(diff, facts, notes)
            return GenerateNoticeDebugResponse(
                result=result,
                facts=facts,
                llm_error=f"{type(exc).__name__}: {exc}",
            )
