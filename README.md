# GCP Serverless Ingestion Framework

Estrutura em Python para execução e deploy contínuo de extratores de dados no Google Cloud Platform, utilizando Cloud Run Services, Cloud Run Jobs, Cloud Scheduler, Secret Manager e BigQuery.

## Arquitetura

```text
Git Push (main)
  └── GitHub Actions (.github/workflows/deploy.yml)
        └── deploy_controller.py
              ├── git diff HEAD^ HEAD (shared/ vs functions/<nome>/)
              ├── Validação estática do config.jsonc
              ├── Docker build & push (Artifact Registry)
              │
              ├── runtime: padrão (HTTP)
              │     ├── gcloud run deploy (Cloud Run Service + Gunicorn/Flask)
              │     ├── Smoke test (status.url e Ready=True)
              │     └── Sincronização do Cloud Scheduler (ENABLED / PAUSED)
              │
              └── runtime: cloud_run_job
                    └── gcloud run jobs deploy (Dockerfile.job + main.py)
```

Durante a execução de cada função:
1. `EnvLoader` identifica o ambiente (`DEV` local ou `PROD` no Cloud Run via `K_SERVICE`) e a origem do disparo (`SCHEDULED` via header `X-CloudScheduler` ou `MANUAL`).
2. `ReferenceDateHandler` extrai `reference_date` do corpo da requisição HTTP ou calcula o dia útil anterior (`D-1`).
3. `Logger` abre o contexto de execução, emite logs estruturados em JSON no `stdout` (Cloud Logging) e grava o resultado final na tabela `ops.extraction_runs` do BigQuery ao sair do bloco `with`.
4. `BigQueryWriter` cria a tabela de destino em `raw_ingestion` caso não exista (particionada por `DATE(_reference_date)` e clusterizada por `_source`), adiciona novas colunas via DDL quando o formato de entrada muda e insere os registros em lotes de 500 linhas com o mesmo `_run_id` gerado pelo `Logger`.

## Componentes

- `deploy_controller.py`: script de CI/CD que analisa os arquivos alterados no commit. Alterações em `shared/` disparam o deploy de todas as funções; alterações isoladas em `functions/<nome>/` atualizam apenas as pastas afetadas. Pastas removidas no commit são classificadas como `SKIP_REMOVED_FOLDER` para não interromper o pipeline.
- `shared/data/bigquery_writer.py`: cliente de escrita na camada Raw com suporte aos modos `APPEND` e `PARTITION_REPLACE` (remoção parametrizada da partição `_reference_date` antes da inserção).
- `shared/core/logger.py`: gerenciador de contexto que exige confirmação explícita de término (`success()`, `no_data_found()` ou `already_processed()`). Caso o bloco termine sem chamada explícita, registra `FINISH_LOG_NOT_CALLED`; em exceções não tratadas, captura o traceback e grava `EXCEPTION`.
- `shared/data/db_reader.py`: conector somente-leitura (`SELECT` e `WITH`) para MySQL e PostgreSQL via SQLAlchemy, buscando credenciais no Secret Manager.
- `shared/secrets_handler.py`: leitura de credenciais no GCP Secret Manager com cache em memória por instância.

## Funções de Exemplo (`functions/`)

- `hello_world`: template base de Cloud Run Service acionado via `POST /`.
- `extraction_function_b3_di_pre`: coleta a curva futura de juros DI x Pré (base 252) na API pública da B3 e grava os vértices em `raw_ingestion.di_pre_252`.
- `extraction_function_batch_job_example`: exemplo de Cloud Run Job (`runtime = "cloud_run_job"`) configurado com saída de rede por IP fixo (`Direct VPC Egress` via bloco `network`).

## Execução Local e Testes

Instale as dependências de desenvolvimento e execute a suíte de validação do controlador de deploy:

```bash
pip install -e ".[dev]"
pytest tests/ci -v
ruff check .
```

Para adicionar um novo extrator, copie `functions/hello_world/` para `functions/<nome_da_funcao>/`, ajuste os parâmetros em `config.jsonc` (`memory`, `timeout`, `scheduler`, `network`, `raw`) e implemente a coleta em `app.py` (Service) ou `main.py` (Job).
