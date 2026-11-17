/**
 * Regression tests for the DSH startup credentials hotfix (issues #24/#37/#38).
 * The corrupt fixture is the exact document shape that made
 * @deepseek-ai/dsh-credentials-local reject the DSH credentials document and
 * abort startup with "1 required plugin did not activate".
 */
import { describe, expect, it } from 'vitest'
import { analyze, findJsonBlock, repair } from '../hotfix/startup-credentials-yaml/lib.mjs'

const REF_A = 'ref-alpha'
const REF_B = 'ref-beta'
const REF_C = 'ref-gamma'
const SECRET = 'session-secret'
const CORRUPT = `version: 1
refs:
  REF_A: ${REF_A}
  {
    REF_B: ${REF_B},
    REF_C: ${REF_C},
  }
records:
  client-connection/browser-session:
    kind: grant
    payload:
      version: 1
      secret: ${SECRET}
`

const VALID = `version: 1
refs:
  REF_A: ${REF_A}
records:
  client-connection/browser-session:
    kind: grant
    payload:
      version: 1
      secret: ${SECRET}
`

describe('startup credentials hotfix', () => {
  it('recognizes the JSON-style block spliced into the YAML refs mapping', () => {
    const block = findJsonBlock(CORRUPT)
    expect(block).not.toBeNull()
    expect(block!.start).toBe(3)
    expect(block!.end).toBe(6)
    expect(block!.indent).toBe('  ')
    expect(block!.entries).toEqual([
      { key: 'REF_B', value: REF_B },
      { key: 'REF_C', value: REF_C },
    ])
    expect(analyze(CORRUPT).state).toBe('repairable')
  })

  it('repairs to a valid version-1 document, preserving every key and value', () => {
    const fixed = repair(CORRUPT)!
    expect(fixed).not.toBeNull()
    expect(analyze(fixed).state).toBe('valid')
    expect(fixed).toContain(`  REF_A: ${REF_A}`)
    expect(fixed).toContain(`  REF_B: ${REF_B}`)
    expect(fixed).toContain(`  REF_C: ${REF_C}`)
    expect(fixed).toContain('  client-connection/browser-session:')
    expect(fixed).toContain(`      secret: ${SECRET}`)
    expect(fixed).not.toContain('{')
    expect(fixed).not.toContain('},')
    // Unrelated lines survive byte for byte.
    expect(fixed.split('\n').filter(line => line.includes('version: 1'))).toHaveLength(2)
  })

  it('leaves an already valid document untouched', () => {
    expect(analyze(VALID).state).toBe('valid')
    expect(findJsonBlock(VALID)).toBeNull()
    expect(repair(VALID)).toBeNull()
  })

  it('refuses to guess on an unrecognized document shape', () => {
    const unknown = 'some_other_key: value\n'
    expect(analyze(unknown).state).toBe('invalid')
    expect(repair(unknown)).toBeNull()
  })

  it('classifies an empty document as an empty credential store', () => {
    expect(analyze('').state).toBe('empty')
    expect(analyze('   \n').state).toBe('empty')
  })
})
