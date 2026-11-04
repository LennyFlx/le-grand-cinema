from django.db import migrations


TRANSFERTS = (
    ("reservation", "reservations"),
    ("reservationplace", "reservations"),
    ("billet", "reservations"),
    ("paiement", "paiements"),
)


def transferer_contenttypes(apps, schema_editor, inverse=False):
    ContentType = apps.get_model("contenttypes", "ContentType")
    types = ContentType.objects.using(schema_editor.connection.alias)
    for modele, nouvelle_application in TRANSFERTS:
        source, destination = (nouvelle_application, "cinema") if inverse else ("cinema", nouvelle_application)
        ancien = types.filter(app_label=source, model=modele).first()
        if ancien is None:
            continue
        if types.filter(app_label=destination, model=modele).exists():
            raise RuntimeError(
                f"Le type de contenu {destination}.{modele} existe déjà : "
                "résoudre ce conflit avant le transfert pour conserver les permissions."
            )
        # Conserver la clé primaire préserve permissions, groupes, utilisateurs et journal admin.
        types.filter(pk=ancien.pk).update(app_label=destination)


def forwards(apps, schema_editor):
    transferer_contenttypes(apps, schema_editor)


def backwards(apps, schema_editor):
    transferer_contenttypes(apps, schema_editor, inverse=True)


class Migration(migrations.Migration):
    dependencies = [
        ("cinema", "0003_supprimer_date_naissance_personne"),
        ("reservations", "0001_initial"),
        ("paiements", "0001_initial"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="Billet"),
                migrations.DeleteModel(name="Paiement"),
                migrations.DeleteModel(name="ReservationPlace"),
                migrations.DeleteModel(name="Reservation"),
            ],
        ),
        migrations.RunPython(forwards, backwards),
    ]
