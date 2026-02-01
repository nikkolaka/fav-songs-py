const accounts = [
  {
    id: "acct-1",
    name: "Niko L.",
    status: "Active",
    nowPlaying: {
      title: "Midnight Harbor",
      artist: "Orion Avenue",
      progress: 0.55,
      elapsed: "02:18",
      total: "04:12",
    },
    stats: {
      tracksCounted: 18,
      completionThreshold: "80%",
      nextFavorite: "2 plays",
    },
    recentPlays: [
      "Orion Avenue - Midnight Harbor",
      "Lumen Wells - Side Street",
      "Northbound - Atlas Sky",
      "Wavepath - Quiet Signal",
    ],
    favorites: [
      { title: "Quiet Signal", artist: "Wavepath", plays: 5, last: "Today 08:14" },
      { title: "Atlas Sky", artist: "Northbound", plays: 7, last: "Today 07:55" },
      { title: "Side Street", artist: "Lumen Wells", plays: 5, last: "Yesterday" },
    ],
  },
  {
    id: "acct-2",
    name: "Marin Studio",
    status: "Paused",
    nowPlaying: {
      title: "Signal Drift",
      artist: "Aeon Field",
      progress: 0.3,
      elapsed: "01:02",
      total: "03:27",
    },
    stats: {
      tracksCounted: 4,
      completionThreshold: "90%",
      nextFavorite: "1 play",
    },
    recentPlays: [
      "Aeon Field - Signal Drift",
      "Civic Tone - Ivory",
      "Tracewell - Calm Water",
    ],
    favorites: [
      { title: "Civic Tone", artist: "Ivory", plays: 5, last: "Today 06:50" },
    ],
  },
];

const accountSelect = document.getElementById("account-select");
const trackTitle = document.getElementById("track-title");
const trackArtist = document.getElementById("track-artist");
const progressBar = document.getElementById("progress-bar");
const progressTime = document.getElementById("progress-time");
const progressTotal = document.getElementById("progress-total");
const tracksCounted = document.getElementById("tracks-counted");
const completionThreshold = document.getElementById("completion-threshold");
const nextFavorite = document.getElementById("next-favorite");
const recentPlays = document.getElementById("recent-plays");
const favoritesTable = document.getElementById("favorites-table");
const accountStatus = document.getElementById("account-status");

function renderAccountOptions() {
  accountSelect.innerHTML = accounts
    .map((account) => `<option value="${account.id}">${account.name}</option>`)
    .join("");
}

function renderAccount(account) {
  trackTitle.textContent = account.nowPlaying.title;
  trackArtist.textContent = account.nowPlaying.artist;
  progressBar.style.width = `${account.nowPlaying.progress * 100}%`;
  progressTime.textContent = account.nowPlaying.elapsed;
  progressTotal.textContent = account.nowPlaying.total;
  tracksCounted.textContent = account.stats.tracksCounted;
  completionThreshold.textContent = account.stats.completionThreshold;
  nextFavorite.textContent = account.stats.nextFavorite;
  recentPlays.innerHTML = account.recentPlays
    .map((play) => `<li><span>${play}</span><span>Logged</span></li>`)
    .join("");
  favoritesTable.innerHTML = account.favorites
    .map(
      (favorite) => `
        <div class="table-row">
          <span>${favorite.title}</span>
          <span>${favorite.artist}</span>
          <span>${favorite.plays}</span>
          <span>${favorite.last}</span>
        </div>
      `
    )
    .join("");
  accountStatus.textContent = account.status;
  accountStatus.classList.toggle("neutral", account.status !== "Active");
}

accountSelect.addEventListener("change", (event) => {
  const selected = accounts.find((account) => account.id === event.target.value);
  if (selected) {
    renderAccount(selected);
  }
});

const tabs = document.querySelectorAll(".tab");
const views = document.querySelectorAll(".view");

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((item) => item.classList.remove("is-active"));
    tab.classList.add("is-active");
    const target = tab.dataset.view;
    views.forEach((view) => {
      view.classList.toggle("hidden", view.dataset.view !== target);
    });
  });
});

renderAccountOptions();
renderAccount(accounts[0]);
