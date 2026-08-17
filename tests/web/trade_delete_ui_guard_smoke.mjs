import assert from 'node:assert/strict';
import { wrapDeleteHandler } from '../../seiltanzer/web/js/trade_delete_ui_guard.js';

function controls() {
  return {
    button: { dataset: {}, disabled: false, textContent: 'УДАЛИТЬ', onclick: null },
    cancel: { disabled: false },
    errorBox: { textContent: '' },
    modalBack: { hidden: false },
  };
}

{
  const ui = controls();
  let calls = 0;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const original = async () => { calls += 1; await gate; ui.modalBack.hidden = true; };
  assert.equal(wrapDeleteHandler({ ...ui, original }), true);

  const first = ui.button.onclick({ type: 'click' });
  const second = ui.button.onclick({ type: 'click' });
  assert.equal(calls, 1, 'double click must not issue a duplicate delete request');
  assert.equal(ui.button.disabled, true);
  assert.equal(ui.cancel.disabled, true);
  assert.equal(ui.button.textContent, 'УДАЛЯЮ…');

  release();
  await Promise.all([first, second]);
  assert.equal(calls, 1);
  assert.equal(ui.button.disabled, true, 'successful hidden modal stays settled');
}

{
  const ui = controls();
  let calls = 0;
  const original = async () => { calls += 1; ui.errorBox.textContent = 'Trade not found'; };
  wrapDeleteHandler({ ...ui, original });
  await ui.button.onclick({ type: 'click' });
  assert.equal(calls, 1);
  assert.equal(ui.button.disabled, false, 'visible API error must allow retry');
  assert.equal(ui.cancel.disabled, false);
  assert.equal(ui.button.textContent, 'УДАЛИТЬ');
}

console.log('trade delete UI guard smoke: ok');
