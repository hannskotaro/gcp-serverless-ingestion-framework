import json
import os
from typing import Any

from google.api_core.exceptions import GoogleAPIError
from google.cloud import secretmanager


class SecretsHandler:
    """Cliente para leitura de credenciais no GCP Secret Manager com cache em memória."""

    def __init__(self, logger=None):
        self.logger = logger
        self._cache: dict[str, str] = {}
        self._project_id = os.environ.get("GCP_PROJECT_ID")
        if not self._project_id:
            raise EnvironmentError("GCP_PROJECT_ID não definida.")
        self._client = secretmanager.SecretManagerServiceClient()

    @staticmethod
    def _normalize_name(secret_name: str) -> str:
        return secret_name.replace("/", "_").replace("-", "_")

    def _log(self, message: str, level: str = "info") -> None:
        if self.logger:
            fn = getattr(self.logger, level, None) or getattr(self.logger, "info")
            fn(message if hasattr(self.logger, level) else f"[{level.upper()}] {message}")
        else:
            print(f"[SecretsHandler][{level.upper()}] {message}")

    def get_secret_value(self, secret_name: str) -> str:
        if secret_name in self._cache:
            return self._cache[secret_name]

        gcp_name = self._normalize_name(secret_name)
        self._log(f"Buscando segredo '{gcp_name}' no Secret Manager...", "info")

        try:
            name = f"projects/{self._project_id}/secrets/{gcp_name}/versions/latest"
            response = self._client.access_secret_version(request={"name": name})
            secret = response.payload.data.decode("utf-8")
            self._cache[secret_name] = secret
            return secret
        except GoogleAPIError as e:
            self._log(f"Erro ao buscar segredo '{gcp_name}': {e}", "error")
            raise

    def get_secret_as_json(self, secret_name: str) -> dict[str, Any]:
        raw = self.get_secret_value(secret_name)
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            self._log(f"Segredo '{secret_name}' não contém JSON válido.", "error")
            raise
