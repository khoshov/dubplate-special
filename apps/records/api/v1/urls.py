from rest_framework import routers

from django.urls import include, path

from . import views

router = routers.DefaultRouter()
router.register(r"records", views.RecordViewSet)

urlpatterns = [
    path("", include(router.urls)),
    path('styles/', views.StyleListView.as_view(), name=views.StyleListView.name),
]
