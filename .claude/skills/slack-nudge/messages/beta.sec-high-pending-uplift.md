# beta.sec-high-pending-uplift

Sec-high and sec-critical bugs fixed on Nightly, still affecting Beta, with
no Beta uplift request. The ask: request Beta uplift and
ESR uplift too where an ESR is affected.

Every bug here is restricted: bug numbers and links only. Call them "security
bugs", never by rating (sec-high, sec-critical).

`<beta>` is the version from the check's `channel` (`Fx160 beta` → 160).

**Affected ESRs:** a bug's `flags` include each current ESR's status
(`cf_status_firefox_esr140` and so on). The ESRs whose status is `affected`
are that bug's `<esrs>`, newest first, written like `ESR 153 and 115`. Leave
the ESR wording out for a bug with none.

## One bug

```
Hi @<slack name>, security bug <id> is fixed on Nightly but Beta <beta> is still
affected and there's no Beta uplift request yet:
<link>
Could you please add a Beta uplift request? Thanks!
```

With affected ESRs, the ask becomes:

```
<esrs> are affected too, so could you add uplift requests for Beta and ESR? Thanks!
```

(`is affected` for a single ESR.)

## Several bugs

```
Hi @<slack name>, these security bugs are fixed on Nightly but Beta <beta> is still
affected and there's no Beta uplift request yet:
• <link>
• <link> (<esrs> affected too)
Could you please add a Beta uplift request, and ESR uplift where applicable? Thanks!
```

Drop `, and ESR uplift where applicable` when no bug in the message has an
affected ESR.
