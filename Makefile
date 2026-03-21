.PHONY: help install dev lint test run docker-up docker-down

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install production dependencies
	pip install -e .

dev: ## Install with dev dependencies
	pip install -e ".[dev]"
	pre-commit install

lint: ## Run linter and type checker
	ruff check .
	ruff format --check .
	mypy app/

test: ## Run unit tests
	pytest tests/unit/ -v --cov=app --cov-report=term-missing

test-integration: ## Run

```makefile
 integration tests (requires docker-compose services)
	pytest tests/integration/ -v --cov=app --cov-report=term-missing

test-all: ## Run all tests
	pytest tests/ -v --cov=app --cov-report=term-missing

test-eval: ## Run RAG evaluation tests
	pytest tests/evaluation/ -v

run: ## Start the dev server
	uvicorn app.main:create_app --factory --reload --host 0.0.0.0 --port 8001

# Add to existing Makefile:

# ── Docker ─────────────────────────────────────────────
docker-build: ## Build Docker images
	docker build -t quillflow:latest -f docker/Dockerfile .
	docker build -t quillflow-worker:latest -f docker/Dockerfile.worker .

docker-up: ## Start all services (dev environment)
	docker compose -f docker/docker-compose.yml up -d

docker-down: ## Stop all services
	docker compose -f docker/docker-compose.yml down

docker-down-clean: ## Stop all services and delete data
	docker compose -f docker/docker-compose.yml down -v

docker-logs: ## Follow API logs
	docker compose -f docker/docker-compose.yml logs -f api

docker-logs-worker: ## Follow worker logs
	docker compose -f docker/docker-compose.yml logs -f worker

docker-ps: ## Show running containers
	docker compose -f docker/docker-compose.yml ps

docker-restart: ## Restart API and worker
	docker compose -f docker/docker-compose.yml restart api worker

# ── Database (via Docker) ──────────────────────────────
db-shell: ## Open psql shell
	docker compose -f docker/docker-compose.yml exec postgres psql -U quillflow

# ── Kubernetes ─────────────────────────────────────────
helm-install: ## Install QuillFlow to Kubernetes
	helm install quillflow deploy/helm/quillflow \
		--namespace quillflow \
		--create-namespace

helm-upgrade: ## Upgrade QuillFlow deployment
	helm upgrade quillflow deploy/helm/quillflow \
		--namespace quillflow

helm-uninstall: ## Remove QuillFlow from Kubernetes
	helm uninstall quillflow --namespace quillflow

helm-template: ## Render Helm templates (dry run)
	helm template quillflow deploy/helm/quillflow
worker: ## Start the background worker
	arq app.workers.settings.WorkerSettings

worker-dev: ## Start worker with auto-reload (dev only)
	watchfiles "arq app.workers.settings.WorkerSettings" --filter python

db-migrate: ## Create a new migration
	alembic revision --autogenerate -m "$(msg)"

db-upgrade: ## Apply all pending migrations
	alembic upgrade head

db-downgrade: ## Rollback last migration
	alembic downgrade -1

db-reset: ## Drop and recreate all tables (DEV ONLY)
	alembic downgrade base
	alembic upgrade head

eval: ## Run evaluation suite
	python -m scripts.run_evaluation

eval-strict: ## Run evaluation suite (fail on any regression)
	python -m scripts.run_evaluation --strict

eval-report: ## Run evaluation and save JSON report
	python -m scripts.run_evaluation --output eval_report.json --strict