# Existing upstream components and model provenance

The initial inventory below predates this task. See the Graphite desktop additions
section for the newly pinned SVG/search/extraction components. The voice environment
was not upgraded; final pip checks passed in voice, build and web environments.

Verified 2026-09-28 against the running Ollama metadata API, installed distribution metadata, the captured environment, and primary upstream license sources. This integration reuses the existing local stack: **no new agent runtime, GUI stack, model download or package installation** was performed for this inventory. No third-party source was copied into the integration changes.

## Software (licenses apply to code, not automatically to model weights)

| Component / installed version | Onyx use and integration changes | Upstream license / source |
| --- | --- | --- |
| Ollama **0.34.4** | Existing local Brain, vision and embedding service; adapter fixes for payload, streaming and resource transitions. Version read from `/api/version`, without inference. | [MIT — Ollama LICENSE](https://github.com/ollama/ollama/blob/main/LICENSE) |
| faster-whisper **1.2.1** | Existing final STT and Russian/mixed speech; retain Whisper turbo and cache-first loading. | [MIT — faster-whisper LICENSE](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE) |
| CTranslate2 **4.8.2** | Existing CUDA `int8_float16` Whisper execution; no engine update. | [MIT — CTranslate2 LICENSE](https://github.com/OpenNMT/CTranslate2/blob/master/LICENSE) |
| PySide6, Qt, Shiboken **6.11.2** | Existing Qt Quick/D3D11 process reused for Edge; original Onyx QML/shader and small Win32 adapters. | Installed PySide6 metadata: `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`; [Qt licensing](https://doc.qt.io/qt-6/licensing.html), [Qt for Python third-party notices](https://doc.qt.io/qtforpython-6/licenses.html). Module-specific terms/notices remain with the existing distribution. |
| `uiautomation` **2.0.29** | Existing Windows accessibility backend; fresh targeting and own-window exclusion. Windows UIA itself is an OS API, not a new installed agent. | [Apache-2.0 — Python-UIAutomation-for-Windows LICENSE](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows/blob/master/LICENSE) |
| `pycaw` **20260921** | Existing Windows audio-session ducking/restore. | [MIT — pycaw LICENSE](https://github.com/AndreMiras/pycaw/blob/main/LICENSE); confirmed in installed wheel license file. |
| Silero loader | Onyx's existing `torch.package` adapter loads a cached model package; no `silero` pip package or PyTorch Hub code checkout added. PyTorch's license does not establish the model license. | Model package terms are listed separately below; [upstream Silero repository LICENSE](https://github.com/snakers4/silero-models/blob/master/LICENSE). |

The full installed Python environment is pinned in `requirements-lock.txt`: **66 exact direct/transitive pins**, Python **3.12.10**, Windows x64. Generated from `data/integration/environment-freeze.txt`, checked against installed metadata; no editable installs, local paths, credentials or direct-URL requirements. It includes the existing CPU Torch wheel and NVIDIA STT dependencies. This is a version snapshot, not a cross-platform resolver output or wheel-hash lock. The official CPU wheel index is noted in its header; no installation was run.

## Model weights (separate provenance)

| Installed model / role | Weight license evidence | Local identity |
| --- | --- | --- |
| `qwen3:14b` — Brain, Q4_K_M | Local `/api/show` contains Apache-2.0; [Qwen3-14B upstream LICENSE](https://huggingface.co/Qwen/Qwen3-14B/blob/main/LICENSE). | Ollama digest `bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8` |
| `qwen3-vl:8b` — vision, Q4_K_M | Local `/api/show` contains Apache-2.0; [Qwen3-VL 8B upstream model card](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct). | Ollama digest `901cae73216286ea8c5aba8b46d307ff7188f737285ec500c795a12f05225d28` |
| `qwen3-embedding:0.6b` — memory embedding, Q8_0 | Local metadata has no license text; the [Qwen3-Embedding-0.6B upstream card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) declares Apache-2.0. | Ollama digest `ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d` |
| Whisper **large-v3-turbo** — STT | OpenAI states both [Whisper code and weights are MIT](https://github.com/openai/whisper#license). The installed faster-whisper mapping uses `mobiuslabsgmbh/faster-whisper-large-v3-turbo`, whose [current converted-model card](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) redirects to Dropbox Dash and declares MIT. | Existing CTranslate2 cache; no model conversion/download during this inventory. |
| Silero **v4_ru**, voice **baya**, CPU — TTS | Upstream [models.yml](https://github.com/snakers4/silero-models/blob/master/models.yml) maps `v4_ru` to the configured package. The [model licensing section](https://github.com/snakers4/silero-models#licence) applies the repository license except to named CIS base models. The actual [LICENSE text](https://raw.githubusercontent.com/snakers4/silero-models/master/LICENSE) is **CC BY-NC-SA 4.0**, despite abbreviated README badge wording. `v4_ru` is not the CIS-base MIT exception. | Cached package: 40,107,184 bytes; SHA-256 `896ab96347d5bd781ab97959d4fd6885620e5aab52405d3445626eb7c1414b00`. |

Silero's current published V4 terms are recorded above; the cached Torch archive contains no separate LICENSE/NOTICE member. Its historical acquisition terms were not embedded or independently archived. The model is retained for this local personal companion; this document does not relabel its weights or packaged model code as MIT, and does not grant commercial redistribution rights. No model weights are included in Git.

## Scope and privacy

Ollama and the authenticated Onyx control transport remain local; Qt is a local child process. No external Brain, cloud-default memory service, browser profile/cookie copying, or new telemetry service was introduced. Telemetry environment switches and final startup policy are documented with launcher changes; this inventory does not claim that an upstream service's telemetry was independently audited.

Windows-MCP, UFO, Agent-S, UI-TARS, Pipecat, browser-use, OpenHands and Mem0 were not installed as additional runtimes in this integration. Primary documentation was used to adapt the existing contracts rather than adding parallel coordinators. Validation and unresolved live checks are in `ONYX_INTEGRATION_REPORT.md` and `ONYX_REMAINING_ISSUES.md`.

## Final-pass verification (2026-09-28)

The final pass installed no packages, platforms, models or frameworks. Local distribution metadata reconfirmed Python 3.12.10, faster-whisper 1.2.1, CTranslate2 4.8.2, PySide6 6.11.2, uiautomation 2.0.29 and pycaw 20260921. `python -m pip check` returned `No broken requirements found.` Existing pins and model provenance above were retained; this pass did not re-audit changing upstream license pages. The own WPF fixture uses the already installed Windows/.NET PresentationFramework; its UTF-8 BOM was fixed for Windows PowerShell, without installing a platform.

Computer Use was used externally by the tester to inspect permitted non-browser windows and send fixture input. It is **not an integrated Onyx dependency**. Firefox's previously reported URL-policy limitation was not bypassed. Documentation-only references to Windows-MCP, UFO, Agent-S, UI-TARS, Pipecat, browser-use, OpenHands and Mem0 remain documentation-only. See the integration report and issue ledger for the measured source revision and live/offline distinction.
# Graphite desktop additions (2026-09-28)

- Lucide **1.48.0**, ISC license, selected SVG files only. Source URLs and SHA-256
  are in `onyx_desktop/assets/icons/manifest.json`; license is adjacent as
  `LICENSE-LUCIDE`. Only currentColor was replaced with neutral #dedede.
  https://lucide.dev/guide/static
- Backend search: DDGS **9.16.0** (MIT), fixed DuckDuckGo HTML provider;
  Trafilatura **2.0.0** (Apache-2.0), extraction only, no crawler. All transitive
  dependencies are pinned in requirements-web-lock.txt and isolated in .venv-web.
  Upstream licenses remain in installed wheel metadata. This environment is not
  included in the frozen desktop and does not change voice dependencies.
  https://github.com/deedy5/ddgs ; https://trafilatura.readthedocs.io/
- Search is an unguaranteed third-party service governed by
  https://duckduckgo.com/terms. No cookies, CAPTCHA bypass, paid LLM fallback,
  browser-session access or distributed search mesh is used.
- Existing original vector Onyx marks (onyx.svg/onyx-light.svg) are reused with
  transparency; old reference-derived blue bitmaps are no longer UI backdrops.

## Compact desktop references, 2026-09-28
Qt 6.11 Menu/MenuBar/QWindow and Qt Quick performance/profiling documentation
reviewed. Context menu explicitly uses Popup.Item, shared native QML Actions,
and startSystemMove; no code copied from a second client stack.
GPT4All ChatView.qml (https://raw.githubusercontent.com/nomic-ai/gpt4all/main/gpt4all-chat/qml/ChatView.qml)
reviewed as structural reference only. Chatbox repository and GPL-3.0 LICENSE
(https://raw.githubusercontent.com/chatboxai/chatbox/main/LICENSE) reviewed;
no Chatbox code copied, no Electron/runtime or cloud defaults introduced.
Lucide additions pinned at 0.468.0 (pencil/pin/trash-2), ISC; manifest URLs and
SHA256 of modified bytes and bundled LICENSE-LUCIDE retained. Existing 1.48.0
manifest entries remain untouched. SQLite backup uses https://sqlite.org/backup.html.

## Models / black desktop, 2026-09-29
Ollama runtime inspected: 0.34.4, local API. Contract references:
https://docs.ollama.com/api/tags , https://docs.ollama.com/api/ps ,
https://docs.ollama.com/api/pull , https://docs.ollama.com/api/create ,
https://docs.ollama.com/import . Upstream v0.34.4 api/types.go and
server/download.go were inspected for blobs and cancellation semantics.
No Ollama source copied or weights redistributed in the GUI.
Qt 6.11 Context2D putImageData requires the dirty-rectangle arguments used by
Pearl.qml: https://doc.qt.io/qt-6/qml-qtquick-context2d.html .
Git uses installed Git for Windows with reviewed scope, following
https://git-scm.com/docs/git-commit and https://git-scm.com/docs/git-push .
New mark provenance, pinned OpenCode MIT source and exact hashes are recorded
in onyx_desktop/assets/PROVENANCE.md. These supersede prior logo descriptions.
No new cloud inference SDK or telemetry dependency was added.
