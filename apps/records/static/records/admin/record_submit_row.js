(function () {
  "use strict";

  function getCsrfToken() {
    var tokenInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return tokenInput ? tokenInput.value : "";
  }

  function bindSearchButton() {
    var button = document.querySelector(".js-youtube-audio-search");
    if (!button || button.dataset.bound === "1") {
      return;
    }
    button.dataset.bound = "1";

    button.addEventListener("click", function () {
      var url = button.dataset.searchUrl || "";
      if (!url || button.disabled) {
        return;
      }

      button.disabled = true;

      fetch(url, {
        method: "POST",
        headers: {
          "X-CSRFToken": getCsrfToken(),
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
            throw new Error(result.payload.error || "Запрос не выполнен.");
          }
          if (
            window.recordsAudioJobWatcher &&
            typeof window.recordsAudioJobWatcher.startWatching === "function"
          ) {
            window.recordsAudioJobWatcher.startWatching(result.payload.job_id);
            return;
          }
          window.location.reload();
        })
        .catch(function (error) {
          button.disabled = false;
          window.alert(error.message || "Ошибка при постановке в очередь.");
        });
    });
  }

  function bindRefreshButton() {
    var button = document.querySelector(".js-youtube-audio-refresh");
    if (!button || button.dataset.bound === "1") {
      return;
    }
    button.dataset.bound = "1";

    button.addEventListener("click", function (event) {
      event.preventDefault();
      var url = button.dataset.refreshUrl || button.getAttribute("formaction") || "";
      if (!url || button.disabled) {
        return;
      }

      button.disabled = true;

      fetch(url, {
        method: "POST",
        headers: {
          "X-CSRFToken": getCsrfToken(),
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
            throw new Error(result.payload.error || "Запрос не выполнен.");
          }
          if (
            window.recordsAudioJobWatcher &&
            typeof window.recordsAudioJobWatcher.startWatching === "function"
          ) {
            window.recordsAudioJobWatcher.startWatching(result.payload.job_id);
            return;
          }
          window.location.reload();
        })
        .catch(function (error) {
          button.disabled = false;
          window.alert(error.message || "Ошибка при постановке в очередь.");
        });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindSearchButton();
    bindRefreshButton();
  });
})();
