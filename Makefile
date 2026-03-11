.PHONY: install chat feishu menubar menubar-app test test-cov lint format check precommit-install

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

test:
	@uv run pytest -q

test-cov:
	@uv run pytest --cov=hermit --cov-report=term-missing

lint:
	@uv run ruff check .

format:
	@uv run ruff format .

check:
	@$(MAKE) lint
	@$(MAKE) test

precommit-install:
	@uv run pre-commit install
