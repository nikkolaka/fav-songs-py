<template>
  <div class="crt-overlay" aria-hidden="true"></div>

  <div class="app-shell">
    <header class="masthead panel">
      <div class="title-stack">
        <p class="eyebrow">FavSongs Node</p>
        <h1>Local Music Ops Board</h1>
      </div>
      <div class="badges">
        <span class="badge" :class="service.local_only_mode ? 'badge-ok' : 'badge-warn'">
          {{ service.local_only_mode ? "LAN ONLY" : "OPEN MODE" }}
        </span>
        <span class="badge" :class="service.accepting_new_users ? 'badge-ok' : 'badge-warn'">
          SEATS {{ service.connected_users }}/{{ service.max_connected_users }}
        </span>
      </div>
    </header>

    <section class="status-strip panel">
      <article class="status-item">
        <p class="status-label">Service</p>
        <p class="status-value">{{ serviceStatusText }}</p>
        <p class="status-sub">{{ serviceSubtext }}</p>
      </article>
      <article class="status-item">
        <p class="status-label">Account</p>
        <p class="status-value">{{ accountLabel }}</p>
        <p class="status-sub">{{ trackerRunning ? "Tracking on" : "Tracking idle" }}</p>
      </article>
      <article class="status-item actions">
        <button class="btn" type="button" @click="connectSpotify">Connect Spotify</button>
        <button class="btn btn-ghost" type="button" :disabled="!controlsEnabled" @click="refreshDashboard">
          Refresh
        </button>
      </article>
    </section>

    <p class="message" :class="{ hidden: !globalMessage, error: globalMessageKind === 'error' }">
      {{ globalMessage || "-" }}
    </p>

    <nav class="tabbar panel" aria-label="View selector">
      <button
        class="tab"
        :class="{ active: activeView === 'dashboard' }"
        type="button"
        @click="setActiveView('dashboard')"
      >
        Dashboard
      </button>
      <button
        class="tab"
        :class="{ active: activeView === 'favorites' }"
        type="button"
        @click="setActiveView('favorites')"
      >
        Favorites
      </button>
      <button
        class="tab"
        :class="{ active: activeView === 'settings' }"
        type="button"
        @click="setActiveView('settings')"
      >
        Settings
      </button>
    </nav>

    <section v-if="activeView === 'dashboard'" class="view-grid">
      <article class="panel card">
        <div class="card-head">
          <h2>Now Playing</h2>
          <span class="chip">{{ trackerRunning ? "RUNNING" : "STOPPED" }}</span>
        </div>

        <div class="track-block">
          <p class="track-title">{{ dashboard.trackTitle }}</p>
          <p class="track-artist">{{ dashboard.trackArtist }}</p>
          <div class="progress-track">
            <div class="progress-bar" :style="{ width: progressBarWidth }"></div>
          </div>
          <div class="progress-meta">
            <span>{{ progressTime }}</span>
            <span>{{ progressTotal }}</span>
          </div>
        </div>

        <div class="row-actions">
          <button
            class="btn"
            type="button"
            :disabled="!controlsEnabled"
            @click="toggleTracker"
          >
            {{ trackerRunning ? "Stop Tracker" : "Start Tracker" }}
          </button>
        </div>
      </article>

      <article class="panel card">
        <div class="card-head">
          <h2>Session Stats</h2>
        </div>

        <ul class="stat-list">
          <li>
            <span>Tracks counted (24h)</span>
            <strong>{{ dashboard.tracksCounted }}</strong>
          </li>
          <li>
            <span>Completion threshold</span>
            <strong>{{ completionThresholdText }}</strong>
          </li>
          <li>
            <span>Next favorite in</span>
            <strong>{{ nextFavoriteText }}</strong>
          </li>
          <li>
            <span>Open seats</span>
            <strong>{{ service.remaining_slots }}</strong>
          </li>
        </ul>
      </article>

      <article class="panel card full-width">
        <div class="card-head">
          <h2>Recent Plays</h2>
        </div>

        <ul class="recent-list">
          <li v-if="!dashboard.recentPlays.length">
            <span>{{ recentPlaysEmptyText }}</span>
            <span>-</span>
          </li>
          <li v-for="play in dashboard.recentPlays" :key="play.track_id + '-' + play.played_at">
            <span>{{ play.artist }} - {{ play.name }}</span>
            <span>{{ formatRelativeTime(play.played_at) }}</span>
          </li>
        </ul>
      </article>
    </section>

    <section v-if="activeView === 'favorites'" class="view-single">
      <article class="panel card">
        <div class="card-head">
          <h2>Favorites Ledger</h2>
        </div>

        <div class="table-wrap">
          <table class="table" aria-label="Favorites">
            <thead>
              <tr>
                <th>Track</th>
                <th>Artist</th>
                <th>Plays</th>
                <th>Last played</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
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
                    class="btn btn-ghost compact"
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

    <section v-if="activeView === 'settings'" class="view-grid">
      <article class="panel card">
        <div class="card-head">
          <h2>Tracking</h2>
        </div>

        <form class="form" @submit.prevent="saveTrackingSettings">
          <label>
            Favorite threshold
            <input
              v-model.number="settings.favorite_threshold"
              type="number"
              min="1"
              max="20"
              step="1"
              :disabled="!controlsEnabled"
            />
          </label>

          <label>
            Completion ratio
            <input
              v-model.number="settings.min_completion_ratio"
              type="number"
              min="0.5"
              max="1"
              step="0.05"
              :disabled="!controlsEnabled"
            />
          </label>

          <label>
            Check interval (seconds)
            <input
              v-model.number="settings.check_interval"
              type="number"
              min="3"
              max="300"
              step="1"
              :disabled="!controlsEnabled"
            />
          </label>

          <label>
            Min play gap (ms)
            <input
              v-model.number="settings.min_play_gap_ms"
              type="number"
              min="0"
              max="86400000"
              step="30000"
              :disabled="!controlsEnabled"
            />
          </label>

          <button class="btn" type="submit" :disabled="!controlsEnabled">Save tracking settings</button>
        </form>
      </article>

      <article class="panel card">
        <div class="card-head">
          <h2>Playlist</h2>
        </div>

        <form class="form" @submit.prevent="savePlaylistSettings">
          <label>
            Playlist name
            <input v-model="settings.playlist_name" type="text" required :disabled="!controlsEnabled" />
          </label>

          <label>
            Playlist privacy
            <select v-model="settings.playlist_public" :disabled="!controlsEnabled">
              <option :value="true">Public</option>
              <option :value="false">Private</option>
            </select>
          </label>

          <label class="check-line">
            <input v-model="settings.auto_add_enabled" type="checkbox" :disabled="!controlsEnabled" />
            Auto add when threshold reached
          </label>

          <button class="btn" type="submit" :disabled="!controlsEnabled">Save playlist settings</button>
        </form>
      </article>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";

const activeView = ref("dashboard");
const account = ref(null);
const trackerRunning = ref(false);

const globalMessage = ref("");
const globalMessageKind = ref("info");

const service = reactive({
  local_only_mode: true,
  connected_users: 0,
  max_connected_users: 6,
  remaining_slots: 6,
  accepting_new_users: true,
});

const dashboard = reactive({
  trackTitle: "No active track",
  trackArtist: "Connect Spotify to begin",
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

const controlsEnabled = computed(() => Boolean(account.value));
const accountLabel = computed(() =>
  account.value ? account.value.display_name : "No authenticated session"
);

const serviceStatusText = computed(() => {
  if (!service.local_only_mode) {
    return "Running (open mode)";
  }
  return "Running (LAN-only)";
});

const serviceSubtext = computed(() => {
  if (service.accepting_new_users) {
    return `${service.remaining_slots} seats available`;
  }
  return "Account cap reached";
});

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

const recentPlaysEmptyText = computed(() =>
  controlsEnabled.value ? "No recent plays" : "Login required for personal activity"
);
const favoritesEmptyText = computed(() =>
  controlsEnabled.value ? "No favorites yet" : "Login and start tracker to build favorites"
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
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }

  return payload;
}

function applyServiceStatus(payload) {
  const servicePayload = payload?.service || payload;
  if (!servicePayload) {
    return;
  }

  service.local_only_mode = Boolean(servicePayload.local_only_mode);
  service.connected_users = Number(servicePayload.connected_users || 0);
  service.max_connected_users = Number(servicePayload.max_connected_users || 6);
  service.remaining_slots = Number(servicePayload.remaining_slots || 0);
  service.accepting_new_users = Boolean(servicePayload.accepting_new_users);
}

function resetDashboard() {
  dashboard.trackTitle = "No active track";
  dashboard.trackArtist = "Connect Spotify to begin";
  dashboard.progressRatio = 0;
  dashboard.progressMs = 0;
  dashboard.durationMs = 0;
  dashboard.tracksCounted = 0;
  dashboard.completionThreshold = 0.8;
  dashboard.nextFavorite = 0;
  dashboard.recentPlays = [];
  trackerRunning.value = false;
}

function renderDashboard(data) {
  const nowPlaying = data.now_playing;
  const accountData = data.account;

  account.value = accountData;
  trackerRunning.value = Boolean(accountData?.tracker_running);

  if (nowPlaying && !nowPlaying.error && nowPlaying.name) {
    dashboard.trackTitle = nowPlaying.name;
    dashboard.trackArtist = nowPlaying.artist;
    dashboard.progressRatio = Number(nowPlaying.completion_ratio || 0);
    dashboard.progressMs = Number(nowPlaying.progress_ms || 0);
    dashboard.durationMs = Number(nowPlaying.duration_ms || 0);
  } else {
    dashboard.trackTitle = "No active track";
    dashboard.trackArtist = nowPlaying?.error || "Spotify playback is idle";
    dashboard.progressRatio = 0;
    dashboard.progressMs = 0;
    dashboard.durationMs = 0;
  }

  dashboard.tracksCounted = Number(data.stats?.tracks_counted_24h || 0);
  dashboard.completionThreshold = Number(data.stats?.completion_threshold || 0.8);
  dashboard.nextFavorite = Number(data.stats?.next_favorite || 0);
  dashboard.recentPlays = data.recent_plays || [];

  applyServiceStatus(data.service);
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
    86_400_000
  );
  settings.playlist_name = payload.playlist_name ?? settings.playlist_name;

  if (payload.playlist_public !== undefined) {
    settings.playlist_public = Boolean(payload.playlist_public);
  }
  if (payload.auto_add_enabled !== undefined) {
    settings.auto_add_enabled = Boolean(payload.auto_add_enabled);
  }
}

async function loadPublicStatus() {
  const payload = await apiRequest("/api/public/status");
  applyServiceStatus(payload.service || payload);
}

async function loadSessionData() {
  const [dashboardPayload, favoritesPayload, settingsPayload] = await Promise.all([
    apiRequest("/api/me/dashboard"),
    apiRequest("/api/me/favorites"),
    apiRequest("/api/me/settings"),
  ]);

  renderDashboard(dashboardPayload);
  renderFavorites(favoritesPayload.favorites || []);
  renderSettings(settingsPayload);
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
  if (!controlsEnabled.value) {
    return;
  }

  showMessage("");

  try {
    await Promise.all([loadPublicStatus(), loadSessionData()]);
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function toggleTracker() {
  if (!controlsEnabled.value) {
    return;
  }

  showMessage("");
  const action = trackerRunning.value ? "stop" : "start";

  try {
    await apiRequest(`/api/me/tracker/${action}`, { method: "POST" });
    await loadSessionData();
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function saveTrackingSettings() {
  if (!controlsEnabled.value) {
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
    await apiRequest("/api/me/settings", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showMessage("Tracking settings saved.");
    await loadSessionData();
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function savePlaylistSettings() {
  if (!controlsEnabled.value) {
    return;
  }

  showMessage("");

  const payload = {
    playlist_name: settings.playlist_name,
    playlist_public: Boolean(settings.playlist_public),
    auto_add_enabled: Boolean(settings.auto_add_enabled),
  };

  try {
    await apiRequest("/api/me/settings", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showMessage("Playlist settings saved.");
    await loadSessionData();
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function forceAdd(trackId) {
  if (!trackId || !controlsEnabled.value) {
    return;
  }

  showMessage("");

  try {
    await apiRequest(`/api/me/favorites/${trackId}/force-add`, {
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
    showMessage("Open via http://<host>:<port>, not file://", "error");
    return;
  }

  const query = new URLSearchParams(window.location.search);
  const oauthState = query.get("oauth");

  if (oauthState === "connected") {
    showMessage("Spotify account connected.");
    window.history.replaceState({}, document.title, "/");
  } else if (oauthState === "error") {
    showMessage("Spotify connection failed or was cancelled.", "error");
    window.history.replaceState({}, document.title, "/");
  } else if (oauthState === "limit") {
    const maxUsers = Number(query.get("max") || service.max_connected_users);
    showMessage(`User cap reached (${maxUsers}). Existing users can still sign in.`, "error");
    window.history.replaceState({}, document.title, "/");
  }

  try {
    await loadPublicStatus();
  } catch (error) {
    showMessage(error.message, "error");
  }

  try {
    await loadSessionData();
  } catch (error) {
    if (error.status === 401) {
      account.value = null;
      favorites.value = [];
      resetDashboard();
      showMessage("Connect your Spotify account to begin.", "info");
      return;
    }

    showMessage(error.message, "error");
  }
}

onMounted(() => {
  initialize();
});
</script>
