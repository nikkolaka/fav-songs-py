"use strict";

const POLL_MS = 5000;

const $ = (id) => document.getElementById(id);

const LOGIN_ERRORS = {
  invalid_state: "That login link expired. Try again.",
  exchange_failed: "Spotify rejected the login. Try again.",
  full: "This app already has its five Spotify accounts. Development Mode allows no more.",
  access_denied: "You declined the permissions, so nothing was connected.",
};

let settingsDirty = false;
let state = null;

const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

function ago(ms) {
  if (!ms) return "never";
  const seconds = Math.max(0, Math.floor((Date.now() - ms) / 1000));
  if (seconds < 90) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

function notify(message, isError = true) {
  const banner = $("banner");
  banner.textContent = message;
  banner.hidden = !message;
  banner.className = isError ? "pico-color-red-500" : "muted";
}

// ------------------------------------------------------------------ render

function renderNowPlaying(now) {
  const box = $("now-playing");
  if (!now) {
    box.innerHTML = '<p class="muted">Nothing playing.</p>';
    return;
  }

  const counts = state.favorites.find((f) => f.track_id === now.track_id);
  const plays = counts ? counts.occurrences : 0;
  const percent = Math.round((now.completion_ratio || 0) * 100);

  box.innerHTML = `
    <p class="track-title"><strong>${esc(now.name)}</strong></p>
    <p class="track-artist">${esc(now.artist)}</p>
    <progress value="${percent}" max="100"></progress>
    <small class="muted">${percent}% &middot; ${plays} play${plays === 1 ? "" : "s"} counted${
      now.is_playing ? "" : " &middot; paused"
    }</small>
  `;
}

function renderStats(stats) {
  $("stat-plays").textContent = stats.plays_24h;
  $("stat-tracks").textContent = stats.tracked_tracks;

  const next = stats.next_favorite;
  const threshold = state.settings.favorite_threshold;
  $("stat-next").innerHTML = next
    ? `<small>${esc(next.name)} &mdash; ${threshold - next.occurrences} to go</small>`
    : "<small class='muted'>&mdash;</small>";
}

function renderFavorites(favorites) {
  const body = $("favorites");
  $("favorites-empty").hidden = favorites.length > 0;

  body.innerHTML = favorites
    .map(
      (item) => `
      <tr>
        <td><input type="checkbox" data-track="${esc(item.track_id)}" aria-label="Select" /></td>
        <td>${esc(item.name)}</td>
        <td class="muted">${esc(item.artist)}</td>
        <td>${item.occurrences}</td>
        <td>${item.in_playlist ? "yes" : `<span class="muted">no</span>`}</td>
      </tr>`,
    )
    .join("");
}

function renderDiscovery(discovery) {
  $("discovery-month").textContent = discovery.month_name;

  const prompt = $("context-prompt");
  prompt.hidden = discovery.unlabelled.length === 0;
  $("context-list").className = "plain";
  $("context-list").innerHTML = discovery.unlabelled
    .map(
      (row) => `
      <li>
        <span>
          <strong>${esc(row.title || row.sample_track)}</strong><br />
          <small class="muted">${
            row.title ? `heard: ${esc(row.sample_track)} &middot; ` : ""
          }${row.play_count} play${row.play_count === 1 ? "" : "s"} &middot; ${esc(
            row.playlist_id,
          )}</small>
        </span>
        <span class="context-actions">
          <button data-label-context="${esc(row.playlist_id)}" data-label="Discover Weekly">
            Discover Weekly
          </button>
          <button data-label-context="${esc(row.playlist_id)}" data-label="Release Radar"
                  class="secondary">Release Radar</button>
          <button data-dismiss-context="${esc(row.playlist_id)}" class="secondary outline">
            Ignore
          </button>
        </span>
      </li>`,
    )
    .join("");

  $("discovery-empty").hidden = discovery.tracks.length > 0;
  $("discovery-tracks").className = "plain";
  $("discovery-tracks").innerHTML = discovery.tracks
    .map(
      (track) => `
      <li>
        <span><strong>${esc(track.name)}</strong><br />
        <small class="muted">${esc(track.artist)}</small></span>
        <small class="muted">${ago(track.added_at * 1000)}</small>
      </li>`,
    )
    .join("");

  const blocked = discovery.blocked || [];
  $("blocked-details").hidden = blocked.length === 0;
  $("blocked-count").textContent = blocked.length;
  $("blocked-tracks").className = "plain";
  $("blocked-tracks").innerHTML = blocked
    .map(
      (track) => `
      <li>
        <span><strong>${esc(track.name)}</strong><br />
        <small class="muted">${esc(track.artist)}</small></span>
        <small class="muted">${esc(track.reason)}</small>
      </li>`,
    )
    .join("");

  const bl = discovery.blocklist || {};
  $("blocklist-status").innerHTML = bl.artists
    ? `AI blocklist: ${bl.artists.toLocaleString()} artists, updated ${ago(
        bl.fetched_at * 1000,
      )}${bl.error ? ` &mdash; last refresh failed: ${esc(bl.error)}` : ""}`
    : "AI blocklist unavailable &mdash; nothing is being filtered.";

  $("discovery-sources").className = "plain";
  $("discovery-sources").innerHTML = discovery.sources.length
    ? discovery.sources
        .map(
          (source) => `
          <li>
            <span>${esc(source.label)}<br />
            <small class="muted">${esc(source.playlist_id)}</small>
            ${
              source.degraded
                ? `<br /><small class="pico-color-red-500">Full read unavailable
                   (${esc(source.degraded)}) &mdash; falling back to what you play.</small>`
                : ""
            }</span>
            <button data-remove-source="${esc(source.playlist_id)}" class="secondary outline">
              Remove
            </button>
          </li>`,
        )
        .join("")
    : '<li><small class="muted">No sources yet.</small></li>';

  $("discovery-months").className = "plain";
  $("discovery-months").innerHTML = discovery.months
    .map(
      (month) =>
        `<li><span>${esc(month.name)}</span><small class="muted">${month.tracks} track${
          month.tracks === 1 ? "" : "s"
        }</small></li>`,
    )
    .join("");
}

function renderSettings(settings) {
  if (settingsDirty) return;
  const form = $("settings-form");
  for (const [key, value] of Object.entries(settings)) {
    const field = form.elements[key];
    if (!field) continue;
    if (field.type === "checkbox") field.checked = Boolean(value);
    else field.value = value;
  }
}

function render(next) {
  state = next;
  const connected = Boolean(state.connected);

  $("login").hidden = connected;
  $("app").hidden = !connected;
  $("account").hidden = !connected;
  if (!connected) return;

  $("account-name").textContent = state.user.display_name;
  $("tracker-toggle").checked = state.tracker_running;
  $("tracker-label").textContent = state.tracker_running ? "Tracking" : "Paused";
  notify(state.last_error || "");

  renderNowPlaying(state.now_playing);
  renderFavorites(state.favorites);
  renderStats(state.stats);
  renderDiscovery(state.discovery);
  renderSettings(state.settings);
  updateRemoveButton();
}

// ------------------------------------------------------------------ events

function updateRemoveButton() {
  const selected = document.querySelectorAll("#favorites input:checked").length;
  $("remove-selected").disabled = selected === 0;
}

function selectedTracks() {
  return [...document.querySelectorAll("#favorites input:checked")].map(
    (input) => input.dataset.track,
  );
}

async function refresh() {
  try {
    render(await api("/api/state"));
    document.body.classList.remove("stale");
  } catch (error) {
    document.body.classList.add("stale");
  }
}

async function act(work) {
  try {
    await work();
    await refresh();
  } catch (error) {
    notify(error.message);
  }
}

$("login-button").addEventListener("click", async () => {
  const { auth_url } = await api("/api/auth/start", { method: "POST" });
  window.location.href = auth_url;
});

$("logout").addEventListener("click", () =>
  act(async () => {
    await api("/api/auth/logout", { method: "POST" });
    window.location.reload();
  }),
);

$("disconnect").addEventListener("click", () => {
  if (!confirm("Erase your play counts, archive and tokens from this server?")) return;
  act(async () => {
    await api("/api/auth/disconnect", { method: "POST" });
    window.location.reload();
  });
});

$("tracker-toggle").addEventListener("change", (event) =>
  act(() =>
    api(event.target.checked ? "/api/tracker/start" : "/api/tracker/stop", { method: "POST" }),
  ),
);

$("settings-form").addEventListener("input", () => {
  settingsDirty = true;
});

$("settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  const payload = {
    favorite_threshold: Number(form.elements.favorite_threshold.value),
    poll_interval: Number(form.elements.poll_interval.value),
    min_completion_ratio: Number(form.elements.min_completion_ratio.value),
    playlist_name: form.elements.playlist_name.value.trim(),
    auto_add_enabled: form.elements.auto_add_enabled.checked,
    discovery_enabled: form.elements.discovery_enabled.checked,
    playlist_public: form.elements.playlist_public.checked,
  };
  act(async () => {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    settingsDirty = false;
    notify("Settings saved.", false);
  });
});

$("favorites").addEventListener("change", updateRemoveButton);

$("select-all").addEventListener("change", (event) => {
  document
    .querySelectorAll("#favorites input[type=checkbox]")
    .forEach((input) => (input.checked = event.target.checked));
  updateRemoveButton();
});

$("remove-selected").addEventListener("click", () => {
  const track_ids = selectedTracks();
  if (!track_ids.length) return;
  act(() =>
    api("/api/favorites/remove", { method: "POST", body: JSON.stringify({ track_ids }) }),
  );
});

$("sweep-now").addEventListener("click", () => {
  const button = $("sweep-now");
  button.setAttribute("aria-busy", "true");
  button.disabled = true;
  act(async () => {
    try {
      const result = await api("/api/discovery/sweep", { method: "POST" });
      const parts = [`${result.archived} archived`];
      if (result.blocked) parts.push(`${result.blocked} filtered as AI`);
      if (result.errors.length) parts.push(`${result.errors.length} source unreadable`);
      notify(parts.join(", "), Boolean(result.errors.length));
    } finally {
      button.removeAttribute("aria-busy");
      button.disabled = false;
    }
  });
});

$("source-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const playlist = $("source-input").value.trim();
  const label = $("source-label").value.trim() || "Discover Weekly";
  if (!playlist) return;
  act(async () => {
    await api("/api/discovery/sources", {
      method: "POST",
      body: JSON.stringify({ playlist, label }),
    });
    $("source-input").value = "";
  });
});

document.addEventListener("click", (event) => {
  const label = event.target.closest("[data-label-context]");
  if (label) {
    act(() =>
      api(`/api/discovery/contexts/${encodeURIComponent(label.dataset.labelContext)}`, {
        method: "POST",
        body: JSON.stringify({ label: label.dataset.label }),
      }),
    );
    return;
  }

  const dismiss = event.target.closest("[data-dismiss-context]");
  if (dismiss) {
    act(() =>
      api(`/api/discovery/contexts/${encodeURIComponent(dismiss.dataset.dismissContext)}`, {
        method: "DELETE",
      }),
    );
    return;
  }

  const remove = event.target.closest("[data-remove-source]");
  if (remove) {
    act(() =>
      api(`/api/discovery/sources/${encodeURIComponent(remove.dataset.removeSource)}`, {
        method: "DELETE",
      }),
    );
  }
});

// ------------------------------------------------------------------- start

const loginError = new URLSearchParams(window.location.search).get("login");
if (loginError) {
  const box = $("login-error");
  box.textContent = LOGIN_ERRORS[loginError] || "Login failed. Try again.";
  box.hidden = false;
  box.className = "pico-color-red-500";
  window.history.replaceState({}, "", "/");
}

refresh();
setInterval(refresh, POLL_MS);
