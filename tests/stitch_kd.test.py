import torch, ast, sys
import os, sys as _s; _s.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "nodes"))
import stitch_kd as S

fails = 0
def check(label, cond, extra=""):
    global fails
    if not cond: fails += 1
    print(("ok   " if cond else "FAIL ") + label + (("  " + str(extra)) if not cond else ""))

def rnd(b, h, w, c, seed=0):
    g = torch.Generator().manual_seed(seed)
    return torch.rand((b, h, w, c), generator=g)

N = S.ImageStitcherKD()

# --- 1. round trip: crop out, stitch back, get the original -------------
src = rnd(1, 200, 300, 3, 1)
box = (40, 30, 120, 90)
patch = src[:, 30:120, 40:160, :]
for fit in S.FIT_MODES:
    out, = N.stitch(patch, [box], fit, 1, 1, canvas=src.clone())
    check(f"round trip is lossless ({fit})", torch.equal(out, src),
          (out - src).abs().max().item())

# --- 2. nothing outside the bbox is touched -----------------------------
out, = N.stitch(torch.ones(1, 90, 120, 3), [box], "stretch", 1, 1, canvas=src.clone())
mask = torch.ones(200, 300, dtype=torch.bool); mask[30:120, 40:160] = False
check("canvas outside the bbox is untouched",
      torch.equal(out[0][mask], src[0][mask]))
check("...and inside it is the patch", torch.allclose(out[:, 30:120, 40:160, :],
      torch.ones(1, 90, 120, 3)))

# --- 3. no canvas -> transparent black RGBA at width x height -----------
out, = N.stitch(torch.ones(1, 10, 10, 3), [(5, 5, 10, 10)], "fit", 64, 48)
check("no canvas gives width x height", tuple(out.shape) == (1, 48, 64, 4), out.shape)
check("...transparent black away from the patch",
      out[0, 0, 0].tolist() == [0, 0, 0, 0], out[0, 0, 0].tolist())
check("...opaque where an RGB patch landed", out[0, 9, 9, 3].item() == 1.0)

# --- 4. fit modes, geometry ---------------------------------------------
# 100x100 patch into a 200x100 bbox (w x h). Marker: patch is all 1.
p = torch.ones(1, 100, 100, 3)
def cover(fit, bw, bh, ph=100, pw=100):
    pp = torch.ones(1, ph, pw, 3)
    o, = N.stitch(pp, [(0, 0, bw, bh)], fit, bw, bh)
    return o[0, ..., 3] > 0                      # alpha marks covered pixels

c = cover("fit", 200, 100)
check("fit: contained, centred horizontally",
      c[:, :50].sum() == 0 and c[:, 150:].sum() == 0 and c[:, 50:150].all())
c = cover("fill", 200, 100)
check("fill: covers the whole bbox", c.all())
c = cover("stretch", 200, 100)
check("stretch: covers the whole bbox", c.all())

# --- 5. crop: no scaling, anchored bottom-left ---------------------------
# 40x30 patch (w x h) into a 100x80 bbox -> sits at x 0..40, bottom rows.
o, = N.stitch(torch.ones(1, 30, 40, 3), [(0, 0, 100, 80)], "crop", 100, 80)
cov = o[0, ..., 3] > 0
check("crop: unscaled footprint", int(cov.sum()) == 30 * 40, int(cov.sum()))
check("crop: flush to the left", cov[50:80, 0:40].all() and cov[:, 40:].sum() == 0)
check("crop: flush to the bottom", cov[0:50, :].sum() == 0)
# Oversized patch: the overflow is discarded, not squeezed.
o, = N.stitch(torch.ones(1, 120, 150, 3), [(0, 0, 100, 80)], "crop", 100, 80)
check("crop: oversized patch is clipped to the bbox", (o[0, ..., 3] > 0).all())

# --- 6. alpha compositing ------------------------------------------------
# Half-transparent red over a transparent black canvas keeps its colour.
half = torch.zeros(1, 4, 4, 4); half[..., 0] = 1.0; half[..., 3] = 0.5
o, = N.stitch(half, [(0, 0, 4, 4)], "stretch", 4, 4)
check("half-alpha over transparent black keeps its colour",
      torch.allclose(o[0, 0, 0], torch.tensor([1., 0., 0., .5]), atol=1e-5),
      o[0, 0, 0].tolist())
# Same patch over opaque white -> a 50/50 blend, still opaque.
white = torch.ones(1, 4, 4, 3)
o, = N.stitch(half, [(0, 0, 4, 4)], "stretch", 4, 4, canvas=white)
check("half-alpha over opaque white blends 50/50",
      torch.allclose(o[0, 0, 0], torch.tensor([1., .5, .5]), atol=1e-5),
      o[0, 0, 0].tolist())
check("...and an RGB canvas stays RGB", out.shape[-1] == 4 and o.shape[-1] == 3)
# Fully transparent patch leaves the canvas alone.
clear = torch.zeros(1, 4, 4, 4); clear[..., 0] = 1.0
o, = N.stitch(clear, [(0, 0, 4, 4)], "stretch", 4, 4, canvas=white.clone())
check("fully transparent patch changes nothing", torch.allclose(o, white))

# --- 7. clipping ---------------------------------------------------------
o, = N.stitch(torch.ones(1, 20, 20, 3), [(-10, -10, 20, 20)], "stretch", 40, 40)
cov = o[0, ..., 3] > 0
check("bbox straddling the corner paints only the overlap",
      int(cov.sum()) == 100 and cov[:10, :10].all())
o, = N.stitch(torch.ones(1, 20, 20, 3), [(500, 500, 20, 20)], "stretch", 40, 40)
check("bbox entirely off-canvas is a no-op", (o[..., 3] == 0).all())

# --- 8. batching ---------------------------------------------------------
o, = N.stitch(rnd(5, 10, 10, 3, 2), [(0, 0, 10, 10)], "fit", 1, 1,
              canvas=rnd(1, 20, 20, 3, 3))
check("one canvas broadcasts across a patch batch", o.shape[0] == 5, o.shape)
o, = N.stitch(rnd(1, 10, 10, 3, 4), [(0, 0, 10, 10)], "fit", 1, 1,
              canvas=rnd(4, 20, 20, 3, 5))
check("one patch broadcasts across a canvas batch", o.shape[0] == 4, o.shape)
# A per-frame bbox list moves the patch frame by frame.
o, = N.stitch(torch.ones(2, 5, 5, 3), [(0, 0, 5, 5), (10, 10, 5, 5)], "fit", 20, 20)
check("per-frame bboxes are honoured",
      (o[0, 0, 0, 3] == 1) and (o[1, 0, 0, 3] == 0) and (o[1, 12, 12, 3] == 1))

# --- 9. canvas is never mutated in place ---------------------------------
canvas = rnd(1, 30, 30, 3, 6); before = canvas.clone()
N.stitch(torch.ones(1, 10, 10, 3), [(5, 5, 10, 10)], "fit", 1, 1, canvas=canvas)
check("the caller's canvas tensor is not modified", torch.equal(canvas, before))

# --- 10. bbox shapes accepted -------------------------------------------
for label, b in [("tuple list", [(1, 2, 3, 4)]), ("bare tuple", (1, 2, 3, 4)),
                 ("dict", {"x": 1, "y": 2, "width": 3, "height": 4}),
                 ("nested", [[(1, 2, 3, 4)]]),
                 ("tensor", torch.tensor([[1, 2, 3, 4]]))]:
    check(f"bbox accepted: {label}", S.normalize_bbox(b) == [(1, 2, 3, 4)],
          S.normalize_bbox(b))

print("\nFAILURES:", fails)
sys.exit(1 if fails else 0)
