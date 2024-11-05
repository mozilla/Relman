#!/usr/bin/env python3
import argparse
import json
import requests
import pyperclip

# get the data from the bugzilla api
def fetch_bugzilla_data(api_url, api_key=None):
# add the api key to the end of the url
    if api_key:
        api_url += f"&api_key={api_key}"
    try:
        response = requests.get(api_url)
        response.raise_for_status()  # raise an error for bad responses
        data = response.json() 
        return data
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")
        return None


if __name__ == "__main__":
    # parse command line arguments, in case of an issue this was copied from the contributor script
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--api-key",
        "--apikey",
        required=True,
        help="Bugzilla API-Key",
    )

    args = parser.parse_args()
    api_key = args.api_key

    # list of dictionaries of urls
    api_urls = [
        {
            "name": "1 function-open-blockers",
            "url": "https://bugzilla.mozilla.org/rest/bug?bug_file_loc_type=allwordssubstr&bug_id_type=anyexact&emailassigned_to1=1&emailassigned_to2=1&emailqa_contact2=1&emailreporter2=1&emailtype1=exact&emailtype2=exact&emailtype3=substring&f1=cf_status_firefox_beta&f2=cf_tracking_firefox_beta&j_top=AND&keywords_type=allwords&longdesc_type=allwordssubstr&o1=equals&o2=equals&resolution=---&short_desc_type=allwordssubstr&status_whiteboard_type=allwordssubstr&submit-button=Create%20Data%20Set&v1=affected&v2=blocking&votes_type=greaterthaneq&count_only=1"
        },
        {
            "name":"2 function-unfixed-new-regressions",
            "url": "https://bugzilla.mozilla.org/rest/bug?bug_file_loc_type=allwordssubstr&bug_id_type=anyexact&chfield=regresses&emailtype1=substring&emailtype2=substring&emailtype3=substring&f1=cf_status_firefox_beta&f2=cf_status_firefox_release&f3=reporter&j_top=AND&keywords=regression%2C&keywords_type=allwords&longdesc_type=allwordssubstr&o1=anyexact&o2=anyexact&o3=notsubstring&resolution=---&resolution=FIXED&short_desc_type=allwordssubstr&status_whiteboard_type=allwordssubstr&submit-button=Create%20Data%20Set&v1=affected%2C%20fix-optional%2C%20wontfix&v2=disabled%2C%20unaffected%2C%20---&v3=intermittent-bug-filer%40mozilla.bugs&votes_type=greaterthaneq&count_only=1"
        },
        {
            "name":"3 function-open-carryover-regressions",
            "url": "https://bugzilla.mozilla.org/rest/bug?bug_file_loc_type=allwordssubstr&bug_id_type=anyexact&emailtype1=substring&emailtype2=substring&emailtype3=substring&f1=cf_status_firefox_beta&f2=OP&f3=cf_status_firefox_release&f4=cf_status_firefox_release&f5=cf_status_firefox_release&f6=CP&f7=reporter&j2=OR&j_top=AND&keywords=regression%2C&keywords_type=allwords&longdesc_type=allwordssubstr&o1=equals&o3=equals&o4=equals&o5=equals&o7=notsubstring&short_desc_type=allwordssubstr&status_whiteboard_type=allwordssubstr&submit-button=Create%20Data%20Set&v1=affected&v3=affected&v4=fix-optional&v5=wontfix&v7=intermittent-bug-filer%40mozilla.bugs&votes_type=greaterthaneq&count_only=1"
        },
        {
            "name":"4 function-severe-carryover-regressions",
            "url": "https://bugzilla.mozilla.org/rest/bug?bug_file_loc_type=allwordssubstr&bug_id_type=anyexact&bug_severity=blocker&bug_severity=S1&bug_severity=critical&bug_severity=S2&bug_severity=major&classification=Client%20Software&classification=Developer%20Infrastructure&classification=Components&classification=Server%20Software&classification=Other&emailtype1=substring&emailtype2=substring&emailtype3=substring&f1=cf_status_firefox_beta&f2=OP&f3=cf_status_firefox_release&f4=cf_status_firefox_release&f5=cf_status_firefox_release&f6=CP&j2=OR&j_top=AND&keywords=regression%2C&keywords_type=allwords&longdesc_type=allwordssubstr&o1=equals&o3=equals&o4=equals&o5=equals&short_desc_type=allwordssubstr&status_whiteboard_type=allwordssubstr&submit-button=Create%20Data%20Set&v1=affected&v3=affected&v4=fix-optional&v5=wontfix&votes_type=greaterthaneq&count_only=1",
        },
        {
            "name":"5 stability-new-top-crashers",
            "url": "https://bugzilla.mozilla.org/rest/bug?bug_file_loc_type=allwordssubstr&bug_id_type=anyexact&classification=Client%20Software&classification=Developer%20Infrastructure&classification=Components&classification=Server%20Software&classification=Other&emailtype1=substring&emailtype2=substring&emailtype3=substring&f1=cf_status_firefox_beta&f2=cf_status_firefox_release&j_top=AND&keywords=topcrash&keywords_type=anywords&longdesc_type=allwordssubstr&o1=equals&o2=anywordssubstr&short_desc_type=allwordssubstr&status_whiteboard_type=allwordssubstr&submit-button=Create%20Data%20Set&v1=affected&v2=unaffected%2C%20---&votes_type=greaterthaneq&count_only=1"
        },
        {
            "name":"6 stability-tracked-crashers",
            "url": "https://bugzilla.mozilla.org/rest/bug?bug_file_loc_type=allwordssubstr&bug_id_type=anyexact&classification=Client%20Software&classification=Developer%20Infrastructure&classification=Components&classification=Server%20Software&classification=Other&emailassigned_to1=1&emailassigned_to2=1&emailqa_contact2=1&emailreporter2=1&emailtype1=exact&emailtype2=exact&emailtype3=substring&f1=cf_status_firefox_beta&f2=cf_tracking_firefox_beta&j_top=AND&keywords=crash%2C%20topcrash&keywords_type=anywords&longdesc_type=allwordssubstr&o1=equals&o2=anyexact&resolution=---&short_desc_type=allwordssubstr&status_whiteboard_type=allwordssubstr&submit-button=Create%20Data%20Set&v1=affected&v2=%2B%2Cblocking&votes_type=greaterthaneq&count_only=1"
        },
        {
            "name":"7 Perf-bugs-affecting-beta",
            "url": "https://bugzilla.mozilla.org/rest/bug?bug_file_loc_type=allwordssubstr&bug_id_type=anyexact&classification=Client%20Software&classification=Developer%20Infrastructure&classification=Components&classification=Server%20Software&classification=Other&emailtype1=substring&emailtype2=substring&emailtype3=substring&f1=cf_status_firefox_beta&f10=keywords&f11=product&f12=resolution&f2=OP&f3=cf_status_firefox_release&f4=cf_status_firefox_release&f5=cf_status_firefox_release&f6=CP&f7=flagtypes.name&f8=cf_tracking_firefox_beta&f9=product&j2=OR&j_top=AND&keywords=regression%2C%20perf%2C%20&keywords_type=allwords&longdesc_type=allwordssubstr&o1=equals&o10=notsubstring&o11=notequals&o12=nowordssubstr&o3=equals&o4=equals&o5=equals&o7=notsubstring&o8=notequals&o9=notequals&resolution=---&short_desc_type=allwordssubstr&status_whiteboard_type=allwordssubstr&submit-button=Create%20Data%20Set&v1=affected&v10=stalled&v11=Geckoview&v12=DUPLICATE%2CWONTFIX%2CINVALID&v3=unaffected&v4=%3F&v5=---&v7=needinfo&v8=-&v9=Testing&votes_type=greaterthaneq&count_only=1"
        },
        {
            "name":"8 sec-all-high-crit-affecting-beta",
            "url": "https://bugzilla.mozilla.org/rest/bug?bug_file_loc_type=allwordssubstr&bug_id_type=anyexact&classification=Client%20Software&classification=Developer%20Infrastructure&classification=Components&classification=Server%20Software&classification=Other&emailtype1=substring&emailtype2=substring&emailtype3=substring&f1=cf_status_firefox_beta&f2=keywords&j_top=AND&keywords=sec-high%20sec-critical&keywords_type=anywords&longdesc_type=allwordssubstr&o1=anywords&o2=notsubstring&short_desc_type=allwordssubstr&status_whiteboard_type=allwordssubstr&submit-button=Create%20Data%20Set&v1=affected%20fix-optional&v2=stalled&votes_type=greaterthaneq&https://bugzilla.mozilla.org/buglist.cgi?cmdtype=dorem&namedcmd=-All-%20%2F%20-All-%20%2F%208%20sec-all-high-crit-affecting-beta&series_id=7756&remaction=runseries&list_id=17288310&count_only=1"
        }
    ]

    bug_counts = []
    for definition in api_urls:
        print(f"Fetching {definition['name']}...") # print the progress
        data = fetch_bugzilla_data(definition["url"], api_key)
        if data is not None:
            bug_count = data.get('bug_count', 0)  
            bug_counts.append(str(bug_count))  

    # print the bug counts, separated by commas and copy to clipboard
    output = ",".join(bug_counts)
    pyperclip.copy(output)
    print(output)
