#!/usr/bin/python3

import json
import urllib.request
import sys

balrog_url = "https://aus-api.mozilla.org/api/v1/releases/Firefox-mozilla-central-nightly-latest"

target_buildid = int(sys.argv[1])

with urllib.request.urlopen(balrog_url) as blob:
    data = json.load(blob)

for platform in data['platforms']:
    if 'locales' not in data['platforms'][platform]:
        continue
    locales = data['platforms'][platform]['locales']
    for locale in locales:
        if int(locales[locale]['buildID']) < target_buildid:
            print("%s\t%s\t%s" % (platform, locale, locales[locale]['buildID']))
