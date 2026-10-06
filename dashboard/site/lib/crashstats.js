// Crash counts and top signatures from Socorro.
//
// Socorro is asked about what *shipped* (product-details), not the train
// version: a version that has not shipped has no crashes. It also rejects
// relative dates, so the window start is an absolute timestamp.

import { CRASH_TOP_SIGNATURES, CRASH_WINDOW_DAYS } from "./config.js";
import { SourceError, cache, getJson } from "./http.js";

const SUPERSEARCH = "https://crash-stats.mozilla.org/api/SuperSearch/";
const SITE = "https://crash-stats.mozilla.org";

const NO_RATE =
  "No crash rate: Socorro has crash counts but not usage hours, so " +
  "crashes per 1,000 hours cannot be derived from it. That number lives in telemetry.";

const products = (c) => (c.isEsr ? ["Firefox"] : ["Firefox", "Fenix"]);

// Is `version` a build of the line `shipping` belongs to?
//
// A build shipped this morning has almost no crashes yet, so the window
// covers the shipping line rather than the one latest string: every beta of
// the version (158.0b3, 158.0b4), a release and its dot releases (157.0,
// 157.0.1), an ESR dot line and its respins (115.42.0esr, 115.42.1esr).
// Old ESR builds that never updated are excluded.
export function inSeries(shipping, version, kind) {
  if (kind === "nightly") return version === shipping;
  if (kind === "beta") return version.startsWith(`${shipping.split("b")[0]}b`);
  const [major, minor] = shipping.split(".");
  const parts = version.replace(/esr$/, "").split(".");
  if (!parts.every((p) => /^\d+$/.test(p)) || version.endsWith("esr") !== (kind === "esr")) return false;
  if (kind === "release") return parts[0] === major;
  return parts[0] === major && parts[1] === minor;
}

async function product(c, name, since) {
  const base = [["product", name], ["date", `>=${since}`], ["release_channel", c.kind]];
  const facets = await getJson(SUPERSEARCH, { params: [...base,
    ["_results_number", "0"], ["_facets", "version"], ["_facets_size", "200"]] });
  const versions = (facets.facets.version ?? []).map((f) => f.term)
    .filter((v) => inSeries(c.shipping, v, c.kind)).sort().reverse();
  if (!versions.length) return { product: name, versions: [], total: 0, signatures: [], search_url: null };
  const params = [...base, ...versions.map((v) => ["version", v])];
  const data = await getJson(SUPERSEARCH, { params: [...params,
    ["_results_number", "0"], ["_facets", "signature"], ["_facets_size", String(CRASH_TOP_SIGNATURES)]] });
  const sigBase = [["product", name], ["date", `>=${since}`], ...versions.map((v) => ["version", v])];
  return {
    product: name,
    versions,
    total: data.total,
    signatures: (data.facets.signature ?? []).map((s) => ({
      signature: s.term,
      count: s.count,
      url: `${SITE}/signature/?${new URLSearchParams([["signature", s.term], ...sigBase])}`,
    })),
    search_url: `${SITE}/search/?${new URLSearchParams([...params, ["_facets", "signature"]])}`,
  };
}

export async function forChannel(c) {
  const days = CRASH_WINDOW_DAYS[c.kind];
  const out = { channel: c.key, label: c.label, shipping: c.shipping, days, note: NO_RATE };
  if (!c.shipping) {
    return { ...out, state: "unavailable", message: "product-details has no shipping build for this channel" };
  }
  const start = new Date(Date.now() - days * 86_400_000);
  start.setUTCMinutes(0, 0, 0);
  const since = start.toISOString().replace(/\.\d{3}Z$/, "+00:00");

  const one = async (name) => {
    try {
      const r = await cache.getOrRun(["crash", name, c.kind, c.shipping, since], () => product(c, name, since));
      return { ...r, state: "ok" };
    } catch (e) {
      if (e instanceof SourceError || e instanceof TypeError) return { product: name, state: "error", message: e.message };
      throw e;
    }
  };
  return { ...out, state: "ok", since, products: await Promise.all(products(c).map(one)) };
}
