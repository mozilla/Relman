// Phabricator's API (Conduit). Every call runs as the reader's own API token,
// sent straight from this page to phabricator.services.mozilla.com; Conduit
// has no anonymous access, even to public revisions.

import { RELEASES_CACHE_MS } from "./config.js";
import { SourceError, cache, get } from "./http.js";

const API = "https://phabricator.services.mozilla.com/api";
// Revisions still in progress. Landed ("published") and abandoned ones are left out.
const OPEN = ["needs-review", "accepted", "needs-revision"];

export const revisionUrl = (id) => `https://phabricator.services.mozilla.com/D${id}`;

async function call(method, params, token) {
  // A plain form POST, so the browser sends it without a CORS preflight. Never
  // follow a redirect: it would carry the token to wherever it points.
  const r = await get(`${API}/${method}`, {
    form: { params: JSON.stringify({ ...params, __conduit__: { token } }), output: "json" },
    redirect: "error",
  });
  let body;
  try {
    body = await r.json();
  } catch {
    throw new SourceError(`Phabricator answered HTTP ${r.status} with no JSON body`);
  }
  if (body.error_code) throw new SourceError(`Phabricator error ${body.error_code}: ${body.error_info}`, body.error_code);
  return body.result;
}

export const whoami = (token) => call("user.whoami", {}, token);

// A token Phabricator refused, as opposed to Phabricator being down.
export const rejected = (e) => typeof e.code === "string" && e.code.startsWith("ERR-INVALID");

// The repository's id, from its short name (firefox-beta, firefox-esr140).
function repositoryPhid(shortName, token) {
  return cache.getOrRun(["phab-repo", shortName], async () => {
    const [repo] = (await call("diffusion.repository.search", { constraints: { shortNames: [shortName] } }, token)).data;
    if (!repo) throw new SourceError(`Phabricator has no repository ${shortName}`);
    return repo.phid;
  }, RELEASES_CACHE_MS);
}

// Every open revision targeting the repository: [{ id, title, status, bug, changed }],
// where `changed` is when the revision itself last changed, as an ISO time.
export async function openRevisions(shortName, token) {
  const phid = await repositoryPhid(shortName, token);
  const out = [];
  let after = null;
  do {
    const page = await call("differential.revision.search", {
      constraints: { repositoryPHIDs: [phid], statuses: OPEN }, limit: 100, ...(after ? { after } : {}),
    }, token);
    for (const r of page.data) {
      const f = r.fields;
      out.push({
        id: r.id, title: f.title, status: f.status.value, bug: Number(f["bugzilla.bug-id"]) || null,
        changed: new Date(f.dateModified * 1000).toISOString().replace(/\.\d{3}Z$/, "Z"),
      });
    }
    after = page.cursor.after;
  } while (after);
  return out;
}
