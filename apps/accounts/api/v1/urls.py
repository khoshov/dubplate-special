from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import UserProfileViewSet, AuthViewSet, UserDetailView, OrderHistoryViewSet, SMSAuthViewSet

app_name = 'accounts_api'

router = DefaultRouter()
router.register(r'profile', UserProfileViewSet, basename='profile')
router.register(r'auth', AuthViewSet, basename='auth')
router.register(r'sms-auth', SMSAuthViewSet, basename='sms-auth')
router.register(r'orders', OrderHistoryViewSet, basename='orders')

urlpatterns = [
    path('', include(router.urls)),
    path('me/', UserDetailView.as_view(), name='user-detail'),
]