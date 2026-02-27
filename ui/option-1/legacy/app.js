const queryInput = document.getElementById("query-input");
const lookupButton = document.getElementById("lookup-btn");
const clearButton = document.getElementById("clear-btn");

const globalMessage = document.getElementById("global-message");
const serviceStatus = document.getElementById("service-status");
const healthSubtext = document.getElementById("health-subtext");
const accountLabel = document.getElementById("account-label");

const accountSummary = document.getElementById("account-summary");
const candidatesBody = document.getElementById("candidates-table-body");
const playlistsBody = document.getElementById("playlists-table-body");
const likedStatusList = document.getElementById("liked-status-list");
const likedPlaylistsBody = document.getElementById("liked-playlists-body");
const likedTracksList = document.getElementById("liked-tracks-list");
const followingStatusList = document.getElementById("following-status-list");
const followedPlaylistsBody = document.getElementById("followed-playlists-body");
const limitationsList = document.getElementById("limitations-list");

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

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
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

function renderAccountSummary(user, stats) {
  if (!user) {
    accountLabel.textContent = "No account loaded";
    accountSummary.innerHTML = "<li><span>No data loaded.</span><span>-</span></li>";
    return;
  }

  accountLabel.textContent = `${user.display_name} (@${user.id})`;
  accountSummary.innerHTML = [
    `<li><span>Display name</span><span>${escapeHtml(user.display_name)}</span></li>`,
    `<li><span>User ID</span><span>${escapeHtml(user.id)}</span></li>`,
    `<li><span>Followers</span><span>${formatNumber(user.followers_total)}</span></li>`,
    `<li><span>Public playlists</span><span>${formatNumber(stats.playlists_total)}</span></li>`,
  ].join("");
}

function renderCandidates(candidates) {
  if (!candidates.length) {
    candidatesBody.innerHTML = '<tr><td colspan="4">No candidates loaded.</td></tr>';
    return;
  }

  candidatesBody.innerHTML = candidates
    .map((candidate) => {
      const profile = candidate.profile || {};
      return `<tr>
        <td>${escapeHtml(profile.display_name || "Unknown")}</td>
        <td>${escapeHtml(profile.id || "")}</td>
        <td>${formatNumber(profile.followers_total)}</td>
        <td>${escapeHtml(candidate.source || "-")}</td>
      </tr>`;
    })
    .join("");
}

function renderPlaylists(playlists) {
  if (!playlists.length) {
    playlistsBody.innerHTML = '<tr><td colspan="4">No playlists loaded.</td></tr>';
    return;
  }

  playlistsBody.innerHTML = playlists
    .map((playlist) => {
      const name = playlist.external_url
        ? `<a href="${escapeHtml(playlist.external_url)}" target="_blank" rel="noreferrer">${escapeHtml(playlist.name)}</a>`
        : escapeHtml(playlist.name);

      return `<tr>
        <td>${name}</td>
        <td>${escapeHtml((playlist.owner || {}).display_name || "Unknown")}</td>
        <td>${formatNumber(playlist.tracks_total)}</td>
        <td>${escapeHtml(playlist.relationship || "-")}</td>
      </tr>`;
    })
    .join("");
}

function renderLikedStatus(likedSongs) {
  likedStatusList.innerHTML = `<li><span>${escapeHtml(
    likedSongs.reason || "Spotify does not expose another user's Liked Songs via public API."
  )}</span><span></span></li>`;
}

function renderLikedPlaylists(playlists) {
  if (!playlists.length) {
    likedPlaylistsBody.innerHTML = '<tr><td colspan="3">No inferred liked playlists.</td></tr>';
    return;
  }

  likedPlaylistsBody.innerHTML = playlists
    .map(
      (playlist) => `<tr>
        <td>${escapeHtml(playlist.name || "Untitled")}</td>
        <td>${escapeHtml((playlist.matched_keywords || []).join(", "))}</td>
        <td>${formatNumber(playlist.tracks_total)}</td>
      </tr>`
    )
    .join("");
}

function renderLikedTracks(tracks) {
  if (!tracks.length) {
    likedTracksList.innerHTML = "<li><span>No derived tracks loaded.</span><span>-</span></li>";
    return;
  }

  likedTracksList.innerHTML = tracks
    .map((track) => {
      const artists = Array.isArray(track.artists) && track.artists.length ? track.artists.join(", ") : "Unknown Artist";
      const source = (track.source_playlist || {}).name || "Unknown playlist";
      return `<li>
        <span>${escapeHtml(artists)} - ${escapeHtml(track.name || "Unknown")}
          <small class="section-label soft">(${escapeHtml(source)})</small>
        </span>
        <span>${formatDuration(track.duration_ms)}</span>
      </li>`;
    })
    .join("");
}

function renderFollowingStatus(following) {
  followingStatusList.innerHTML = `<li><span>${escapeHtml(
    following.reason || "Spotify does not expose another user's followed artists/users via public API."
  )}</span><span></span></li>`;
}

function renderFollowedPlaylists(playlists) {
  if (!playlists.length) {
    followedPlaylistsBody.innerHTML = '<tr><td colspan="3">No followed public playlists.</td></tr>';
    return;
  }

  followedPlaylistsBody.innerHTML = playlists
    .map(
      (playlist) => `<tr>
        <td>${escapeHtml(playlist.name || "Untitled")}</td>
        <td>${escapeHtml((playlist.owner || {}).display_name || "Unknown")}</td>
        <td>${formatNumber(playlist.tracks_total)}</td>
      </tr>`
    )
    .join("");
}

function renderLimitations(limitations) {
  const list = Array.isArray(limitations) && limitations.length
    ? limitations
    : ["No limitation notes returned."];
  limitationsList.innerHTML = list
    .map((item) => `<li><span>${escapeHtml(item)}</span><span></span></li>`)
    .join("");
}

function clearView() {
  renderAccountSummary(null, { playlists_total: 0 });
  renderCandidates([]);
  renderPlaylists([]);
  renderLikedStatus({});
  renderLikedPlaylists([]);
  renderLikedTracks([]);
  renderFollowingStatus({});
  renderFollowedPlaylists([]);
  renderLimitations([]);
}

async function lookup() {
  const query = queryInput.value.trim();
  if (!query) {
    showMessage("Enter a Spotify username, @tag, or profile URL.", "error");
    return;
  }

  lookupButton.disabled = true;
  showMessage("");

  try {
    const payload = await apiRequest(`/api/public/lookup?query=${encodeURIComponent(query)}`);
    const selectedUser = payload.selected_user || null;

    serviceStatus.textContent = "Online";
    healthSubtext.textContent = selectedUser ? `${selectedUser.display_name} loaded` : "Lookup completed";

    renderAccountSummary(selectedUser, payload.stats || {});
    renderCandidates(payload.candidate_users || []);
    renderPlaylists((payload.playlists || {}).all || []);
    renderLikedStatus(payload.liked_songs || {});
    renderLikedPlaylists((payload.liked_songs || {}).derived_from_public_playlists || []);
    renderLikedTracks((payload.liked_songs || {}).derived_tracks || []);
    renderFollowingStatus(payload.following || {});
    renderFollowedPlaylists((payload.following || {}).public_followed_playlists || []);
    renderLimitations(payload.limitations || []);

    showMessage(
      `Loaded ${selectedUser ? selectedUser.display_name : "account"} with ${formatNumber((payload.stats || {}).playlists_total)} public playlists.`
    );
  } catch (error) {
    serviceStatus.textContent = "Error";
    healthSubtext.textContent = "Lookup failed";
    showMessage(error.message, "error");
  } finally {
    lookupButton.disabled = false;
  }
}

lookupButton.addEventListener("click", () => {
  lookup();
});

queryInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    lookup();
  }
});

clearButton.addEventListener("click", () => {
  queryInput.value = "";
  showMessage("");
  serviceStatus.textContent = "Ready";
  healthSubtext.textContent = "Enter a username/tag";
  clearView();
});

(async function init() {
  clearView();
  try {
    await apiRequest("/api/health");
    serviceStatus.textContent = "Online";
    healthSubtext.textContent = "Ready for public lookups";
  } catch (error) {
    serviceStatus.textContent = "Offline";
    healthSubtext.textContent = "API unavailable";
    showMessage(error.message, "error");
    return;
  }

  const initialQuery = new URLSearchParams(window.location.search).get("q");
  if (initialQuery) {
    queryInput.value = initialQuery;
    lookup();
  }
})();
