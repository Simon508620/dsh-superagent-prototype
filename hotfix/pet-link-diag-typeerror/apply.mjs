#!/usr/bin/env node
/**
 * Hotfix: [pet-link diag] guide-shot / meme 取图 reason 报 "TypeError"（issue #30）
 *
 * 根因（已验证）：
 *   dsh-plugin-wallpaper-engine/lib/client.js 里 apiFetch 的 catch 块
 *     error: String((e && e.name) || (e && e.message) || e)
 *   优先取 Error 的 **类名**（浏览器 fetch 失败抛的是 TypeError，name === "TypeError"，
 *   而 message === "Failed to fetch" 才是真因），于是宿主半边把 meme / guide-shot
 *   预取失败的上报 reason 折叠成无信息的 "TypeError"。
 *
 *   pet-link 子包 (client.pet-link.js) 的 reportPetLinkDiag 直接透传 response.error，
 *   因此修好 host 的 apiFetch 一处即同时修好 meme 与 guide-shot 两条上报。
 *
 * 本脚本只做一处外科手术替换（把 name 放到 message 之后），幂等：
 *   - 找到 BEFORE 行 → 替换为 AFTER 行
 *   - 已经是 AFTER → 跳过
 *   - 两者都没有 → 报告未找到（可能版本不同 / 路径不对）
 *
 * 用法：
 *   node apply.mjs                    # 修本机 ~/.dsh 下所有 profile 的已安装插件
 *   node apply.mjs <node_modules 目录> ...  # 显式指定若干目录
 *
 * 注意：这是在**已安装产物**上打补丁，不是上游源码修改。上游
 *   elysia395/dsh-wallpaper-engine（forkable=false，本机账号无 push）才是源码真源，
 *   建议同步向该仓提一个等价的一行 PR；在此之前，本地热修让诊断立刻可用。
 *   插件下次 pnpm 升级会覆盖此补丁 —— 届时需重新 apply 或改用上游已修的版本。
 */

import { promises as fs } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const BEFORE = "error: String((e && e.name) || (e && e.message) || e), url }";
const AFTER  = "error: String((e && e.message) || (e && e.name) || e), url }";
// 主包持有 apiFetch；pet-link 子包复用宿主半边，无需单独打。
const TARGET = join('dsh-plugin-wallpaper-engine', 'lib', 'client.js');

function defaultRoots() {
  // ~/.dsh/profiles/*/node_modules  —— 各 profile 独立安装
  return [join(homedir(), '.dsh', 'profiles')];
}

/** 在 profile 根或显式 node_modules 目录下定位目标文件。 */
async function findTargets(roots) {
  const out = [];
  for (const root of roots) {
    let entries;
    try { entries = await fs.readdir(root); } catch { continue; }
    // root 直接是 node_modules？
    const direct = join(root, TARGET);
    try {
      await fs.access(direct);
      out.push(direct);
      continue;
    } catch { /* root 可能是 ~/.dsh/profiles，需要下钻一层 */ }
    for (const profile of entries) {
      const nm = join(root, profile, 'node_modules');
      const cand = join(nm, TARGET);
      try { await fs.access(cand); out.push(cand); } catch { /* 该 profile 没装 */ }
    }
  }
  return out;
}

const roots = process.argv.slice(2).length ? process.argv.slice(2) : defaultRoots();
const targets = await findTargets(roots);
if (targets.length === 0) {
  console.error(`[pet-link-diag-typeerror] 未在以下根目录找到 ${TARGET}：`);
  console.error(roots.map(r => `  ${r}`).join('\n'));
  console.error('请确认 DSH profile 已安装 dsh-plugin-wallpaper-engine，或用参数显式传 node_modules 目录。');
  process.exit(2);
}

let patched = 0;
let already = 0;
let missed = 0;
for (const file of targets) {
  let src;
  try { src = await fs.readFile(file, 'utf8'); } catch (e) { console.error(`读失败 ${file}: ${e.message}`); missed++; continue; }
  if (src.includes(AFTER) && !src.includes(BEFORE)) { console.log(`  已是修复版，跳过 ${file}`); already++; continue; }
  if (!src.includes(BEFORE)) { console.warn(`  未定位到 BEFORE 行（版本不同？）${file}`); missed++; continue; }
  const next = src.replace(BEFORE, AFTER);
  if (next === src) { console.warn(`  替换未生效 ${file}`); missed++; continue; }
  await fs.writeFile(file, next, 'utf8');
  console.log(`  已修复 ${file}`);
  patched++;
}

console.log(`\n[pet-link-diag-typeerror] 完成：修复 ${patched}，已是修复版 ${already}，未定位 ${missed}。`);
if (missed > 0) {
  console.log('部分目标未定位到原文 —— 若上游插件已升级/改动该 catch，请核对上游源码后同步本补丁。');
}
process.exit(missed > 0 ? 1 : 0);
