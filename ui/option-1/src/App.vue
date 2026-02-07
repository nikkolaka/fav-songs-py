<template>
  <div>
    <div class="scanlines" aria-hidden="true"></div>

    <div class="app-shell">
      <aside class="side-column panel-frame">
        <div class="brand-block panel-inset">
          <p class="brand-label">FavSongs</p>
          <p class="brand-name">Super Secret Dashboard</p>
        </div>

        <div class="status-block panel-inset">
          <p class="section-label">Service Health</p>
          <div class="status-row">
            <span class="status-led" aria-hidden="true"></span>
            <div>
              <p class="status-main" id="service-status">{{ serviceStatus }}</p>
              <p class="status-sub" id="health-subtext">{{ healthSubtext }}</p>
            </div>
          </div>
        </div>
      </aside>

      <main class="main-column">
        <header class="console-rack panel-frame">
          <div class="rack-line rack-title panel-inset">
            <div class="window-title">
              <span class="title-dot"></span>
              <span>FavSongs v1.0</span>
            </div>
            <div class="window-controls" aria-hidden="true">
              <span class="control-btn">_</span>
              <span class="control-btn">[]</span>
              <span class="control-btn">X</span>
            </div>
          </div>

          <div class="rack-line rack-nav panel-inset">
            <p class="section-label">Mode</p>
            <nav class="nav nav-top" aria-label="Primary mirror">
              <button
                class="nav-link"
                :class="{ 'is-active': activeView === 'dashboard' }"
                type="button"
                @click="setActiveView('dashboard')"
              >
                Dashboard
              </button>
              <button
                class="nav-link"
                :class="{ 'is-active': activeView === 'favorites' }"
                type="button"
                @click="setActiveView('favorites')"
              >
                Favorites
              </button>
              <button
                class="nav-link"
                :class="{ 'is-active': activeView === 'settings' }"
                type="button"
                @click="setActiveView('settings')"
              >
                Settings
              </button>
            </nav>
            <div class="vu-meter" aria-hidden="true">
              <span></span><span></span><span></span><span></span><span></span><span></span><span></span>
            </div>
          </div>

          <div class="rack-line rack-control panel-inset">
            <div class="select-wrap">
              <label for="account-select">Spotify Account</label>
              <select
                id="account-select"
                aria-label="Account"
                v-model="selectedAccountId"
                :disabled="!controlsEnabled"
                @change="onAccountChange"
              >
                <option v-if="!accounts.length" value="">No connected accounts</option>
                <option v-for="account in accounts" :key="account.id" :value="account.id">
                  {{ account.display_name }}
                </option>
              </select>
            </div>
            <div class="rack-actions">
              <button class="btn" id="connect-spotify" type="button" @click="connectSpotify">
                Connect Spotify
              </button>
            </div>
          </div>
        </header>

        <p class="message" :class="{ hidden: !globalMessage, error: globalMessageKind === 'error' }">
          {{ globalMessage }}
        </p>

        <section class="view" :class="{ hidden: activeView !== 'dashboard' }" data-view="dashboard">
          <div class="content-grid two-up">
            <article class="panel-frame feature-panel">
              <div class="panel-head">
                <h2>Now Playing</h2>
                <span class="status-pill" id="tracker-status">{{ trackerRunning ? "Running" : "Stopped" }}</span>
              </div>

              <div class="now-playing panel-inset">
                <div class="album-art" aria-hidden="true"></div>
                <div>
                  <p class="track-title" id="track-title">{{ dashboard.trackTitle }}</p>
                  <p class="track-artist" id="track-artist">{{ dashboard.trackArtist }}</p>

                  <div class="progress-slot">
                    <div class="progress-bar" id="progress-bar" :style="{ width: progressBarWidth }"></div>
                  </div>

                  <div class="progress-meta">
                    <span id="progress-time">{{ progressTime }}</span>
                    <span id="progress-total">{{ progressTotal }}</span>
                  </div>
                </div>
              </div>

              <div class="panel-actions">
                <button
                  class="btn ghost"
                  id="toggle-tracker"
                  type="button"
                  :disabled="!controlsEnabled"
                  @click="toggleTracker"
                >
                  {{ trackerRunning ? "Stop Tracking" : "Start Tracking" }}
                </button>
                <button
                  class="btn ghost"
                  id="refresh-dashboard"
                  type="button"
                  :disabled="!controlsEnabled"
                  @click="refreshDashboard"
                >
                  Refresh
                </button>
              </div>
            </article>

            <article class="panel-frame">
              <h2>Session Summary</h2>

              <div class="stats-grid">
                <div class="stat panel-inset">
                  <p class="stat-label">Tracks counted (24h)</p>
                  <p class="stat-value" id="tracks-counted">{{ dashboard.tracksCounted }}</p>
                </div>
                <div class="stat panel-inset">
                  <p class="stat-label">Completion threshold</p>
                  <p class="stat-value" id="completion-threshold">{{ completionThresholdText }}</p>
                </div>
                <div class="stat panel-inset">
                  <p class="stat-label">Next favorite in</p>
                  <p class="stat-value" id="next-favorite">{{ nextFavoriteText }}</p>
                </div>
              </div>

              <div class="queue-list">
                <p class="section-label soft">Recent plays</p>
                <ul id="recent-plays">
                  <li v-if="!dashboard.recentPlays.length">
                    <span>{{ recentPlaysEmptyText }}</span>
                    <span>-</span>
                  </li>
                  <li v-for="play in dashboard.recentPlays" :key="play.track_id + '-' + play.played_at">
                    <span>{{ play.artist }} - {{ play.name }}</span>
                    <span>{{ formatRelativeTime(play.played_at) }}</span>
                  </li>
                </ul>
              </div>
            </article>
          </div>
        </section>

        <section class="view" :class="{ hidden: activeView !== 'favorites' }" data-view="favorites">
          <article class="panel-frame">
            <div class="panel-head">
              <h2>Favorites Ledger</h2>
            </div>
            <div class="table-wrap panel-inset">
              <table class="table" aria-label="Favorites">
                <thead>
                  <tr>
                    <th>Track</th>
                    <th>Artist</th>
                    <th>Plays</th>
                    <th>Last played</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody id="favorites-table">
                  <tr v-if="!favorites.length">
                    <td colspan="5">{{ favoritesEmptyText }}</td>
                  </tr>
                  <tr v-for="favorite in favorites" :key="favorite.track_id">
                    <td>{{ favorite.name }}</td>
                    <td>{{ favorite.artist }}</td>
                    <td>{{ favorite.occurrences }}</td>
                    <td>{{ formatRelativeTime(favorite.last_played) }}</td>
                    <td>
                      <button
                        class="btn ghost force-add-btn"
                        type="button"
                        :disabled="!controlsEnabled"
                        @click="forceAdd(favorite.track_id)"
                      >
                        Force add
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </article>
        </section>

        <section class="view" :class="{ hidden: activeView !== 'settings' }" data-view="settings">
          <div class="content-grid two-up">
            <article class="panel-frame">
              <h2>Tracking Controls</h2>
              <form class="form" id="tracking-form" @submit.prevent="saveTrackingSettings">
                <label class="slider-field">
                  Favorite threshold
                  <div class="slider-row">
                    <div class="slider-track-wrap ref-21">
                      <input
                        type="range"
                        id="favorite-threshold"
                        min="1"
                        max="20"
                        step="1"
                        v-model.number="settings.favorite_threshold"
                        :disabled="!controlsEnabled"
                      />
                    </div>
                    <output class="slider-value" id="favorite-threshold-value" for="favorite-threshold">
                      {{ favoriteThresholdValue }}
                    </output>
                  </div>
                  <p class="slider-reference">Default - 5</p>
                </label>

                <label class="slider-field">
                  Completion ratio
                  <div class="slider-row">
                    <div class="slider-track-wrap ref-60">
                      <input
                        type="range"
                        id="completion-ratio"
                        min="0.5"
                        max="1"
                        step="0.05"
                        v-model.number="settings.min_completion_ratio"
                        :disabled="!controlsEnabled"
                      />
                    </div>
                    <output class="slider-value" id="completion-ratio-value" for="completion-ratio">
                      {{ completionRatioValue }}
                    </output>
                  </div>
                  <p class="slider-reference">Default - 80%</p>
                </label>

                <label class="slider-field">
                  Check interval (seconds)
                  <div class="slider-row">
                    <div class="slider-track-wrap ref-2">
                      <input
                        type="range"
                        id="check-interval"
                        min="3"
                        max="300"
                        step="1"
                        v-model.number="settings.check_interval"
                        :disabled="!controlsEnabled"
                      />
                    </div>
                    <output class="slider-value" id="check-interval-value" for="check-interval">
                      {{ checkIntervalValue }}
                    </output>
                  </div>
                  <p class="slider-reference">Default - 10s</p>
                </label>

                <label class="slider-field">
                  Min play gap
                  <div class="slider-row">
                    <div class="slider-track-wrap ref-17">
                      <input
                        type="range"
                        id="play-gap"
                        min="0"
                        max="1800000"
                        step="30000"
                        v-model.number="settings.min_play_gap_ms"
                        :disabled="!controlsEnabled"
                      />
                    </div>
                    <output class="slider-value" id="play-gap-value" for="play-gap">
                      {{ playGapValue }}
                    </output>
                  </div>
                  <p class="slider-reference">Default - 5m 0s</p>
                </label>

                <button class="btn" type="submit" :disabled="!controlsEnabled">Save settings</button>
              </form>
            </article>

            <article class="panel-frame">
              <h2>Playlist Targets</h2>
              <form class="form" id="playlist-form" @submit.prevent="savePlaylistSettings">
                <label>
                  Playlist name
                  <input
                    type="text"
                    id="playlist-name"
                    v-model="settings.playlist_name"
                    required
                    :disabled="!controlsEnabled"
                  />
                </label>
                <label>
                  Playlist privacy
                  <select
                    id="playlist-public"
                    v-model="settings.playlist_public"
                    :disabled="!controlsEnabled"
                  >
                    <option :value="true">Public</option>
                    <option :value="false">Private</option>
                  </select>
                </label>
                <label class="inline-check">
                  <input
                    type="checkbox"
                    id="auto-add"
                    v-model="settings.auto_add_enabled"
                    :disabled="!controlsEnabled"
                  />
                  Auto add when threshold reached
                </label>
                <button class="btn" type="submit" :disabled="!controlsEnabled">Save playlist settings</button>
              </form>
            </article>
          </div>
        </section>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";

const accounts = ref([]);
const selectedAccountId = ref("");
const trackerRunning = ref(false);
const activeView = ref("dashboard");

const globalMessage = ref("");
const globalMessageKind = ref("info");
const serviceStatus = ref("Connected");
const healthSubtext = ref("Ready");

const dashboard = reactive({
  trackTitle: "No active track",
  trackArtist: "Connect a Spotify account to begin",
  progressRatio: 0,
  progressMs: 0,
  durationMs: 0,
  tracksCounted: 0,
  completionThreshold: 0.8,
  nextFavorite: 0,
  recentPlays: [],
});

const favorites = ref([]);

const settings = reactive({
  favorite_threshold: 5,
  min_completion_ratio: 0.8,
  check_interval: 10,
  min_play_gap_ms: 300000,
  playlist_name: "Favourite Songs - Whatsit",
  playlist_public: true,
  auto_add_enabled: true,
});

const controlsEnabled = computed(() => accounts.value.length > 0);

const progressBarWidth = computed(() => {
  const percent = Math.min(Math.max(dashboard.progressRatio * 100, 0), 100);
  return `${percent}%`;
});
const progressTime = computed(() => formatTime(dashboard.progressMs));
const progressTotal = computed(() => formatTime(dashboard.durationMs));
const completionThresholdText = computed(
  () => `${Math.round(dashboard.completionThreshold * 100)}%`
);
const nextFavoriteText = computed(() => `${dashboard.nextFavorite} plays`);
const favoriteThresholdValue = computed(() => String(settings.favorite_threshold));
const completionRatioValue = computed(
  () => `${Math.round(settings.min_completion_ratio * 100)}%`
);
const checkIntervalValue = computed(() => `${Math.round(settings.check_interval)}s`);
const playGapValue = computed(() => formatDuration(settings.min_play_gap_ms));
const recentPlaysEmptyText = computed(() =>
  controlsEnabled.value ? "No recent plays" : "No plays logged yet"
);
const favoritesEmptyText = computed(() =>
  controlsEnabled.value ? "No favorites yet." : "No favorites yet. Start a tracker and play music."
);

function showMessage(message, kind = "info") {
  if (!message) {
    globalMessage.value = "";
    globalMessageKind.value = "info";
    return;
  }

  globalMessage.value = message;
  globalMessageKind.value = kind;
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

function resetDashboard() {
  dashboard.trackTitle = "No active track";
  dashboard.trackArtist = "Connect a Spotify account to begin";
  dashboard.progressRatio = 0;
  dashboard.progressMs = 0;
  dashboard.durationMs = 0;
  dashboard.tracksCounted = 0;
  dashboard.completionThreshold = 0.8;
  dashboard.nextFavorite = 0;
  dashboard.recentPlays = [];
  trackerRunning.value = false;
}

function renderAccounts(list) {
  accounts.value = list;

  if (!list.length) {
    selectedAccountId.value = "";
    resetDashboard();
    favorites.value = [];
    healthSubtext.value = "No accounts connected";
    return;
  }

  const selectedStillExists = list.some((acct) => acct.id === selectedAccountId.value);
  if (!selectedStillExists) {
    selectedAccountId.value = list[0].id;
  }
}

function renderDashboard(data) {
  const nowPlaying = data.now_playing;
  const account = data.account;

  trackerRunning.value = Boolean(account.tracker_running);

  if (nowPlaying && !nowPlaying.error && nowPlaying.name) {
    dashboard.trackTitle = nowPlaying.name;
    dashboard.trackArtist = nowPlaying.artist;
    dashboard.progressRatio = nowPlaying.completion_ratio;
    dashboard.progressMs = nowPlaying.progress_ms;
    dashboard.durationMs = nowPlaying.duration_ms;
  } else {
    dashboard.trackTitle = "No active track";
    dashboard.trackArtist = nowPlaying && nowPlaying.error ? nowPlaying.error : "Spotify playback is idle";
    dashboard.progressRatio = 0;
    dashboard.progressMs = 0;
    dashboard.durationMs = 0;
  }

  dashboard.tracksCounted = data.stats.tracks_counted_24h;
  dashboard.completionThreshold = data.stats.completion_threshold;
  dashboard.nextFavorite = data.stats.next_favorite;
  dashboard.recentPlays = data.recent_plays || [];

  serviceStatus.value = "Connected";
  healthSubtext.value = `${account.display_name} selected`;
}

function renderFavorites(list) {
  favorites.value = list || [];
}

function renderSettings(data) {
  const payload = data.settings || data;

  if (!payload) {
    return;
  }

  settings.favorite_threshold = clamp(
    payload.favorite_threshold ?? settings.favorite_threshold,
    1,
    20
  );
  settings.min_completion_ratio = clamp(
    payload.min_completion_ratio ?? settings.min_completion_ratio,
    0.5,
    1
  );
  settings.check_interval = clamp(payload.check_interval ?? settings.check_interval, 3, 300);
  settings.min_play_gap_ms = clamp(
    payload.min_play_gap_ms ?? settings.min_play_gap_ms,
    0,
    1_800_000
  );
  settings.playlist_name = payload.playlist_name ?? settings.playlist_name;
  if (payload.playlist_public !== undefined) {
    settings.playlist_public = Boolean(payload.playlist_public);
  }
  if (payload.auto_add_enabled !== undefined) {
    settings.auto_add_enabled = Boolean(payload.auto_add_enabled);
  }
}

async function loadAccountData() {
  if (!selectedAccountId.value) {
    return;
  }

  const userId = selectedAccountId.value;

  const [dashboardPayload, favoritesPayload, settingsPayload] = await Promise.all([
    apiRequest(`/api/accounts/${userId}/dashboard`),
    apiRequest(`/api/accounts/${userId}/favorites`),
    apiRequest(`/api/accounts/${userId}/settings`),
  ]);

  renderDashboard(dashboardPayload);
  renderFavorites(favoritesPayload.favorites || []);
  renderSettings(settingsPayload);
}

async function loadAccountsAndData() {
  const accountPayload = await apiRequest("/api/accounts");
  renderAccounts(accountPayload.accounts || []);
  if (selectedAccountId.value) {
    await loadAccountData();
  }
}

async function onAccountChange() {
  showMessage("");
  try {
    await loadAccountData();
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function connectSpotify() {
  showMessage("");
  try {
    const payload = await apiRequest("/api/auth/spotify/start", { method: "POST" });
    window.location.href = payload.auth_url;
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function refreshDashboard() {
  if (!selectedAccountId.value) {
    return;
  }

  showMessage("");
  try {
    await loadAccountData();
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function toggleTracker() {
  if (!selectedAccountId.value) {
    return;
  }

  showMessage("");
  const action = trackerRunning.value ? "stop" : "start";

  try {
    await apiRequest(`/api/accounts/${selectedAccountId.value}/tracker/${action}`, {
      method: "POST",
    });
    await loadAccountData();
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function saveTrackingSettings() {
  if (!selectedAccountId.value) {
    return;
  }

  showMessage("");

  const payload = {
    favorite_threshold: Number(settings.favorite_threshold),
    min_completion_ratio: Number(settings.min_completion_ratio),
    check_interval: Number(settings.check_interval),
    min_play_gap_ms: Number(settings.min_play_gap_ms),
  };

  try {
    await apiRequest(`/api/accounts/${selectedAccountId.value}/settings`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showMessage("Tracking settings saved.");
    await loadAccountData();
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function savePlaylistSettings() {
  if (!selectedAccountId.value) {
    return;
  }

  showMessage("");

  const payload = {
    playlist_name: settings.playlist_name,
    playlist_public: Boolean(settings.playlist_public),
    auto_add_enabled: Boolean(settings.auto_add_enabled),
  };

  try {
    await apiRequest(`/api/accounts/${selectedAccountId.value}/settings`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showMessage("Playlist settings saved.");
    await loadAccountData();
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function forceAdd(trackId) {
  if (!trackId || !selectedAccountId.value) {
    return;
  }

  showMessage("");

  try {
    await apiRequest(`/api/accounts/${selectedAccountId.value}/favorites/${trackId}/force-add`, {
      method: "POST",
    });
    showMessage("Track queued for playlist add.");
  } catch (error) {
    showMessage(error.message, "error");
  }
}

function setActiveView(view) {
  activeView.value = view;
}

async function initialize() {
  if (window.location.protocol === "file:") {
    serviceStatus.value = "Offline";
    healthSubtext.value = "Use HTTP server";
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
    serviceStatus.value = "Error";
    healthSubtext.value = "API unavailable";
    showMessage(error.message, "error");
  }
}

onMounted(() => {
  initialize();
});
</script>
