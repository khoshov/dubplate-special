"""
URL configuration for dubplate-special-api project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="admin:index"), name="root"),
    path(
        "admin/records/audioenrichmentjob/",
        RedirectView.as_view(
            pattern_name="admin:records_releasejob_changelist",
            permanent=False,
        ),
    ),
    path(
        "admin/records/audioenrichmentjob/<path:object_id>/change/",
        RedirectView.as_view(
            pattern_name="admin:records_releasejob_change",
            permanent=False,
        ),
    ),
    path(
        "admin/records/audioenrichmentjobrecord/",
        RedirectView.as_view(
            pattern_name="admin:records_releasereport_changelist",
            permanent=False,
        ),
    ),
    path(
        "admin/records/audioenrichmentjobrecord/<path:object_id>/change/",
        RedirectView.as_view(
            pattern_name="admin:records_releasereport_change",
            permanent=False,
        ),
    ),
    path(
        "admin/records/vkpublicationjobrecord/",
        RedirectView.as_view(
            pattern_name="admin:records_vkpublicationreport_changelist",
            permanent=False,
        ),
    ),
    path(
        "admin/records/vkpublicationjobrecord/<path:object_id>/change/",
        RedirectView.as_view(
            pattern_name="admin:records_vkpublicationreport_change",
            permanent=False,
        ),
    ),
    path("admin/", admin.site.urls),
    path("ckeditor5/", include("django_ckeditor_5.urls")),
    path("api/v1/records/", include("records.api.v1.urls")),
    path("api/v1/orders/", include("orders.api.v1.urls")),
    path("api/v1/accounts/", include("accounts.api.v1.urls")),
    path("openapi.json", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.SILK_ENABLED:
    urlpatterns.append(path("silk/", include("silk.urls", namespace="silk")))
