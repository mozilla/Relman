// Settings. Edit here; there is no other configuration.

// ESR lines that are supported but absent from the uplift-train API, which only
// has `esr` and `esr_previous` slots. ESR 115 was extended for users on OS
// versions that cannot move to a newer ESR, so it is in neither slot.
//
// Remove 115 when it leaves support. Nothing will remind you: the API never
// mentioned it in the first place.
export const EXTRA_ESR_VERSIONS = [115];

// How long a successful result is reused. Errors are never cached.
export const CACHE_MS = 30 * 60_000;
// Version and schedule data change on merge day, not minute to minute.
export const RELEASES_CACHE_MS = 30 * 60_000;

// Bugzilla returns at most this many rows; a result this size is truncated.
export const BUGZILLA_ROW_CAP = 10_000;
// Concurrent requests to Bugzilla from this page.
export const BUGZILLA_CONCURRENCY = 6;
export const HTTP_TIMEOUT_MS = 90_000;

// Crash-stats windows, in days, by channel kind.
export const CRASH_WINDOW_DAYS = { nightly: 3, beta: 7, release: 14, esr: 14 };
export const CRASH_TOP_SIGNATURES = 10;

export const GITHUB_REPO = "mozilla-firefox/firefox";
