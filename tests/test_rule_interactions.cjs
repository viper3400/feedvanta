const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

const script = readFileSync(join(__dirname, '../app/templates/rule_interactions.html'), 'utf8')
  .match(/<script>([\s\S]*?)<\/script>/)[1];

function setup(operation = 'edit', options = {}) {
  const listeners = {};
  const status = { textContent: '' };
  const feedback = { textContent: '' };
  const savedFeedback = { textContent: '' };
  const calls = [];
  let focused;
  const focusable = name => ({ focus(opts) { focused = { name, opts }; } });
  const summary = focusable('summary');
  const input = focusable('create-value');
  const editor = { open: true, querySelector: () => summary };
  const create = { reset() { this.resetCalled = true; }, querySelector: () => input };
  const rows = new Map();
  const replacement = {
    dataset: { ruleId: '1' },
    querySelector: selector => selector === 'summary' ? summary : savedFeedback,
  };
  const row = {
    dataset: { ruleId: '1' },
    querySelector: () => feedback,
    remove() { rows.delete('1'); },
    replaceWith(value) { rows.set('1', value); },
  };
  const untouched = { draft: 'unsaved sibling' };
  rows.set('1', row);
  if (!options.lastRule) rows.set('2', untouched);
  const empty = { remove() {} };
  const list = {
    querySelector(selector) {
      if (selector === '[data-empty-rules]') return null;
      if (selector === '[data-rule-id]') return rows.values().next().value;
      return rows.get(selector.match(/"(\d+)"/)[1]);
    },
    append(value) {
      if (value.dataset) rows.set(value.dataset.ruleId, value);
      else this.empty = value;
    },
  };
  const form = {
    dataset: { ruleForm: operation },
    // Named controls shadow native form properties in browsers.
    action: { value: 'include', toString: () => '[object HTMLSelectElement]' },
    getAttribute: key => key === 'action' ? 'http://testserver/prefix/rules/1/edit' : null,
    draft: 'unsaved',
    closest: selector => selector === '[data-rule-id]' ? (operation === 'create' ? null : row) : editor,
    reset() { this.draft = 'saved'; this.resetCalled = true; },
    querySelector: () => input,
  };
  const cancel = { hidden: true, closest: () => form };
  const deleteForm = { onsubmit: 'native confirmation' };
  const buttons = [cancel, {}, {}];
  const panel = {
    addEventListener(type, fn) { listeners[type] = fn; },
    querySelectorAll(selector) {
      if (selector === '[data-cancel-rule]') return [cancel];
      if (selector === 'button') return buttons;
      return [deleteForm];
    },
    querySelector: () => create.querySelector(),
    setAttribute(key, value) { this[key] = value; },
    removeAttribute(key) { delete this[key]; },
  };
  const counts = { innerHTML: 'old counts' };
  const filtered = { innerHTML: 'old entries', hidden: true };
  const badges = { 'rules-tab': { textContent: 'old' }, 'filtered-tab': { textContent: 'old' } };
  const ids = { 'rules-panel': panel, 'rule-list': list, 'rule-status': status,
    'feed-counts': counts, 'filtered-panel': filtered };
  const freshList = {
    querySelector: selector => selector === '[data-empty-rules]' ? empty : (operation === 'delete' ? null : replacement),
    querySelectorAll: () => operation === 'create' ? [{ ...replacement, dataset: { ruleId: '3' } }] : [],
  };
  const fresh = {
    getElementById(key) {
      if (options.invalidPage) return null;
      if (key === 'rule-list') return freshList;
      return { innerHTML: `new ${key}` };
    },
    querySelector: () => ({ textContent: 'new count' }),
  };
  const window = {
    scrollX: 0, scrollY: 650,
    confirm: () => options.confirm !== false,
    scrollTo(x, y) { calls.push(['scroll', x, y]); },
  };
  runInNewContext(script, {
    document: {
      getElementById: key => ids[key],
      querySelector: selector => badges[selector.split(' ')[0].slice(1)],
    },
    window,
    FormData: class { constructor(value) { this.form = value; } },
    DOMParser: class { parseFromString() { return fresh; } },
    fetch: async (url, init) => {
      calls.push(['fetch', url, init]);
      if (options.networkError) throw new Error('offline');
      if (options.wait) await options.wait;
      return { ok: !options.status, status: options.status || 200, text: async () => '<html>' };
    },
  });
  const submit = () => listeners.submit({ target: { closest: () => form }, preventDefault() {} });
  return { submit, listeners, cancel, deleteForm, form, editor, rows, untouched, replacement,
    buttons, status, feedback, savedFeedback, counts, filtered, badges, list, calls, panel,
    get focused() { return focused; } };
}

test('cancel restores saved values, closes the editor and returns focus without a request', () => {
  const ui = setup();
  assert.equal(ui.cancel.hidden, false);
  ui.listeners.click({ target: { closest: () => ui.cancel } });
  assert.equal(ui.form.draft, 'saved');
  assert.equal(ui.editor.open, false);
  assert.equal(ui.focused.name, 'summary');
  assert.equal(ui.calls.length, 0);
});

test('save replaces only the edited rule and refreshes counts and entries without changing tabs or scroll', async () => {
  const ui = setup();
  await ui.submit();
  assert.equal(ui.rows.get('1'), ui.replacement);
  assert.equal(ui.rows.get('2'), ui.untouched);
  assert.equal(ui.untouched.draft, 'unsaved sibling');
  assert.equal(ui.counts.innerHTML, 'new feed-counts');
  assert.equal(ui.filtered.innerHTML, 'new filtered-panel');
  assert.equal(ui.filtered.hidden, true);
  assert.equal(ui.badges['rules-tab'].textContent, 'new count');
  assert.equal(ui.badges['filtered-tab'].textContent, 'new count');
  assert.equal(ui.savedFeedback.textContent, 'Regel gespeichert.');
  assert.equal(ui.focused.name, 'summary');
  assert.equal(ui.focused.opts.preventScroll, true);
  assert.deepEqual(ui.calls.at(-1), ['scroll', 0, 650]);
  assert.equal(ui.calls[0][1], 'http://testserver/prefix/rules/1/edit');
  assert.equal(ui.calls[0][2].method, 'POST');
  assert.equal(ui.calls[0][2].body.form, ui.form);
  assert.ok(ui.buttons.every(button => !button.disabled));
  assert.equal(ui.panel['aria-busy'], undefined);
});

test('delete asks once, can be declined, and removes the last rule with a useful focus target', async () => {
  const declined = setup('delete', { confirm: false });
  assert.equal(declined.deleteForm.onsubmit, null);
  await declined.submit();
  assert.equal(declined.calls.length, 0);
  assert.ok(declined.rows.has('1'));
  const ui = setup('delete', { lastRule: true });
  await ui.submit();
  assert.equal(ui.rows.size, 0);
  assert.ok(ui.list.empty);
  assert.equal(ui.focused.name, 'create-value');
  assert.equal(ui.status.textContent, 'Regel gelöscht.');
});

test('creating appends the new rule and resets only the create form', async () => {
  const ui = setup('create');
  await ui.submit();
  assert.ok(ui.rows.has('3'));
  assert.equal(ui.rows.get('2'), ui.untouched);
  assert.equal(ui.form.resetCalled, true);
  assert.equal(ui.focused.name, 'create-value');
});

for (const options of [{ status: 422 }, { status: 403 }, { networkError: true }, { invalidPage: true }]) {
  test(`failure retains drafts and editor state: ${JSON.stringify(options)}`, async () => {
    const ui = setup('edit', options);
    const original = ui.rows.get('1');
    await ui.submit();
    assert.equal(ui.form.draft, 'unsaved');
    assert.equal(ui.editor.open, true);
    assert.equal(ui.rows.get('1'), original);
    assert.ok(ui.feedback.textContent);
    assert.ok(ui.buttons.every(button => !button.disabled));
  });
}

test('pending mutations prevent duplicate requests and cancelling during submission', async () => {
  let finish;
  const wait = new Promise(resolve => { finish = resolve; });
  const ui = setup('edit', { wait });
  const pending = ui.submit();
  assert.ok(ui.buttons.every(button => button.disabled));
  await ui.submit();
  ui.listeners.click({ target: { closest: () => ui.cancel } });
  assert.equal(ui.form.resetCalled, undefined);
  assert.equal(ui.calls.length, 1);
  finish();
  await pending;
});
