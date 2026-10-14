Newark Event Desk — Eventbrite link importer fix
October 14, 2026

WHAT CHANGED
- Replaces page scraping for Eventbrite event links with the documented Eventbrite GET /v3/events/{id}/ API.
- Extracts event ID automatically from Eventbrite links. Examples:
    Annual Holiday Shop Hop - Downtown: 1992781990578
    New Jersey African Restaurant Week 2026: 868518300617
- With a valid personal Eventbrite OAuth token, attempts to retrieve event title, date/time, venue, description, and poster.
- An event with a verified upcoming Newark location goes into Needs Review; no automatic approvals or Reddit publishing.
- If a token is missing, an event response is incomplete, or Eventbrite denies access, a Social Lead is recorded (unless a lead with that same URL already exists).
- A specifically watched event that started in the past can be kept as a Needs Details lead for manual checking of multi-day/recurring dates.
- Existing Luma image and event-source code remains in place.

TO INSTALL
1. In GitHub open your existing newarkevents repository.
2. Replace ONLY the file named platform_sources.py with the attached platform_sources.py.
   Do not upload as platform_sources 2.py. Do not replace dashboard.py.
3. Commit; wait for Railway's latest deployment to show Active.
4. Event Desk > Events > Scan for Events > View scan results.

TO ENABLE VERIFIED EVENTBRITE DETAILS WITHOUT OWNING THE EVENTS
- You need an Eventbrite account and approved API application/valid private OAuth token (but do not need to own the two events).
- Eventbrite help: https://www.eventbrite.co.uk/help/en-gb/articles/849962/generate-an-api-key/
- Eventbrite API docs: https://www.eventbrite.com/platform/new/api
- Eventbrite account settings > Developer Links > API Keys > Create API Key (if available).
- On Railway > newarkevents service > Variables add:
    EVENTBRITE_ACCESS_TOKEN = <your PRIVATE OAuth token, NOT an API key/client ID>
- Apply changes. Do not put token in GitHub or screenshots.
- Scan again. API permission/data availability may vary; 401/403/404 create a lead instead of publishing a guessed event.

IMPORTANT
- No Eventbrite key? The saved links can still appear in Social Leads for manual verification. They will not silently disappear.
- Eventbrite has retired its old public location event search API. This update only fetches details for event IDs already found via your existing Brave preview or manually-added Sources.
- Eventbrite does not necessarily grant all API data to every token. Live authenticated calls must be tested after setup.
- Your previously approved Luma events, images, existing event statuses and Railway DB do not change.
- Reposting the original flyer to Reddit still requires appropriate permission; the image URL alone isn't permission.

LOCAL TESTS
27 offline tests passed. Live Eventbrite responses were not tested due to lack of Eventbrite OAuth credentials.
