(function () {
  "use strict";

  const API_BASE = window.location.origin.includes("localhost") ||
    window.location.origin.includes("127.0.0.1")
    ? "http://127.0.0.1:5000"
    : window.location.origin;

  const DEFAULT_COORDS = { lat: 28.6139, lng: 77.2090 };
  const PEAK_HOURS = [7, 8, 9, 17, 18, 19];

  const WEATHER_CODES = {
    0: "Clear Sky",
    1: "Mainly Clear",
    2: "Partly Cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing Rime Fog",
    51: "Light Drizzle",
    53: "Moderate Drizzle",
    55: "Dense Drizzle",
    61: "Slight Rain",
    63: "Moderate Rain",
    65: "Heavy Rain",
    71: "Slight Snow",
    73: "Moderate Snow",
    75: "Heavy Snow",
    80: "Rain Showers",
    95: "Thunderstorm",
  };

  const FALLBACK_INTERSECTIONS = [
    { id: "INT-01", name: "Central Plaza", lat: 28.6139, lng: 77.2090, congestion_index: 0.42, density_color: "yellow" },
    { id: "INT-02", name: "Tech Park Gate", lat: 28.6289, lng: 77.2065, congestion_index: 0.28, density_color: "green" },
    { id: "INT-03", name: "Metro Junction", lat: 28.6200, lng: 77.2300, congestion_index: 0.61, density_color: "red" },
    { id: "INT-04", name: "Harbor Ring", lat: 28.6080, lng: 77.2500, congestion_index: 0.35, density_color: "yellow" },
    { id: "INT-05", name: "University Ave", lat: 28.6350, lng: 77.1950, congestion_index: 0.22, density_color: "green" },
    { id: "INT-06", name: "Industrial Belt", lat: 28.6000, lng: 77.1800, congestion_index: 0.55, density_color: "red" },
  ];

  const state = {
    user: null,
    currentView: "auth",
    selectedIntersection: FALLBACK_INTERSECTIONS[0],
    calendarMonth: new Date().getMonth(),
    calendarYear: new Date().getFullYear(),
    selectedDate: null,
    weather: { temp: 28, wind: 12, precip: 0, code: "Clear Sky" },
    oauthConfigs: { google: null, github: null },
    telemetryLabels: [],
    telemetryData: [],
    pollingTimer: null,
    weatherTimer: null,
    adminTimer: null,
    backendOnline: false,
  };

  let trafficMap = null;
  let mapMarkers = {};
  let lineChart = null;
  let fleetChart = null;
  let epochChart = null;

  const views = {
    auth: document.getElementById("view-auth"),
    dashboard: document.getElementById("view-dashboard"),
    admin: document.getElementById("view-admin"),
  };

  function $(id) {
    return document.getElementById(id);
  }

  function showMessage(elementId, text, type) {
    const el = $(elementId);
    if (!el) return;
    el.textContent = text;
    el.className = "auth-message" + (type ? " " + type : "");
  }

  function switchView(viewName) {
    state.currentView = viewName;
    Object.keys(views).forEach(function (key) {
      views[key].classList.toggle("active-view", key === viewName);
    });
    if (viewName === "dashboard") {
      setTimeout(function () {
        if (trafficMap) trafficMap.invalidateSize();
        initChartsIfNeeded();
        startPolling();
        startWeatherStream();
        loadDatasetInfo();
      }, 100);
    }
    if (viewName === "admin") {
      loadAdminMetrics();
      startAdminPolling();
    }
    if (viewName !== "admin") {
      stopAdminPolling();
    }
    if (viewName === "auth") {
      stopPolling();
      stopWeatherStream();
    }
  }

  async function apiFetch(path, options) {
    const config = Object.assign(
      { credentials: "include", headers: { "Content-Type": "application/json" } },
      options || {}
    );
    try {
      const response = await fetch(API_BASE + path, config);
      const data = await response.json();
      state.backendOnline = true;
      return { ok: response.ok, status: response.status, data: data };
    } catch (error) {
      state.backendOnline = false;
      return { ok: false, status: 0, data: null, error: error };
    }
  }

  function generateFallbackTelemetry() {
    const now = new Date();
    const hour = now.getHours();
    const rush = hour >= 7 && hour <= 10 || hour >= 17 && hour <= 20;
    const base = rush ? randomInt(180, 320) : randomInt(70, 180);
    const congestion = Math.min(1, base / 400 + (state.weather.precip > 0 ? 0.15 : 0));
    const color = congestion > 0.55 ? "red" : congestion > 0.35 ? "yellow" : "green";
    const fleetTotal = base;
    return {
      success: true,
      timestamp: now.toISOString(),
      intersection: state.selectedIntersection,
      vehicle_count: base,
      congestion_index: parseFloat(congestion.toFixed(3)),
      density_color: color,
      fleet: {
        two_wheelers: Math.floor(fleetTotal * 0.35),
        commuter_cars: Math.floor(fleetTotal * 0.38),
        ev_autonomous: Math.floor(fleetTotal * 0.12),
        freight_trucks: Math.max(0, fleetTotal - Math.floor(fleetTotal * 0.85)),
      },
      intersections: FALLBACK_INTERSECTIONS.map(function (item) {
        return Object.assign({}, item, {
          congestion_index: parseFloat((Math.random() * 0.6 + 0.15).toFixed(3)),
          density_color: ["green", "yellow", "red"][randomInt(0, 2)],
        });
      }),
    };
  }

  function randomInt(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
  }

  function densityColorToHex(color) {
    if (color === "red") return "#ff3860";
    if (color === "yellow") return "#ffb800";
    return "#00e676";
  }

  function initAuthTabs() {
    const tabLogin = $("tab-login");
    const tabRegister = $("tab-register");
    const loginForm = $("login-form");
    const registerForm = $("register-form");

    tabLogin.addEventListener("click", function () {
      tabLogin.classList.add("active");
      tabRegister.classList.remove("active");
      loginForm.classList.add("active-form");
      registerForm.classList.remove("active-form");
      showMessage("auth-message", "");
    });

    tabRegister.addEventListener("click", function () {
      tabRegister.classList.add("active");
      tabLogin.classList.remove("active");
      registerForm.classList.add("active-form");
      loginForm.classList.remove("active-form");
      showMessage("auth-message", "");
    });
  }

  async function loadOAuthConfigs() {
    try {
      const googleRes = await fetch("/auth_config_google.json");
      const githubRes = await fetch("/auth_config_github.json");
      if (googleRes.ok) state.oauthConfigs.google = await googleRes.json();
      if (githubRes.ok) state.oauthConfigs.github = await githubRes.json();
    } catch (e) {
      state.oauthConfigs.google = {
        client_id: "MOCK_ID.apps.googleusercontent.com",
        auth_uri: "https://accounts.google.com/o/oauth2/auth",
        token_uri: "https://oauth2.googleapis.com/token",
        scopes: ["profile", "email"],
      };
      state.oauthConfigs.github = {
        client_id: "MOCK_GITHUB_CLIENT_ID",
        auth_uri: "https://github.com/login/oauth/authorize",
        token_uri: "https://github.com/login/oauth/access_token",
        scopes: ["read:user", "user:email"],
      };
    }
  }

  async function handleLogin(event) {
    event.preventDefault();
    const identifier = $("login-identifier").value.trim();
    const password = $("login-password").value;
    const result = await apiFetch("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ identifier: identifier, password: password }),
    });
    if (result.ok && result.data.success) {
      state.user = result.data.user;
      onAuthSuccess();
    } else {
      showMessage("auth-message", result.data ? result.data.message : "Backend offline. Use demo or start Flask server.", "error");
    }
  }

  async function handleRegister(event) {
    event.preventDefault();
    const username = $("register-username").value.trim();
    const email = $("register-email").value.trim();
    const password = $("register-password").value;
    const result = await apiFetch("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ username: username, email: email, password: password }),
    });
    if (result.ok && result.data.success) {
      state.user = result.data.user;
      onAuthSuccess();
    } else {
      showMessage("auth-message", result.data ? result.data.message : "Registration failed. Is the backend running?", "error");
    }
  }

  async function handleOAuth(provider) {
    showMessage("auth-message", "Initiating secure " + provider + " handshake...", "");
    await new Promise(function (resolve) { setTimeout(resolve, 900); });
    const result = await apiFetch("/api/auth/oauth-mock", {
      method: "POST",
      body: JSON.stringify({ provider: provider }),
    });
    if (result.ok && result.data.success) {
      state.user = result.data.user;
      showMessage("auth-message", provider + " OAuth verified via " + result.data.config.client_id, "success");
      setTimeout(onAuthSuccess, 600);
    } else {
      showMessage("auth-message", "OAuth simulation failed. Start backend server.", "error");
    }
  }

  function onAuthSuccess() {
    $("user-greeting").textContent = "Welcome, " + state.user.username;
    const adminBtn = $("btn-admin");
    if (state.user.role === "admin") {
      adminBtn.classList.remove("hidden");
    } else {
      adminBtn.classList.add("hidden");
    }
    switchView("dashboard");
  }

  async function handleLogout() {
    await apiFetch("/api/auth/logout", { method: "POST" });
    state.user = null;
    switchView("auth");
    showMessage("auth-message", "Session terminated.");
  }

  async function checkSession() {
    const result = await apiFetch("/api/auth/session");
    if (result.ok && result.data.authenticated) {
      state.user = result.data.user;
      onAuthSuccess();
    }
  }

  function initMap() {
    if (trafficMap) return;
    trafficMap = L.map("traffic-map", {
      center: [DEFAULT_COORDS.lat, DEFAULT_COORDS.lng],
      zoom: 13,
      zoomControl: true,
    });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19,
    }).addTo(trafficMap);
    renderMapMarkers(FALLBACK_INTERSECTIONS);
  }

  function renderMapMarkers(intersections) {
    Object.keys(mapMarkers).forEach(function (key) {
      trafficMap.removeLayer(mapMarkers[key]);
    });
    mapMarkers = {};
    intersections.forEach(function (item) {
      const color = densityColorToHex(item.density_color || "green");
      const radius = 14 + (item.congestion_index || 0.3) * 20;
      const circle = L.circleMarker([item.lat, item.lng], {
        radius: radius,
        fillColor: color,
        color: "#ffffff",
        weight: 2,
        opacity: 0.9,
        fillOpacity: 0.65,
        className: "density-marker",
      }).addTo(trafficMap);
      circle.bindPopup(
        "<strong>" + item.name + "</strong><br/>ID: " + item.id +
        "<br/>Congestion: " + ((item.congestion_index || 0) * 100).toFixed(0) + "%"
      );
      circle.on("click", function () {
        state.selectedIntersection = item;
        $("selected-intersection").textContent = item.id + " · " + item.name;
        fetchLiveTelemetry();
        circle.setStyle({ weight: 4, color: "#00f2fe" });
        Object.keys(mapMarkers).forEach(function (key) {
          if (key !== item.id) {
            mapMarkers[key].setStyle({ weight: 2, color: "#ffffff" });
          }
        });
      });
      mapMarkers[item.id] = circle;
    });
  }

  function initChartsIfNeeded() {
    if (!lineChart) {
      const lineCtx = $("line-chart").getContext("2d");
      state.telemetryLabels = [];
      state.telemetryData = [];
      for (let i = 19; i >= 0; i--) {
        const t = new Date(Date.now() - i * 2000);
        state.telemetryLabels.push(t.toLocaleTimeString());
        state.telemetryData.push(randomInt(80, 200));
      }
      lineChart = new Chart(lineCtx, {
        type: "line",
        data: {
          labels: state.telemetryLabels,
          datasets: [{
            label: "Vehicle Volume",
            data: state.telemetryData,
            borderColor: "#00f2fe",
            backgroundColor: "rgba(0, 242, 254, 0.1)",
            borderWidth: 2,
            fill: true,
            tension: 0.4,
            pointRadius: 0,
            pointHoverRadius: 4,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: { duration: 400 },
          plugins: {
            legend: { labels: { color: "#8ba3c7", font: { family: "Rajdhani" } } },
          },
          scales: {
            x: {
              ticks: { color: "#8ba3c7", maxTicksLimit: 8, font: { size: 10 } },
              grid: { color: "rgba(255,255,255,0.04)" },
            },
            y: {
              ticks: { color: "#8ba3c7" },
              grid: { color: "rgba(255,255,255,0.06)" },
              beginAtZero: true,
            },
          },
        },
      });
    }

    if (!fleetChart) {
      const fleetCtx = $("fleet-chart").getContext("2d");
      fleetChart = new Chart(fleetCtx, {
        type: "polarArea",
        data: {
          labels: ["Two-Wheelers", "Commuter Cars", "Autonomous/EV", "Freight Trucks"],
          datasets: [{
            data: [120, 140, 45, 35],
            backgroundColor: [
              "rgba(0, 242, 254, 0.6)",
              "rgba(155, 81, 224, 0.6)",
              "rgba(0, 230, 118, 0.6)",
              "rgba(255, 184, 0, 0.6)",
            ],
            borderColor: ["#00f2fe", "#9b51e0", "#00e676", "#ffb800"],
            borderWidth: 1,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: {
              position: "bottom",
              labels: { color: "#8ba3c7", font: { family: "Rajdhani", size: 11 } },
            },
          },
          scales: {
            r: {
              ticks: { display: false },
              grid: { color: "rgba(255,255,255,0.06)" },
            },
          },
        },
      });
    }
  }

  function updateCharts(telemetry) {
    if (!lineChart || !fleetChart) return;
    const label = new Date().toLocaleTimeString();
    state.telemetryLabels.push(label);
    state.telemetryData.push(telemetry.vehicle_count);
    if (state.telemetryLabels.length > 20) {
      state.telemetryLabels.shift();
      state.telemetryData.shift();
    }
    lineChart.data.labels = state.telemetryLabels;
    lineChart.data.datasets[0].data = state.telemetryData;
    lineChart.update("none");

    const fleet = telemetry.fleet;
    fleetChart.data.datasets[0].data = [
      fleet.two_wheelers,
      fleet.commuter_cars,
      fleet.ev_autonomous,
      fleet.freight_trucks,
    ];
    fleetChart.update("none");
  }

  function updateMetricCards(telemetry) {
    $("metric-vehicles").textContent = telemetry.vehicle_count;
    $("metric-congestion").textContent = telemetry.congestion_index.toFixed(2);
    const densityEl = $("metric-density");
    const color = telemetry.density_color;
    densityEl.textContent = color === "red" ? "CRITICAL" : color === "yellow" ? "MODERATE" : "LIGHT";
    densityEl.className = "metric-value density-" + color;
  }

  async function fetchLiveTelemetry() {
    const intersectionId = state.selectedIntersection.id;
    let result = await apiFetch("/api/traffic/live?intersection_id=" + intersectionId);
    let telemetry;
    if (result.ok && result.data.success) {
      telemetry = result.data;
      if (telemetry.intersections) {
        renderMapMarkers(telemetry.intersections);
      }
    } else {
      telemetry = generateFallbackTelemetry();
      renderMapMarkers(telemetry.intersections);
    }
    updateCharts(telemetry);
    updateMetricCards(telemetry);
    $("telemetry-status").textContent = state.backendOnline ? "POLLING 2s · ONLINE" : "POLLING 2s · OFFLINE FALLBACK";
    $("telemetry-status").classList.toggle("live", state.backendOnline);
  }

  function startPolling() {
    stopPolling();
    fetchLiveTelemetry();
    state.pollingTimer = setInterval(fetchLiveTelemetry, 2000);
  }

  function stopPolling() {
    if (state.pollingTimer) {
      clearInterval(state.pollingTimer);
      state.pollingTimer = null;
    }
  }

  async function fetchWeather() {
    const url = "https://api.open-meteo.com/v1/forecast?latitude=" +
      DEFAULT_COORDS.lat + "&longitude=" + DEFAULT_COORDS.lng +
      "&current=temperature_2m,wind_speed_10m,weather_code,precipitation&timezone=auto";
    try {
      const response = await fetch(url);
      const data = await response.json();
      const current = data.current;
      state.weather.temp = current.temperature_2m;
      state.weather.wind = current.wind_speed_10m;
      state.weather.precip = current.precipitation || 0;
      state.weather.code = WEATHER_CODES[current.weather_code] || "Unknown";
      $("weather-temp").textContent = state.weather.temp.toFixed(1) + "°C";
      $("weather-wind").textContent = state.weather.wind.toFixed(1) + " km/h";
      $("weather-code").textContent = state.weather.code;
      $("weather-precip").textContent = state.weather.precip.toFixed(1) + " mm";
      $("weather-updated").textContent = "Updated " + new Date().toLocaleTimeString();
    } catch (error) {
      $("weather-temp").textContent = "28.0°C";
      $("weather-wind").textContent = "12.0 km/h";
      $("weather-code").textContent = "Partly Cloudy";
      $("weather-precip").textContent = "0.0 mm";
      $("weather-updated").textContent = "Offline fallback";
    }
  }

  function startWeatherStream() {
    stopWeatherStream();
    fetchWeather();
    state.weatherTimer = setInterval(fetchWeather, 60000);
  }

  function stopWeatherStream() {
    if (state.weatherTimer) {
      clearInterval(state.weatherTimer);
      state.weatherTimer = null;
    }
  }

  function buildCalendar() {
    const grid = $("calendar-grid");
    const label = $("cal-month-label");
    const monthNames = ["January", "February", "March", "April", "May", "June",
      "July", "August", "September", "October", "November", "December"];
    label.textContent = monthNames[state.calendarMonth] + " " + state.calendarYear;
    grid.innerHTML = "";

    const firstDay = new Date(state.calendarYear, state.calendarMonth, 1).getDay();
    const daysInMonth = new Date(state.calendarYear, state.calendarMonth + 1, 0).getDate();
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    for (let i = 0; i < firstDay; i++) {
      const empty = document.createElement("div");
      empty.className = "cal-day empty";
      grid.appendChild(empty);
    }

    for (let day = 1; day <= daysInMonth; day++) {
      const cell = document.createElement("div");
      cell.className = "cal-day";
      cell.textContent = day;
      const cellDate = new Date(state.calendarYear, state.calendarMonth, day);
      cellDate.setHours(0, 0, 0, 0);

      if (cellDate.getTime() === today.getTime()) {
        cell.classList.add("today");
      }
      if (cellDate < today) {
        cell.classList.add("past");
      } else if (cellDate > today) {
        cell.classList.add("future");
      }
      if (PEAK_HOURS.indexOf(cellDate.getDay() === 0 || cellDate.getDay() === 6 ? 10 : 18) >= 0) {
        cell.classList.add("peak");
      }
      if (state.selectedDate && cellDate.getTime() === state.selectedDate.getTime()) {
        cell.classList.add("selected");
      }

      cell.addEventListener("click", function () {
        state.selectedDate = cellDate;
        buildCalendar();
        handleCalendarSelect(cellDate);
      });
      grid.appendChild(cell);
    }
  }

  async function handleCalendarSelect(date) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const hint = $("calendar-hint");

    if (date < today) {
      hint.textContent = "Loading historical logs for " + date.toDateString() + "...";
      const dayOfWeek = date.getDay() === 0 ? 6 : date.getDay() - 1;
      const adjustedDow = date.getDay() === 0 ? 6 : date.getDay() - 1;
      const result = await apiFetch(
        "/api/traffic/history?day_of_week=" + adjustedDow +
        "&intersection_id=" + state.selectedIntersection.id
      );
      if (result.ok && result.data.records && result.data.records.length > 0) {
        const records = result.data.records;
        const avgCongestion = records.reduce(function (sum, r) { return sum + r.avg_congestion; }, 0) / records.length;
        hint.textContent = "Historical avg congestion: " + (avgCongestion * 100).toFixed(0) + "% across " + records.length + " hourly buckets.";
        if (records[0].fleet) {
          fleetChart.data.datasets[0].data = [
            records[0].fleet.two_wheelers,
            records[0].fleet.commuter_cars,
            records[0].fleet.ev_autonomous,
            records[0].fleet.freight_trucks,
          ];
          fleetChart.update();
        }
        const histLabels = records.map(function (r) { return r.hour + ":00"; });
        const histData = records.map(function (r) { return Math.round(r.avg_count); });
        lineChart.data.labels = histLabels;
        lineChart.data.datasets[0].data = histData;
        lineChart.data.datasets[0].label = "Historical Volume";
        lineChart.update();
      } else {
        hint.textContent = "Historical data unavailable. Showing simulated archive for " + date.toDateString() + ".";
      }
    } else if (date > today) {
      hint.textContent = "Requesting ML forecast for " + date.toDateString() + "...";
      const hour = 17;
      const payload = {
        hour: hour,
        day_of_week: date.getDay() === 0 ? 6 : date.getDay() - 1,
        temp: state.weather.temp,
        precipitation: state.weather.precip > 0 ? 0.6 : 0.05,
        historical_count: 220,
        intersection_id: state.selectedIntersection.id,
      };
      const result = await apiFetch("/api/predict/model", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (result.ok && result.data.success) {
        const pred = result.data;
        hint.textContent = "ML Forecast: " + pred.congestion_label +
          " congestion (" + (pred.predicted_congestion * 100).toFixed(0) + "%) · ~" +
          pred.estimated_vehicle_count + " vehicles at 5 PM.";
        $("metric-forecast").textContent = (pred.predicted_congestion * 100).toFixed(0) + "%";
      } else {
        const fallback = Math.min(1, 0.35 + state.weather.precip * 0.1);
        hint.textContent = "Offline ML fallback: " + (fallback * 100).toFixed(0) + "% predicted congestion.";
        $("metric-forecast").textContent = (fallback * 100).toFixed(0) + "%";
      }
    } else {
      hint.textContent = "Today selected — live telemetry active.";
      lineChart.data.datasets[0].label = "Vehicle Volume";
      fetchLiveTelemetry();
    }
  }

  async function handleNlpCommand() {
    const text = $("nlp-command").value.trim();
    const resultEl = $("nlp-result");
    if (!text) {
      resultEl.textContent = "Enter a natural language command to parse.";
      return;
    }
    resultEl.textContent = "Parsing command through NLP bridge...";
    const result = await apiFetch("/api/nlp/parse", {
      method: "POST",
      body: JSON.stringify({ text: text }),
    });
    if (result.ok && result.data.success) {
      const parsed = result.data.parsed;
      const congestion = result.data.predicted_congestion;
      resultEl.innerHTML =
        "<strong>Parsed:</strong> hour=" + parsed.hour +
        ", day=" + parsed.day_of_week +
        ", rain=" + (parsed.is_raining ? "yes" : "no") +
        ", confidence=" + (parsed.confidence * 100).toFixed(0) + "%<br/>" +
        "<strong>Tokens:</strong> " + (parsed.parsed_tokens.join(", ") || "none") + "<br/>" +
        "<strong>Predicted Congestion:</strong> " + (congestion * 100).toFixed(1) + "%";
      $("metric-forecast").textContent = (congestion * 100).toFixed(0) + "%";
    } else {
      const localParsed = parseNlpLocally(text);
      resultEl.innerHTML =
        "<strong>Local NLP Fallback:</strong> hour=" + localParsed.hour +
        ", raining=" + localParsed.isRaining +
        "<br/><strong>Estimated Congestion:</strong> " + (localParsed.congestion * 100).toFixed(0) + "%";
      $("metric-forecast").textContent = (localParsed.congestion * 100).toFixed(0) + "%";
    }
  }

  function parseNlpLocally(text) {
    const lower = text.toLowerCase();
    let hour = new Date().getHours();
    let isRaining = false;
    const hourMatch = lower.match(/(\d{1,2})\s*(pm|am)/);
    if (hourMatch) {
      hour = parseInt(hourMatch[1], 10);
      if (hourMatch[2] === "pm" && hour < 12) hour += 12;
    }
    if (lower.indexOf("rain") >= 0) isRaining = true;
    if (lower.indexOf("tomorrow") >= 0) hour = 17;
    const congestion = Math.min(1, 0.25 + (hour >= 17 && hour <= 20 ? 0.35 : 0.1) + (isRaining ? 0.2 : 0));
    return { hour: hour, isRaining: isRaining, congestion: congestion };
  }

  async function loadDatasetInfo() {
    const result = await apiFetch("/api/dataset/info");
    if (!result.ok || !result.data.success) {
      $("dataset-source").textContent = "CSV offline";
      return;
    }
    const data = result.data;
    $("dataset-source").textContent = "Source: " + data.source + " · " + data.kaggle_dataset;
    $("dataset-rows").textContent = data.rows;
    if (data.stats) {
      $("dataset-mean").textContent = Math.round(data.stats.mean || 0);
      $("dataset-max").textContent = Math.round(data.stats.max || 0);
      $("dataset-min").textContent = Math.round(data.stats.min || 0);
    }
    $("dataset-chart").src = API_BASE + "/api/dataset/visualization?t=" + Date.now();
    const body = $("dataset-preview-body");
    body.innerHTML = "";
    (data.preview || []).forEach(function (row) {
      const tr = document.createElement("tr");
      tr.innerHTML = "<td>" + row.ID + "</td><td>" + Number(row.traffic_volume).toFixed(2) + "</td>";
      body.appendChild(tr);
    });
  }

  async function handleKaggleSync() {
    const btn = $("btn-kaggle-sync");
    if (!btn) return;
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Syncing...';
    const result = await apiFetch("/api/dataset/kaggle-sync", { method: "POST", body: JSON.stringify({}) });
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-brands fa-kaggle"></i> Sync Kaggle';
    if (result.ok && result.data.success) {
      alert("Kaggle dataset synced: " + result.data.rows + " rows. Models retrained.");
      loadDatasetInfo();
      loadAdminMetrics();
    } else {
      alert(result.data ? result.data.message : "Kaggle sync failed. Use local CSV or install kagglehub.");
    }
  }

  function initEpochChart(lossData) {
    const ctx = $("epoch-chart");
    if (!ctx) return;
    if (epochChart) {
      epochChart.destroy();
    }
    const labels = (lossData || []).map(function (_, i) { return "E" + (i + 1); });
    epochChart = new Chart(ctx.getContext("2d"), {
      type: "line",
      data: {
        labels: labels.length ? labels : ["E1", "E2", "E3", "E4", "E5"],
        datasets: [{
          label: "Epoch MSE Loss",
          data: lossData && lossData.length ? lossData : [0.08, 0.06, 0.045, 0.035, 0.03],
          borderColor: "#9b51e0",
          backgroundColor: "rgba(155, 81, 224, 0.15)",
          fill: true,
          tension: 0.3,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#8ba3c7" } } },
        scales: {
          x: { ticks: { color: "#8ba3c7" }, grid: { color: "rgba(255,255,255,0.04)" } },
          y: { ticks: { color: "#8ba3c7" }, grid: { color: "rgba(255,255,255,0.06)" } },
        },
      },
    });
  }

  async function loadAdminMetrics() {
    const result = await apiFetch("/api/admin/metrics");
    if (!result.ok || !result.data.success) {
      $("admin-health").textContent = "Degraded (login as admin@traffic.dev / admin123)";
      $("admin-model-type").textContent = "GradientBoostingRegressor";
      $("admin-mse").textContent = "0.0042";
      $("admin-r2").textContent = "0.87";
      $("admin-rows").textContent = "1200";
      $("admin-trained").textContent = "N/A";
      initEpochChart([0.09, 0.07, 0.05, 0.04, 0.032, 0.028]);
      return;
    }
    const data = result.data;
    $("admin-health").textContent = data.system_health;
    $("admin-model-type").textContent = data.model.type;
    $("admin-csv-rows").textContent = data.model.csv_rows || data.dataset.rows || "--";
    $("admin-mse").textContent = data.model.mse;
    $("admin-r2").textContent = data.model.r2;
    $("admin-rows").textContent = data.model.training_rows;
    $("admin-trained").textContent = data.model.last_trained || "N/A";
    initEpochChart(data.model.epoch_loss);

    const usersBody = $("admin-users-body");
    usersBody.innerHTML = "";
    (data.users || []).forEach(function (user) {
      const row = document.createElement("tr");
      row.innerHTML =
        "<td>" + user.id + "</td>" +
        "<td>" + user.username + "</td>" +
        "<td>" + user.email + "</td>" +
        "<td>" + user.role + "</td>" +
        "<td>" + (user.oauth_provider || "—") + "</td>";
      usersBody.appendChild(row);
    });

    const logsBody = $("admin-logs-body");
    logsBody.innerHTML = "";
    (data.recent_logs || []).forEach(function (log) {
      const row = document.createElement("tr");
      row.innerHTML =
        "<td>" + log.id + "</td>" +
        "<td>" + log.intersection_id + "</td>" +
        "<td>" + (log.recorded_at || "").substring(0, 19) + "</td>" +
        "<td>" + log.hour + "</td>" +
        "<td>" + log.historical_count + "</td>" +
        "<td>" + (log.congestion_level * 100).toFixed(1) + "%</td>";
      logsBody.appendChild(row);
    });
  }

  async function handleRetrain() {
    const btn = $("btn-retrain");
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Training...';
    const result = await apiFetch("/api/admin/retrain", { method: "POST" });
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-rotate"></i> Retrain Model';
    if (result.ok && result.data.success) {
      loadAdminMetrics();
    } else {
      alert("Retrain failed. Ensure you are logged in as admin.");
    }
  }

  function startAdminPolling() {
    stopAdminPolling();
    state.adminTimer = setInterval(loadAdminMetrics, 10000);
  }

  function stopAdminPolling() {
    if (state.adminTimer) {
      clearInterval(state.adminTimer);
      state.adminTimer = null;
    }
  }

  function bindEvents() {
    $("login-form").addEventListener("submit", handleLogin);
    $("register-form").addEventListener("submit", handleRegister);
    $("oauth-google").addEventListener("click", function () { handleOAuth("google"); });
    $("oauth-github").addEventListener("click", function () { handleOAuth("github"); });
    $("btn-logout").addEventListener("click", handleLogout);
    $("btn-admin").addEventListener("click", function () { switchView("admin"); });
    $("btn-back-dashboard").addEventListener("click", function () { switchView("dashboard"); });
    $("nlp-submit").addEventListener("click", handleNlpCommand);
    $("nlp-command").addEventListener("keydown", function (e) {
      if (e.key === "Enter") handleNlpCommand();
    });
    $("cal-prev").addEventListener("click", function () {
      state.calendarMonth -= 1;
      if (state.calendarMonth < 0) {
        state.calendarMonth = 11;
        state.calendarYear -= 1;
      }
      buildCalendar();
    });
    $("cal-next").addEventListener("click", function () {
      state.calendarMonth += 1;
      if (state.calendarMonth > 11) {
        state.calendarMonth = 0;
        state.calendarYear += 1;
      }
      buildCalendar();
    });
    $("btn-retrain").addEventListener("click", handleRetrain);
    const kaggleBtn = $("btn-kaggle-sync");
    if (kaggleBtn) kaggleBtn.addEventListener("click", handleKaggleSync);
  }

  async function init() {
    initAuthTabs();
    bindEvents();
    await loadOAuthConfigs();
    initMap();
    buildCalendar();
    await checkSession();
    if (!state.user) {
      switchView("auth");
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
