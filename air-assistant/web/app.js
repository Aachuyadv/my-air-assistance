(function () {
  "use strict";

  var form = document.getElementById("form");
  var input = document.getElementById("message");
  var sendBtn = document.getElementById("send-btn");
  var log = document.getElementById("log");
  var status = document.getElementById("status");
  var engineBadge = document.getElementById("engine-badge");
  var ttsBtn = document.getElementById("tts-btn");
  var ttsStatus = document.getElementById("tts-status");
  var resetBtn = document.getElementById("reset-btn");
  var loadingStatus = document.getElementById("loading-status");
  var chips = document.querySelectorAll(".chip");

  var state = null;
  var ttsEnabled = false;

  var VERDICT_LABEL = {
    GO: "Good to go (Normal outdoor activity)",
    GO_WITH_CARE: "Go with care (Take breaks, sensitive take note)",
    MODIFY: "Modify plan (Reduce intensity & duration)",
    AVOID: "Avoid outdoor exertion (Reschedule/indoor)",
    STAY_INDOORS: "Stay indoors (Close windows, air purifier)",
    INDOORS_OK: "Indoors is a good choice (Keep clean air)"
  };

  function announce(text) {
    if (loadingStatus) {
      loadingStatus.textContent = text;
    }
  }

  function formatReplyIntoBubble(container, text) {
    var lines = text.split("\n");
    var currentList = null;

    lines.forEach(function (line) {
      var trimmed = line.trim();
      if (!trimmed) return;

      if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
        if (!currentList) {
          currentList = document.createElement("ul");
          container.appendChild(currentList);
        }
        var li = document.createElement("li");
        li.appendChild(document.createTextNode(trimmed.slice(2)));
        currentList.appendChild(li);
      } else {
        currentList = null;
        var p = document.createElement("p");
        p.appendChild(document.createTextNode(trimmed));
        container.appendChild(p);
      }
    });
  }

  function addMessage(who, text, cls) {
    var msgDiv = document.createElement("div");
    msgDiv.className = "msg " + cls;

    var label = document.createElement("span");
    label.className = "who";
    label.textContent = who + ":";
    msgDiv.appendChild(label);

    var bubble = document.createElement("div");
    bubble.className = "bubble";
    formatReplyIntoBubble(bubble, text);
    msgDiv.appendChild(bubble);

    log.appendChild(msgDiv);
    log.scrollTop = log.scrollHeight;

    if (ttsEnabled && cls === "bot" && window.speechSynthesis) {
      try {
        window.speechSynthesis.cancel();
        var utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 1.0;
        window.speechSynthesis.speak(utterance);
      } catch (e) {}
    }
  }

  function badgeClass(aqi) {
    if (aqi <= 50) return "b0";
    if (aqi <= 100) return "b1";
    if (aqi <= 200) return "b2";
    if (aqi <= 300) return "b3";
    if (aqi <= 400) return "b4";
    return "b5";
  }

  function updateStatus(summary) {
    if (!summary || summary.aqi === null || summary.aqi === undefined) return;
    var text = "AQI " + summary.aqi;
    if (summary.location) text += " \u00B7 " + summary.location;
    if (summary.verdict) text += " \u00B7 " + (VERDICT_LABEL[summary.verdict] || summary.verdict);
    status.textContent = text;
    status.className = "badge " + badgeClass(summary.aqi);
  }

  function checkHealth() {
    fetch("/healthz")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.provider && engineBadge) {
          engineBadge.textContent = data.provider;
        }
      })
      .catch(function () {});
  }

  function sendMessage(message) {
    if (!message) return;
    addMessage("You", message, "user");
    input.value = "";
    sendBtn.disabled = true;
    log.setAttribute("aria-busy", "true");
    announce("One moment — checking your question and the available air-quality information...");

    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: message, state: state })
    })
      .then(function (r) {
        return r.json().then(function (j) { return { ok: r.ok, body: j }; });
      })
      .then(function (res) {
        sendBtn.disabled = false;
        log.setAttribute("aria-busy", "false");
        if (!res.ok) {
          var errMsg = res.body.error || "Something went wrong.";
          addMessage("AirGuide", errMsg, "bot");
          announce("Error: " + errMsg);
          return;
        }
        state = res.body.state;
        addMessage("AirGuide", res.body.reply, "bot");
        updateStatus(res.body.summary);
        announce("New guidance received from AirGuide.");
        if (res.body.provider && engineBadge) {
          engineBadge.textContent = res.body.provider;
        }
      })
      .catch(function () {
        sendBtn.disabled = false;
        log.setAttribute("aria-busy", "false");
        var connErr = "I couldn't reach the server. Please check your connection and try again.";
        addMessage("AirGuide", connErr, "bot");
        announce(connErr);
      });

    input.focus();
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    sendMessage(input.value.trim());
  });

  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      var prompt = chip.getAttribute("data-prompt");
      if (prompt) {
        input.value = prompt;
        sendMessage(prompt);
      }
    });
  });

  if (ttsBtn && ttsStatus) {
    ttsBtn.addEventListener("click", function () {
      ttsEnabled = !ttsEnabled;
      ttsBtn.setAttribute("aria-pressed", ttsEnabled ? "true" : "false");
      ttsStatus.textContent = ttsEnabled ? "On" : "Off";
      if (!ttsEnabled && window.speechSynthesis) {
        window.speechSynthesis.cancel();
      }
      announce(ttsEnabled ? "Voice narration enabled." : "Voice narration disabled.");
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener("click", function () {
      state = null;
      status.textContent = "No reading yet";
      status.className = "badge badge-none";
      log.innerHTML = "";
      addMessage("AirGuide", "Conversation reset. Which Indian city should I check for you?", "bot");
      announce("Conversation has been reset.");
      input.value = "";
      input.focus();
    });
  }

  checkHealth();
})();
