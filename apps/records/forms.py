from django import forms

class BarcodeImportForm(forms.Form):
    barcode = forms.CharField(
        label='Штрих-код',
        max_length=20,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Введите штрих-код с обложки'
        })
    )
