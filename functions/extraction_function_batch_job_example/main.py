"""
Cloud Run Job Example: Extração batch de alto volume com concorrência controlada
e gravação na camada Raw do BigQuery.

Diferente dos Cloud Run Services (acionados via HTTP request/response até 60 min),
os Cloud Run Jobs executam como processos batch dedicados (até 24h), ideais para
varreduras paginadas pesadas ou conciliações históricas com checkpointing.
"""

import os
from datetime import date, timedelta

from shared.core.logger import Logger
from shared.data.bigquery_writer import BigQueryWriter

AUTOMATION_NAME = os.getenv("AUTOMATION_NAME", "extraction_function_batch_job_example")
TABLE_NAME = "batch_contracts_snapshot"
SOURCE_LABEL = "batch_job_api"


def fetch_batch_records(reference_date: date) -> list[dict]:
    """Simula extração batch paginada para a data de referência."""
    return [
        {
            "contract_id": f"CTR-{i:04d}",
            "reference_date": reference_date.isoformat(),
            "outstanding_balance": round(1500.0 * i, 2),
            "status": "ACTIVE",
        }
        for i in range(1, 6)
    ]


def main() -> None:
    reference_date = date.today() - timedelta(days=1)

    with Logger(
        automation_name=AUTOMATION_NAME,
        reference_date=reference_date,
        trigger_type="SCHEDULED",
    ) as logger:
        records = fetch_batch_records(reference_date)
        if not records:
            logger.no_data_found()
            return

        writer = BigQueryWriter()
        inserted = writer.insert_raw(
            table=TABLE_NAME,
            rows=records,
            source=SOURCE_LABEL,
            reference_date=reference_date,
            run_id=logger.run_id,
            load_mode="APPEND",
        )
        logger.info(f"{inserted} registros gravados em raw_ingestion.{TABLE_NAME}")
        logger.success()


if __name__ == "__main__":
    main()
