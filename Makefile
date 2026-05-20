# lingo-core — backend
#
# Quick targets:
#   make run      uvicorn w/ SQLite forced; never touches AWS/DynamoDB
#   make dev      alias for `make run`
#   make test     pytest
#   make install  pip install -e .[dev]
#
# `make run` overrides DB_BACKEND=sqlite at the command level so even a stray
# `.env` with dynamodb won't push you onto AWS during local work. AWS_* vars
# are unset for the same reason.

PORT ?= 8000

.PHONY: help run dev test install clean

help:
	@echo "lingo-core backend"
	@echo ""
	@echo "  make run      uvicorn on :$(PORT) — SQLite forced, no AWS"
	@echo "  make dev      alias for run"
	@echo "  make test     pytest"
	@echo "  make install  pip install -e .[dev]"
	@echo "  make clean    remove local.db and __pycache__"

run:
	@echo ">> Forcing DB_BACKEND=sqlite, unsetting AWS_* env"
	@DB_BACKEND=sqlite \
	  AWS_ACCESS_KEY_ID= \
	  AWS_SECRET_ACCESS_KEY= \
	  AWS_SESSION_TOKEN= \
	  AWS_PROFILE= \
	  uvicorn app.main:app --reload --port $(PORT)

dev: run

test:
	pytest

install:
	pip install -e ".[dev]"

clean:
	rm -f local.db
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
