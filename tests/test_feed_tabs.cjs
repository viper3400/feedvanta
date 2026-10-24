const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');
const { join } = require('node:path');

test('feed tabs default to rules and support clicks and keyboard navigation', () => {
  const template = readFileSync(join(__dirname, '../app/templates/feed_detail.html'), 'utf8');
  const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];
  const panels = [{ hidden: false }, { hidden: false }];
  panels.forEach(panel => { panel.setAttribute = (key, value) => { panel[key] = value; }; });
  let focused;
  const tabs = panels.map((panel, index) => ({
    attributes: { 'aria-controls': `panel-${index}` },
    listeners: {},
    getAttribute(key) { return this.attributes[key]; },
    setAttribute(key, value) { this.attributes[key] = value; },
    addEventListener(type, listener) { this.listeners[type] = listener; },
    focus() { focused = index; },
  }));
  const tablist = { hidden: true, querySelectorAll: () => tabs };
  const document = {
    getElementById: id => id === 'filter-tabs' ? tablist : panels[Number(id.split('-')[1])],
  };
  runInNewContext(script, { document });

  function assertSelected(index) {
    tabs.forEach((tab, current) => {
      assert.equal(tab.attributes['aria-selected'], String(current === index));
      assert.equal(tab.tabIndex, current === index ? 0 : -1);
      assert.equal(panels[current].hidden, current !== index);
      assert.equal(panels[current].role, 'tabpanel');
      assert.equal(panels[current].tabIndex, 0);
    });
  }

  assert.equal(tablist.hidden, false);
  assertSelected(0);
  tabs[1].listeners.click();
  assertSelected(1);
  tabs[0].listeners.click();
  assertSelected(0);

  for (const [from, key, expected] of [
    [0, 'ArrowRight', 1], [1, 'ArrowRight', 0],
    [0, 'ArrowLeft', 1], [1, 'ArrowLeft', 0],
    [0, 'End', 1], [1, 'Home', 0],
  ]) {
    let prevented = false;
    tabs[from].listeners.keydown({ key, preventDefault() { prevented = true; } });
    assert.equal(prevented, true);
    assert.equal(focused, expected);
    assertSelected(expected);
  }
  tabs[0].listeners.keydown({ key: 'Tab', preventDefault() { assert.fail('Tab must retain native behavior'); } });
  assertSelected(0);
});
