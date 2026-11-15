// Independent implementation using Claude's documented mods API.
// Inspired by Oqura-ai/optim-jev's spawn/step split; no upstream code copied.
const EFFORTS = new Set(['low', 'medium', 'high', 'xhigh']);
const TIERS = new Set(['T1', 'T2', 'T3', 'T4']);

function validRoute(value) {
  return value && value.status === 'selected' && typeof value.model === 'string'
    && /^[a-zA-Z0-9._-]{1,128}$/.test(value.model) && TIERS.has(value.tier)
    && (value.effort === null || value.effort === undefined || EFFORTS.has(value.effort));
}

async function call($, options, flag, input) {
  const python = options.python || `${$.plugin.root}/../../.venv/bin/python`;
  const helper = options.helper || `${$.plugin.root}/../../tools/host_routing.py`;
  if (typeof python !== 'string' || !python.startsWith('/') || typeof helper !== 'string' || !helper.startsWith('/')) {
    return { status: 'unavailable', reason: 'Absolute helper and Python paths required' };
  }
  const response = await $.process.run([python, helper, flag], {
    stdin: JSON.stringify({ ...input, host: 'claude-code' }),
    timeoutMs: flag === '--authorize' ? 10000 : 310000,
  });
  if (response.exitCode !== 0 || response.isStdoutTruncated || response.stdout.length > 65536) {
    return { status: 'unavailable', reason: 'Routing helper did not return a complete result' };
  }
  return JSON.parse(response.stdout);
}

async function select($, options, request, event) {
  try {
    const result = await call($, options, '--execute', { request, context: '', event });
    if (validRoute(result)) return result;
    await $.ui.log(`Strategist: route unchanged (${result.status || 'invalid response'}).`);
  } catch {
    await $.ui.log('Strategist: routing unavailable; host model unchanged.');
  }
  return null;
}


export function register(on, options = {}) {
  const turns = new Map();
  const agents = new Map();
  const cap = (map, key, value) => {
    if (map.size >= 256) map.delete(map.keys().next().value);
    map.set(key, value);
  };

  on('turn.start', async ($, e, next) => {
    // Resumed/forked/custom subagents retain the host's original model contract.
    if (e.agentId === undefined && e.text && e.text.trim()) {
      const route = await select($, options, e.text, 'turn');
      if (route) cap(turns, e.turnId, route);
      else turns.delete(e.turnId);
    }
    return next(e);
  });

  on('agent.spawn', async ($, e, next) => {
    if (e.resume || e.fork || e.isTeammate || e.subagentType !== 'general-purpose') return next(e);
    const route = await select($, options, e.prompt, 'agent.spawn');
    if (!route) return next(e);
    if (route.tier === 'T4') {
      return { deny: 'Strategist: T4 subagent routing requires verified per-request accounting; spawn was not started.' };
    }
    let authorization;
    try {
      authorization = await call($, options, '--authorize', { model: route.model, effort: route.effort, tier: route.tier });
    } catch {
      authorization = { status: 'unavailable' };
    }
    if (authorization?.status !== 'authorized') {
      return { deny: 'Strategist: selected subagent route is no longer authorized; spawn was not started.' };
    }
    const result = await next({ ...e, model: route.model });
    if (result.agentId && !result.deny) {
      // Use the actual resolved model, never apply an effort chosen for a model
      // the host refused to use. Alias names resolve to full model IDs.
      const sameModel = result.model === route.model || result.model?.includes(route.model);
      if (sameModel) cap(agents, result.agentId, { ...route, resolved_model: result.model });
      else await $.ui.log('Strategist: host retained a different subagent model; effort unchanged.');
    }
    return result;
  });

  on('turn.step', async function* ($, e, next) {
    const route = e.agentId === undefined ? turns.get(e.turnId) : agents.get(e.agentId);
    if (!route) return yield* next(e);
    let authorization;
    try {
      authorization = await call($, options, '--authorize', { model: route.model, effort: route.effort, tier: route.tier });
    } catch {
      authorization = { status: 'unavailable' };
    }
    if (authorization?.status !== 'authorized') {
      // Throwing would make Claude skip the failed hook and send the original
      // request. Answer locally instead, with no next() and no inference.
      const answer = 'Strategist: выбранный маршрут сейчас не разрешён; запрос к модели не отправлен.';
      yield { kind: 'text', index: 0, text: answer };
      return { turnId: e.turnId, index: e.index, answer, toolUses: [], stopReason: 'end_turn', usage: null };
    }
    const routed = { ...e, model: route.model };
    if (route.effort) routed.effort = route.effort;
    else delete routed.effort;
    return yield* next(routed);
  });

  on('turn.complete', async ($, e, next) => {
    turns.delete(e.turnId);
    // A resumed subagent should not silently inherit a stale routing decision.
    if (e.agentId !== undefined) agents.delete(e.agentId);
    return next(e);
  });
}
