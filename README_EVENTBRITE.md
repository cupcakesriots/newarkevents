# Eventbrite Newark public discovery (no organizer connection)

The retired Eventbrite `/v3/events/search/` API cannot search events by city. Eventbrite's terms prohibit automated crawling/scraping its webpages. This connector instead uses **Brave Search API's own web index** to identify possible public Eventbrite event URLs associated with Newark. It never requests Eventbrite webpages. Its results are **unverified leads**, not approved events.

## Installation

Copy only `aggregator.py` (replace your existing file) and `eventbrite_newark_discovery.py` (new file) to the **root** of your existing `newarkevents` GitHub repository. Keep exactly those filenames. Railway will redeploy automatically. This update does not change the existing Luma importer, event data, or review decisions.

## Configure (Railway > newarkevents > Variables)

1. Obtain a Brave Search API key at https://brave.com/search/api/.
2. **Confirm your API subscription permits saving search results/URLs in a persistent database.** Standard Brave API terms generally prohibit storing API search results without appropriate storage rights. Do not enable this connector without the rights you need.
3. Add `BRAVE_SEARCH_API_KEY` = your secret API key.
4. Only **if your account/contract expressly grants storage rights**, add `BRAVE_STORAGE_RIGHTS_CONFIRMED` = `1`.
5. Apply changes. Open Newark Event Desk and run **Scan for Events**.
6. Look for `[discovery] Eventbrite Newark web discovery: ...` and inspect **Social Leads**.

If no key is set (or storage rights are not confirmed), the scan displays a **skipped** message. Nothing breaks.

## Important limitations

- This uses a search index, not an official Eventbrite city feed. It cannot guarantee all Newark events or current availability.
- Search snippets can be outdated or mention other Newarks. Verify each event's actual date and street address before promoting it.
- Cover images are **not** collected or reused. Use authorized images from organizers when needed.
- No Reddit posts are made from this connector.
- Three search queries per scan. Brave Search API may charge for usage. Do not scan excessively.
- Source metadata is retained in your SQLite lead records ONLY if your provider agreement allows that storage.

Alternative for broader official access: ask Eventbrite about its event distribution partner program: https://www.eventbrite.com/platform/.
