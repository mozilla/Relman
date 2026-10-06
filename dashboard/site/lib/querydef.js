// Bugzilla query model.
//
// A query is an ordered list of terms. The REST parameters, the buglist.cgi
// link and the readable text shown under each row's "?" are all generated from
// that one list, so the text people read is always the query that was sent.
//
// Boolean-chart rows (f/o/v) are numbered here and nowhere else: Bugzilla reads a
// gap in the numbering as a different query rather than an error, so nothing
// outside this module should ever pick a number.

const BUGZILLA = "https://bugzilla.mozilla.org";
const ORDER = "changeddate"; // last-changed ascending: the stalest bug is on top

// A plain search parameter: resolution=FIXED.
export const Eq = (name, value) => ({
  params: () => [[name, value]],
  lines: () => [`${name} = ${value}`],
});

// A repeated search parameter: product=Core&product=Firefox.
export const In = (name, values) => ({
  params: () => values.map((v) => [name, v]),
  lines: () => [`${name} in ${values.join(", ")}`],
});

export const Keywords = (value) => ({
  params: () => [["keywords", value], ["keywords_type", "allwords"]],
  lines: () => [`keywords = ${value} (allwords)`],
});

// chfield: the field changed (to a value) within a date window.
export const Changed = (field, since, value = null) => ({
  params: () => [
    ["chfield", field],
    ...(value !== null ? [["chfieldvalue", value]] : []),
    ["chfieldfrom", since], ["chfieldto", "Now"],
  ],
  lines: () => [
    `changed field = ${field}`,
    ...(value !== null ? [`changed to value = ${value}`] : []),
    `changed from = ${since}`, "changed to = Now",
  ],
});

// One boolean-chart row: f/o/v.
export const Chart = (field, op, value) => ({
  params: (n) => {
    const i = n();
    return [[`f${i}`, field], [`o${i}`, op], [`v${i}`, value]];
  },
  lines: () => [`${field} ${op} ${value}`],
});

const group = (join, terms) => ({
  params: (n) => {
    const i = n();
    const inner = terms.flatMap((t) => t.params(n));
    return [[`f${i}`, "OP"], [`j${i}`, join], ...inner, [`f${n()}`, "CP"]];
  },
  lines: () => [
    join === "OR" ? "ANY OF (" : "ALL OF (",
    ...terms.flatMap((t) => t.lines()).map((ln) => `  ${ln}`),
    ")",
  ],
});

export const anyOf = (...terms) => group("OR", terms);
export const allOf = (...terms) => group("AND", terms);

export class Query {
  // `linkOnly`: terms only the buglist.cgi link can carry (REST rejects them).
  constructor(terms, linkOnly = []) {
    this.terms = terms;
    this.linkOnly = linkOnly;
  }

  #params(terms) {
    let i = 0;
    const n = () => ++i;
    return [["query_format", "advanced"], ["order", ORDER], ...terms.flatMap((t) => t.params(n))];
  }

  // query_format is a buglist.cgi parameter; REST ignores it, so drop it.
  restParams() {
    return this.#params(this.terms).filter(([k]) => k !== "query_format");
  }

  linkParams() {
    return this.#params([...this.terms, ...this.linkOnly]);
  }

  buglistUrl() {
    return `${BUGZILLA}/buglist.cgi?${new URLSearchParams(this.linkParams())}`;
  }

  lines() {
    return this.terms.flatMap((t) => t.lines());
  }

  linkOnlyLines() {
    return this.linkOnly.flatMap((t) => t.lines());
  }

  extended(...terms) {
    return new Query([...this.terms, ...terms], this.linkOnly);
  }
}

// A buglist link naming exactly these bugs, in the same order rule.
export function idsUrl(ids) {
  return `${BUGZILLA}/buglist.cgi?${new URLSearchParams([["bug_id", ids.join(",")], ["order", ORDER]])}`;
}
