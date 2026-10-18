"""Testes de validação e geração de flags para Direct VPC egress (bloco 'network')."""

import copy
import glob
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

import deploy_controller as dc  # noqa: E402

CONFIG_BASE = {
    "memory": "512Mi",
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
    "raw": {"table": "t", "load_mode": "APPEND", "dataset": "raw_ingestion"},
}

NETWORK_OK = {"network": "default", "subnet": "static-egress-subnet", "vpc_egress": "all-traffic"}
STATIC_EGRESS_FOLDERS = {"extraction_function_batch_job_example"}


def test_sem_bloco_network_nao_gera_flags():
    assert dc.build_network_args(copy.deepcopy(CONFIG_BASE)) == []


def test_bloco_network_gera_tres_flags_na_ordem():
    cfg = copy.deepcopy(CONFIG_BASE)
    cfg["network"] = dict(NETWORK_OK)
    assert dc.build_network_args(cfg) == [
        "--network", "default",
        "--subnet", "static-egress-subnet",
        "--vpc-egress", "all-traffic",
    ]


def test_config_com_network_completo_valida():
    cfg = copy.deepcopy(CONFIG_BASE)
    cfg["network"] = dict(NETWORK_OK)
    assert dc.validate_config("fn", cfg) is True


def test_network_parcial_reprova_deploy():
    for faltante in ("network", "subnet", "vpc_egress"):
        cfg = copy.deepcopy(CONFIG_BASE)
        cfg["network"] = {k: v for k, v in NETWORK_OK.items() if k != faltante}
        assert dc.validate_config("fn", cfg) is False


def test_network_chave_desconhecida_reprova():
    cfg = copy.deepcopy(CONFIG_BASE)
    cfg["network"] = dict(NETWORK_OK, connector="custom-conn")
    assert dc.validate_config("fn", cfg) is False


def test_network_vpc_egress_invalido_reprova():
    cfg = copy.deepcopy(CONFIG_BASE)
    cfg["network"] = dict(NETWORK_OK, vpc_egress="invalid-mode")
    assert dc.validate_config("fn", cfg) is False


def test_configs_com_egress_estatico_declaram_network_valido():
    for caminho in sorted(glob.glob(os.path.join(REPO_ROOT, "functions", "*", "config.jsonc"))):
        folder = os.path.basename(os.path.dirname(caminho))
        with open(caminho, encoding="utf-8") as fh:
            cfg = dc.parse_jsonc(fh.read())
        if folder in STATIC_EGRESS_FOLDERS:
            assert cfg.get("network") == NETWORK_OK
            assert dc.valida_network(folder, cfg) == []
        else:
            assert "network" not in cfg
