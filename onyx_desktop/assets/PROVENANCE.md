# Onyx visual assets

Canonical user-supplied artwork: `design/reference/onyx-desktop-approved.png`,
1536×1024, SHA-256 `2a95ab5d834760d0b84bab4e3d68fba64283813890054e86d6cde3df0a522382`.
The user explicitly authorized extracting clean decorative fragments for Onyx.
No independent third-party license or authorship claim is inferred for that PNG.

`scripts/prepare_reference_assets.py` reproduces the following offline:

- `pearl.png`: clean logo decoration crop (66,100)-(222,238), feathered edges.
- `planet.png`: (283,78)-(1159,560). Text regions (383,260)-(824,437) and
  (442,468)-(1004,505) reconstructed with a harmonic fill from boundary pixels.
  Visible planetary arc, curvature and stars outside those masks are original.
- `center-lower.png`: clean empty center background (283,702)-(1159,842).
- `waves.png`: clean Live decoration (1178,562)-(1513,952).
- `live-ring.png`: decorative annulus (1189,196)-(1506,484), feathered exterior;
  the microphone glyph is removed and center made transparent.
- `composer-ring.png`: annulus (1046,852)-(1132,938), glyph removed similarly.
- `mic-glow.png`: original procedural transparent blue glow, no UI or lettering.

All text, controls, panels, card borders, list rows and composer are real QML.
No full reference image is bundled or loaded at runtime. The glyph in each mic
button is live SVG content, separate from its decorative ring.

`icons/*.svg` are original outline paths drawn for this implementation; usable,
modifiable and redistributable with Onyx. They are not a downloaded icon set.
`title.frag` is original static gradient shader, compiled by pinned Qt qsb;
software-rendering fallback uses plain lavender text. Prior `onyx*.svg` are
retained historical original artwork, no longer used by the current UI/icon.

Segoe UI and Consolas are locally installed system fonts, not redistributed.
The source font is unknown. Qt/PySide and Python notices are in ../NOTICES.md.

## Compact pass 2026-09-28
The existing pearl.png crop is restored at 96 logical pixels in welcome, matching
the existing ICO build source. No image was regenerated or enlarged into a banner.
The seven newly described references were not attached; exact reference-4 matching
cannot be certified. The available historical approved PNG was visually inspected.
The graphite pass had replaced the bitmap with onyx.svg; that vector substitution
is removed. The small welcome animation blends the same original image only.
New menu icons edit/pin/trash are Lucide 0.468.0, ISC, with pinned source URLs and
modified-byte hashes in icons/manifest.json. Existing icons retain their recorded
provenance. The earlier blanket claim that all icons were original is superseded.

## Canonical black desktop, 2026-09-29 (supersedes earlier logo claims)

`onyx-original.png` is the exact new user attachment, 1254×1254. The current
shared Pearl.qml uses its pixels with max-channel transparency; no replacement
shape is generated. `scripts/render_brand_asset.py` renders that same QML into
transparent `pearl.png` for static Windows/tray/ICO use. The image-generation
trial was rejected and is neither used nor redistributed. Historical planet,
ring and wave assets remain for compatibility but are not the default backdrop.

Publisher marks identify third-party products, without endorsement or a claim
that their trademark rights are licensed under Onyx's source license:
- Qwen: https://github.com/QwenLM ; https://avatars.githubusercontent.com/u/141221163?v=4
- OpenAI: https://github.com/openai ; https://avatars.githubusercontent.com/u/14957082?v=4
- Anthropic: https://github.com/anthropics ; https://avatars.githubusercontent.com/u/76263028?v=4
- OpenCode square mark: https://github.com/anomalyco/opencode/blob/7945de208964a49300d7f770d1a71d078db9a4c4/packages/console/app/src/asset/brand/opencode-logo-dark-square.png
  MIT source notice retained in LICENSE-OPENCODE.
Codex/Claude/Qwen cards use the publisher marks, not an invented application logo.
Terminal, apps and info outlines are original neutral icons. Unknown/custom
models use a neutral mark; family/name matching is classification, not signed
publisher authentication. Downloaded files are local; there is no runtime CDN.

SHA-256 at acceptance:
- `onyx-original.png`: `ca36e32ad593625593c717d00eb9b74490e1cecf53ad265b095aa474bea43a68`
- `pearl.png`: `a70243d1547ef785a762dd228a2fcbecc4529235989c8cadf5d6307ecd7126d0`
- `qwen.png`: `a6b261b9b688a81e984c20411dadd616215287e90a31e9c5c65fdefbc09f12b3`
- `openai.png`: `6025a4347a8eaed17e31eaebf7834e33ec4af26cc7f59be586ac59ba5157fa1c`
- `anthropic.png`: `4124b8d03b154d23afe1d6b9a0b9b7813c5b40407d8d039c38d6ff3b2e923239`
- `opencode.png`: `44d24a1c9e7e2af1f6551bd808b1330e2493e233a173c5930b300b70290e1b57`

## Product rebuild assets
Hermes (Nous Research), Cline, Oh My Pi and DeepSeek Harness use upstream
repository PNGs. Exact commit URLs, licenses and content hashes are in
app-sources.json; LICENSE-* files retain their terms. These marks identify
products and do not imply endorsement. Codex CLI deliberately uses the neutral
terminal fallback, distinct from the OpenAI model-family mark; the current
Codex CLI source tree exposed no redistributable product icon. OpenClaw, Droid,
Pi and Copilot also use a clearly labelled neutral fallback where icon terms
were not verified. No remote image loading occurs at runtime.

## Final rescue source audit, 2026-09-29

This section supersedes the preceding OpenClaw/Pi/Copilot fallback statement.
Exact revisions, original-byte hashes and distributed-byte hashes are recorded
in `app-sources.json`. These are product identifiers, not Onyx branding or an
endorsement claim. Assets load from the installed bundle, never a runtime CDN.

- `openclaw.svg`: original official OpenClaw UI favicon at revision
  `dc7fe2a2805f72e0d5c729db2aab55b1de86b568`. MIT license is retained in
  `LICENSE-OPENCLAW`; no shape or color edits.
- `pi.svg`: faithful SVG representation of the exact 4 by 4 terminal pixel
  layout and fixed coral/blue/yellow colors in the official Pi `piLogoLines`
  source at revision `cb7969d212836b8939001dce159fbd2ed6ad395f`. This is an
  adaptation of the supplied code-native logo, not a newly designed mark.
  MIT notice retained in `LICENSE-PI`.
- `copilot.svg`: official GitHub Octicons `copilot-24` at revision
  `90af1f14984832de34e94b2d530043fbcf85eb7f`, MIT (`LICENSE-OCTICONS`). Path
  geometry is unchanged; foreground is set to `#f0f0f0` for the black UI.
  Use as a supporting icon beside the explicit GitHub Copilot name. The
  [current Copilot brand guide](https://brand.github.com/brand-identity/copilot)
  does not present the old standalone Copilot icon as the product's primary
  logo. No new lockup or endorsement is implied.
- `huggingface.svg`: unmodified official logo at dataset revision
  `233827f481bf0f0f413795f1f3cd1b131f77e55a`.
  [Hugging Face brand assets](https://huggingface.co/brand) explicitly provides
  these files for project use. Trademark rights remain with Hugging Face;
  the logo is not claimed to be MIT-licensed. Used to identify that model family.

## Final publisher-identity completion, 2026-09-29

This section supersedes the neutral Codex/Droid and Meta/DeepSeek fallback
statements above. It changes static asset mappings only. The requested product
and model names remain explicit, and installation/connection states remain real.
These marks identify the external publisher/product and do not imply sponsorship
or an Onyx partnership. Trademark ownership is retained by the publisher; the
marks are not claimed to be covered by any repository's MIT source-code license.
See `NOTICE-PUBLISHER-MARKS` and the exact-byte entries in `app-sources.json`.

- Codex CLI uses the existing unmodified `openai.png` beside **Codex CLI** and
  the **OpenAI** publisher label. This is publisher identity, not a newly
  invented Codex app logo or the unverified purple mark in the recovered mockup.
  [OpenAI brand guidance](https://openai.com/brand/) is retained as the terms source.
- The original `factory.svg` is unchanged from
  [Factory's official favicon](https://factory.ai/favicon.svg), also served
  identically by [Factory documentation](https://docs.factory.ai/favicon.svg).
  SHA-256: `416ea4962d7b0b8be8bec7f7190c13c22d5f20fdcac401aa72886fd5c81d2fb2`.
  Qt considers that nested SVG valid but renders it completely transparent.
  Droid therefore displays `factory-qt.svg`: the exact inner SVG promoted to
  the document root, with the unresolved clip-path reference removed. Every
  original path, fill color, viewBox and proportion is retained. Its distributed
  hash is recorded separately; the original is still bundled. Actual rendering
  tests now check visible pixels and light mark pixels on the dark background,
  rather than relying only on the SVG parser's valid flag.
- DeepSeek model-family identity uses `deepseek.png`, the unchanged blue whale
  avatar linked by the [official DeepSeek organization](https://github.com/deepseek-ai),
  numeric organization ID 148330874. This is a separate source from the
  DeepSeek Harness app icon; it was visually inspected before inclusion.
  SHA-256: `55e6e0c1ba0c453749211368b8a26e00f470b4ab696ce1fed539d70777d4aab1`.
- Meta/Llama model-family identity uses `meta-llama.png`, the unchanged Meta
  symbol linked by the [official Meta Llama organization](https://github.com/meta-llama),
  numeric organization ID 153379578. The original colors, aspect ratio and
  whitespace are preserved. [Meta brand guidance](https://www.meta.com/brand/resources/meta/company-brand/)
  remains the terms source; no partnership or separately obtained endorsement
  is claimed. This asset was visually inspected before inclusion.
  SHA-256: `3876b393102ffb040fbac5d953f1ec01586d91932634052c8662376e60b0ca57`.

Organization-avatar URLs are versioned by their provider but may change later;
the locally distributed byte hashes are the exact reviewed snapshot. No runtime
CDN lookup occurs. Unknown/custom models continue to use a neutral marker.
