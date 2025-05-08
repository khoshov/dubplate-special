from django.shortcuts import render, redirect
from django.views.generic import DetailView
from .forms import BarcodeImportForm
from .models import Record
from services.discogs_service import import_from_discogs


class RecordDetailView(DetailView):
    model = Record
    template_name = 'records/record_detail.html'
    context_object_name = 'record'


def record_list(request):
    records = Record.objects.all()
    return render(request, 'records/record_list.html', {'records': records})


def import_by_barcode(request):
    if request.method == 'POST':
        form = BarcodeImportForm(request.POST)
        if form.is_valid():
            barcode = form.cleaned_data['barcode']

            # Импортируем данные из Discogs
            record = import_from_discogs(barcode)

            if record:
                return redirect('record_detail', pk=record.pk)
            else:
                form.add_error('barcode', 'Релиз с таким штрих-кодом не найден')
    else:
        form = BarcodeImportForm()

    return render(request, 'records/import_by_barcode.html', {'form': form})
