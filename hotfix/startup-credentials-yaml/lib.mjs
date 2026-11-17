#!/usr/bin/env node
/**
 * Shared detection / repair logic for the DSH startup failure
 * "1 required plugin did not activate" (issues #24, #37, #38, #57).
 *
 * Root cause: @deepseek-ai/dsh-credentials-local loadInitial() parses
 * ~/.dsh/.credentials.yaml with yaml.parseDocument(). A JSON-style object
 * block was spliced into the YAML `refs:` mapping:
 *
 *   refs:
 *     ANYSEARCH_API_KEY: as_sk_...
 *     {
 *       SENSENOVA1_API_KEY: sk-...,
 *       MAGPIE_GATEWAY_KEY: magpie,
 *     }
 *
 * YAML reads the bare `{` as a multiline implicit key and rejects the file
 * (MULTILINE_IMPLICIT_KEY / MISSING_CHAR). credentials is a required plugin,
 * so DSH aborts startup and 18 plugins wait for the missing `credentials`
 * service. The file was repaired on 11-14 but the failure was re-reported on
 * 11-17 purely because the reporter re-scanned old startup-*.log snapshots.
 *
 * The repair unwraps that block into ordinary `  KEY: value` mapping lines,
 * preserving every key/value and every other byte. It is idempotent and
 * refuses to guess on any document shape other than the one above.
 */

import { join } from 'node:path'

/** Return the JSON-style block spliced into a YAML mapping, or null. */
export function findJsonBlock(text) {
  const lines = text.split(/\r?\n/)
  for (let i = 0; i < lines.length; i++) {
    const open = /^([ \t]*)\{\s*$/.exec(lines[i])
    if (!open) continue
    const indent = open[1]
    const entries = []
    let j = i + 1
    for (; j < lines.length; j++) {
      const line = lines[j]
      if (/^\s*\}\s*,?\s*$/.test(line)) {
        const closeIndent = /^([ \t]*)\}/.exec(line)[1]
        if (closeIndent !== indent) return null
        if (entries.length === 0) return null
        return { start: i, end: j, indent, entries }
      }
      const item = /^([ \t]+)([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*,?\s*$/.exec(line)
      if (!item || item[1].length <= indent.length) return null
      const value = item[3]
      if (!value) return null
      entries.push({ key: item[2], value })
    }
    return null
  }
  return null
}

/**
 * Analyze a credentials document.
 * @returns {{state: 'empty'|'valid'|'repairable'|'invalid', detail: string, block?: object}}
 */
export function analyze(text) {
  if (!text || !text.trim()) return { state: 'empty', detail: 'empty document (empty credential store)' }
  const block = findJsonBlock(text)
  if (block) {
    return {
      state: 'repairable',
      detail: `JSON-style object block at lines ${block.start + 1}-${block.end + 1} with ${block.entries.length} entries`,
      block,
    }
  }
  // A well-formed version-1 document has a top-level `version: 1` line and no
  // JSON punctuation spliced into its mappings.
  const lines = text.split(/\r?\n/)
  const hasVersion = lines.some(line => /^version\s*:\s*1\s*$/.test(line))
  const hasJsonPunctuation = lines.some(line => /^\s*[{}]\s*,?\s*$/.test(line) || /^\s+[A-Za-z_][\w-]*\s*:.*,\s*$/.test(line))
  if (hasVersion && !hasJsonPunctuation) return { state: 'valid', detail: 'version-1 document, no JSON block found' }
  if (!hasVersion) return { state: 'invalid', detail: 'no top-level `version: 1` line; document shape is not recognized, refusing to guess' }
  return { state: 'invalid', detail: 'JSON punctuation remains in the document; refusing to guess' }
}

/** Repair the known JSON-block corruption. Returns null when nothing can be done safely. */
export function repair(text) {
  const result = analyze(text)
  if (result.state !== 'repairable') return null
  const lines = text.split(/\r?\n/)
  const { start, end, indent, entries } = result.block
  const replacement = entries.map(({ key, value }) => `${indent}${key}: ${value}`)
  const next = [...lines.slice(0, start), ...replacement, ...lines.slice(end + 1)].join('\n')
  return next
}

/** Default target: this machine's DSH credentials document. */
export function defaultTargets(homedir) {
  return [join(homedir, '.dsh', '.credentials.yaml')]
}
