"""Garante que o nome completo da revisão no Cloud Run respeite o limite RFC 1123 de 63 caracteres."""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

import deploy_controller as dc  # noqa: E402


def test_revision_name_fits_63_chars(monkeypatch):
    monkeypatch.setattr(dc, "BUILD_NUMBER", "1000")
    monkeypatch.setattr(dc, "COMMIT_HASH", "b04fec45")
    for folder in [
        "hello_world",
        "extraction_function_b3_di_pre",
        "extraction_function_batch_job_example",
        "extraction_function_corporate_payroll_reconciliation",
    ]:
        name = f"{dc.service_name(folder)}-{dc.revision_suffix(folder)}"
        assert len(name) <= 63 and not name.endswith("-"), name
    assert dc.revision_suffix("hello_world") == "build1000-b04fec45"
