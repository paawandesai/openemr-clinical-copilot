# Vendored PDF.js

| | |
|---|---|
| Package | [`pdfjs-dist`](https://www.npmjs.com/package/pdfjs-dist) |
| Version | **6.3.289** |
| Build | `legacy/build` (transpiled; the non-legacy build needs a very recent engine) |
| Upstream | https://github.com/mozilla/pdf.js |
| Licence | Apache-2.0 — see `LICENSE` in this directory |

Files, copied byte-for-byte out of the published tarball:

| file | source path in the tarball | sha256 |
|---|---|---|
| `pdf.min.mjs` | `package/legacy/build/pdf.min.mjs` | `f401927e692efc7735e0cd528c490d0dd31b7f0972c122b7040df805be45cce4` |
| `pdf.worker.min.mjs` | `package/legacy/build/pdf.worker.min.mjs` | `a33cfe728c584fdba4fcc1fd54bcdc2f9f2f13889ddbb5b2bd1d0f8cbe49b84e` |
| `LICENSE` | `package/LICENSE` | Apache-2.0 |

Reproduce:

```bash
npm pack pdfjs-dist@6.3.289
tar xzf pdfjs-dist-6.3.289.tgz
cp package/legacy/build/pdf.min.mjs package/legacy/build/pdf.worker.min.mjs package/LICENSE .
shasum -a 256 pdf.min.mjs pdf.worker.min.mjs
```

## Why vendored, and not a CDN

This is the CSP reasoning the work order asks for, and the short version is
that the decision does not rest on CSP at all — OpenEMR does not send a
`script-src` policy on the patient dashboard (the only `Content-Security-Policy`
headers in the tree are `frame-ancestors 'none'` on `interface/login/login.php`
and `portal/index.php`). A CDN `<script>` would therefore load. That is the
problem, not the permission:

1. **The script would run in the chart page, next to PHI.** `pdf.min.mjs` is
   loaded into the same document as the patient dashboard. A CDN origin is a
   third party with script access to that DOM on every chart open. Nothing but
   the CDN's own integrity stands between an upstream compromise and the
   record. Vendoring makes the file reviewable, diffable and pinned in git.
2. **OpenEMR is deployed where there is no egress.** Clinic installs are
   routinely on isolated networks; a viewer that needs `cdnjs` at page load is
   a viewer that does not work there. Every other front-end asset OpenEMR uses
   is served from its own webroot (`public/assets`, populated by
   `scripts/install-assets.js`), and this module already serves its own
   `public/assets/pre-visit-brief-card.js` the same way.
3. **The bytes are an audit artefact.** When a rendered page is the evidence
   behind a clinical claim (CLAUDE.md invariant 2), "which renderer drew it"
   is part of the provenance. A pinned file with a recorded digest answers
   that; `@latest` on a CDN does not.

A CSP is worth adding to this module's own pages later, and if one is added it
should be `default-src 'none'; script-src 'self'; worker-src 'self'; ...` —
which these vendored paths already satisfy and a CDN would not.

## Upgrading

Re-run the reproduce block with a new version, update the table above, and
re-run the overlay alignment check (`dev/README.md`): the coordinate work
depends on `page.view`, `page.rotate` and `viewport.transform`, which are the
three parts of the API most likely to move.
