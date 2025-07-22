import logging
import re

import requests

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


class SMSService:
    """
    Сервис для отправки SMS через различных провайдеров
    """

    def __init__(self):
        self.api_key = getattr(settings, "SMS_API_KEY", None)
        self.provider = getattr(settings, "SMS_PROVIDER", "sms_ru")
        self.test_mode = getattr(settings, "SMS_TEST_MODE", True)

    def normalize_phone_number(self, phone):
        """
        Нормализует номер телефона для российских номеров
        """
        # Убираем все нецифровые символы
        phone = re.sub(r"\D", "", phone)

        # Российские номера
        if phone.startswith("8") and len(phone) == 11:
            phone = "7" + phone[1:]
        elif phone.startswith("7") and len(phone) == 11:
            pass  # Уже в правильном формате
        elif len(phone) == 10:
            phone = "7" + phone
        else:
            raise ValueError("Неверный формат номера телефона")

        return phone

    def validate_russian_phone(self, phone):
        """
        Валидирует российский номер телефона
        """
        try:
            normalized = self.normalize_phone_number(phone)
            # Проверяем, что это российский номер
            if not normalized.startswith("7"):
                return False, "Поддерживаются только российские номера"

            # Проверяем длину
            if len(normalized) != 11:
                return False, "Неверная длина номера"

            # Проверяем код оператора (начинается с 79)
            if not normalized.startswith("79"):
                return False, "Неверный код российского оператора"

            return True, normalized
        except ValueError as e:
            return False, str(e)

    def can_send_sms(self, phone_number):
        """
        Проверяет, можно ли отправить SMS (защита от спама)
        """
        cache_key = f"sms_cooldown_{phone_number}"
        last_sent = cache.get(cache_key)

        if last_sent:
            return False, "SMS уже отправлена. Повторить можно через 1 минуту"

        return True, ""

    def send_sms_via_sms_ru(self, phone, message):
        """
        Отправка SMS через SMS.ru
        """
        if not self.api_key:
            logger.error("SMS.ru API key not configured")
            return False, "SMS сервис не настроен"

        url = "https://sms.ru/sms/send"
        params = {"api_id": self.api_key, "to": phone, "msg": message, "json": 1}

        try:
            response = requests.get(url, params=params, timeout=10)
            data = response.json()

            if data.get("status") == "OK":
                return True, "SMS отправлена"
            else:
                error_msg = data.get("status_text", "Неизвестная ошибка")
                logger.error(f"SMS.ru error: {error_msg}")
                return False, f"Ошибка отправки SMS: {error_msg}"

        except Exception as e:
            logger.error(f"SMS sending error: {str(e)}")
            return False, "Ошибка отправки SMS"

    def send_test_sms(self, phone, message):
        """
        Тестовый режим - логирует SMS вместо отправки
        """
        logger.info(f"TEST SMS to {phone}: {message}")
        print(f"TEST SMS to {phone}: {message}")
        return True, "SMS отправлена (тестовый режим)"

    def send_verification_code(self, phone_number, code):
        """
        Отправляет код верификации
        """
        # Валидируем номер
        is_valid, result = self.validate_russian_phone(phone_number)
        if not is_valid:
            return False, result

        normalized_phone = result

        # Проверяем лимиты
        can_send, error_msg = self.can_send_sms(normalized_phone)
        if not can_send:
            return False, error_msg

        # Формируем сообщение
        message = f"Ваш код подтверждения: {code}. Код действителен 5 минут."

        # Отправляем SMS
        if self.test_mode:
            success, msg = self.send_test_sms(normalized_phone, message)
        else:
            if self.provider == "sms_ru":
                success, msg = self.send_sms_via_sms_ru(normalized_phone, message)
            else:
                return False, "Неподдерживаемый SMS провайдер"

        if success:
            # Устанавливаем кулдаун на 1 минуту
            cache.set(f"sms_cooldown_{normalized_phone}", timezone.now(), 60)

        return success, msg


# Глобальный экземпляр сервиса
sms_service = SMSService()
