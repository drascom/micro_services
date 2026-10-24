# Third-party notices

## CleanFrame (MIT)

Layout ideas, focus/glass approach (pre-rendered "registered" material instead of live backdrop-filter), list/dock geometry and timing values are adapted from CleanFrame — https://github.com/nthnclrk/CleanFrame — and the MagicPath design "Google TV Home — C · Clean Frame" (https://designs.magicpath.ai/v1/fair-road-5684). No code was copied verbatim; it was rewritten in ES5.

```
MIT License

Copyright (c) 2026 Nathan Clark

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Manrope font (SIL OFL 1.1)

`assets/manrope.woff2`, licence text in `assets/manrope-OFL.txt` (copied from CleanFrame).

## hls.js 1.7.3 (Apache-2.0)

`js/vendor/hls.min.js` is npm `hls.js@1.7.3` unmodified (UMD, `window.Hls`), Copyright (c) 2017 Dailymotion; licence text in `js/vendor/hls.js.LICENSE`.
- Source: `https://cdn.jsdelivr.net/npm/hls.js@1.7.3/dist/hls.min.js` (byte-identical to the CDN file; the trailing `sourceMappingURL` line is kept, no `.map` shipped)
- sha256: `a12e7ee1cd64a69dcdb314157e45dafcba705bfb0b1440b7935cb265d374423e`
- Lazy-loaded once via `<script>` only by the HTML5 engine for `type: "hls"` streams; AVPlay (Tizen TV) never touches it.
- Upgrading: download the new version from the same path, update the sha256 here and bump `?v=hlsjs-<version>` in `js/player.js`.
