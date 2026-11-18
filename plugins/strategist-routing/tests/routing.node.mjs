// Offline contract runner when the native Claude test sandbox is unavailable.
// This does not claim to emulate engine policies; native acceptance stays separate.
import { test as nodeTest } from 'node:test';
import assert from 'node:assert/strict';
import { register } from '../hooks/register.mjs';
import { defineCases } from './cases.mjs';

const expect = value => ({
  toBe: expected => assert.equal(value, expected),
  toEqual: expected => assert.deepEqual(value, expected),
  toContain: expected => assert.ok(value.includes(expected)),
});

function test(name, body) {
  nodeTest(name, async () => {
    const hooks = new Map();
    const terminals = new Map();
    const on = (name, fn) => hooks.set(name, fn);
    register(on);
    const host = {
      plugin: {root:'/repo/plugins/strategist-routing'},
      process: {run:async(argv,init) => (await terminals.get('process.run')(host,{argv,init})).value},
      ui: {log:async text => (await terminals.get('ui.log')(host,{text})).value},
    };
    function event(name, input) {
      const next = e => terminals.get(name)(host,e);
      return hooks.has(name) ? hooks.get(name)(host,Object.freeze(input),next) : next(input);
    }
    const client = {
      turn: {start:e=>event('turn.start',e), step:e=>event('turn.step',e), complete:e=>event('turn.complete',e)},
      agent: {spawn:e=>event('agent.spawn',e)},
    };
    await body(client,(name,fn)=>terminals.set(name,fn));
  });
}
defineCases(test, expect);
