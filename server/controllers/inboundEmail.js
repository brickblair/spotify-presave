// Inbound email -> Claude agent -> Notion page.
//
// Wired to SendGrid Inbound Parse (multipart/form-data POST). See README.
// Fields SendGrid sends we care about: from, to, subject, text, html, envelope,
// attachments (count), attachment-info, attachment1..N (files).

const ANTHROPIC_API = 'https://api.anthropic.com/v1/messages';
const NOTION_API = 'https://api.notion.com/v1';
const NOTION_VERSION = '2022-06-28';
const MODEL = process.env.ANTHROPIC_MODEL || 'claude-sonnet-4-5';

const EXTRACT_TOOL = {
  name: 'save_to_notion',
  description:
    'Save the processed email to Notion. Call this exactly once with the ' +
    'structured summary of the email.',
  input_schema: {
    type: 'object',
    properties: {
      title: { type: 'string', description: 'Short page title, <= 80 chars.' },
      summary: {
        type: 'string',
        description: 'One-paragraph plain-language summary of the email.',
      },
      category: {
        type: 'string',
        enum: [
          'Task',
          'Reading',
          'Reference',
          'Newsletter',
          'Receipt',
          'Personal',
          'Other',
        ],
      },
      tags: {
        type: 'array',
        items: { type: 'string' },
        description: 'Up to 5 short topical tags.',
      },
      action_items: {
        type: 'array',
        items: { type: 'string' },
        description: 'Concrete follow-ups the recipient should take.',
      },
      due_date: {
        type: ['string', 'null'],
        description: 'ISO date (YYYY-MM-DD) if a deadline is stated, else null.',
      },
      priority: { type: 'string', enum: ['Low', 'Medium', 'High'] },
    },
    required: ['title', 'summary', 'category', 'tags', 'action_items', 'priority'],
  },
};

function parseEmailFields(body) {
  // SendGrid delivers each field as a form value; multer .none() puts them on req.body.
  const from = body.from || body.From || '';
  const to = body.to || body.To || '';
  const subject = body.subject || body.Subject || '(no subject)';
  const text = body.text || '';
  const html = body.html || '';
  return { from, to, subject, text, html };
}

async function runAgent({ from, to, subject, text }) {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) throw new Error('ANTHROPIC_API_KEY not set');

  const userContent =
    `You are triaging a forwarded email for the recipient's Notion inbox.\n\n` +
    `From: ${from}\nTo: ${to}\nSubject: ${subject}\n\n` +
    `--- Body ---\n${(text || '').slice(0, 16000)}\n--- End body ---\n\n` +
    `Extract the fields and call the save_to_notion tool exactly once.`;

  const res = await fetch(ANTHROPIC_API, {
    method: 'POST',
    headers: {
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      model: MODEL,
      max_tokens: 1024,
      tools: [EXTRACT_TOOL],
      tool_choice: { type: 'tool', name: 'save_to_notion' },
      messages: [{ role: 'user', content: userContent }],
    }),
  });

  if (!res.ok) {
    const errText = await res.text();
    throw new Error(`Anthropic API ${res.status}: ${errText}`);
  }

  const data = await res.json();
  const toolUse = (data.content || []).find((b) => b.type === 'tool_use');
  if (!toolUse) throw new Error('Model did not call save_to_notion');
  return toolUse.input;
}

function rt(text) {
  return [{ type: 'text', text: { content: (text || '').slice(0, 2000) } }];
}

function buildNotionProperties({ extracted, from, subject }) {
  // Uses only Title + rich_text + select + multi_select + date properties so
  // it works with the default database schema the README asks users to create.
  const props = {
    Name: { title: rt(extracted.title || subject) },
    From: { rich_text: rt(from) },
    Category: { select: { name: extracted.category || 'Other' } },
    Priority: { select: { name: extracted.priority || 'Medium' } },
  };
  if (Array.isArray(extracted.tags) && extracted.tags.length) {
    props.Tags = {
      multi_select: extracted.tags.slice(0, 10).map((t) => ({
        name: String(t).slice(0, 100),
      })),
    };
  }
  if (extracted.due_date) {
    props['Due Date'] = { date: { start: extracted.due_date } };
  }
  return props;
}

function buildNotionChildren({ extracted, subject, from, to, text }) {
  const para = (t) => ({
    object: 'block',
    type: 'paragraph',
    paragraph: { rich_text: rt(t) },
  });
  const heading = (t) => ({
    object: 'block',
    type: 'heading_2',
    heading_2: { rich_text: rt(t) },
  });
  const bullet = (t) => ({
    object: 'block',
    type: 'bulleted_list_item',
    bulleted_list_item: { rich_text: rt(t) },
  });

  const children = [
    heading('Summary'),
    para(extracted.summary || ''),
  ];

  if (Array.isArray(extracted.action_items) && extracted.action_items.length) {
    children.push(heading('Action items'));
    for (const item of extracted.action_items.slice(0, 20)) {
      children.push(bullet(item));
    }
  }

  children.push(heading('Original email'));
  children.push(para(`From: ${from}`));
  children.push(para(`To: ${to}`));
  children.push(para(`Subject: ${subject}`));

  // Notion caps a rich_text at 2000 chars; split the body into chunks.
  const body = (text || '').trim();
  if (body) {
    for (let i = 0; i < body.length && i < 40000; i += 1800) {
      children.push(para(body.slice(i, i + 1800)));
    }
  }
  return children.slice(0, 100); // Notion caps children per create call
}

async function createNotionPage({ extracted, from, to, subject, text }) {
  const token = process.env.NOTION_TOKEN;
  const databaseId = process.env.NOTION_DATABASE_ID;
  if (!token) throw new Error('NOTION_TOKEN not set');
  if (!databaseId) throw new Error('NOTION_DATABASE_ID not set');

  const res = await fetch(`${NOTION_API}/pages`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Notion-Version': NOTION_VERSION,
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      parent: { database_id: databaseId },
      properties: buildNotionProperties({ extracted, from, subject }),
      children: buildNotionChildren({ extracted, subject, from, to, text }),
    }),
  });

  if (!res.ok) {
    const errText = await res.text();
    throw new Error(`Notion API ${res.status}: ${errText}`);
  }
  return res.json();
}

exports.handleInboundEmail = async (req, res) => {
  try {
    const fields = parseEmailFields(req.body || {});
    if (!fields.text && !fields.html && !fields.subject) {
      return res.status(400).json({ error: 'Empty email payload' });
    }

    const extracted = await runAgent(fields);
    const page = await createNotionPage({ ...fields, extracted });

    // SendGrid retries on non-2xx, so always return 200 on success.
    return res.status(200).json({
      ok: true,
      notion_page_id: page.id,
      notion_url: page.url,
      extracted,
    });
  } catch (err) {
    console.error('[inbound-email] failed:', err);
    // Return 200 so SendGrid doesn't hammer retries on our bugs; log for debugging.
    return res.status(200).json({ ok: false, error: err.message });
  }
};
