import { cpSync, mkdirSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const source = fileURLToPath(new URL('../website/', import.meta.url));
const destination = fileURLToPath(new URL('../src/chinalaw/server/static/about/', import.meta.url));
mkdirSync(destination, { recursive: true });
for (const file of readdirSync(source)) {
  if (/\.(html|css|js|svg|json)$/.test(file)) cpSync(`${source}/${file}`, `${destination}/${file}`);
}
