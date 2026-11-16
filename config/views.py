from django.conf import settings
from django.http import FileResponse
from django.views.decorators.http import require_safe
from django.shortcuts import render


@require_safe
def specification_openapi(request):
    chemin = settings.BASE_DIR / "docs" / "openapi.yaml"

    return FileResponse(
        chemin.open("rb"),
        content_type="text/plain; charset=utf-8",
        as_attachment=False,
    )

@require_safe
def documentation_swagger(request):
    return render(request, "cinema/swagger_ui.html")