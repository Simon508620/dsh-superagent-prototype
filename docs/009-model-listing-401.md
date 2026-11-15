# 009 Model listing 401 (issue #23)

The `[dsh-bug]` signature `generic::[llm-deepseek-api-key] model listing from <url> failed (LlmError: <url> answered HTTP <n>); using the bundled catalog` (issue #23) is a platform-side credential problem, not a defect of dsh-superagent's own code. This document records the root cause and the deployment steps the issue asked for.

## Root cause

The warning comes from DSH's `@deepseek-ai/dsh-llm-deepseek-api-key` plugin. While the route's built-in catalog is in use on the official endpoint, the plugin follows the service's model listing and refreshes it in the background. Its listing transport sends the route's API key as the `x-api-key` header:

```
GET https://api.deepseek.com/models
  x-api-key: <the route's DEEPSEEK_API_KEY>
```

DeepSeek's platform answers that request with `HTTP 401` and DSH falls back to the bundled catalog, logging the warning. The 401 is reproducible from this machine: `GET https://api.deepseek.com/models` returns 401 with no credentials and with a wrong `Authorization: Bearer` key. DeepSeek's own [API reference](https://platform.deepseek.com/api-docs/en/develop/models/overview) shows `Authorization: Bearer <key>` for its chat endpoints.

The `x-api-key` header is the credential the plugin resolves for its `x-api-key`-authenticated Messages route; whether DeepSeek's listing endpoint accepts it is a DeepSeek-platform-side fact, so the fix belongs in DSH's `@deepseek-ai/dsh-llm-deepseek` package (align the listing request's credential to what the platform answers 200 to, e.g. `Authorization: Bearer`), not in this repository. Until a DSH release carries it, the warning is by-design fallback behaviour: while a built-in catalog is configured, DSH always tries the live listing and retries it periodically; the bundled catalog keeps serving requests meanwhile.

## Local deployment

This machine's profile (`~/.dsh/profiles/superagent`) stores its DeepSeek model provider through a third-party gateway (`dsh-llm-pi-ai` with custom `baseURL`s and `models` lists); it does not mount the official `dsh-llm-deepseek-api-key` plugin. The `[llm-deepseek-api-key]` warning therefore comes from DSH's official base composition, not from this profile's `cordis.patch.yml`. To stop it on this machine: keep the official route's credential valid and the route off the hot path, or accept the warning as harmless fallback until DSH's listing transport is fixed upstream.

The warning stops once the official route's credential is stored and valid. Two equivalent ways:

1. **Web Models page (recommended).** In the DeepSeek Harness profile that runs dsh-superagent (`~/.dsh/profiles/superagent`), open Settings → Models and store the `DEEPSEEK_API_KEY` reference. The credentials service writes it and the listing refresh picks it up.
2. **Launch environment.** Export `DEEPSEEK_API_KEY` in the environment that launches the profile; the plugin's credential resolver falls back to the launch environment when no stored credential exists.

Then restart the profile. The next listing refresh answers 200 and the warning no longer appears.

If the key is correct but the 401 persists after the next DSH upgrade, that is the upstream package bug: re-arm the signature with the reporter's persistent-recurrence rule (a new local-day count re-arms even inside cooldown).

## Updating the reporter

The operator's machine runs the reporter from an unpacked release directory (`D:\AI software\DSH-Superagent-0.1.0-win-x64\dsh-superagent`), which predates this repository's bundled copy. This PR adds `scripts/dsh-bug-report.ps1` to the repository and the `llm-deepseek-model-listing-401` signature to it, so the release copy and the repository stay in step. Refresh the release directory from the merged tree, or point the scheduled task at the repository copy.
