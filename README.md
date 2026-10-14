# Newark Events Aggregator • Review-First Edition

**Deploying to Railway?** Start with [`DEPLOY_RAILWAY.md`](DEPLOY_RAILWAY.md), including a required persistent SQLite volume. The cloud deployment is a test dashboard, not live automation.


A local event discovery and approval dashboard for **r/newarkevents**. Calendars and (where officially authorized) Instagram, Threads, and Facebook provide event candidates. **Nothing publishes until a human approves it in the dashboard and explicitly runs the Reddit publishing command.**

**What is built:** a real local interactive dashboard, calendar collector, Meta API connector scaffolding, social-lead queue, event editor, deduplication, review decisions, original-link attribution, flyer previews, and optional Reddit publisher.

**What is not yet connected:** live Meta API accounts and Reddit developer credentials. Real-world website parsers and API permissions must be tested after deployment. No unsupported whole-network Facebook event search and no Instagram/Threads/Facebook account scraping are included.

## Quick start (Mac / Windows / Linux)

1. Install **Python 3.11+**. Extract this ZIP.
2. Open a terminal in `newark-events-aggregator/` and install dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

3. Create a `.env` file by copying `.env.example`. Leave API credentials blank for now.
4. Start the **interactive approval dashboard**:

   ```bash
   python dashboard.py
   ```

5. Open **http://127.0.0.1:8765** in your browser. The dashboard initially has no events; use **+ Add Event** or **Social Leads > Paste an event post** to try the workflow.
6. In a second terminal, pull public calendar feeds and enabled social integrations:

   ```bash
   python aggregator.py discover --days 45
   ```

7. Reload the dashboard. Open the **Needs Review** tab and use **Approve**, **Edit details**, or **Reject**. Open **Social Leads** to confirm the date, time, Newark address, description and link for posts that came from social platforms, then click **Complete event details**. The new verified event will appear in **Needs Review**.

Windows users may need `py` instead of `python`. On macOS/Linux you may need `python3`.

### How publication works

Approved events **do not post automatically**. Check a Reddit post preview (dry run):

```bash
python aggregator.py publish --limit 2
```

When you're ready to post, configure valid Reddit API credentials in `.env`, obtain the necessary Reddit API/developer permissions, and deliberately run:

```bash
python aggregator.py publish --live --limit 2
```

It will publish at most two approved, unpublished events per run and store returned Reddit links. **Only** `publish --live` can publish; discovery/dashboard buttons never send Reddit posts. The `--from-csv` option is a legacy alternative if you explicitly want to import edits from `review.csv`; otherwise dashboard/database decisions are authoritative.

A successful post contains date/time, venue and address, summary, event/ticket link, original announcement link (when different) and source. Flyers are **linked**, not copied, by default. Select the image-rights checkbox in the event editor only when the organizer or rights holder has authorized reuploading the image to Reddit.

## How social discovery works

Social search finds **leads, not complete event listings**. An Instagram image may omit a machine-readable date, and Facebook posts may mention several events. We **never guess** a date or create a publishable Reddit event from a caption alone. Leads are saved to `social_leads` with a platform, original post URL, text, optional image, optional external event URL and a `needs_details` status. Confirm the details manually in the dashboard to create a regular event.

| Platform | Discovery method | Access needed | Not supported |
|---|---|---|---|
| **Threads** | Official keyword search endpoint, terms in `social_sources.json` | Meta Threads app, access token, approved `threads_keyword_search` permission for broader public results | Private posts, unrestricted anonymous search |
| **Instagram** | Official professional-account hashtag search and recent public hashtag media | Linked Instagram Business/Creator account and Facebook Page, appropriate Meta app/token and Instagram public content/hashtag access | Personal/private accounts, unrestricted city-wide post search |
| **Facebook** | Official `/{PAGE_ID}/posts` for authorized Pages listed in `social_sources.json` | Meta Page access token and applicable Page permissions | City-wide public Facebook Events search or unauthorized private pages/groups |
| **Any social link** | Paste original public post URL in dashboard → Social Leads | None, but you must verify the details | Automatically reading private content |

### Threads

1. Create/configure a Meta app for Threads and request the necessary public keyword search permission. Without approval, Meta may limit keyword search to the authenticated account; this is **not city-wide access**.
2. Set `THREADS_ACCESS_TOKEN` in `.env` (never commit or share the file).
3. Edit `threads_queries` in `social_sources.json`, e.g. `Newark events`, `Newark NJ`, `Newark weekend`, `Newark arts`. Terms should be curated to avoid unrelated results.
4. Run `python aggregator.py discover`. Matching leads appear under **Social Leads**.

The connector uses `GET https://graph.threads.net/keyword_search` with `search_type=RECENT` and available text, link and media fields. It cannot verify flyer dates on its own.

### Instagram

1. Use an Instagram **Professional account** (Business or Creator) linked to a Facebook Page, with a Meta app authorized for the relevant Instagram Graph API, including public content access where required.
2. Set `INSTAGRAM_IG_USER_ID` and `INSTAGRAM_ACCESS_TOKEN` in `.env`.
3. Edit `instagram_hashtags` in `social_sources.json` to include relevant tags, e.g. `newarkevents`, `newarknj`, `newarkarts` and `brickcityevents`. There are Meta limits on unique hashtag searches within rolling periods; don't put hundreds of hashtags in the configuration.
4. Run discovery and review results. Public hashtag results are incomplete, and Meta may not make a post's author available.

The connector calls `ig_hashtag_search` and `/{hashtag-id}/recent_media` through the official Facebook Graph API. These requests are disabled until configured; they do **not** collect all Instagram posts mentioning Newark.

### Facebook

There is **no general supported API endpoint to search all public Facebook Events in Newark**. The connector can read posts from specific Pages you manage or for which you have the necessary permissions. In `social_sources.json` change:

```json
"facebook_pages": [
  {"name": "My Community Events Page", "page_id": "YOUR_ACTUAL_PAGE_ID", "token_env": "FACEBOOK_PAGE_ACCESS_TOKEN"}
]
```

Set `FACEBOOK_PAGE_ACCESS_TOKEN` in `.env` to a **Page** token authorized to read that Page's posts. Follow Meta's app review/permission requirements. For Pages you don't manage, use a source's RSS/calendar feed, an organizer partnership, an officially supported permission, or paste individual Facebook event links in the dashboard. Avoid unofficial cookie-based scraping; it is fragile and can violate platform restrictions.

### Helpful source additions

The existing `sources.json` supports public iCalendar feeds, RSS feeds that lead to structured event pages, and specified event-listing websites (which can break when their HTML changes). Initially configured: City of Newark calendar, NJPAC, Newark Museum of Art. Other promising authorized calendar partners: Newark Happening, Rutgers-Newark, Newark Arts, Newark Public Library, Luma and Eventbrite organizers. For full coverage, collaborations and direct organizer submissions will matter as much as search.

## Suggested review routine

- **Every morning:** run `python aggregator.py discover --days 45` manually, or schedule this command on a computer/server with persistent storage.
- **Review Social Leads:** open original sources, check whether the announcement truly relates to an event in Newark, NJ, and confirm specific date, time, venue, address and ticket page. Use **Complete event details** to move the lead into your event queue. Dismiss unrelated posts.
- **Approve / Reject:** use the event dashboard. Review potential duplicates and image-use rights before approving.
- **Publish:** preview with `python aggregator.py publish --limit 2`, and only then run the `--live` command if authorized. Keep volume reasonable for subreddit moderation.

No automatic approval logic is active, including for public calendar sources. Old `auto_approve` properties in `sources.json` are ignored.

## Dashboard data and security

- Dashboard URL by default: **http://127.0.0.1:8765**, bound to localhost only. It is a *local app*, not a hosted web service or Google Sheet.
- SQLite database at `newark_events.db`. Keep it on persistent storage and back it up.
- Set `DASHBOARD_PASSWORD` in `.env` to add a simple HTTP Basic password (username `admin`). This does **not** provide full production authentication. For multi-person/Internet hosting, use HTTPS, stronger authentication, secret management, backups and a reviewed deployment setup. **Never publicly expose the basic HTTP version over plaintext HTTP.**
- Form submissions use a local CSRF token; posted items cannot be edited through the dashboard.
- Refresh sources via the CLI; the current dashboard doesn't schedule automatic background scans and does not contain a one-click Reddit publish button. Run scheduling elsewhere only after testing.
- Avoid reuploading event flyers without ownership/permission. Even a public social photo may be copyrighted. The original social link can remain in the text post.
- Media URLs from Meta sometimes expire or refuse third-party embedding. Use the source post link when this happens.

## Files

- `dashboard.py`: **interactive local moderation dashboard** (approval, editor, lead-to-event conversion)
- `social.py`: optional Meta connectors and separate social-lead storage
- `social_sources.json`: Threads terms, Instagram hashtags and explicitly authorized Facebook Pages
- `aggregator.py`: calendars, deduplication, `discover`, optional Reddit `publish`
- `sources.json`: conventional calendar and website configurations
- `manual_events.csv`: legacy manual imports (dashboard Add Event is easier)
- `.env.example`: environment-variable template
- `review.csv`, `review.html`: optional legacy exports generated on discovery; **dashboard is now the primary approval method**
- `newark_events.db`: generated SQLite database, not included with the starter
- `tests/`: parser, duplicate, social and review tests

Test offline:

```bash
python -m unittest discover -s tests -v
```

**Status:** Implemented and verified against offline test data. No live social feed, production login or Reddit publication was exercised in the build environment. You must configure eligible access credentials and test the actual providers before calling the full pipeline operational.
