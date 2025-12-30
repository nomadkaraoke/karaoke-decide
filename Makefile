.PHONY: install dev test test-unit test-backend test-e2e lint format clean help

# Default target
help:
	@echo "Nomad Karaoke Decide - Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install      Install dependencies"
	@echo ""
	@echo "Development:"
	@echo "  make dev          Start development API server"
	@echo "  make cli          Run CLI in development mode"
	@echo ""
	@echo "Testing:"
	@echo "  make test         Run all tests"
	@echo "  make test-unit    Run unit tests only"
	@echo "  make test-backend Run backend tests only"
	@echo "  make test-e2e     Run e2e tests with emulators"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint         Run linters"
	@echo "  make format       Format code"
	@echo ""
	@echo "Utilities:"
	@echo "  make clean        Clean build artifacts"
	@echo "  make emulators    Start GCP emulators"
	@echo "  make stop-emulators Stop GCP emulators"

# Setup
install:
	poetry install

# Development
dev:
	poetry run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

cli:
	poetry run karaoke-decide $(ARGS)

# Testing
test: test-unit test-backend

test-unit:
	poetry run pytest tests/unit -v --tb=short

test-backend:
	poetry run pytest backend/tests -v --tb=short --ignore=backend/tests/emulator

test-e2e:
	@echo "Starting emulators..."
	@./scripts/start-emulators.sh
	@echo "Running e2e tests..."
	poetry run pytest backend/tests/emulator -v --tb=short || (./scripts/stop-emulators.sh && exit 1)
	@./scripts/stop-emulators.sh
	@echo "Emulators stopped."

# Code Quality
lint:
	poetry run ruff check .
	poetry run mypy karaoke_decide backend --ignore-missing-imports

format:
	poetry run ruff format .
	poetry run ruff check --fix .

# Utilities
clean:
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache
	rm -rf build dist *.egg-info
	rm -rf .coverage htmlcov
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

emulators:
	./scripts/start-emulators.sh

stop-emulators:
	./scripts/stop-emulators.sh
