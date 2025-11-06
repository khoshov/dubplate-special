# tests/providers/redeye/test_helpers.py
"""
Быстрые модульные тесты для helpers провайдера Redeye.

Покрываем чистые функции без сторонних зависимостей:
- text_join
- page_text
- normalize_abs_url
- validate_redeye_product_url
- parse_expected_date_parts_from_text
- format_expected_date_ru
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from records.services.providers.redeye import helpers as h


# ---------------------- text_join / page_text ----------------------


@pytest.mark.unit
@pytest.mark.redeye
def test_text_join__collects_and_normalizes_spaces() -> None:
    """Метод корректно собирает текст узла и нормализует пробелы."""
    html = """
        <div>
            Привет,
            <span>мир</span>
            <span>
                !
            </span>
        </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    node = soup.div
    assert h.text_join(node) == "Привет, мир !"


@pytest.mark.unit
@pytest.mark.redeye
def test_page_text__collects_full_page_text() -> None:
    """Метод возвращает читабельный текст всей страницы; допускаем любые переводы строк внутри — нормализуем пробелы."""
    html = """
        <html><body>
          <h1>Title</h1>
          <p>Line 1</p>
          <p>Line
             2</p>
        </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    text = h.page_text(soup)

    # --- добавлено: нормализация пробелов/переводов строки для устойчивого сравнения ---
    normalized = " ".join(text.split())
    assert normalized == "Title Line 1 Line 2"


# ---------------------- normalize_abs_url ----------------------


@pytest.mark.unit
@pytest.mark.redeye
def test_normalize_abs_url__absolute_kept() -> None:
    """Метод сохраняет абсолютный http/https URL без изменений."""
    assert (
        h.normalize_abs_url("https://www.redeyerecords.co.uk/vinyl/123")
        == "https://www.redeyerecords.co.uk/vinyl/123"
    )
    assert h.normalize_abs_url("http://example.com/x") == "http://example.com/x"


@pytest.mark.unit
@pytest.mark.redeye
def test_normalize_abs_url__root_relative() -> None:
    """Метод переводит корневой путь '/path' в абсолютный URL провайдера."""
    url = h.normalize_abs_url("/vinyl/187440-lqldisc01-dj-balaton-back-to-the-mood")
    assert url.startswith("http")
    assert "redeyerecords.co.uk" in url
    assert "/vinyl/187440-lqldisc01-dj-balaton-back-to-the-mood" in url


@pytest.mark.unit
@pytest.mark.redeye
def test_normalize_abs_url__scheme_relative() -> None:
    """Метод корректно обрабатывает схемо-независимый URL вида //host/path."""
    url = h.normalize_abs_url("//www.redeyerecords.co.uk/vinyl/187440")
    assert url.startswith("http")
    assert url.endswith("/vinyl/187440")


# ---------------------- validate_redeye_product_url ----------------------


@pytest.mark.unit
@pytest.mark.redeye
def test_validate_redeye_product_url__accepts_correct() -> None:
    """Валидатор НЕ бросает исключение и возвращает None для корректного URL."""
    result = h.validate_redeye_product_url(
        "https://www.redeyerecords.co.uk/vinyl/187440-lqldisc01-dj-balaton-back-to-the-mood"
    )
    assert result is None  # важен факт отсутствия исключения


@pytest.mark.unit
@pytest.mark.redeye
def test_validate_redeye_product_url__rejects_wrong_host_and_scheme() -> None:
    """Валидатор бросает ValueError при чужом домене или неподдерживаемой схеме."""
    with pytest.raises(ValueError):
        h.validate_redeye_product_url("https://example.com/vinyl/187440")
    with pytest.raises(ValueError):
        h.validate_redeye_product_url("ftp://www.redeyerecords.co.uk/vinyl/187440")
    with pytest.raises(ValueError):
        h.validate_redeye_product_url("")  # пустое значение тоже ошибка


# ---------------------- parse_expected_date_parts_from_text / format_expected_date_ru ----------------------


@pytest.mark.unit
@pytest.mark.redeye
def test_parse_expected_date_and_format_ru__happy_path() -> None:
    """
    Разбираем текст вида 'Expected 24 Oct 2025' и форматируем по-русски:
    '24 октября 2025 года'.
    """
    parts = h.parse_expected_date_parts_from_text("Expected 24 Oct 2025")
    assert parts is not None
    year, month, day = parts
    assert h.format_expected_date_ru(year, month, day) == "24 октября 2025 года"


@pytest.mark.unit
@pytest.mark.redeye
def test_parse_expected_date_parts_from_text__no_match() -> None:
    """Отсутствие паттерна возвращает кортеж None-значений."""
    parts = h.parse_expected_date_parts_from_text("No expected date here")
    assert parts == (None, None, None)


@pytest.mark.unit
@pytest.mark.redeye
def test_format_expected_date_ru__invalid_month() -> None:
    """Неверный номер месяца приводит к None."""
    assert h.format_expected_date_ru(2025, 0, 24) is None
    assert h.format_expected_date_ru(2025, 13, 24) is None
