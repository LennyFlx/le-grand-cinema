from django.test import SimpleTestCase
from django.urls import reverse


class DocumentationSwaggerTests(SimpleTestCase):

    def test_page_swagger_accessible(self):
        response = self.client.get(reverse("swagger_ui"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SwaggerUIBundle")
        self.assertContains(response, "supportedSubmitMethods: []")

    def test_specification_openapi_accessible(self):
        response = self.client.get(reverse("openapi_schema"))

        self.assertEqual(response.status_code, 200)

        contenu = b"".join(response.streaming_content)

        self.assertIn(b"openapi: 3.0.3", contenu)