const express = require('express');
const multer = require('multer');
const router = express.Router();

const { handleInboundEmail } = require('../controllers/inboundEmail');

// SendGrid Inbound Parse posts multipart/form-data. We only need the text
// fields (from, to, subject, text, html, ...). Attachments are ignored for
// now; swap .none() for .any() and add handling if you want to forward them.
const upload = multer({
  limits: { fieldSize: 25 * 1024 * 1024 }, // large emails
});

router.post('/inbound-email', upload.none(), handleInboundEmail);

module.exports = router;
