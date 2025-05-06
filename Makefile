ruff:
	uvx ruff check --fix --unsafe-fixes apps && uvx ruff format apps && uvx ruff check --select I --fix apps