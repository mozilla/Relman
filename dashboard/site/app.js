import * as bugzilla from "./lib/bugzilla.js";
import { CACHE_MS } from "./lib/config.js";
import * as catalogue from "./lib/catalogue.js";
import * as crashstats from "./lib/crashstats.js";
import * as phabricator from "./lib/phabricator.js";
import * as runner from "./lib/runner.js";
import * as versions from "./lib/versions.js";

const KEY_STORAGE = "bugzillaApiKey";
const PAGES = [
  { id: "nightly", label: "Nightly" },
  { id: "beta", label: "Beta" },
  { id: "release", label: "Release" },
  { id: "esr", label: "ESR" },
  { id: "misc", label: "Misc" },
];

let releases = null;
// Bumps on navigation so late results for an old page are dropped. Those
// queries still finish and fill the cache, so coming back is quick.
let renderToken = 0;

// --- helpers ---------------------------------------------------------------

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const apiKey = () => credential("bugzilla");
const phabToken = () => credential("phabricator");

function age(iso) {
  if (!iso) return "";
  const days = (Date.now() - new Date(iso).getTime()) / 86400000;
  if (days < 1) return `${Math.max(1, Math.round(days * 24))}h`;
  if (days < 60) return `${Math.round(days)}d`;
  if (days < 730) return `${Math.round(days / 30)}mo`;
  return `${Math.round(days / 365)}y`;
}

function extLink(href, text) {
  return h("a", { href, target: "_blank", rel: "noopener" }, text);
}

const bugUrl = (id) => `https://bugzilla.mozilla.org/show_bug.cgi?id=${id}`;
const TABS_CONFIRM_ABOVE = 20;

// A tab per bug. Browsers allow one new tab per click unless the site may
// open pop-ups, so report how many were blocked.
function openTabsButton(bugs) {
  const blocked = h("span", { class: "warn-text", hidden: true });
  const open = () => {
    if (bugs.length > TABS_CONFIRM_ABOVE && !confirm(`Open ${bugs.length} tabs?`)) return;
    let failed = 0;
    for (const b of bugs) {
      const w = window.open(bugUrl(b.id), "_blank"); // "noopener" would always return null
      if (w) w.opener = null; else failed++;
    }
    blocked.hidden = !failed;
    blocked.textContent = ` The browser blocked ${failed} of ${bugs.length} tabs: allow pop-ups for this site and click again.`;
  };
  return [h("button", { type: "button", class: "link", onclick: open }, `Open all ${bugs.length} bugs in tabs`), blocked];
}

// --- account ---------------------------------------------------------------

// The credentials the page can hold. Each stays in this browser and is sent
// only to its own site, straight from this page.
const CREDENTIALS = {
  bugzilla: {
    storage: KEY_STORAGE,
    label: "Bugzilla",
    title: "Bugzilla API key",
    why: "The dashboard runs as you: with your key it sees exactly what your Bugzilla account sees, " +
      "including the security bugs. Without one, security checks show needs key.",
    create: ["https://bugzilla.mozilla.org/userprefs.cgi?tab=apikey", "Bugzilla → Preferences → API Keys"],
    host: "bugzilla.mozilla.org",
    name: async (key) => { const w = await bugzilla.whoami(key); return w.real_name || w.name; },
    // Only Bugzilla's "invalid API key" (306) means the key was refused; an
    // outage or any other error is not the key's fault.
    rejected: (e) => e.code === 306,
  },
  phabricator: {
    storage: "phabricatorToken",
    label: "Phabricator",
    title: "Phabricator API token",
    why: "Only the checks for open uplift revisions use it: Phabricator is the only place that says which " +
      "branch a revision targets. Without one, those checks show needs Phabricator token.",
    create: ["https://phabricator.services.mozilla.com/settings/panel/apitokens/", "Phabricator → Settings → Conduit API Tokens"],
    host: "phabricator.services.mozilla.com",
    name: async (token) => { const w = await phabricator.whoami(token); return w.realName || w.userName; },
    rejected: phabricator.rejected,
  },
};

const credential = (kind) => localStorage.getItem(CREDENTIALS[kind].storage) || "";

async function renderAccount() {
  const box = document.getElementById("account");
  const parts = Object.keys(CREDENTIALS).map((kind) => {
    const c = CREDENTIALS[kind];
    const part = h("span", {});
    const show = (...children) => part.replaceChildren(h("span", { class: "muted" }, `${c.label}: `), ...children);
    const value = credential(kind);
    if (!value) {
      show(h("button", { class: "link", onclick: () => openKeyDialog(kind) }, "add"));
      return part;
    }
    show(h("span", { class: "muted" }, "checking…"));
    c.name(value).then(
      (name) => show(h("span", {}, name, " "), h("button", { class: "link", onclick: () => openKeyDialog(kind) }, "change")),
      (e) => show(h("span", { class: "error", title: e.message }, c.rejected(e) ? "rejected " : "could not check "),
        h("button", { class: "link", onclick: () => openKeyDialog(kind) }, "fix")));
    return part;
  });
  box.replaceChildren(...parts.flatMap((p, i) => (i ? [h("span", { class: "muted" }, " · "), p] : [p])));
}

function openKeyDialog(kind = "bugzilla") {
  const c = CREDENTIALS[kind];
  const dialog = document.getElementById("key-dialog");
  dialog.dataset.kind = kind;
  document.getElementById("key-title").textContent = c.title;
  document.getElementById("key-why").textContent = c.why;
  const link = document.getElementById("key-create");
  [link.href, link.textContent] = c.create;
  document.getElementById("key-host").textContent = c.host;
  document.getElementById("key-input").value = "";
  document.getElementById("key-input").placeholder = `Paste your ${c.title}`;
  document.getElementById("key-error").hidden = true;
  document.getElementById("key-remove").hidden = !credential(kind);
  dialog.showModal();
}

function setupKeyDialog() {
  const dialog = document.getElementById("key-dialog");
  const err = document.getElementById("key-error");
  const kind = () => CREDENTIALS[dialog.dataset.kind];
  document.getElementById("key-cancel").onclick = () => dialog.close();
  document.getElementById("key-remove").onclick = () => {
    localStorage.removeItem(kind().storage);
    dialog.close();
    keyChanged();
  };
  document.getElementById("key-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const c = kind();
    const value = document.getElementById("key-input").value.trim();
    if (!value) return;
    err.hidden = true;
    try {
      await c.name(value);
    } catch (e) {
      err.textContent = c.rejected(e) ? `${c.label} did not accept that: ${e.message}` : `Could not check it: ${e.message}`;
      err.hidden = false;
      return;
    }
    localStorage.setItem(c.storage, value);
    dialog.close();
    keyChanged();
  };
}

function keyChanged() {
  renderAccount();
  route();
}

// --- navigation ------------------------------------------------------------

function renderTabs(current) {
  const nav = document.getElementById("tabs");
  nav.replaceChildren(
    h("a", { href: "#dashboard", class: current === "dashboard" ? "active" : "" }, "Dashboard"),
    ...PAGES.map((p) => h("a", { href: `#${p.id}`, class: current === p.id ? "active" : "" }, tabLabel(p))),
  );
}

async function route() {
  const page = location.hash.slice(1) || "dashboard";
  const token = ++renderToken;
  const main = document.getElementById("main");
  if (!releases) main.replaceChildren(h("p", { class: "muted" }, h("span", { class: "spinner" }), " Finding the live Firefox versions…"));
  try {
    // Every navigation, so a tab left open over a merge day moves with the
    // trains. Cached for 30 minutes, so this is usually instant.
    releases = await versions.load();
  } catch (e) {
    if (token === renderToken) main.replaceChildren(h("p", { class: "error" }, `Could not load the live versions: ${e.message}`));
    return;
  }
  if (token !== renderToken) return;
  renderTabs(page);
  main.replaceChildren(h("p", { class: "muted" }, h("span", { class: "spinner" }), " Loading…"));
  if (page === "dashboard") return renderDashboard(main, token);
  if (PAGES.some((p) => p.id === page)) return renderPage(main, page, token);
  main.append(h("p", {}, "No such page."));
}

// --- landing page ----------------------------------------------------------

function renderDashboard(main, token) {
  const cards = h("div", { class: "cards" });
  main.replaceChildren(

    h("h2", {}, "Needs attention"),
    h("p", { class: "muted" }, "Distinct bugs across each channel's Tier 1 queues. The same bug can sit in several queues, so this is not the sum of the rows."),
    cards,
  );
  for (const p of PAGES.filter((p) => p.id !== "misc")) {
    const [num, detail] = loadHeadline(p.id, "Tier 1, distinct bugs", token);
    detail.classList.add("card-detail");
    cards.append(h("a", { class: "card", href: `#${p.id}` }, h("div", { class: "card-title" }, tabLabel(p)), num, detail));
  }
}

function tabLabel(p) {
  if (!releases || !["nightly", "beta", "release"].includes(p.id)) return p.label;
  return `${p.label} ${releases[p.id].version}`;
}

// The big number and its caption, filled in when the headline arrives.
function loadHeadline(page, caption, token) {
  const num = h("div", { class: "big pending" }, h("span", { class: "spinner big-spinner" }));
  const detail = h("div", { class: "muted" }, caption);
  const tier1 = catalogue.pageChecks(page, releases).filter((c) => c.tier === "1");
  runner.headline(tier1, apiKey()).then((hl) => {
    if (token === renderToken) fillHeadline(num, detail, hl, caption);
  }).catch((e) => {
    if (token !== renderToken) return;
    num.textContent = "error";
    num.className = "big error";
    detail.textContent = e.message;
  });
  return [num, detail];
}

// Headline colour bands. Arbitrary for now: revisit once we know what a normal day looks like.
const HEADLINE_GREEN_BELOW = 5; // under 5 is green
const HEADLINE_RED_ABOVE = 10; // over 10 is red; 5 to 10 is orange

function headlineBand(count, partial) {
  if (count > HEADLINE_RED_ABOVE) return "bad";
  // A partial count is a lower bound, so it can't be called good or merely orange.
  if (partial) return "";
  return count < HEADLINE_GREEN_BELOW ? "good" : "mid";
}

function fillHeadline(num, detail, hl, caption) {
  const partial = hl.incomplete.length > 0;
  num.className = `big ${headlineBand(hl.count, partial)}`;
  num.textContent = partial ? `≥ ${hl.count}` : hl.count;
  detail.replaceChildren(caption);
  if (partial) {
    detail.append(h("ul", { class: "incomplete" },
      hl.incomplete.map((i) => h("li", {}, `${i.title}: ${i.reason}`))));
  } else if (hl.sum_of_rows > hl.count) {
    detail.append(h("div", { class: "small" }, `(${hl.sum_of_rows} if the rows were summed)`));
  }
}

// --- channel pages ---------------------------------------------------------

async function renderPage(main, page, token) {
  main.replaceChildren();

  if (page !== "misc") {
    main.append(h("section", { class: "headline" }, ...loadHeadline(page, "distinct bugs need attention across Tier 1", token)));
  } else {
    main.append(h("p", { class: "muted" }, "Not about any one version. Nothing here runs until you open it."));
  }

  const autorun = {};
  for (const section of catalogue.sections(page, releases)) {
    const { el, rows } = renderSection(section, token);
    main.append(el);
    if (section.autorun) autorun[section.tier] = rows;
  }
  const crashes = page !== "misc" ? renderCrashes(catalogue.pageChannels(page, releases), token) : null;
  if (crashes) main.append(crashes.el);

  // Tier 1, Tier 2 and crash data all start at once. Only a few Bugzilla
  // requests run at a time (bugzilla.js), so Tier 1 shares them with Tier 2.
  (autorun["1"] || []).forEach((r) => r.run());
  (autorun["2"] || []).forEach((r) => r.run());
  if (crashes) crashes.start();
}

function renderSection(section, token) {
  const rows = [];
  const body = h("div", { class: "rows" });
  const multi = new Set(section.checks.map((c) => c.channel)).size > 1;
  let lastChannel = null;
  for (const check of section.checks) {
    if (multi && check.channel !== lastChannel) {
      body.append(h("h4", { class: "channel-heading" }, check.channel.label));
      lastChannel = check.channel;
    }
    const row = new Row(check, token);
    rows.push(row);
    body.append(row.el);
  }
  const runAll = section.autorun ? null : h("button", { class: "secondary small", onclick: () => rows.forEach((r) => r.run()) }, "Run all");
  const el = h("section", { class: "tier" },
    h("div", { class: "tier-heading" },
      h("h3", {}, section.name, h("span", { class: "muted" }, ` — ${section.subtitle}`)),
      runAll),
    body);
  return { el, rows };
}

class Row {
  constructor(check, token) {
    this.check = check;
    this.token = token;
    this.result = null;
    this.loading = false;
    this.view = -1; // which of check.filters is applied; -1 shows all
    this.pill = h("span", { class: "pill idle" }, "–");
    this.panel = h("div", { class: "panel", hidden: true });
    const options = check.source?.options;
    this.opts = options ? { [options.name]: options.default } : {};
    this.seq = 0;
    this.sort = { col: -1, dir: 1 }; // kept on the row so a redrawn table keeps its order
    // A div, not a <button>: the header can hold a <select>, and Firefox does
    // not deliver clicks to controls inside a button.
    this.header = h("div", {
      class: "row-head", role: "button", tabindex: "0", "aria-expanded": "false",
      onclick: () => this.toggle(),
      onkeydown: (ev) => {
        if (ev.target === this.header && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); this.toggle(); }
      },
    },
      // A sourced check's result says what its query turned out to be.
      h("span", { class: "row-title" }, check.title, queryHelp(() => this.result?.lines ?? check.lines()),
        check.shouldBeZero ? h("span", { class: "tag" }, "should read zero") : null,
        check.needsKey ? h("span", { class: "tag" }, "needs an API key") : null,
        check.needsPhabricator ? h("span", { class: "tag" }, "needs a Phabricator token") : null,
        check.skills.length ? h("span", { class: "tag skill", title: "Has a deeper check in Claude Code; open the row" }, "Claude skill") : null),
      h("span", { class: "row-right" }, options ? this.optionSelect(options) : null, this.pill, this.refreshButton()));
    this.el = h("div", { class: "row" }, this.header, this.panel);
    if (check.needsKey && !apiKey()) this.setPill("needs key", "warn");
    else if (check.needsPhabricator && !phabToken()) this.setPill("needs Phabricator token", "warn");
    else if (check.runsOnLoad) this.setPill("queued", "queued", "Waiting for its turn: only a few Bugzilla queries run at once");
    this.renderPanel();
  }

  // Runs the check again, skipping every cached result it depends on.
  refreshButton() {
    this.refresh = h("button", {
      type: "button", class: "icon-btn refresh", hidden: true,
      title: "Refresh: run this check again now", "aria-label": `Refresh ${this.check.title}`,
      onclick: (ev) => {
        ev.stopPropagation(); // refreshing should not also open or close the row
        this.result = null;
        this.loading = false; // a newer request supersedes any in flight
        this.run({ fresh: true });
      },
    });
    return this.refresh;
  }

  setPill(text, cls, title) {
    this.pill.textContent = text;
    this.pill.className = `pill ${cls}`;
    if (title) this.pill.title = title; else this.pill.removeAttribute("title");
  }

  optionSelect(o) {
    const stop = (ev) => ev.stopPropagation(); // choosing a range should not also open or close the row
    return h("label", { class: "row-option", onclick: stop, onkeydown: stop },
      `${o.label} `,
      h("select", {
        onchange: (ev) => {
          this.opts[o.name] = ev.target.value;
          this.result = null;
          this.loading = false; // a newer request supersedes any in flight
          this.run();
        },
      }, o.choices.map(([value, text]) => h("option", { value, selected: value === o.default }, text))));
  }

  toggle() {
    this.panel.hidden = !this.panel.hidden;
    this.el.classList.toggle("open", !this.panel.hidden);
    this.header.setAttribute("aria-expanded", String(!this.panel.hidden));
    if (!this.panel.hidden && !this.result && !this.loading) this.run();
  }

  async run({ fresh = false } = {}) {
    if (this.loading || this.result) return;
    const missing = this.check.needsKey && !apiKey() ? "needs_key"
      : this.check.needsPhabricator && !phabToken() ? "needs_phab" : null;
    if (missing) {
      this.result = { state: missing };
      this.renderPanel();
      return;
    }
    this.loading = true;
    // On a refresh the icon spins where it was clicked; on a first run it stays hidden.
    this.refresh.disabled = true;
    this.refresh.classList.add("spinning");
    // A quick query would otherwise flash its loading state too briefly to see.
    const shown = fresh ? new Promise((resolve) => setTimeout(resolve, 600)) : null;
    const seq = ++this.seq;
    this.setPill("loading", "loading");
    this.renderPanel();
    let result;
    try {
      result = await runner.run(this.check, apiKey(), this.opts, { fresh, phabToken: phabToken() });
    } catch (e) {
      result = { state: "error", message: e.message };
    }
    await shown;
    if (seq !== this.seq || this.token !== renderToken) return; // superseded, or the page changed
    this.result = result;
    this.loading = false;
    this.refresh.disabled = false;
    this.refresh.classList.remove("spinning");
    this.refresh.hidden = false;
    this.showResult();
    this.renderPanel();
  }

  showResult() {
    const r = this.result;
    switch (r.state) {
      case "needs_key": return this.setPill("needs key", "warn", "Matches nothing without an API key, so it was not run.");
      case "needs_phab": return this.setPill("needs Phabricator token", "warn", "Reads Phabricator, which needs your API token, so it was not run.");
      case "unavailable": return this.setPill("unavailable", "warn", r.message);
      case "error": return this.setPill("error", "bad", r.message);
    }
    if (r.capped && r.total != null) return this.setPill(r.total.toLocaleString(), "bad", `Hit Bugzilla's row cap: the count is right, but the table lists only the first ${r.count.toLocaleString()}.`);
    if (r.capped) return this.setPill(`${r.count}+ capped`, "bad", "Hit Bugzilla's row cap: the count is truncated. Do not trust it.");
    // Tier 1 is the day's decision queue; an empty result anywhere else is "Clear".
    if (r.count === 0) return this.setPill(this.check.tier === "1" ? "Nothing waiting" : "Clear", "good");
    this.setPill(String(r.count), this.check.shouldBeZero ? "bad" : "count");
  }

  renderPanel() {
    const c = this.check;
    const filtered = c.post.length > 0; // the dashboard drops some of Bugzilla's rows itself
    const parts = [];
    if (this.loading) parts.push(h("p", { class: "muted" }, h("span", { class: "spinner" }), " Running…"));
    if (this.result) {
      const r = this.result;
      if (r.state === "needs_key") parts.push(h("p", { class: "warn-text" }, "This check matches nothing without an API key, so a zero would be false reassurance. ", h("button", { class: "link", onclick: () => openKeyDialog("bugzilla") }, "Add a key")));
      if (r.state === "needs_phab") parts.push(h("p", { class: "warn-text" }, "This check reads Phabricator, which has no anonymous access, so it needs your Phabricator API token. ", h("button", { class: "link", onclick: () => openKeyDialog("phabricator") }, "Add a token")));
      if (r.state === "unavailable") parts.push(h("p", { class: "warn-text" }, `Unavailable: ${r.message}. There is no fallback to the unfiltered result: the cross-check subtracts bugs, so without it the count would invent problems.`));
      if (r.state === "error") parts.push(h("p", { class: "error" }, `Failed: ${r.message}`));
      if (r.capped && r.total != null) parts.push(h("p", { class: "error" }, `${r.total.toLocaleString()} bugs match, past Bugzilla's ${r.candidates.toLocaleString()}-row cap. The count is right; the table below lists only the first ${r.count.toLocaleString()}.`));
      else if (r.capped) parts.push(h("p", { class: "error" }, `This query hit Bugzilla's ${r.candidates}-row cap. The result is truncated; do not trust the count.`));
    }
    for (const n of c.notes || []) parts.push(h("p", { class: "note" }, n));

    const views = this.result?.state === "ok" && this.result.bugs.length
      ? c.filters.map((_, i) => this.result.bugs.filter((b) => b.filters[i])) : [];
    const shown = views[this.view]; // undefined while showing all
    const bugs = shown ?? this.result?.bugs;
    if (views.length) {
      const choice = (view, text) => h("button", {
        type: "button", class: "secondary small", "aria-pressed": String(this.view === view),
        onclick: () => { this.view = view; this.renderPanel(); },
      }, text);
      parts.push(h("p", { class: "view-filter", role: "group", "aria-label": "Show" }, "Show: ",
        choice(-1, `All (${this.result.bugs.length})`),
        c.filters.flatMap((f, i) => [" ", choice(i, `${f.label} (${views[i].length})`)])));
    }
    const queryUrl = shown ? c.filteredQueryUrl(c.filters[this.view]) : c.queryUrl;
    const links = queryUrl ? [extLink(queryUrl, "Open query in Bugzilla")] : [];
    const count = shown ? shown.length : this.result?.count;
    const ids = shown ? runner.idsLink(shown) : this.result?.ids_url;
    if (ids) {
      if (links.length) links.push(" · ");
      links.push(extLink(ids, `Open buglist for ${count} bugs`), " · ", ...openTabsButton(bugs));
    } else if (this.result && this.result.state === "ok" && count > 0) {
      // The runner leaves ids_url out when the bug_id link would be too long.
      if (links.length) links.push(" · ");
      links.push(h("span", { class: "muted" }, `Too many bugs (${count}) to open by number`));
    }
    parts.push(h("p", { class: "links" }, links));
    if (c.skills.length) {
      parts.push(h("div", { class: "skills" },
        h("p", {}, h("strong", {}, "Deeper checks in Claude Code"), h("span", { class: "muted" }, " — run from a checkout of this repo")),
        h("ul", {}, c.skills.map((s) => h("li", {}, h("code", {}, `/${s.name}`), `: ${s.summary}`)))));
    }
    if (filtered) {
      parts.push(h("p", { class: "muted small" }, "Bugzilla can't do all of this check's filtering, so the dashboard removes some bugs itself (the \"then:\" lines under ?). Open query in Bugzilla shows the list before that, so expect more bugs there than here."));
    }
    if (this.result && this.result.state === "ok") {
      const r = this.result;
      if (filtered) parts.push(h("p", { class: "muted small" }, `Bugzilla matched ${r.candidates} bugs; ${r.count} are left after the dashboard's own filtering.`));
      if (bugs.length) parts.push(bugTable(bugs, this.sort));
      parts.push(h("p", { class: "muted small" }, `Ran ${new Date(r.ran_at).toLocaleString()} (results are reused for up to ${CACHE_MS / 60_000} minutes; reload the page for fresh ones).`));
    }
    this.panel.replaceChildren(...parts);
  }
}

// "?" icon whose hover/focus overlay shows the query as it runs. One shared
// overlay, positioned in the viewport so a long query is never clipped.
const queryOverlay = h("pre", { class: "query-overlay", hidden: true });
document.body.append(queryOverlay);

function queryHelp(getLines) {
  const show = (ev) => {
    queryOverlay.textContent = getLines().join("\n");
    queryOverlay.hidden = false;
    const r = ev.currentTarget.getBoundingClientRect();
    const { width, height } = queryOverlay.getBoundingClientRect();
    const below = r.bottom + 6;
    queryOverlay.style.top = `${below + height <= innerHeight ? below : Math.max(6, r.top - 6 - height)}px`;
    queryOverlay.style.left = `${Math.max(6, Math.min(r.left, innerWidth - width - 6))}px`;
  };
  const hide = () => { queryOverlay.hidden = true; };
  return h("span", {
    class: "query-help", tabindex: "0", role: "img", "aria-label": "Show query",
    onmouseenter: show, onmouseleave: hide, onfocus: show, onblur: hide,
    onclick: (ev) => ev.stopPropagation(), // hovering explains; it should not also open the row
  }, "?");
}

// A revision becomes a Phabricator link, and an ISO time an age like "Last changed".
function flagCell(value) {
  if (/^D\d+$/.test(value)) return h("td", {}, extLink(phabricator.revisionUrl(value.slice(1)), value));
  if (/^\d{4}-\d\d-\d\dT[\d:]+Z$/.test(value)) return h("td", { title: value }, age(value));
  return h("td", { class: value === "---" ? "muted" : "" }, value);
}

function bugTable(bugs, sort) {
  const flagCols = Object.keys(bugs[0].flags);
  const short = (f) => f.replace("cf_status_firefox", "status ").replace("cf_tracking_firefox", "tracking ").replace("_esr", "esr").replace(/_/g, " ");
  const cols = [
    ["Bug", (b) => b.id],
    ["Summary", (b) => b.summary],
    ["Product :: Component", (b) => `${b.product} :: ${b.component}`],
    ["Status", (b) => `${b.status} ${b.resolution || ""}`],
    ["Sev", (b) => b.severity],
    ["Assignee", (b) => (b.assignee === "nobody@mozilla.org" ? "" : b.assignee_name || b.assignee)],
    ...flagCols.map((f) => [short(f), (b) => (runner.DERIVED[f]?.sortKey ?? String)(b.flags[f])]),
    ["Last changed", (b) => Date.now() - new Date(b.last_change_time)], // by age, as shown
  ];
  const row = (b) => h("tr", {},
    h("td", { class: "bug-id" }, b.id ? extLink(bugUrl(b.id), b.id) : h("span", { class: "muted" }, "—"),
      b.security ? h("span", { class: "lock", role: "img", "aria-label": "Security bug", title: "Security bug" }) : null),
    h("td", { class: "summary" }, b.summary),
    h("td", {}, b.product ? `${b.product} :: ${b.component}` : ""),
    h("td", {}, b.resolution ? `${b.status} ${b.resolution}` : b.status),
    h("td", {}, b.severity),
    b.assignee === "nobody@mozilla.org"
      ? h("td", { class: "muted" }, "nobody")
      : h("td", { title: b.assignee }, b.assignee_name || b.assignee),
    flagCols.map((f) => flagCell(b.flags[f])),
    h("td", { title: b.last_change_time }, age(b.last_change_time)),
  );
  // Clicking a heading sorts ascending, then descending, then back to the query's own order.
  const tbody = h("tbody", {});
  const headRow = h("tr", {});
  const render = () => {
    const key = cols[sort.col]?.[1];
    const sorted = key ? [...bugs].sort((a, b) => sort.dir * String(key(a) ?? "").localeCompare(String(key(b) ?? ""), undefined, { numeric: true })) : bugs;
    tbody.replaceChildren(...sorted.map(row));
    headRow.replaceChildren(...cols.map(([label], i) => h("th", { "aria-sort": i === sort.col ? (sort.dir > 0 ? "ascending" : "descending") : null },
      h("button", {
        type: "button", class: "sort",
        onclick: () => {
          if (i !== sort.col) Object.assign(sort, { col: i, dir: 1 }); else if (sort.dir > 0) sort.dir = -1; else sort.col = -1;
          render();
        },
      }, label, h("span", { class: "sort-arrow", "aria-hidden": "true" }, i === sort.col ? (sort.dir > 0 ? "▲" : "▼") : "")))));
  };
  render();
  return h("div", { class: "table-wrap" }, h("table", { class: "bugs" }, h("thead", {}, headRow), tbody));
}

// --- crash data ------------------------------------------------------------

function renderCrashes(channels, token) {
  const el = h("section", { class: "tier" }, h("div", { class: "tier-heading" }, h("h3", {}, "Crash data", h("span", { class: "muted" }, " — from crash-stats"))));
  const boxes = channels.map((c) => {
    const box = h("div", { class: "crash" }, h("p", { class: "muted" }, `${c.label}: queued`));
    el.append(box);
    return [c, box];
  });
  const start = () => boxes.forEach(([c, box]) => {
    box.replaceChildren(h("p", { class: "muted" }, h("span", { class: "spinner" }), ` Loading ${c.label}…`));
    crashstats.forChannel(c).then((d) => {
      if (token === renderToken) box.replaceChildren(...crashBlock(d));
    }).catch((e) => {
      if (token === renderToken) box.replaceChildren(h("p", { class: "error" }, `${c.label}: ${e.message}`));
    });
  });
  return { el, start };
}

function crashBlock(d) {
  const head = h("h4", {}, `${d.label} — last ${d.days} days`);
  if (d.state !== "ok") return [head, h("p", { class: "warn-text" }, `Unavailable: ${d.message}`)];
  const cols = d.products.map((p) => {
    if (p.state !== "ok") return h("div", { class: "crash-product" }, h("h5", {}, p.product), h("p", { class: "warn-text" }, `Unavailable: ${p.message}`));
    if (!p.versions.length) return h("div", { class: "crash-product" }, h("h5", {}, p.product), h("p", { class: "muted" }, `No crash reports yet for the ${d.shipping} series in this window.`));
    return h("div", { class: "crash-product" },
      h("h5", {}, p.product, " ", h("span", { class: "big-inline" }, p.total.toLocaleString()), h("span", { class: "muted small" }, " crashes")),
      h("p", { class: "muted small" }, `Builds: ${p.versions.join(", ")} · `, extLink(p.search_url, "Open in crash-stats")),
      h("table", { class: "sigs" }, h("tbody", {}, p.signatures.map((s) =>
        h("tr", {}, h("td", { class: "num" }, s.count.toLocaleString()), h("td", {}, extLink(s.url, s.signature))))))
    );
  });
  return [head, h("div", { class: "crash-products" }, cols), h("p", { class: "muted small" }, d.note)];
}

// --- theme -----------------------------------------------------------------

// Follows the OS until the reader picks one; the pick is remembered.
function setupTheme() {
  const button = document.getElementById("theme-toggle");
  const os = matchMedia("(prefers-color-scheme: dark)");
  const apply = (theme) => {
    document.documentElement.dataset.theme = theme;
    const other = theme === "dark" ? "light" : "dark";
    button.textContent = theme === "dark" ? "☀︎" : "☾";
    button.title = button.ariaLabel = `Switch to ${other} mode`;
  };
  apply(document.documentElement.dataset.theme);
  button.onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    localStorage.setItem("theme", next);
    apply(next);
  };
  os.addEventListener("change", (e) => {
    if (!localStorage.getItem("theme")) apply(e.matches ? "dark" : "light");
  });
}

// --- start -----------------------------------------------------------------

setupTheme();

setupKeyDialog();
renderAccount();
window.addEventListener("hashchange", route);
route();
