from django.shortcuts import redirect, render
from django.views.generic import DetailView

from services.discogs_service import DiscogsService  # Измененный импорт

from .forms import BarcodeImportForm
from .models import Record


class RecordDetailView(DetailView):
    model = Record
    template_name = "records/record_detail.html"
    context_object_name = "record"


def record_list(request):
    records = Record.objects.all()
    return render(request, "records/record_list.html", {"records": records})


def import_by_barcode(request):
    if request.method == "POST":
        form = BarcodeImportForm(request.POST)
        if form.is_valid():
            barcode = form.cleaned_data["barcode"]

            # Создаем экземпляр сервиса и импортируем данные
            discogs_service = DiscogsService()  # Создаем экземпляр сервиса
            record = discogs_service.import_release_by_barcode(
                barcode
            )  # Используем новый метод

            if record:
                return redirect("record_detail", pk=record.pk)
            else:
                form.add_error("barcode", "Релиз с таким штрих-кодом не найден")
    else:
        form = BarcodeImportForm()

    return render(request, "records/import_by_barcode.html", {"form": form})
