(function () {
  function initStructuredFormatGroup(group) {
    const rows = Array.from(
      group.querySelectorAll("tr.form-row[data-structured-format-variant]")
    );
    if (!rows.length) {
      return;
    }

    const selectors = Array.from(
      group.querySelectorAll("[data-structured-format-selector]")
    );
    const hiddenInput = group.querySelector("[data-structured-active-variant]");
    const hasMultipleVariants =
      group.getAttribute("data-has-multiple-variants") === "1";
    const variants = rows.map(
      (row) => row.getAttribute("data-structured-format-variant") || "1"
    );

    function applyVariant(variant) {
      const normalizedVariant = variants.includes(String(variant))
        ? String(variant)
        : variants[0];

      rows.forEach((row) => {
        row.hidden =
          hasMultipleVariants &&
          row.getAttribute("data-structured-format-variant") !== normalizedVariant;
      });

      selectors.forEach((selector) => {
        selector.value = normalizedVariant;
      });

      if (hiddenInput) {
        hiddenInput.value = normalizedVariant;
      }
    }

    selectors.forEach((selector) => {
      selector.addEventListener("change", (event) => {
        applyVariant(event.target.value);
      });
    });

    const initialVariant = (hiddenInput && hiddenInput.value) || variants[0];
    applyVariant(initialVariant);
  }

  document.addEventListener("DOMContentLoaded", () => {
    document
      .querySelectorAll("[data-structured-format-group]")
      .forEach(initStructuredFormatGroup);
  });
})();
