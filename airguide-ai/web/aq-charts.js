/* Chart wrapper. Uses Chart.js when /static/vendor/chart.umd.min.js exists; otherwise a small
   built-in canvas renderer so the dashboard works with zero external dependencies (strict CSP). */
(function () {
  "use strict";

  var instances = new WeakMap();
  var fallbackStates = new WeakMap();

  // Chart.js is optional: only requested when the server reports that the file is present.
  var ready = new Promise(function (resolve) {
    if (window.Chart) return resolve(true);
    fetch("/api/aq/config", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (cfg) {
      if (!cfg.chartjs) return resolve(false);
      var s = document.createElement("script");
      s.src = "/static/vendor/chart.umd.min.js";
      s.onload = function () { resolve(!!window.Chart); };
      s.onerror = function () { resolve(false); };
      document.head.appendChild(s);
    }).catch(function () { resolve(false); });
  });

  function css(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function destroy(canvas) {
    var inst = instances.get(canvas);
    if (inst) { inst.destroy(); instances["delete"](canvas); }
    fallbackStates["delete"](canvas);
  }

  function empty(canvas, message) {
    destroy(canvas);
    var ctx = canvas.getContext("2d");
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = css("--muted", "#555");
    ctx.font = "14px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(message || "No data yet", w / 2, h / 2);
  }

  function niceMax(v) {
    if (v <= 0) return 10;
    var p = Math.pow(10, Math.floor(Math.log10(v)));
    var n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
  }

  function paint(canvas, spec, hover) {
    var ctx = canvas.getContext("2d");
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    var fg = css("--fg", "#111"), muted = css("--muted", "#555"), grid = css("--border", "#ccc");
    var n = spec.labels.length;
    var legendH = spec.datasets.length > 1 ? 22 : 4;
    var rotated = spec.type === "bar" && n > 8;
    var L = 46, R = 10, T = legendH + 6, B = rotated ? 70 : 34;
    var pw = w - L - R, ph = h - T - B;
    var max = 0;
    spec.datasets.forEach(function (d) { d.data.forEach(function (v) { if (v !== null && v > max) max = v; }); });
    max = niceMax(max * 1.05);
    function X(i) { return spec.type === "bar" ? L + (i + 0.5) * pw / n : L + (n === 1 ? pw / 2 : i * pw / (n - 1)); }
    function Y(v) { return T + ph - (v / max) * ph; }

    ctx.font = "11px system-ui, sans-serif";
    ctx.textBaseline = "middle";
    for (var g = 0; g <= 4; g++) {
      var gv = max * g / 4, gy = Y(gv);
      ctx.strokeStyle = grid; ctx.globalAlpha = 0.5; ctx.beginPath(); ctx.moveTo(L, gy); ctx.lineTo(w - R, gy); ctx.stroke();
      ctx.globalAlpha = 1; ctx.fillStyle = muted; ctx.textAlign = "right";
      ctx.fillText(String(Math.round(gv * 10) / 10), L - 6, gy);
    }
    if (spec.yTitle) {
      ctx.save(); ctx.translate(11, T + ph / 2); ctx.rotate(-Math.PI / 2);
      ctx.textAlign = "center"; ctx.fillStyle = muted; ctx.fillText(spec.yTitle, 0, 0); ctx.restore();
    }
    ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillStyle = muted;
    var step = rotated ? Math.max(1, Math.ceil(n / Math.max(2, Math.floor(pw / 14)))) : (spec.type === "bar" ? 1 : Math.max(1, Math.ceil(n / Math.max(2, Math.floor(pw / 110)))));
    for (var i = 0; i < n; i += step) {
      var label = spec.labels[i];
      if (rotated) {
        ctx.save(); ctx.translate(X(i), T + ph + 6); ctx.rotate(-Math.PI / 4); ctx.textAlign = "right";
        ctx.fillText(label, 0, 0); ctx.restore();
      } else {
        ctx.fillText(label, X(i), T + ph + 6);
      }
    }

    var dsN = spec.datasets.length;
    spec.datasets.forEach(function (d, di) {
      if (spec.type === "bar") {
        var bw = Math.max(3, (pw / n) * 0.7 / dsN);
        d.data.forEach(function (v, i) {
          if (v === null) return;
          ctx.fillStyle = (d.pointColors && d.pointColors[i]) || d.color;
          ctx.fillRect(X(i) - (bw * dsN) / 2 + di * bw, Y(v), bw, T + ph - Y(v));
        });
      } else {
        ctx.strokeStyle = d.color; ctx.lineWidth = 2; ctx.beginPath();
        var pen = false;
        d.data.forEach(function (v, i) {
          if (v === null) { pen = false; return; }
          if (!pen) { ctx.moveTo(X(i), Y(v)); pen = true; } else { ctx.lineTo(X(i), Y(v)); }
        });
        ctx.stroke();
        if (n <= 40) {
          ctx.fillStyle = d.color;
          d.data.forEach(function (v, i) { if (v !== null) { ctx.beginPath(); ctx.arc(X(i), Y(v), 2.5, 0, 6.3); ctx.fill(); } });
        }
      }
    });

    if (dsN > 1) {
      ctx.textBaseline = "middle"; ctx.textAlign = "left";
      var lx = L;
      spec.datasets.forEach(function (d) {
        ctx.fillStyle = d.color; ctx.fillRect(lx, 6, 12, 8);
        ctx.fillStyle = fg; ctx.font = "12px system-ui, sans-serif"; ctx.fillText(d.label, lx + 16, 10);
        lx += 24 + ctx.measureText(d.label).width;
      });
    }

    if (hover !== null && hover !== undefined) {
      var hx = X(hover);
      ctx.strokeStyle = fg; ctx.globalAlpha = 0.4; ctx.beginPath(); ctx.moveTo(hx, T); ctx.lineTo(hx, T + ph); ctx.stroke(); ctx.globalAlpha = 1;
      var lines = [spec.labels[hover]].concat(spec.datasets.map(function (d) {
        return d.label + ": " + (d.data[hover] === null ? "n/a" : d.data[hover]);
      }));
      ctx.font = "12px system-ui, sans-serif";
      var tw = Math.max.apply(null, lines.map(function (t) { return ctx.measureText(t).width; })) + 14;
      var th = lines.length * 16 + 8;
      var bx = hx + 10 + tw > w ? hx - 10 - tw : hx + 10;
      ctx.fillStyle = css("--card-bg", "#fff"); ctx.strokeStyle = grid;
      ctx.fillRect(bx, T + 4, tw, th); ctx.strokeRect(bx, T + 4, tw, th);
      ctx.fillStyle = fg; ctx.textAlign = "left"; ctx.textBaseline = "top";
      lines.forEach(function (t, i) { ctx.fillText(t, bx + 7, T + 8 + i * 16); });
    }
  }

  function fallback(canvas, spec) {
    fallbackStates.set(canvas, spec);
    paint(canvas, spec, null);
    if (canvas.dataset.bound) return;
    canvas.dataset.bound = "1";
    canvas.addEventListener("mousemove", function (e) {
      var s = fallbackStates.get(canvas); if (!s) return;
      var rect = canvas.getBoundingClientRect();
      var n = s.labels.length, L = 46, pw = rect.width - L - 10;
      var x = e.clientX - rect.left - L;
      var idx = s.type === "bar" ? Math.floor(x / (pw / n)) : Math.round(n === 1 ? 0 : x / (pw / (n - 1)));
      paint(canvas, s, idx >= 0 && idx < n ? idx : null);
    });
    canvas.addEventListener("mouseleave", function () { var s = fallbackStates.get(canvas); if (s) paint(canvas, s, null); });
    if (window.ResizeObserver) {
      new ResizeObserver(function () { var s = fallbackStates.get(canvas); if (s) paint(canvas, s, null); }).observe(canvas.parentElement);
    }
  }

  /* spec: {type: "line"|"bar", labels: [], datasets: [{label, data, color, pointColors?}], yTitle?} */
  function draw(canvas, spec) {
    if (!spec.labels.length || !spec.datasets.some(function (d) { return d.data.some(function (v) { return v !== null; }); })) {
      return empty(canvas, spec.emptyMessage);
    }
    ready.then(function (hasChartJs) {
      destroy(canvas);
      if (hasChartJs && window.Chart) {
        instances.set(canvas, new window.Chart(canvas, {
          type: spec.type,
          data: {
            labels: spec.labels,
            datasets: spec.datasets.map(function (d) {
              return { label: d.label, data: d.data, borderColor: d.color, backgroundColor: d.pointColors || d.color,
                       tension: 0.25, pointRadius: spec.labels.length > 40 ? 0 : 2, spanGaps: false };
            })
          },
          options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            plugins: { legend: { display: spec.datasets.length > 1 } },
            scales: { y: { beginAtZero: true, title: { display: !!spec.yTitle, text: spec.yTitle || "" } } }
          }
        }));
      } else {
        fallback(canvas, spec);
      }
    });
  }

  window.AQCharts = { draw: draw, ready: ready };
})();
