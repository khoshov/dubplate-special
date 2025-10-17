(function () {
  function toggleFields() {
    const source = document.querySelector('select[name="source"]');
    if (!source) return;

    const barcodeRow = document.querySelector('.forms-row.field-barcode, .field-barcode');
    const catalogRow = document.querySelector('.forms-row.field-catalog_number, .field-catalog_number');

    if (!barcodeRow || !catalogRow) return;

    if (source.value === 'redeye') {
      barcodeRow.style.display = 'none';
      catalogRow.style.display = '';
    } else {
      barcodeRow.style.display = '';
      catalogRow.style.display = '';
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    toggleFields();
    const source = document.querySelector('select[name="source"]');
    if (source) {
      source.addEventListener('change', toggleFields);
    }
  });
})();
