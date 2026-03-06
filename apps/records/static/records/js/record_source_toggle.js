(function () {
  function normalizeSource(value) {
    return (value || '').trim().toLowerCase();
  }

  function getCurrentSourceFromUrl() {
    const params = new URLSearchParams(window.location.search);
    return normalizeSource(params.get('source'));
  }

  function syncSourceSelectWithUrl(sourceSelect) {
    const sourceFromUrl = getCurrentSourceFromUrl();
    if (!sourceFromUrl) return;

    const hasOption = Array.from(sourceSelect.options).some(function (option) {
      return normalizeSource(option.value) === sourceFromUrl;
    });
    if (!hasOption) return;

    if (normalizeSource(sourceSelect.value) !== sourceFromUrl) {
      sourceSelect.value = sourceFromUrl;
    }
  }

  function reloadWithSelectedSource(selectedSource) {
    const url = new URL(window.location.href);
    url.searchParams.set('source', selectedSource);
    window.location.assign(url.toString());
  }

  document.addEventListener('DOMContentLoaded', function () {
    const source = document.querySelector('select[name="source"]');
    if (!source) return;

    syncSourceSelectWithUrl(source);

    source.addEventListener('change', function () {
      const selectedSource = normalizeSource(source.value);
      const currentSource = getCurrentSourceFromUrl() || 'redeye';
      if (!selectedSource || selectedSource === currentSource) return;
      reloadWithSelectedSource(selectedSource);
    });
  });
})();
