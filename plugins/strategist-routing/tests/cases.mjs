export function defineCases(test, expect) {

function stubs(on, choose = () => ({status:'selected',model:'claude-sonnet-4-6',effort:'high',tier:'T2'}), authorize = () => ({status:'authorized'})) {
  const calls = [];
  const forwarded = [];
  on('ui.log', () => ({value: undefined}));
  on('process.run', ($, e) => {
    const payload = JSON.parse(e.init.stdin);
    calls.push({argv:e.argv,payload});
    const result = e.argv.includes('--authorize') ? authorize(payload) : choose(payload);
    return {value:{exitCode:0,stdout:JSON.stringify(result),stderr:'',isStdoutTruncated:false}};
  });
  on('turn.start', ($, e) => ({turnId:e.turnId}));
  on('turn.complete', () => ({text:''}));
  on('turn.step', async function* ($, e) {
    forwarded.push(e);
    yield {kind:'text',index:0,text:'ok'};
    return {turnId:e.turnId,index:e.index,answer:'ok',toolUses:[],stopReason:'end_turn',usage:null};
  });
  return {calls,forwarded};
}

async function step($, turnId, extra = {}) {
  const stream = $.turn.step({turnId,index:0,model:'claude-opus-4-6',effort:'medium',messageCount:1,...extra});
  let result = await stream.next();
  while (!result.done) result = await stream.next();
  return result.value;
}

test('main turn actually rewrites model and effort; D1 once, authorize every request', async ($, on) => {
  const seen = stubs(on);
  await $.turn.start({turnId:'one',text:'Draft a plan'});
  await step($,'one');
  await step($,'one',{index:1});
  expect(seen.calls.length).toBe(3);
  expect(seen.calls[0].payload.event).toBe('turn');
  expect(seen.calls[0].payload.host).toBe('claude-code');
  expect(seen.forwarded.map(e=>e.model)).toEqual(['claude-sonnet-4-6','claude-sonnet-4-6']);
  expect(seen.forwarded[0].effort).toBe('high');
});

test('new task starts from its own route and old route is cleared', async ($, on) => {
  const seen = stubs(on, p => ({status:'selected',model:p.request==='easy'?'haiku':'opus',effort:'low',tier:p.request==='easy'?'T1':'T3'}));
  await $.turn.start({turnId:'hard',text:'hard'});
  await step($,'hard');
  await $.turn.complete({turnId:'hard',answer:'ok',durationMs:1,isAborted:false,usage:null});
  await $.turn.start({turnId:'easy',text:'easy'});
  await step($,'easy');
  await step($,'hard');
  expect(seen.forwarded.map(e=>e.model)).toEqual(['opus','haiku','claude-opus-4-6']);
});

test('denied authorization sends no request to the model', async ($, on) => {
  const seen = stubs(on,undefined,() => ({status:'needs_review'}));
  await $.turn.start({turnId:'denied',text:'Task'});
  const result = await step($,'denied');
  expect(seen.forwarded.length).toBe(0);
  expect(result.answer).toContain('не отправлен');
});

test('uncertain classification preserves native route explicitly', async ($, on) => {
  const seen = stubs(on,() => ({status:'needs_review'}));
  await $.turn.start({turnId:'uncertain',text:'Task'});
  await step($,'uncertain');
  expect(seen.calls.length).toBe(1);
  expect(seen.forwarded[0].model).toBe('claude-opus-4-6');
});

test('fork/resume/custom role untouched and no classifier call', async ($, on) => {
  const seen = stubs(on);
  const spawned=[];
  on('agent.spawn',($,e)=>{spawned.push(e);return {model:e.model||'opus',agentId:'child'};});
  for (const detail of [{fork:true},{resume:'existing'},{subagentType:'named-reviewer'},{isTeammate:true}]) {
    await $.agent.spawn({tool_use_id:'tool',prompt:'Task',description:'Task',subagentType:'general-purpose',provider:{plugin:'engine',tier:'core'},parentModel:'opus',...detail});
  }
  expect(seen.calls.length).toBe(0);
  expect(spawned.length).toBe(4);
});

test('ordinary subagent model and step effort rewritten; resume later stays native', async ($, on) => {
  const seen = stubs(on);
  const spawned=[];
  on('agent.spawn',($,e)=>{spawned.push(e);return {model:e.model,agentId:'child'};});
  await $.agent.spawn({tool_use_id:'tool',prompt:'Task',description:'Task',subagentType:'general-purpose',provider:{plugin:'engine',tier:'core'},parentModel:'opus'});
  await $.turn.start({turnId:'child-turn',text:'Task',agentId:'child'});
  await step($,'child-turn',{agentId:'child'});
  expect(spawned[0].model).toBe('claude-sonnet-4-6');
  expect(seen.calls[0].payload.event).toBe('agent.spawn');
  expect(seen.calls.length).toBe(3);
  expect(seen.forwarded[0].effort).toBe('high');
  await $.turn.complete({turnId:'child-turn',agentId:'child',answer:'ok',durationMs:1,isAborted:false,usage:null});
  await step($,'resumed',{agentId:'child'});
  expect(seen.forwarded[1].model).toBe('claude-opus-4-6');
});

test('invalid model or max effort is never applied', async ($, on) => {
  const seen=stubs(on,() => ({status:'selected',model:'opus',effort:'max',tier:'T3'}));
  await $.turn.start({turnId:'invalid',text:'Task'});
  await step($,'invalid');
  expect(seen.calls.length).toBe(1);
  expect(seen.forwarded[0].effort).toBe('medium');
});

test('T4 subagent refused before spawn until request accounting is proven', async ($, on) => {
  const seen=stubs(on,()=>({status:'selected',model:'fable',effort:'high',tier:'T4'}));
  let spawned=0;
  on('agent.spawn',()=>{spawned++;return {model:'fable',agentId:'child'};});
  const answer=await $.agent.spawn({tool_use_id:'tool',prompt:'Task',description:'Task',subagentType:'general-purpose',provider:{plugin:'engine',tier:'core'},parentModel:'opus'});
  expect(spawned).toBe(0);
  expect(answer.deny).toContain('T4');
  expect(seen.calls.length).toBe(1);
});

test('authorization transport failure never falls through to inference', async ($, on) => {
  const seen=stubs(on,undefined,()=>{throw new Error('timeout');});
  await $.turn.start({turnId:'transport',text:'Task'});
  const result=await step($,'transport');
  expect(seen.forwarded.length).toBe(0);
  expect(result.usage).toBe(null);
});

test('main T4 rechecks quota on the second model request', async ($, on) => {
  let count=0;
  const seen=stubs(on,()=>({status:'selected',model:'fable',effort:'high',tier:'T4'}),()=>({status:++count===1?'authorized':'needs_review'}));
  await $.turn.start({turnId:'quota',text:'Difficult task'});
  await step($,'quota');
  const result=await step($,'quota',{index:1});
  expect(seen.forwarded.length).toBe(1);
  expect(count).toBe(2);
  expect(result.answer).toContain('не отправлен');
});

test('resolved child model preserves configured alias for authorization', async ($, on) => {
  const seen=stubs(on,()=>({status:'selected',model:'haiku',effort:'low',tier:'T1'}),p=>({status:p.model==='haiku'?'authorized':'needs_review'}));
  on('agent.spawn',()=>({model:'claude-haiku-5-5',agentId:'resolved-child'}));
  await $.agent.spawn({tool_use_id:'tool',prompt:'Task',description:'Task',subagentType:'general-purpose',provider:{plugin:'engine',tier:'core'},parentModel:'opus'});
  await step($,'child-turn',{agentId:'resolved-child',model:'claude-haiku-5-5'});
  expect(seen.calls.filter(c=>c.argv.includes('--authorize')).map(c=>c.payload.model)).toEqual(['haiku','haiku']);
  expect(seen.forwarded.length).toBe(1);
  expect(seen.forwarded[0].model).toBe('haiku');
});

test('authorization revoked after selection prevents spawn', async ($, on) => {
  const seen=stubs(on,undefined,()=>({status:'needs_review'}));
  let started=0;
  on('agent.spawn',()=>{started++;return {model:'sonnet',agentId:'child'};});
  const result=await $.agent.spawn({tool_use_id:'tool',prompt:'Task',description:'Task',subagentType:'general-purpose',provider:{plugin:'engine',tier:'core'},parentModel:'opus'});
  expect(started).toBe(0);
  expect(result.deny).toContain('no longer authorized');
  expect(seen.calls.length).toBe(2);
});


test('classifier consent revocation reaches authorization for main turn', async ($, on) => {
  const seen = stubs(on,
    () => ({status:'selected',model:'haiku',effort:'low',tier:'T1',classifier:'cascade'}),
    p => ({status:p.classifier === 'cascade' ? 'needs_review' : 'authorized'}));
  await $.turn.start({turnId:'revoked-cascade',text:'Task'});
  await step($,'revoked-cascade');
  expect(seen.calls[1].payload.classifier).toBe('cascade');
  expect(seen.forwarded.length).toBe(0);
});

test('classifier consent revocation reaches authorization for subagent', async ($, on) => {
  const seen = stubs(on,
    () => ({status:'selected',model:'haiku',effort:'low',tier:'T1',classifier:'cascade'}),
    p => ({status:p.classifier === 'cascade' ? 'needs_review' : 'authorized'}));
  let started = 0;
  on('agent.spawn', () => {started++; return {model:'haiku',agentId:'child'};});
  const result = await $.agent.spawn({tool_use_id:'tool',prompt:'Task',description:'Task',subagentType:'general-purpose',provider:{plugin:'engine',tier:'core'},parentModel:'opus'});
  expect(seen.calls[1].payload.classifier).toBe('cascade');
  expect(started).toBe(0);
  expect(result.deny).toContain('no longer authorized');
});

}
