<template>
  <div>
    <div class="scanlines" aria-hidden="true"></div>

    <div class="app-shell">
      <aside class="side-column panel-frame">
        <div class="brand-block panel-inset">
          <p class="brand-label">FavSongs</p>
          <p class="brand-name">Public Explorer</p>
        </div>

        <div class="status-block panel-inset">
          <p class="section-label">Service Health</p>
          <div class="status-row">
            <span class="status-led" aria-hidden="true"></span>
            <div>
              <p class="status-main">{{ serviceStatus }}</p>
              <p class="status-sub">{{ healthSubtext }}</p>
            </div>
          </div>
        </div>

        <div class="status-block panel-inset">
          <p class="section-label">Current Account</p>
          <p class="account-value">{{ accountLabel }}</p>
        </div>
      </aside>

      <main class="main-column">
        <header class="console-rack panel-frame">
          <div class="rack-line rack-title panel-inset">
            <div class="window-title">
              <span class="title-dot"></span>
              <span>FavSongs Public Mode</span>
            </div>
            <div class="window-controls" aria-hidden="true">
              <span class="control-btn">_</span>
              <span class="control-btn">[]</span>
              <span class="control-btn">X</span>
            </div>
          </div>

          <div class="rack-line rack-nav panel-inset">
            <p class="section-label">Mode</p>
            <nav class="nav nav-top" aria-label="Primary navigation">
              <button
                class="nav-link"
                :class="{ 'is-active': activeView === 'profile' }"
                type="button"
                @click="setActiveView('profile')"
              >
                Profile
              </button>
              <button
                class="nav-link"
                :class="{ 'is-active': activeView === 'playlists' }"
                type="button"
                @click="setActiveView('playlists')"
              >
                Playlists
              </button>
              <button
                class="nav-link"
                :class="{ 'is-active': activeView === 'liked' }"
                type="button"
                @click="setActiveView('liked')"
              >
                Liked Songs
              </button>
              <button
                class="nav-link"
                :class="{ 'is-active': activeView === 'following' }"
                type="button"
                @click="setActiveView('following')"
              >
                Following
              </button>
            </nav>
            <div class="vu-meter" aria-hidden="true">
              <span></span><span></span><span></span><span></span><span></span><span></span><span></span>
            </div>
          </div>

          <div class="rack-line rack-control panel-inset">
            <div class="select-wrap">
              <label for="profile-query">Spotify username, @tag, or profile URL</label>
              <div class="search-row">
                <input
                  id="profile-query"
                  v-model="searchQuery"
                  type="text"
                  placeholder="e.g. open.spotify.com/user/spotify"
                  :disabled="loadingLookup"
                  @keyup.enter="lookupProfile"
                />
                <button
                  class="btn"
                  type="button"
                  :disabled="loadingLookup || !searchQuery.trim()"
                  @click="lookupProfile"
                >
                  {{ loadingLookup ? "Searching..." : "Lookup" }}
                </button>
              </div>
            </div>
            <div class="rack-actions">
              <button
                class="btn ghost"
                type="button"
                :disabled="loadingLookup || !hasResult"
                @click="clearResults"
              >
                Clear
              </button>
            </div>
          </div>
        </header>

        <p class="message" :class="{ 'is-empty': !globalMessage, error: globalMessageKind === 'error' }">
          {{ globalMessage }}
        </p>

        <section class="view" :class="{ hidden: activeView !== 'profile' }">
          <div class="content-grid two-up">
            <article class="panel-frame feature-panel">
              <div class="panel-head">
                <h2>Selected Account</h2>
                <span class="status-pill">{{ hasResult ? "Loaded" : "Idle" }}</span>
              </div>

              <div v-if="selectedUser" class="profile-card panel-inset">
                <img
                  v-if="selectedUser.image_url"
                  :src="selectedUser.image_url"
                  :alt="selectedUser.display_name"
                  class="profile-avatar"
                />
                <div v-else class="profile-avatar profile-avatar-fallback" aria-hidden="true">{{ avatarInitials }}</div>

                <div class="profile-meta">
                  <p class="track-title">{{ selectedUser.display_name }}</p>
                  <p class="track-artist">@{{ selectedUser.id }}</p>

                  <div class="queue-list">
                    <ul>
                      <li>
                        <span>Followers</span>
                        <span>{{ formatNumber(selectedUser.followers_total) }}</span>
                      </li>
                      <li>
                        <span>Public playlists</span>
                        <span>{{ formatNumber(stats.playlists_total) }}</span>
                      </li>
                    </ul>
                  </div>

                  <a v-if="selectedUser.external_url" class="btn ghost" :href="selectedUser.external_url" target="_blank" rel="noreferrer">
                    Open On Spotify
                  </a>
                </div>
              </div>

              <div v-else class="panel-inset empty-panel">
                Search a username/tag to load public profile data.
              </div>
            </article>

            <article class="panel-frame">
              <h2>Candidate Matches</h2>
              <div class="table-wrap panel-inset">
                <table class="table" aria-label="Candidate users">
                  <thead>
                    <tr>
                      <th>Display name</th>
                      <th>User ID</th>
                      <th>Followers</th>
                      <th>Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-if="!candidateUsers.length">
                      <td colspan="4">No candidates loaded.</td>
                    </tr>
                    <tr v-for="candidate in candidateUsers" :key="candidate.profile.id">
                      <td>{{ candidate.profile.display_name }}</td>
                      <td>{{ candidate.profile.id }}</td>
                      <td>{{ formatNumber(candidate.profile.followers_total) }}</td>
                      <td>{{ candidate.source }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </article>
          </div>
        </section>

        <section class="view" :class="{ hidden: activeView !== 'playlists' }">
          <div class="content-grid">
            <article class="panel-frame">
              <h2>Playlist Inventory</h2>

              <div class="stats-grid playlist-stats">
                <div class="stat panel-inset">
                  <p class="stat-label">Total playlists</p>
                  <p class="stat-value">{{ stats.playlists_total }}</p>
                </div>
                <div class="stat panel-inset">
                  <p class="stat-label">Owned</p>
                  <p class="stat-value">{{ stats.owned_playlists_total }}</p>
                </div>
                <div class="stat panel-inset">
                  <p class="stat-label">Followed public</p>
                  <p class="stat-value">{{ stats.followed_public_playlists_total }}</p>
                </div>
              </div>

              <div class="table-wrap panel-inset">
                <table class="table" aria-label="Public playlists">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Owner</th>
                      <th>Tracks</th>
                      <th>Type</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-if="!playlistsAll.length">
                      <td colspan="5">No public playlists loaded.</td>
                    </tr>
                    <tr v-for="playlist in playlistsAll" :key="playlist.id">
                      <td>
                        <a
                          v-if="playlist.external_url"
                          :href="playlist.external_url"
                          target="_blank"
                          rel="noreferrer"
                        >
                          {{ playlist.name }}
                        </a>
                        <span v-else>{{ playlist.name }}</span>
                      </td>
                      <td>{{ playlist.owner.display_name }}</td>
                      <td>{{ formatNumber(playlist.tracks_total) }}</td>
                      <td>{{ playlist.relationship }}</td>
                      <td>
                        <button
                          class="btn ghost"
                          type="button"
                          :disabled="loadingTracks"
                          @click="loadPlaylistTracks(playlist)"
                        >
                          {{ loadingTracks && selectedPlaylist?.id === playlist.id ? "Loading..." : "Load Tracks" }}
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </article>

            <article class="panel-frame">
              <div class="panel-head">
                <h2>Playlist Track Listing</h2>
                <span class="status-pill" v-if="selectedPlaylistTracks.length">{{ selectedPlaylistTracks.length }} loaded</span>
              </div>

              <div v-if="selectedPlaylist" class="queue-list panel-inset">
                <p class="section-label soft">
                  {{ selectedPlaylist.name }}
                  ({{ selectedPlaylistTracks.length }} of {{ selectedPlaylistTrackTotal }})
                </p>
                <ul>
                  <li v-if="!selectedPlaylistTracks.length">
                    <span>No tracks returned from Spotify for this playlist.</span>
                    <span>-</span>
                  </li>
                  <li v-for="track in selectedPlaylistTracks" :key="track.id">
                    <span>{{ artistsLabel(track) }} - {{ track.name }}</span>
                    <span>{{ formatDuration(track.duration_ms) }}</span>
                  </li>
                </ul>
              </div>

              <div v-else class="panel-inset empty-panel">
                Click <code>Load Tracks</code> on a playlist to list songs.
              </div>
            </article>
          </div>
        </section>

        <section class="view" :class="{ hidden: activeView !== 'liked' }">
          <div class="content-grid two-up">
            <article class="panel-frame">
              <h2>Liked Songs Availability</h2>
              <div class="panel-inset empty-panel">
                <p>{{ likedSongs.reason || unavailableLikedReason }}</p>
                <p class="section-label soft">
                  Derived tracks are from public playlists that look like "liked/favorites" collections.
                </p>
              </div>

              <h2>Detected Public Liked Playlists</h2>
              <div class="table-wrap panel-inset">
                <table class="table" aria-label="Inferred liked playlists">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Matches</th>
                      <th>Tracks</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-if="!derivedLikedPlaylists.length">
                      <td colspan="3">No public liked/favorite-style playlists detected.</td>
                    </tr>
                    <tr v-for="playlist in derivedLikedPlaylists" :key="playlist.id">
                      <td>{{ playlist.name }}</td>
                      <td>{{ playlist.matched_keywords.join(', ') }}</td>
                      <td>{{ formatNumber(playlist.tracks_total) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </article>

            <article class="panel-frame">
              <h2>Derived Liked Tracks</h2>
              <div class="queue-list panel-inset">
                <ul>
                  <li v-if="!derivedLikedTracks.length">
                    <span>No derived tracks available.</span>
                    <span>-</span>
                  </li>
                  <li v-for="track in derivedLikedTracks" :key="track.id + '-' + track.source_playlist.id">
                    <span>
                      {{ artistsLabel(track) }} - {{ track.name }}
                      <small class="section-label soft">({{ track.source_playlist.name }})</small>
                    </span>
                    <span>{{ formatDuration(track.duration_ms) }}</span>
                  </li>
                </ul>
              </div>
            </article>
          </div>
        </section>

        <section class="view" :class="{ hidden: activeView !== 'following' }">
          <div class="content-grid two-up">
            <article class="panel-frame">
              <h2>Following Availability</h2>
              <div class="panel-inset empty-panel">
                <p>{{ following.reason || unavailableFollowingReason }}</p>
                <p class="section-label soft">
                  Spotify does not expose other users' follow graph in public Web API responses.
                </p>
              </div>

              <h2>Public API Limitations</h2>
              <div class="queue-list panel-inset">
                <ul>
                  <li v-for="item in limitations" :key="item">
                    <span>{{ item }}</span>
                    <span></span>
                  </li>
                </ul>
              </div>
            </article>

            <article class="panel-frame">
              <h2>Public Followed Playlists</h2>
              <div class="table-wrap panel-inset">
                <table class="table" aria-label="Followed public playlists">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Owner</th>
                      <th>Tracks</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-if="!followedPlaylists.length">
                      <td colspan="3">No followed public playlists in current result.</td>
                    </tr>
                    <tr v-for="playlist in followedPlaylists" :key="playlist.id">
                      <td>{{ playlist.name }}</td>
                      <td>{{ playlist.owner.display_name }}</td>
                      <td>{{ formatNumber(playlist.tracks_total) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </article>
          </div>
        </section>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";

const DEFAULT_LIMITATIONS = [
  "Spotify public API does not provide another user's private Liked Songs collection.",
  "Spotify public API does not provide another user's following graph (artists/users).",
  "Any 'liked songs' shown here are inferred from public playlists with liked/favorite-style naming.",
];

const unavailableLikedReason =
  "Spotify does not expose another user's Liked Songs via the public Web API.";
const unavailableFollowingReason =
  "Spotify does not expose another user's followed artists/users via the public Web API.";

const searchQuery = ref("");
const activeView = ref("profile");
const loadingLookup = ref(false);
const loadingTracks = ref(false);

const globalMessage = ref("");
const globalMessageKind = ref("info");
const serviceStatus = ref("Ready");
const healthSubtext = ref("Enter a username/tag");

const result = reactive({
  selectedUser: null,
  candidateUsers: [],
  playlistsAll: [],
  playlistsOwned: [],
  playlistsFollowed: [],
  likedSongs: {
    available: false,
    reason: unavailableLikedReason,
    derived_from_public_playlists: [],
    derived_tracks: [],
  },
  following: {
    available: false,
    reason: unavailableFollowingReason,
    public_followed_playlists: [],
  },
  stats: {
    playlists_total: 0,
    owned_playlists_total: 0,
    followed_public_playlists_total: 0,
    inferred_liked_playlists_total: 0,
    derived_liked_tracks_total: 0,
  },
  limitations: [...DEFAULT_LIMITATIONS],
  selectedPlaylist: null,
  selectedPlaylistTrackTotal: 0,
  selectedPlaylistTracks: [],
});

const selectedUser = computed(() => result.selectedUser);
const candidateUsers = computed(() => result.candidateUsers);
const playlistsAll = computed(() => result.playlistsAll);
const derivedLikedPlaylists = computed(() => result.likedSongs.derived_from_public_playlists || []);
const derivedLikedTracks = computed(() => result.likedSongs.derived_tracks || []);
const likedSongs = computed(() => result.likedSongs);
const followedPlaylists = computed(() => result.following.public_followed_playlists || []);
const following = computed(() => result.following);
const stats = computed(() => result.stats);
const limitations = computed(() => result.limitations || DEFAULT_LIMITATIONS);
const hasResult = computed(() => Boolean(result.selectedUser));
const selectedPlaylist = computed(() => result.selectedPlaylist);
const selectedPlaylistTrackTotal = computed(() => result.selectedPlaylistTrackTotal || 0);
const selectedPlaylistTracks = computed(() => result.selectedPlaylistTracks || []);

const accountLabel = computed(() => {
  if (!selectedUser.value) {
    return "No account loaded";
  }
  return `${selectedUser.value.display_name} (@${selectedUser.value.id})`;
});

const avatarInitials = computed(() => {
  if (!selectedUser.value) {
    return "?";
  }
  const value = String(selectedUser.value.display_name || selectedUser.value.id || "?").trim();
  return value.slice(0, 2).toUpperCase();
});

function showMessage(message, kind = "info") {
  globalMessage.value = message || "";
  globalMessageKind.value = kind;
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(Number(value) || 0);
}

function formatDuration(ms) {
  const totalSeconds = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function artistsLabel(track) {
  const artists = Array.isArray(track?.artists) ? track.artists : [];
  if (!artists.length) {
    return "Unknown Artist";
  }
  return artists.join(", ");
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

function resetResultState() {
  result.selectedUser = null;
  result.candidateUsers = [];
  result.playlistsAll = [];
  result.playlistsOwned = [];
  result.playlistsFollowed = [];
  result.likedSongs = {
    available: false,
    reason: unavailableLikedReason,
    derived_from_public_playlists: [],
    derived_tracks: [],
  };
  result.following = {
    available: false,
    reason: unavailableFollowingReason,
    public_followed_playlists: [],
  };
  result.stats = {
    playlists_total: 0,
    owned_playlists_total: 0,
    followed_public_playlists_total: 0,
    inferred_liked_playlists_total: 0,
    derived_liked_tracks_total: 0,
  };
  result.limitations = [...DEFAULT_LIMITATIONS];
  result.selectedPlaylist = null;
  result.selectedPlaylistTrackTotal = 0;
  result.selectedPlaylistTracks = [];
}

function applyLookupPayload(payload) {
  result.selectedUser = payload.selected_user || null;
  result.candidateUsers = payload.candidate_users || [];
  result.playlistsAll = payload.playlists?.all || [];
  result.playlistsOwned = payload.playlists?.owned || [];
  result.playlistsFollowed = payload.playlists?.followed_public || [];
  result.likedSongs = payload.liked_songs || {
    available: false,
    reason: unavailableLikedReason,
    derived_from_public_playlists: [],
    derived_tracks: [],
  };
  result.following = payload.following || {
    available: false,
    reason: unavailableFollowingReason,
    public_followed_playlists: [],
  };
  result.stats = payload.stats || result.stats;
  result.limitations = payload.limitations || [...DEFAULT_LIMITATIONS];
  result.selectedPlaylist = null;
  result.selectedPlaylistTrackTotal = 0;
  result.selectedPlaylistTracks = [];
}

async function lookupProfile() {
  const query = searchQuery.value.trim();
  if (!query) {
    showMessage("Enter a Spotify username, @tag, or profile URL.", "error");
    return;
  }

  loadingLookup.value = true;
  showMessage("");

  try {
    const payload = await apiRequest(`/api/public/lookup?query=${encodeURIComponent(query)}`);
    applyLookupPayload(payload);

    serviceStatus.value = "Online";
    if (result.selectedUser) {
      healthSubtext.value = `${result.selectedUser.display_name} loaded`;
      showMessage(
        `Loaded ${result.selectedUser.display_name} with ${formatNumber(result.stats.playlists_total)} public playlists.`,
        "info"
      );
    }
  } catch (error) {
    showMessage(error.message, "error");
    serviceStatus.value = "Error";
    healthSubtext.value = "Lookup failed";
  } finally {
    loadingLookup.value = false;
  }
}

async function loadPlaylistTracks(playlist) {
  if (!playlist?.id) {
    return;
  }

  loadingTracks.value = true;
  showMessage("");

  try {
    const payload = await apiRequest(
      `/api/public/playlists/${encodeURIComponent(playlist.id)}/tracks?limit=100`
    );
    result.selectedPlaylist = playlist;
    result.selectedPlaylistTrackTotal = payload.total || 0;
    result.selectedPlaylistTracks = payload.tracks || [];
    showMessage(`Loaded ${result.selectedPlaylistTracks.length} tracks from ${playlist.name}.`, "info");
  } catch (error) {
    showMessage(error.message, "error");
  } finally {
    loadingTracks.value = false;
  }
}

function clearResults() {
  searchQuery.value = "";
  showMessage("");
  resetResultState();
  serviceStatus.value = "Ready";
  healthSubtext.value = "Enter a username/tag";
  activeView.value = "profile";
}

function setActiveView(view) {
  activeView.value = view;
}

onMounted(async () => {
  try {
    await apiRequest("/api/health");
    serviceStatus.value = "Online";
    healthSubtext.value = "Ready for public lookups";
  } catch (error) {
    serviceStatus.value = "Offline";
    healthSubtext.value = "API unavailable";
    showMessage(error.message, "error");
    return;
  }

  const initialQuery = new URLSearchParams(window.location.search).get("q");
  if (initialQuery) {
    searchQuery.value = initialQuery;
    await lookupProfile();
  }
});
</script>
