# Newark Event Desk: Eventbrite preview (no Brave storage rights required)

This mini-update adds an Eventbrite Preview tab to Newark Event Desk. It makes **one transient Brave Web Search API request** on demand and displays matching Eventbrite links only in the immediate authenticated response. It never adds Brave results to the SQL database, Social Leads, background job cache, export, log, or filesystem.

## Install (GitHub)

1. Unzip this package on your Mac.
2. Open your GitHub repository `cupcakesriots/newarkevents` and go to `dashboard.py`.
3. **Replace the existing `dashboard.py` with the included file, keeping the exact name `dashboard.py`.** If GitHub renames the upload `dashboard 2.py`, **do not keep the duplicate**. Open the original `dashboard.py`, click Edit, replace all content with the updated file's content, and commit.
4. Upload the new `eventbrite_preview.py` at the **repository root**, with that exact name.
5. Do **not** change `aggregator.py`, `platform_sources.py`, `social.py`, or any other files. Keep your existing Luma image fixes and events as they are.
6. Wait for Railway's deployment to become `ACTIVE`.

## Railway variable

Set `BRAVE_SEARCH_API_KEY` to the *newly rotated* Brave Search API key in Railway → newarkevents → Variables. Do not put the key in GitHub or screenshots.

**Do not set** `BRAVE_STORAGE_RIGHTS_CONFIRMED=1` on the standard plan. The old scheduled collector will continue to report the persistent Eventbrite discovery feed as skipped; this is intentional. The new button works separately and never saves results.

## Test

1. Open the password-protected dashboard at https://newarkevents-production.up.railway.app.
2. Choose **Eventbrite Preview** on the top bar.
3. Select a category and click **Search Newark Eventbrite**.
4. Review the returned links. They are unverified, and can be expired or from nearby cities.
5. For something genuine, open its Eventbrite page, verify the *future* date and physical Newark venue, then use **Add Event** to enter verified details manually (or use a single-event source if you already have authorization to import it).

The temporary search display is not a background event feed and doesn't persist results. Every button click consumes one search request.

## Licensing and privacy

Brave's Sep 2026 Search API Terms allow transient storage required to operate an application, but exclude permanent storage or caching of API results without special rights. See https://api-dashboard.search.brave.com/documentation/resources/terms-of-service . Verify your account terms before expanding this workflow.

## Troubleshooting

- **Missing API key**: Set `BRAVE_SEARCH_API_KEY` in Railway. Variables must be deployed.
- **401 / 403**: Rotate/verify API key, ensure active Search plan, and deploy changes.
- **Empty list**: The search index isn't a comprehensive events listing; try another category.
- **Old events**: Expected for search indexes. Verify the future event date before manual entry.
