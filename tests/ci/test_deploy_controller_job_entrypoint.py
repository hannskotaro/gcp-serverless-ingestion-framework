"""Testes do entrypoint parametrizado do Cloud Run Job (Dockerfile.job)."""

import os
import re
import sys
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

import deploy_controller as dc  # noqa: E402


def _dockerfile_job() -> str:
    with open(os.path.join(REPO_ROOT, "Dockerfile.job"), encoding="utf-8") as f:
        return f.read()


def test_dockerfile_job_declara_arg_function_name():
    conteudo = _dockerfile_job()
    assert re.search(r"^ARG FUNCTION_NAME=", conteudo, re.MULTILINE)
    assert re.search(r"^ENV FUNCTION_NAME=\$\{FUNCTION_NAME\}", conteudo, re.MULTILINE)


def test_dockerfile_job_usa_a_variavel_no_entrypoint():
    conteudo = _dockerfile_job()
    assert "functions/${FUNCTION_NAME}/main.py" in conteudo


def _captura_build(folder: str) -> list:
    comandos = []

    def fake_run_cmd(cmd, description):
        comandos.append(cmd)
        raise RuntimeError("stop after build")

    with patch.object(dc, "run_cmd", side_effect=fake_run_cmd):
        dc.deploy_job(folder)

    return comandos[0] if comandos else []


def test_build_de_job_passa_function_name_da_pasta():
    cmd = _captura_build("extraction_function_batch_job_example")
    assert "--build-arg" in cmd
    assert cmd[cmd.index("--build-arg") + 1] == "FUNCTION_NAME=extraction_function_batch_job_example"
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "Dockerfile.job"


def test_jobs_distintos_geram_entrypoints_distintos():
    a = _captura_build("extraction_function_batch_job_example")
    b = _captura_build("extraction_function_b3_di_pre")
    assert a[a.index("--build-arg") + 1] != b[b.index("--build-arg") + 1]
