import runpy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType

from django.conf import settings
from django.test import SimpleTestCase


class MediaDevelopmentTests(SimpleTestCase):
    def urlconf(self):
        configuration = ModuleType("test_media_urls")
        configuration.urlpatterns = runpy.run_path(
            str(settings.BASE_DIR / "config" / "urls.py")
        )["urlpatterns"]
        return configuration

    def test_media_servi_en_developpement(self):
        with TemporaryDirectory() as dossier:
            affiche = Path(dossier) / "affiches" / "test.webp"
            affiche.parent.mkdir()
            affiche.write_bytes(b"image-de-test")
            with (
                self.settings(DEBUG=True, MEDIA_ROOT=dossier, MEDIA_URL="/media/"),
                self.settings(ROOT_URLCONF=self.urlconf()),
            ):
                response = self.client.get("/media/affiches/test.webp")
                self.assertEqual(response.status_code, 200)
                try:
                   self.assertEqual(b"".join(response.streaming_content), b"image-de-test")
                finally:
                   response.close()

    def test_media_non_servi_quand_debug_est_desactive(self):
        with TemporaryDirectory() as dossier:
            affiche = Path(dossier) / "test.webp"
            affiche.write_bytes(b"image-de-test")
            with (
                self.settings(DEBUG=False, MEDIA_ROOT=dossier, MEDIA_URL="/media/"),
                self.settings(ROOT_URLCONF=self.urlconf()),
            ):
                self.assertEqual(self.client.get("/media/test.webp").status_code, 404)
