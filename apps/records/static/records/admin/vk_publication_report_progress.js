(function () {
  "use strict";

  var POLL_INTERVAL_MS = 3000;
  function extractReportId() {
    var match = window.location.pathname.match(
      /\/admin\/records\/vkpublicationreport\/([0-9a-f-]+)\/change\/?$/i
    );
    return match ? String(match[1]) : "";
  }

  function buildStatusUrl(reportId) {
    if (!reportId) {
      return "";
    }
    return (
      "/admin/records/vkpublicationreport/" +
      reportId +
      "/status/?_ts=" +
      String(Date.now())
    );
  }

  function pollUntilFinished() {
    var reportId = extractReportId();
    var statusUrl = buildStatusUrl(reportId);
    if (!statusUrl) {
      return;
    }

    fetch(statusUrl, {
      method: "GET",
      cache: "no-store",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
      },
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          return { status: response.status, payload: payload };
        });
      })
      .then(function (result) {
        if (result.status >= 400 || !result.payload.ok) {
          throw new Error(result.payload.error || "Не удалось получить статус лога VK.");
        }

        var status = String(result.payload.status || "").trim().toLowerCase();
        var finished = Boolean(result.payload.finished);
        if (finished || (status && status !== "queued" && status !== "running")) {
          window.location.reload();
          return;
        }

        window.setTimeout(pollUntilFinished, POLL_INTERVAL_MS);
      })
      .catch(function () {
        window.setTimeout(pollUntilFinished, POLL_INTERVAL_MS);
      });
  }

  document.addEventListener("DOMContentLoaded", pollUntilFinished);
})();
