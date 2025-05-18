from django.urls import include, path

urlpatterns = [
    path("api/v1/", include("records.api.v1.urls")),
]
