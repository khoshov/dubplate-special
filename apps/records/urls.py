from django.urls import path, include


urlpatterns = [
    path('api/v1/', include('records.api.v1.urls')),
]
