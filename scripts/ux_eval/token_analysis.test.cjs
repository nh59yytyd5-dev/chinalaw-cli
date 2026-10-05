const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const zlib = require('node:zlib');
const { decodeEvents, analyzeSession, distribution, replayEvidence } = require('./token_analysis.cjs');
const event = (type, seq, data) => ({ type, seq, time: seq * 10, data });
const usage = { inputTokens: 100, cacheReadTokens: 200, outputTokens: 30, reasoningTokens: 20 };
const call = (seq, id, name = 'search') => event('tool/call', seq,
  { turn: 1, step: 1, callId: id, name: `mcp__chinalaw__chinalaw_${name}`, arguments: '{"secret":"测试"}' });
const result = (seq, id, text = '测试') => event('tool/result', seq,
  { turn: 1, step: 1, message: { content: [
    { type: 'tool-result', toolCallId: id, content: [{ type: 'text', text }], isError: false },
  ] } });
test('decode every concatenated zstd frame, including split UTF-8 and JSON lines', () => {
  const payload = Buffer.from(JSON.stringify({ text: '测试' }) + '\n' + JSON.stringify({ seq: 2 }) + '\n');
  const split = payload.indexOf(Buffer.from('测')) + 1;
  const frames = Buffer.concat([zlib.zstdCompressSync(payload.subarray(0, split)),
    zlib.zstdCompressSync(payload.subarray(split))]);
  assert.deepEqual(decodeEvents(frames), [{ text: '测试' }, { seq: 2 }]);
  assert.throws(() => decodeEvents(Buffer.from('corrupt frame')));
});
test('count only assistant/message usage; bind batched results to the following step', () => {
  const events = [event('step/start', 1, { turn: 1, step: 1 }),
    event('assistant/chunk', 2, { usage }),
    event('assistant/message', 3, { turn: 1, step: 1, usage }),
    call(4, 'a'), call(5, 'b', 'article'), result(6, 'b'), result(7, 'a'),
    event('step/start', 8, { turn: 1, step: 2 }),
    event('assistant/message', 9, { turn: 1, step: 2, usage })];
  const out = analyzeSession(events, 'test-run', 'trace.zstd');
  assert.deepEqual(out.usage, { inputTokens: 200, cacheReadTokens: 400, outputTokens: 60, reasoningTokens: 40 });
  assert.deepEqual(out.steps[0].preceding_mcp_results, []);
  assert.deepEqual(out.steps[1].preceding_mcp_results, [5, 4]);
  assert.equal(out.steps[1].preceding_result_text_bytes, 12);
  assert.equal(out.calls[0].next_step, 2);
  assert.equal(out.calls[0].result_text_bytes, 6);
  assert.equal(out.calls[0].result_text_chars, 2);
  assert.equal(out.calls[0].result_latency_ms, 30);
  assert.ok(!JSON.stringify(out).includes('secret'));
  assert.ok(!JSON.stringify(out).includes('测试'));
});
test('failed generation with no usage stays unknown even after receiving MCP results', () => {
  const out = analyzeSession([event('step/start', 1, { turn: 1, step: 1 }),
    call(2, 'a'), result(3, 'a'), event('step/start', 4, { turn: 1, step: 2 }),
    event('turn/end', 5, { reason: { kind: 'error', error: { code: 'QUOTA' } } })], 'run', 'trace');
  assert.equal(out.steps[1].usage, null);
  assert.equal(out.usage_messages, 0);
  assert.equal(out.error_code, 'QUOTA');
  assert.deepEqual(out.steps[1].preceding_mcp_results, [2]);
});
test('missing usage fields, clipped results, missing results and planning calls remain distinct', () => {
  const out = analyzeSession([event('step/start', 1, { turn: 1, step: 1 }),
    event('assistant/message', 2, { turn: 1, step: 1, usage: { inputTokens: 1 } }),
    call(3, 'a'), result(4, 'a', 'head Omitted 61704 bytes tail'), call(5, 'b'),
    event('tool/call', 6, { callId: 'plan', name: 'todo_write', arguments: 'private' })], 'run', 'trace');
  assert.equal(out.steps[0].usage.outputTokens, null);
  assert.equal(out.calls[0].clipped, true);
  assert.equal(out.calls[0].omitted_bytes, 61704);
  assert.equal(out.calls[0].next_step, null);
  assert.equal(out.calls[1].result_text_bytes, null);
  assert.equal(out.other_tool_calls, 1);
  assert.equal(out.calls.length, 2);
  assert.ok(!JSON.stringify(out).includes('private'));
});
test('duplicate call/result IDs fail instead of silently corrupting metrics', () => {
  assert.throws(() => analyzeSession([call(1, 'a'), call(2, 'a')], 'r', 'f'), /Duplicate/);
  assert.throws(() => analyzeSession([call(1, 'a'), result(2, 'a'), result(3, 'a')], 'r', 'f'), /Duplicate/);
});
test('nearest-rank p95, even median and missing distributions', () => {
  assert.deepEqual(distribution([1, 2, 3, 100]), { count: 4, sum: 106, median: 2.5, p95: 100, max: 100 });
  assert.equal(distribution([]).median, null);
});
test('historical/replay exports allow only numeric outcomes and anonymous participants', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'chinalaw-evidence-'));
  try {
    fs.writeFileSync(path.join(root, 'history-anonymized.json'), JSON.stringify([
      { id: 1, client: 'PRIVATE NAME', tool: 'search', params: { query: 'PRIVATE QUERY' },
        outcome: { secret: 'PRIVATE OUTCOME', counts: { total: 0, secret: 'PRIVATE' } } },
    ]));
    fs.mkdirSync(path.join(root, 'replay-final'));
    fs.writeFileSync(path.join(root, 'replay-final/replay.jsonl'), JSON.stringify({ id: 1,
      tool: 'search', result: { is_error: false, content: [{ type: 'text',
        text: JSON.stringify({ secret: 'PRIVATE BODY', counts: { total: 1 } }) }] } }));
    const out = replayEvidence(root);
    assert.equal(out.history[0].participant, 1);
    assert.equal(out.history[0].outcome.counts.total, 0);
    assert.equal(out.replays[0].outcome.counts.total, 1);
    assert.ok(!JSON.stringify(out).includes('PRIVATE'));
  } finally { fs.rmSync(root, { recursive: true }); }
});
