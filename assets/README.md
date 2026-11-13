# Assets

Real assets are provided separately and are not in git. `python scripts/make_placeholder_assets.py`
fills any missing file with a placeholder (test patterns, a sine tone, a system font) so the
pipeline can run end to end. It never overwrites a file that exists, so drop real files in and
delete placeholders you're replacing.

The app checks every file below at startup and refuses to start, listing what's missing.

```
assets/
  templates.json                 [{id, label, folder}]: one entry per template (in git)
  templates/men/A.mp4 B.mp4 C.mp4
  templates/women/A.mp4 B.mp4 C.mp4
  intro.mp4                      3 s branded intro
  endcard.mp4                    3 s end card
  music.mp3                      at least 25 s (shorter tracks loop)
  font.ttf                       used for the watermark
  plaque.png                     the award plaque photo with an empty nameplate
  plaque.json                    nameplate box in plaque.png pixels, engraving font + colour (in git)
  plaque_font.ttf                font the name is engraved in
  logos/*.png                    official logos, transparent PNG
  logo_positions.json            logos overlaid on clip A, per template
```

## Template clips

- 5 s each, 720x1280 (9:16), 30 fps, H.264. Other sizes and frame rates are normalised, but
  matching the spec avoids surprises.
- One clear main performer per clip. Wan replace mode swaps the visitor in for that person and
  keeps the motion, scene and camera. Pastor Chris appears only in these approved clips and is
  never regenerated.
- Clip A must have a static camera, because the logos are overlaid at fixed positions.
- Short clips hold their last frame up to 5 s. Long clips are cut at 5 s.

## logo_positions.json

Coordinates are in pixels on the 720x1280 frame. `width` is the logo's rendered width; height
keeps the aspect ratio.

```json
{
  "men":   [{"file": "loveworld.png", "x": 40, "y": 60, "width": 220}],
  "women": [{"file": "loveworld.png", "x": 40, "y": 60, "width": 220}]
}
```

A template with no entry gets no logos.

## Adding a template (no code change)

1. Add `templates/<folder>/A.mp4`, `B.mp4` and `C.mp4`.
2. Add `{"id": "...", "label": "...", "folder": "templates/<folder>"}` to `templates.json`.
3. Optionally add logo positions for the new id.
4. Restart the service. The capture page shows the new option.

## plaque.json

```json
{"image": "plaque.png", "nameplate": [345, 598, 882, 670], "font": "plaque_font.ttf",
 "text_color": [43, 28, 8], "uppercase": true}
```

`nameplate` is `[left, top, right, bottom]` in `plaque.png` pixels: the empty area the name is
engraved into. If you swap in a different plaque photo, update this box.

The fonts must cover every character visitors' names use. If a glyph is missing, it renders as a box.
