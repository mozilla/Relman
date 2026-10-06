import * as bugzilla from "./lib/bugzilla.js";
import { CACHE_MS } from "./lib/config.js";
import * as catalogue from "./lib/catalogue.js";
import * as crashstats from "./lib/crashstats.js";
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

function apiKey() {
  return localStorage.getItem(KEY_STORAGE) || "";
}

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

// --- account ---------------------------------------------------------------

async function renderAccount() {
  const box = document.getElementById("account");
  box.replaceChildren();
  const key = apiKey();
  if (!key) {
    box.append(h("span", { class: "muted" }, "No API key "), h("button", { class: "link", onclick: openKeyDialog }, "add"));
    return;
  }
  box.append(h("span", { class: "muted" }, "Checking key…"));
  try {
    const who = await bugzilla.whoami(key);
    box.replaceChildren(h("span", {}, who.real_name || who.name, " "), h("button", { class: "link", onclick: openKeyDialog }, "change"));
  } catch (e) {
    const text = rejected(e) ? "API key rejected " : `Could not check key: ${e.message} `;
    box.replaceChildren(h("span", { class: "error", title: e.message }, text), h("button", { class: "link", onclick: openKeyDialog }, "fix"));
  }
}

// Only Bugzilla's "invalid API key" (306) means the key was refused; an
// outage or any other error is not the key's fault.
const rejected = (e) => e.code === 306;

function openKeyDialog() {
  document.getElementById("key-input").value = "";
  document.getElementById("key-error").hidden = true;
  document.getElementById("key-remove").hidden = !apiKey();
  document.getElementById("key-dialog").showModal();
}

function setupKeyDialog() {
  const dialog = document.getElementById("key-dialog");
  const err = document.getElementById("key-error");
  document.getElementById("key-cancel").onclick = () => dialog.close();
  document.getElementById("key-remove").onclick = () => {
    localStorage.removeItem(KEY_STORAGE);
    dialog.close();
    keyChanged();
  };
  document.getElementById("key-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const key = document.getElementById("key-input").value.trim();
    if (!key) return;
    err.hidden = true;
    try {
      await bugzilla.whoami(key);
    } catch (e) {
      err.textContent = rejected(e) ? `Bugzilla did not accept that key: ${e.message}` : `Could not check the key: ${e.message}`;
      err.hidden = false;
      return;
    }
    localStorage.setItem(KEY_STORAGE, key);
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

  // Tier 1 first, on its own: it is the morning's work, and only a few
  // Bugzilla requests run at once (bugzilla.js), so anything started alongside
  // it would delay it. Then Tier 2 and crash data together.
  await Promise.all((autorun["1"] || []).map((r) => r.run()));
  if (token !== renderToken) return;
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
    this.pill = h("span", { class: "pill idle" }, "–");
    this.panel = h("div", { class: "panel", hidden: true });
    const options = check.source?.options;
    this.opts = options ? { [options.name]: options.default } : {};
    this.seq = 0;
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
        check.skills.length ? h("span", { class: "tag skill", title: "Has a deeper check in Claude Code; open the row" }, "Claude skill") : null),
      h("span", { class: "row-right" }, options ? this.optionSelect(options) : null, this.pill, this.refreshButton()));
    this.el = h("div", { class: "row" }, this.header, this.panel);
    if (check.needsKey && !apiKey()) this.setPill("needs key", "warn");
    else if (check.runsOnLoad) this.setPill("queued", "queued", "Waiting for its turn: Tier 1 loads first");
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
    if (this.check.needsKey && !apiKey()) {
      this.result = { state: "needs_key" };
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
      result = await runner.run(this.check, apiKey(), this.opts, { fresh });
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
      if (r.state === "needs_key") parts.push(h("p", { class: "warn-text" }, "This check matches nothing without an API key, so a zero would be false reassurance. ", h("button", { class: "link", onclick: openKeyDialog }, "Add a key")));
      if (r.state === "unavailable") parts.push(h("p", { class: "warn-text" }, `Unavailable: ${r.message}. There is no fallback to the unfiltered result: the cross-check subtracts bugs, so without it the count would invent problems.`));
      if (r.state === "error") parts.push(h("p", { class: "error" }, `Failed: ${r.message}`));
      if (r.capped && r.total != null) parts.push(h("p", { class: "error" }, `${r.total.toLocaleString()} bugs match, past Bugzilla's ${r.candidates.toLocaleString()}-row cap. The count is right; the table below lists only the first ${r.count.toLocaleString()}.`));
      else if (r.capped) parts.push(h("p", { class: "error" }, `This query hit Bugzilla's ${r.candidates}-row cap. The result is truncated; do not trust the count.`));
    }
    for (const n of c.notes || []) parts.push(h("p", { class: "note" }, n));

    const links = c.queryUrl ? [extLink(c.queryUrl, "Open query in Bugzilla")] : [];
    if (this.result && this.result.ids_url) {
      if (links.length) links.push(" · ");
      links.push(extLink(this.result.ids_url, `Open these ${this.result.count} bugs in Bugzilla`));
    } else if (this.result && this.result.state === "ok" && this.result.count > 0) {
      // The runner leaves ids_url out when the bug_id link would be too long.
      if (links.length) links.push(" · ");
      links.push(h("span", { class: "muted" }, `Too many bugs (${this.result.count}) to open by number`));
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
      if (r.bugs.length) parts.push(bugTable(r.bugs));
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

function bugTable(bugs) {
  const flagCols = Object.keys(bugs[0].flags);
  const short = (f) => f.replace("cf_status_firefox", "status ").replace("cf_tracking_firefox", "tracking ").replace("_esr", "esr").replace(/_/g, " ");
  return h("div", { class: "table-wrap" }, h("table", { class: "bugs" },
    h("thead", {}, h("tr", {},
      ["Bug", "Summary", "Product :: Component", "Status", "Sev", "Assignee", ...flagCols.map(short), "Last changed"].map((t) => h("th", {}, t)))),
    h("tbody", {}, bugs.map((b) => h("tr", {},
      h("td", { class: "bug-id" }, extLink(`https://bugzilla.mozilla.org/show_bug.cgi?id=${b.id}`, b.id),
        b.security ? h("span", { class: "lock", role: "img", "aria-label": "Security bug", title: "Security bug" }) : null),
      h("td", { class: "summary" }, b.summary),
      h("td", {}, `${b.product} :: ${b.component}`),
      h("td", {}, b.resolution ? `${b.status} ${b.resolution}` : b.status),
      h("td", {}, b.severity),
      b.assignee === "nobody@mozilla.org"
        ? h("td", { class: "muted" }, "nobody")
        : h("td", { title: b.assignee }, b.assignee_name || b.assignee),
      flagCols.map((f) => h("td", { class: b.flags[f] === "---" ? "muted" : "" }, b.flags[f])),
      h("td", { title: b.last_change_time }, age(b.last_change_time)),
    ))),
  ));
}

// --- crash data ------------------------------------------------------------

function renderCrashes(channels, token) {
  const el = h("section", { class: "tier" }, h("div", { class: "tier-heading" }, h("h3", {}, "Crash data", h("span", { class: "muted" }, " — from crash-stats"))));
  const boxes = channels.map((c) => {
    const box = h("div", { class: "crash" }, h("p", { class: "muted" }, `${c.label}: queued until Tier 1 has loaded`));
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
