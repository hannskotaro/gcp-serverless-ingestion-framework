#!/usr/bin/env python3
"""
Orquestrador de CI/CD seletivo para Cloud Run Services, Cloud Run Jobs e Cloud Scheduler.

Fluxo:
  1. Detecta funções alteradas no commit via `git diff --name-status HEAD^ HEAD`
     (ou redeploy completo quando `shared/` sofre alterações).
  2. Classifica cada candidato em disco (DEPLOY, SKIP_REMOVED_FOLDER ou falha de contrato).
  3. Valida `config.jsonc` (limites de recursos, chaves válidas e Direct VPC egress).
  4. Executa build/push da imagem no Artifact Registry e deploya como Service ou Job.
  5. Para Services, valida prontidão (smoke test) e sincroniza o Cloud Scheduler.
"""

import json
import os
import re
import subprocess
import sys

FUNCTIONS_DIR   = "functions"
GCP_REGION      = os.getenv("GCP_REGION", "us-east1")
GCP_PROJECT     = os.getenv("GCP_PROJECT_ID", "")
REPO_NAME       = os.getenv("ARTIFACT_REPO_NAME", "serverless-extractors")
SERVICE_ACCOUNT = os.getenv(
    "GCP_SERVICE_ACCOUNT",
    f"cloud-run-runtime@{GCP_PROJECT}.iam.gserviceaccount.com",
)
REGISTRY        = f"{GCP_REGION}-docker.pkg.dev/{GCP_PROJECT}/{REPO_NAME}"

COMMIT_HASH  = os.getenv("GITHUB_SHA",        "local")[:8]
BUILD_NUMBER = os.getenv("GITHUB_RUN_NUMBER", "local")
BRANCH       = os.getenv("GITHUB_REF_NAME",   "local")
GH_REPO      = os.getenv("GITHUB_REPOSITORY", "hannskotaro/gcp-serverless-ingestion-framework")

FAILURE_DETAILS: dict[str, dict] = {}

_JSONC_COMMENT_RE = re.compile(r'"(?:\\.|[^"\\])*"|(//[^\n]*|/\*[\s\S]*?\*/)')

SCHEDULER_KEYS_VALIDAS = {
    "state", "cron", "timezone", "http_method", "body",
    "headers", "attempt_deadline", "retry", "name",
}
RETRY_KEYS_VALIDAS   = {"max_retry_attempts", "max_retry_duration"}
RAW_KEYS_VALIDAS     = {"table", "dataset", "load_mode"}
NETWORK_KEYS_VALIDAS = {"network", "subnet", "vpc_egress"}
VPC_EGRESS_VALIDOS   = {"all-traffic", "private-ranges-only"}


def header(title: str) -> None:
    bar = "═" * 64
    print(f"\n{bar}\n  {title}\n{bar}")


def ok(msg: str) -> None:   print(f"  [OK]   {msg}")
def info(msg: str) -> None: print(f"  [INFO] {msg}")
def warn(msg: str) -> None: print(f"  [WARN] {msg}")
def fail(msg: str) -> None: print(f"  [FAIL] {msg}")
def step(msg: str) -> None: print(f"\n  --> {msg}")


def parse_jsonc(text: str) -> dict:
    """Remove comentários // e /* */ preservando strings literais e faz parse com stdlib json."""
    cleaned = _JSONC_COMMENT_RE.sub(lambda m: "" if m.group(1) else m.group(0), text)
    return json.loads(cleaned)


def load_config(folder: str) -> dict:
    path = os.path.join(FUNCTIONS_DIR, folder, "config.jsonc")
    with open(path, encoding="utf-8") as f:
        return parse_jsonc(f.read())


def run_cmd(cmd: list[str], description: str) -> subprocess.CompletedProcess:
    print(f"\n  [RUN] {description}\n  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    for line in result.stdout.strip().splitlines():
        print(f"    | {line}")
    if result.returncode != 0:
        for line in result.stderr.strip().splitlines():
            print(f"    ! {line}")
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stderr)
    return result


def classify_deploy_stage(cmd: list[str]) -> str:
    if not cmd:
        return "UNKNOWN"
    if cmd[:2] == ["docker", "build"]:
        return "DOCKER_BUILD"
    if cmd[:2] == ["docker", "push"]:
        return "DOCKER_PUSH"
    if cmd[:3] in (["gcloud", "run", "deploy"], ["gcloud", "run", "jobs"]):
        return "CLOUD_RUN_DEPLOY"
    if cmd[:3] == ["gcloud", "run", "services"]:
        return "SMOKE_TEST"
    if cmd[:2] == ["gcloud", "scheduler"]:
        return "SCHEDULER"
    return "UNKNOWN"


def service_name(folder: str) -> str:
    return folder.replace("_", "-")


def revision_suffix(folder: str) -> str:
    suffix = f"build{BUILD_NUMBER}-{COMMIT_HASH}"
    max_svc_len = 63 - len(suffix) - 1
    svc = service_name(folder)
    if len(svc) > max_svc_len:
        return suffix[: max(1, 63 - len(svc) - 1)].rstrip("-")
    return suffix


def get_all_functions() -> list[str]:
    if not os.path.exists(FUNCTIONS_DIR):
        return []
    return sorted(
        d for d in os.listdir(FUNCTIONS_DIR)
        if os.path.isdir(os.path.join(FUNCTIONS_DIR, d))
        and not d.startswith(".")
        and d not in {"__pycache__", ".venv", ".git"}
    )


def is_cloud_run_job(cfg: dict) -> bool:
    return str(cfg.get("runtime", "")).strip().lower() == "cloud_run_job"


def classify_function_candidate(folder: str) -> dict:
    folder_path = os.path.join(FUNCTIONS_DIR, folder)
    exists      = os.path.isdir(folder_path)
    app_py      = os.path.isfile(os.path.join(folder_path, "app.py")) if exists else False
    main_py     = os.path.isfile(os.path.join(folder_path, "main.py")) if exists else False
    config_json = os.path.isfile(os.path.join(folder_path, "config.jsonc")) if exists else False

    is_job = False
    if exists and config_json:
        try:
            is_job = is_cloud_run_job(load_config(folder))
        except Exception:
            is_job = False

    runtime = "cloud_run_job" if is_job else "cloud_run_service"

    if not exists:
        decision, reason = "SKIP_REMOVED_FOLDER", "pasta removida ou renomeada neste commit"
    elif not config_json:
        decision, reason = "FAIL_MISSING_CONFIG", f"config.jsonc ausente em {folder_path}"
    elif is_job and not main_py:
        decision, reason = "FAIL_JOB_ENTRYPOINT", f"Cloud Run Job sem main.py em {folder_path}"
    elif not is_job and not app_py:
        decision, reason = "FAIL_MISSING_APP", f"Cloud Run Service sem app.py em {folder_path}"
    else:
        decision, reason = "DEPLOY", "arquivos obrigatórios presentes"

    return {
        "candidate":    folder,
        "path":         folder_path,
        "exists":       exists,
        "app_py":       app_py,
        "main_py":      main_py,
        "config_jsonc": config_json,
        "runtime":      runtime,
        "decision":     decision,
        "reason":       reason,
    }


def normalize_scheduler(cfg: dict) -> dict:
    s = cfg.get("scheduler") or {}
    retry = s.get("retry") or {}
    return {
        "state":              str(s.get("state", "PAUSED")).strip().upper(),
        "cron":               str(s.get("cron", "")).strip(),
        "timezone":           str(s.get("timezone", "America/Sao_Paulo")).strip(),
        "http_method":        str(s.get("http_method", "POST")).strip().upper(),
        "body":               s.get("body", {"reference_date": None}) or {"reference_date": None},
        "headers":            s.get("headers", {"Content-Type": "application/json"}) or {"Content-Type": "application/json"},
        "attempt_deadline":   str(s.get("attempt_deadline", "180s")).strip(),
        "max_retry_attempts": int(retry.get("max_retry_attempts", 0)),
        "max_retry_duration": str(retry.get("max_retry_duration", "0s")).strip(),
    }


def valida_chaves_desconhecidas(folder: str, cfg: dict) -> list[str]:
    errors = []
    scheduler = cfg.get("scheduler") or {}
    if isinstance(scheduler, dict):
        for chave in sorted(set(scheduler) - SCHEDULER_KEYS_VALIDAS):
            errors.append(f"scheduler.'{chave}' não reconhecida (permitidas: {sorted(SCHEDULER_KEYS_VALIDAS)})")
        retry = scheduler.get("retry") or {}
        if isinstance(retry, dict):
            for chave in sorted(set(retry) - RETRY_KEYS_VALIDAS):
                errors.append(f"scheduler.retry.'{chave}' não reconhecida (permitidas: {sorted(RETRY_KEYS_VALIDAS)})")

    raw = cfg.get("raw") or {}
    if isinstance(raw, dict):
        for chave in sorted(set(raw) - RAW_KEYS_VALIDAS):
            warn(f"[{folder}] raw.'{chave}' ignorada pelo runtime (permitidas: {sorted(RAW_KEYS_VALIDAS)})")

    return errors


def valida_network(folder: str, cfg: dict) -> list[str]:
    net = cfg.get("network")
    if net is None:
        return []
    if not isinstance(net, dict):
        return [f"'network' deve ser um objeto com {sorted(NETWORK_KEYS_VALIDAS)}"]

    errors = [
        f"network.'{k}' não reconhecida (permitidas: {sorted(NETWORK_KEYS_VALIDAS)})"
        for k in sorted(set(net) - NETWORK_KEYS_VALIDAS)
    ]
    for k in sorted(NETWORK_KEYS_VALIDAS):
        if not str(net.get(k, "")).strip():
            errors.append(f"network.'{k}' é obrigatório quando o bloco 'network' está presente")

    egress = str(net.get("vpc_egress", "")).strip()
    if egress and egress not in VPC_EGRESS_VALIDOS:
        errors.append(f"network.vpc_egress inválido: '{egress}' (use {sorted(VPC_EGRESS_VALIDOS)})")
    return errors


def build_network_args(cfg: dict) -> list[str]:
    net = cfg.get("network")
    if not net:
        return []
    return [
        "--network",    str(net["network"]).strip(),
        "--subnet",     str(net["subnet"]).strip(),
        "--vpc-egress", str(net["vpc_egress"]).strip(),
    ]


def validate_config(folder: str, cfg: dict) -> bool:
    errors = []

    memory = cfg.get("memory", "")
    if not memory or not any(memory.endswith(u) for u in ("Mi", "Gi")):
        errors.append(f"'memory' inválido ou ausente: '{memory}' (ex: '512Mi', '1Gi')")

    timeout = cfg.get("timeout", "")
    if not timeout or not timeout.endswith("s"):
        errors.append(f"'timeout' inválido ou ausente: '{timeout}' (ex: '840s')")

    errors.extend(valida_chaves_desconhecidas(folder, cfg))
    errors.extend(valida_network(folder, cfg))

    if not is_cloud_run_job(cfg):
        scheduler = normalize_scheduler(cfg)
        if scheduler["state"] not in {"ENABLED", "PAUSED"}:
            errors.append("scheduler.state deve ser 'ENABLED' ou 'PAUSED'")
        if scheduler["state"] == "ENABLED":
            if not scheduler["cron"] or len(scheduler["cron"].split()) != 5:
                errors.append(f"scheduler.cron inválido: '{scheduler['cron']}' (use 5 campos)")
        if scheduler["http_method"] != "POST":
            errors.append("scheduler.http_method deve ser 'POST'")
        if not scheduler["attempt_deadline"].endswith("s"):
            errors.append("scheduler.attempt_deadline deve terminar em 's' (ex: '180s')")

    if errors:
        fail(f"config.jsonc inválido para '{folder}':")
        for e in errors:
            print(f"    • {e}")
        return False
    return True


def get_changed_functions() -> tuple[list[str], bool]:
    try:
        res = subprocess.run(
            ["git", "diff", "--name-status", "HEAD^", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        lines = res.stdout.strip().splitlines()
    except subprocess.CalledProcessError:
        warn("Diff do git indisponível (commit inicial) - selecionando todas as funções.")
        return get_all_functions(), False

    changed_files = [p for line in lines for p in line.split("\t")[1:]]
    if any(f.startswith("shared/") for f in changed_files):
        warn("shared/ alterado - selecionando todas as funções para redeploy.")
        return get_all_functions(), True

    altered = {
        parts[1]
        for f in changed_files
        if (parts := f.split("/"))[0] == "functions"
        and len(parts) > 1
        and parts[1] not in {"__pycache__", ".venv", ".git", ".gitkeep"}
    }
    return sorted(altered), False


def smoke_test(svc: str) -> str:
    step("Smoke test - verificando status do serviço Cloud Run")
    try:
        res = subprocess.run(
            [
                "gcloud", "run", "services", "describe", svc,
                "--region", GCP_REGION,
                "--format", "value(status.url,status.conditions[0].status)",
            ],
            capture_output=True, text=True,
        )
        parts = res.stdout.strip().split()
        url, ready = (parts[0], parts[1]) if len(parts) >= 2 else ("", "")
        if url and ready == "True":
            ok(f"Serviço ativo: {url}")
            return url
        warn(f"Serviço ainda não reportou Ready=True (url={url}, ready={ready})")
    except Exception as e:
        warn(f"Smoke test falhou: {e}")
    return ""


def _scheduler_job_state(svc: str) -> str:
    res = subprocess.run(
        ["gcloud", "scheduler", "jobs", "describe", svc, "--location", GCP_REGION, "--format", "value(state)"],
        capture_output=True, text=True,
    )
    return (res.stdout.strip() or "NOTFOUND") if res.returncode == 0 else "NOTFOUND"


def manage_scheduler(svc: str, scheduler: dict, url: str) -> None:
    step("Sincronizando Cloud Scheduler")
    current = _scheduler_job_state(svc)

    if scheduler["state"] == "PAUSED":
        if current == "ENABLED":
            run_cmd(
                ["gcloud", "scheduler", "jobs", "pause", svc, "--location", GCP_REGION, "--quiet"],
                f"pausar scheduler job: {svc}",
            )
            ok(f"Job '{svc}' pausado.")
        return

    if not url:
        warn("URL do serviço indisponível - Cloud Scheduler ignorado.")
        return

    body_json   = json.dumps(scheduler["body"], separators=(",", ":"))
    headers_arg = ",".join(f"{k}={v}" for k, v in scheduler["headers"].items())
    shared_args = [
        "--schedule",                   scheduler["cron"],
        "--uri",                        url,
        "--http-method",                scheduler["http_method"],
        "--message-body",               body_json,
        "--oidc-service-account-email", SERVICE_ACCOUNT,
        "--time-zone",                  scheduler["timezone"],
        "--attempt-deadline",           scheduler["attempt_deadline"],
        "--max-retry-attempts",         str(scheduler["max_retry_attempts"]),
        "--max-retry-duration",         scheduler["max_retry_duration"],
        "--location",                   GCP_REGION,
        "--quiet",
    ]

    if current == "NOTFOUND":
        run_cmd(
            ["gcloud", "scheduler", "jobs", "create", "http", svc, *shared_args, "--headers", headers_arg],
            f"criar scheduler job: {svc}",
        )
    else:
        run_cmd(
            ["gcloud", "scheduler", "jobs", "update", "http", svc, *shared_args, "--update-headers", headers_arg],
            f"atualizar scheduler job: {svc}",
        )
        if current == "PAUSED":
            run_cmd(
                ["gcloud", "scheduler", "jobs", "resume", svc, "--location", GCP_REGION, "--quiet"],
                f"retomar scheduler job: {svc}",
            )
    ok(f"Scheduler '{svc}' sincronizado ({scheduler['cron']}).")


def _build_and_push_image(
    folder: str, svc: str, *, dockerfile: str | None = None
) -> tuple[str, str]:
    image_versioned = f"{REGISTRY}/{svc}:{COMMIT_HASH}"
    image_latest    = f"{REGISTRY}/{svc}:latest"

    step(f"Construindo imagem Docker ({dockerfile or 'Dockerfile'})")
    cmd = ["docker", "build"]
    if dockerfile:
        cmd += ["-f", dockerfile]
    cmd += [
        "--cache-from", image_latest,
        "--build-arg",  f"FUNCTION_NAME={folder}",
        "--label",      f"build={BUILD_NUMBER}",
        "--label",      f"commit={COMMIT_HASH}",
        "-t",           image_versioned,
        "-t",           image_latest,
        ".",
    ]
    run_cmd(cmd, f"docker build -f {dockerfile}" if dockerfile else "docker build")
    run_cmd(["docker", "push", image_versioned], "push :commit")
    run_cmd(["docker", "push", image_latest],    "push :latest")
    return image_versioned, image_latest


def _record_deploy_exception(folder: str, exc: Exception) -> bool:
    if isinstance(exc, FileNotFoundError):
        FAILURE_DETAILS[folder] = {
            "stage": "CONFIG_VALIDATION",
            "error": "FileNotFoundError",
            "message": f"config.jsonc não encontrado em functions/{folder}/config.jsonc",
            "expected_config_path": f"functions/{folder}/config.jsonc",
        }
    elif isinstance(exc, subprocess.CalledProcessError):
        FAILURE_DETAILS[folder] = {
            "stage": classify_deploy_stage(exc.cmd),
            "error": f"CalledProcessError(code={exc.returncode})",
            "message": "Falha na execução do comando de deploy.",
            "command_label": " ".join(exc.cmd[:3]) if exc.cmd else "unknown",
        }
    else:
        FAILURE_DETAILS[folder] = {
            "stage": "UNKNOWN",
            "error": type(exc).__name__,
            "message": str(exc),
        }
    fail(f"Falha no deploy de '{folder}': {FAILURE_DETAILS[folder]['message']}")
    return False


def deploy_job(folder: str, cfg: dict | None = None) -> bool:
    svc = service_name(folder)
    header(f"DEPLOY (JOB): {folder} -> {svc}")
    try:
        cfg = cfg if cfg is not None else load_config(folder)
        if not validate_config(folder, cfg):
            return False

        image_versioned, _ = _build_and_push_image(folder, svc, dockerfile="Dockerfile.job")
        run_cmd(
            [
                "gcloud", "run", "jobs", "deploy", svc,
                "--image",           image_versioned,
                "--region",          GCP_REGION,
                "--memory",          cfg.get("memory", "1Gi"),
                "--cpu",             str(cfg.get("cpu", 1)),
                "--task-timeout",    cfg.get("timeout", "3600s"),
                "--max-retries",     str(cfg.get("max_retries", 0)),
                "--parallelism",     str(cfg.get("parallelism", 1)),
                "--tasks",           str(cfg.get("tasks", 1)),
                *build_network_args(cfg),
                "--service-account", SERVICE_ACCOUNT,
                "--set-env-vars",
                f"GCP_PROJECT_ID={GCP_PROJECT},BUILD_NUMBER={BUILD_NUMBER},COMMIT={COMMIT_HASH},AUTOMATION_NAME={svc}",
                "--quiet",
            ],
            f"gcloud run jobs deploy {svc}",
        )
        ok(f"Cloud Run Job '{svc}' atualizado (:{COMMIT_HASH})")
        return True
    except Exception as e:
        return _record_deploy_exception(folder, e)


def deploy_service(folder: str, cfg: dict | None = None) -> bool:
    svc = service_name(folder)
    rev = revision_suffix(folder)
    header(f"DEPLOY (SERVICE): {folder} -> {svc}")
    try:
        cfg = cfg if cfg is not None else load_config(folder)
        if not validate_config(folder, cfg):
            return False

        scheduler = normalize_scheduler(cfg)
        image_versioned, _ = _build_and_push_image(folder, svc)
        run_cmd(
            [
                "gcloud", "run", "deploy", svc,
                "--image",           image_versioned,
                "--region",          GCP_REGION,
                "--platform",        "managed",
                "--memory",          cfg.get("memory", "512Mi"),
                "--timeout",         cfg.get("timeout", "840s"),
                "--max-instances",   "1",
                "--min-instances",   "0",
                "--concurrency",     "1",
                "--no-allow-unauthenticated",
                "--revision-suffix", rev,
                *build_network_args(cfg),
                "--service-account", SERVICE_ACCOUNT,
                "--set-env-vars",
                f"GCP_PROJECT_ID={GCP_PROJECT},BUILD_NUMBER={BUILD_NUMBER},COMMIT={COMMIT_HASH},AUTOMATION_NAME={folder}",
                "--quiet",
            ],
            f"gcloud run deploy {svc}",
        )
        ok(f"Cloud Run Service '{svc}' atualizado (revisão: {rev})")
        service_url = smoke_test(svc)
        manage_scheduler(svc, scheduler, service_url)
        return True
    except Exception as e:
        return _record_deploy_exception(folder, e)


def deploy_function(folder: str) -> bool:
    try:
        cfg = load_config(folder)
    except Exception as e:
        return _record_deploy_exception(folder, e)
    return deploy_job(folder, cfg) if is_cloud_run_job(cfg) else deploy_service(folder, cfg)


def main() -> int:
    header(f"DEPLOY CONTROLLER - Build #{BUILD_NUMBER} ({COMMIT_HASH} @ {BRANCH})")
    info(f"GitHub Actions : https://github.com/{GH_REPO}/actions")

    candidates, _ = get_changed_functions()
    if not candidates:
        info("Nenhuma função alterada neste commit.")
        return 0

    to_deploy, skipped, failed = [], [], []
    for folder in candidates:
        c = classify_function_candidate(folder)
        if c["decision"] == "DEPLOY":
            to_deploy.append(folder)
        elif c["decision"] == "SKIP_REMOVED_FOLDER":
            skipped.append(folder)
        else:
            failed.append(folder)
            FAILURE_DETAILS[folder] = {"stage": "DISCOVERY", "error": c["decision"], "message": c["reason"]}

    succeeded = [fn for fn in to_deploy if deploy_function(fn) or failed.append(fn)]

    header("RESUMO DO PIPELINE")
    if succeeded:
        ok(f"Sucesso ({len(succeeded)}): {', '.join(succeeded)}")
    if skipped:
        info(f"Ignoradas ({len(skipped)}): {', '.join(skipped)}")
    if failed:
        fail(f"Falhas ({len(failed)}): {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
