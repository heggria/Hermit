.PHONY: sync sync-macos install chat feishu menubar menubar-app mac-dmg env-up env-restart env-down env-status env-watch dev-up dev-restart dev-down dev-status dev-watch test test-quick test-cov coverage-diff lint format typecheck docs-build security sbom bump-version release-prep release-tag version-check lock-check build package-check install-check docker-smoke check verify verify-release precommit-install

UV_CACHE_DIR ?= .uv-cache
PYTEST_PARALLEL_FLAGS ?= -n auto
SYNC_GROUP_FLAGS ?= --group dev --group typecheck --group docs --group security --group release
SYNC_MACOS_FLAGS ?= $(SYNC_GROUP_FLAGS) --extra macos
DIFF_RANGE ?= origin/main...HEAD
SBOM_PATH ?= dist/hermit.sbom.json

sync:
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv sync $(SYNC_GROUP_FLAGS)

sync-macos:
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv sync $(SYNC_MACOS_FLAGS)

install:
	@bash install.sh

chat:
	@hermit chat

feishu:
	@hermit serve --adapter feishu

menubar:
	@hermit-menubar --adapter feishu

menubar-app:
	@hermit-menubar-install-app --adapter feishu --open

mac-dmg:
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/build_macos_dmg.py --adapter feishu --out-dir dist

env-up:
	@bash scripts/hermit-envctl.sh $(if $(ENV),$(ENV),dev) up

env-restart:
	@bash scripts/hermit-envctl.sh $(if $(ENV),$(ENV),dev) restart

env-down:
	@bash scripts/hermit-envctl.sh $(if $(ENV),$(ENV),dev) down

env-status:
	@bash scripts/hermit-envctl.sh $(if $(ENV),$(ENV),dev) status

env-watch:
	@bash scripts/hermit-watch.sh $(if $(ENV),$(ENV),dev)

dev-up:
	@bash scripts/hermit-envctl.sh dev up

dev-restart:
	@bash scripts/hermit-envctl.sh dev restart

dev-down:
	@bash scripts/hermit-envctl.sh dev down

dev-status:
	@bash scripts/hermit-envctl.sh dev status

dev-watch:
	@bash scripts/hermit-watch.sh dev

test:
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pytest $(PYTEST_PARALLEL_FLAGS) -q

test-quick:
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pytest $(PYTEST_PARALLEL_FLAGS) -q tests/test_provider_input_compiler.py tests/test_kernel_store_tasks_support.py tests/test_cli.py tests/test_docs_alignment.py

test-cov:
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pytest $(PYTEST_PARALLEL_FLAGS) --cov=hermit --cov-report=term-missing --cov-report=xml

coverage-diff: test-cov
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run diff-cover coverage.xml --compare-branch $(DIFF_RANGE) --fail-under=95

lint:
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run ruff check .

format:
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run ruff format .

typecheck:
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --group typecheck pyright

docs-build:
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/check_install_docs_sync.py
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run mkdocs build --strict --clean

security:
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pip-audit

sbom:
	@mkdir -p dist
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run cyclonedx-py environment --spec-version 1.6 --output-format JSON --output-file $(SBOM_PATH)

bump-version:
	@test -n "$(VERSION)" || (echo "Usage: make bump-version VERSION=x.y.z" && exit 1)
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/bump_version.py "$(VERSION)" --update-lock

release-prep:
	@test -n "$(VERSION)" || (echo "Usage: make release-prep VERSION=x.y.z" && exit 1)
	@$(MAKE) bump-version VERSION=$(VERSION)
	@$(MAKE) verify-release
	@echo "Release prep complete for v$(VERSION). Next: commit changes, push, then tag with: git tag -a v$(VERSION) -m 'Release v$(VERSION)'"

release-tag:
	@test -n "$(VERSION)" || (echo "Usage: make release-tag VERSION=x.y.z" && exit 1)
	@git tag -a "v$(VERSION)" -m "Release v$(VERSION)"
	@echo "Created tag v$(VERSION). Push with: git push origin v$(VERSION)"

version-check:
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/check_release_version.py

lock-check:
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv lock --check

build:
	@mkdir -p dist
	@find dist -mindepth 1 -maxdepth 1 -exec rm -rf {} +
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv build --out-dir dist

install-check: build
	@rm -rf .pkg-venv
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv venv --python 3.13 .pkg-venv
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv pip install --python .pkg-venv/bin/python dist/*.whl
	@.pkg-venv/bin/hermit --help >/dev/null
	@.pkg-venv/bin/hermit setup --help >/dev/null
	@.pkg-venv/bin/hermit chat --help >/dev/null
	@.pkg-venv/bin/hermit config --help >/dev/null
	@rm -rf .pkg-venv

package-check: build
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python scripts/check_release_version.py --dist-dir dist
	@UV_CACHE_DIR=$(UV_CACHE_DIR) uv run twine check dist/*

docker-smoke:
	@docker build -t hermit:local .
	@docker run --rm hermit:local --help >/dev/null
	@docker run --rm hermit:local config --help >/dev/null

check:
	@$(MAKE) lint
	@$(MAKE) typecheck
	@$(MAKE) test

verify:
	@$(MAKE) sync
	@$(MAKE) lock-check
	@$(MAKE) version-check
	@$(MAKE) lint
	@$(MAKE) typecheck
	@$(MAKE) test
	@$(MAKE) test-cov
	@$(MAKE) docs-build
	@$(MAKE) security
	@$(MAKE) package-check
	@$(MAKE) install-check
	@$(MAKE) sbom

verify-release:
	@$(MAKE) verify
	@$(MAKE) docker-smoke

precommit-install:
	@chmod +x scripts/git-hooks/pre-commit scripts/git-hooks/pre-push
	@git config --local core.hooksPath scripts/git-hooks
	@printf 'Installed git hooks from %s/scripts/git-hooks\n' "$$(git rev-parse --show-toplevel)"
