import fs from 'node:fs';
import assert from 'node:assert/strict';
const app=fs.readFileSync('seiltanzer/web/js/app.js','utf8');
const ui=fs.readFileSync('seiltanzer/web/js/management_ui.js','utf8');
const util=fs.readFileSync('seiltanzer/web/js/util.js','utf8');
const legacy=fs.readFileSync('seiltanzer/web/js/ai_decision_ack.js','utf8');
const main=fs.readFileSync('seiltanzer/__main__.py','utf8');
const extensions=fs.readFileSync('seiltanzer/app_extensions.py','utf8');
assert.match(app,/body\.management_decision/);
assert.match(app,/ai-management-execution/);
assert.equal((app.match(/id="ai-management-execution"/g) || []).length, 1);
assert.match(ui,/ФАКТИЧЕСКОЕ ИСПОЛНЕНИЕ У БРОКЕРА/);
assert.match(ui,/ВЫПОЛНЕНО/);
assert.match(ui,/НЕ ВЫПОЛНЕНО/);
assert.match(ui,/\/api\/ai\/decision\/ack/);
assert.match(ui,/actions\.remove\(\)/);
assert.match(ui,/submitting \|\| settled/);
assert.doesNotMatch(ui,/Не удалось сохранить: \+ error/);
// Regression: the old global fetch interceptor remains only as dead legacy code.
// Importing it from util.js created a second set of execution buttons and stale ACKs.
assert.doesNotMatch(util,/import\s+['"]\.\/ai_decision_ack\.js['"]/);
assert.match(legacy,/window\.fetch/);
assert.doesNotMatch(main,/install_ai_decision_routes\(app\)/);
assert.match(extensions,/canonical_position_state/);
console.log('ai management decision smoke: PASS');
