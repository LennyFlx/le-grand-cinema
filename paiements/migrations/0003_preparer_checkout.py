from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("paiements", "0002_renommer_tables"),
    ]

    operations = [
        migrations.AlterField(
            model_name="paiement",
            name="stripe_session_id",
            field=models.CharField(blank=True, max_length=255, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="paiement",
            name="date_expiration",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="paiement",
            name="url_retour",
            field=models.URLField(blank=True, max_length=500),
        ),
    ]
