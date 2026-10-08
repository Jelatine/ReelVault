.PHONY: install dev backend frontend test lint build docker package clean

VERSION ?= $(shell cd backend && uv run --quiet python -c "import reelvault; print(reelvault.__version__)" 2>/dev/null)

install:
	cd backend && uv sync
	cd frontend && npm ci

# Backend on :8080 and Vite dev server (proxying /api) on :5173
dev:
	@trap 'kill 0' INT; \
	(cd backend && REELVAULT_DATA_DIR=../data uv run python -m reelvault) & \
	(cd frontend && npm run dev) & \
	wait

test:
	cd backend && uv run pytest
	cd frontend && npm test

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy reelvault
	cd frontend && npm run lint && npm run typecheck

build:
	cd frontend && npm run build
	rm -rf backend/reelvault/static && cp -r frontend/dist backend/reelvault/static

docker:
	docker build -t reelvault:dev .

# Release archive for Ubuntu installs: dist/reelvault-<version>.tar.gz
package: build
	rm -rf dist/reelvault-$(VERSION) && mkdir -p dist/reelvault-$(VERSION)/backend
	cp -r backend/reelvault backend/pyproject.toml backend/uv.lock backend/.python-version dist/reelvault-$(VERSION)/backend/
	cp -r deploy README.md LICENSE docker-compose.yml dist/reelvault-$(VERSION)/
	find dist/reelvault-$(VERSION) -name __pycache__ -type d -prune -exec rm -rf {} +
	find dist/reelvault-$(VERSION) -name '._*' -delete
	COPYFILE_DISABLE=1 tar -C dist -czf dist/reelvault-$(VERSION).tar.gz reelvault-$(VERSION)
	cd dist && shasum -a 256 reelvault-$(VERSION).tar.gz > reelvault-$(VERSION).tar.gz.sha256

clean:
	rm -rf dist frontend/dist backend/reelvault/static
