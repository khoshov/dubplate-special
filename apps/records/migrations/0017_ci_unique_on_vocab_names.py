from django.db import migrations, models
from django.db.models.functions import Lower


class Migration(migrations.Migration):
    dependencies = [
        ("records", "0016_squash_not_specified_ci"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="genre",
            constraint=models.UniqueConstraint(
                Lower("name"),
                name="genre_name_ci_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="style",
            constraint=models.UniqueConstraint(
                Lower("name"),
                name="style_name_ci_unique",
            ),
        ),
    ]
