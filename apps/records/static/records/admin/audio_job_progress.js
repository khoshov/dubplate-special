(function () {
  "use strict";

  var DUMMY_UUID = "00000000-0000-0000-0000-000000000000";
  var STORAGE_KEY_PREFIX = "records:audio-job-watch:";
  var POLL_INTERVAL_MS = 1000;
  var RELOAD_THRESHOLDS = [25, 50, 75, 100];

  function getSubmitRow() {
    return document.querySelector(
      ".submit-row[data-record-id][data-audio-job-status-url-template]"
    );
  }

  function getContext() {
    var submitRow = getSubmitRow();
    if (!submitRow) {
      return null;
    }
    var recordId = String(submitRow.dataset.recordId || "").trim();
    var statusUrlTemplate = String(
      submitRow.dataset.audioJobStatusUrlTemplate || ""
    ).trim();
    if (!recordId || !statusUrlTemplate) {
      return null;
    }
    return {
      recordId: recordId,
      statusUrlTemplate: statusUrlTemplate,
    };
  }

  function buildStorageKey(recordId) {
    return STORAGE_KEY_PREFIX + recordId;
  }

  function readWatcher(recordId) {
    try {
      var raw = window.sessionStorage.getItem(buildStorageKey(recordId));
      return raw ? JSON.parse(raw) : null;
    } catch (error) {
      return null;
    }
  }

  function writeWatcher(recordId, payload) {
    try {
      window.sessionStorage.setItem(
        buildStorageKey(recordId),
        JSON.stringify(payload)
      );
    } catch (error) {}
  }

  function clearWatcher(recordId) {
    try {
      window.sessionStorage.removeItem(buildStorageKey(recordId));
    } catch (error) {}
  }

  function buildStatusUrl(template, jobId) {
    var baseUrl = String(template || "").replace(DUMMY_UUID, String(jobId || ""));
    if (!baseUrl) {
      return "";
    }
    var separator = baseUrl.indexOf("?") === -1 ? "?" : "&";
    return baseUrl + separator + "_ts=" + String(Date.now());
  }

  function findNextThreshold(seenThresholds, progressPercent) {
    var normalizedSeen = Array.isArray(seenThresholds) ? seenThresholds : [];
    for (var index = 0; index < RELOAD_THRESHOLDS.length; index += 1) {
      var threshold = RELOAD_THRESHOLDS[index];
      if (normalizedSeen.indexOf(threshold) !== -1) {
        continue;
      }
      if (progressPercent >= threshold) {
        return threshold;
      }
    }
    return null;
  }

  function pollWatcher(context) {
    var watcher = readWatcher(context.recordId);
    if (!watcher || !watcher.jobId) {
      return;
    }

    var statusUrl = buildStatusUrl(context.statusUrlTemplate, watcher.jobId);
    if (!statusUrl) {
      clearWatcher(context.recordId);
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
          throw new Error(result.payload.error || "Не удалось получить статус job.");
        }

        var progressPercent = Number(result.payload.progress_percent || 0);
        var finished = Boolean(result.payload.finished);
        var nextThreshold = findNextThreshold(
          watcher.seenThresholds,
          progressPercent
        );

        if (finished && nextThreshold === null) {
          clearWatcher(context.recordId);
          window.location.reload();
          return;
        }

        if (nextThreshold !== null) {
          var updatedWatcher = {
            jobId: watcher.jobId,
            seenThresholds: (watcher.seenThresholds || []).concat([nextThreshold]),
          };
          if (nextThreshold >= 100 || finished) {
            clearWatcher(context.recordId);
          } else {
            writeWatcher(context.recordId, updatedWatcher);
          }
          window.location.reload();
          return;
        }

        window.setTimeout(function () {
          pollWatcher(context);
        }, POLL_INTERVAL_MS);
      })
      .catch(function () {
        window.setTimeout(function () {
          pollWatcher(context);
        }, POLL_INTERVAL_MS);
      });
  }

  function startWatching(jobId) {
    var context = getContext();
    if (!context || !jobId) {
      return;
    }
    writeWatcher(context.recordId, {
      jobId: String(jobId),
      seenThresholds: [],
    });
    pollWatcher(context);
  }

  function resumeWatching() {
    var context = getContext();
    if (!context) {
      return;
    }
    if (!readWatcher(context.recordId)) {
      return;
    }
    pollWatcher(context);
  }

  window.recordsAudioJobWatcher = {
    startWatching: startWatching,
    resumeWatching: resumeWatching,
  };

  document.addEventListener("DOMContentLoaded", resumeWatching);
})();
