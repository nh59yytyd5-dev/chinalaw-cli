// Inspect DSH's concatenated zstd frames without exposing reasoning or credentials.
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

function files(root) {
  if (!fs.existsSync(root)) return [];
  return fs.readdirSync(root, { withFileTypes: true }).flatMap(entry => {
    const name = path.join(root, entry.name);
    return entry.isDirectory() ? files(name) : [name];
  });
}

function events(file) {
  let bytes = fs.readFileSync(file);
  const chunks = [];
  while (bytes.length) {
    const decoded = zlib.zstdDecompressSync(bytes, { info: true });
    if (!decoded.engine.bytesWritten) throw Error('zstd decoder made no progress');
    chunks.push(decoded.buffer.toString());
    bytes = bytes.subarray(decoded.engine.bytesWritten);
  }
  return chunks.join('').trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
}

const root = path.resolve(process.argv[2]);
const summaries = [];
for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
  const dir = path.join(root, entry.name);
  if (!entry.isDirectory() || !fs.existsSync(path.join(dir, 'run.json'))) continue;
  const run = JSON.parse(fs.readFileSync(path.join(dir, 'run.json')));
  if (run.mode !== 'run') continue;
  const result = { id: entry.name, exit: run.exit_code,
    seconds: Math.round(run.ended - run.started), tools: {}, usage: {},
    calls: [], completion: null, model: run.model, clipped_results: 0 };
  for (const file of files(path.join(dir, 'dsh', 'sessions')).filter(p => p.endsWith('.zstd'))) {
    for (const event of events(file)) {
      const d = event.data || {};
      if (event.type === 'tool/call') {
        result.tools[d.name] = (result.tools[d.name] || 0) + 1;
        result.calls.push({ tool: d.name, arguments: d.arguments });
      }
      if (event.type === 'tool/result') {
        const message = JSON.stringify(d.message || {});
        if (/Omitted \d+ bytes/.test(message)) result.clipped_results += 1;
      }
      if (event.type === 'assistant/message') {
        for (const [key, value] of Object.entries(d.usage || {})) {
          if (typeof value === 'number') result.usage[key] = (result.usage[key] || 0) + value;
        }
      }
      if (event.type === 'turn/end') result.completion = d.reason?.kind;
    }
  }
  fs.writeFileSync(path.join(dir, 'summary.json'), JSON.stringify(result, null, 2), { mode: 0o600 });
  summaries.push(result);
}
fs.writeFileSync(path.join(root, 'dsh-summary.json'), JSON.stringify(summaries, null, 2), { mode: 0o600 });
console.log(JSON.stringify(summaries.map(({ calls, ...result }) => result), null, 2));
