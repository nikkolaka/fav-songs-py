const state = {
  accounts: [],
  selectedAccountId: null,
  trackerRunning: false,
};

const accountSelect = document.getElementById("account-select");
const connectSpotifyButton = document.getElementById("connect-spotify");
const refreshDashboardButton = document.getElementById("refresh-dashboard");
const toggleTrackerButton = document.getElementById("toggle-tracker");
const globalMessage = document.getElementById("global-message");
const serviceStatus = document.getElementById("service-status");
const healthSubtext = document.getElementById("health-subtext");

const trackTitle = document.getElementById("track-title");
const trackArtist = document.getElementById("track-artist");
const progressBar = document.getElementById("progress-bar");
const progressTime = document.getElementById("progress-time");
const progressTotal = document.getElementById("progress-total");
const trackerStatus = document.getElementById("tracker-status");
const tracksCounted = document.getElementById("tracks-counted");
const completionThreshold = document.getElementById("completion-threshold");
const nextFavorite = document.getElementById("next-favorite");
const recentPlays = document.getElementById("recent-plays");

const favoritesTable = document.getElementById("favorites-table");

const trackingForm = document.getElementById("tracking-form");
const playlistForm = document.getElementById("playlist-form");

const favoriteThresholdInput = document.getElementById("favorite-threshold");
const completionRatioInput = document.getElementById("completion-ratio");
const checkIntervalInput = document.getElementById("check-interval");
const playGapInput = document.getElementById("play-gap");
const playlistNameInput = document.getElementById("playlist-name");
const playlistPublicInput = document.getElementById("playlist-public");
const autoAddInput = document.getElementById("auto-add");

const favoriteThresholdValue = document.getElementById("favorite-threshold-value");
const completionRatioValue = document.getElementById("completion-ratio-value");
const checkIntervalValue = document.getElementById("check-interval-value");
const playGapValue = document.getElementById("play-gap-value");

function showMessage(message, kind = "info") {
  if (!message) {
    globalMessage.textContent = "";
    globalMessage.classList.add("hidden");
    globalMessage.classList.remove("error");
    return;
  }

  globalMessage.textContent = message;
  globalMessage.classList.remove("hidden");
  globalMessage.classList.toggle("error", kind === "error");
}

function formatTime(ms) {
  const totalSeconds = Math.max(0, Math.floor((ms || 0) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function formatRelativeTime(ms) {
  if (!ms) {
    return "-";
  }

  const diffSeconds = Math.floor((Date.now() - ms) / 1000);
  if (diffSeconds < 60) {
    return `${Math.max(diffSeconds, 0)}s ago`;
  }
  if (diffSeconds < 3600) {
    return `${Math.floor(diffSeconds / 60)}m ago`;
  }
  if (diffSeconds < 86400) {
    return `${Math.floor(diffSeconds / 3600)}h ago`;
  }
  return `${Math.floor(diffSeconds / 86400)}d ago`;
}

function formatDuration(ms) {
  const totalSeconds = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${seconds}s`;
}

function clamp(value, min, max) {
  const num = Number(value);
  return Math.min(Math.max(num, min), max);
}

function syncSliderReadouts() {
  favoriteThresholdValue.textContent = String(Number(favoriteThresholdInput.value));
  completionRatioValue.textContent = `${Math.round(Number(completionRatioInput.value) * 100)}%`;
  checkIntervalValue.textContent = `${Math.round(Number(checkIntervalInput.value))}s`;
  playGapValue.textContent = formatDuration(playGapInput.value);
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function apiRequest(path, options = {}) {
  const fetchOptions = { ...options };
  fetchOptions.headers = { ...(options.headers || {}) };
  if (options.body && !fetchOptions.headers["Content-Type"]) {
    fetchOptions.headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, fetchOptions);
  const contentType = response.headers.get("content-type") || "";

  let payload;
  if (contentType.includes("application/json")) {
    payload = await response.json();
  } else {
    payload = await response.text();
  }

  if (!response.ok) {
    const detail = payload && payload.detail ? payload.detail : String(payload || "Unknown error");
    throw new Error(detail);
  }

  return payload;
}

function setControlsEnabled(enabled) {
  accountSelect.disabled = !enabled;
  refreshDashboardButton.disabled = !enabled;
  toggleTrackerButton.disabled = !enabled;
  trackingForm.querySelectorAll("input, button").forEach((node) => {
    node.disabled = !enabled;
  });
  playlistForm.querySelectorAll("input, select, button").forEach((node) => {
    node.disabled = !enabled;
  });
}

function renderAccounts(accounts) {
  state.accounts = accounts;

  if (!accounts.length) {
    state.selectedAccountId = null;
    accountSelect.innerHTML = '<option value="">No connected accounts</option>';
    setControlsEnabled(false);
    trackTitle.textContent = "No active track";
    trackArtist.textContent = "Connect a Spotify account to begin";
    recentPlays.innerHTML = "<li><span>No plays logged yet</span><span>-</span></li>";
    favoritesTable.innerHTML =
      '<tr><td colspan="5">No favorites yet. Start a tracker and play music.</td></tr>';
    trackerStatus.textContent = "Stopped";
    return;
  }

  const selectedStillExists = accounts.some((acct) => acct.id === state.selectedAccountId);
  if (!selectedStillExists) {
    state.selectedAccountId = accounts[0].id;
  }

  accountSelect.innerHTML = accounts
    .map((account) => {
      const selected = account.id === state.selectedAccountId ? "selected" : "";
      return `<option value="${escapeHtml(account.id)}" ${selected}>${escapeHtml(account.display_name)}</option>`;
    })
    .join("");

  setControlsEnabled(true);
}

function renderDashboard(data) {
  const nowPlaying = data.now_playing;
  const account = data.account;

  state.trackerRunning = Boolean(account.tracker_running);
  trackerStatus.textContent = state.trackerRunning ? "Running" : "Stopped";
  toggleTrackerButton.textContent = state.trackerRunning ? "Stop Tracking" : "Start Tracking";

  if (nowPlaying && !nowPlaying.error && nowPlaying.name) {
    trackTitle.textContent = nowPlaying.name;
    trackArtist.textContent = nowPlaying.artist;
    progressBar.style.width = `${Math.min(Math.max(nowPlaying.completion_ratio * 100, 0), 100)}%`;
    progressTime.textContent = formatTime(nowPlaying.progress_ms);
    progressTotal.textContent = formatTime(nowPlaying.duration_ms);
  } else {
    trackTitle.textContent = "No active track";
    trackArtist.textContent = nowPlaying && nowPlaying.error ? nowPlaying.error : "Spotify playback is idle";
    progressBar.style.width = "0%";
    progressTime.textContent = "00:00";
    progressTotal.textContent = "00:00";
  }

  tracksCounted.textContent = String(data.stats.tracks_counted_24h);
  completionThreshold.textContent = `${Math.round(data.stats.completion_threshold * 100)}%`;
  nextFavorite.textContent = `${data.stats.next_favorite} plays`;

  if (data.recent_plays.length) {
    recentPlays.innerHTML = data.recent_plays
      .map(
        (play) =>
          `<li><span>${escapeHtml(play.artist)} - ${escapeHtml(play.name)}</span><span>${formatRelativeTime(play.played_at)}</span></li>`
      )
      .join("");
  } else {
    recentPlays.innerHTML = "<li><span>No recent plays</span><span>-</span></li>";
  }

  serviceStatus.textContent = "Connected";
  healthSubtext.textContent = `${account.display_name} selected`;
}

function renderFavorites(favorites) {
  if (!favorites.length) {
    favoritesTable.innerHTML = '<tr><td colspan="5">No favorites yet.</td></tr>';
    return;
  }

  favoritesTable.innerHTML = favorites
    .map(
      (favorite) => `
        <tr>
          <td>${escapeHtml(favorite.name)}</td>
          <td>${escapeHtml(favorite.artist)}</td>
          <td>${favorite.occurrences}</td>
          <td>${formatRelativeTime(favorite.last_played)}</td>
          <td><button class="btn ghost force-add-btn" data-track-id="${escapeHtml(favorite.track_id)}">Force add</button></td>
        </tr>
      `
    )
    .join("");
}

function renderSettings(settings) {
  favoriteThresholdInput.value = String(clamp(settings.favorite_threshold, 1, 20));
  completionRatioInput.value = String(clamp(settings.min_completion_ratio, 0.5, 1));
  checkIntervalInput.value = String(clamp(settings.check_interval, 3, 300));
  playGapInput.value = String(clamp(settings.min_play_gap_ms, 0, 1_800_000));
  playlistNameInput.value = settings.playlist_name;
  playlistPublicInput.value = settings.playlist_public ? "public" : "private";
  autoAddInput.checked = Boolean(settings.auto_add_enabled);
  syncSliderReadouts();
}

async function loadAccountData() {
  if (!state.selectedAccountId) {
    return;
  }

  const userId = state.selectedAccountId;

  const [dashboard, favorites, settings] = await Promise.all([
    apiRequest(`/api/accounts/${userId}/dashboard`),
    apiRequest(`/api/accounts/${userId}/favorites`),
    apiRequest(`/api/accounts/${userId}/settings`),
  ]);

  renderDashboard(dashboard);
  renderFavorites(favorites.favorites || []);
  renderSettings(settings);
}

async function loadAccountsAndData() {
  const accountPayload = await apiRequest("/api/accounts");
  renderAccounts(accountPayload.accounts || []);
  if (state.selectedAccountId) {
    await loadAccountData();
  }
}

accountSelect.addEventListener("change", async (event) => {
  state.selectedAccountId = event.target.value;
  showMessage("");
  try {
    await loadAccountData();
  } catch (error) {
    showMessage(error.message, "error");
  }
});

connectSpotifyButton.addEventListener("click", async () => {
  showMessage("");
  try {
    const payload = await apiRequest("/api/auth/spotify/start", { method: "POST" });
    window.location.href = payload.auth_url;
  } catch (error) {
    showMessage(error.message, "error");
  }
});

refreshDashboardButton.addEventListener("click", async () => {
  if (!state.selectedAccountId) {
    return;
  }

  showMessage("");
  try {
    await loadAccountData();
  } catch (error) {
    showMessage(error.message, "error");
  }
});

toggleTrackerButton.addEventListener("click", async () => {
  if (!state.selectedAccountId) {
    return;
  }

  showMessage("");
  const action = state.trackerRunning ? "stop" : "start";

  try {
    await apiRequest(`/api/accounts/${state.selectedAccountId}/tracker/${action}`, {
      method: "POST",
    });
    await loadAccountData();
  } catch (error) {
    showMessage(error.message, "error");
  }
});

trackingForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selectedAccountId) {
    return;
  }

  showMessage("");

  const payload = {
    favorite_threshold: Number(favoriteThresholdInput.value),
    min_completion_ratio: Number(completionRatioInput.value),
    check_interval: Number(checkIntervalInput.value),
    min_play_gap_ms: Number(playGapInput.value),
  };

  try {
    await apiRequest(`/api/accounts/${state.selectedAccountId}/settings`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showMessage("Tracking settings saved.");
    await loadAccountData();
  } catch (error) {
    showMessage(error.message, "error");
  }
});

playlistForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selectedAccountId) {
    return;
  }

  showMessage("");

  const payload = {
    playlist_name: playlistNameInput.value,
    playlist_public: playlistPublicInput.value === "public",
    auto_add_enabled: autoAddInput.checked,
  };

  try {
    await apiRequest(`/api/accounts/${state.selectedAccountId}/settings`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showMessage("Playlist settings saved.");
    await loadAccountData();
  } catch (error) {
    showMessage(error.message, "error");
  }
});

favoritesTable.addEventListener("click", async (event) => {
  const button = event.target.closest(".force-add-btn");
  if (!button || !state.selectedAccountId) {
    return;
  }

  const trackId = button.dataset.trackId;
  if (!trackId) {
    return;
  }

  showMessage("");

  try {
    await apiRequest(`/api/accounts/${state.selectedAccountId}/favorites/${trackId}/force-add`, {
      method: "POST",
    });
    showMessage("Track queued for playlist add.");
  } catch (error) {
    showMessage(error.message, "error");
  }
});

const navLinks = document.querySelectorAll(".nav-link");
const views = document.querySelectorAll(".view");

navLinks.forEach((link) => {
  link.addEventListener("click", () => {
    navLinks.forEach((item) => item.classList.remove("is-active"));

    const target = link.dataset.view;
    document.querySelectorAll(`.nav-link[data-view="${target}"]`).forEach((match) => {
      match.classList.add("is-active");
    });

    views.forEach((view) => {
      view.classList.toggle("hidden", view.dataset.view !== target);
    });
  });
});

[favoriteThresholdInput, completionRatioInput, checkIntervalInput, playGapInput].forEach((slider) => {
  slider.addEventListener("input", syncSliderReadouts);
});

async function initialize() {
  syncSliderReadouts();

  if (window.location.protocol === "file:") {
    serviceStatus.textContent = "Offline";
    healthSubtext.textContent = "Use HTTP server";
    showMessage(
      "Open this app via http://<host>:<port>, not file://. API calls are blocked from local files.",
      "error"
    );
    return;
  }

  const query = new URLSearchParams(window.location.search);
  if (query.get("oauth") === "connected") {
    showMessage("Spotify account connected.");
    window.history.replaceState({}, document.title, "/");
  } else if (query.get("oauth") === "error") {
    showMessage("Spotify connection was cancelled or failed.", "error");
    window.history.replaceState({}, document.title, "/");
  }

  try {
    await loadAccountsAndData();
  } catch (error) {
    serviceStatus.textContent = "Error";
    healthSubtext.textContent = "API unavailable";
    showMessage(error.message, "error");
  }
}

initialize();
