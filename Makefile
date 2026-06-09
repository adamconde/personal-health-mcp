# Convenience targets mirroring CI. Use a local virtualenv's tools on PATH,
# or prefix with your interpreter (e.g. `python -m`).
.PHONY: install lint type test cov check run docker-build compose-up

install:
	pip install -e ".[dev]"

lint:
	ruff check src tests

type:
	mypy

test:
	pytest

cov:
	pytest --cov=personal_health_mcp --cov-report=term-missing

check: lint type cov

run:
	uvicorn personal_health_mcp.server:create_default_app --factory --host 0.0.0.0 --port 8000

docker-build:
	docker build -f deploy/Dockerfile -t personal-health-mcp:local .

compose-up:
	docker compose -f deploy/docker-compose.yml -f deploy/compose.caddy.yml up -d
