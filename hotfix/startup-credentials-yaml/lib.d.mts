/** Types for the ESM hotfix library, consumed by the regression test under NodeNext resolution. */
export interface JsonBlock {
  start: number
  end: number
  indent: string
  entries: { key: string; value: string }[]
}
export type DocumentState = 'empty' | 'valid' | 'repairable' | 'invalid'
export interface Analysis {
  state: DocumentState
  detail: string
  block?: JsonBlock
}
export function findJsonBlock(text: string): JsonBlock | null
export function analyze(text: string): Analysis
/** Returns the repaired document, or null when no safe repair is possible. */
export function repair(text: string): string | null
export function defaultTargets(homedir: string): string[]
