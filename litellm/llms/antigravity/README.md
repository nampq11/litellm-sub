# Antigravity provider

Routes requests to Google Antigravity (Cloud Code Assist) models with
multi-account rotation, OAuth token refresh, and auto-failover to the next
account on HTTP 429 quota exhaustion.

Supported models are listed in `common_utils.py` (`gemini-3.x-flash`,
`gemini-3.1-pro`, `claude-sonnet-4-6`, `claude-opus-4-6`, `gpt-oss-120b`).

## Configuration

Single account, via environment variables:

| Variable | Purpose | Default |
|---|---|---|
| `ANTIGRAVITY_ACCESS_TOKEN` | OAuth access token for the account | none |
| `ANTIGRAVITY_REFRESH_TOKEN` | OAuth refresh token used to renew the access token | none |
| `ANTIGRAVITY_EMAIL` | Account email, used when storing tokens | none |
| `ANTIGRAVITY_PROJECT_ID` | Google Cloud project id billed for the requests | none |

Multiple accounts (rotation pool), via files:

| Variable | Purpose | Default |
|---|---|---|
| `ANTIGRAVITY_TOKEN_DIR` | Directory holding per-account token caches | `~/.config/litellm/antigravity` |
| `ANTIGRAVITY_ACCOUNTS_FILE` | JSON file listing the accounts to rotate through | `<token_dir>/accounts.json` |

Miscellaneous:

| Variable | Purpose | Default |
|---|---|---|
| `ANTIGRAVITY_USER_AGENT` | `User-Agent` header sent to Antigravity | `antigravity/cli/1.4.0 (...)` |

The provider also imports accounts from `~/.pi/agent/antigravity-accounts.json`
(pi-antigravity format) when present.

## Proxy config

See `antigravity_config.yaml` at the repository root:

```yaml
model_list:
  - model_name: gemini-3.8-flash
    litellm_params:
      model: antigravity/gemini-3.8-flash
```
