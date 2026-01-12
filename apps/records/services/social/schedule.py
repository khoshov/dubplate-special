from __future__ import annotations

from datetime import datetime


def build_even_schedule(start_at: datetime, end_at: datetime, n: int) -> list[datetime]:
    """
    Равномерно распределяет n дат/времени между start_at и end_at, включая границы.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if n == 1:
        return [start_at]

    # Шаг в виде timedelta с дробной частью, чтобы сохранить равномерность.
    step = (end_at - start_at) / (n - 1)
    return [start_at + step * i for i in range(n)]
