// Validate that every built lib/*.js file is a syntactically complete ES module.
//
// The dsh-superagent Windows adaptation fork (Check-mj/dsh-superagent-windows) patches the
// built lib/*.js in place. A truncated write or a partially-applied patch leaves a file that
// Node's ESM loader rejects with `SyntaxError: Unexpected end of input` or
// `SyntaxError: Invalid or unexpected token`, which surfaces at startup as
// "dsh-superagent: failed to import" (auto-reported as [dsh-bug] issues #34 and #35).
// This validator parses every entry and shared chunk with `vm.SourceTextModule`, which
// catches both error classes before the process starts.
//
// Usage:
//   node --experimental-vm-modules scripts/validate-lib.mjs          # validate ./lib
//   node --experimental-vm-modules scripts/validate-lib.mjs <dir>     # validate a directory
//
// Exits 0 when every .js file parses; exits 1 with a per-file diagnosis otherwise.

import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { SourceTextModule } from 'node:vm'
import { fileURLToPath } from 'node:url'

const dir = resolve(process.argv[2] ?? join(fileURLToPath(import.meta.url), '..', '..', 'lib'))

let failed = 0
for (const name of readdirSync(dir).filter((n) => n.endsWith('.js')).sort()) {
  const path = join(dir, name)
  const source = readFileSync(path, 'utf8')
  try {
    new SourceTextModule(source, { identifier: path })
  } catch (error) {
    failed += 1
    const message = String(error.message ?? error)
    const hint = /unexpected end of input/i.test(message)
      ? 'file is truncated: the last write finished before the module was complete (a non-atomic patch left a half-written file)'
      : /invalid or unexpected token/i.test(message)
        ? 'file contains invalid bytes (an encoding conversion or a partial write corrupted the source)'
        : 'file does not parse as an ES module'
    console.error(`FAIL ${name}: ${message}\n  hint: ${hint}`)
  }
}

if (failed) {
  console.error(`${failed} of ${readdirSync(dir).filter((n) => n.endsWith('.js')).length} lib files failed to parse. Rebuild with \`pnpm build\` before installing the plugin.`)
  process.exit(1)
}
console.log(`OK: every lib/*.js file under ${dir} parses as a complete ES module`)
