# Hotfix · [pet-link diag] guide-shot / meme `reason:"TypeError"`（issue #30）

> 这是针对**本机已安装 DSH 插件产物**的本地热修（hotfix），不是上游源码 PR。
> 上游 `elysia395/dsh-wallpaper-engine` 设了 `forkable=false`、本机账号无 push，
> 无法直接提交 PR；本目录提供「分析 + 一键本地热修 + 验证」三件套，
> 让诊断在等待上游发版期间立刻可用。

## 根因（已在本机 1.4.2 安装上逐行验证）

`[pet-link diag] { kind:"guide-shot"/"meme", ok:false, reason:"TypeError" }`
这条诊断由**浏览器半边** `dsh-plugin-wallpaper-engine-pet-link/lib/client.pet-link.js`
上报到宿主日志。其 `reason` 透传的是宿主 `apiFetch` 返回的 `response.error`：

```js
// pet-link 子包 client.pet-link.js（meme / guide-shot 预取失败路径）
const why = (response && (response.error || response.status)) || 'no-blob';
reportPetLinkDiag({ kind: 'guide-shot', step, ok: false, reason: String(why) });
```

而 `response.error` 的真源在**主包** `dsh-plugin-wallpaper-engine/lib/client.js` 的
`apiFetch` catch 块（约 20432 行）：

```js
} catch (e) {
  return { ok: false, status: 0, data: null,
           error: String((e && e.name) || (e && e.message) || e), url };
}
```

**缺陷**：`e.name`（错误类名）被排在 `e.message`（具体信息）之前。
浏览器里 `fetch` 网络失败抛的是 `TypeError`：

- `e.name === "TypeError"`（类名，对所有网络失败都一样，无诊断价值）
- `e.message === "Failed to fetch"` / `"NetworkError when attempting to fetch resource"`（真因）

由于 class 名优先，宿主把具体 message 丢弃，诊断只剩 `reason:"TypeError"`。
同一时刻 issue #29（meme）与本 issue #30（guide-shot）都命中该路径，故两条同时报 `TypeError`，
**根因唯一**，就是这一行。

### 佐证（代码库自身约定）

pet-link 子包内其它诊断点（`rt.reportDiag`，约 1026/1033/1164/1169 行）
全部用 `error.message`（message-first）：

```js
error: String(error && error.message || error)
```

即本包内部约定本就是 message 优先，唯独宿主 `apiFetch` 写反了。
本热修把宿主 `apiFetch` 对齐为 message-first，一处即同时修好 meme 与 guide-shot 两条上报。

## 修复（一行）

`dsh-plugin-wallpaper-engine/lib/client.js` `apiFetch` catch：

```diff
- return { ok: false, status: 0, data: null, error: String((e && e.name) || (e && e.message) || e), url };
+ return { ok: false, status: 0, data: null, error: String((e && e.message) || (e && e.name) || e), url };
```

- 取 `e.message`（如 "Failed to fetch"）为优先，`e.name`（如 "TypeError"）退为兜底；
- 非 Error 对象（无 name/message）仍回落 `String(e)`，行为不变；
- 幂等、外科手术：只动这一处，不碰路由 / 业务 / 其它 catch。

## 部署步骤（本机）

插件装在 `~/.dsh/profiles/<profile>/node_modules/`。本机实测 `superagent` 与 `acp`
两个 profile 均已安装 v1.4.2。

```bash
# 1) 一键热修所有 profile 的已安装插件（幂等，可重复跑）
node hotfix/pet-link-diag-typeerror/apply.mjs

# 2) 读回验证：每个目标都应是「已修复」
node hotfix/pet-link-diag-typeerror/verify.mjs

# 3) 重启 DSH Web / 桌面会话使浏览器半边重取
#    （热重载会重拉已修改的 client.js；若走 HMR 接收器则无需整页刷新）
```

显式指定安装根目录（非默认 `~/.dsh/profiles`）：

```bash
node hotfix/pet-link-diag-typeerror/apply.mjs /path/to/node_modules
```

**重要**：这是打补丁在已安装产物上。下次 `pnpm` 升级 `dsh-plugin-wallpaper-engine*`
会**覆盖**本补丁 —— 届时要么重跑 `apply.mjs`，要么（首选）等上游发布含修复的版本。

## 上游同步建议（待人工，非本 ticket 可交付）

向 `elysia395/dsh-wallpaper-engine` 提等价的一行 PR（同上述 diff），
在 `src/` 真源（对应宿主 `apiFetch`）落地。本机无该仓写/ fork 权限
（`forkable=false`），故本 ticket 仅能交付本地热修 + 上游 diff，不能代替上游 PR。

## 验证口径

- 本机 1.4.2 安装上已逐行确认 BEFORE 串仅 1 处命中（主包 `lib/client.js`），
  子包 `client.pet-link.js` 无自有 `apiFetch`、复用宿主半边，无需单独打。
- `apply.mjs` 跑完后 `verify.mjs` 全「已修复」即通过；诊断日志里
  guide-shot / meme 的 `reason` 将变为具体 message（如 "Failed to fetch"），
  而非无信息 "TypeError"。
