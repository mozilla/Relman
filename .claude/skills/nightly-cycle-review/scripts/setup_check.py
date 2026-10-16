"""Shared setup checks for the nightly-cycle-review scripts: Bugzilla API key and external tools."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

RELMAN_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RELMAN_ROOT / "scripts/relnotes"))
import trainlib  # noqa: E402

API_KEY_FILE = Path("~/.config/bugzilla/api-key")
API_KEY_URL = "https://bugzilla.mozilla.org/userprefs.cgi?tab=apikey"
USER_AGENT = "relman-nightly-cycle-review"

KEY_SETUP = f"""\
To include security bugs and the user-report section, set up a Bugzilla API key:
  1. Create a key at {API_KEY_URL} (description e.g. "relman cycle review").
  2. In your own terminal, not in a Claude session, store it without echoing it:
       mkdir -p ~/.config/bugzilla && chmod 700 ~/.config/bugzilla
       read -rs KEY && printf '%s' "$KEY" > ~/.config/bugzilla/api-key && unset KEY
       chmod 600 ~/.config/bugzilla/api-key
     (Alternatively, export BUGZILLA_API_KEY in your shell profile.)
  3. Re-run this script. Never paste the key into a chat."""


def resolve_repo(cli=None):
    """The Gecko checkout, resolved like every other Relman script (see trainlib.resolve_repo)."""
    return trainlib.resolve_repo(cli)


def default_outdir(version):
    """Scratch output stays out of the working tree; RELMAN_CYCLE_OUTDIR overrides the base."""
    base = os.environ.get("RELMAN_CYCLE_OUTDIR") or Path(tempfile.gettempdir()) / "relman-cycle-review"
    return Path(base).expanduser() / f"cycle-{version}"


def api_key():
    if os.environ.get("BUGZILLA_API_KEY"):
        return os.environ["BUGZILLA_API_KEY"].strip() or None
    path = API_KEY_FILE.expanduser()
    if path.is_file():
        return path.read_text().strip() or None
    return None


def bugzilla_status():
    """Return (key, problem): problem is None when the key works, else a one-line explanation."""
    key = api_key()
    if not key:
        return None, f"no Bugzilla API key (BUGZILLA_API_KEY or {API_KEY_FILE})"
    path = API_KEY_FILE.expanduser()
    if not os.environ.get("BUGZILLA_API_KEY") and path.stat().st_mode & 0o077:
        print(f"# warning: {path} is readable by other users; run: chmod 600 {path}",
              file=sys.stderr)
    req = urllib.request.Request("https://bugzilla.mozilla.org/rest/whoami",
                                 headers={"X-BUGZILLA-API-KEY": key, "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            return None, "the Bugzilla API key was rejected (revoked or mistyped)"
        return key, None
    except (urllib.error.URLError, TimeoutError):
        return key, None
    return key, None


def report_bugzilla(problem):
    if problem:
        print(f"# NOTE: running without Bugzilla authentication: {problem}.\n"
              "# Security-restricted bugs will be missing and the user-report section skipped.\n"
              + "\n".join("# " + line for line in KEY_SETUP.splitlines()), file=sys.stderr)


def missing_tools(relman=None):
    """Tools and checkouts the signals script uses, with the fix for each one missing."""
    out = []
    if not shutil.which("socorro-cli"):
        out.append(("crash pings", "socorro-cli not found; run ./mach bootstrap in the Firefox checkout"))
    if not shutil.which("gh"):
        out.append(("webcompat.com reports", "gh not found; install the GitHub CLI and run gh auth login"))
    elif subprocess.run(["gh", "auth", "status"], capture_output=True).returncode:
        out.append(("webcompat.com reports", "gh is not logged in; run gh auth login"))
    if relman and not (Path(relman).expanduser() / "scripts/relnotes/pref-delta.py").is_file():
        out.append(("pref defaults", f"scripts/relnotes/pref-delta.py not found under {relman}"))
    return out
