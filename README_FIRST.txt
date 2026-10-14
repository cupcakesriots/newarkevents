NEWARK EVENT DESK — EVENTBRITE TROUBLESHOOTING & VENUE FIX

INSTALL (GitHub > cupcakesriots/newarkevents)
1. Unzip this package.
2. In the root of the repository, replace the EXISTING platform_sources.py with the enclosed file.
   Do not add "platform_sources 2.py"; Railway imports platform_sources.py exactly.
3. Commit the change on main and wait for Railway deployment to become ACTIVE.
4. Open https://newarkevents-production.up.railway.app/ and choose Scan for Events.
5. On View scan results, look for lines beginning "Eventbrite details:".

WHAT CHANGED
- If the official Eventbrite event API omits city/state for an event but returns a venue ID, the importer tries the official venue-by-ID endpoint using the same Railway EVENTBRITE_ACCESS_TOKEN.
- Diagnostic lines explain reasons behind "0 Newark events": date in past, start outside the 45-day window, missing Newark NJ venue/address, or duplicate entry.
- It preserves all existing moderation decisions, queues, and database fields; it does not post to Reddit.
- It retains nonqualifying specifically watched Eventbrite events as manual-review leads.

Do NOT paste API tokens or passwords into ChatGPT or GitHub. Do not modify Railway secrets for this test.

TESTS
30 offline unit tests passed. Live Eventbrite authorization is NOT verified by these tests; the next Railway scan is needed.
