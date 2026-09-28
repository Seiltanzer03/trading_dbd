import assert from 'node:assert/strict';
import {selectIVSurface} from '../../seiltanzer/web/js/bybit_context.js';

const primary = {status: 'delayed', value: [{strikes: [1, 2, 3]}]};
const surface = {status: 'delayed', value: [{strikes: [90, 100, 110]}], production_authority: false};
const supplemental = {options: {status: 'delayed', surface}};
assert.equal(selectIVSurface(primary, supplemental), primary);
assert.equal(selectIVSurface(primary, supplemental, 'bybit'), surface);
assert.equal(selectIVSurface({status: 'no_data'}, supplemental), surface);
assert.equal(selectIVSurface(primary, {}, 'bybit').status, 'no_data');
assert.deepEqual(primary.value[0].strikes, [1, 2, 3]);
console.log('Bybit source isolation smoke passed');
