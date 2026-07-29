# Email → Notion Agent Integration

Forward any email to a dedicated address. A Claude agent triages it — pulls out
a title, summary, category, tags, action items, priority, and any deadline —
and creates a page in your Notion database.

## Architecture

```
you forward → SendGrid Inbound Parse → POST /inbound-email → Claude (tool use) → Notion page
```

- `server/routes/inboundEmail.js` — Express route, multer parses SendGrid's multipart form.
- `server/controllers/inboundEmail.js` — calls the Anthropic Messages API with a
  single `save_to_notion` tool, then writes the returned fields to Notion.

## One-time setup

### 1. Notion

1. Create a database (Table view is fine). Add these properties:
   - **Name** (Title) — already exists.
   - **From** (Text)
   - **Category** (Select)
   - **Priority** (Select)
   - **Tags** (Multi-select)
   - **Due Date** (Date)
2. Create an integration: https://www.notion.so/profile/integrations →
   "New internal integration". Copy the token.
3. Open the database → `···` → **Connections** → add your integration.
4. Copy the database ID from its URL (the 32-char hex block).

### 2. Anthropic

Create an API key at https://console.anthropic.com/. Any Claude model with tool
use works; `claude-sonnet-4-5` is the default.

### 3. SendGrid Inbound Parse

1. Add an MX record on a domain you control pointing at `mx.sendgrid.net`.
2. SendGrid dashboard → **Settings → Inbound Parse → Add Host & URL**.
3. Host: the subdomain (e.g. `inbox.yourdomain.com`). URL: your deployed
   `https://.../inbound-email`. Leave "POST the raw, full MIME message"
   **unchecked** — the controller reads the parsed fields.
4. Now anything sent to `anything@inbox.yourdomain.com` reaches your endpoint.

Prefer no DNS? Use **Postmark** instead: their inbound stream gives you a
ready-made `<hash>@inbound.postmarkapp.com` address. The payload shape is
different (JSON, keys like `From`, `Subject`, `TextBody`) — tweak
`parseEmailFields()` accordingly.

### 4. Environment variables

Copy `.env.example` to `.env` and fill in `ANTHROPIC_API_KEY`, `NOTION_TOKEN`,
`NOTION_DATABASE_ID`.

## Try it locally

```bash
npm install
node server.js
```

In another terminal, simulate a SendGrid POST:

```bash
curl -X POST http://localhost:3000/inbound-email \
  -F 'from=Alice <alice@example.com>' \
  -F 'to=inbox@yourdomain.com' \
  -F 'subject=Kickoff notes for Q4 planning' \
  -F 'text=Hey — please review the attached deck by Friday and send comments. Priorities: pricing, hiring, launch date.'
```

You should see a JSON response with `notion_page_id` and `notion_url`, and a
new page in your database.

For local dev with real SendGrid traffic, run `ngrok http 3000` and point
SendGrid at the public URL.

## Customizing the agent

Edit `EXTRACT_TOOL.input_schema` in `server/controllers/inboundEmail.js` to
change what the agent extracts (new fields, different categories, sentiment,
whatever). Add matching Notion properties in `buildNotionProperties()`. The
agent will conform to whatever schema you declare.

## Notes

- Attachments are ignored today. Switch `upload.none()` to `upload.any()` in
  the route and upload each file to Notion (`/v1/blocks/{id}/children` with
  `type: file`) if you need them.
- The controller returns HTTP 200 even on internal failure so SendGrid won't
  retry-storm on bugs. Errors are logged; check server logs when a forwarded
  email doesn't show up.
