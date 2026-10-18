"""
Gravação na camada Raw do BigQuery.
Cria tabelas particionadas por DATE(_reference_date) e clusterizadas por _source,
adicionando novas colunas automaticamente via DDL quando o payload muda.
"""

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Any, Literal

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

logger = logging.getLogger(__name__)

LoadMode = Literal["APPEND", "PARTITION_REPLACE"]

_METADATA_FIELDS = [
    bigquery.SchemaField("_ingested_at",    "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("_source",         "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("_run_id",         "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("_reference_date", "DATE",      mode="REQUIRED"),
    bigquery.SchemaField("_load_mode",      "STRING",    mode="NULLABLE"),
]
_METADATA_NAMES = {f.name for f in _METADATA_FIELDS}
_JSON_COLUMNS = {"raw_payload"}

_PYTHON_TO_BQ = {
    int:      "INTEGER",
    float:    "FLOAT",
    bool:     "BOOLEAN",
    str:      "STRING",
    date:     "DATE",
    datetime: "TIMESTAMP",
}


def _infer_bq_type(col: str, value: Any) -> str:
    if col in _JSON_COLUMNS:
        return "JSON"
    return _PYTHON_TO_BQ.get(type(value), "STRING") if value is not None else "STRING"


class BigQueryWriter:
    _CHUNK_SIZE = 500

    def __init__(self, project_id: str | None = None):
        self._project_id = project_id or os.environ["GCP_PROJECT_ID"]
        self._client = bigquery.Client(project=self._project_id)

    def _ensure_table(self, table_ref: str, sample_row: dict) -> None:
        try:
            self._client.get_table(table_ref)
            return
        except NotFound:
            pass

        schema = list(_METADATA_FIELDS) + [
            bigquery.SchemaField(col, _infer_bq_type(col, val), mode="NULLABLE")
            for col, val in sample_row.items()
            if col not in _METADATA_NAMES
        ]
        table = bigquery.Table(table_ref, schema=schema)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="_reference_date",
        )
        table.clustering_fields = ["_source"]
        self._client.create_table(table, exists_ok=True)
        logger.info(f"Tabela '{table_ref}' criada.")

    def _add_missing_columns(self, table_ref: str, rows: list[dict]) -> None:
        table = self._client.get_table(table_ref)
        existing = {f.name for f in table.schema}
        new_fields = {
            col: _infer_bq_type(col, val)
            for row in rows
            for col, val in row.items()
            if col not in existing and col not in _METADATA_NAMES
        }
        if not new_fields:
            return

        table.schema = list(table.schema) + [
            bigquery.SchemaField(col, bq_type, mode="NULLABLE")
            for col, bq_type in new_fields.items()
        ]
        self._client.update_table(table, ["schema"])
        logger.info(f"Schema atualizado em '{table_ref}': {list(new_fields)}")

    def _delete_partition(self, table_ref: str, reference_date: date) -> None:
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("ref_date", "DATE", reference_date),
            ]
        )
        self._client.query(
            f"DELETE FROM `{table_ref}` WHERE _reference_date = @ref_date",
            job_config=job_config,
        ).result()

    def _insert_chunk(
        self, table_ref: str, chunk: list[dict], raw_chunk: list[dict], retries: int
    ) -> int:
        for attempt in range(1, retries + 2):
            errors = self._client.insert_rows_json(table_ref, chunk)
            if not errors:
                return len(chunk)

            err_text = json.dumps(errors).lower()
            if ("no such field" in err_text or "invalid" in err_text) and attempt <= retries:
                self._add_missing_columns(table_ref, raw_chunk)
                continue

            raise RuntimeError(
                f"BigQueryWriter: falha ao inserir em '{table_ref}': {json.dumps(errors)}"
            )
        raise RuntimeError(f"BigQueryWriter: falha após {retries} tentativas.")

    def insert_raw(
        self,
        *,
        table: str,
        rows: list[dict[str, Any]],
        source: str,
        reference_date: date,
        run_id: str,
        load_mode: LoadMode = "APPEND",
        dataset: str = "raw_ingestion",
        max_retries: int = 2,
    ) -> int:
        if not rows:
            return 0

        table_ref = f"{self._project_id}.{dataset}.{table}"
        now = datetime.now(timezone.utc).isoformat()

        enriched = [
            {
                "_ingested_at":    now,
                "_source":         source,
                "_run_id":         run_id,
                "_reference_date": str(reference_date),
                "_load_mode":      load_mode,
                **row,
            }
            for row in rows
        ]

        self._ensure_table(table_ref, rows[0])
        if load_mode == "PARTITION_REPLACE":
            self._delete_partition(table_ref, reference_date)

        inserted = 0
        for start in range(0, len(enriched), self._CHUNK_SIZE):
            end = start + self._CHUNK_SIZE
            inserted += self._insert_chunk(
                table_ref, enriched[start:end], rows[start:end], max_retries
            )

        logger.info(f"{inserted} linha(s) inserida(s) em '{table_ref}' [{load_mode}].")
        return inserted
