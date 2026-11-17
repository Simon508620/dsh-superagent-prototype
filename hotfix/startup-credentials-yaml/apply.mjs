#!/usr/bin/env node
/**
 * Repair the invalid ~/.dsh/.credentials.yaml that made the `credentials`
 * required plugin fail activation and aborted DSH startup (issues #24/#37/#38).
 *
 * Usage:
 *   node apply.mjs                 # repair ~/.dsh/.credentials.yaml
 *   node apply.mjs <file> [<file>] # repair explicit files
 *
 * Idempotent. Backs up the original beside the file before an atomic rename,
 * so a repair can always be undone. Exits 1 if any target is invalid in a way
 * this hotfix does not recognize (it never guesses at credential content).
 */
import { promises as fs } from 'node:fs'
import { homedir } from 'node:os'
import { analyze, defaultTargets, repair } from './lib.mjs'

const args = process.argv.slice(2)
const targets = args.length ? args : defaultTargets(homedir())

let patched = 0, already = 0, skipped = 0, failed = 0
for (const file of targets) {
  let text
  try {
    text = await fs.readFile(file, 'utf8')
  } catch (error) {
    if (error?.code !== 'ENOENT') {
      console.error(`  READ FAILED ${file}: ${error.message}`)
      failed++
    } else {
      console.log(`  ABSENT (nothing to repair) ${file}`)
      skipped++
    }
    continue
  }

  const result = analyze(text)
  if (result.state === 'empty' || result.state === 'valid') {
    console.log(`  OK (${result.state}) ${file}`)
    already++
    continue
  }
  if (result.state === 'invalid') {
    console.error(`  UNSUPPORTED ${file}: ${result.detail}`)
    failed++
    continue
  }

  const next = repair(text)
  if (next === null || next === text) {
    console.error(`  REPAIR FAILED ${file}`)
    failed++
    continue
  }

  const backup = `${file}.bak-${new Date().toISOString().replace(/[:.]/g, '-')}`
  const temp = `${file}.dsh-repair-${process.pid}.tmp`
  await fs.writeFile(backup, text, 'utf8')
  await fs.writeFile(temp, next, 'utf8')
  await fs.rename(temp, file)
  console.log(`  REPAIRED ${file} (${result.detail})`)
  console.log(`    backup: ${backup}`)
  patched++
}

console.log(`\n[startup-credentials-yaml] repaired=${patched} ok=${already} absent=${skipped} failed=${failed}`)
process.exit(failed > 0 ? 1 : 0)
