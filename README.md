# Anne Arundel Traffic & Emergency Alerts | free starter bot

**Status:** Tested locally with sample data, not connected to your Facebook Page. Traffic feeds and Meta access must be configured. Starts in **preview-only** mode and will never publish by default.

## What it does

- Reads Maryland CHART public JSON traffic incidents and active road closures, when configured.
- Filters for **Anne Arundel** county by the *county field*, not a dangerous approximation based on route numbers.
- Identifies reported roadway **vehicle fires** (not building/house fires).
- Reads severe/warning-level **National Weather Service** alerts using the verified Anne Arundel County zone `MDC003`.
- Creates plain-language posts with direct attribution, no made-up delays or injuries.
- Records IDs in `state.json` to prevent repeats; first run establishes a baseline without flooding followers with existing events.
- Limits publishing to 3 new posts per run. No automatic "all clear" post unless a verified clearance feed is added in the future.

## 1. Create your Facebook Page

Visit https://www.facebook.com/pages/create and create a **Community** Page named, for example, **Anne Arundel Traffic & Emergency Alerts**. Mention in the About section that it's independent and uses official sources. A draft:

> Independent community traffic and public-safety updates for Anne Arundel County, Maryland. Traffic incidents, road closures, disabled vehicles, and official weather warnings, as available from MDOT CHART and NWS. Not affiliated with any government agency. For emergencies, call 911. Always follow official instructions.

## 2. Select the two CHART JSON feeds

Visit https://chart.maryland.gov/DataFeeds/GetDataFeeds . In the "Traffic Incidents and Events" section click **Open Json** and copy the resulting feed URL. Repeat for **Active Road Closures**. These feed URL strings belong in `CHART_INCIDENTS_URL` and `CHART_CLOSURES_URL`.

The official site documents the feeds, but direct feed link targets can change. This starter intentionally does not invent unverified URLs. If clicking JSON is difficult, use your browser's "Copy link address" command or send the link for a quick check. If the URL is blocked from your hosting region, that source will require an alternative approved access method.

## 3. Run in preview mode on your Mac

Python 3.10+ recommended. On your Mac in Terminal:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` with the JSON URL values. In Terminal, load it and run:

```bash
set -a; source .env; set +a
python bot.py
```

On the **first run**, the bot records a baseline, so it doesn't post old incidents. On later runs, new events are printed as previews while `DRY_RUN=true`. No credentials needed for preview.

If you need to test it from scratch again, reset `state.json` to `{ "initialized": false, "seen": {} }`.

## 4. Connect Facebook to publish (later)

Create a Meta Developer app at https://developers.facebook.com/apps/ . Under the supported Pages publishing flow, obtain a **Page access token** for a user with CREATE_CONTENT on the Page and the permissions `pages_manage_posts`, `pages_read_engagement`, and `pages_show_list`. Meta may require extra permission/access review depending on the app and its operating mode. The token cannot be substituted with your Facebook password. Tokens can expire or be revoked.

Use Meta's official instructions: https://developers.facebook.com/docs/graph-api/reference/page/feed/

Fill in `FB_PAGE_ID` and `FB_PAGE_ACCESS_TOKEN`; set `DRY_RUN=false` **only after** testing the preview and permissions. Do not share Page tokens in chat or commit them to GitHub.

## 5. Optional free scheduling with GitHub Actions

1. Create a GitHub repository and upload these files (including `.github/workflows/alerts.yml`). A public repo allows Actions to run without paying for minutes on normal GitHub-hosted runners, subject to GitHub's usage limits and policy changes.
2. Open **Settings → Secrets and variables → Actions**.
3. Add **Repository variables**: `CHART_INCIDENTS_URL`, `CHART_CLOSURES_URL`, `FB_PAGE_ID`, `DRY_RUN` (start `true`), and `NWS_USER_AGENT` (include a real contact email).
4. Add **Repository secret**: `FB_PAGE_ACCESS_TOKEN`. Never add it as a variable or commit it to a file.
5. In **Actions**, run **County alerts check** manually first (workflow_dispatch). It will create `state.json` baseline. Future runs only preview or publish new incidents.
6. When the preview is reliable, change the `DRY_RUN` variable to `false` and run the workflow again.

The included workflow checks every 15 minutes. GitHub scheduled jobs may be delayed/skipped, and public repo schedules can be disabled after 60 days without activity. **This is not an emergency dispatch substitute.** For more frequent or reliable delivery, use a dedicated host later.

## Scope limitations

- No house/structure fires until an authorized reliable dispatch feed is available.
- Neither traffic nor NWS is guaranteed to include all local events.
- CHART publication/redistribution terms should be confirmed with MDOT before running a large public automated account, especially if monetized.
- The code uses data templates and no paid AI, ads service, or web scraping.
- It deliberately does **not** invent traffic delays, injury counts, or closure confirmations.
- Facebook app permissions and Meta's app review may be the main non-financial obstacle.
- This is a starter build that was tested against fixtures, not a completed production deployment.
