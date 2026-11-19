#!/usr/bin/env python3
"""Get the status of one or more Taskcluster task groups.

ex: tk.py AxS4W3EDR1e-d8X96nw1dA [OtherTaskId ...]
"""

import itertools
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# Colors are disabled when the output is not a terminal or when NO_COLOR is set
USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _ansi(code):
    return f"\033[{code}m" if USE_COLOR else ""


ANSI_RED = _ansi("31")
ANSI_GREEN = _ansi("32")
ANSI_YELLOW = _ansi("33")
ANSI_BLUE = _ansi("34")
ANSI_MAGENTA = _ansi("35")
ANSI_CYAN = _ansi("36")
ANSI_WHITE = _ansi("37")
ANSI_BOLD = _ansi("1")
ANSI_DIM = _ansi("2")
ANSI_CLOSE = _ansi("0")

# Use a taskcluster binary next to the script if there is one, else the one on the PATH
_LOCAL_TC = os.path.join(os.path.dirname(os.path.realpath(__file__)), "taskcluster")
TASKCLUSTER = _LOCAL_TC if os.access(_LOCAL_TC, os.X_OK) else (shutil.which("taskcluster") or _LOCAL_TC)
TC_ROOT = "https://firefox-ci-tc.services.mozilla.com"
# The CLI needs a root URL; default to Firefox CI if the user has not set one
TC_ENV = {**os.environ, "TASKCLUSTER_ROOT_URL": os.environ.get("TASKCLUSTER_ROOT_URL") or TC_ROOT}

# Display order: completed, problems, then in progress / waiting
STATE_ORDER = ["completed", "failed", "exception", "running", "pending", "unscheduled"]


def state_style(state):
    """Icon and color for each Taskcluster state."""
    return {
        "completed": ("✔", ANSI_GREEN),
        "failed": ("✘", ANSI_RED),
        "exception": ("⚠", ANSI_MAGENTA),
        "running": ("▶", ANSI_CYAN),
        "pending": ("◌", ANSI_YELLOW),
        "unscheduled": ("○", ANSI_DIM),
    }.get(state.lower(), ("•", ANSI_WHITE))


def line(text=""):
    """Card line, prefixed with the left border."""
    print(ANSI_DIM + "│" + ANSI_CLOSE + ("  " + text if text else ""))


def field(label, value, color=ANSI_CYAN):
    line(ANSI_DIM + label.ljust(7) + ANSI_CLOSE + color + value + ANSI_CLOSE)


def ucfirst(s):
    return s[:1].upper() + s[1:]


def to_int(s):
    """Mimic PHP's (int) cast: leading integer, 0 otherwise."""
    m = re.match(r"\s*([+-]?\d+)", s)
    return int(m.group(1)) if m else 0


def run_taskcluster(*args):
    try:
        res = subprocess.run(
            [TASKCLUSTER, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=TC_ENV,
            text=True,
        )
        return res.stdout.strip()
    except OSError:
        return ""


def fetch_task_def(task):
    url = f"{TC_ROOT}/api/queue/v1/task/" + urllib.parse.quote(task, safe="")
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            if resp.status == 200:
                return resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    return None


def get_product(response):
    """Get the product and version from the task definition."""
    product = "Unknown Product"
    version = ""
    if not response:
        return product, version

    try:
        task_def = json.loads(response) or {}
    except ValueError:
        return product, version

    # In action tasks, the product is often in payload.env.ACTION_INPUT (a JSON string)
    action_input_raw = ((task_def.get("payload") or {}).get("env") or {}).get("ACTION_INPUT")
    flavor = (task_def.get("extra") or {}).get("release_promotion_flavor") or ""

    if action_input_raw:
        try:
            action_input = json.loads(action_input_raw) or {}
        except ValueError:
            action_input = {}

        # Use the actual version, fall back to next_version if missing
        v = action_input.get("version")
        if v is None:
            v = action_input.get("next_version")
        version = "" if v is None else str(v)

        # Get the product from the release promotion flavor in the action input
        if action_input.get("release_promotion_flavor") is not None:
            flavor = action_input["release_promotion_flavor"]

    # Map the flavor to a readable product name
    if "devedition" in flavor:
        product = "Firefox Developer Edition"
    elif "android" in flavor or "fennec" in flavor:
        product = "Firefox for Android"
    elif "firefox" in flavor:
        product = "Firefox Desktop"
    elif "thunderbird" in flavor:
        product = "Thunderbird"
    elif flavor:
        product = ucfirst(flavor.replace("_", " "))

    return product, version


def fetch_all(tasks):
    """Run all requests (taskcluster CLI + API) in parallel, with a spinner."""
    commands = {"name": ("task", "name"), "status": ("group", "status"), "state": ("task", "status")}
    results = {t: {"name": "", "status": "", "state": "", "api": None} for t in tasks}

    jobs = []
    with ThreadPoolExecutor(max_workers=len(tasks) * 4) as pool:
        for task in tasks:
            for key, cmd in commands.items():
                jobs.append((task, key, pool.submit(run_taskcluster, *cmd, task)))
            jobs.append((task, "api", pool.submit(fetch_task_def, task)))

        # Spinner on stderr, only when it is a terminal
        show_spinner = sys.stderr.isatty()
        frames = itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
        total = len(jobs)
        while True:
            done = sum(1 for _, _, f in jobs if f.done())
            if show_spinner:
                sys.stderr.write(
                    f"\r{ANSI_YELLOW}{next(frames)}{ANSI_CLOSE} Waiting for Taskcluster data… "
                    f"{ANSI_WHITE}({done}/{total}){ANSI_CLOSE}\033[K"
                )
                sys.stderr.flush()
            if done == total:
                break
            time.sleep(0.1)

        if show_spinner:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()

    for task, key, fut in jobs:
        results[task][key] = fut.result()
    return results


def order_key(state):
    idx = STATE_ORDER.index(state) if state in STATE_ORDER else 99
    return (idx, state)


def print_card(task, res):
    taskname = res["name"]
    data = res["status"]
    # "taskcluster task status" returns e.g. "failed 'failed'": keep only the first word
    state = (res["state"].split() or [""])[0].lower()

    # No task group AND no task: unknown ID
    if not data and not state:
        print(ANSI_RED + "╭─ ✘ Unknown task" + ANSI_CLOSE)
        field("Task", task)
        line(ANSI_RED + "Error or no data returned by Taskcluster" + ANSI_CLOSE)
        print(ANSI_RED + "╰─" + ANSI_CLOSE)
        return

    product, version = get_product(res["api"])

    # Parse the status lines
    counts = {}
    for value in data.split("\n"):
        if not value:
            continue
        split = value.split(":")
        if len(split) > 1:
            counts[split[0].strip().lower()] = to_int(split[1])
    counts = dict(sorted(counts.items(), key=lambda kv: order_key(kv[0])))

    total = sum(counts.values())
    resolved = counts.get("completed", 0)
    problems = counts.get("failed", 0) + counts.get("exception", 0)

    # Card color from the overall state
    if total > 0:
        card_color = ANSI_RED if problems > 0 else (ANSI_GREEN if resolved == total else ANSI_YELLOW)
    else:
        card_color = state_style(state)[1]

    # Output
    print(card_color + "╭─ " + ANSI_CLOSE + ANSI_BOLD + product + ANSI_CLOSE
          + (ANSI_MAGENTA + " " + version + ANSI_CLOSE if version else ""))
    field("Task", task)
    field("Name", taskname, ANSI_WHITE)

    # The task exists but has no task group (yet): show its state
    if not counts:
        field("Link", f"{TC_ROOT}/tasks/{task}", ANSI_BLUE)
        line()
        icon, color = state_style(state)
        if state in ("pending", "unscheduled"):
            note = "not started yet, no task group"
        else:
            note = "no task group"
        line(color + icon + " " + state + ANSI_CLOSE + ANSI_DIM + f"  ({note})" + ANSI_CLOSE)
        print(card_color + "╰─" + ANSI_CLOSE)
        return

    field("Link", f"{TC_ROOT}/tasks/groups/{task}", ANSI_BLUE)
    line()

    num_width = len(str(max(counts.values())))
    for key, value in counts.items():
        icon, color = state_style(key)
        line(color + icon + " " + ucfirst(key).ljust(12) + ANSI_CLOSE
             + ANSI_BOLD + str(value).rjust(num_width) + ANSI_CLOSE)

    # Completion percentage (only successfully completed tasks count)
    if total > 0:
        pct = resolved / total * 100
        width = 30
        filled = math.floor(resolved / total * width)
        bar_color = ANSI_GREEN if pct >= 100 else (ANSI_RED if problems > 0 else ANSI_YELLOW)
        line()
        line(bar_color + "━" * filled + ANSI_CLOSE
             + ANSI_DIM + "━" * (width - filled) + ANSI_CLOSE
             + " " + ANSI_BOLD + f"{pct:.1f}%".rjust(6) + ANSI_CLOSE
             + ANSI_DIM + f"  {resolved}/{total}" + ANSI_CLOSE)
    print(card_color + "╰─" + ANSI_CLOSE)


def main():
    tasks = sys.argv[1:]
    if not tasks:
        print(ANSI_RED + "You need to provide at least one task ID" + ANSI_CLOSE)
        sys.exit()

    # The taskcluster CLI must be next to the script or on the PATH
    if not (os.path.isfile(TASKCLUSTER) and os.access(TASKCLUSTER, os.X_OK)):
        sys.stderr.write(
            ANSI_RED + "The taskcluster CLI is not installed or not executable: " + TASKCLUSTER + ANSI_CLOSE + "\n"
            + "Get it from https://github.com/taskcluster/taskcluster/tree/main/clients/client-shell\n"
        )
        sys.exit(1)

    # Local time of the machine running the script, with its timezone name
    now = datetime.now().astimezone().strftime("%H:%M %Z")
    plural = "s" if len(tasks) > 1 else ""
    print()
    print(ANSI_BOLD + "Taskcluster" + ANSI_CLOSE + ANSI_DIM + f" · {now} · {len(tasks)} task{plural}" + ANSI_CLOSE)

    results = fetch_all(tasks)

    for task in tasks:
        print()
        print_card(task, results[task])

    print()


if __name__ == "__main__":
    main()
