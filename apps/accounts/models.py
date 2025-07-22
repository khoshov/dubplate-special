import random
import string

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    email = models.EmailField(_("email address"), unique=True)
    phone = models.CharField(
        _("phone number"), max_length=20, blank=True, null=True, db_index=True
    )
    birth_date = models.DateField(_("birth date"), null=True, blank=True)
    address = models.TextField(_("address"), blank=True)
    city = models.CharField(_("city"), max_length=100, blank=True)
    country = models.CharField(_("country"), max_length=100, blank=True)
    postal_code = models.CharField(_("postal code"), max_length=20, blank=True)

    # Store-specific fields
    # is_premium_customer = models.BooleanField(_('premium customer'), default=False)
    # loyalty_points = models.PositiveIntegerField(_('loyalty points'), default=0)
    # preferred_genres = models.CharField(_('preferred genres'), max_length=200, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        verbose_name = _("User")
        verbose_name_plural = _("Users")

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        # Проверяем уникальность номера телефона, если он указан
        if self.phone:
            existing_user = (
                User.objects.filter(phone=self.phone).exclude(pk=self.pk).first()
            )
            if existing_user:
                raise ValueError(
                    f"Пользователь с номером телефона {self.phone} уже существует"
                )

        # Если телефон пустой, устанавливаем None вместо пустой строки
        if not self.phone:
            self.phone = None

        super().save(*args, **kwargs)


class SMSVerification(models.Model):
    phone_number = models.CharField(
        max_length=20,
        verbose_name=_("Phone number"),
        help_text=_("Phone number in international format"),
    )
    code = models.CharField(max_length=6, verbose_name=_("Verification code"))
    is_verified = models.BooleanField(default=False, verbose_name=_("Is verified"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created at"))
    expires_at = models.DateTimeField(verbose_name=_("Expires at"))
    attempts = models.PositiveIntegerField(default=0, verbose_name=_("Attempts count"))

    class Meta:
        verbose_name = _("SMS Verification")
        verbose_name_plural = _("SMS Verifications")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.phone_number} - {self.code}"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.generate_code()
        if not self.expires_at:
            self.expires_at = timezone.now() + timezone.timedelta(minutes=5)
        super().save(*args, **kwargs)

    @staticmethod
    def generate_code():
        """Генерирует 6-значный код"""
        return "".join(random.choices(string.digits, k=6))

    def is_expired(self):
        """Проверяет, истек ли код"""
        return timezone.now() > self.expires_at

    def can_resend(self):
        """Проверяет, можно ли отправить код повторно (через 1 минуту)"""
        return timezone.now() > self.created_at + timezone.timedelta(minutes=1)

    @classmethod
    def create_verification(cls, phone_number):
        """Создает новую верификацию или обновляет существующую"""
        # Деактивируем старые коды для этого номера
        cls.objects.filter(phone_number=phone_number, is_verified=False).update(
            is_verified=True
        )  # Помечаем как использованные

        # Создаем новый код
        return cls.objects.create(phone_number=phone_number)

    def verify_code(self, entered_code):
        """Верифицирует введенный код"""
        self.attempts += 1
        self.save(update_fields=["attempts"])

        if self.attempts > 3:
            return False, "Превышено количество попыток"

        if self.is_expired():
            return False, "Код истек"

        if self.code != entered_code:
            return False, "Неверный код"

        self.is_verified = True
        self.save(update_fields=["is_verified"])
        return True, "Код подтвержден"
