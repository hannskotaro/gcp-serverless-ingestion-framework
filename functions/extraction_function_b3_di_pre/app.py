import base64
import csv
import io
import json
import os
from datetime import date, timedelta

import requests
from flask import Flask, Response, jsonify, request

from shared.core.env_loader import EnvLoader
from shared.core.logger import Logger
from shared.core.reference_date_handler import ReferenceDateHandler
from shared.data.bigquery_writer import BigQueryWriter

app = Flask(__name__)
env_loader = EnvLoader()

B3_URL = "https://sistemaswebb3-derivativos.b3.com.br/referenceRatesProxy/Search/GetDownloadFile/{}"


@app.route("/", methods=["POST"])
def main() -> tuple[Response, int]:
    _, trigger_type = env_loader.load_env(request)
    reference_date = ReferenceDateHandler().get_reference_date_from_request(request)

    with Logger(
        automation_name=os.environ["AUTOMATION_NAME"],
        reference_date=reference_date,
        trigger_type=trigger_type,
    ) as logger:
        rows = extrair_dados(reference_date, logger)
        if not rows:
            logger.no_data_found()
            return jsonify(status="NO_DATA_FOUND"), 200

        inserted = BigQueryWriter().insert_raw(
            table="di_pre_252",
            rows=rows,
            source="b3_di_pre_api",
            reference_date=reference_date,
            run_id=logger.run_id,
        )
        logger.info(f"{inserted} rows inserted into raw_ingestion.di_pre_252")
        logger.success()

    return jsonify(status="SUCCESS", rows=inserted), 200


def extrair_dados(reference_date: date, logger) -> list[dict] | None:
    payload = {"language": "pt-br", "date": reference_date.isoformat(), "id": "PRE"}
    token = base64.b64encode(json.dumps(payload).encode()).decode()
    url = B3_URL.format(token)

    logger.info(f"Consultando curva DI x Pré na B3 para {reference_date}")
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()

    raw_body = resp.text.strip().strip('"')
    try:
        csv_text = base64.b64decode(raw_body).decode("latin-1")
    except Exception:
        return None

    reader = csv.DictReader(io.StringIO(csv_text), delimiter=";")
    rows = []
    for raw_row in reader:
        row = {k.strip(): (v or "").strip() for k, v in raw_row.items() if k}
        if not row.get("Dias Corridos"):
            continue
        working_days = int(row["Dias Corridos"])
        rate = float(row["Preço/Taxa"].replace(",", ".")) / 100
        clean = {
            "descricao_taxa": row["Descrição da Taxa"],
            "days": int(row["Dias Úteis"]),
            "working_days": working_days,
            "di_pre": rate,
            "day": (reference_date + timedelta(days=working_days)).isoformat(),
        }
        rows.append({"raw_payload": json.dumps(clean)})

    return rows or None


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)
