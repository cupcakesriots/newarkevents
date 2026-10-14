NEWARK EVENT DESK — ABOUT + CATEGORY/WARD FILTERS
================================================

Upload only dashboard.py to your existing GitHub repository and REPLACE the
current dashboard.py in the repository ROOT. Do not upload it as "dashboard 2.py".

WHAT'S NEW
- About page: /about — why Newark Event Desk exists and how it works.
- Explore page: /explore — read-only upcoming APPROVED/PUBLISHED events.
- Filter by event type AND ward on /explore and the existing admin Events page.
- Newark ward choices: Central, East, North, South, West, Unconfirmed.
- Event editors can choose type and confirmed ward. Category suggestions come
  from event titles/descriptions, but editors can override them.
- Optional checkbox on Edit Event can apply a verified ward to other untagged
  events at exactly the same street address (not by ZIP code).
- Existing database records and approval statuses remain unchanged.
- No Reddit publishing changes. No other Python files need replacing.

TEST FIRST — PRIVATE MODE
Both /about and /explore require the same existing dashboard login by default.
Your Railway variables and persistent volume do not need changing.

OPTIONAL PUBLIC LAUNCH — ONLY WHEN YOU CHOOSE
After reviewing your pages, add this Railway service variable:
    PUBLIC_BROWSE_ENABLED = 1
This makes ONLY GET /about and GET /explore publicly viewable without login.
It does not expose unpublished/pending/rejected events, social leads, editing,
source settings, admin dashboard, scans, or publishing controls. Leave the
variable unset to require login on all pages. Admin still requires your password.

WARD ACCURACY
Wards do not match ZIP-code boundaries. Existing records start with 'Ward not
confirmed' until edited. A confirmed ward is never inferred from a ZIP code.
Ward maps published by City of Newark:
https://www.newarknj.gov/524/Elections

INSTALL
1. Unzip the archive.
2. In GitHub repository cupcakesriots/newarkevents replace dashboard.py only.
3. Commit and wait for Railway ACTIVE.
4. Sign into Event Desk and open Explore and About in the top navigation.
5. Edit an event and set a verified ward, optionally applying it to matching
   street addresses. Try type+ward filters.
6. Do not enable public access until you are comfortable with the approved
   event listings. No secrets belong in GitHub.

TESTING
Tested: additive migration, login restrictions, safe public opt-in, approved-
only Explore, filters, editable labels, persistent database updates and existing
app tests. Live Railway deployment still needs to be verified by you.
