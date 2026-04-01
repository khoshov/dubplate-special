(function () {
  "use strict";

  var POLL_INTERVAL_MS = 2000;

  function getSubmitRow() {
    return document.querySelector(
      ".submit-row[data-record-id][data-record-audio-state-url]"
    );
  }

  function readInitialAudioCount(submitRow) {
    var rawValue = String(submitRow.dataset.recordAudioCount || "").trim();
    var parsedValue = Number(rawValue || "0");
    return Number.isFinite(parsedValue) ? parsedValue : 0;
  }

  function pollRecordAudioState() {
    var submitRow = getSubmitRow();
    if (!submitRow) {
      return;
    }

    var stateUrl = String(submitRow.dataset.recordAudioStateUrl || "").trim();
    if (!stateUrl) {
      return;
    }

    var initialAudioCount = readInitialAudioCount(submitRow);
    fetch(stateUrl + "?_ts=" + String(Date.now()), {
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
          throw new Error(result.payload.error || "Не удалось получить состояние аудио релиза.");
        }

        var currentAudioCount = Number(result.payload.tracks_with_audio || 0);
        if (currentAudioCount > initialAudioCount) {
          window.location.reload();
          return;
        }

        window.setTimeout(pollRecordAudioState, POLL_INTERVAL_MS);
      })
      .catch(function () {
        window.setTimeout(pollRecordAudioState, POLL_INTERVAL_MS);
      });
  }

  document.addEventListener("DOMContentLoaded", pollRecordAudioState);
})();
