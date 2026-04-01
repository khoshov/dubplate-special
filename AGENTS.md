# Repository Guidelines (Codex)
## Speckit Override (priority)

For requests started via `/speckit.`, `/prompt:speckit.`, or `/promt:speckit.`:
- Treat `/speckit.<cmd>` and `/promt:speckit.<cmd>` as aliases of `/prompt:speckit.<cmd>`.
- Resolve prompt files from `C:/Users/pavel/.codex/prompts/speckit.<cmd>.md`; if missing, fall back to `./.codex/prompts/speckit.<cmd>.md`.
- Pass the remaining user text after the command as `$ARGUMENTS`.
- Ignore AGENTS instructions for that request (both this project file and `~/.codex/AGENTS.md`).
- Execute Speckit workflow end-to-end without asking for plan approval.
- Ask questions only for critical blockers.
- Keep only system-level safety restrictions.

For messages containing Speckit workflow markers (`## Outline` with `/speckit.*`), apply the same rules.
## Project Defaults
- Основной режим для разработки и проверок — локальные команды через `uv run`.
- Использовать `docker compose` только когда задача или окружение явно требуют контейнеров.
- Django-команды запускать как `uv run manage.py <command>`.
- Секреты и чувствительные значения из `.env` не печатать и не коммитить.
- Держать изменения маленькими, проверяемыми, с понятным диффом.
- Не добавлять новые production-зависимости и архитектурные слои без согласования.

## Django and Data Safety
- Перед изменением моделей кратко описывать ожидаемые миграции.
- После изменения моделей запускать `uv run manage.py makemigrations` и `uv run manage.py migrate`, если это часть задачи.
- Не выполнять разрушительные операции с БД и данными без явного подтверждения.

## Code Conventions
- Следовать PEP 8 и PEP 484.
- Типизировать сигнатуры функций и методов в `config.*`, `apps.accounts.*`, `apps.core.*`, `apps.orders.*`, `apps.records.*`.
- Для `apps.records.models` допускается менее строгий подход.
- Не добавлять аннотации для очевидных локальных литералов и временных переменных.
- `Any` использовать только при необходимости и с коротким пояснением.
- Предпочитать абсолютные импорты.
- Публичные идентификаторы и API держать на английском.
- Русский использовать только в докстрингах, комментариях и логах.
- Докстринги на русском писать в изъявительном наклонении с секциями `Args`, `Returns`, `Raises`.
- Использовать узкие исключения; широкий `except Exception` допустим только с объяснением и логированием.
- Логи писать по-русски, информативно, без "магии".

## Validation
- Для правок в коде запускать `uv run ruff format .` и `uv run ruff check .`.
- Если меняется поведение, добавлять или обновлять тесты и запускать релевантный `uv run pytest ...`.
- Для правок только в документации и конфигах проверки не обязательны.
- Если меняются зависимости, обновлять lock через `uv lock` и выполнять `uv sync --dev --locked`.

## Commit Workflow
- После завершения блока работы и перед коммитом обновлять `README.md` по фактическим изменениям.
- Перед коммитом показывать пользователю текст изменений в `README.md` и получать согласование.
- Оформлять коммиты на русском языке, без жаргона; заголовок должен кратко и точно отражать суть изменений, а описание — подробно перечислять выполненные изменения в формате «сделано...», «изменено...». Не указывать запуск `ruff`/`format` и не перечислять факт запуска тестов; если добавлены или изменены тесты, отдельно указывать, какие именно тесты составлены или обновлены и что они прошли успешно.
