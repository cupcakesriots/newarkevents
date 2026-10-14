NEWARK EVENT DESK — EVENTBRITE PREVIEW DISPLAY FIX
October 14, 2026

This update fixes the Eventbrite Preview screen without changing your Luma
integrations, event database, approval decisions, or Reddit settings.

CHANGES
1. Displays safe bold/italic formatting in Brave search snippets instead of
   literal <strong>, <em>, and similar HTML tags.
2. Displays a search-result picture above the card when Brave Web Search
   returns a usable, non-logo thumbnail. Never scrapes Eventbrite pages.
3. Hides results only when an expired event date is clearly indicated in the
   indexed result title (or the same date with a year in its snippet).
   Unknown or ambiguous dates remain labeled unverified.
4. Results and images are temporary; nothing gets saved to Social Leads,
   the event DB, a background job, or Reddit.

GITHUB UPDATE — ONLY TWO FILES
1. Unzip this download.
2. In your existing cupcakesriots/newarkevents GitHub repository, replace
   the existing root dashboard.py with this version.
3. Replace the existing root eventbrite_preview.py with this version.
4. Commit changes and let Railway redeploy.
5. If GitHub uploads a file as 'dashboard 2.py' or 'eventbrite_preview 2.py',
   DO NOT rely on the duplicate. Open the ORIGINAL file, click Edit, replace
   its full contents with the new version, and commit.

DO NOT replace platform_sources.py, aggregator.py, social.py or your database.
DO NOT paste your BRAVE_SEARCH_API_KEY into GitHub.

AFTER RAILWAY REDEPLOYS
1. Open https://newarkevents-production.up.railway.app/eventbrite
2. Click Search Newark Eventbrite.
3. Check whether image thumbnails appear, and whether the snippets display
   bold rather than <strong> tags.
4. Some listings will still have no image or reliable future date; the
   thumbnail is optional in the Brave search response. Only show image
   previews transiently. Click the original listing to confirm details.

The current Brave Search API plan's storage restrictions remain in effect;
this preview-only feature does not require storage rights. Every search click
uses one Brave API request.

LOCAL TEST STATUS
30 offline checks passed: 21 existing project tests + 9 preview tests.
The user-facing Brave API call cannot be tested here without your private key.

OFFICIAL API REFERENCE
https://api-dashboard.search.brave.com/api-reference/web/search/post
