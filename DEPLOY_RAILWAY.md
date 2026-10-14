# Deploy a private Newark Events test dashboard on Railway

This package supports the **existing Python dashboard**. It does not yet automate source discovery or post to Reddit from the browser. Everything remains review-first. Meta and Reddit API credentials are **not** required for your first deployment.

## Step 1 — Put the code on GitHub

1. Unzip the downloaded package.
2. Create a **private** repository at https://github.com/new, such as `newark-events-aggregator`.
3. Upload the *files inside* `newark-events-aggregator-deployable/` so `dashboard.py`, `aggregator.py`, and `requirements.txt` are at the repository root. Do **not** upload only the ZIP.
4. Keep `.env` and any SQLite `.db` files out of GitHub; `.gitignore` excludes them.

## Step 2 — Create a Railway web service

1. Open https://railway.com/new and choose **Deploy from GitHub repo**.
2. Choose the private repository and allow Railway to access it.
3. For the service, set the **Custom Start Command** in Settings > Deploy:

   `python dashboard.py`

4. Under **Variables** for the Railway service set:

   | Key | Value |
   | --- | --- |
   | `DASHBOARD_HOST` | `0.0.0.0` |
   | `DASHBOARD_PASSWORD` | a **strong, unique password** you choose (never commit it) |
   | `NEWARK_DB` | `/data/newark_events.db` |

   Railway supplies the `PORT` variable. The dashboard now reads `PORT` automatically. No API keys are needed to test review/edit/approve.

5. In your **web service** add a persistent **Volume** with mount path `/data` before starting to enter events. This is essential for retaining the SQLite queue across restarts or deployments. A volume is not a database server and may entail additional charges/limits.
6. Deploy/redeploy the service, then go to **Settings > Networking > Public Networking > Generate Domain**. This creates an HTTPS URL.
7. Open that URL. The browser's Basic Auth prompt uses username **`admin`**, password = your `DASHBOARD_PASSWORD`. Do not disable this protection for internet hosting.

## Step 3 — Test the approval process

- Add an example *future Newark, NJ event* from the `+ Add Event` page with a valid event URL.
- Confirm it appears under **Needs Review**.
- Try **Edit**, **Approve**, and **Reject**.
- Paste an Instagram, Facebook, or Threads URL under **Social Leads** and check that it stays a lead until you verify a date and venue.
- Redeploy the application. Confirm the event queue remains in place. If data disappears, the `/data` volume or `NEWARK_DB` setting needs correction.
- Do **not** set Reddit credentials yet. Approving an event never publishes it automatically.

## Next phase: automate discovery and publishing

The browser dashboard does *not* run the collector on a schedule. Initially it is suitable for manual testing of the review queue. Once the dashboard is stable:

1. Decide which public calendar feeds and authorized social APIs provide reliable results.
2. Add an authenticated background discovery mechanism. Because the current database is SQLite on one Railway service's volume, separate scheduled services cannot directly share it. For background workers, switch to a shared PostgreSQL database, or run discovery within the same service process with appropriate protections.
3. Add a deliberate **Publish approved events** action. The current app's Reddit publisher runs only from the CLI with `python aggregator.py publish --live` after Reddit API approval/credentials are configured. It is *not* a dashboard button.
4. Replace staging Basic Auth with stronger app authentication before inviting additional editors or exposing sensitive information.

## Troubleshooting

- **Railway app not reachable:** check `DASHBOARD_HOST=0.0.0.0`, custom start command, service deployment logs, and generated domain.
- **500/error unable to open database file:** ensure the Volume is mounted at `/data` and `NEWARK_DB=/data/newark_events.db`.
- **401 browser password request:** username `admin`; password is the secret in Railway Variables.
- **Empty dashboard:** expected at first. `discover` is not scheduled yet; add an event to test the workflow.
- **Port error:** don't hardcode the service port; Railway injects `PORT` which the updated dashboard reads.

Pricing and storage limits change. See https://docs.railway.com/pricing/plans and https://docs.railway.com/volumes/reference .
