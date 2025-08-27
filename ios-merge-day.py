"""
ios-merge-day.py

Automates Firefox iOS version management and release branching.

WHAT THIS SCRIPT DOES:
-----------------------
1. Ensures you have a clean working directory and latest main branch.
2. Reads the current version from version.txt (e.g. 142.3).
3. Creates a new release branch named release/v<version> (e.g. release/v142.3).
4. Bumps the version in version.txt on main using a rolling scheme:
     - If the version is X.0 → bump to X.1
     - If the version is X.1 → bump to X.2
     - If the version is X.2 → bump to X.3
     - If the version is X.3 → bump to (X+1).0

   Examples:
     version.txt = 142.1 → bumps to 142.2
     version.txt = 142.3 → bumps to 143.0
     version.txt = 143.0 → bumps to 143.1

5. Commits the version bump to main.
6. Prompts you to push both the release branch and updated main.

HOW TO RUN:
-----------
From the root of your firefox-ios Git clone, run:

    python path/to/ios-merge-day.py

"""

import os
import subprocess

# Constants for file and branch naming
VERSION_FILE = "version.txt"
RELEASE_BRANCH_PREFIX = "release/"

def run_git_command(*args, capture_output=False):
    """
    Run a git command.
    If capture_output=True, returns the stdout.
    """
    cmd = ["git"] + list(args)
    try:
        if capture_output:
            result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, text=True)
            return result.stdout.strip()
        else:
            subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Git command failed: {' '.join(cmd)}")
        exit(1)

def ensure_clean_working_tree():
    """
    Check that the working tree is clean (no uncommitted changes).
    Exits if anything is dirty.
    """
    print("🧼 Checking for uncommitted changes...")
    status = run_git_command("status", "--porcelain", capture_output=True)
    if status:
        print("⚠️  Your working directory has uncommitted changes.")
        print("Please commit or stash them before running this script.")
        exit(1)
    print("✅ Working directory is clean.")

def checkout_and_update_main():
    """
    Switch to the 'main' branch and pull the latest changes from origin.
    """
    print("📦 Checking out 'main' and pulling latest changes...")
    run_git_command("checkout", "main")
    run_git_command("pull")
    print("✅ 'main' is now up to date.")

def read_current_version():
    """
    Read the current version number from version.txt.
    """
    print("📖 Reading current version from version.txt...")
    with open(VERSION_FILE, "r") as f:
        version = f.readline().strip()
    print(f"✅ Current version found: {version}")
    return version

def calculate_next_version(current_version):
    """
    Given the current version in format 'XXX.Y', return the next version.
    Increments Y up to 3, then rolls over to (XXX+1).0.
    """
    print("🔢 Calculating next version...")
    major, minor = map(int, current_version.split("."))
    if minor < 3:
        next_version = f"{major}.{minor + 1}"
    else:
        next_version = f"{major + 1}.0"
    print(f"✅ Next version will be: {next_version}")
    return next_version

def create_release_branch(current_version):
    """
    Create a new release branch from main named release/vXXX.Y.
    """
    branch_name = f"{RELEASE_BRANCH_PREFIX}v{current_version}"
    print(f"🌿 Creating branch: {branch_name} from 'main'...")
    run_git_command("checkout", "-b", branch_name)
    print(f"✅ Branch {branch_name} created successfully.")
    return branch_name

def bump_version(next_version):
    """
    Update version.txt to the next version and commit the change on main.
    """
    print(f"✏️ Bumping version in 'main' to {next_version}...")
    run_git_command("checkout", "main")
    with open(VERSION_FILE, "w") as f:
        f.write(f"{next_version}\n")
    run_git_command("add", VERSION_FILE)
    run_git_command("commit", "-m", f"Bump version to {next_version}")
    print(f"✅ Commit created: Bump version to {next_version}")

def prompt_and_push(branch_name):
    """
    Ask the user whether to push the new release branch and updated main.
    If yes, perform the push. If not, print manual commands.
    """
    print(f"\nWould you like to push the new release branch and updated 'main' to origin? (y/N): ", end="")
    choice = input().strip().lower()

    if choice == "y":
        print(f"⬆️  Pushing release branch '{branch_name}'...")
        run_git_command("push", "--set-upstream", "origin", branch_name)
        print(f"⬆️  Pushing updated 'main'...")
        run_git_command("push", "origin", "main")
        print("✅ Both branches pushed.")
    else:
        print("\n📌 Push skipped. To do it manually, run:")
        print(f"    git push --set-upstream origin {branch_name}")
        print(f"    git push origin main")

def main():
    """
    Main script workflow:
    - Check repo cleanliness
    - Update main
    - Read and bump version
    - Create release branch
    - Optionally push changes
    """
    ensure_clean_working_tree()
    checkout_and_update_main()
    current_version = read_current_version()
    next_version = calculate_next_version(current_version)
    release_branch = create_release_branch(current_version)
    bump_version(next_version)
    prompt_and_push(release_branch)
    print("\n🎉 All done!")

if __name__ == "__main__":
    main()
