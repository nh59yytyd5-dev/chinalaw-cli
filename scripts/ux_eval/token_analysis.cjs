// Offline evidence export. Node >=22.15; Python 3 is used only for read-only SQLite.
// No prompts, arguments, response text, reasoning or credentials are exported.
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');

const KEYS = ['inputTokens', 'cacheReadTokens', 'outputTokens', 'reasoningTokens'];
const PREFIX = 'mcp__chinalaw__chinalaw_';
const TOOL_NAMES = new Set(['applicable', 'article', 'document', 'list', 'resolve', 'search']);
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const bytes = value => Buffer.byteLength(value, 'utf8');
const json = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const sumUsage = rows => Object.fromEntries(KEYS.map(key => [key,
  rows.reduce((sum, row) => sum + (row?.[key] ?? 0), 0)]));
function files(root) {
  if (!fs.existsSync(root)) return [];
  return fs.readdirSync(root, { withFileTypes: true }).flatMap(entry => {
    const file = path.join(root, entry.name);
    return entry.isDirectory() ? files(file) : entry.isFile() ? [file] : [];
  }).sort();
}
function decodeEvents(buffer) {
  const chunks = [];
  while (buffer.length) {
    const decoded = zlib.zstdDecompressSync(buffer, { info: true });
    if (!decoded.engine.bytesWritten) throw Error('zstd decoder made no progress');
    chunks.push(decoded.buffer);
    buffer = buffer.subarray(decoded.engine.bytesWritten);
  }
  // Concatenate bytes first: a Unicode codepoint may straddle two frames.
  return Buffer.concat(chunks).toString('utf8').split('\n').filter(Boolean).map(JSON.parse);
}
function mcpName(name) {
  const short = name?.startsWith(PREFIX) ? name.slice(PREFIX.length) : null;
  return TOOL_NAMES.has(short) ? short : null;
}
function distribution(values) {
  if (!values.length) return { count: 0, sum: 0, median: null, p95: null, max: null };
  const sorted = [...values].sort((a, b) => a - b), n = sorted.length;
  return { count: n, sum: sorted.reduce((a, b) => a + b, 0),
    median: (sorted[Math.floor((n - 1) / 2)] + sorted[Math.floor(n / 2)]) / 2,
    p95: sorted[Math.ceil(n * 0.95) - 1], max: sorted[n - 1] };
}
function analyzeSession(events, runId, artifact) {
  const steps = [], calls = [], schemas = [], prompts = [], usageRows = [];
  const stepMap = new Map(), callMap = new Map();
  let pending = [], config = {}, completion = null, errorCode = null, otherCalls = 0;
  function startStep(d, seq) {
    const key = `${d.turn}:${d.step}`;
    if (!stepMap.has(key)) {
      const row = { run: runId, artifact, turn: d.turn, step: d.step, start_seq: seq,
        preceding_mcp_results: pending.map(call => call.call_seq),
        preceding_result_text_bytes: pending.reduce((n, call) => n + call.result_text_bytes, 0),
        usage: null, usage_message_seqs: [] };
      pending.forEach(call => { call.next_step = d.step; call.next_turn = d.turn; });
      pending = [];
      stepMap.set(key, row); steps.push(row);
    }
    return stepMap.get(key);
  }
  for (const event of events) {
    const d = event.data || {};
    if (event.type === 'user/message' && d.source?.kind === 'user') {
      const content = JSON.stringify(d.content);
      prompts.push({ artifact, seq: event.seq, sha256: sha(content), content_bytes: bytes(content) });
    }
    if (event.type === 'request/header') {
      const header = d.header || {}, c = header.config || {};
      config = { provider: c.provider, model: c.model,
        max_tokens: c.maxTokens, reasoning_effort: c.reasoningEffort };
      for (const tool of header.tools || []) {
        const name = mcpName(tool.name);
        if (name) {
          const serialized = JSON.stringify(tool);
          schemas.push({ run: runId, artifact, seq: event.seq, tool: name,
            sha256: sha(serialized), json_bytes: bytes(serialized),
            json_chars: [...serialized].length });
        }
      }
    }
    if (event.type === 'step/start') startStep(d, event.seq);
    if (event.type === 'assistant/message') {
      const step = startStep(d, event.seq);
      if (d.usage && KEYS.some(key => typeof d.usage[key] === 'number')) {
        const usage = Object.fromEntries(KEYS.map(key => [key,
          typeof d.usage[key] === 'number' ? d.usage[key] : null]));
        usageRows.push(usage);
        step.usage = sumUsage([step.usage, usage]);
        step.usage_message_seqs.push(event.seq);
        step.missing_usage_fields = [...new Set([...(step.missing_usage_fields || []),
          ...KEYS.filter(key => usage[key] === null)])];
        for (const key of step.missing_usage_fields) step.usage[key] = null;
      }
    }
    if (event.type === 'tool/call') {
      const name = mcpName(d.name);
      if (!name) { otherCalls++; continue; }
      if (callMap.has(d.callId)) throw Error('Duplicate tool call id in session');
      const args = typeof d.arguments === 'string' ? d.arguments : JSON.stringify(d.arguments ?? {});
      const call = { run: runId, artifact, turn: d.turn, step: d.step,
        call_seq: event.seq, tool: name, at_ms: event.time,
        argument_bytes: bytes(args), argument_sha256: sha(args),
        result_seq: null, result_text_bytes: null, result_text_chars: null,
        result_content_json_bytes: null, result_nontext_blocks: null,
        result_is_error: null, clipped: null, omitted_bytes: null, result_latency_ms: null,
        next_step: null, next_turn: null };
      calls.push(call); callMap.set(d.callId, call);
    }
    if (event.type === 'tool/result') {
      for (const block of d.message?.content || []) {
        if (block.type !== 'tool-result') continue;
        const call = callMap.get(block.toolCallId);
        if (!call) continue; // Planning tools are outside MCP metrics.
        if (call.result_seq !== null) throw Error('Duplicate result for a tool call');
        const content = block.content || [];
        const texts = content.filter(b => b.type === 'text').map(b => b.text || '');
        const omitted = texts.flatMap(s => [...s.matchAll(/Omitted (\d+) bytes/g)])
          .map(match => Number(match[1]));
        Object.assign(call, { result_seq: event.seq,
          result_text_bytes: texts.reduce((n, s) => n + bytes(s), 0),
          result_text_chars: texts.reduce((n, s) => n + [...s].length, 0),
          result_content_json_bytes: bytes(JSON.stringify(content)),
          result_nontext_blocks: content.filter(b => b.type !== 'text').length,
          result_is_error: Boolean(block.isError), clipped: omitted.length > 0,
          omitted_bytes: omitted.reduce((a, b) => a + b, 0),
          result_latency_ms: event.time - call.at_ms });
        pending.push(call);
      }
    }
    if (event.type === 'turn/end') {
      completion = d.reason?.kind ?? null;
      errorCode = d.reason?.error?.code ?? null;
    }
  }
  return { steps, calls, schemas, prompts, usage: sumUsage(usageRows),
    usage_messages: usageRows.length, config, completion, error_code: errorCode,
    other_tool_calls: otherCalls };
}

// Server logs do not contain model usage or complete response bodies. Export them
// separately; defaults/parallelism make a naive positional join to calls unsafe.
function serverCalls(root) {
  const code = `import json, sqlite3, sys
from pathlib import Path
root = Path(sys.argv[1])
out = []
for p in sorted(root.glob('dsh-*/server-state/queries.db')):
    with sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True) as c:
        for rid, tool, duration, error, at in c.execute('SELECT id,tool,duration_ms,error,at FROM queries ORDER BY id'):
            if tool not in {'applicable','article','document','list','resolve','search'}:
                raise ValueError('Unexpected tool name')
            out.append(dict(run=p.parent.parent.name,artifact=str(p.relative_to(root)),row_id=rid,tool=tool,duration_ms=duration,is_error=bool(error),at=at))
print(json.dumps(out))`;
  return JSON.parse(execFileSync('python3', ['-c', code, root], { encoding: 'utf8' }));
}
function sourceDigest(root) {
  const hash = crypto.createHash('sha256');
  for (const file of files(root).filter(p => !p.split(path.sep).includes('__pycache__'))) {
    hash.update(path.relative(root, file)); hash.update('\0'); hash.update(fs.readFileSync(file));
  }
  return hash.digest('hex');
}
function outcomeMetrics(value) {
  const out = {};
  for (const key of ['matched', 'found', 'has_more']) {
    if (typeof value?.[key] === 'boolean') out[key] = value[key];
  }
  for (const key of ['total', 'returned', 'next_offset']) {
    if (typeof value?.[key] === 'number') out[key] = value[key];
  }
  if (value?.counts) {
    out.counts = Object.fromEntries(['article', 'law', 'norm_clause', 'norm_source', 'total']
      .filter(k => typeof value.counts[k] === 'number').map(k => [k, value.counts[k]]));
  }
  return out;
}
function replayEvidence(root) {
  const historical = path.join(root, 'history-anonymized.json');
  const participants = new Map();
  const history = fs.existsSync(historical) ? json(historical).map(row => {
    if (!participants.has(row.client)) participants.set(row.client, participants.size + 1);
    if (!TOOL_NAMES.has(row.tool)) throw Error('Unexpected historical tool');
    return { id: row.id, participant: participants.get(row.client), tool: row.tool,
      duration_ms: row.duration_ms, is_error: Boolean(row.error),
      outcome: outcomeMetrics(row.outcome), artifact: 'history-anonymized.json' };
  }) : [];
  const replays = [];
  for (const name of ['replay-baseline', 'replay-candidate', 'replay-v2', 'replay-final']) {
    const file = path.join(root, name, 'replay.jsonl');
    if (!fs.existsSync(file)) continue;
    const lines = fs.readFileSync(file, 'utf8').split('\n').filter(Boolean);
    lines.forEach((line, index) => {
      const row = JSON.parse(line);
      if (!TOOL_NAMES.has(row.tool)) throw Error('Unexpected replay tool');
      const text = (row.result?.content || []).filter(b => b.type === 'text').map(b => b.text).join('');
      let body = null;
      try { body = JSON.parse(text); } catch { /* Preserve failed/non-JSON results as such. */ }
      replays.push({ replay: name, id: row.id, tool: row.tool, duration_ms: row.duration_ms,
        artifact: `${name}/replay.jsonl`, line: index + 1,
        is_error: Boolean(row.result?.is_error), result_text_bytes: bytes(text),
        json_parseable: body !== null, outcome: outcomeMetrics(body) });
    });
  }
  return { history, replays };
}
function analyzeRoot(root) {
  const runs = [], steps = [], calls = [], schemas = [], servers = serverCalls(root);
  const sourceHashes = new Map();
  for (const entry of fs.readdirSync(root).sort()) {
    const dir = path.join(root, entry), runFile = path.join(dir, 'run.json');
    if (!fs.existsSync(runFile)) continue;
    const run = json(runFile);
    if (run.mode !== 'run') continue;
    if (!/^dsh-[a-z0-9-]+$/.test(entry)) throw Error('Unsafe public run identifier');
    const sessions = files(path.join(dir, 'dsh/sessions')).filter(p => p.endsWith('.zstd'));
    const analyzed = sessions.map(file => analyzeSession(decodeEvents(fs.readFileSync(file)),
      entry, path.relative(root, file)));
    analyzed.forEach(s => { steps.push(...s.steps); calls.push(...s.calls); schemas.push(...s.schemas); });
    const factsFile = path.join(dir, 'profile-facts.json');
    const facts = fs.existsSync(factsFile) ? json(factsFile) : {};
    const sourceName = path.basename(run.source || 'unknown');
    const source = path.join(root, sourceName);
    if (!sourceHashes.has(sourceName) && fs.existsSync(source) && sourceName.endsWith('-src')) {
      sourceHashes.set(sourceName, sourceDigest(source));
    }
    const capturedHash = sourceHashes.get(sourceName) ?? null;
    if (run.source_sha256 && capturedHash && run.source_sha256 !== capturedHash) {
      throw Error(`Frozen source checksum differs: ${entry}`);
    }
    const errorCode = analyzed.map(s => s.error_code).find(Boolean) ?? null;
    const stderrFile = path.join(dir, 'dsh-stderr.log');
    const stderr = fs.existsSync(stderrFile) ? fs.readFileSync(stderrFile, 'utf8') : '';
    const startupError = analyzed.every(s => s.usage_messages === 0) &&
      /plugin tree failed to load|--expose-internals is required/.test(stderr);
    const runSteps = analyzed.flatMap(s => s.steps), runCalls = analyzed.flatMap(s => s.calls);
    runs.push({ id: entry, model: run.model, exit_code: run.exit_code,
      seconds: Math.round(run.ended - run.started),
      status: run.exit_code === 0 ? 'completed' : errorCode === 'QUOTA' ? 'quota_error' :
        sessions.length === 0 || startupError ? 'setup_failure' : 'other_failure',
      source: sourceName, source_sha256: capturedHash,
      source_hash_recorded_at_run: Boolean(run.source_sha256),
      config: analyzed.map(s => s.config), prompts: analyzed.flatMap(s => s.prompts),
      profile: { harness: facts.harness ?? null,
        spill_policy: facts.spill_policy ?? 'unrecorded',
        tool_result_pruner: facts.tool_result_pruner ?? 'unrecorded' },
      usage: sumUsage(analyzed.map(s => s.usage)),
      usage_messages: analyzed.reduce((n, s) => n + s.usage_messages, 0),
      steps: runSteps.length, steps_without_usage: runSteps.filter(s => s.usage === null).length,
      mcp_calls: runCalls.length, server_calls: servers.filter(s => s.run === entry).length,
      other_tool_calls: analyzed.reduce((n, s) => n + s.other_tool_calls, 0),
      results_missing: runCalls.filter(c => c.result_seq === null).length,
      clipped_results: runCalls.filter(c => c.clipped).length,
      result_text_bytes: runCalls.reduce((n, c) => n + (c.result_text_bytes ?? 0), 0) });
  }
  const usage = sumUsage(runs.map(r => r.usage));
  const summary = { schema_version: 1, runs: runs.length,
    status: Object.fromEntries(['completed', 'setup_failure', 'quota_error', 'other_failure']
      .map(status => [status, runs.filter(r => r.status === status).length])),
    usage, total_input_tokens: usage.inputTokens + usage.cacheReadTokens,
    input_plus_output_tokens: usage.inputTokens + usage.cacheReadTokens + usage.outputTokens,
    usage_messages: runs.reduce((n, r) => n + r.usage_messages, 0),
    steps: steps.length, steps_without_usage: steps.filter(s => s.usage === null).length,
    mcp_calls: calls.length, server_calls: servers.length,
    results_missing: calls.filter(c => c.result_seq === null).length,
    results_without_next_step: calls.filter(c => c.next_step === null).length,
    nontext_blocks: calls.reduce((n, c) => n + (c.result_nontext_blocks ?? 0), 0),
    clipped_results: calls.filter(c => c.clipped).length,
    result_text_bytes: calls.reduce((n, c) => n + (c.result_text_bytes ?? 0), 0),
    tools: Object.fromEntries([...TOOL_NAMES].sort().map(tool => {
      const selected = calls.filter(c => c.tool === tool);
      return [tool, { calls: selected.length,
        result_text_bytes: distribution(selected.filter(c => c.result_seq !== null).map(c => c.result_text_bytes)),
        result_errors: selected.filter(c => c.result_is_error).length,
        clipped_results: selected.filter(c => c.clipped).length,
        server_duration_ms: distribution(servers.filter(c => c.tool === tool).map(c => c.duration_ms)) }];
    })),
    largest_results: [...calls].sort((a, b) => b.result_text_bytes - a.result_text_bytes).slice(0, 10),
    reconciliation: { usage_matches_legacy: null, calls_match_per_run: runs.every(r => r.mcp_calls === r.server_calls) },
  };
  const legacy = path.join(root, 'dsh-summary.json');
  if (fs.existsSync(legacy)) {
    const oldUsage = sumUsage(json(legacy).map(r => r.usage));
    summary.reconciliation.usage_matches_legacy = KEYS.every(k => oldUsage[k] === usage[k]);
    if (!summary.reconciliation.usage_matches_legacy) throw Error('Legacy usage totals differ');
  }
  return { summary, runs, steps, calls, schemas, servers, ...replayEvidence(root) };
}
function main() {
  if (process.argv.length !== 4) throw Error('Usage: node token_analysis.cjs INPUT_ROOT NEW_OUTPUT_DIR');
  const root = path.resolve(process.argv[2]), output = path.resolve(process.argv[3]);
  if (output === root || output.startsWith(root + path.sep)) throw Error('Keep derived exports outside raw evidence');
  const result = analyzeRoot(root);
  fs.mkdirSync(output, { recursive: false }); // Never overwrite a published evidence revision.
  fs.writeFileSync(path.join(output, 'summary.json'), JSON.stringify(result.summary, null, 2) + '\n');
  for (const name of ['runs', 'steps', 'calls', 'schemas', 'servers', 'history', 'replays']) {
    fs.writeFileSync(path.join(output, `${name}.jsonl`), result[name].map(r => JSON.stringify(r)).join('\n') + '\n');
  }
  console.log(JSON.stringify(result.summary, null, 2));
}
if (require.main === module) main();
module.exports = { decodeEvents, analyzeSession, analyzeRoot, distribution, replayEvidence };
