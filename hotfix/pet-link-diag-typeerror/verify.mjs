#!/usr/bin/env node
/**
 * 验证热修是否生效：读回目标文件，确认 apiFetch catch 已是 message-first。
 * 用法：node verify.mjs [node_modules 目录 ...]
 */
import { promises as fs } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const AFTER = "error: String((e && e.message) || (e && e.name) || e), url }";
const BEFORE = "error: String((e && e.name) || (e && e.message) || e), url }";
const TARGET = join('dsh-plugin-wallpaper-engine', 'lib', 'client.js');

function defaultRoots() {
  return [join(homedir(), '.dsh', 'profiles')];
}
async function findTargets(roots) {
  const out = [];
  for (const root of roots) {
    let entries;
    try { entries = await fs.readdir(root); } catch { continue; }
    const direct = join(root, TARGET);
    try { await fs.access(direct); out.push(direct); continue; } catch {}
    for (const profile of entries) {
      const cand = join(root, profile, 'node_modules', TARGET);
      try { await fs.access(cand); out.push(cand); } catch {}
    }
  }
  return out;
}

const roots = process.argv.slice(2).length ? process.argv.slice(2) : defaultRoots();
const targets = await findTargets(roots);
if (targets.length === 0) {
  console.error(`未找到 ${TARGET}（根：${roots.join(', ')}）`);
  process.exit(2);
}

let good = 0, bad = 0, notfound = 0;
for (const file of targets) {
  let src;
  try { src = await fs.readFile(file, 'utf8'); } catch { notfound++; continue; }
  if (src.includes(AFTER) && !src.includes(BEFORE)) { console.log(`  已修复 ${file}`); good++; }
  else if (src.includes(BEFORE)) { console.log(`  未修复 ${file}`); bad++; }
  else { console.log(`  原文缺失（版本不同）${file}`); notfound++; }
}
console.log(`\nverify: 已修复 ${good}，未修复 ${bad}，未定位 ${notfound}`);
process.exit(bad > 0 || notfound > 0 ? 1 : 0);
