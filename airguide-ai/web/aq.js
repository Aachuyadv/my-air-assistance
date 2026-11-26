(function () {
  "use strict";

  /* ------------------------------------------------------------------ helpers */
  var $ = function (id) { return document.getElementById(id); };
  var VIEWS = ["dashboard", "map", "forecast", "cities", "employee", "family", "activities", "history", "alerts", "escape", "lens", "admin"];
  var KIND_LABEL = { current: "Current city", office: "Office", home: "Home", family: "Family" };
  var AUD_LABEL = { employee: "Employee", child: "Child", family: "Family", office: "Office", field: "Field", commuter: "Commuter", sensitive: "Respiratory sensitivity" };
  var S = { overview: null, me: null, config: null, selected: "", loaded: {}, map: null, mapMarkers: [], stationMarkers: [] };

  function el(tag, props, kids) {
    var n = document.createElement(tag);
    props = props || {};
    Object.keys(props).forEach(function (k) {
      if (k === "text") n.textContent = props[k];
      else if (k === "class") n.className = props[k];
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), props[k]);
      else if (props[k] !== null && props[k] !== undefined && props[k] !== false) n.setAttribute(k, props[k] === true ? "" : props[k]);
    });
    (kids || []).forEach(function (c) {
      if (c === null || c === undefined) return;
      n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
  function na() { return el("span", { class: "na", text: "Data unavailable" }); }

  function api(path, opts) {
    opts = opts || {};
    var init = { method: opts.method || "GET", credentials: "same-origin", headers: {} };
    if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    return fetch(path, init).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var e = new Error(j.error || ("HTTP " + r.status)); e.status = r.status; throw e; }
        return j;
      });
    });
  }

  var IST = { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false };
  function fmt(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleString("en-IN", IST);
  }
  function num(v, d) { return v === null || v === undefined ? null : (Math.round(v * Math.pow(10, d)) / Math.pow(10, d)); }
  function numCell(v, d) { return el("td", { class: "num" }, [v === null || v === undefined ? na() : String(num(v, d))]); }

  function pill(aqi, level, category, title) {
    if (aqi === null || aqi === undefined) return na();
    return el("span", { class: "pill cp" + level, title: title || category }, [String(aqi)]);
  }

  function option(value, text, selected) { return el("option", { value: value, selected: selected ? true : null, text: text }); }

  function setBanner(text, warn) {
    var b = $("banner");
    b.textContent = text || "";
    b.className = "aq-banner" + (warn ? " warn" : "");
  }

  /* ------------------------------------------------------------------ data */
  function loadOverview(force) {
    if (S.overview && !force) return Promise.resolve(S.overview);
    return api("/api/aq/overview").then(function (o) {
      S.overview = o;
      var datalist = $("map-city-options");
      if (datalist) { clear(datalist); o.cities.forEach(function (c) { datalist.appendChild(el("option", { value: c.city })); }); }
      var have = o.cities.filter(function (c) { return c.available; }).length;
      if (!have) {
        setBanner("No air quality readings have been collected yet. The first hourly collection starts shortly after the server boots; refresh in a minute. An admin can also run it from Admin Settings.", true);
      } else if (o.last_update && (Date.now() - new Date(o.last_update).getTime()) > 3 * 3600 * 1000) {
        setBanner("The last successful data update was " + fmt(o.last_update) + " IST. Values shown may be out of date.", true);
      } else {
        setBanner("");
      }
      return o;
    }).catch(function () {
      setBanner("Could not load air quality data. Please try again shortly.", true);
      throw new Error("overview");
    });
  }
  function loadMe(force) {
    if (S.me && !force) return Promise.resolve(S.me);
    return api("/api/aq/me").then(function (m) { S.me = m; return m; });
  }
  function cityById(id) {
    return (S.overview ? S.overview.cities : []).filter(function (c) { return String(c.city_id) === String(id); })[0];
  }

  /* ------------------------------------------------------------------ dashboard */
  function kpi(label, valueNode, sub) {
    return el("div", { class: "kpi" }, [
      el("span", { class: "card-label", text: label }),
      el("p", { class: "val" }, [valueNode]),
      el("p", { class: "sub", text: sub || "" })
    ]);
  }

  function renderDashboard(o) {
    var k = clear($("kpis"));
    var n = o.national;
    k.appendChild(kpi("National AQI overview", n.aqi === null ? na() : pill(n.aqi, n.level, n.category), n.aqi === null ? "" : n.category + " \u00B7 average of " + n.cities_with_aqi + " cities"));
    var mp = o.most_polluted, cl = o.cleanest;
    k.appendChild(kpi("Most polluted city", mp ? el("span", {}, [mp.city + " ", pill(mp.aqi, mp.level, mp.category)]) : na(), mp ? mp.category : ""));
    k.appendChild(kpi("Cleanest city", cl ? el("span", {}, [cl.city + " ", pill(cl.aqi, cl.level, cl.category)]) : na(), cl ? cl.category : ""));
    k.appendChild(kpi("Cities monitored", el("span", { text: String(o.cities_monitored) }), o.cities.filter(function (c) { return c.available; }).length + " with data"));
    k.appendChild(kpi("Average PM2.5", o.avg_pm25 === null ? na() : el("span", { text: o.avg_pm25 + " \u00B5g/m\u00B3" }), "across cities with PM2.5"));
    k.appendChild(kpi("Last API update", o.last_update ? el("span", { text: fmt(o.last_update) }) : na(), "IST (successful collection)"));

    $("method-note").textContent = "AQI is calculated from pollutant concentrations using the CPCB National AQI breakpoints (maximum sub-index; needs at least 3 pollutants including PM2.5 or PM10). " +
      "Hourly concentrations are used, so values are indicative rather than the 24-hour/8-hour averaged official figure. Cities without enough data show \u201CData unavailable\u201D.";

    var rows = o.cities.filter(function (c) { return c.aqi !== null && c.aqi !== undefined; }).sort(function (a, b) { return b.aqi - a.aqi; });
    AQCharts.draw($("c-compare"), {
      type: "bar", yTitle: "AQI", emptyMessage: "No AQI data yet",
      labels: rows.map(function (c) { return c.city; }),
      datasets: [{ label: "AQI", data: rows.map(function (c) { return c.aqi; }), color: "#0369a1", pointColors: rows.map(function (c) { return c.color; }) }]
    });
    api("/api/aq/trend?hours=48").then(function (t) {
      AQCharts.draw($("c-national"), {
        type: "line", yTitle: "AQI", emptyMessage: "Trend appears after a few hourly readings",
        labels: t.points.map(function (p) { return fmt(p.t); }),
        datasets: [{ label: "Average AQI", data: t.points.map(function (p) { return p.aqi; }), color: "#0369a1" }]
      });
    }).catch(function () {});
  }

  /* --------------------------------------------------------------- live India map */
  function aqiColor(c) {
    if (!c || c.aqi === null || c.aqi === undefined) return "#64748b";
    return c.aqi <= 50 ? "#16a34a" : c.aqi <= 100 ? "#84cc16" : c.aqi <= 200 ? "#eab308" : c.aqi <= 300 ? "#f97316" : c.aqi <= 400 ? "#dc2626" : "#7e22ce";
  }
  function renderMap() {
    if (!S.overview) return;
    if (!window.L) { $("india-map").textContent = "The map library could not load. Check your internet connection or CDN access."; return; }
    if (!S.map) {
      S.map = L.map("india-map", { scrollWheelZoom: false }).setView([22.8, 79.0], 4.5);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap contributors" }).addTo(S.map);
    }
    S.mapMarkers.forEach(function (m) { S.map.removeLayer(m); }); S.mapMarkers = [];
    S.stationMarkers.forEach(function (m) { S.map.removeLayer(m); }); S.stationMarkers = [];
    S.overview.cities.forEach(function (c) {
      if (!c.latitude || !c.longitude) return;
      var marker = L.circleMarker([c.latitude, c.longitude], { radius: c.aqi == null ? 7 : 10, color: aqiColor(c), fillColor: aqiColor(c), fillOpacity: 0.82, weight: 2 }).addTo(S.map);
      var label = c.aqi == null ? "Data unavailable" : "AQI " + c.aqi + " · " + (c.category || "");
      marker.bindTooltip(c.city + " · " + label);
      marker.on("click", function () { showStations(c); });
      S.mapMarkers.push(marker);
    });
    setTimeout(function () { S.map.invalidateSize(); }, 120);
  }
  function showStations(city) {
    S.stationMarkers.forEach(function (m) { if (S.map) S.map.removeLayer(m); }); S.stationMarkers = [];
    $("station-results").textContent = "Loading station readings for " + city.city + "…";
    api("/api/aq/stations?city=" + encodeURIComponent(city.city_id)).then(function (r) {
      var box = clear($("station-results"));
      box.appendChild(el("p", { class: "note", text: city.city + ", " + city.state + " · city summary " + (city.aqi == null ? "unavailable" : "AQI " + city.aqi) }));
      if (!r.stations.length) { box.appendChild(el("p", { class: "note", text: "No station records have been collected for this city yet." })); return; }
      var table = el("div", { class: "table-wrap" });
      var t = el("table", {}); t.appendChild(el("thead", {}, [el("tr", {}, ["Station", "Source", "AQI", "PM2.5", "PM10", "Last reading"].map(function (x) { return el("th", { text: x }); }))]));
      var body = el("tbody", {});
      r.stations.forEach(function (st) { body.appendChild(el("tr", {}, [el("th", { text: st.name }), el("td", { text: st.source }), el("td", { text: st.aqi == null ? "Unavailable" : String(st.aqi) }), el("td", { text: st.pm25 == null ? "—" : String(st.pm25) }), el("td", { text: st.pm10 == null ? "—" : String(st.pm10) }), el("td", { text: st.reading_time ? fmt(st.reading_time) : "—" })])); });
      t.appendChild(body); table.appendChild(t); box.appendChild(table);
      r.stations.forEach(function (st) { if (st.latitude != null && st.longitude != null) { var mk = L.circleMarker([st.latitude, st.longitude], { radius: 6, color: aqiColor({aqi:st.aqi}), fillColor: aqiColor({aqi:st.aqi}), fillOpacity: .9 }).addTo(S.map); mk.bindTooltip(st.name + " · " + (st.aqi == null ? "AQI unavailable" : "AQI " + st.aqi)); S.stationMarkers.push(mk); } });
    }).catch(function (e) { $("station-results").textContent = "Station details unavailable: " + e.message; });
  }
  function renderQuality() {
    api("/api/aq/quality").then(function (q) {
      var n = $("quality-strip"); clear(n);
      [ ["Cities with readings", q.cities_with_readings + "/" + q.cities_total], ["Tracked stations", q.stations_tracked], ["Fresh readings", q.freshness_percent + "%"], ["Stale cities", q.stale_cities] ].forEach(function (v) { n.appendChild(el("div", { class: "quality-item" }, [el("span", { class: "note", text: v[0] }), el("strong", { text: String(v[1]) })])); });
      if (q.last_api_log) n.appendChild(el("p", { class: "note", text: "Last collector: " + q.last_api_log.source + " · " + q.last_api_log.status + " · " + fmt(q.last_api_log.created_at) }));
    }).catch(function () { $("quality-strip").textContent = "Data quality status is temporarily unavailable."; });
  }
  /* -------------------------------------------------------------- forecast */
  function renderForecast() {
    var sel = $("forecast-city"); fillCitySelect(sel, S.selected || defaultCity());
    if (!sel.value) { $("forecast-summary").textContent = "Select a city to view its forecast."; return; }
    $("forecast-summary").textContent = "Loading forecast…";
    var hours = Number($("forecast-hours").value || 24);
    api("/api/aq/forecast?city=" + encodeURIComponent(sel.value) + "&hours=" + hours).then(function (f) {
      $("forecast-source").textContent = f.source + " · model-based forecast, not a CPCB observation.";
      if (!f.available || !f.points.length) { AQCharts.draw($("c-forecast"), { type: "line", labels: [], datasets: [], emptyMessage: "Forecast unavailable for this city right now" }); $("forecast-summary").textContent = "No forecast data is currently available. Please try again later."; return; }
      AQCharts.draw($("c-forecast"), { type: "line", yTitle: "Indicative AQI", labels: f.points.map(function (p) { return fmt(p.t); }), datasets: [{ label: "Forecast AQI", data: f.points.map(function (p) { return p.aqi; }), color: "#0d9488" }] });
      var max = f.points.reduce(function (a,b) { return !a || b.aqi > a.aqi ? b : a; }, null), min = f.points.reduce(function (a,b) { return !a || b.aqi < a.aqi ? b : a; }, null);
      $("forecast-summary").textContent = "Forecast hours available: " + f.points.length + ". Highest predicted period: " + (max ? fmt(max.t) + " (AQI " + max.aqi + ")" : "—") + ". Lowest predicted period: " + (min ? fmt(min.t) + " (AQI " + min.aqi + ")" : "—") + ". Forecasts are estimates and can differ from station measurements.";
    }).catch(function (e) { $("forecast-summary").textContent = "Forecast request failed: " + e.message; });
  }
  function renderHealthPersonalised(a) {
    var profile = $("health-profile").value, activity = $("health-activity").value, city = a.city || {}, level = city.level;
    var msg = "Choose a city with a current reading to receive contextual precautions.";
    if (city.aqi != null) {
      var high = city.aqi > 200, elevated = city.aqi > 100;
      var audience = profile === "child" ? "Children can be more affected by air pollution" : profile === "older" ? "Older adults may be more sensitive to pollution" : profile === "asthma" || profile === "copd" ? "People with respiratory conditions may be more sensitive" : profile === "heart" ? "People with heart conditions may be more sensitive" : profile === "pregnancy" ? "Pregnant people should take extra care during polluted periods" : "For the general population";
      var activityText = activity === "school" ? "For the school commute, keep outdoor waiting time short and avoid outdoor play where possible." : activity === "run" || activity === "sports" ? "Postpone strenuous outdoor exercise and choose an indoor alternative." : activity === "walk" ? "Keep walks short and avoid busy roads or strenuous routes." : "Keep checking local conditions and reduce prolonged outdoor exertion when pollution is elevated.";
      msg = audience + ". Current available reading: AQI " + city.aqi + " (" + (city.category || "category unavailable") + "). " + (high ? "Avoid strenuous outdoor activity; keep necessary trips brief." : elevated ? "Reduce prolonged or strenuous outdoor activity, especially if symptoms occur." : "Conditions are comparatively better, but sensitive people should follow their clinician's advice and monitor symptoms.") + " " + activityText + " This is general guidance, not a medical assessment.";
    }
    $("health-personalised").textContent = msg;
  }
  function downloadHistoryCsv() {
    var id = $("hist-city").value, hours = $("hist-hours").value;
    if (!id) { toast("Select a city first."); return; }
    api("/api/aq/history?city=" + encodeURIComponent(id) + "&hours=" + encodeURIComponent(hours)).then(function (h) {
      var cols = ["timestamp_utc","aqi","pm25","pm10","no2","so2","co","o3"];
      var lines = [cols.join(",")].concat(h.points.map(function (p) { return cols.map(function (k) { var v = p[k]; return v == null ? "" : '"' + String(v).replace(/"/g, '""') + '"'; }).join(","); }));
      var blob = new Blob([lines.join("\r\n")], { type: "text/csv;charset=utf-8" }), url = URL.createObjectURL(blob), a = el("a", { href: url, download: "airguide-" + (h.city.name || "city").toLowerCase().replace(/[^a-z0-9]+/g,"-") + "-history.csv" });
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    }).catch(function (e) { toast("CSV export failed: " + e.message); });
  }
  function printHistoryReport() {
    var id = $("hist-city").value, hours = $("hist-hours").value;
    if (!id) { toast("Select a city first."); return; }
    var win = window.open("", "_blank");
    if (!win) { toast("Allow pop-ups to print the report."); return; }
    win.document.write("<!doctype html><html><head><title>AirGuide report</title><style>body{font:14px Arial,sans-serif;padding:28px;color:#172033}h1{color:#0f766e}table{border-collapse:collapse;width:100%;margin-top:18px}th,td{border:1px solid #ccd5df;padding:7px;text-align:left}th{background:#e6f4f1}.note{color:#475569}@media print{button{display:none}}</style></head><body><h1>AirGuide · Air Quality Report</h1><p>Preparing report…</p></body></html>");
    api("/api/aq/history?city=" + encodeURIComponent(id) + "&hours=" + encodeURIComponent(hours)).then(function (h) {
      var esc = function (v) { return String(v == null ? "" : v).replace(/[&<>\"]/g, function (c) { return ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c]; }); };
      var rows = h.points.map(function (p) { return "<tr>" + [p.t,p.aqi,p.pm25,p.pm10,p.no2,p.so2,p.co,p.o3].map(function (v) { return "<td>" + esc(v) + "</td>"; }).join("") + "</tr>"; }).join("");
      win.document.body.innerHTML = "<h1>AirGuide · Air Quality Report</h1><h2>" + esc(h.city.name) + ", " + esc(h.city.state) + "</h2><p class='note'>Period: last " + esc(hours) + " hours · Source: " + esc(h.source_label || "stored readings") + " · Generated: " + esc(new Date().toLocaleString()) + "</p><table><thead><tr><th>Timestamp (UTC)</th><th>AQI</th><th>PM2.5</th><th>PM10</th><th>NO2</th><th>SO2</th><th>CO</th><th>O3</th></tr></thead><tbody>" + (rows || "<tr><td colspan='8'>No readings available for this period.</td></tr>") + "</tbody></table><p class='note'>Missing values are blank, not estimated. Forecasts are not included. AQI values may be indicative depending on source and averaging completeness. General information only, not medical advice.</p><button onclick='window.print()'>Print / Save as PDF</button>";
      win.document.close();
    }).catch(function (e) { win.close(); toast("Report could not be generated: " + e.message); });
  }
  function enableBrowserAlerts() {
    if (!("Notification" in window)) { $("browser-alert-status").textContent = "Browser notifications are not supported."; return; }
    Notification.requestPermission().then(function (permission) {
      $("browser-alert-status").textContent = permission === "granted" ? "Enabled while this dashboard is open." : "Permission not granted.";
      if (permission === "granted") pollBrowserAlerts();
    });
  }
  function pollBrowserAlerts() {
    if (!window.Notification || Notification.permission !== "granted") return;
    api("/api/aq/overview").then(function (o) {
      var threshold = Number($("browser-alert-threshold").value || 150);
      o.cities.forEach(function (c) {
        if (c.aqi == null || c.stale || c.aqi < threshold) return;
        var key = "airguide-alert-" + c.city_id + "-" + c.reading_time + "-" + threshold;
        if (sessionStorage.getItem(key)) return;
        new Notification("AirGuide · " + c.city + " AQI " + c.aqi, { body: (c.category || "Elevated pollution") + ". Consider reducing prolonged outdoor exertion." });
        sessionStorage.setItem(key, "1");
      });
    }).catch(function () {});
  }

  /* ------------------------------------------------------------------ city table */
  function renderCities(o) {
    var sel = $("f-state");
    if (sel.options.length <= 1) {
      var states = {};
      o.cities.forEach(function (c) { states[c.state] = 1; });
      Object.keys(states).sort().forEach(function (s) { sel.appendChild(option(s, s)); });
    }
    var q = $("f-search").value.trim().toLowerCase(), st = sel.value, sort = $("f-sort").value;
    var list = o.cities.filter(function (c) {
      return (!q || c.city.toLowerCase().indexOf(q) !== -1) && (!st || c.state === st);
    });
    list.sort(function (a, b) {
      if (sort === "name") return a.city.localeCompare(b.city);
      var av = a.aqi === null || a.aqi === undefined ? null : a.aqi, bv = b.aqi === null || b.aqi === undefined ? null : b.aqi;
      if (av === null && bv === null) return a.city.localeCompare(b.city);
      if (av === null) return 1;
      if (bv === null) return -1;
      return sort === "low" ? av - bv : bv - av;
    });
    var tb = clear(document.querySelector("#city-table tbody"));
    if (!list.length) { tb.appendChild(el("tr", {}, [el("td", { colspan: "10", text: "No cities match your filters." })])); return; }
    list.forEach(function (c) {
      var aqiCell = el("td", {}, [c.aqi === null || c.aqi === undefined ? na() : pill(c.aqi, c.level, c.category, (c.aqi_source || "") + " \u00B7 " + (c.source_label || ""))]);
      if (c.stale && c.available) aqiCell.appendChild(el("span", { class: "stale-tag", text: "stale" }));
      tb.appendChild(el("tr", {}, [
        el("th", { scope: "row", text: c.city }), el("td", { text: c.state }), aqiCell,
        numCell(c.pm25, 1), numCell(c.pm10, 1), numCell(c.no2, 1),
        el("td", {}, [c.category ? c.category : na()]),
        el("td", {}, [fpBadge(c.fingerprint)]),
        el("td", {}, [c.health_risk ? c.health_risk : na()]),
        el("td", {}, [c.reading_time ? fmt(c.reading_time) : na()])
      ]));
    });
  }

  /* ------------------------------------------------------------------ city pickers + advisories */
  function fillCitySelect(select, keep) {
    var cur = keep || S.selected;
    clear(select);
    select.appendChild(option("", "Select a city", !cur));
    S.overview.cities.forEach(function (c) { select.appendChild(option(String(c.city_id), c.city + ", " + c.state, String(c.city_id) === String(cur))); });
  }
  function defaultCity() {
    if (S.selected) return S.selected;
    var p = (S.me && S.me.preferences) || [];
    var order = ["current", "office", "home", "family"];
    for (var i = 0; i < order.length; i++) {
      var f = p.filter(function (x) { return x.kind === order[i]; })[0];
      if (f) return String(f.city_id);
    }
    var saved = lsGet();
    return saved && cityById(saved) ? saved : "";
  }
  var CITY_SELECTS = ["adv-city-emp", "adv-city-fam", "adv-city-act", "morning-city", "esc-city", "lens-city"];
  function setSelected(v) {
    S.selected = v;
    lsSet(v);
    CITY_SELECTS.forEach(function (id) { fillCitySelect($(id), v); });
    if (!$("employee").hidden || !$("family").hidden || !$("activities").hidden) refreshAdvisory();
    if (!$("dashboard").hidden) renderMorning();
    if (!$("escape").hidden) renderEscape();
    if (!$("lens").hidden) lensCityChanged();
  }
  function familyExtras(a) {
    var box = el("div", { class: "panel" });
    var row = el("div", { class: "chip-row" }, [el("strong", { text: "Source signature:" }), fpBadge(a.city.fingerprint)]);
    box.appendChild(row);
    box.appendChild(winCard(a.window_advisor));
    box.appendChild(el("div", { class: "inline" }, [el("button", { type: "button", class: "btn", text: "Download Family Safety Card", disabled: a.city.available ? null : true,
      onclick: function () { shareCardFor(a.city.city_id); } })]));
    return box;
  }

  function cityHeader(c) {
    return el("div", { class: "panel" }, [
      el("div", { class: "hd" }, [
        el("h3", { text: c.city + ", " + c.state }),
        c.aqi === null || c.aqi === undefined ? na() : pill(c.aqi, c.level, c.category)
      ]),
      el("p", { text: c.aqi === null || c.aqi === undefined ? "No AQI is available for this city right now, so no advisory can be given." : c.category + " air. " + c.health_risk + "." }),
      el("p", { class: "note", text: c.reading_time ? "Reading from " + fmt(c.reading_time) + " IST \u00B7 " + (c.source_label || "") : "" })
    ]);
  }

  var advCache = {};
  function loadAdvisory() {
    if (!S.selected) return Promise.resolve(null);
    return api("/api/aq/advisory?city=" + encodeURIComponent(S.selected)).then(function (a) { advCache = a; return a; });
  }

  function adviceCards(items, wanted) {
    var grid = el("div", { class: "adv-grid" });
    items.forEach(function (it) {
      grid.appendChild(el("div", { class: "card" }, [el("h3", { text: it.title }), el("p", { text: it.body })]));
    });
    return grid;
  }

  function refreshAdvisory() {
    var emp = clear($("adv-employee")), fam = clear($("adv-family")), tb = clear(document.querySelector("#act-table tbody"));
    if (!S.selected) {
      var hint = "Choose a city above (or save your locations) to see advice for its current air quality.";
      emp.appendChild(el("p", { class: "note", text: hint })); fam.appendChild(el("p", { class: "note", text: hint }));
      tb.appendChild(el("tr", {}, [el("td", { colspan: "5", text: hint })]));
      return;
    }
    loadAdvisory().then(function (a) {
      if (!a) return;
      var ok = a.advice.available;
      renderHealthPersonalised(a);
      [emp, fam].forEach(function (box) { box.appendChild(cityHeader(a.city)); });
      if (!ok) { tb.appendChild(el("tr", {}, [el("td", { colspan: "5" }, [na()])])); return; }
      emp.appendChild(adviceCards(a.advice.employee));
      fam.appendChild(familyExtras(a));
      fam.appendChild(adviceCards(a.advice.family));
      var aud = $("act-aud").value;
      var acts = a.advice.activities.filter(function (x) { return !aud || x.audience === aud; });
      acts.forEach(function (x) {
        tb.appendChild(el("tr", {}, [
          el("th", { scope: "row", text: x.activity }),
          el("td", {}, [el("span", { class: "risk-" + String(x.risk).split(" ")[0], text: x.risk })]),
          el("td", { text: x.alternative }), el("td", { text: x.explanation }),
          el("td", { text: AUD_LABEL[x.audience] || x.audience })
        ]));
      });
    }).catch(function () { emp.appendChild(el("p", { class: "note", text: "Could not load advice. Please try again." })); });
  }

  /* ------------------------------------------------------------------ my locations */
  function renderPrefs() {
    var form = clear($("prefs-form")), list = clear($("prefs-list"));
    var kind = el("select", { id: "pref-kind", "aria-label": "Location type" }, Object.keys(KIND_LABEL).map(function (k) { return option(k, KIND_LABEL[k]); }));
    var city = el("select", { id: "pref-city", "aria-label": "City" });
    city.appendChild(option("", "Select a city"));
    S.overview.cities.forEach(function (c) { city.appendChild(option(String(c.city_id), c.city + ", " + c.state)); });
    var msg = el("span", { class: "note", role: "status" });
    var add = el("button", { type: "button", class: "btn", text: "Save location", onclick: function () {
      if (!city.value) { msg.textContent = "Choose a city first."; return; }
      api("/api/aq/me/prefs", { method: "POST", body: { kind: kind.value, city_id: Number(city.value), action: "add" } })
        .then(function () { return loadMe(true); }).then(function () { renderPrefs(); if (!S.selected) setSelected(defaultCity()); })
        .catch(function (e) { msg.textContent = e.message; });
    } });
    form.appendChild(el("label", {}, ["Type", kind]));
    form.appendChild(el("label", {}, ["City", city]));
    form.appendChild(add); form.appendChild(msg);

    var prefs = (S.me && S.me.preferences) || [];
    if (!prefs.length) { list.appendChild(el("p", { class: "note", text: "No saved locations yet. Example: work in Gurugram (Office) with family in Delhi (Family)." })); return; }
    prefs.forEach(function (p) {
      var l = p.latest;
      list.appendChild(el("div", { class: "card" }, [
        el("div", { class: "hd" }, [el("h3", { text: KIND_LABEL[p.kind] + ": " + p.city }), l && l.aqi !== null && l.aqi !== undefined ? pill(l.aqi, l.level, l.category) : na()]),
        el("p", { text: l && l.category ? l.category + ". " + l.health_risk : "No current AQI available." }),
        el("div", { class: "chip-row" }, [winChip(p.window_advisor), l ? fpBadge(l.fingerprint) : null]),
        el("div", { class: "inline" }, [
          el("button", { type: "button", class: "btn", text: "Download Family Safety Card", disabled: l && l.aqi !== null && l.aqi !== undefined ? null : true, onclick: function () { shareCardFor(p.city_id); } }),
          el("button", { type: "button", class: "btn secondary", text: "View advice", onclick: function () { setSelected(String(p.city_id)); window.scrollTo(0, 0); } }),
          el("button", { type: "button", class: "link-btn", text: "Remove", onclick: function () {
            api("/api/aq/me/prefs", { method: "POST", body: { kind: p.kind, city_id: p.city_id, action: "remove" } })
              .then(function () { return loadMe(true); }).then(renderPrefs);
          } })
        ])
      ]));
    });
  }

  /* ------------------------------------------------------------------ history */
  function renderHistory() {
    var sel = $("hist-city");
    if (!sel.options.length) {
      fillCitySelect(sel, S.selected || "");
    }
    if (!sel.value) {
      ["c-hourly", "c-daily", "c-pm", "c-gases"].forEach(function (id) { AQCharts.draw($(id), { type: "line", labels: [], datasets: [], emptyMessage: "Select a city" }); });
      $("hist-source").textContent = "Select a city to see its stored readings.";
      return;
    }
    api("/api/aq/history?city=" + encodeURIComponent(sel.value) + "&hours=" + $("hist-hours").value).then(function (h) {
      $("hist-source").textContent = h.source_label ? "Source: " + h.source_label + ". Charts show stored hourly readings; gaps mean no reading was collected." : "No stored readings for this city in the selected period.";
      var lab = h.points.map(function (p) { return fmt(p.t); });
      var series = function (key) { return h.points.map(function (p) { return p[key] === undefined ? null : p[key]; }); };
      AQCharts.draw($("c-hourly"), { type: "line", yTitle: "AQI", labels: lab, datasets: [{ label: "AQI", data: series("aqi"), color: "#0369a1" }] });
      AQCharts.draw($("c-daily"), { type: "bar", yTitle: "AQI", labels: h.daily.map(function (d) { return d.date; }),
        datasets: [{ label: "Average AQI", data: h.daily.map(function (d) { return d.avg_aqi; }), color: "#0369a1" },
                   { label: "Maximum AQI", data: h.daily.map(function (d) { return d.max_aqi; }), color: "#e00000" }] });
      AQCharts.draw($("c-pm"), { type: "line", yTitle: "\u00B5g/m\u00B3", labels: lab,
        datasets: [{ label: "PM2.5", data: series("pm25"), color: "#e00000" }, { label: "PM10", data: series("pm10"), color: "#0369a1" }] });
      AQCharts.draw($("c-gases"), { type: "line", yTitle: "\u00B5g/m\u00B3", labels: lab,
        datasets: [{ label: "NO2", data: series("no2"), color: "#7e0023" }, { label: "SO2", data: series("so2"), color: "#ff7c00" }, { label: "O3", data: series("o3"), color: "#00b050" }] });
    }).catch(function (e) { $("hist-source").textContent = "Could not load history: " + e.message; });
  }

  /* ------------------------------------------------------------------ alerts */
  function renderAlerts() {
    var mine = $("alerts-mine").checked;
    var ids = mine ? ((S.me && S.me.preferences) || []).map(function (p) { return p.city_id; }) : [];
    var ul = clear($("alert-list"));
    if (mine && !ids.length) { ul.appendChild(el("li", { text: "Save at least one location under Employee Advisory to filter by your locations." })); return; }
    api("/api/aq/alerts?limit=60" + (ids.length ? "&cities=" + ids.join(",") : "")).then(function (r) {
      if (!r.alerts.length) { ul.appendChild(el("li", { text: "No alerts yet. Alerts appear when AQI crosses a configured threshold or rises sharply within an hour." })); return; }
      r.alerts.forEach(function (a) {
        var lvl = 0;
        var thr = a.aqi;
        lvl = thr <= 50 ? 0 : thr <= 100 ? 1 : thr <= 200 ? 2 : thr <= 300 ? 3 : thr <= 400 ? 4 : 5;
        ul.appendChild(el("li", { class: "bl" + lvl }, [
          el("strong", { text: (a.alert_type === "jump" ? "Sharp rise \u00B7 " : "Threshold crossed \u00B7 ") + a.city }),
          el("p", { text: a.message }),
          el("time", { datetime: a.reading_time, text: fmt(a.reading_time) + " IST" })
        ]));
      });
    });
    api("/api/aq/config").then(function (c) {
      $("alert-rules").textContent = "Alerts are raised when a city's AQI rises past " + c.thresholds.join(", ") + ", or jumps sharply within an hour. Administrators can change these rules.";
    }).catch(function () {});
  }

  /* ------------------------------------------------------------------ admin */
  function adminMsg(box, text, bad) { box.textContent = text; box.className = bad ? "msg-bad" : "msg-ok"; }

  function renderAdmin() {
    var body = clear($("admin-body"));
    api("/api/aq/config").then(function (c) {
      S.config = c;
      if (!c.admin_enabled) {
        body.appendChild(el("p", { class: "panel", text: "The admin area is disabled because the ADMIN_PASSWORD environment variable is not set on the server." }));
        return;
      }
      if (!c.is_admin) { return renderLogin(body); }
      renderAdminPanel(body);
    });
  }

  function renderLogin(body) {
    var msg = el("p", { role: "alert" });
    var pw = el("input", { type: "password", id: "admin-pw", autocomplete: "current-password", required: true });
    var go = function () {
      api("/api/aq/admin/login", { method: "POST", body: { password: pw.value } })
        .then(function () { renderAdmin(); }).catch(function (e) { adminMsg(msg, e.message, true); });
    };
    body.appendChild(el("div", { class: "panel" }, [
      el("h3", { text: "Administrator sign-in" }),
      el("div", { class: "toolbar" }, [el("label", {}, ["Password", pw]), el("button", { type: "button", class: "btn", text: "Sign in", onclick: go })]), msg
    ]));
    pw.addEventListener("keydown", function (e) { if (e.key === "Enter") go(); });
  }

  function table(headers, rows) {
    return el("div", { class: "table-wrap" }, [el("table", {}, [
      el("thead", {}, [el("tr", {}, headers.map(function (h) { return el("th", { scope: "col", text: h }); }))]),
      el("tbody", {}, rows.map(function (r) { return el("tr", {}, r.map(function (c) { return el("td", {}, [c === null || c === undefined ? "" : (typeof c === "object" ? c : String(c))]); })); }))
    ])]);
  }
  function block(title, open) { var d = el("details", { class: "admin-block", open: open ? true : null }); d.appendChild(el("summary", { text: title })); return d; }
  function statusTag(s) { return el("span", { class: s === "ok" ? "status-ok" : "status-bad", text: s }); }

  function renderAdminPanel(body) {
    Promise.all([api("/api/aq/admin/status"), api("/api/aq/admin/cities"), api("/api/aq/admin/advisories")]).then(function (res) {
      var st = res[0], cities = res[1].cities, adv = res[2].items;
      clear(body);
      var top = el("div", { class: "toolbar" }, [
        el("button", { type: "button", class: "btn secondary", text: "Sign out", onclick: function () { api("/api/aq/admin/logout", { method: "POST", body: {} }).then(renderAdmin); } })
      ]);
      body.appendChild(top);

      /* 1. API status + credentials */
      var b1 = block("API connection status and credentials", true), m1 = el("p", { role: "status" });
      var srcRows = ["open-meteo", "openaq"].map(function (k) {
        var l = st.sources[k];
        return [k, l ? statusTag(l.status) : el("span", { class: "na", text: "never run" }), l ? fmt(l.created_at) : "", l ? (l.message || "") : (k === "openaq" && !st.openaq_key.configured ? "No API key configured" : "")];
      });
      b1.appendChild(table(["Source", "Last run", "When (IST)", "Message"], srcRows));
      b1.appendChild(el("p", { class: "note", text: "Readings stored: " + st.readings_total + ". Last successful update: " + (st.last_update ? fmt(st.last_update) + " IST" : "none yet") + "." }));
      var keyIn = el("input", { type: "password", id: "oaq-key", autocomplete: "off", placeholder: st.openaq_key.configured ? st.openaq_key.masked : "Paste OpenAQ API key" });
      b1.appendChild(el("div", { class: "toolbar" }, [
        el("label", {}, ["OpenAQ API key" + (st.openaq_key.from_env ? " (set by environment variable)" : ""), keyIn]),
        el("button", { type: "button", class: "btn", text: "Save key", onclick: function () {
          api("/api/aq/admin/settings", { method: "POST", body: { openaq_api_key: keyIn.value } }).then(function () { keyIn.value = ""; adminMsg(m1, "Key saved.", false); }).catch(function (e) { adminMsg(m1, e.message, true); });
        } }),
        el("button", { type: "button", class: "btn secondary", text: "Remove stored key", onclick: function () {
          api("/api/aq/admin/settings", { method: "POST", body: { clear_openaq_key: true } }).then(function () { adminMsg(m1, "Stored key removed.", false); }).catch(function (e) { adminMsg(m1, e.message, true); });
        } })
      ]));
      var useOM = el("input", { type: "checkbox", checked: st.settings.use_openmeteo === "1" ? true : null });
      var useOA = el("input", { type: "checkbox", checked: st.settings.use_openaq === "1" ? true : null });
      b1.appendChild(el("div", { class: "toolbar" }, [
        el("label", {}, [useOM, "Use Open-Meteo (no key needed, model estimates)"]),
        el("label", {}, [useOA, "Use OpenAQ (station data, needs key)"]),
        el("button", { type: "button", class: "btn secondary", text: "Save sources", onclick: function () {
          api("/api/aq/admin/settings", { method: "POST", body: { use_openmeteo: useOM.checked, use_openaq: useOA.checked } }).then(function () { adminMsg(m1, "Saved.", false); });
        } }),
        el("button", { type: "button", class: "btn", text: "Run collection now", onclick: function (e) {
          e.target.disabled = true; adminMsg(m1, "Collecting\u2026", false);
          api("/api/aq/admin/collect", { method: "POST", body: {} }).then(function (r) {
            adminMsg(m1, "Done: " + r.stored + " new readings. " + Object.keys(r.sources).map(function (k) { return k + ": " + r.sources[k].status; }).join(", "), false);
            S.overview = null; renderAdminPanel(body);
          }).catch(function (er) { adminMsg(m1, er.message, true); e.target.disabled = false; });
        } })
      ]));
      b1.appendChild(m1);
      body.appendChild(b1);

      /* 2. Notification settings */
      var b2 = block("Notification rules", false), m2 = el("p", { role: "status" });
      var thr = el("input", { type: "text", value: JSON.parse(st.settings.alert_thresholds).join(", "), "aria-label": "Thresholds" });
      var alertsOn = el("input", { type: "checkbox", checked: st.settings.alerts_enabled === "1" ? true : null });
      var jumpOn = el("input", { type: "checkbox", checked: st.settings.jump_alert_enabled === "1" ? true : null });
      var jumpPts = el("input", { type: "number", min: "10", max: "500", value: st.settings.jump_alert_points });
      var stale = el("input", { type: "number", min: "1", max: "48", value: st.settings.stale_hours });
      b2.appendChild(el("div", { class: "toolbar" }, [
        el("label", {}, [alertsOn, "Alerts enabled"]),
        el("label", {}, ["AQI thresholds (comma separated)", thr]),
        el("label", {}, [jumpOn, "Alert on sharp hourly rise"]),
        el("label", {}, ["Rise of at least (AQI points/hour)", jumpPts]),
        el("label", {}, ["Mark data stale after (hours)", stale]),
        el("button", { type: "button", class: "btn", text: "Save rules", onclick: function () {
          var ts = thr.value.split(",").map(function (x) { return parseInt(x.trim(), 10); }).filter(function (x) { return !isNaN(x); });
          api("/api/aq/admin/settings", { method: "POST", body: { alerts_enabled: alertsOn.checked, alert_thresholds: ts, jump_alert_enabled: jumpOn.checked, jump_alert_points: Number(jumpPts.value), stale_hours: Number(stale.value) } })
            .then(function () { adminMsg(m2, "Saved.", false); }).catch(function (e) { adminMsg(m2, e.message, true); });
        } })
      ]));
      b2.appendChild(m2); body.appendChild(b2);

      /* 3. Cities */
      var b3 = block("Monitored cities", false), m3 = el("p", { role: "status" });
      b3.appendChild(table(["City", "State", "Latitude", "Longitude", "Active"], cities.map(function (c) {
        return [c.name, c.state, c.latitude, c.longitude, el("button", { type: "button", class: "btn secondary", text: c.active ? "Active \u2013 disable" : "Disabled \u2013 enable", onclick: function () {
          api("/api/aq/admin/cities", { method: "PUT", body: { id: c.id, active: !c.active } }).then(function () { S.overview = null; renderAdminPanel(body); });
        } })];
      })));
      var f = { name: el("input", { type: "text", maxlength: "60" }), state: el("input", { type: "text", maxlength: "60" }), lat: el("input", { type: "number", step: "any" }), lon: el("input", { type: "number", step: "any" }) };
      b3.appendChild(el("div", { class: "toolbar" }, [
        el("label", {}, ["City name", f.name]), el("label", {}, ["State", f.state]), el("label", {}, ["Latitude", f.lat]), el("label", {}, ["Longitude", f.lon]),
        el("button", { type: "button", class: "btn", text: "Add city", onclick: function () {
          api("/api/aq/admin/cities", { method: "POST", body: { name: f.name.value, state: f.state.value, latitude: Number(f.lat.value), longitude: Number(f.lon.value) } })
            .then(function () { S.overview = null; renderAdminPanel(body); }).catch(function (e) { adminMsg(m3, e.message, true); });
        } })
      ]));
      b3.appendChild(m3); body.appendChild(b3);

      /* 4. Logs and failures */
      var b4 = block("Hourly data collection logs", false);
      b4.appendChild(table(["When (IST)", "Source", "Endpoint", "Status", "Records", "ms", "Message"], st.logs.slice(0, 50).map(function (l) {
        return [fmt(l.created_at), l.source, l.endpoint, statusTag(l.status), l.records, l.duration_ms, l.message];
      })));
      body.appendChild(b4);
      var b5 = block("API failures", false);
      b5.appendChild(st.failures.length ? table(["When (IST)", "Source", "Endpoint", "HTTP", "Message"], st.failures.map(function (l) { return [fmt(l.created_at), l.source, l.endpoint, l.http_status || "", l.message]; })) : el("p", { class: "note", text: "No failures recorded." }));
      body.appendChild(b5);

      /* 5. Advisory content */
      var b6 = block("Advisory content", false), m6 = el("p", { role: "status" });
      var KL = { employee: "Employee advisory", family: "Family & children", activity: "Alternative activities" };
      b6.appendChild(el("p", { class: "note", text: "Edit wording shown to users. Keep guidance general: no diagnoses and no medicine advice." }));
      adv.forEach(function (a) {
        var t = el("input", { type: "text", value: a.title, maxlength: "120", "aria-label": "Title" });
        var bd = el("textarea", { maxlength: "1200", "aria-label": "Text" }); bd.value = a.body;
        var ac = el("input", { type: "checkbox", checked: a.active ? true : null });
        b6.appendChild(el("div", { class: "admin-row" }, [
          el("strong", { text: KL[a.kind] + " \u00B7 " + (AUD_LABEL[a.audience] || a.audience) + " \u00B7 AQI levels " + a.level_min + "\u2013" + a.level_max + " (0 = Good \u2026 5 = Severe)" }),
          t, bd,
          el("div", { class: "inline" }, [el("label", {}, [ac, " Active"]), el("button", { type: "button", class: "btn secondary", text: "Save", onclick: function () {
            api("/api/aq/admin/advisories", { method: "PUT", body: { id: a.id, title: t.value, body: bd.value, active: ac.checked } })
              .then(function () { adminMsg(m6, "Saved #" + a.id, false); }).catch(function (e) { adminMsg(m6, e.message, true); });
          } })])
        ]));
      });
      b6.appendChild(m6); body.appendChild(b6);

      /* 6. Historical records */
      var b7 = block("Historical pollution records", false);
      var hc = el("select", { "aria-label": "City" }); hc.appendChild(option("", "All cities"));
      cities.forEach(function (c) { hc.appendChild(option(String(c.id), c.name)); });
      var holder = el("div");
      var loadRec = function () {
        api("/api/aq/admin/readings?limit=100" + (hc.value ? "&city=" + hc.value : "")).then(function (r) {
          clear(holder).appendChild(table(["Reading time (IST)", "City", "Source", "AQI", "AQI basis", "PM2.5", "PM10", "NO2", "SO2", "CO mg/m3", "O3"], r.readings.map(function (x) {
            return [fmt(x.reading_time), x.city, x.source, x.aqi === null ? "n/a" : x.aqi, x.aqi_source || "n/a", x.pm25, x.pm10, x.no2, x.so2, x.co, x.o3];
          })));
        });
      };
      hc.addEventListener("change", loadRec);
      b7.appendChild(el("div", { class: "toolbar" }, [el("label", {}, ["City", hc]), el("button", { type: "button", class: "btn secondary", text: "Load latest 100", onclick: loadRec })]));
      b7.appendChild(holder); body.appendChild(b7);
    }).catch(function (e) {
      if (e.status === 401) return renderAdmin();
      clear(body).appendChild(el("p", { class: "panel", text: "Could not load admin data: " + e.message }));
    });
  }

  /* ================================================================== creative features */
  var LS_KEY = "aq_city";  // only a city id, kept in this browser (never sent to the server)
  function lsGet() { try { return window.localStorage.getItem(LS_KEY) || ""; } catch (e) { return ""; } }
  function lsSet(v) { try { window.localStorage.setItem(LS_KEY, v || ""); } catch (e) { /* storage blocked */ } }
  function clock(iso) {
    var d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "numeric", minute: "2-digit", hour12: true }).toUpperCase();
  }
  var toastTimer = 0;
  function toast(msg) {
    var t = $("toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove("show"); }, 4500);
  }

  /* ---- source signature pill (keyboard accessible, tap to read why) */
  function fpBadge(fp) {
    var wrap = el("div", {});
    var key = (fp && fp.key) || "unavailable";
    if (key === "unavailable") { wrap.appendChild(el("span", { class: "fp fp-unavailable", text: "Data unavailable" })); return wrap; }
    var note = el("p", { class: "fp-note", hidden: true, text: fp.explanation + " " + fp.primary_action });
    var btn = el("button", {
      type: "button", class: "fp fp-" + key, text: fp.badge, title: fp.explanation, "aria-expanded": "false",
      "aria-label": "Source signature: " + fp.signature + ". Press to show or hide the explanation.",
      onclick: function () {
        var closed = note.hasAttribute("hidden");
        if (closed) note.removeAttribute("hidden"); else note.setAttribute("hidden", "");
        btn.setAttribute("aria-expanded", closed ? "true" : "false");
      }
    });
    wrap.appendChild(btn); wrap.appendChild(note);
    return wrap;
  }

  /* ---- Smart Window status */
  function winCard(w) {
    w = w || { status: "UNAVAILABLE", headline: "Data unavailable", reason: "" };
    return el("div", { class: "win win-" + w.status, role: "group", "aria-label": "Smart Window status: " + w.headline }, [
      el("span", { class: "card-label", text: "Smart Window Status" }),
      el("p", { class: "big", text: w.headline }),
      el("p", { text: w.reason })
    ]);
  }
  function winChip(w) { return el("span", { class: "win-chip win-" + ((w && w.status) || "UNAVAILABLE"), text: "Windows: " + ((w && w.headline) || "Data unavailable") }); }

  /* ---- cigarette meter */
  function cigIcons(v) {
    var box = el("div", { class: "cigs", role: "img", "aria-label": v + " cigarettes per day equivalent" });
    var shown = Math.min(v, 10), full = Math.floor(shown), frac = shown - full;
    for (var i = 0; i < full; i++) box.appendChild(el("span", { class: "cig" }));
    if (frac > 0.04) { var part = el("span", { class: "cig part" }); part.style.width = Math.max(0.5, 1.9 * frac).toFixed(2) + "rem"; box.appendChild(part); }
    if (v > 10) box.appendChild(el("span", { class: "note", text: "(showing 10)" }));
    return box;
  }

  /* ---- Today's Breathability Card */
  var morningToken = 0, lastMorning = null;
  function renderMorning() {
    var body = clear($("morning-body")), share = $("morning-share");
    fillCitySelect($("morning-city"), S.selected);
    share.hidden = true;
    if (!S.selected) { body.appendChild(el("p", { class: "note", text: "Choose a city to see today\u2019s breathability, the best time to step out and a shareable family card." })); return; }
    var token = ++morningToken;
    body.appendChild(el("p", { class: "note", text: "Loading\u2026" }));
    api("/api/aq/morning?city=" + encodeURIComponent(S.selected)).then(function (m) {
      if (token !== morningToken) return;
      lastMorning = m;
      clear(body);
      var c = m.city;
      body.appendChild(el("p", {}, [
        el("strong", { text: c.city + ", " + c.state + "  " }),
        c.aqi === null || c.aqi === undefined ? na() : pill(c.aqi, c.level, c.category),
        c.aqi === null || c.aqi === undefined ? "" : "  " + c.category,
        c.reading_time ? el("span", { class: "note", text: "  \u00B7 reading " + fmt(c.reading_time) + " IST" }) : null
      ]));
      var grid = el("div", { class: "morning-grid" });
      var cig = m.cigarette_equivalent;
      grid.appendChild(el("div", { class: "morning-tile" }, [
        el("span", { class: "card-label", text: "Cigarette equivalent" }),
        cig === null || cig === undefined ? el("p", {}, [na()]) : el("p", { class: "big", text: "\u2248 " + cig + " cigarettes/day" }),
        cig === null || cig === undefined ? null : cigIcons(cig),
        el("p", { class: "note", text: "If you breathed this outdoor air unfiltered all day. A rule of thumb (22 \u00B5g/m\u00B3 PM2.5 \u2248 1 cigarette), not a medical measurement." })
      ]));
      var bw = m.best_window, wh = m.worst_hour;
      var basis = m.forecast_basis === "open-meteo-forecast" ? "From the Open-Meteo forecast." : m.forecast_basis === "yesterday-pattern" ? "Estimated from yesterday\u2019s pattern (forecast unavailable)." : "";
      grid.appendChild(el("div", { class: "morning-tile" }, [
        el("span", { class: "card-label", text: "Best 2-hour window to step out" }),
        bw ? el("p", { class: "big", text: clock(bw.start) + " \u2013 " + clock(bw.end) }) : el("p", {}, [na()]),
        bw ? el("p", { text: "Est. AQI " + bw.avg_aqi }) : null,
        el("p", { class: "note", text: bw ? basis : "Not enough forecast or history yet." })
      ]));
      grid.appendChild(el("div", { class: "morning-tile" }, [
        el("span", { class: "card-label", text: "Worst hour to avoid" }),
        wh ? el("p", { class: "big", text: clock(wh.t) }) : el("p", {}, [na()]),
        wh ? el("p", { text: "Est. AQI " + wh.aqi }) : null
      ]));
      grid.appendChild(el("div", { class: "morning-tile" }, [el("span", { class: "card-label", text: "Source signature" }), fpBadge(m.fingerprint)]));
      body.appendChild(grid);
      body.appendChild(winCard(m.window_advisor));
      share.hidden = !m.available;
    }).catch(function () { if (token === morningToken) { clear(body).appendChild(el("p", { class: "note", text: "Could not load the breathability card. Please try again." })); } });
  }

  /* ---- Where Can I Breathe? */
  function renderEscape() {
    var body = clear($("esc-body"));
    fillCitySelect($("esc-city"), S.selected);
    if (!S.selected) { body.appendChild(el("p", { class: "note", text: "Choose your city to find the cleanest air nearby." })); return; }
    api("/api/aq/escape?city=" + encodeURIComponent(S.selected)).then(function (r) {
      var me = r.selected;
      body.appendChild(el("div", { class: "panel" }, [
        el("div", { class: "hd" }, [el("h3", { text: me.city + ", " + me.state }), me.aqi === null || me.aqi === undefined ? na() : pill(me.aqi, me.level, me.category)]),
        el("p", { text: r.message || (r.escapes.length ? "Cleaner monitored cities right now:" : "") })
      ]));
      if (!r.escapes.length) return;
      var rows = [{ city: me.city, aqi: me.aqi, level: me.level }].concat(r.escapes);
      var chart = el("div", { class: "panel", role: "group", "aria-label": "AQI comparison" }, [el("h3", { text: "Visual comparison (AQI)" })]);
      rows.forEach(function (x) {
        var bar = el("span", { class: "cp" + x.level });
        bar.style.width = Math.max(2, Math.min(100, x.aqi / 5)) + "%";
        chart.appendChild(el("div", { class: "esc-bar-row" }, [el("span", { text: x.city }), el("div", { class: "esc-bar", role: "img", "aria-label": x.city + " AQI " + x.aqi }, [bar]), el("strong", { text: String(x.aqi) })]));
      });
      body.appendChild(chart);
      var grid = el("div", { class: "adv-grid" });
      r.escapes.forEach(function (e) {
        grid.appendChild(el("div", { class: "card" }, [
          el("div", { class: "hd" }, [el("h3", { text: e.city + ", " + e.state }), pill(e.aqi, e.level, e.category)]),
          el("p", { text: e.message }),
          el("p", { class: "note", text: "About " + e.distance_km + " km away in a straight line \u00B7 " + e.category + (e.stale ? " \u00B7 reading is stale" : "") })
        ]));
      });
      body.appendChild(grid);
    }).catch(function () { body.appendChild(el("p", { class: "note", text: "Could not load the comparison. Please try again." })); });
  }

  /* ================================================================== Family Air Shield PNG card */
  function rr(ctx, x, y, w, h, r) {
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }
  function fit(ctx, text, maxW) {
    if (ctx.measureText(text).width <= maxW) return text;
    while (text.length > 1 && ctx.measureText(text + "\u2026").width > maxW) text = text.slice(0, -1);
    return text + "\u2026";
  }
  function wrap(ctx, text, x, y, maxW, lh, maxLines) {
    var words = String(text).split(/\s+/), line = "", n = 0;
    for (var i = 0; i < words.length; i++) {
      var test = line ? line + " " + words[i] : words[i];
      if (ctx.measureText(test).width > maxW && line) {
        n++;
        if (n >= maxLines) { ctx.fillText(fit(ctx, line + " " + words.slice(i).join(" "), maxW), x, y); return y; }
        ctx.fillText(line, x, y); y += lh; line = words[i];
      } else line = test;
    }
    ctx.fillText(line, x, y);
    return y;
  }
  var FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
  var WIN_COLORS = { SAFE_TO_VENTILATE: ["#00b050", "#ffffff"], OPEN_BRIEF: ["#ffc000", "#0f172a"], KEEP_SEALED: ["#e00000", "#ffffff"], UNAVAILABLE: ["#3b4b5a", "#ffffff"] };
  var FP_COLORS = { dust: ["#f3e2b3", "#4a3200"], traffic: ["#e2e6ee", "#1e293b"], smoke: ["#d4d7de", "#111827"], mixed: ["#2b4b63", "#ffffff"], unavailable: ["#2b4b63", "#cbd5e1"] };

  function buildShieldCard(m) {
    var W = 1080, H = 1350, cv = document.createElement("canvas");
    cv.width = W; cv.height = H;
    var x = cv.getContext("2d"), c = m.city, ok = m.available && c.aqi !== null && c.aqi !== undefined;
    var g = x.createLinearGradient(0, 0, 0, H); g.addColorStop(0, "#0b2239"); g.addColorStop(1, "#15435f");
    x.fillStyle = g; x.fillRect(0, 0, W, H);
    x.fillStyle = ok ? c.color : "#64748b"; x.fillRect(0, 0, W, 16);
    x.textBaseline = "alphabetic"; x.textAlign = "left";

    x.fillStyle = "#9fd3f2"; x.font = "700 40px " + FONT; x.fillText("AirGuide \u2022 Family Air Shield", 60, 88);
    var size = 100; x.font = "800 " + size + "px " + FONT;
    while (x.measureText(c.city).width > W - 120 && size > 50) { size -= 4; x.font = "800 " + size + "px " + FONT; }
    x.fillStyle = "#ffffff"; x.fillText(c.city, 60, 190);
    x.fillStyle = "#cfe3f0"; x.font = "500 30px " + FONT;
    var now = new Date().toLocaleString("en-IN", { timeZone: "Asia/Kolkata", weekday: "short", day: "2-digit", month: "short", year: "numeric", hour: "numeric", minute: "2-digit", hour12: true });
    x.fillText(c.state + " \u2022 " + now + " IST", 60, 240);
    x.fillStyle = "#8fb4cc"; x.font = "500 26px " + FONT;
    x.fillText(fit(x, c.reading_time ? "Latest reading: " + fmt(c.reading_time) + " IST \u2022 " + (c.source_label || "") : "Latest reading: Data unavailable", W - 120), 60, 278);

    // AQI badge
    x.beginPath(); x.arc(250, 530, 165, 0, Math.PI * 2); x.fillStyle = ok ? c.color : "#475569"; x.fill();
    x.lineWidth = 8; x.strokeStyle = "rgba(255,255,255,0.85)"; x.stroke();
    x.textAlign = "center"; x.fillStyle = ok ? c.text_color : "#ffffff";
    x.font = "700 34px " + FONT; x.fillText("AQI", 250, 455);
    x.font = "800 " + (ok ? 150 : 70) + "px " + FONT; x.fillText(ok ? String(c.aqi) : "N/A", 250, ok ? 585 : 560);
    x.textAlign = "left";
    x.fillStyle = "#ffffff"; x.font = "800 66px " + FONT; x.fillText(ok ? c.category : "Data unavailable", 470, 500);
    x.fillStyle = "#d8e6f0"; x.font = "500 29px " + FONT;
    wrap(x, ok ? c.health_risk + "." : "No reading is available for this city right now, so no advice is shown.", 470, 552, 560, 38, 5);

    // cigarette equivalent
    x.fillStyle = "#ffffff"; x.font = "800 46px " + FONT;
    var cig = m.cigarette_equivalent;
    x.fillText(cig === null || cig === undefined ? "Cigarettes: Data unavailable" : "\u2248 " + cig + " cigarettes/day", 60, 812);
    if (cig !== null && cig !== undefined) {
      var shown = Math.min(cig, 10), full = Math.floor(shown), frac = shown - full;
      for (var i = 0; i < Math.ceil(shown); i++) {
        var cx = 60 + (i % 5) * 88, cy = 846 + Math.floor(i / 5) * 32, w = (i < full ? 76 : Math.max(8, 76 * frac));
        x.fillStyle = "#f3efe6"; rr(x, cx, cy, w, 16, 3); x.fill();
        x.fillStyle = "#d9a05b"; x.fillRect(cx, cy, Math.min(w, 20), 16);
        x.fillStyle = "#e25822"; x.fillRect(cx + w - 6 > cx ? cx + w - 6 : cx, cy, Math.min(6, w), 16);
      }
    }
    x.fillStyle = "#8fb4cc"; x.font = "500 22px " + FONT; x.fillText("22 \u00B5g/m\u00B3 PM2.5 \u2248 1 cigarette (rule of thumb)", 60, 935);

    // source signature
    var fp = m.fingerprint || { key: "unavailable", signature: "Data unavailable", explanation: "" }, fc = FP_COLORS[fp.key] || FP_COLORS.mixed;
    x.fillStyle = "#8fb4cc"; x.font = "700 24px " + FONT; x.fillText("SOURCE SIGNATURE", 580, 800);
    x.font = "800 30px " + FONT;
    var fw = Math.min(470, x.measureText(fp.signature).width + 40);
    x.fillStyle = fc[0]; rr(x, 580, 818, fw, 56, 28); x.fill();
    x.fillStyle = fc[1]; x.fillText(fit(x, fp.signature, fw - 30), 598, 857);
    x.fillStyle = "#d8e6f0"; x.font = "500 22px " + FONT; wrap(x, fp.explanation || "", 580, 902, 450, 28, 3);

    // smart window
    var w2 = m.window_advisor || { status: "UNAVAILABLE", headline: "Data unavailable", reason: "" }, wc = WIN_COLORS[w2.status] || WIN_COLORS.UNAVAILABLE;
    x.fillStyle = wc[0]; rr(x, 60, 975, W - 120, 100, 18); x.fill();
    x.fillStyle = wc[1]; x.font = "800 38px " + FONT; x.fillText("Smart Window: " + w2.headline, 84, 1022);
    x.font = "500 23px " + FONT; x.fillText(fit(x, (w2.reason || "").split(". ")[0].replace(/\.$/, "") + ".", W - 170), 84, 1058);

    // verdicts
    var v = m.verdicts || {}, bw = m.best_window;
    var rows = [
      ["Kids & Schools", v.kids || "Data unavailable"],
      ["Elderly & Sensitive", v.elderly || "Data unavailable"],
      ["Masks", v.mask || "Data unavailable"],
      ["Best time to step out", bw ? clock(bw.start) + " \u2013 " + clock(bw.end) + " (est. AQI " + bw.avg_aqi + ")" : "Data unavailable"]
    ];
    rows.forEach(function (r, i) {
      var y = 1122 + i * 54;
      x.fillStyle = "#9fd3f2"; x.font = "700 26px " + FONT; x.fillText(r[0], 60, y);
      x.fillStyle = "#ffffff"; x.font = "500 25px " + FONT; x.fillText(fit(x, r[1], 660), 370, y);
    });

    x.fillStyle = "#8fb4cc"; x.font = "500 21px " + FONT;
    x.fillText(fit(x, "AirGuide \u2022 CPCB AQI scale \u2022 General guidance, not medical advice", W - 120), 60, 1326);
    return cv;
  }

  function saveCanvas(cv, name) {
    return new Promise(function (resolve, reject) {
      cv.toBlob(function (blob) {
        if (!blob) return reject(new Error("export failed"));
        var url = URL.createObjectURL(blob), a = document.createElement("a");
        a.href = url; a.download = name; a.rel = "noopener"; document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
        resolve();
      }, "image/png");
    });
  }
  function shareCard(m) {
    if (!m || !m.available) { toast("No air quality data for this city yet, so there is nothing to share."); return Promise.resolve(); }
    var name = "AirGuide-" + m.city.city.replace(/[^A-Za-z0-9]+/g, "-") + "-Shield.png";
    return saveCanvas(buildShieldCard(m), name).then(function () { toast("Family card saved as " + name); }).catch(function () { toast("Could not create the card in this browser."); });
  }
  function shareCardFor(cityId) {
    return api("/api/aq/morning?city=" + encodeURIComponent(cityId)).then(shareCard).catch(function () { toast("Could not load data for the card. Please try again."); });
  }

  /* ================================================================== Smog Lens */
  var LW = 960, LH = 540;
  var TINTS = { dust: [201, 166, 104], smoke: [146, 150, 163], traffic: [172, 160, 142], mixed: [186, 182, 172], unavailable: [186, 182, 172] };
  var Lens = { base: document.createElement("canvas"), mode: "preset", video: null, stream: null, raf: 0, last: 0, split: 0.5, parts: [], cache: { key: "", clean: null, live: null } };
  Lens.base.width = LW; Lens.base.height = LH;
  Lens.bctx = Lens.base.getContext("2d", { willReadFrequently: true });
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function rng(seed) { return function () { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; }; }

  /* Koschmieder / Beer-Lambert haze: extinction from particle mass, stronger scattering of blue by fine particles */
  function hazeParams(pm25, pm10, key) {
    var coarse = Math.max(0, pm10 - pm25);
    var beta = 0.02 + 0.004 * pm25 + 0.0006 * coarse;                 // per km (~4 m2/g fine, ~0.6 m2/g coarse)
    var fine = pm25 / Math.max(pm10, pm25, 1), alpha = 1.3 * fine;    // Angstrom exponent
    var f = [Math.pow(550 / 650, alpha), 1, Math.pow(550 / 450, alpha)];
    var w = Math.min(1, beta / 0.35), base = [196, 214, 235], tint = TINTS[key] || TINTS.mixed;
    var air = [0, 1, 2].map(function (i) { return Math.round(base[i] * (1 - w) + tint[i] * w); });
    return { beta: beta, vis: 3.912 / beta, air: air, tfun: function (d) { return [Math.exp(-beta * f[0] * d), Math.exp(-beta * f[1] * d), Math.exp(-beta * f[2] * d)]; } };
  }
  function mixc(rgb, hp, depth) {
    var t = hp.tfun(depth);
    return "rgb(" + [0, 1, 2].map(function (i) { return Math.round(rgb[i] * t[i] + hp.air[i] * (1 - t[i])); }).join(",") + ")";
  }

  function drawSkyline(ctx, hp) {
    var r = rng(11);
    var sky = ctx.createLinearGradient(0, 0, 0, LH * 0.6);
    sky.addColorStop(0, mixc([70, 140, 225], hp, 12)); sky.addColorStop(1, mixc([190, 225, 255], hp, 12));
    ctx.fillStyle = sky; ctx.fillRect(0, 0, LW, LH);
    var sun = ctx.createRadialGradient(LW * 0.78, LH * 0.2, 4, LW * 0.78, LH * 0.2, 90);
    sun.addColorStop(0, mixc([255, 250, 220], hp, 11)); sun.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = sun; ctx.fillRect(0, 0, LW, LH);
    function layer(depth, color, minH, maxH, wMin, wMax, windows) {
      var xx = -20;
      while (xx < LW) {
        var w = wMin + r() * (wMax - wMin), h = minH + r() * (maxH - minH);
        ctx.fillStyle = mixc(color, hp, depth); ctx.fillRect(xx, LH * 0.78 - h, w, h + 4);
        if (windows) {
          ctx.fillStyle = mixc([255, 226, 150], hp, depth);
          for (var wy = LH * 0.78 - h + 12; wy < LH * 0.78 - 8; wy += 22) for (var wx = xx + 8; wx < xx + w - 10; wx += 18) if (r() > 0.55) ctx.fillRect(wx, wy, 7, 10);
        }
        xx += w + 2 + r() * 6;
      }
    }
    layer(7, [150, 165, 185], 70, 190, 40, 80, false);
    layer(3.5, [95, 110, 130], 110, 250, 50, 90, false);
    layer(1.3, [45, 55, 70], 150, 330, 60, 110, true);
    ctx.fillStyle = mixc([54, 62, 72], hp, 0.3); ctx.fillRect(0, LH * 0.78, LW, LH * 0.22);
    ctx.fillStyle = mixc([235, 235, 235], hp, 0.25);
    for (var dx = 20; dx < LW; dx += 120) ctx.fillRect(dx, LH * 0.9, 60, 6);
    for (var tx = 60; tx < LW; tx += 190 + r() * 60) {
      ctx.fillStyle = mixc([90, 60, 40], hp, 0.5); ctx.fillRect(tx - 4, LH * 0.7, 8, 50);
      ctx.fillStyle = mixc([50, 130, 60], hp, 0.5); ctx.beginPath(); ctx.arc(tx, LH * 0.68, 34, 0, 6.3); ctx.fill();
    }
  }

  function drawCover(ctx, src, sw, sh) {
    var s = Math.max(LW / sw, LH / sh), dw = sw * s, dh = sh * s;
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, LW, LH);
    ctx.drawImage(src, (LW - dw) / 2, (LH - dh) / 2, dw, dh);
  }
  function rowDepth(y) { var hz = LH * 0.42; if (y < hz) return 10; var yn = (y - hz) / (LH - hz); return 0.15 + 9.85 * Math.pow(1 - yn, 2); }

  function hazePhoto(ctx, splitX, pc, pl) {
    var img = Lens.bctx.getImageData(0, 0, LW, LH), d = img.data;
    for (var y = 0; y < LH; y++) {
      var depth = rowDepth(y), tc = pc.tfun(depth), tl = pl.tfun(depth), row = y * LW * 4;
      for (var xx = 0; xx < LW; xx++) {
        var i = row + xx * 4, left = xx < splitX, t = left ? tc : tl, a = left ? pc.air : pl.air;
        d[i] = d[i] * t[0] + a[0] * (1 - t[0]); d[i + 1] = d[i + 1] * t[1] + a[1] * (1 - t[1]); d[i + 2] = d[i + 2] * t[2] + a[2] * (1 - t[2]);
      }
    }
    ctx.putImageData(img, 0, 0);
  }

  function lensCityRow() {
    var id = $("lens-city").value;
    return id ? cityById(id) : null;
  }
  function lensParticles(pm25, pm10, key) {
    var n = Math.min(520, Math.round(pm25 * 1.1 + Math.max(0, pm10 - pm25) * 0.6)), r = rng(5), out = [];
    for (var i = 0; i < n; i++) out.push({ x: r(), y: r(), s: r() < 0.25 ? 3 + r() * 3 : 1 + r() * 2, vx: (r() - 0.3) * 0.012, vy: (r() - 0.5) * 0.006, a: 0.2 + r() * 0.35 });
    Lens.parts = out; Lens.partKey = key;
  }

  function lensFrame(ts) {
    var cv = $("lens-canvas"), ctx = cv.getContext("2d"), row = lensCityRow();
    var split = Math.round(Lens.split * LW);
    var hasData = row && row.pm25 !== null && row.pm25 !== undefined;
    var pc = hazeParams(15, 30, "mixed");  // AQI ~25 reference
    var pm25 = hasData ? row.pm25 : 15, pm10 = hasData && row.pm10 !== null && row.pm10 !== undefined ? row.pm10 : pm25;
    var key = hasData ? row.fingerprint.key : "mixed", pl = hazeParams(pm25, pm10, key);

    if (Lens.mode === "cam" && Lens.video && Lens.video.readyState >= 2) drawCover(Lens.bctx, Lens.video, Lens.video.videoWidth, Lens.video.videoHeight);
    if (Lens.mode === "preset") {
      var ck = [row ? row.city_id : 0, pm25, pm10, key].join("|");
      if (Lens.cache.key !== ck) {
        var a = document.createElement("canvas"), b = document.createElement("canvas");
        a.width = b.width = LW; a.height = b.height = LH;
        drawSkyline(a.getContext("2d"), pc); drawSkyline(b.getContext("2d"), pl);
        Lens.cache = { key: ck, clean: a, live: b };
      }
      ctx.drawImage(Lens.cache.clean, 0, 0);
      ctx.save(); ctx.beginPath(); ctx.rect(split, 0, LW - split, LH); ctx.clip(); ctx.drawImage(Lens.cache.live, 0, 0); ctx.restore();
    } else {
      hazePhoto(ctx, hasData ? split : LW, pc, pl);
    }

    if (hasData) {
      var pk = [pm25, pm10].join("|");
      if (Lens.partKey !== pk) lensParticles(pm25, pm10, pk);
      var dt = Lens.last ? Math.min(0.1, (ts - Lens.last) / 1000) : 0;
      var tint = TINTS[key] || TINTS.mixed;
      ctx.save(); ctx.beginPath(); ctx.rect(split, 0, LW - split, LH); ctx.clip();
      Lens.parts.forEach(function (p) {
        if (!reduceMotion && dt) { p.x = (p.x + p.vx * dt * 60 + 1) % 1; p.y = (p.y + p.vy * dt * 60 + 1) % 1; }
        ctx.fillStyle = "rgba(" + Math.round(tint[0] * 0.55) + "," + Math.round(tint[1] * 0.55) + "," + Math.round(tint[2] * 0.55) + "," + p.a + ")";
        ctx.beginPath(); ctx.arc(p.x * LW, p.y * LH, p.s, 0, 6.3); ctx.fill();
      });
      ctx.restore();
    }
    Lens.last = ts || 0;

    // divider + labels
    ctx.fillStyle = "#ffffff"; ctx.fillRect(split - 1.5, 0, 3, LH);
    ctx.beginPath(); ctx.arc(split, LH / 2, 18, 0, 6.3); ctx.fillStyle = "#ffffff"; ctx.fill();
    ctx.fillStyle = "#0f172a"; ctx.font = "700 16px " + FONT; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText("\u2194", split, LH / 2 + 1);
    function chip(text, xx, align) {
      ctx.font = "700 18px " + FONT; var w = ctx.measureText(text).width + 24, x0 = align === "left" ? xx : xx - w;
      ctx.fillStyle = "rgba(15,23,42,0.78)"; rr(ctx, x0, 14, w, 34, 17); ctx.fill();
      ctx.fillStyle = "#ffffff"; ctx.textAlign = "left"; ctx.fillText(text, x0 + 12, 32);
    }
    chip("Clean Air (AQI 25)", 14, "left");
    chip(hasData ? row.city + " Live (AQI " + (row.aqi === null || row.aqi === undefined ? "n/a" : row.aqi) + ")" : "Select a city with data", LW - 14, "right");

    var cap = $("lens-caption");
    var text = hasData
      ? "Estimated visibility \u2248 " + Math.min(50, pl.vis).toFixed(1) + " km here vs \u2248 " + Math.min(50, pc.vis).toFixed(0) + " km in clean air. Based on PM2.5 " + Math.round(pm25) + " \u00B5g/m\u00B3, source: " + row.fingerprint.signature + (row.pm10 === null || row.pm10 === undefined ? ". PM10 unavailable, so no coarse dust is assumed" : "") + ". An illustration only: humidity and light also change real visibility."
      : row ? "Data unavailable: " + row.city + " has no PM2.5 reading right now." : "Choose a city to compare its air with clean air.";
    if (cap.textContent !== text) cap.textContent = text;
  }

  function lensLoop(ts) {
    Lens.raf = 0;
    if ($("lens").hidden) return;
    if (!Lens.last || ts - Lens.last >= 33) lensFrame(ts);
    var animate = Lens.mode === "cam" || (!reduceMotion && Lens.parts.length);
    if (animate) Lens.raf = requestAnimationFrame(lensLoop);
  }
  function lensRender() { if (!Lens.raf) { Lens.raf = requestAnimationFrame(function (ts) { Lens.last = 0; lensLoop(ts); }); } }
  function lensCityChanged() { Lens.partKey = ""; lensRender(); }

  function stopCam() {
    if (Lens.stream) Lens.stream.getTracks().forEach(function (t) { t.stop(); });
    Lens.stream = null; Lens.video = null;
    if (Lens.mode === "cam") { Lens.mode = "preset"; }
    $("lens-stop").hidden = true; $("lens-cam").hidden = false;
  }
  function stopLens() {
    if (Lens.raf) { cancelAnimationFrame(Lens.raf); Lens.raf = 0; }
    stopCam();
  }
  function startCam() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { toast("The camera is not available in this browser. You can upload a photo instead."); return; }
    navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false }).then(function (stream) {
      var v = document.createElement("video");
      v.muted = true; v.setAttribute("playsinline", ""); v.srcObject = stream;
      Lens.stream = stream; Lens.video = v; Lens.mode = "cam";
      $("lens-stop").hidden = false; $("lens-cam").hidden = true;
      v.play().catch(function () {});
      $("lens-stop").focus();
      toast("Camera is on. Frames stay on this device. Press Stop camera when done.");
      lensRender();
    }).catch(function () { toast("Camera permission was not granted. You can upload a photo instead."); });
  }
  function initLens() {
    fillCitySelect($("lens-city"), S.selected);
    lensRender();
  }

  function bindCreative() {
    ["morning-city", "esc-city", "lens-city"].forEach(function (id) { $(id).addEventListener("change", function (e) { setSelected(e.target.value); }); });
    $("morning-share").addEventListener("click", function () { shareCard(lastMorning); });
    $("lens-cam").addEventListener("click", startCam);
    $("lens-stop").addEventListener("click", function () { stopCam(); lensRender(); $("lens-cam").focus(); toast("Camera stopped."); });
    $("lens-preset").addEventListener("click", function () { stopCam(); Lens.mode = "preset"; lensRender(); });
    $("lens-file").addEventListener("change", function (e) {
      var f = e.target.files && e.target.files[0];
      if (!f) return;
      if (!/^image\//.test(f.type) || f.size > 15 * 1024 * 1024) { toast("Please choose an image file under 15 MB."); return; }
      stopCam();
      createImageBitmap(f).then(function (bmp) {
        drawCover(Lens.bctx, bmp, bmp.width, bmp.height);
        if (bmp.close) bmp.close();
        Lens.mode = "photo"; lensRender();
      }).catch(function () { toast("That image could not be read. Try a JPG or PNG."); });
    });
    var slider = $("lens-slider"), cv = $("lens-canvas");
    slider.addEventListener("input", function () { Lens.split = slider.value / 100; lensRender(); });
    function drag(e) { var r = cv.getBoundingClientRect(); Lens.split = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)); slider.value = Math.round(Lens.split * 100); lensRender(); }
    cv.addEventListener("pointerdown", function (e) { cv.setPointerCapture(e.pointerId); drag(e); cv.addEventListener("pointermove", drag); });
    cv.addEventListener("pointerup", function () { cv.removeEventListener("pointermove", drag); });
    document.addEventListener("visibilitychange", function () { if (document.hidden) stopCam(); });
    window.addEventListener("pagehide", stopCam);
  }

  /* ------------------------------------------------------------------ router */
  function show(view) {
    if (VIEWS.indexOf(view) === -1) view = "dashboard";
    VIEWS.forEach(function (v) { $(v).hidden = v !== view; });
    Array.prototype.forEach.call(document.querySelectorAll("#nav a"), function (a) {
      if (a.getAttribute("href") === "#" + view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    if (view !== "lens") stopLens();
    document.title = "AirGuide \u2013 " + $("h-" + view).textContent;
    var needsMe = view === "employee" || view === "family" || view === "alerts" || view === "activities";
    var ready = loadOverview().catch(function () { return null; });
    ready.then(function (o) {
      if (!o) return;
      if (view === "dashboard") { renderDashboard(o); if (!S.selected) S.selected = defaultCity(); renderMorning(); renderQuality(); }
      if (view === "map") renderMap();
      if (view === "forecast") { if (!S.selected) S.selected = defaultCity(); }
      if (view === "escape") { if (!S.selected) S.selected = defaultCity(); renderEscape(); }
      if (view === "lens") { if (!S.selected) S.selected = defaultCity(); initLens(); }
      if (view === "cities") renderCities(o);
      var after = function () {
        if (view === "employee") { renderPrefs(); }
        if (view === "employee" || view === "family" || view === "activities") {
          if (!S.selected) S.selected = defaultCity();
          setSelected(S.selected);
        }
        if (view === "history") renderHistory();
        if (view === "alerts") renderAlerts();
        if (view === "forecast") renderForecast();
      };
      if (needsMe) { loadMe().catch(function () {}).then(after); } else after();
    });
    if (view === "admin") renderAdmin();
  }

  function route() { show((location.hash || "#dashboard").slice(1)); }

  $("refresh-aqi").addEventListener("click", function () {
    var btn = $("refresh-aqi");
    btn.disabled = true;
    btn.textContent = "Checking CPCB...";
    setBanner("Checking the latest available CPCB station readings. This may take a few seconds.", false);
    api("/api/aq/refresh", { method: "POST", body: {} })
      .then(function (result) {
        S.overview = null;
        return loadOverview(true).then(function () {
          show((location.hash || "#dashboard").slice(1));
          var added = result.stored || 0;
          setBanner(added > 0
            ? "CPCB refresh complete. " + added + " new or updated station readings were saved."
            : "CPCB checked successfully. No newer or changed station readings were available; the latest saved values are still shown.", false);
        });
      })
      .catch(function (err) {
        setBanner("Refresh could not complete: " + err.message, true);
      })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = "↻ Refresh CPCB AQI";
      });
  });

  window.addEventListener("hashchange", route);
  $("f-search").addEventListener("input", function () { if (S.overview) renderCities(S.overview); });
  $("f-state").addEventListener("change", function () { if (S.overview) renderCities(S.overview); });
  $("f-sort").addEventListener("change", function () { if (S.overview) renderCities(S.overview); });
  ["adv-city-emp", "adv-city-fam", "adv-city-act"].forEach(function (id) { $(id).addEventListener("change", function (e) { setSelected(e.target.value); }); });
  $("act-aud").addEventListener("change", refreshAdvisory);
  $("hist-city").addEventListener("change", renderHistory);
  $("hist-hours").addEventListener("change", renderHistory);
  $("alerts-mine").addEventListener("change", renderAlerts);
  $("forecast-city").addEventListener("change", renderForecast);
  $("forecast-hours").addEventListener("change", renderForecast);
  $("health-profile").addEventListener("change", function () { if (advCache) renderHealthPersonalised(advCache); });
  $("health-activity").addEventListener("change", function () { if (advCache) renderHealthPersonalised(advCache); });
  $("history-csv").addEventListener("click", downloadHistoryCsv);
  $("history-pdf").addEventListener("click", printHistoryReport);
  $("enable-browser-alerts").addEventListener("click", enableBrowserAlerts);
  $("map-fit").addEventListener("click", function () { if (S.map) S.map.setView([22.8,79.0],4.5); });
  $("map-city-search").addEventListener("change", function () { if (!S.overview) return; var q = this.value.toLowerCase(); var c = S.overview.cities.filter(function (x) { return (x.city + ", " + x.state).toLowerCase() === q || x.city.toLowerCase() === q; })[0]; if (c && S.map) { S.map.setView([c.latitude,c.longitude],10); showStations(c); } });
  $("me-delete").addEventListener("click", function () {
    if (!window.confirm("Delete your saved locations and profile?")) return;
    api("/api/aq/me", { method: "DELETE", body: {} }).then(function () { S.me = null; S.selected = ""; return loadMe(true); }).then(function () { renderPrefs(); setSelected(""); });
  });

  bindCreative();
  window.setInterval(pollBrowserAlerts, 5 * 60 * 1000);
  route();
})();
