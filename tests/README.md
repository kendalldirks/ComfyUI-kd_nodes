# KDNodes tests

Pure-logic tests for the crop / stitch pair. Neither one needs ComfyUI running.

    node tests/kd_crop.test.mjs      # crop box geometry  (needs node)
    python tests/stitch_kd.test.py   # stitcher           (needs torch)

`kd_crop.test.mjs` pulls the real `setRect` / `applyDrag` / `applyRatioDrag` /
`applyCentredDrag` bodies straight out of `js/kd_crop.js` and runs them against
stubs, so it tests the shipped code rather than a copy of it that can rot. If
you rename one of those functions, the extractor throws rather than silently
testing nothing.

Both end with a `FAILURES: n` line and exit non-zero on failure.
