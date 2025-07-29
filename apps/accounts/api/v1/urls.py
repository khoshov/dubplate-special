from rest_framework.routers import DefaultRouter

from django.urls import include, path

from .views import (
    AuthViewSet,
    SMSAuthViewSet,
    UserDetailView,
    UserProfileViewSet,
)

app_name = "accounts_api"

router = DefaultRouter()
router.register(r"profile", UserProfileViewSet, basename="profile")
router.register(r"auth", AuthViewSet, basename="auth")
router.register(r"sms-auth", SMSAuthViewSet, basename="sms-auth")

urlpatterns = [
    path("", include(router.urls)),
    path("me/", UserDetailView.as_view(), name="user-detail"),
]
