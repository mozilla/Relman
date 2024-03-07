#!/usr/bin/env python3

from git import Repo
import os
import plistlib
import re
import sys

# Check that a valid version number was specified
if len(sys.argv) < 2:
    print("No version number specified")
    sys.exit(1)
elif not sys.argv[1].split(".")[0].isdigit():
    print("Invalid version specified")
    sys.exit(1)

new_version = sys.argv[1]
major_version = new_version.split(".")[0]

bitrise_file = 'bitrise.yml'
plist_files = ['firefox-ios/Client/Info.plist',
               'firefox-ios/CredentialProvider/Info.plist',
               'firefox-ios/Extensions/NotificationService/Info.plist',
               'firefox-ios/Extensions/ShareTo/Info.plist',
               'firefox-ios/WidgetKit/Info.plist',
               'focus-ios/Blockzilla/Info.plist',
               'focus-ios/ContentBlocker/Info.plist',
               'focus-ios/FocusIntentExtension/Info.plist',
               'focus-ios/OpenInFocus/Info.plist',
               'focus-ios/Widgets/Info.plist']

# Loop through the plist files and replace the version number
print("Updating plist files...")
for file in plist_files:
    with open(file, 'rb') as fp:
        plist = plistlib.load(fp)
    plist['CFBundleShortVersionString'] = new_version
    with open(file, 'wb') as fp:
        plistlib.dump(plist, fp)

print("Updating bitrise.yml...")
# Read the YAML file
with open(bitrise_file, 'r') as file:
    yaml_data = file.read()

# Define the regex patterns for the keys we want to update
release_version_pattern = r"(BITRISE_RELEASE_VERSION: )'(\d+\.\d+)'"
beta_version_pattern = r"(BITRISE_BETA_VERSION: )'(\d+\.\d+)'"
push_branch_pattern = r"(push_branch:\s+release/v)(\d+)"

# Update the BITRISE_RELEASE_VERSION
yaml_data = re.sub(release_version_pattern, r"\1'" + new_version + "'", yaml_data)

# Update the BITRISE_BETA_VERSION
yaml_data = re.sub(beta_version_pattern, r"\1'" + new_version + "'", yaml_data)

# Update the push_branch value based on the major_version
yaml_data = re.sub(push_branch_pattern, r"\g<1>" + major_version, yaml_data)

# Write the updated YAML back to the file
with open(bitrise_file, 'w') as file:
    file.write(yaml_data)

# Commit the results
print("Creating git commit...")
repo = Repo()
repo.git.add(all=True)
repo.index.commit('Bump - Set version to ' + new_version)

print("Successfully updated the version!")
