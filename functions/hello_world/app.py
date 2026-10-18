"""Template padrão para funções HTTP (Cloud Run Service)."""

import os
from datetime import date

from flask import Flask, Response, jsonify, request

from shared.core.env_loader import EnvLoader
from shared.core.logger import Logger
from shared.core.reference_date_handler import ReferenceDateHandler
from shared.data.bigquery_writer import BigQueryWriter

app = Flask(__name__)
env_loader = EnvLoader()


@app.route("/", methods=["POST"])
def main() -> tuple[Response, int]:
    _, trigger_type = env_loader.load_env(request)
    reference_date = ReferenceDateHandler().get_reference_date_from_request(request)

    with Logger(
        automation_name=os.environ["AUTOMATION_NAME"],
        reference_date=reference_date,
        trigger_type=trigger_type,
    ) as logger:
        dados = extrair_dados(reference_date)
        if not dados:
            logger.no_data_found()
            return jsonify({"status": "NO_DATA_FOUND"}), 200

        rows_inserted = BigQueryWriter().insert_raw(
            table="hello_world",
            rows=dados,
            source="hello_world",
            reference_date=reference_date,
            run_id=logger.run_id,
        )
        logger.info(f"{rows_inserted} rows inserted into raw_ingestion.hello_world")
        logger.success()

    return jsonify({"status": "SUCCESS", "rows": rows_inserted}), 200


def extrair_dados(reference_date: date) -> list[dict] | None:
    return [
        {"contrato_id": "C001", "valor": 1000.0, "data": reference_date.isoformat()},
        {"contrato_id": "C002", "valor": 2000.0, "data": reference_date.isoformat()},
    ]


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)
