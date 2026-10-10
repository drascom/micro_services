// Fixed Apple Reminders bridge (JXA). Run only as:
//   /usr/bin/osascript -l JavaScript reminders-bridge.js   (request JSON on stdin, result JSON on stdout)
// Input is data only; it is never evaluated or turned into script text. Reminders is never activated:
// if it is not running it is launched hidden in the background (open -g -j).
// Design reference (no code copied): https://github.com/ssdiwu/pi-reminders (MIT).
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
function iso(value) { return value instanceof Date && !isNaN(value.getTime()) ? value.toISOString() : null; }

function reminders() {
  var app = Application('Reminders');
  if (!app.running()) {
    var task = $.NSTask.launchedTaskWithLaunchPathArguments('/usr/bin/open', $(['-g', '-j', '-a', 'Reminders']));
    task.waitUntilExit;
    for (var i = 0; i < 50 && !app.running(); i++) delay(0.1);
    if (!app.running()) throw new BridgeError('reminders_unavailable');
  }
  return app;
}

function findList(app, name) {
  if (typeof name !== 'string' || !name) throw new BridgeError('invalid_request');
  var names = app.lists.name(), wanted = name.toLowerCase();
  for (var i = 0; i < names.length; i++) if (String(names[i]).toLowerCase() === wanted) return app.lists.byName(names[i]);
  throw new BridgeError('list_not_found');
}

function describe(r) {
  var allDay = r.alldayDueDate(), due = r.dueDate();
  var list = '';
  try { list = r.container().name(); } catch (e) {}
  return { id: String(r.id()), title: str(r.name(), 300), notes: str(r.body() || '', 300), due: iso(allDay || due), all_day: Boolean(allDay),
    priority: r.priority() || 0, completed: Boolean(r.completed()), list: str(list, 100) };
}

// Bulk property reads: one Apple Event per property per list.
function listOp(app, request) {
  var lists = request.list ? [findList(app, request.list)] : app.lists();
  var after = request.due_after ? Date.parse(request.due_after) : null, before = request.due_before ? Date.parse(request.due_before) : null;
  var notesMax = Math.min(Math.max(1, request.notes_max | 0 || 300), 8000);
  var found = [];
  for (var l = 0; l < lists.length; l++) {
    var all = lists[l].reminders;
    var set = request.status === 'open' ? all.whose({ completed: false }) : request.status === 'done' ? all.whose({ completed: true }) : all;
    var ids = set.id(), names = set.name(), dues = set.dueDate(), allDays = set.alldayDueDate(), priorities = set.priority(),
      done = set.completed(), bodies = set.body(), listName = str(lists[l].name(), 100);
    for (var i = 0; i < ids.length; i++) {
      var due = allDays[i] || dues[i], time = due instanceof Date ? due.getTime() : null;
      if (time === null ? !request.undated : (after !== null && time < after) || (before !== null && time >= before)) continue;
      found.push({ id: String(ids[i]), title: str(names[i], 300), notes: str(bodies[i] || '', notesMax), due: iso(due), all_day: Boolean(allDays[i]),
        priority: priorities[i] || 0, completed: Boolean(done[i]), list: listName, time: time });
    }
  }
  found.sort(function (a, b) {
    if (a.time === b.time) return 0;
    if (a.time === null) return 1;
    if (b.time === null) return -1;
    return a.time - b.time;
  });
  var max = Math.min(Math.max(1, request.max | 0), 300);
  var result = found.slice(0, max).map(function (item) { delete item.time; return item; });
  return { ok: true, matched: found.length, reminders: result };
}

// The reminder must still exist with the title the client listed; otherwise the reference is stale.
function target(app, request) {
  if (typeof request.id !== 'string' || !request.id || typeof request.expected_title !== 'string') throw new BridgeError('invalid_request');
  var r = app.reminders.byId(request.id), name;
  try { name = r.name(); } catch (e) { throw new BridgeError('stale_reference'); }
  if (name !== request.expected_title) throw new BridgeError('stale_reference');
  return r;
}

function setDue(r, request) {
  if (request.due === null) { r.alldayDueDate = null; r.dueDate = null; return; }
  if (typeof request.due !== 'string') return;
  var date = new Date(request.due);
  if (isNaN(date.getTime())) throw new BridgeError('invalid_request');
  if (request.all_day) r.alldayDueDate = date; else r.dueDate = date;
}

function add(app, request) {
  if (typeof request.title !== 'string' || !request.title.trim()) throw new BridgeError('invalid_request');
  var list = request.list ? findList(app, request.list) : app.defaultList();
  var props = { name: request.title };
  if (typeof request.notes === 'string' && request.notes) props.body = request.notes;
  if (typeof request.priority === 'number') props.priority = request.priority;
  var r = app.Reminder(props);
  list.reminders.push(r);
  setDue(r, request);
  return { ok: true, reminder: describe(r) };
}

function update(app, request) {
  var r = target(app, request);
  if (typeof request.title === 'string' && request.title.trim()) r.name = request.title;
  if (typeof request.notes === 'string') r.body = request.notes;
  if (typeof request.priority === 'number') r.priority = request.priority;
  setDue(r, request);
  return { ok: true, reminder: describe(r) };
}

function run() {
  var request;
  try { request = readInput(); } catch (error) { return output({ ok: false, error: 'invalid_request' }); }
  try {
    if (!request || typeof request !== 'object') throw new BridgeError('invalid_request');
    var app = reminders();
    if (request.op === 'list') return output(listOp(app, request));
    if (request.op === 'add') return output(add(app, request));
    if (request.op === 'update') return output(update(app, request));
    if (request.op === 'complete') { var r = target(app, request); r.completed = true; return output({ ok: true, reminder: describe(r) }); }
    if (request.op === 'delete') { app.delete(target(app, request)); return output({ ok: true, deleted: true }); }
    throw new BridgeError('invalid_request');
  } catch (error) {
    if (error instanceof BridgeError) return output({ ok: false, error: error.code });
    if (error && (error.errorNumber === -1743 || /not authori[sz]ed/i.test(String(error.message)))) return output({ ok: false, error: 'automation_denied' });
    return output({ ok: false, error: 'reminders_unavailable' });
  }
}
