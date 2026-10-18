"""
Leitura de bancos relacionais (MySQL e Postgres) via SQLAlchemy e GCP Secret Manager.
Permite apenas consultas (SELECT / WITH); a escrita ocorre no BigQuery via BigQueryWriter.
"""

import logging
from typing import Any, Optional

from sqlalchemy import Engine, create_engine, text

from shared.secrets_handler import SecretsHandler

logger = logging.getLogger(__name__)


class DbReader:
    def __init__(self, secret_name: str):
        self._secret_name = secret_name
        self._engine: Engine | None = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = self._build_engine()
        return self._engine

    def _build_engine(self) -> Engine:
        logger.info(f"DbReader: buscando credenciais do secret '{self._secret_name}'...")
        creds = SecretsHandler().get_secret_as_json(self._secret_name)
        host, user, password, database = (
            creds["host"],
            creds["user"],
            creds["password"],
            creds["database"],
        )
        dialect = creds.get("dialect", "mysql").lower()

        if dialect == "mysql":
            url = f"mysql+mysqlconnector://{user}:{password}@{host}/{database}"
        elif dialect in ("postgres", "postgresql"):
            url = f"postgresql+psycopg2://{user}:{password}@{host}/{database}"
        else:
            raise ValueError(f"DbReader: dialect não suportado: '{dialect}'. Use 'mysql' ou 'postgres'.")

        logger.info(f"DbReader: conectado em {dialect}://{host}/{database}")
        return create_engine(url)

    def select(
        self,
        table: str,
        *,
        columns: Optional[list[str]] = None,
        filters: Optional[dict[str, Any]] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        cols = ", ".join(columns) if columns else "*"
        query = f"SELECT {cols} FROM {table}"
        params: dict = {}

        if filters:
            clauses = [f"{k} = :p{i}" for i, k in enumerate(filters.keys())]
            query += " WHERE " + " AND ".join(clauses)
            params = {f"p{i}": v for i, (_, v) in enumerate(filters.items())}

        if order_by:
            query += f" ORDER BY {order_by}"
        if limit:
            query += f" LIMIT {limit}"

        return self._execute_query(query, params)

    def query(self, sql: str, params: Optional[dict] = None) -> list[dict]:
        sql_upper = sql.strip().upper()
        if not sql_upper.startswith(("SELECT", "WITH")):
            raise ValueError("DbReader.query() aceita apenas SELECT ou WITH.")
        return self._execute_query(sql, params or {})

    def _execute_query(self, sql: str, params: dict) -> list[dict]:
        logger.info(f"DbReader: executando query em '{self._secret_name}'...")
        with self.engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(text(sql), params).mappings()]
        logger.info(f"DbReader: {len(rows)} linha(s) retornada(s).")
        return rows

    def close(self) -> None:
        if self._engine:
            self._engine.dispose()
            self._engine = None
            logger.info("DbReader: conexão encerrada.")
