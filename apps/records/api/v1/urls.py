from rest_framework import routers

from django.urls import include, path

from . import views

router = routers.DefaultRouter()
router.register(r"records", views.RecordViewSet)
router.register(r"styles", views.StyleViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
