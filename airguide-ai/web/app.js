(function () {
  "use strict";

  // ---------------------------------------------------------------------------
  // DOM references – cached once at startup for efficiency
  // ---------------------------------------------------------------------------
  var form = document.getElementById("form");
  var input = document.getElementById("message");
  var sendBtn = document.getElementById("send-btn");
  var log = document.getElementById("log");
  var status = document.getElementById("status");
  var engineBadge = document.getElementById("engine-badge");
  var ttsBtn = document.getElementById("tts-btn");
  var ttsStatus = document.getElementById("tts-status");
  var resetBtn = document.getElementById("reset-btn");
  var micBtn = document.getElementById("mic-btn");
  var voiceLanguage = document.getElementById("voice-language");
  var voiceStatus = document.getElementById("voice-status");
  var loadingStatus = document.getElementById("loading-status");
  var chips = document.querySelectorAll(".chip");

  var state = null;
  var ttsEnabled = false;
  var recognition = null;

  // ---------------------------------------------------------------------------
  // AQI verdict → human-readable label map
  // ---------------------------------------------------------------------------
  var VERDICT_LABEL = {
    GO: "Good to go (Normal outdoor activity)",
    GO_WITH_CARE: "Go with care (Take breaks, sensitive take note)",
    MODIFY: "Modify plan (Reduce intensity & duration)",
    AVOID: "Avoid outdoor exertion (Reschedule/indoor)",
    STAY_INDOORS: "Stay indoors (Close windows, air purifier)",
    INDOORS_OK: "Indoors is a good choice (Keep clean air)"
  };

  // ---------------------------------------------------------------------------
  // Accessibility: announce messages to screen readers via the status region
  // ---------------------------------------------------------------------------
  function announce(text) {
    if (loadingStatus) {
      // Clear first so repeated identical messages re-trigger announcement
      loadingStatus.textContent = "";
      // Deferred assignment ensures the DOM mutation fires as a new live region update
      setTimeout(function () {
        loadingStatus.textContent = text;
      }, 50);
    }
  }

  // ---------------------------------------------------------------------------
  // Format plain-text reply into safe DOM nodes (no innerHTML – XSS-safe)
  // ---------------------------------------------------------------------------
  function formatReplyIntoBubble(container, text) {
    var lines = text.split("\n");
    var currentList = null;

    lines.forEach(function (line) {
      var trimmed = line.trim();
      if (!trimmed) {
        currentList = null;
        return;
      }
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

  // ---------------------------------------------------------------------------
  // Add a chat bubble to the conversation log
  // ---------------------------------------------------------------------------
  function addMessage(who, text, cls) {
    var msgDiv = document.createElement("div");
    msgDiv.className = "msg " + cls;

    var label = document.createElement("span");
    label.className = "who";
    // aria-hidden: the role="log" container already announces additions;
    // the sender label is decorative structure, not substantive content.
    label.setAttribute("aria-hidden", "true");
    label.textContent = who + ":";
    msgDiv.appendChild(label);

    var bubble = document.createElement("div");
    bubble.className = "bubble";
    formatReplyIntoBubble(bubble, text);
    msgDiv.appendChild(bubble);

    log.appendChild(msgDiv);
    // Scroll to newest message; respects prefers-reduced-motion via CSS
    log.scrollTop = log.scrollHeight;

    // Text-to-speech for bot messages when TTS is enabled
    if (ttsEnabled && cls === "bot" && window.speechSynthesis) {
      try {
        window.speechSynthesis.cancel();
        var utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 1.0;
        utterance.lang = voiceLanguage ? voiceLanguage.value : "en-IN";
        window.speechSynthesis.speak(utterance);
      } catch (e) {
        // Speech synthesis failure is non-fatal
      }
    }
  }

  // ---------------------------------------------------------------------------
  // AQI → CSS badge class (WCAG AA/AAA compliant colour pairs in style.css)
  // ---------------------------------------------------------------------------
  function badgeClass(aqi) {
    if (aqi <= 50) return "b0";
    if (aqi <= 100) return "b1";
    if (aqi <= 200) return "b2";
    if (aqi <= 300) return "b3";
    if (aqi <= 400) return "b4";
    return "b5";
  }

  // ---------------------------------------------------------------------------
  // Update the AQI status banner from an API summary object
  // ---------------------------------------------------------------------------
  function updateStatus(summary) {
    if (!summary || summary.aqi === null || summary.aqi === undefined) return;
    var text = "AQI " + summary.aqi;
    if (summary.location) text += " \u00B7 " + summary.location;
    if (summary.verdict) text += " \u00B7 " + (VERDICT_LABEL[summary.verdict] || summary.verdict);
    status.textContent = text;
    status.className = "badge " + badgeClass(summary.aqi);
  }

  // ---------------------------------------------------------------------------
  // Health-check: populate the engine badge with the current provider
  // ---------------------------------------------------------------------------
  function checkHealth() {
    fetch("/healthz")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.provider && engineBadge) {
          engineBadge.textContent = data.provider;
        }
      })
      .catch(function () {
        // Server not reachable on startup — not fatal, badge shows default text
      });
  }

  // ---------------------------------------------------------------------------
  // Core: send a message to the /api/chat endpoint
  // ---------------------------------------------------------------------------
  function sendMessage(message) {
    if (!message || !message.trim()) return;

    addMessage("You", message, "user");
    input.value = "";
    sendBtn.disabled = true;
    // Notify screen readers that the app is processing
    log.setAttribute("aria-busy", "true");
    announce("One moment — checking your question and the available air-quality information\u2026");

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
          var errMsg = (res.body && res.body.error) ? res.body.error : "Something went wrong.";
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
        var connErr = "I couldn\u2019t reach the server. Please check your connection and try again.";
        addMessage("AirGuide", connErr, "bot");
        announce(connErr);
      });

    input.focus();
  }

  // ---------------------------------------------------------------------------
  // Form submit
  // ---------------------------------------------------------------------------
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    sendMessage(input.value.trim());
  });

  // ---------------------------------------------------------------------------
  // Quick-scenario chips
  // ---------------------------------------------------------------------------
  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      var prompt = chip.getAttribute("data-prompt");
      if (prompt) {
        input.value = prompt;
        sendMessage(prompt);
      }
    });
    // Allow keyboard activation (Enter/Space already work for buttons)
  });

  // ---------------------------------------------------------------------------
  // TTS toggle
  // ---------------------------------------------------------------------------
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

  // ---------------------------------------------------------------------------
  // Voice / Speech Recognition (progressive enhancement — not required)
  // ---------------------------------------------------------------------------
  if (micBtn) {
    var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      micBtn.disabled = true;
      micBtn.setAttribute("aria-disabled", "true");
      micBtn.title = "Voice input is not supported by this browser. Try a recent version of Chrome.";
      if (voiceStatus) voiceStatus.textContent = "Voice input is not supported in this browser.";
    } else {
      recognition = new SpeechRecognition();
      recognition.continuous = false;
      recognition.interimResults = false;

      recognition.onstart = function () {
        micBtn.textContent = "Listening\u2026";
        micBtn.setAttribute("aria-pressed", "true");
        micBtn.setAttribute("aria-label", "Stop voice input");
        if (voiceStatus) voiceStatus.textContent = "Listening. Speak now.";
      };

      recognition.onresult = function (event) {
        var transcript = (
          event.results &&
          event.results[0] &&
          event.results[0][0]
        ) ? event.results[0][0].transcript : "";
        if (transcript) {
          input.value = transcript;
          input.focus();
          if (voiceStatus) voiceStatus.textContent = "Voice captured. Review your message and press Send.";
        }
      };

      recognition.onerror = function (event) {
        var msg = "Voice input error: " + (event.error || "please try again");
        if (voiceStatus) voiceStatus.textContent = msg;
        announce(msg);
      };

      recognition.onend = function () {
        micBtn.textContent = "\uD83C\uDF99 Speak";
        micBtn.setAttribute("aria-pressed", "false");
        micBtn.setAttribute("aria-label", "Start voice input");
      };

      micBtn.addEventListener("click", function () {
        try {
          recognition.lang = voiceLanguage ? voiceLanguage.value : "en-IN";
          recognition.start();
        } catch (e) {
          if (voiceStatus) voiceStatus.textContent = "Microphone is already active or unavailable.";
        }
      });
    }
  }

  // ---------------------------------------------------------------------------
  // Reset conversation
  // ---------------------------------------------------------------------------
  if (resetBtn) {
    resetBtn.addEventListener("click", function () {
      state = null;
      status.textContent = "No reading yet";
      status.className = "badge badge-none";
      // Clear log safely (no innerHTML assignment)
      while (log.firstChild) {
        log.removeChild(log.firstChild);
      }
      addMessage("AirGuide", "Conversation reset. Which Indian city should I check for you?", "bot");
      announce("Conversation has been reset.");
      input.value = "";
      input.focus();
    });
  }

  // ---------------------------------------------------------------------------
  // Initialise: fetch provider info from health endpoint
  // ---------------------------------------------------------------------------
  checkHealth();
})();
