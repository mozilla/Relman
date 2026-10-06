// Which Firefox versions are live, what each is shipping, and when the cycle began.
//
// Two sources that are not interchangeable:
//
// - the uplift-train API gives the *version* (what Bugzilla fields are named
//   after). It names the next train: on merge day Beta becomes the new version
//   immediately.
// - product-details gives the *shipping* build string (what crash-stats knows
//   about). It lags the version for the first days of a cycle.

import { EXTRA_ESR_VERSIONS, RELEASES_CACHE_MS } from "./config.js";
import { cache, getJson } from "./http.js";

const TRAIN_URL = "https://whattrainisitnow.com/api/lando/uplift/train/";
const SCHEDULE_URL = "https://whattrainisitnow.com/api/release/schedule/";
const PRODUCT_DETAILS_URL = "https://product-details.mozilla.org/1.0/firefox_versions.json";

export class Channel {
  constructor(key, kind, version, shipping) {
    this.key = key; // nightly, beta, release, esr153
    this.kind = kind; // nightly, beta, release, esr
    this.version = version;
    this.shipping = shipping ?? null;
  }

  get isEsr() { return this.kind === "esr"; }
  get label() { return this.isEsr ? `ESR ${this.version}` : `Fx${this.version} ${this.kind}`; }
  get suffix() { return this.isEsr ? `esr${this.version}` : String(this.version); }
  get statusField() { return `cf_status_firefox${this.isEsr ? "_" : ""}${this.suffix}`; }
  get trackingField() { return `cf_tracking_firefox${this.isEsr ? "_" : ""}${this.suffix}`; }

  // Mainline flags carry no version, so they can be stale; ESR flags cannot.
  get approvalFlag() {
    if (this.kind === "nightly") return null;
    return `approval-mozilla-${this.isEsr ? this.suffix : this.kind}`;
  }

  // Branch on mozilla-firefox/firefox that uplifts for this channel land on.
  get branch() {
    if (this.kind === "nightly") return null;
    return this.isEsr ? `esr${this.version}` : this.kind;
  }
}

class Releases {
  constructor({ nightly, beta, release, esrs, cycleStart }) {
    Object.assign(this, { nightly, beta, release, esrs, cycleStart });
  }

  get mainline() { return [this.nightly, this.beta, this.release]; }
  get all() { return [...this.mainline, ...this.esrs]; } // ESRs newest first
}

function esrShipping(details, version) {
  for (const [key, value] of Object.entries(details)) {
    if (key.startsWith("FIREFOX_ESR") && value.split(".")[0] === String(version)) return value;
  }
  return null;
}

export function build(train, details, mergeDay) {
  const mainline = (kind, shipping) => new Channel(kind, kind, Number(train[kind].version), shipping);
  const esrVersions = new Set(EXTRA_ESR_VERSIONS);
  for (const slot of ["esr", "esr_previous"]) {
    if (train[slot]) esrVersions.add(Number(train[slot].version));
  }
  return new Releases({
    nightly: mainline("nightly", details.FIREFOX_NIGHTLY),
    beta: mainline("beta", details.LATEST_FIREFOX_DEVEL_VERSION),
    release: mainline("release", details.LATEST_FIREFOX_VERSION),
    esrs: [...esrVersions].sort((a, b) => b - a)
      .map((v) => new Channel(`esr${v}`, "esr", v, esrShipping(details, v))),
    cycleStart: mergeDay.replace(" ", "T"), // ISO form, as Bugzilla links use
  });
}

export function load() {
  return cache.getOrRun(["releases"], async () => {
    const [train, details] = await Promise.all([getJson(TRAIN_URL), getJson(PRODUCT_DETAILS_URL)]);
    const schedule = await getJson(SCHEDULE_URL, { params: [["version", train.beta.version]] });
    return build(train, details, schedule.merge_day);
  }, RELEASES_CACHE_MS);
}
