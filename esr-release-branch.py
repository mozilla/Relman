# esr-release-branch.py
#
# This script automates the creation of a dot-release branch for Firefox ESR releases.
# It checks out the given ESR branch (e.g., ESR140), finds the commit from the last shipped
# release tag (e.g., FIREFOX_140_1_0esr_RELEASE), creates a new release branch from that
# commit (e.g., FIREFOX_ESR_140_1_X_RELBRANCH), bumps the version number (e.g., to 140.1.1),
# updates version files, and commits the changes.
#
# Usage:
#   python esr-release-branch.py esr140

import argparse
import subprocess
import re
from pathlib import Path

def run(cmd):
    # Run a shell command and return its output, raising on failure
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr)
        raise Exception(f"Command failed: {cmd}")
    return result.stdout.strip()

def get_current_version():
    # Read the current version from version.txt
    version = Path("browser/config/version.txt").read_text().strip()
    return version

def bump_version(prev):
    # Bump the given version string by adding or incrementing the patch number
    parts = prev.split(".")
    if len(parts) == 2:
        # e.g., 140.1 -> 140.1.1
        return f"{parts[0]}.{parts[1]}.1"
    elif len(parts) == 3:
        # e.g., 140.1.1 -> 140.1.2
        return f"{parts[0]}.{parts[1]}.{int(parts[2])+1}"
    else:
        raise ValueError(f"Unexpected version format: {prev}")

def update_version_files(new_version, esr=True):
    # Update version.txt, version_display.txt, and milestone.txt with the new version
    display_version = f"{new_version}esr" if esr else new_version

    Path("browser/config/version.txt").write_text(new_version + "\n")
    Path("browser/config/version_display.txt").write_text(display_version + "\n")

    milestone_path = Path("config/milestone.txt")
    lines = milestone_path.read_text().splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\d+\.\d+(\.\d+)?$", line):
            lines[i] = new_version
            break
    milestone_path.write_text("\n".join(lines) + "\n")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("branch", help="ESR branch, e.g. ESR140")
    args = parser.parse_args()

    branch = args.branch
    assert re.match(r"^(esr|ESR)\d+$", branch), f"Unexpected branch format: {branch}"
    git_branch = branch.lower()

    major_version = re.findall(r"\d+", branch)[0]

    # Try to checkout the local branch; if not found, try the remote one
    try:
        run(f"git checkout {git_branch}")
    except Exception:
        print(f"Local branch {git_branch} not found, trying origin/{git_branch}...")
        try:
            run(f"git fetch origin {git_branch}:{git_branch}")
            run(f"git checkout {git_branch}")
        except Exception:
            print(f"❌ Branch {git_branch} not found on origin. Exiting.")
            return

    run("git pull")

    # Get current version and derive previous version (to locate release tag)
    current_version = get_current_version()  # e.g., 140.2
    print(f"🔍 Current version: {current_version}")
    parts = current_version.split(".")
    if len(parts) == 2 and parts[1] == '0':
        base_version = current_version  # e.g., 140.0
    elif len(parts) == 2:
        prev_minor = int(parts[1]) - 1
        base_version = f"{parts[0]}.{prev_minor}.0"
    elif len(parts) == 3:
        if parts[1] == '0' and parts[2] == '0':
            base_version = f"{parts[0]}.0"  # e.g., from 140.0.0 → 140.0
        else:
            prev_minor = int(parts[1]) - 1
            base_version = f"{parts[0]}.{prev_minor}.0"
    else:
        raise ValueError(f"Unexpected version format: {current_version}")
    print(f"⬅️  Previous version: {base_version}")

    # Find the commit used to build the previous dot release
    tag = f"FIREFOX_{base_version.replace('.', '_')}esr_RELEASE"
    run(f"git fetch origin tag {tag}")
    commit = run(f"git rev-list -n 1 {tag}")

    # Show the commit message for context
    commit_msg = run(f"git log -1 --pretty=%B {commit}")
    print(f"📌 Branch will be based on commit {commit}: {commit_msg.strip()}")

    # Create new release branch from that commit
    base_parts = base_version.split(".")
    relbranch = f"FIREFOX_ESR_{base_parts[0]}_{base_parts[1]}_X_RELBRANCH"
    try:
        run(f"git checkout -b {relbranch} {commit}")
        print(f"✅ Created branch {relbranch} from commit {commit}")
    except Exception:
        print(f"❌ Branch {relbranch} already exists. Please delete it or choose another name.")
        return

    # Bump the version and update files
    new_version = bump_version(base_version)
    print(f"⬆️  New version will be: {new_version}")  # e.g., 140.1.1
    update_version_files(new_version)
    # Commit the changes
    run(f"git commit -a -m \"No bug - Bump version to {new_version} a=me\"")
    print(f"📝 Version bump committed: {new_version}")
    
    

if __name__ == "__main__":
    main()
