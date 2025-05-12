from rest_framework import permissions, generics
from rest_framework.response import Response
from rest_framework.reverse import reverse

from .serializers import (
    ArtistSerializer,
    GenreSerializer,
    LabelSerializer,
    RecordSerializer,
    StyleSerializer,
    TrackSerializer,
)
from records.models import Artist, Label, Genre, Style, Record, Track


class BaseListCreateAPIView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]


class BaseRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]


class ArtistsList(BaseListCreateAPIView):
    queryset = Artist.objects.all()
    serializer_class = ArtistSerializer
    name = 'artists-list'


class ArtistsDetail(BaseRetrieveUpdateDestroyAPIView):
    queryset = Artist.objects.all()
    serializer_class = ArtistSerializer
    name = 'artist-detail'


class LabelsList(BaseListCreateAPIView):
    queryset = Label.objects.all()
    serializer_class = LabelSerializer
    name = 'labels-list'


class LabelsDetail(BaseRetrieveUpdateDestroyAPIView):
    queryset = Label.objects.all()
    serializer_class = LabelSerializer
    name = 'label-detail'


class GenresList(BaseListCreateAPIView):
    queryset = Genre.objects.all()
    serializer_class = GenreSerializer
    name = 'genres-list'


class GenresDetail(BaseRetrieveUpdateDestroyAPIView):
    queryset = Genre.objects.all()
    serializer_class = GenreSerializer
    name = 'genre-detail'


class StylesList(BaseListCreateAPIView):
    queryset = Style.objects.all()
    serializer_class = StyleSerializer
    name = 'styles-list'


class StylesDetail(BaseRetrieveUpdateDestroyAPIView):
    queryset = Style.objects.all()
    serializer_class = StyleSerializer
    name = 'style-detail'


class RecordsList(BaseListCreateAPIView):
    queryset = Record.objects.all()
    serializer_class = RecordSerializer
    name = 'records-list'


class RecordsDetail(BaseRetrieveUpdateDestroyAPIView):
    queryset = Record.objects.all()
    serializer_class = RecordSerializer
    name = 'record-detail'


class TracksList(BaseListCreateAPIView):
    queryset = Track.objects.all()
    serializer_class = TrackSerializer
    name = 'tracks-list'


class TracksDetail(BaseRetrieveUpdateDestroyAPIView):
    queryset = Track.objects.all()
    serializer_class = TrackSerializer
    name = 'track-detail'



class ApiRoot(generics.GenericAPIView):
    name = 'api-root'
    def get(self, request, *args, **kwargs):
        return Response({
            'artists': reverse('artists-list', request=request),
            'labels': reverse('labels-list', request=request),
            'genres': reverse('genres-list', request=request),
            'styles': reverse('styles-list', request=request),
            'records': reverse('records-list', request=request),
            'tracks': reverse('tracks-list', request=request),
        })
