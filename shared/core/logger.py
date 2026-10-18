import json
import os
import traceback
import uuid
from datetime import date, datetime, timezone

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

_OPS_SCHEMA = [
    bigquery.SchemaField("log_time",        "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("reference_date",  "DATE",      mode="NULLABLE"),
    bigquery.SchemaField("run_id",          "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("subtype",         "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("type",            "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("message",         "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("automation_name", "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("trigger_type",    "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("run_time",        "INTEGER",   mode="NULLABLE"),
]

_SUBTYPE_SEVERITY = {
    "EXCEPTION": "ERROR",
    "FINISH_LOG_NOT_CALLED": "WARNING",
}


class Logger:
    """
    Context manager de observabilidade para extrações no Cloud Run.
    Emite JSON estruturado no stdout (Cloud Logging) e grava o registro final
    de execução em `ops.extraction_runs` no BigQuery ao sair do bloco `with`.
    """

    def __init__(
        self,
        *,
        automation_name: str,
        reference_date: date,
        trigger_type: str = "MANUAL",
    ):
        if not automation_name:
            raise ValueError("automation_name is required")

        self._automation_name = automation_name
        self._reference_date = reference_date
        self._trigger_type = trigger_type
        self._run_id = str(uuid.uuid4())
        self._finished_with: str | None = None
        self._in_context = False
        self._started_at: datetime | None = None
        self._project_id = os.environ["GCP_PROJECT_ID"]
        self._bq_table = f"{self._project_id}.ops.extraction_runs"

    @property
    def run_id(self) -> str:
        return self._run_id

    def info(self, message: str):
        return self._log("INFO", "INFO", message)

    def warning(self, message: str):
        return self._log("WARNING", "WARNING", message)

    def success(self):
        return self._finish("SUCCESS", f"Data processed successfully for {self._reference_date}")

    def no_data_found(self):
        return self._finish("NO_DATA_FOUND", f"No data found for {self._reference_date}")

    def already_processed(self):
        return self._finish("ALREADY_PROCESSED", f"Already processed for {self._reference_date}")

    def update_reference_date(self, reference_date: date):
        self._reference_date = reference_date
        self._finished_with = None
        return self.info(f"Reference date updated to {reference_date}")

    def _finish(self, status: str, msg: str):
        if self._finished_with is not None:
            return None
        self._finished_with = status
        return self._log("INFO", status, msg, force=True)

    def __enter__(self):
        self._in_context = True
        self._started_at = datetime.now(timezone.utc)
        self._log("INFO", "STARTED", f"Started {self._automation_name} for {self._reference_date}")
        return self

    def __exit__(self, exc_type, exc_value, tb):
        error_message = None

        if exc_type is not None and self._finished_with is None:
            self._finished_with = "EXCEPTION"
            error_message = f"{exc_value}\n{''.join(traceback.format_tb(tb))}"
            self._log("ERROR", "EXCEPTION", error_message, force=True)

        if self._finished_with is None:
            self._finished_with = "FINISH_LOG_NOT_CALLED"
            self._log(
                "WARNING",
                "FINISH_LOG_NOT_CALLED",
                "Finish log not called. Use success(), no_data_found() or already_processed().",
                force=True,
            )

        self._write_to_bq(error_message)
        self._in_context = False
        return False

    def _write_to_bq(self, error_message: str | None = None) -> None:
        try:
            client = bigquery.Client(project=self._project_id)
            row = {
                "log_time":        datetime.now(timezone.utc).isoformat(),
                "reference_date":  str(self._reference_date),
                "run_id":          self._run_id,
                "subtype":         self._finished_with,
                "type":            _SUBTYPE_SEVERITY.get(self._finished_with, "INFO"),
                "message":         error_message,
                "automation_name": self._automation_name,
                "trigger_type":    self._trigger_type,
                "run_time":        self._elapsed_ms(),
            }
            try:
                errors = client.insert_rows_json(self._bq_table, [row])
            except NotFound:
                table = bigquery.Table(self._bq_table, schema=_OPS_SCHEMA)
                client.create_table(table, exists_ok=True)
                errors = client.insert_rows_json(self._bq_table, [row])

            if errors:
                self._stdout_log("ERROR", "BQ_INSERT_FAILED", str(errors))
        except Exception as e:
            self._stdout_log("ERROR", "BQ_INSERT_FAILED", str(e))

    def _log(self, severity: str, subtype: str, message: str, force: bool = False):
        if not force and self._finished_with is not None:
            return None
        if not self._in_context:
            raise RuntimeError("Logger must be used inside a `with` block.")
        return self._stdout_log(severity, subtype, message)

    def _stdout_log(self, severity: str, subtype: str, message: str) -> dict:
        entry = {
            "severity":        severity,
            "message":         f"[{subtype}] {message}",
            "reference_date":  str(self._reference_date),
            "subtype":         subtype,
            "automation_name": self._automation_name,
            "run_id":          self._run_id,
        }
        print(json.dumps(entry), flush=True)
        return entry

    def _elapsed_ms(self) -> int:
        if self._started_at is None:
            return 0
        return int((datetime.now(timezone.utc) - self._started_at).total_seconds() * 1000)
