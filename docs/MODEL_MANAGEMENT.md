# Onyx model management — 2026-09-29

The one existing Python backend owns model operations. The Qt client never
loads weights. Runtime inspected on this machine: Ollama 0.34.4, loopback only.
Installed, selected for this chat, default for new chats, resident and in-use
are separate states obtained from tags/show/ps and the current task registry.
Listing metadata does not warm models. Refresh is asynchronous, with a 60s
show cache invalidated by tag/digest changes and an explicit refresh button.

## Chat selection and identity

`models.select` verifies actual local weights and completion capability before
writing the chat selection. Embedding-only models and remote/cloud aliases
cannot be Brain. New chats use the explicit default or existing brain.fast.
No first-installed fallback remains when a configured model is missing.

At submit, name, digest, capabilities and effective context/temperature are
frozen in desktop_model_turns in the canonical data/kk.db. Changing the selector
does not rewrite an accepted turn. Before inference, identity is revalidated;
an external tag replacement causes an explicit error. Diagnostics record the
actual response model and effective options. The response header shows it.
Model changes preserve the same conversation and history. The last successful
actual model is offered as an explicit recovery choice; no silent substitution.

The effective context is capped by both existing provider configuration and
the model's metadata. Thinking/tools fields are omitted when unsupported;
invented calls from a non-tools model are rejected. Existing execution policy,
voice gates, negation handling and permission checks still apply. Metadata is
not proof of model quality. Custom aliases do not establish signed authorship.

## Installation

`models.install` starts one backend-owned durable job, with a stable ID and
SQLite progress. Concurrent duplicate requests return the active job; other
installs wait for explicit action. Reopening a client recovers the same job.
Backend restart marks unfinished jobs interrupted; retry is explicit.

- Pull: exact `model:tag`, official runtime registry, explicit confirmation,
  maximum 32 GiB chosen limit (UI defaults to 2 GiB), at least 1 GiB disk reserve
  beyond that limit. Exact metadata is unknown until the manifest arrives.
- Import: local single-file GGUF v2/v3; bounded metadata inspection shows size
  and architecture before confirmation. Rejects missing architecture, malformed
  metadata, adapters/projectors needing a base, split GGUF, name collisions and
  excess size. The runtime verifies final architecture compatibility. SHA-256
  blob HEAD/POST then create/files use the supported API; no shell interpolation.
  Original files are preserved. Training, LoRA and multi-shard import are absent.
- Completion requires runtime success plus valid tags/show metadata. Install
  never chooses a default or loads weights into VRAM.
- Cancel closes the owned HTTP subscription, including socket shutdown while
  waiting. Ollama's shared downloader cancels the transfer only when its last
  subscriber leaves (v0.34.4 download.go). Another runtime client can retain the
  shared download. Partial blobs may be reused by explicit retry; no manual blob
  directory cleanup is attempted. Total limit is checked as manifest layers
  appear, not a guarantee of zero transient bytes over the limit.
- Delete requires confirmation and rejects assigned roles/chats, active tasks
  and installs. It uses the existing resource lock, unload and runtime delete;
  it does not erase chats, voice weights or source GGUF files.

The runtime's disk location can be external to Onyx; free-space preflight uses
OLLAMA_MODELS or the standard local model directory. Nonstandard service storage
must be checked separately. The existing coordinator remains sequential and
retains >=8 GiB RAM reserve, STT reservation and VRAM/offload guards. No new
embedding switching/index migration UI exists; existing memory remains intact.

## Network and Git scope

New profiles start Only local. Existing explicit/auto choices are preserved;
the UI offers Only local / Local + Internet. Online enables public web retrieval
and explicitly requested downloads/push; it never enables cloud inference.
Managed web, pull, push and unverified external/cloud coding routes reject
offline at the backend. Missing STT/TTS assets no longer auto-download on startup.
Changing offline cancels managed pull subscriptions, search helpers and owned
Git network processes. A push already accepted remotely cannot be rolled back.
This is not a Windows firewall: an external terminal, other application or other
subscriber to the shared runtime manages its own network. Terminal opening is
an explicit user action, with a visible scope statement in the app.

Git requires an explicitly chosen repository root. Status/diff -> checked files
-> message -> expiring reviewed receipt -> local commit. Unrelated staged files,
secrets/binaries/large files, renames and unreviewed hooks/filters/signers are
refused. Push uses a separate receipt for exact root/head/branch/remote URL and
the outgoing commits against the local tracking ref. No fetch, repository
creation, force-push or automatic publication. Credentialed URLs are rejected;
gh authentication remains the user's system flow. No token is stored by Onyx.

## API/persistence map

| Visible action | Backend | Durable owner |
|---|---|---|
| Model selector/default | models.select/default | SQLite chat selection / backend settings |
| Installed/loaded list | models.list/refresh | Runtime metadata; no new weights |
| Download/import/progress | models.inspect/install/list | desktop_model_jobs in data/kk.db |
| Cancel/retry/delete | models.cancel/retry/delete | Same job registry and runtime |
| Chat send/model label | desktop.submit/history | desktop_model_turns + existing messages |
| Appearance/sections | Client preference queue | Existing per-user preferences.json |
| Internet/voice/data | settings.save | Existing desktop-settings.json |
| Live/dictation | voice.live/stopLive; dictation.* | Existing loop and conversation |
| Apps discovery/Terminal | apps.list/open | Actual executable discovery, no fake adapters |
| Git review/commit/push | git.* | Chosen git_project; in-memory expiring receipts |

Actual test reports and limits are in DESKTOP_ACCEPTANCE.md. No user repository
was published as part of acceptance.
