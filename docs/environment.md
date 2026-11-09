# Runnable environment for the R1 case

## Chosen OS

macOS (arm64) — the trace's path shapes suggest the original host was
macOS; the exact OS version is unrecoverable and recorded as unknown.
Linux is not validated by this PoC.

## Toolchain selection

| Tool | Observed in evidence | Declared in starting `mise.toml` | Selected | Note |
| --- | --- | --- | --- | --- |
| Node | `v26.8.2` | `24` | `v26.8.2` | Observed runtime takes precedence over the declaration |
| pnpm | `11.6.0` | `11.6.0` | `11.6.0` | Resolved from the repo's `packageManager` field via corepack |

`environment.json` (generated into the agent package) records observed
versions, selected versions, unknowns (OS version, PATH, locale,
environment variables, internal harness instructions), and every
deviation. Binary paths are deliberately excluded — versions only.

## Reproduction steps

```sh
# 1. Materialize the pinned source (read-only use of any local clone).
PYTHONPATH=tools python3 -m t2e.cli materialize \
  --repo <path-to-yuurei-clone> \
  --case out/case --records out/records

# 2. Write environment.json + setup.sh, then run setup and the five
#    existing checks in an isolated copy (never inside the pristine
#    case source). T2E_NODE_BIN points at the selected toolchain.
T2E_NODE_BIN=<node-26.8.2-bin-dir> PYTHONPATH=tools python3 -m t2e.cli \
  environment --case out/case --records out/records --workdir out/env-check
```

Dependency setup is `corepack pnpm install --frozen-lockfile
--ignore-scripts` — the same flags as the original run. `node_modules`
and all other dependency outputs are produced only by setup, never ship
in the starting source, and never count toward source identity.

## What is verified

- The materialized tree equals the pinned commit
  `99cd09ab71b0ba2807f1d39d0a26dfe042e89bac` blob-for-blob (clean
  worktree at `HEAD == pinned`, depth-1 single commit, no remotes).
- Setup succeeds or fails explicitly; failure never marks the
  environment ready.
- Each of the five existing checks records its exit code and bounded
  output references (sha256 + byte counts of saved logs).
- Existing-check success on the starting commit is expected and does
  **not** establish task completion.
