# Adversarial review: ChatGPT/Codex image backend

Review date: **2026-09-09**. Scope: `--provider chatgpt` / `--provider codex` in PR #20.

The review deliberately treated the implementation as hostile/buggy rather than assuming the happy path. Findings were converted into tests first where practical, then the implementation was changed to make those tests green.

## Perspectives used

### 1. Security reviewer

Questions asked:

- Can a prompt escape into a shell or make the child invoke `draw` recursively?
- Can existing OpenAI/HF environment variables silently turn this into API billing?
- Can model text trick the wrapper into reading an arbitrary output path?
- Can symlinks/hardlinks redirect artifact reads or output writes?
- Can error output leak credentials?
- Can project/user Codex configuration inject tools or alternate providers?

Findings and disposition:

| Finding | Severity | Result |
|---|---:|---|
| Prompt is passed on stdin with `shell=False`; no command interpolation. | good | Kept and covered by subprocess test. |
| Child could inherit API-routing credentials unless stripped. | high | Already stripped; tests cover parent/child separation. |
| Assignment-style secrets such as `api_key=...` were not redacted. | medium | **Fixed after RED test**; diagnostic redaction expanded. |
| Agent-authored output paths are untrusted. | high | Already ignored; wrapper reads only native thread/workspace artifact directories. |
| Symlink/hardlink artifact tricks. | high | Regular-file/link-count/inode recheck retained; tests cover symlink, hardlink and directory symlink cases. |
| User/project Codex configuration could change provider/tool behavior. | high | `--ignore-user-config`, `--ignore-rules`, zero project-doc bytes, forced OpenAI + ChatGPT login, disabled shell/web/apps/multi-agent retained. |
| A malicious replacement `codex` executable can still act with user permissions. | accepted | Explicitly documented as part of the trusted local computing base. |

### 2. Reliability / filesystem / OS reviewer

Questions asked:

- What happens on timeout, cancellation, clock changes, partial writes and output replacement failure?
- Are stale/ambiguous artifacts distinguishable from the current generation?
- Does a bad local reference waste a Codex process/login check before failing?

Findings and disposition:

| Finding | Severity | Result |
|---|---:|---|
| Artifact freshness depended on wall-clock `mtime`. A clock jump or restored metadata could reject the current thread's valid image. | medium | **Fixed after RED test**. Current canonical thread UUID + fresh temp workspace define freshness; `mtime` is no longer used. |
| Invalid reference images were validated only after Codex preflight. | low/medium | **Fixed after RED test**. Prompt/output/references now fail locally before any Codex process is started. |
| Existing destination could be lost on encode/save failure. | high | Atomic temp-file + `os.replace` retained; tests verify an existing output survives failure. |
| Timeout could leave descendants behind. | medium | POSIX process-group termination retained and tested. |
| Native artifact ambiguity (0 or >1 candidates). | medium | Fail closed; tests cover no image and multiple images. |

### 3. CLI / product UX reviewer

Questions asked:

- Can users pass options that look meaningful but are silently ignored?
- Does `--check` imply it validates settings that it actually ignores?
- Is the distinction between HF model selection, Codex reasoning model, and ChatGPT image model clear?

Findings and disposition:

| Finding | Severity | Result |
|---|---:|---|
| `--codex-bin` and `--codex-model` were accepted with `--backend hf` and silently ignored. | medium | **Fixed after RED tests**; explicit backend-mismatched flags are argument errors. `--timeout` is intentionally shared with the hf/stability API backends, not codex-only. |
| `--timeout` / `--codex-model` with `--check` were silently ignored. | medium | **Fixed after RED tests**; generation-only flags are rejected with `--check`. |
| `--model` could be mistaken for ChatGPT image-model selection. | high product-risk | Existing fail-closed behavior retained and README made explicit. |
| README overemphasized implementation prose and lacked an architecture view. | medium | Rewritten with a Mermaid flow diagram, guarantees, limits and provider table. |
| README said a free-tier HF read token was enough for inference. | stale fact | Removed. Current text explains HF credits/billing separately from authentication. |

### 4. Upstream-compatibility reviewer

Questions asked:

- Does preflight verify every Codex option the adapter later uses?
- Is the native image artifact layout still consistent with current Codex source?
- Can draw actually pin GPT Image 2.5 through subscription usage?

Findings and disposition:

| Finding | Severity | Result |
|---|---:|---|
| Preflight checked only a subset of required `codex exec` flags. | medium | **Fixed**. It now checks ignore-config/rules, ephemeral, JSON, git bypass, sandbox, cwd, image and color flags before generation. |
| `image_generation` feature could disappear on an old/incompatible client. | medium | Existing feature check retained. |
| Codex native artifact path remains `generated_images/<session>/<call>.png` when a save root is used. | verified | Wrapper remains thread-scoped. |
| ChatGPT Images 2.5 rollout includes Codex, but native Codex tool does not expose an image-model selector and inspected source still has internal `gpt-image-2`. | high honesty/compatibility risk | README/docs explicitly distinguish rollout from model pinning. No fake `--image-model` option added. |

### 5. Resource-abuse / fuzz reviewer

Questions asked:

- Can an agent send arbitrarily large prompt input?
- Are image sizes bounded before decode/read?
- Is JSONL returned to Python bounded?

Findings and disposition:

| Finding | Severity | Result |
|---|---:|---|
| Prompt length had no wrapper-level upper bound. | medium | **Fixed after RED test**: 1 MiB UTF-8 maximum. |
| Reference/native images could cause unbounded reads. | high | 32 MiB caps retained; Pillow decompression-bomb handling retained. |
| JSONL returned to Python could be arbitrarily large. | medium | Existing 8 MiB returned-output cap retained. The temporary child stdout file itself can still grow until process exit; see accepted risks. |

## RED → GREEN sequence

The first adversarial test commit was intentionally failing against the implementation:

- commit `7f7a4bd2b86172fcc02c4d169f2306f5590f0085` — `test(red): add adversarial TDD cases for subscription backend`

It introduced tests for:

1. invalid reference validation before Codex preflight;
2. 1 MiB prompt bound before Codex preflight;
3. HF rejection of explicit Codex-only flags;
4. `--check` rejection of ignored generation-only flags;
5. thread-scoped artifact selection independent of wall clock;
6. assignment-style credential redaction.

A second RED documentation-contract commit pinned the required README architecture/current-facts contract:

- commit `3bff3d094916388df7b2b5e48bd306a4a722ff78` — `test(red): pin README architecture and current provider contract`

Implementation/doc fixes followed in later commits; the CI result at the final PR head is the source of truth for GREEN status.

## Test layers

- **Pure unit/contract:** argument validation, event parsing, redaction, artifact validation, image encoding.
- **Real subprocess with fake Codex executable:** login/preflight sequencing, environment stripping, stdin prompt transport, timeout/process-group handling, native artifact discovery, no retry.
- **Linux CI:** full pytest suite on Python 3.9 and 3.12.
- **macOS CI:** offline Codex adapter + smoke/version contract.
- **Opt-in live preflight:** real local Codex login; no image request.
- **Opt-in live generation:** real native image generation; never enabled in CI because it may consume plan usage.

## Accepted risks / non-goals

1. **No server-side model attestation.** `draw` cannot prove whether OpenAI routed a particular subscription request to Flare, Sunburst, or another ChatGPT Images 2.5 serving configuration.
2. **Trusted local Codex binary.** The wrapper defends against inherited configuration and prompt/tool misuse, not a malicious executable already installed under the user's account.
3. **Child stdout disk growth.** Python only reads at most 8 MiB back into memory, but the temporary file receiving child stdout is not hard-capped while the child is running. With the official trusted Codex CLI this is accepted; a future streaming monitor could enforce a hard disk-output cap.
4. **No automatic retries.** This is deliberate because retries can double-consume plan usage.
5. **No automatic cleanup of Codex-native artifacts.** The original artifact belongs to Codex; `draw` leaves it in place and writes/copies the requested output separately.

## Review conclusion

After the fixes, the design is appropriately conservative for a local wrapper around an authenticated first-party CLI: local inputs fail before network-capable work, billing mode is constrained to ChatGPT login, outputs are thread-scoped and validated, provider-specific CLI mistakes fail closed, and the documentation no longer claims an image-model selection capability the upstream client does not expose.
