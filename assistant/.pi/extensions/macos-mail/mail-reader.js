// Fixed, read-only Apple Mail bridge (JXA). Run only as:
//   /usr/bin/osascript -l JavaScript mail-reader.js   (request JSON on stdin, result JSON on stdout)
// Input is data only; it is never evaluated or turned into script text. This script never sends,
// moves, deletes, flags or marks messages, never opens links/attachments and never launches Mail.
ObjC.import('Foundation');

function BridgeError(code) { this.code = code; }

function readInput() {
  var data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
  var text = $.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding);
  return JSON.parse(ObjC.unwrap(text));
}

// ASCII-only JSON so stdout encoding cannot corrupt non-Latin text.
function output(value) {
  return JSON.stringify(value).replace(/[\u007f-￿]/g, function (c) {
    return '\\u' + ('0000' + c.charCodeAt(0).toString(16)).slice(-4);
  });
}

function str(value, max) { return typeof value === 'string' ? value.slice(0, max) : ''; }

function mailbox(request) {
  var mail = Application('Mail');
  if (!mail.running()) throw new BridgeError('mail_unavailable');
  if (typeof request.account !== 'string' || !request.account || typeof request.mailbox !== 'string' || !request.mailbox)
    throw new BridgeError('invalid_request');
  var accounts = mail.accounts.name();
  var a = accounts.indexOf(request.account);
  if (a < 0) throw new BridgeError('account_not_found');
  var account = mail.accounts.byName(accounts[a]);
  var names = account.mailboxes.name();
  var m = names.indexOf(request.mailbox);
  if (m < 0) {
    var wanted = request.mailbox.toLowerCase();
    for (var i = 0; i < names.length; i++) if (String(names[i]).toLowerCase() === wanted) { m = i; break; }
  }
  if (m < 0) throw new BridgeError('mailbox_not_found');
  return account.mailboxes.byName(names[m]);
}

// Account names only (Admin account picker).
function accounts() {
  var mail = Application('Mail');
  if (!mail.running()) throw new BridgeError('mail_unavailable');
  var names = mail.accounts.name(), result = [];
  for (var i = 0; i < names.length && result.length < 50; i++) if (typeof names[i] === 'string') result.push(str(names[i], 200));
  return { ok: true, accounts: result };
}

function count(request) {
  return { ok: true, count: mailbox(request).messages.length };
}

// Performance: bulk property reads return whole-mailbox arrays in one Apple Event each (~70 ms for
// thousands of messages). `whose` filters and bulk `messageId()` are slow (~2 s), so filtering happens
// on the arrays and the RFC Message-ID is fetched only for the final candidates (<= 25).
function list(request) {
  var after = new Date(request.received_after).getTime();
  if (isNaN(after)) throw new BridgeError('invalid_request');
  var limit = Math.max(1, Math.min(25, request.limit | 0));
  var box = mailbox(request), messages = box.messages;
  var ids = messages.id(), dates = messages.dateReceived(), reads = messages.readStatus(), flags = messages.flaggedStatus();
  var sender = str(request.sender_contains, 200).toLowerCase(), subject = str(request.subject_contains, 200).toLowerCase();
  var senders = messages.sender(), subjects = messages.subject(), sizes = messages.messageSize();
  var n = ids.length;
  if (dates.length !== n || reads.length !== n || flags.length !== n || senders.length !== n || subjects.length !== n || sizes.length !== n)
    throw new BridgeError('mail_unavailable'); // Mailbox changed between reads; caller may list again.
  var candidates = [];
  for (var i = 0; i < n; i++) {
    var time = dates[i] ? dates[i].getTime() : NaN;
    if (!(time > after)) continue;
    if (request.unread_only && reads[i]) continue;
    if (request.flagged_only && !flags[i]) continue;
    if (sender && String(senders[i] || '').toLowerCase().indexOf(sender) < 0) continue;
    if (subject && String(subjects[i] || '').toLowerCase().indexOf(subject) < 0) continue;
    candidates.push({ index: i, time: time });
  }
  candidates.sort(function (x, y) { return y.time - x.time; });
  var rows = [];
  for (var c = 0; c < candidates.length && rows.length < limit; c++) {
    var k = candidates[c].index, messageId;
    try { messageId = box.messages.byId(ids[k]).messageId(); } catch (error) {
      if (error && error.errorNumber === -1743) throw error;
      continue; // Moved or deleted since the bulk read.
    }
    if (!messageId) continue;
    rows.push({ id: ids[k], message_id: str(messageId, 998), sender: str(senders[k], 1000), subject: str(subjects[k], 2000),
      date_received: dates[k].toISOString(), read: Boolean(reads[k]), flagged: Boolean(flags[k]), size: Number(sizes[k]) || 0 });
  }
  return { ok: true, matched: candidates.length, messages: rows };
}

function read(request) {
  var box = mailbox(request), message;
  var max = Math.max(500, Math.min(8000, request.max_chars | 0));
  try {
    message = box.messages.byId(request.id);
    if (message.messageId() !== request.message_id) throw new BridgeError('stale_reference');
  } catch (error) {
    if (error && error.errorNumber === -1743) throw error;
    throw new BridgeError('stale_reference');
  }
  var body = message.content();
  body = typeof body === 'string' ? body : '';
  if (!body.trim()) throw new BridgeError('content_not_cached');
  return { ok: true, id: request.id, message_id: request.message_id, sender: str(message.sender(), 1000),
    subject: str(message.subject(), 2000), date_received: message.dateReceived().toISOString(),
    body: body.slice(0, max * 2), body_length: body.length };
}

function run() {
  var request;
  try { request = readInput(); } catch (error) { return output({ ok: false, error: 'invalid_request' }); }
  try {
    if (!request || typeof request !== 'object') throw new BridgeError('invalid_request');
    if (request.op === 'accounts') return output(accounts());
    if (request.op === 'count') return output(count(request));
    if (request.op === 'list') return output(list(request));
    if (request.op === 'read') return output(read(request));
    throw new BridgeError('invalid_request');
  } catch (error) {
    if (error instanceof BridgeError) return output({ ok: false, error: error.code });
    if (error && error.errorNumber === -1743) return output({ ok: false, error: 'automation_denied' });
    return output({ ok: false, error: 'mail_unavailable' });
  }
}
