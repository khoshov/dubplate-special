from django_filters import CharFilter, FilterSet


class RecordFilter(FilterSet):
    genre = CharFilter(
        field_name="genres__name",
        lookup_expr="icontains",
    )

    style = CharFilter(
        field_name="styles__name",
        lookup_expr="icontains",
    )
