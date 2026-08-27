import fs from "node:fs";
const src = fs.readFileSync(new URL("../js/kd_crop.js", import.meta.url), "utf8");
function extract(name) {
    const start = src.indexOf(`function ${name}(`);
    if (start < 0) throw new Error(`missing ${name}`);
    let i = src.indexOf("{", start), depth = 0;
    for (let j = i; j < src.length; j++) {
        if (src[j] === "{") depth++;
        else if (src[j] === "}" && --depth === 0) return src.slice(start, j + 1);
    }
    throw new Error(`unbalanced ${name}`);
}
const MIN_CROP = 8;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const harness = new Function(
    "clamp", "MIN_CROP", "state", "writeWidgets", "updateReadout", "drawOverlay",
    `
    let srcW = state.srcW, srcH = state.srcH, rect = state.rect;
    let ratio = null;
    const lockedRatio = () => ratio;
    ${extract("setRect")}
    ${extract("applyRatioDrag")}
    ${extract("applyCentredDrag")}
    ${extract("applyDrag")}
    return { setRect, applyDrag, get: () => rect,
             set: (w,h,r) => { srcW=w; srcH=h; rect=r; },
             lock: (v) => { ratio = v; } };
    `
);
const api = harness(clamp, MIN_CROP, { srcW: 0, srcH: 0, rect: {x:0,y:0,w:0,h:0} },
                    () => {}, () => {}, () => {});

const W = 1920, H = 1080;
let fails = 0;
const eq = (label, got, exp) => {
    const ok = JSON.stringify(got) === JSON.stringify(exp);
    if (!ok) fails++;
    console.log((ok ? "ok   " : "FAIL ") + label + " -> " + JSON.stringify(got) +
                (ok ? "" : "  (expected " + JSON.stringify(exp) + ")"));
};
const near = (label, got, exp, tol) => {
    const ok = Math.abs(got - exp) <= tol;
    if (!ok) fails++;
    console.log((ok ? "ok   " : "FAIL ") + label + " -> " + got +
                (ok ? "" : "  (expected ~" + exp + ")"));
};
const start = (r) => { api.set(W, H, { ...r }); return { ...r }; };
const ctr = (r) => [r.x + r.w / 2, r.y + r.h / 2];
// An odd integer size cannot straddle an integer centre exactly, so half a
// pixel is the tightest bound that is actually achievable.
const nearCtr = (label, got, exp) => {
    const a = ctr(got), b = ctr(exp);
    const ok = Math.abs(a[0]-b[0]) <= 0.5 && Math.abs(a[1]-b[1]) <= 0.5;
    if (!ok) fails++;
    console.log((ok ? "ok   " : "FAIL ") + label + " -> " + JSON.stringify(a) +
                (ok ? "" : "  (expected ~" + JSON.stringify(b) + ")"));
};
const R169 = 16 / 9;

// ---------------- Alt: scale about the centre ----------------
let s = start({ x: 400, y: 300, w: 400, h: 200 });
api.applyDrag("se", s, 100, 50, true);
eq("alt corner grows symmetrically", api.get(), { x: 300, y: 250, w: 600, h: 300 });
eq("...centre unmoved", ctr(api.get()), ctr(s));

s = start({ x: 400, y: 300, w: 400, h: 200 });
api.applyDrag("nw", s, 100, 50, true);
eq("alt NW shrinks symmetrically", api.get(), { x: 500, y: 350, w: 200, h: 100 });
eq("...centre unmoved", ctr(api.get()), ctr(s));

s = start({ x: 400, y: 300, w: 400, h: 200 });
api.applyDrag("e", s, 60, 0, true);
eq("alt edge moves both sides, other axis untouched",
   api.get(), { x: 340, y: 300, w: 520, h: 200 });

s = start({ x: 400, y: 300, w: 400, h: 200 });
api.applyDrag("n", s, -40, -40, true);
eq("alt top edge ignores dx", api.get(), { x: 400, y: 260, w: 400, h: 280 });

// odd sizes: the centre sits on a half pixel and must not drift
s = start({ x: 401, y: 301, w: 401, h: 201 });
const c0 = ctr(s);
for (const d of [7, 13, 29, 30, 31, 44]) {
    api.set(W, H, { ...s });
    api.applyDrag("se", s, d, d, true);
    const c = ctr(api.get());
    if (c[0] !== c0[0] || c[1] !== c0[1]) {
        fails++; console.log("FAIL odd-sized box centre drifted at d=" + d, c, c0);
    }
}
console.log("ok   odd-sized box keeps its centre across many deltas");

// clamping
s = start({ x: 900, y: 500, w: 200, h: 100 });
api.applyDrag("se", s, 9999, 9999, true);
const big = api.get();
eq("alt growth stops at the frame",
   [big.x >= 0, big.y >= 0, big.x + big.w <= W, big.y + big.h <= H],
   [true, true, true, true]);
eq("...still centred", ctr(big), ctr(s));

s = start({ x: 400, y: 300, w: 400, h: 200 });
api.applyDrag("se", s, -9999, -9999, true);
const tiny = api.get();
eq("alt shrink stops at MIN_CROP", [tiny.w, tiny.h], [8, 8]);
eq("...still centred", ctr(tiny), ctr(s));

// alt + ratio
api.lock(R169);
s = start({ x: 400, y: 300, w: 320, h: 180 });
api.applyDrag("se", s, 200, 20, true);
const ar = api.get();
near("alt + ratio corner holds 16:9", ar.w / ar.h, R169, 0.01);
nearCtr("...and stays centred", ar, s);

s = start({ x: 400, y: 300, w: 320, h: 180 });
api.applyDrag("e", s, 120, 0, true);
const ae = api.get();
near("alt + ratio edge holds 16:9", ae.w / ae.h, R169, 0.01);
nearCtr("...and stays centred", ae, s);
if (!(ae.w > s.w && ae.h > s.h)) { fails++; console.log("FAIL alt+ratio edge grew only one axis", ae); }
else console.log("ok   alt + ratio edge grows both axes");

s = start({ x: 900, y: 500, w: 320, h: 180 });
api.applyDrag("se", s, 9999, 9999, true);
const arb = api.get();
near("alt + ratio clamped at the frame still holds 16:9", arb.w / arb.h, R169, 0.02);
eq("...and inside it", [arb.x >= 0, arb.y >= 0, arb.x + arb.w <= W, arb.y + arb.h <= H],
   [true, true, true, true]);
api.lock(null);

// alt is ignored for move
s = start({ x: 100, y: 100, w: 300, h: 200 });
api.applyDrag("move", s, 50, 50, true);
eq("alt does not change move", api.get(), { x: 150, y: 150, w: 300, h: 200 });

// toggling alt mid-drag re-derives from the same deltas
s = start({ x: 400, y: 300, w: 400, h: 200 });
api.applyDrag("se", s, 120, 60, false);
const free1 = api.get();
api.set(W, H, { ...s }); api.applyDrag("se", s, 120, 60, true);
api.set(W, H, { ...s }); api.applyDrag("se", s, 120, 60, false);
eq("alt off -> on -> off returns the identical rect", api.get(), free1);

// ---------------- regression: everything from before ----------------
s = start({ x: 200, y: 200, w: 400, h: 400 });
api.applyDrag("nw", s, 100, 100);
eq("free NW corner still anchors the SE", api.get(), { x: 300, y: 300, w: 300, h: 300 });
api.lock(R169);
s = start({ x: 200, y: 200, w: 400, h: 400 });
api.applyDrag("nw", s, -150, -80);
const nw = api.get();
near("locked NW still holds 16:9", nw.w / nw.h, R169, 0.01);
eq("...and still pins the SE corner", [nw.x + nw.w, nw.y + nw.h], [600, 600]);
api.lock(null);

// ---------------- sweeps ----------------
let bad = 0;
for (let i = 0; i < 40000; i++) {
    const sw = 64 + Math.floor(Math.random() * 1500);
    const sh = 64 + Math.floor(Math.random() * 1500);
    const w0 = 8 + Math.floor(Math.random() * (sw - 8));
    const h0 = 8 + Math.floor(Math.random() * (sh - 8));
    const st = { x: Math.floor(Math.random() * (sw - w0 + 1)),
                 y: Math.floor(Math.random() * (sh - h0 + 1)), w: w0, h: h0 };
    const lock = Math.random() < 0.5 ? R169 : null;
    api.lock(lock);
    api.set(sw, sh, { ...st });
    const modes = ["nw","ne","sw","se","n","s","e","w"];
    const m = modes[Math.floor(Math.random() * modes.length)];
    api.applyDrag(m, st, (Math.random()-0.5)*3000, (Math.random()-0.5)*3000, true);
    const r = api.get();
    const inBounds = r.w >= 1 && r.h >= 1 && r.x >= 0 && r.y >= 0 &&
                     r.x + r.w <= sw && r.y + r.h <= sh;
    // Centre held, to the half pixel that integer rounding allows.
    const c0 = [st.x + st.w/2, st.y + st.h/2], c1 = [r.x + r.w/2, r.y + r.h/2];
    const cramped = 2*Math.min(c0[0], sw-c0[0]) < 8 || 2*Math.min(c0[1], sh-c0[1]) < 8;
    const centred = Math.abs(c0[0]-c1[0]) <= 0.5 && Math.abs(c0[1]-c1[1]) <= 0.5;
    if (!inBounds || (!centred && !cramped)) {
        if (bad++ < 5) console.log("FAIL alt sweep", { sw, sh, st, m, lock: !!lock, r });
    }
}
api.lock(null);
console.log(bad === 0
    ? "alt sweep: 40000 random centred drags stayed in bounds and kept their centre"
    : `alt sweep FAILURES: ${bad}`);
fails += bad;
// Regression: the same sweep with alt OFF, covering both free and locked.
let obad = 0;
for (let i = 0; i < 40000; i++) {
    const sw = 64 + Math.floor(Math.random() * 1500);
    const sh = 64 + Math.floor(Math.random() * 1500);
    const w0 = 8 + Math.floor(Math.random() * (sw - 8));
    const h0 = 8 + Math.floor(Math.random() * (sh - 8));
    const st = { x: Math.floor(Math.random() * (sw - w0 + 1)),
                 y: Math.floor(Math.random() * (sh - h0 + 1)), w: w0, h: h0 };
    const lock = Math.random() < 0.5 ? R169 : null;
    api.lock(lock);
    api.set(sw, sh, { ...st });
    const modes = ["nw","ne","sw","se","n","s","e","w","move"];
    const m = modes[Math.floor(Math.random() * modes.length)];
    api.applyDrag(m, st, (Math.random()-0.5)*3000, (Math.random()-0.5)*3000, false);
    const r = api.get();
    const inBounds = r.w >= 1 && r.h >= 1 && r.x >= 0 && r.y >= 0 &&
                     r.x + r.w <= sw && r.y + r.h <= sh;
    const moveKeptSize = m !== "move" || (r.w === st.w && r.h === st.h);
    if (!inBounds || !moveKeptSize) {
        if (obad++ < 5) console.log("FAIL anchored sweep", { sw, sh, st, m, lock: !!lock, r });
    }
}
api.lock(null);
console.log(obad === 0
    ? "anchored sweep: 40000 random unmodified drags stayed in bounds"
    : `anchored sweep FAILURES: ${obad}`);
fails += obad;

console.log("\nFAILURES:", fails);
process.exit(fails ? 1 : 0);
