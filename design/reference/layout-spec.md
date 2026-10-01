# Approved Onyx reference measurements

Source: user-supplied 1536×1024 PNG, SHA-256
`2a95ab5d834760d0b84bab4e3d68fba64283813890054e86d6cde3df0a522382`.
The copied bytes are unchanged. No older image is a design source.

## Coordinate system and measured edges

Window crop: `[8,11,1528,975)` = **1520×964 physical pixels**.
Preview: 1520×964 Qt logical pixels at measured DPR=1. Translation from original
PNG to comparison: `(x-8,y-11)`. No independent block scaling or registration.
Qt grab captures the client scene, including our live title strip, excluding OS
shadow. Presentation glow outside the reference crop is deliberately excluded.

| Element | PNG rectangle x,y,w,h | Notes |
|---|---|---|
| Title strip | 8,11,1520,50 | bottom rule y60 |
| Left panel | 25,77,243,878 | rounded border |
| Center | 282,77,878,880 | panel bottom differs by ~2 px |
| Live | 1177,77,337,879 | 17 px center gap |
| Composer | 303,843,836,101 | radius ~18 |
| Input | 402,860,623,67 | radius ~17 |
| Plus circle | 322,863,61,61 | center 352.5,893.5 |
| Cards | 309/518/727/935,567,197–198,134 | gaps 12–13, radius ~16 |
| Logo decoration | 66,100,156,138 | no embedded letters |
| Live ring | center 1347,353, radius ~95 | glow extends ~157 px |

Measured greeting visible top ~270, baseline ~298; title visible top ~322,
baseline ~380 (descender extends ~396); subtitle baseline ~417. Text starts x390.
Tag row baselines ~492; card text baselines ~648 and ~673. Icon centers around
y603. Composer placeholder baseline ~900. Values are observed raster bounds,
not original font metrics; individual anti-aliased edges vary by 1–2 px.

## Sampled colors and estimated style

Median RGB from clean rectangles, extraction script records coordinates:
window top (15,28,49), left lower (7,12,20), center lower (6,11,19), Live lower
(11,18,31), card interior (13,19,33), input (19,26,41).
Borders roughly #202b40–#3b455f, 1 px; panel radius about 17 px.
Text and border colors vary spatially in the artwork. Tokens use representative
colors rather than claiming exact source values.

Source font unknown. **Segoe UI**, locally installed Windows system font, chosen
for similar widths and Cyrillic; no font redistributed. QML default system font
is fallback if absent. Greeting 38 px normal, title 77 px bold, subtitle 20 px,
cards/nav 18 px, small list 14 px. Code uses local Consolas. These font choices,
weights and line metrics are estimates, not recovered original design data.

## Assets and reconstruction

See `onyx_desktop/assets/PROVENANCE.md` and `scripts/prepare_reference_assets.py`.
Only decoration is sampled. Original text/buttons/panels are never runtime
bitmaps. The planet's hidden text areas are harmonic fills in explicit masks;
there are no surviving reference glyphs. All outside-mask planet pixels remain
original. SVG outline icons are newly drawn, so their small shape differences
remain visible in the difference image.

The before image is the previous production QML at the same logical size with
isolated fixture data; its original native title bar lies outside Qt grab.
The running old executable was also inspected with Windows Computer Use.

Qt references: [DPI](https://doc.qt.io/qt-6/highdpi.html),
[grabWindow](https://doc.qt.io/qt-6/qquickwindow.html#grabWindow),
[performance](https://doc.qt.io/qt-6/qtquick-performance.html).
