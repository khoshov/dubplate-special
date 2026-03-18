(function () {
  "use strict";

  function getCsrfToken() {
    var tokenInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return tokenInput ? tokenInput.value : "";
  }

  function bindDeleteButtons() {
    var buttons = document.querySelectorAll("#tracks-group .js-track-delete-mp3");
    buttons.forEach(function (button) {
      if (button.dataset.bound === "1") {
        return;
      }
      button.dataset.bound = "1";

      button.addEventListener("click", function () {
        var deleteUrl = button.dataset.deleteMp3Url || "";
        if (!deleteUrl || button.disabled) {
          return;
        }

        if (!window.confirm("Удалить mp3 у этого трека?")) {
          return;
        }

        button.disabled = true;

        fetch(deleteUrl, {
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
              throw new Error(result.payload.error || "Удаление не выполнено.");
            }
            window.location.reload();
          })
          .catch(function (error) {
            button.disabled = false;
            window.alert(error.message || "Ошибка при удалении mp3.");
          });
      });
    });
  }

  function bindEnqueueButtons() {
    var buttons = document.querySelectorAll("#tracks-group .js-track-enqueue-mp3");
    buttons.forEach(function (button) {
      if (button.dataset.bound === "1") {
        return;
      }
      button.dataset.bound = "1";

      button.addEventListener("click", function () {
        var enqueueUrl = button.dataset.enqueueMp3Url || "";
        if (!enqueueUrl || button.disabled) {
          return;
        }

        if (!window.confirm("Поставить трек в очередь на загрузку mp3?")) {
          return;
        }

        button.disabled = true;

        fetch(enqueueUrl, {
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
              throw new Error(result.payload.error || "Загрузка не выполнена.");
            }
            window.location.reload();
          })
          .catch(function (error) {
            button.disabled = false;
            window.alert(error.message || "Ошибка при постановке в очередь.");
          });
      });
    });
  }

  function bindAll() {
    bindDeleteButtons();
    bindEnqueueButtons();
  }

  document.addEventListener("DOMContentLoaded", bindAll);
  document.addEventListener("formset:added", bindAll);
})();
