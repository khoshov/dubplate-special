from django.contrib import admin
from django.urls import path, include

from . import views


urlpatterns = [
    path("", views.ApiRoot.as_view(), name=views.ApiRoot.name),
    path("artists/", views.ArtistsList.as_view(), name=views.ArtistsList.name),
    path("artists/<int:pk>/", views.ArtistsDetail.as_view(), name=views.ArtistsDetail.name),
    path("labels/", views.LabelsList.as_view(), name=views.LabelsList.name),
    path("labels/<int:pk>/", views.LabelsDetail.as_view(), name=views.LabelsDetail.name),
    path("genres/", views.GenresList.as_view(), name=views.GenresList.name),
    path("genres/<int:pk>/", views.GenresDetail.as_view(), name=views.GenresDetail.name),
    path("styles/", views.StylesList.as_view(), name=views.StylesList.name),
    path("styles/<int:pk>/", views.StylesDetail.as_view(), name=views.StylesDetail.name),
    path("records/", views.RecordsList.as_view(), name=views.RecordsList.name),
    path("records/<int:pk>/", views.RecordsDetail.as_view(), name=views.RecordsDetail.name),
    path("tracks/", views.TracksList.as_view(), name=views.TracksList.name),
    path("tracks/<int:pk>/", views.TracksDetail.as_view(), name=views.TracksDetail.name),
]