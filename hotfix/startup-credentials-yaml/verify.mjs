#!/usr/bin/env node
/**
 * Verify that the DSH credentials document no longer has the JSON-block
 * corruption that aborted startup, and that it parses with DSH's own parser.
 *
 * Usage:
 *   node verify.mjs                 # verify ~/.dsh/.credentials.yaml
 *   node verify.mjs <file> [<file>] # verify explicit files
 *
 * Exits 0 only when every existing target is structurally valid AND accepted
 * by the installed @deepseek-ai/dsh-credentials-local parser (when found).
 */
import { readFile } from 'node:fs/promises'
import { access, constants } from 'node:fs'
import { homedir } from 'node:os'
import { pathToFileURL } from 'node:url'
import { analyze, defaultTargets } from './lib.mjs'

const CREDENTIALS_PARSER = 'C:/Users/Topgun620/AppData/Roaming/npm/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-credentials-local/lib/index.js'

let parseCredentialsDocument
try {
  await access(CREDENTIALS_PARSER, constants.R_OK)
  parseCredentialsDocument = (await import(pathToFileURL(CREDENTIALS_PARSER).href)).parseCredentialsDocument
} catch {
  // The parser is optional here: structural verification still works on a
  // machine where DSH is installed elsewhere.
}

const targets = process.argv.slice(2).length ? process.argv.slice(2) : defaultTargets(homedir())
let ok = 0, bad = 0

for (const file of targets) {
  let text
  try {
    text = await readFile(file, 'utf8')
  } catch (error) {
    if (error?.code === 'ENOENT') {
      console.log(`  OK (absent) ${file}`)
      ok++
    } else {
      console.error(`  READ FAILED ${file}: ${error.message}`)
      bad++
    }
    continue
  }

  const result = analyze(text)
  if (result.state === 'empty' || result.state === 'valid') {
    let parsed = 'parser unavailable'
    if (parseCredentialsDocument) {
      try {
        const doc = parseCredentialsDocument(text, file)
        parsed = `${doc.refs.size} refs, ${doc.records.size} records`
      } catch (error) {
        console.error(`  BAD ${file}: structural check passed but DSH parser rejected it: ${error.message}`)
        bad++
        continue
      }
    }
    console.log(`  OK (${result.state}, ${parsed}) ${file}`)
    ok++
  } else {
    console.error(`  BAD (${result.state}) ${file}: ${result.detail}`)
    bad++
  }
}

console.log(`\nverify: ok=${ok} bad=${bad}`)
process.exit(bad > 0 ? 1 : 0)
