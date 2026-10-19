#!/usr/bin/env python3
"""Checks for uplift-facts.py's parsing, on text from real bugs. Run: python3 scripts/uplift/test_uplift_facts.py"""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("uplift_facts", Path(__file__).with_name("uplift-facts.py"))
uf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uf)

# A commit lands the bugs before " - ", not ones its subject mentions later.
assert uf.bugs_of("Bug 2078555: Part 1 - Version ModuleLoadInfo where it crosses binaries r=yjuglaret") == ["2078555"]
assert uf.bugs_of("Bug 2079560, Bug 2080718 - Fix both r=x") == ["2079560", "2080718"]
assert uf.bugs_of("Bug 2078664 - Move mozglue.dll, see bug 2078555 r=bhearsum") == ["2078664"]
assert uf.bugs_of('Revert "Bug 2030810 - add previousSessionEnd" for causing failures') == ["2030810"]

# The form, with a continuation line, from bug 2079560 comment 12.
form = uf.parse_form("""### firefox-beta Uplift Approval Request
- **User impact if declined/Reason for urgency**: Fixes a lockup scenario in the partial generation.

Nightly is currently using
- **Code covered by automated testing?**: yes
- **Steps to reproduce for manual QE testing**:
- **Risk associated with taking this patch**: low""")
assert form["User impact if declined/Reason for urgency"] == (
    "Fixes a lockup scenario in the partial generation. Nightly is currently using")
assert form["Code covered by automated testing"] == "yes"
assert form["Steps to reproduce for manual QE testing"] == ""
assert form["Risk associated with taking this patch"] == "low"

comments = [
    {"count": 7, "text": "### firefox-beta Uplift Approval Request\n- **Risk associated with taking this patch**: medium"},
    {"count": 8, "text": "Created attachment 9663325\nBug 2080718: Always remove listeners.\n\n\n"
                         "Original Revision: https://phabricator.services.mozilla.com/D333543"},
    {"count": 9, "text": "Created attachment 9659219\nBug 2080718: Always remove listeners. r?asuth!"},
    {"count": 11, "text": "### firefox-beta Uplift Approval Request\n- **Risk associated with taking this patch**: low"},
    {"count": 12, "text": "### firefox-release Uplift Approval Request\n- **Risk associated with taking this patch**: high"},
]
assert uf.original_revisions(comments) == {9663325: "D333543"}
latest = uf.latest_form(comments, "beta")
assert (latest["comment"], latest["count"], latest["Risk associated with taking this patch"]) == (11, 2, "low")
assert uf.latest_form(comments, "esr115") is None

assert uf.strip_trailer("Bug 2074601 - Detect current selected tab r=ngrato,omc-reviewers") == \
    uf.strip_trailer("Bug 2074601 - Detect current selected tab  a=RyanVM") == "Bug 2074601 - Detect current selected tab"

print("ok")
