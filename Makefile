.PHONY: install chat feishu menubar menubar-app mac-dmg env-up env-restart env-down env-status dev-up dev-restart dev-down dev-status test test-cov lint format bump-version release-prep release-tag version-check build package-check install-check check verify precommit-install

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
	@uv run python scripts/build_macos_dmg.py --adapter feishu --out-dir dist

env-up:
	@bash scripts/hermit-envctl.sh $(if $(ENV),$(ENV),dev) up

env-restart:
	@bash scripts/hermit-envctl.sh $(if $(ENV),$(ENV),dev) restart

env-down:
	@bash scripts/hermit-envctl.sh $(if $(ENV),$(ENV),dev) down

env-status:
	@bash scripts/hermit-envctl.sh $(if $(ENV),$(ENV),dev) status

dev-up:
	@bash scripts/hermit-envctl.sh dev up

dev-restart:
	@bash scripts/hermit-envctl.sh dev restart

dev-down:
	@bash scripts/hermit-envctl.sh dev down

dev-status:
	@bash scripts/hermit-envctl.sh dev status

test:
	@uv run pytest -q

test-cov:
	@uv run pytest --cov=hermit --cov-report=term-missing

lint:
	@uv run ruff check .

format:
	@uv run ruff format .

bump-version:
	@test -n "$(VERSION)" || (echo "Usage: make bump-version VERSION=x.y.z" && exit 1)
	@uv run python scripts/bump_version.py "$(VERSION)" --update-lock

release-prep:
	@test -n "$(VERSION)" || (echo "Usage: make release-prep VERSION=x.y.z" && exit 1)
	@$(MAKE) bump-version VERSION=$(VERSION)
	@$(MAKE) verify
	@echo "Release prep complete for v$(VERSION). Next: commit changes, push, then tag with: git tag -a v$(VERSION) -m 'Release v$(VERSION)'"

release-tag:
	@test -n "$(VERSION)" || (echo "Usage: make release-tag VERSION=x.y.z" && exit 1)
	@git tag -a "v$(VERSION)" -m "Release v$(VERSION)"
	@echo "Created tag v$(VERSION). Push with: git push origin v$(VERSION)"

version-check:
	@uv run python scripts/check_release_version.py

build:
	@uv build --out-dir dist --clear

install-check: build
	@rm -rf .pkg-venv
	@uv venv .pkg-venv
	@uv pip install --python .pkg-venv/bin/python dist/*.whl
	@.pkg-venv/bin/hermit --help >/dev/null
	@.pkg-venv/bin/hermit setup --help >/dev/null
	@.pkg-venv/bin/hermit chat --help >/dev/null
	@.pkg-venv/bin/hermit config --help >/dev/null
	@rm -rf .pkg-venv

package-check: build
	@uv run python scripts/check_release_version.py --dist-dir dist

check:
	@$(MAKE) lint
	@$(MAKE) test

verify:
	@$(MAKE) version-check
	@$(MAKE) lint
	@$(MAKE) test
	@$(MAKE) package-check
	@$(MAKE) install-check

precommit-install:
	@uv run pre-commit install --hook-type pre-commit --hook-type pre-push
