"""Testes do validador de chaves desconhecidas no config.jsonc."""

import copy
import glob
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

import deploy_controller as dc  # noqa: E402

CONFIG_VALIDO = {
    "memory": "256Mi",
    "timeout": "840s",
    "scheduler": {
        "state": "ENABLED",
        "cron": "0 7 * * *",
        "timezone": "America/Sao_Paulo",
        "http_method": "POST",
        "body": {"reference_date": None},
        "headers": {"Content-Type": "application/json"},
        "attempt_deadline": "180s",
        "retry": {"max_retry_attempts": 0, "max_retry_duration": "0s"},
    },
    "raw": {
        "table": "tabela_exemplo",
        "load_mode": "APPEND",
        "dataset": "raw_ingestion",
    },
}


def test_config_valido_passa():
    assert dc.validate_config("fn_teste", copy.deepcopy(CONFIG_VALIDO)) is True


def test_chave_camelcase_no_scheduler_falha():
    cfg = copy.deepcopy(CONFIG_VALIDO)
    cfg["scheduler"]["httpmethod"] = cfg["scheduler"].pop("http_method")
    assert dc.validate_config("fn_teste", cfg) is False


def test_chave_camelcase_no_retry_falha():
    cfg = copy.deepcopy(CONFIG_VALIDO)
    cfg["scheduler"]["retry"]["maxRetryAttempts"] = cfg["scheduler"]["retry"].pop("max_retry_attempts")
    assert dc.validate_config("fn_teste", cfg) is False


def test_chave_desconhecida_no_raw_apenas_avisa(capsys):
    cfg = copy.deepcopy(CONFIG_VALIDO)
    cfg["raw"]["loadmode"] = cfg["raw"].pop("load_mode")
    assert dc.validate_config("fn_teste", cfg) is True
    assert "raw.'loadmode'" in capsys.readouterr().out


def test_job_tambem_valida_chaves_do_scheduler():
    cfg = copy.deepcopy(CONFIG_VALIDO)
    cfg["runtime"] = "cloud_run_job"
    cfg["scheduler"]["attemptdeadline"] = cfg["scheduler"].pop("attempt_deadline")
    assert dc.validate_config("fn_teste", cfg) is False


def test_todos_os_configs_reais_passam_no_validador_de_chaves():
    caminhos = sorted(glob.glob(os.path.join(REPO_ROOT, "functions", "*", "config.jsonc")))
    assert caminhos
    for caminho in caminhos:
        with open(caminho, encoding="utf-8") as fh:
            cfg = dc.parse_jsonc(fh.read())
        folder = os.path.basename(os.path.dirname(caminho))
        assert dc.valida_chaves_desconhecidas(folder, cfg) == []
