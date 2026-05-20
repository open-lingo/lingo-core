# lingo-core — backend
#
# Quick targets:
#   make dev      uvicorn w/ SQLite forced; never touches AWS/DynamoDB
#   make test     pytest
#   make install  pip install -e .[dev]
#
# `make dev` overrides DB_BACKEND=sqlite at the command level so even a stray
# `.env` with dynamodb won't push you onto AWS during local work. AWS_* vars
# are unset for the same reason.

PORT ?= 8000

.PHONY: help dev test install clean

help:
	@echo "lingo-core backend"
	@echo ""
	@echo "  make dev      uvicorn on :$(PORT) — SQLite forced, no AWS"
	@echo "  make test     pytest"
	@echo "  make install  pip install -e .[dev]"
	@echo "  make clean    remove local.db and __pycache__"

dev:
	@echo ">> Forcing DB_BACKEND=sqlite, unsetting AWS_* env"
	@DB_BACKEND=sqlite \
	  AWS_ACCESS_KEY_ID= \
	  AWS_SECRET_ACCESS_KEY= \
	  AWS_SESSION_TOKEN= \
	  AWS_PROFILE= \
	  uvicorn app.main:app --reload --port $(PORT)

test:
	pytest

install:
	pip install -e ".[dev]"

clean:
	rm -f local.db
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
