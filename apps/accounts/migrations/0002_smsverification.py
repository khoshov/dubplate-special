# Generated manually for SMS verification

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SMSVerification",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "phone_number",
                    models.CharField(
                        help_text="Phone number in international format",
                        max_length=20,
                        verbose_name="Phone number",
                    ),
                ),
                (
                    "code",
                    models.CharField(max_length=6, verbose_name="Verification code"),
                ),
                (
                    "is_verified",
                    models.BooleanField(default=False, verbose_name="Is verified"),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created at"),
                ),
                ("expires_at", models.DateTimeField(verbose_name="Expires at")),
                (
                    "attempts",
                    models.PositiveIntegerField(
                        default=0, verbose_name="Attempts count"
                    ),
                ),
            ],
            options={
                "verbose_name": "SMS Verification",
                "verbose_name_plural": "SMS Verifications",
                "ordering": ["-created_at"],
            },
        ),
    ]
