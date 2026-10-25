# Editor beginner tutorial

> New to PhotoCrate's editor? Spend five minutes on this page and you'll know where the everyday tools are.
> The app has the same walkthrough built in: it starts automatically the first time you open the editor, and you can replay it any time from **Help → Beginner Tutorial**.

**Relax first**: editing never changes your original photo. If something goes wrong, press **Ctrl+Z**; if you don't like any of it, just close the window and choose **Don't save** — nothing happened.

> This page describes the English interface. To switch languages: **Settings → Appearance → 界面语言 · Language**, then restart.
> 中文版教程：[修图新手教程.md](修图新手教程.md)

---

## 1. Opening the editor

Any of these works:

1. Go to **05 Culling** in the left navigation, select a photo in the grid, and click **Edit…** at the top;
2. Right-click a thumbnail on any page → **Edit (layers / adjustments / retouch)…**;
3. Press **E** in the full-screen viewer.

Videos can't be edited. RAW-only photos open too (using the camera's embedded full-size preview).

---

## 2. What's in the window

![Editor window areas](screenshots/en/editor_guide.png)

| # | Area | What it's for |
|---|---|---|
| ① | **Toolbar** (the strip on the far left) | All tools. Hover over an icon to see its name and how to use it. The two squares at the bottom are the **foreground / background colors**; click to change them |
| ② | **Options bar** (along the top) | Settings for the current tool: brush size, hardness and opacity; crop ratio, straighten angle… |
| ③ | **Canvas** (center) | Your photo. Scroll to zoom, hold Space and drag to move around |
| ④ | **Properties** (top right) | Settings of the selected layer. **All the color sliders live here** |
| ⑤ | **Layers** (middle right) | All layers, the eye (show / hide), blend mode, opacity, and buttons to add / copy / delete layers |
| ⑥ | **History** (bottom right) | Every step you've taken; click a row to go back to that step |
| ⑦ | **Image** and other menus | Adjustments, filters, selection, rotation, image size and more |
| ⑧ | **File** menu | Export image, save project |

The **Histogram** in the top right shows the photo's brightness distribution; it warns "Shadows clipped / Highlights clipped" when large areas are pure black or pure white.

### The toolbar tools you'll use most

| Position | Tool | Shortcut | What it does |
|---|---|---|---|
| 1st | Move | V | Drag the current layer (added text, pasted images) |
| 2nd–4th | Marquee / Lasso / Magic Wand (Object Selection shares its slot) | M / L / W | Mark out an area so the next operations only affect it. **Object Selection** is the easiest: draw a box around a person or object and it's selected along its edges |
| 5th | Crop | C | Cut away extra parts, straighten a tilted horizon |
| 7th | Spot Healing Brush (Patch and Red Eye share the slot) | J | Paint over spots, dust or wires and they're gone |
| 8th | Brush | B | Paint with the foreground color; painting on a mask controls where an effect shows |
| 9th | Clone Stamp | S | Alt+click to sample a spot, then paint it somewhere else |
| 10th | Eraser | E | Erase to transparency |
| 12th, 13th | Blur / Sharpen / Smudge, Dodge / Burn / Sponge | R / O | Local smoothing and sharpening, local brightening and darkening (right-click or Shift+R / Shift+O to switch) |
| T icon | Text | T | Add text to the photo |

---

## 3. The three things you'll do most

### 1. Adjust color (the most common)

1. Click **◐ Adjust** in the Layers panel (or the **Image → Adjust** menu). An "Adjustment" layer appears in the layer list.
2. A set of sliders appears in **Properties** on the right:
   - **Basic**: Exposure (overall brightness), Contrast, Highlights (darken the sky), Shadows (lift dark areas)
   - **Color**: Temperature (cooler / warmer), Tint, Vibrance, Saturation
   - Further down: **Curves, HSL (adjust one color on its own), Clarity, Sharpen, Noise Reduction, Vignette, Grain**…
3. Not sure where to start? Click **Apply preset…** at the top of Properties and pick **Japanese airy, Film, Cinematic, High-contrast B&W**, etc. for an instant look, then fine-tune a few sliders.
4. To compare before / after: **hold the `\` key** (or the **Hold to see original** button at the bottom right); release to go back.

> Adjustment layers never change the photo itself. Effect too strong? Lower the layer's **Opacity** at the top of the Layers panel; don't want it any more? Click the eye to hide it, or delete it.
> Double-click the name to the left of a slider to reset that slider.

![The built-in beginner tutorial](screenshots/en/editor_tour.png)

### 2. Remove blemishes

- **Small things** (spots, sensor dust, thin wires): select the bottom **Background** layer in the Layers panel, press **J**, and paint over the blemish — it's patched when you release. Adjust the brush size in the options bar; slightly larger than the blemish works best.
- **Big things** (passers-by, trash cans): press **L** and draw around it with the Lasso (a little generously), then **Edit → Content-Aware Fill** (Shift+Backspace). If the result isn't good, Ctrl+Z and try a different outline, or touch up with the healing brush.
- **Regular textures** (walls, grass, water): use the Clone Stamp **S**: Alt+click a clean area, then paint over what you want to cover.

### 3. Crop and straighten

1. Press **C** and a crop box appears.
2. Choose a **Ratio** in the options bar (1 : 1 for avatars and social posts, 4 : 3 or 16 : 9 for landscape shots), or keep **Free**.
3. Drag the edges and corners to resize; drag inside the box to move it.
4. Tilted horizon? **Hold Ctrl and drag a line along the horizon** — it straightens automatically when you release. You can also drag the **Straighten** slider in the options bar.
5. Press **Enter** (or double-click inside the box) to finish. Not happy? Ctrl+Z.

---

## 4. A little more

### Adjust only part of the photo (e.g. brighten just a face)

1. Press **W** (Shift+W switches to Object Selection) and box the person (or draw an area with M / L). The flashing dashed line is the selection.
2. Click **◐ Adjust** to create an adjustment layer — the selection automatically becomes the layer's **mask**, so the adjustment only affects the selected area.
3. Edges too hard? Use **Select → Feather…** (Shift+F6) before creating the adjustment layer.

Mask rule: **white = effect on, black = effect off**. Click the mask thumbnail on the layer, then paint on the photo with a black brush to remove the effect where you paint.

### Add text

Press **T**, click on the photo, then type the text and choose the font, size and color in the dialog. You can add an **outline** and a **shadow**. Text can be changed any time: select the text layer and edit it in Properties on the right, or click it again with the Text tool.

### Cut-outs and compositing

1. Box the subject with Object Selection (**W**, Shift+W to switch), then press **Ctrl+J**: the subject is copied to a new layer.
2. **File → Place Image (as a new layer)…** (or copy an image elsewhere and press **Ctrl+V**) to bring in another picture.
3. **Ctrl+T** free transform: drag the corners to scale, drag outside the box to rotate, Enter to apply.
4. Use ↑ ↓ in the Layers panel to change the stacking order.

### Merge exposures (HDR): several exposures into one photo

Backlight, sunsets, a window seen from indoors: the sky is bright and the ground is dark, and no single photo gets both. Shoot a **bracket** with your camera's AEB (for example 3 shots at −2 / 0 / +2 EV) and let PhotoCrate combine them into one photo with detail in the clouds and in the shadows, at a normal exposure.

1. In **05 Culling**, Ctrl+click to select the shots and click **Merge HDR…** (or right-click a thumbnail → **Merge Exposures (HDR, 3 photos)…**). For photos that aren't in the library, use **File → Merge Exposures (HDR)…** in the editor and pick the files.
2. The defaults are usually right; click **Merge**:
   - **Reference**: the middle exposure is picked automatically. It is never resampled, so its sharpness is kept exactly; anything that moved follows this frame.
   - **Deghosting**: Normal. Where people, leaves or water moved between shots, only the reference is used, so no see-through "ghosts" appear.
   - **Auto-align**: handheld shots are a little out of register and get aligned to about 0.1 pixel; tripod shots that didn't move aren't resampled at all.
   - **Keep the source photos as hidden layers**: leave it on; Fix Ghosting needs them.
3. The result opens in the editor. The top layer is **Merged result**, with each aligned source below it (the reference visible, the others hidden). Hold `\` to see the reference as shot, for comparison.
4. Then adjust and retouch as usual, and **Export Image** (named "reference_HDR.jpg" by default) or **Save Project**.

**Still see a ghost?** Draw around it with the Lasso **L** (a little generously), then **Image → Fix Ghosting (from one source photo)…**. Each source gets a preview of how the patch will look; click the cleanest one and choose **Patch**. The patch goes on a new layer with a mask, with its brightness matched to the merged result; refine the edge by painting on the mask, or delete the layer if you don't want it. Skip any source marked "clipped here, no detail".

> Tip: use a tripod or hold steady, and use the camera's burst bracketing (the shorter the gap between shots, the less moves). RAW-only photos are merged from the camera's embedded full-size preview.

### Pro editing: RAW, 16-bit, groups, transforms, retouching tools

**Camera Raw.** For RAW-only photos, **Edit…** first opens the **Camera Raw** dialog: white balance (As Shot / Auto / Daylight, or click the **eyedropper** and then something that should be gray), exposure, highlights (**Reconstruct** recovers part of a blown sky) and noise reduction. The preview is on the left; click **Open** to get a **16-bit** image. Photos with both JPG and RAW still open the JPG; to use the RAW's latitude, choose **File → Re-develop RAW…** and the result arrives as a new layer.

**16-bit.** When the title bar says "(16-bit)", strong Curves and Levels moves won't band smooth gradients (sky, skin). Ordinary JPGs open at 8 bits; before heavy grading choose **Image → Mode → 16 Bits/Channel**. When exporting PNG / TIFF, tick "Save as 16-bit" to keep editing elsewhere.

**Groups.** Lots of layers? Select one and press **Ctrl+G** to put it in a group; the little arrow collapses it. **Ctrl+[ / Ctrl+]** move layers into and out of groups. A group's blend mode defaults to Pass Through; switch it to Normal and adjustment layers inside only affect the group.

**Clipping masks (adjust just one layer).** To make an adjustment layer affect only the image right below it, select the adjustment layer and press **Ctrl+Alt+G** (or **Clip** in the Layers panel); it indents with a bent arrow. Pixel layers can be clipped too — e.g. show a texture only inside the text below it.

**Skew, distort, perspective, warp.** After **Ctrl+T**, pick a **Mode** in the options bar:
- **Perspective**: drag a corner and the other corner on that edge moves symmetrically — straighten a leaning building or put a picture on an angled wall
- **Distort**: drag any corner freely (in Free Transform, Ctrl+drag a corner also distorts; Ctrl+drag a side skews)
- **Warp**: a 4×4 grid appears; drag any point to bend the layer
Enter applies, Esc cancels.

**Dodge / Burn / Sponge (O), Blur / Sharpen / Smudge (R).** These two toolbar slots have a small triangle: right-click to switch tools, or press **Shift+O / Shift+R** to cycle. For portraits: Dodge (Midtones, 15–25% exposure) brightens eyes, Burn deepens outlines, Blur smooths skin, Sharpen crisps lashes.

![Camera Raw](screenshots/en/editor_raw.png)

### Hair cut-outs, color changes, reshaping: the second batch of pro tools

**Select and Mask (cut out hair).** Roughly select the person with **Select → Select Subject** or Object Selection **W**, then choose **Select → Select and Mask…** (Ctrl+Alt+R). Raise "Edge detection radius" to 10–30 and strands of hair follow the photo's own edges; in the Overlay view, red is what's not selected. Output to "New layer with layer mask" is the safest choice — the original stays untouched.

**Color Range.** To adjust only one color (say, just the blue of the sky): **Select → Color Range…**, click the sky in the preview (Shift+click a few more spots), set Fuzziness, then create an adjustment layer — it affects only the selected colors.

**Quick Mask (Q).** Press **Q** and everything not selected turns red. Paint white to add, black to subtract, then press Q again to get the selection back — much easier than tracing with the lasso. Save selections you'll reuse with **Select → Save Selection…** and bring them back with Load Selection.

**New adjustments.** **Image → Adjust** now has **Black & White** (control how bright each color becomes in gray — far nicer than plain desaturate), **Color Balance**, **Selective Color**, **Photo Filter**, **Channel Mixer** and **Gradient Map**.

**Patch and Red Eye.** In the Spot Healing slot (right-click or Shift+J): **Patch** — circle what to remove, drag it to a clean area and release; **Red Eye** — click on the red eye.

**Liquify (slim a face, tidy hair).** **Filter → Liquify…** (Ctrl+Shift+X): pick Forward Warp, a brush a bit larger than what you're pushing and 20–40% pressure, and push gently; overdone? Paint it back with Reconstruct.

**Lens Correction.** **Filter → Lens Correction…**: "Remove distortion" fixes wide-angle barrel distortion, "Vignette amount" brightens the corners, and the fringe sliders remove purple / green edges on branches.

**Layer styles.** Select a text or cut-out layer and click **fx** in the Layers panel: tick Drop Shadow, Stroke, Outer Glow… and see it on the canvas right away; change it any time. Set **Fill** to 0% to keep only the stroke / shadow while the text itself turns transparent. The lock buttons in the Layers panel protect a layer's position or pixels.

![Select and Mask](screenshots/en/editor_refine.png)

---

## 5. Saving: two ways, pick what you need

| | Export image | Save project |
|---|---|---|
| Menu | File → Export Image… | File → Save Project |
| Shortcut | Ctrl+Shift+S | Ctrl+S |
| You get | An ordinary JPG / PNG / TIFF / WebP | A `.pcedit` file that keeps all layers |
| Saved to | Next to the original by default, as "name_edited.jpg" | Next to the original, as "name.pcedit" |
| Good for | Finished — ready to post, print or share | Not finished yet — you'll keep editing later |

- Export **keeps the capture time, camera and other info** by default, so exported photos still sort by date and place; tick **Remove GPS location** when posting online.
- When you export into a folder in your library, the app rescans automatically and the new photo shows up right away.
- Next time you edit the same photo and a project file exists, you'll be asked whether to **Open project** and continue, or **Open original** and start fresh.

---

## 6. Troubleshooting

- **The brush does nothing?** Check whether the selected layer is an **adjustment layer** (it has no pixels; you can only paint on its mask), or whether the layer is hidden (eye off). Select **Background**, or click **+ Layer** to create one, and paint again.
- **Lost track and want to go back?** Look at **History** at the bottom right; click the top row, **Open**, to return to how the photo was when you opened it.
- **Picture too big / too small?** **Ctrl+0** fits the window, **Ctrl+1** shows 100% detail, the mouse wheel zooms around the cursor.
- **Can't remember shortcuts?** Press **F1** for the full list; each tool's shortcut is also in its tooltip.
- **Want to see the walkthrough again?** **Help → Beginner Tutorial**.

## 7. Shortcut cheat sheet (the 12 you'll use most)

| Key | Action | Key | Action |
|---|---|---|---|
| Ctrl+Z | Undo | Ctrl+Shift+Z | Redo |
| Space (hold) | Drag the view | Mouse wheel | Zoom |
| `\` (hold) | See the original | Ctrl+0 / Ctrl+1 | Fit window / 100% |
| [ / ] | Smaller / larger brush | X | Swap foreground / background color |
| Ctrl+J | Layer via copy (only the selection, if there is one) | Ctrl+T | Free transform |
| Ctrl+D | Deselect | Ctrl+Shift+S | Export image |
