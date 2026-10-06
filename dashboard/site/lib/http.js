// Shared fetch helpers and the result cache.

import { CACHE_MS, HTTP_TIMEOUT_MS } from "./config.js";

// A data source failed or answered with an error. `code` is Bugzilla's own
// error code, when Bugzilla gave one.
export class SourceError extends Error {
  constructor(message, code = null) {
    super(message);
    this.code = code;
  }
}

// TTL cache with single-flight: concurrent callers for one key share one
// request. Only successes are stored; a failure reaches every waiter and
// leaves nothing behind. Lives as long as the browser tab, so moving between
// the dashboard's pages (a hash change) comes back to warm results.
// `fresh` skips a stored result (a row's refresh button) but still joins a
// request already in flight, which is as fresh as a new one.
export class Cache {
  done = new Map();
  inflight = new Map();

  getOrRun(key, fn, ttl = CACHE_MS, fresh = false) {
    key = JSON.stringify(key);
    const hit = this.done.get(key);
    if (hit && hit.expires > Date.now() && !fresh) return Promise.resolve(hit.value);
    if (!this.inflight.has(key)) {
      this.inflight.set(key, (async () => {
        try {
          const value = await fn();
          const now = Date.now();
          for (const [k, v] of this.done) if (v.expires <= now) this.done.delete(k);
          this.done.set(key, { expires: now + ttl, value });
          return value;
        } finally {
          this.inflight.delete(key);
        }
      })());
    }
    return this.inflight.get(key);
  }

  clear() {
    this.done.clear();
  }
}

export const cache = new Cache();

// `params` is a list of [name, value] pairs, so a name can repeat.
function withParams(url, params) {
  return params && params.length ? `${url}?${new URLSearchParams(params)}` : url;
}

export async function get(url, { params, headers, redirect = "follow" } = {}) {
  try {
    return await fetch(withParams(url, params), {
      headers, redirect, signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
    });
  } catch (e) {
    throw new SourceError(`${new URL(url).host}: ${e.name} ${e.message}`.trim());
  }
}

export async function getJson(url, opts) {
  const r = await get(url, opts);
  const host = new URL(url).host;
  if (r.status !== 200) throw new SourceError(`${host} answered HTTP ${r.status}`);
  try {
    return await r.json();
  } catch {
    throw new SourceError(`${host} returned something that is not JSON`);
  }
}
