FROM python:3.13-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1

# 1. Установка системных зависимостей + uv

RUN apt-get update && apt-get install -y curl

WORKDIR /app

# 2. Копируем только файлы зависимостей
COPY pyproject.toml uv.lock ./

## 3. Установка зависимостей через uv (прямо из pyproject.toml)
RUN uv sync --locked


# 4. Копируем весь код
COPY . .


# 5. Запускаем сервер django

CMD ["uv","run", "manage.py", "runserver", "0.0.0.0:8000"]

#
#
#PYTHON := docker-compose run -u $(USERID):$(GROUPID) --rm django python
#CELERY := docker-compose run -u $(USERID):$(GROUPID) --rm celery python
#NODE := docker-compose run -u $(USERID):$(GROUPID) --rm node
#NPM := $(NODE) npm
#
#up:
#	docker-compose up
#
#down:
#	docker-compose down
#
#build:
#	docker-compose build
#
#build-no-cache:
#	docker-compose build --no-cacheFROM python:3.9
#
#ENV PYTHONUNBUFFERED=1
#
#WORKDIR /usr/src/app/
#
##COPY requirements.txt .
#
#RUN pip install -U pip
#RUN pip install -r requirements.txt
#
#COPY . `.`
#
#collectstatic:
#	$(PYTHON) manage.py collectstatic --noi -c
#
#startapp:
#	$(PYTHON) manage.py startapp ${app}
#
#makemigrations:
#	$(PYTHON) manage.py makemigrations ${app}
#
#migrate:
#	$(PYTHON) manage.py migrate ${app}
#
#createsuperuser:
#	$(PYTHON) manage.py createsuperuser
#
#shell:
#	$(PYTHON) manage.py shell_plus
#
#celery:
#	$(CELERY) manage.py shell_plus
#
#reset_db:
#	$(PYTHON) manage.py reset_db
#
#npm_install:
#	$(NPM) i
#
#npm_list:
#	$(NPM) ls
#
#npm_root:
#	$(NPM) root -g
#
#npm_install_dev:
#	$(NPM) i ${package} --save-dev
#
#node_shell:
#	$(NODE) node
#
#update:
#	$(NPM) update
#
#


