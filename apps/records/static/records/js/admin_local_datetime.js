(function () {
  "use strict";

  function renderLocalDateTimes() {
    var nodes = document.querySelectorAll("time.js-vk-published-at[data-utc]");
    if (!nodes.length) {
      return;
    }

    nodes.forEach(function (node) {
      var raw = node.getAttribute("data-utc");
      if (!raw) {
        return;
      }

      var dt = new Date(raw);
      if (Number.isNaN(dt.getTime())) {
        return;
      }

      node.textContent = dt.toLocaleString(undefined, {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        timeZoneName: "short",
      });
      node.setAttribute("title", raw);
      node.setAttribute("datetime", raw);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderLocalDateTimes);
  } else {
    renderLocalDateTimes();
  }
})();
