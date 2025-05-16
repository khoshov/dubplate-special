from django.urls import path, include
from rest_framework import routers
from . import views

router = routers.DefaultRouter()
router.register(r'records', views.RecordViewSet)

urlpatterns = [
    path("", views.api_root, name='api-root'),
    path("", include(router.urls)),
]
