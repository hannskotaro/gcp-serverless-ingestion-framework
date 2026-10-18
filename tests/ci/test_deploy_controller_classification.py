"""Testes de classificação de candidatos no deploy seletivo (Services, Jobs e pastas removidas)."""

import os
import shutil
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

import deploy_controller as dc  # noqa: E402


def test_removed_folders_are_skipped_not_failed():
    candidates = [
        "extraction_function_b3_di_pre",
        "extraction_function_legacy_removed_a",
        "extraction_function_legacy_removed_b",
    ]
    to_deploy, skipped, failed = [], [], []
    for folder in candidates:
        decision = dc.classify_function_candidate(folder)["decision"]
        if decision == "DEPLOY":
            to_deploy.append(folder)
        elif decision == "SKIP_REMOVED_FOLDER":
            skipped.append(folder)
        else:
            failed.append(folder)

    assert to_deploy == ["extraction_function_b3_di_pre"]
    assert set(skipped) == {
        "extraction_function_legacy_removed_a",
        "extraction_function_legacy_removed_b",
    }
    assert failed == []


def test_existing_folder_missing_config_fails():
    tmp_path = os.path.join(dc.FUNCTIONS_DIR, "tests_tmp_no_config")
    os.makedirs(tmp_path, exist_ok=True)
    with open(os.path.join(tmp_path, "app.py"), "w") as f:
        f.write("# stub\n")
    try:
        res = dc.classify_function_candidate("tests_tmp_no_config")
        assert res["decision"] == "FAIL_MISSING_CONFIG"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_existing_folder_missing_app_fails():
    tmp_path = os.path.join(dc.FUNCTIONS_DIR, "tests_tmp_no_app")
    os.makedirs(tmp_path, exist_ok=True)
    with open(os.path.join(tmp_path, "config.jsonc"), "w") as f:
        f.write("{}\n")
    try:
        res = dc.classify_function_candidate("tests_tmp_no_app")
        assert res["decision"] == "FAIL_MISSING_APP"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_valid_service_folder_is_deploy():
    res = dc.classify_function_candidate("extraction_function_b3_di_pre")
    assert res["decision"] == "DEPLOY"


def test_cloud_run_job_folder_with_main_is_deploy():
    res = dc.classify_function_candidate("extraction_function_batch_job_example")
    assert res["runtime"] == "cloud_run_job"
    assert res["main_py"] is True
    assert res["decision"] == "DEPLOY"


def test_cloud_run_job_missing_main_fails_with_job_entrypoint():
    tmp_path = os.path.join(dc.FUNCTIONS_DIR, "tests_tmp_job_no_main")
    os.makedirs(tmp_path, exist_ok=True)
    with open(os.path.join(tmp_path, "config.jsonc"), "w") as f:
        f.write('{"runtime": "cloud_run_job", "memory": "1Gi", "timeout": "3600s"}\n')
    try:
        res = dc.classify_function_candidate("tests_tmp_job_no_main")
        assert res["decision"] == "FAIL_JOB_ENTRYPOINT"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
