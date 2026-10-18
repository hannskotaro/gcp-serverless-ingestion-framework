from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

DateMode = Literal["last_working_day", "last_day", "same_day"]


class ReferenceDateHandler:
    """Resolve a data de referência a partir do payload JSON da requisição HTTP."""

    def get_reference_date_from_request(
        self, request, mode: DateMode = "last_working_day"
    ) -> date:
        body = self._parse_request_body(request)
        if body.get("reference_date"):
            return date.fromisoformat(body["reference_date"])
        if mode == "last_working_day":
            return self.get_last_working_day()
        if mode == "last_day":
            return self.get_yesterday()
        return self.get_today()

    def get_today(self, tz: str = "America/Sao_Paulo") -> date:
        return datetime.now(ZoneInfo(tz)).date()

    def get_yesterday(self, tz: str = "America/Sao_Paulo") -> date:
        return self.get_today(tz) - timedelta(days=1)

    def get_last_working_day(self, tz: str = "America/Sao_Paulo") -> date:
        return self.get_working_day_before(self.get_today(tz))

    @staticmethod
    def get_working_day_before(ref: date) -> date:
        offset = 3 if ref.weekday() == 0 else (2 if ref.weekday() == 6 else 1)
        return ref - timedelta(days=offset)

    @staticmethod
    def _parse_request_body(request) -> dict:
        try:
            return request.get_json(silent=True) or {}
        except Exception:
            return {}
