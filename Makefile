UV := docker compose run -u $(USERID):$(GROUPID) --rm django uv
PYTHON := $(UV) run

collectstatic:
	$(PYTHON) manage.py collectstatic --noi -c

startapp:
	$(PYTHON) manage.py startapp ${app}

makemigrations:
	$(PYTHON) manage.py makemigrations ${app}

migrate:
	$(PYTHON) manage.py migrate ${app}

createsuperuser:
	$(PYTHON) manage.py createsuperuser

shell:
	$(PYTHON) manage.py shell_plus

reset_db:
	$(PYTHON) manage.py reset_db

format:
	uvx ruff check --fix apps config && uvx ruff check --select I --fix apps config && uvx ruff format apps config

list_packages:
	$(UV) pip list

test:
	$(PYTHON) pytest -v
