# Vendored third-party assets

Everything the browser loads, committed here rather than fetched from a CDN, **so a deployment
works with no outbound network** -- the reason `igv.min.js` was vendored long before the rest of
this directory existed, and the reason `aledb-phylogeny` vendors phylotree.

That claim used to be false. Until this file existed, `base.html` could not render without
jQuery from `ajax.googleapis.com`, and the suite pulled 22 assets from seven CDN hosts
(`ajax.googleapis.com`, `cdn.datatables.net`, `cdnjs.cloudflare.com`, `maxcdn.bootstrapcdn.com`,
`gitcdn.github.io`, `unpkg.com`). All were reachable at the time of vendoring; nothing was
broken. The property simply was not true, and nothing tested it -- which is invisible to
everybody with a working connection.

`aledb_common/tests/test_offline.py` is what keeps it true. It fails on any `<script src>` or
`<link href>` naming an external host in any first-party template.

## Two deliberate exceptions

**NCBI's Sequence Viewer** (`https://www.ncbi.nlm.nih.gov/projects/sviewer/js/sviewer.js`,
loaded by `aledb_sample/templates/ncbi/ncbi_view.html`) **cannot be vendored and is not meant to
be.** It is a client for NCBI's own backend, so the page needs `ncbi.nlm.nih.gov` reachable
whatever we do with the file. Offline it simply never reaches a verified state and never renders
the script. It is the one host the guard test allows, by name.

**Google Analytics** is loaded only when a deployment sets `GOOGLE_ANALYTICS_TAG`. It used to
render unconditionally, so every page of every deployment fetched `gtag.js` and reported to an
empty tag id.

## Versions are frozen, deliberately

These are byte-for-byte what the CDNs served, at the versions the templates already asked for --
jQuery 1.12.4, Bootstrap 3.3.7, DataTables 1.10.x, select2 4.0.3. Several are long EOL. Making
the app work offline and modernising a decade-old front end are separate problems, and doing
both at once leaves no way to tell which half broke a page.

**`sweetalert` is the exception that had to change**: it was loaded from
`https://unpkg.com/sweetalert/dist/sweetalert.min.js` with **no version at all**, resolving to
whatever unpkg served that day (2.1.2 when vendored). A major release would have changed the
`swal()` API under ten templates with no commit here. Vendoring necessarily pins it.

## Things that will bite

- **A stylesheet drags its fonts with it.** Font Awesome asks for
  `url('../fonts/fontawesome-webfont.woff2')` and Bootstrap for
  `url(../fonts/glyphicons-halflings-regular.woff2)`, so each `css/` must keep a `fonts/`
  sibling. Flatten this layout and every icon in the product becomes a blank box **with no
  error** -- the page renders, the glyphs are just gone.
- **The two DataTables bundles were rewritten, and are the only files here that are not
  byte-identical to their source.** They embed Bootstrap and reference glyphicons at an
  *absolute* `/Bootstrap-3.3.x/fonts/...`, which resolves against `cdn.datatables.net`'s root
  and would 404 from ours. Their `url()` paths now point at a `fonts/` directory beside each
  bundle, so each is self-contained. The sha256 below is of the rewritten file, which is what is
  actually served; re-downloading the original will not match.
- **Four DataTables bundles at four versions are kept distinct** (1.10.12, two bundles, plus a
  1.10.18 one). The pages differ in which extensions they use, so consolidating them is a
  behaviour change wearing a cleanup's clothes. Not done here.
- **Never vendor a jsDelivr `/+esm` build.** It contains hard `import ... from "/npm/..."` CDN
  URLs and is not offline-viable. (Carried across from `aledb-phylogeny`'s own `VENDOR.md`,
  where it was learned.)
- `?v={{ aledb_version }}` cache-busting is **not** used on these, and should not be: every path
  already carries its version, so a release cannot serve half of one version and half of another.

## Not vendored, and still dead

`bio-pv.min.js` (146 KB) and `runs.js` / `experiment_selector.js` are committed here and
referenced by no template. Left in place deliberately; removing them is its own change.
`jquery-1.9.1.min.js` **was** removed, because leaving a dead 1.9.1 beside a live vendored
1.12.4 is a trap rather than merely clutter.

## Manifest

Source URL is where each file came from. Regenerate a hash with
`shasum -a 256 <file>`.

| file | bytes | sha256 |
|---|---|---|
| `bootstrap-3.3.7/css/bootstrap.min.css` | 121200 | `f75e846cc83bd11432f4b1e21a45f31bc85283d11d372f7b19accd1bf6a2635c` |
| `bootstrap-3.3.7/fonts/glyphicons-halflings-regular.eot` | 20127 | `13634da87d9e23f8c3ed9108ce1724d183a39ad072e73e1b3d8cbf646d2d0407` |
| `bootstrap-3.3.7/fonts/glyphicons-halflings-regular.svg` | 108738 | `42f60659d265c1a3c30f9fa42abcbb56bd4a53af4d83d316d6dd7a36903c43e5` |
| `bootstrap-3.3.7/fonts/glyphicons-halflings-regular.ttf` | 45404 | `e395044093757d82afcb138957d06a1ea9361bdcf0b442d06a18a8051af57456` |
| `bootstrap-3.3.7/fonts/glyphicons-halflings-regular.woff` | 23424 | `a26394f7ede100ca118eff2eda08596275a9839b959c226e15439557a5a80742` |
| `bootstrap-3.3.7/fonts/glyphicons-halflings-regular.woff2` | 18028 | `fe185d11a49676890d47bb783312a0cda5a44c4039214094e7957b4c040ef11c` |
| `bootstrap-3.3.7/js/bootstrap.min.js` | 37045 | `53964478a7c634e8dad34ecc303dd8048d00dce4993906de1bacf67f663486ef` |
| `bootstrap-select-1.12.4/css/bootstrap-select.min.css` | 6655 | `feeb377a08b6715a7498491547c727a8bb2e0d8e819ab0eebd33d4b84af51c94` |
| `bootstrap-select-1.12.4/js/bootstrap-select.min.js` | 33379 | `d7d277ad3ded41d89d82daaa750df136efbe19dec4a0ffda83fd31d651e2d316` |
| `bootstrap-toggle-2.2.2/css/bootstrap-toggle.min.css` | 1590 | `ac3597e97ae646db56c9505e3e19aba479e767510f98ce96411425ea1d21ec9f` |
| `bootstrap-toggle-2.2.2/js/bootstrap-toggle.min.js` | 4129 | `799360060bad2c8e3bacace97d48e2fdd0fdb7a2d1b36808dd8a9729da033a6a` |
| `datatables-1.10.12/js/dataTables.bootstrap.min.js` | 1960 | `f7462a9c7a26e23f0e85c110832508d888661984c13b9e0075c7f7603654f713` |
| `datatables-1.10.12/js/jquery.dataTables.min.js` | 82638 | `4d7e8f389436bb9fda2661d327f5d42f9bd609bb8ec34010760504ce4e2f60c7` |
| `datatables-bundle-bs336/datatables.min.css` **(rewritten)** | 128283 | `11b5b5436b603411d06306d0052bc9c1972a88a30191a6f0cc4610b4b4bb60e2` |
| `datatables-bundle-bs336/datatables.min.js` | 702816 | `eddac0ccfa5568979036528ff2f6b4fbcedb36ef01e003ba6430fcdfee2e80f6` |
| `datatables-bundle-bs336/fonts/glyphicons-halflings-regular.eot` | 20127 | `13634da87d9e23f8c3ed9108ce1724d183a39ad072e73e1b3d8cbf646d2d0407` |
| `datatables-bundle-bs336/fonts/glyphicons-halflings-regular.svg` | 108738 | `42f60659d265c1a3c30f9fa42abcbb56bd4a53af4d83d316d6dd7a36903c43e5` |
| `datatables-bundle-bs336/fonts/glyphicons-halflings-regular.ttf` | 45404 | `e395044093757d82afcb138957d06a1ea9361bdcf0b442d06a18a8051af57456` |
| `datatables-bundle-bs336/fonts/glyphicons-halflings-regular.woff` | 23424 | `a26394f7ede100ca118eff2eda08596275a9839b959c226e15439557a5a80742` |
| `datatables-bundle-bs336/fonts/glyphicons-halflings-regular.woff2` | 18028 | `fe185d11a49676890d47bb783312a0cda5a44c4039214094e7957b4c040ef11c` |
| `datatables-bundle-bs337/datatables.min.css` **(rewritten)** | 132816 | `43337851cd7e137f01f54ec7f9f138238fca6d7febba9459ca473af2cf646ef0` |
| `datatables-bundle-bs337/datatables.min.js` | 177832 | `cc7c5a346d895090ecb33ab8c1567b5eb4facebf5fd87d6221cf89a5d0bdfc02` |
| `datatables-bundle-bs337/fonts/glyphicons-halflings-regular.eot` | 20127 | `13634da87d9e23f8c3ed9108ce1724d183a39ad072e73e1b3d8cbf646d2d0407` |
| `datatables-bundle-bs337/fonts/glyphicons-halflings-regular.svg` | 108738 | `42f60659d265c1a3c30f9fa42abcbb56bd4a53af4d83d316d6dd7a36903c43e5` |
| `datatables-bundle-bs337/fonts/glyphicons-halflings-regular.ttf` | 45404 | `e395044093757d82afcb138957d06a1ea9361bdcf0b442d06a18a8051af57456` |
| `datatables-bundle-bs337/fonts/glyphicons-halflings-regular.woff` | 23424 | `a26394f7ede100ca118eff2eda08596275a9839b959c226e15439557a5a80742` |
| `datatables-bundle-bs337/fonts/glyphicons-halflings-regular.woff2` | 18028 | `fe185d11a49676890d47bb783312a0cda5a44c4039214094e7957b4c040ef11c` |
| `datatables-bundle-dt11018/datatables.min.js` | 249243 | `48101aa6caabec9bda014df6083905225b9496140bfdf74ad2a047d1440ec959` |
| `font-awesome-4.6.3/css/font-awesome.min.css` | 29063 | `008a1d103902f15fdb1c191fcb1ce8954330e7b8de43d09abb08555ba609f420` |
| `font-awesome-4.6.3/fonts/fontawesome-webfont.eot` | 76518 | `50bbe9192697e791e2ee4ef73917aeb1b03e727dff08a1fc8d74f00e4aa812e1` |
| `font-awesome-4.6.3/fonts/fontawesome-webfont.svg` | 391622 | `8e3586389bb4cd01b3f85bb3b622739bde6627f28bba63a020c223ca9cf1b9ae` |
| `font-awesome-4.6.3/fonts/fontawesome-webfont.ttf` | 152796 | `ae19e2e4c04f2b04bf030684c4c1db8faf5c8fe3ee03d1e0c409046608b38912` |
| `font-awesome-4.6.3/fonts/fontawesome-webfont.woff` | 90412 | `adbc4f95eb6d7f2738959cf0ecbc374672fce47e856050a8e9791f457623ac2c` |
| `font-awesome-4.6.3/fonts/fontawesome-webfont.woff2` | 71896 | `7dacf83f51179de8d7980a513e67ab3a08f2c6272bb5946df8fd77c0d1763b73` |
| `jquery-1.12.4/jquery.min.js` | 97163 | `668b046d12db350ccba6728890476b3efee53b2f42dbb84743e5e9f1ae0cc404` |
| `jszip-3.1.3/jszip.js` | 364732 | `992d96f77099b1969244a244f72db0ecc9e7947d8472ca527f9a181f1d64124c` |
| `metismenu-2.5.2/metisMenu.min.css` | 1524 | `5e3674cf5744b79ac6ea6a8c121cbeb2c5225fef37b6280cb118505f59eabcab` |
| `metismenu-2.5.2/metisMenu.min.js` | 5806 | `16fb464c98026cf996af40dd22c3167ae931a0ec568564c28d3df8e704e3e58f` |
| `pdfmake-0.1.36/pdfmake.min.js` | 970387 | `071a29c794ab8b2a52f1e139aabdfc06f6a9d99371dc2525f4767ab1ec01b5f9` |
| `pdfmake-0.1.36/vfs_fonts.js` | 870284 | `5cb81fa70754070475938e9859359a268122c9b62cac154ebb8e120e812662cc` |
| `select2-4.0.3/css/select2.min.css` | 15196 | `c493991dfa712d1fee861d41c18152e5f8663807484506a23ae97917f6fbbf7b` |
| `select2-4.0.3/js/select2.min.js` | 66664 | `fa659dfc6ebd4b8aad80fa304842c879502fefe16e2fcef55976a89605e7af04` |
| `sweetalert-2.1.2/sweetalert.min.js` | 40808 | `2ac46ebee46d515be86deeba385b4e41f8cff160364b362c9a6e153df327c66b` |

### Source URLs

| file | fetched from |
|---|---|
| `bootstrap-3.3.7/css/bootstrap.min.css` | https://maxcdn.bootstrapcdn.com/bootstrap/3.3.7/css/bootstrap.min.css |
| `bootstrap-3.3.7/fonts/glyphicons-halflings-regular.eot` | https://maxcdn.bootstrapcdn.com/bootstrap/3.3.7/fonts/glyphicons-halflings-regular.eot |
| `bootstrap-3.3.7/fonts/glyphicons-halflings-regular.svg` | https://maxcdn.bootstrapcdn.com/bootstrap/3.3.7/fonts/glyphicons-halflings-regular.svg |
| `bootstrap-3.3.7/fonts/glyphicons-halflings-regular.ttf` | https://maxcdn.bootstrapcdn.com/bootstrap/3.3.7/fonts/glyphicons-halflings-regular.ttf |
| `bootstrap-3.3.7/fonts/glyphicons-halflings-regular.woff` | https://maxcdn.bootstrapcdn.com/bootstrap/3.3.7/fonts/glyphicons-halflings-regular.woff |
| `bootstrap-3.3.7/fonts/glyphicons-halflings-regular.woff2` | https://maxcdn.bootstrapcdn.com/bootstrap/3.3.7/fonts/glyphicons-halflings-regular.woff2 |
| `bootstrap-3.3.7/js/bootstrap.min.js` | https://maxcdn.bootstrapcdn.com/bootstrap/3.3.7/js/bootstrap.min.js |
| `bootstrap-select-1.12.4/css/bootstrap-select.min.css` | https://cdnjs.cloudflare.com/ajax/libs/bootstrap-select/1.12.4/css/bootstrap-select.min.css |
| `bootstrap-select-1.12.4/js/bootstrap-select.min.js` | https://cdnjs.cloudflare.com/ajax/libs/bootstrap-select/1.12.4/js/bootstrap-select.min.js |
| `bootstrap-toggle-2.2.2/css/bootstrap-toggle.min.css` | https://gitcdn.github.io/bootstrap-toggle/2.2.2/css/bootstrap-toggle.min.css |
| `bootstrap-toggle-2.2.2/js/bootstrap-toggle.min.js` | https://gitcdn.github.io/bootstrap-toggle/2.2.2/js/bootstrap-toggle.min.js |
| `datatables-1.10.12/js/dataTables.bootstrap.min.js` | https://cdn.datatables.net/1.10.12/js/dataTables.bootstrap.min.js |
| `datatables-1.10.12/js/jquery.dataTables.min.js` | https://cdn.datatables.net/1.10.12/js/jquery.dataTables.min.js |
| `datatables-bundle-bs336/datatables.min.css` | https://cdn.datatables.net/v/bs-3.3.6/jszip-2.5.0/pdfmake-0.1.18/dt-1.10.12/b-1.2.2/b-colvis-1.2.2/b-flash-1.2.2/b-html5-1.2.2/b-print-1.2.2/cr-1.3.2/fh-3.1.2/datatables.min.css |
| `datatables-bundle-bs336/datatables.min.js` | https://cdn.datatables.net/v/bs-3.3.6/jszip-2.5.0/pdfmake-0.1.18/dt-1.10.12/b-1.2.2/b-colvis-1.2.2/b-flash-1.2.2/b-html5-1.2.2/b-print-1.2.2/cr-1.3.2/fh-3.1.2/datatables.min.js |
| `datatables-bundle-bs336/fonts/glyphicons-halflings-regular.eot` | https://cdn.datatables.net/Bootstrap-3.3.6/fonts/glyphicons-halflings-regular.eot |
| `datatables-bundle-bs336/fonts/glyphicons-halflings-regular.ttf` | https://cdn.datatables.net/Bootstrap-3.3.6/fonts/glyphicons-halflings-regular.ttf |
| `datatables-bundle-bs336/fonts/glyphicons-halflings-regular.woff` | https://cdn.datatables.net/Bootstrap-3.3.6/fonts/glyphicons-halflings-regular.woff |
| `datatables-bundle-bs336/fonts/glyphicons-halflings-regular.woff2` | https://cdn.datatables.net/Bootstrap-3.3.6/fonts/glyphicons-halflings-regular.woff2 |
| `datatables-bundle-bs337/datatables.min.css` | https://cdn.datatables.net/v/bs-3.3.7/dt-1.10.15/b-1.3.1/b-colvis-1.3.1/b-html5-1.3.1/se-1.2.2/datatables.min.css |
| `datatables-bundle-bs337/datatables.min.js` | https://cdn.datatables.net/v/bs-3.3.7/dt-1.10.15/b-1.3.1/b-colvis-1.3.1/b-html5-1.3.1/se-1.2.2/datatables.min.js |
| `datatables-bundle-bs337/fonts/glyphicons-halflings-regular.eot` | https://cdn.datatables.net/Bootstrap-3.3.7/fonts/glyphicons-halflings-regular.eot |
| `datatables-bundle-bs337/fonts/glyphicons-halflings-regular.ttf` | https://cdn.datatables.net/Bootstrap-3.3.7/fonts/glyphicons-halflings-regular.ttf |
| `datatables-bundle-bs337/fonts/glyphicons-halflings-regular.woff` | https://cdn.datatables.net/Bootstrap-3.3.7/fonts/glyphicons-halflings-regular.woff |
| `datatables-bundle-bs337/fonts/glyphicons-halflings-regular.woff2` | https://cdn.datatables.net/Bootstrap-3.3.7/fonts/glyphicons-halflings-regular.woff2 |
| `datatables-bundle-dt11018/datatables.min.js` | https://cdn.datatables.net/v/dt/jszip-2.5.0/dt-1.10.18/b-1.5.6/b-colvis-1.5.6/b-html5-1.5.6/fc-3.2.5/fh-3.1.4/rg-1.1.0/sl-1.3.0/datatables.min.js |
| `font-awesome-4.6.3/css/font-awesome.min.css` | https://maxcdn.bootstrapcdn.com/font-awesome/4.6.3/css/font-awesome.min.css |
| `font-awesome-4.6.3/fonts/fontawesome-webfont.eot` | https://maxcdn.bootstrapcdn.com/font-awesome/4.6.3/fonts/fontawesome-webfont.eot |
| `font-awesome-4.6.3/fonts/fontawesome-webfont.svg` | https://maxcdn.bootstrapcdn.com/font-awesome/4.6.3/fonts/fontawesome-webfont.svg |
| `font-awesome-4.6.3/fonts/fontawesome-webfont.ttf` | https://maxcdn.bootstrapcdn.com/font-awesome/4.6.3/fonts/fontawesome-webfont.ttf |
| `font-awesome-4.6.3/fonts/fontawesome-webfont.woff` | https://maxcdn.bootstrapcdn.com/font-awesome/4.6.3/fonts/fontawesome-webfont.woff |
| `font-awesome-4.6.3/fonts/fontawesome-webfont.woff2` | https://maxcdn.bootstrapcdn.com/font-awesome/4.6.3/fonts/fontawesome-webfont.woff2 |
| `jquery-1.12.4/jquery.min.js` | https://ajax.googleapis.com/ajax/libs/jquery/1.12.4/jquery.min.js |
| `jszip-3.1.3/jszip.js` | https://cdnjs.cloudflare.com/ajax/libs/jszip/3.1.3/jszip.js |
| `metismenu-2.5.2/metisMenu.min.css` | https://cdnjs.cloudflare.com/ajax/libs/metisMenu/2.5.2/metisMenu.min.css |
| `metismenu-2.5.2/metisMenu.min.js` | https://cdnjs.cloudflare.com/ajax/libs/metisMenu/2.5.2/metisMenu.min.js |
| `pdfmake-0.1.36/pdfmake.min.js` | https://cdnjs.cloudflare.com/ajax/libs/pdfmake/0.1.36/pdfmake.min.js |
| `pdfmake-0.1.36/vfs_fonts.js` | https://cdnjs.cloudflare.com/ajax/libs/pdfmake/0.1.36/vfs_fonts.js |
| `select2-4.0.3/css/select2.min.css` | https://cdnjs.cloudflare.com/ajax/libs/select2/4.0.3/css/select2.min.css |
| `select2-4.0.3/js/select2.min.js` | https://cdnjs.cloudflare.com/ajax/libs/select2/4.0.3/js/select2.min.js |
| `sweetalert-2.1.2/sweetalert.min.js` | https://unpkg.com/sweetalert@2.1.2/dist/sweetalert.min.js |

## Vendored before this file existed

These predate the manifest above and were committed with no record of where they came from.
Hashes are of what is on disk.

**`igv.min.js` is igv.js 3.8.5, established by comparison, not by reading the file.** It carries
two version strings -- `i.version="3.4.12"` and `version:"3.8.5"` -- and it is not possible to
tell from the bundle which is the library's. It was settled by downloading
`https://unpkg.com/igv@3.8.5/dist/igv.min.js` and finding it **byte-identical**, sha256
`62fc5c78…`, 1,502,117 bytes. So `3.4.12` belongs to something bundled inside it.

That is also the current release: npm's `igv@latest` is 3.8.5. (GitHub's latest *release* tag
reads v3.7.0 -- the project publishes to npm ahead of tagging, so npm is the one to check.)
There was nothing to upgrade.

The lesson this file exists for: the version was recoverable only because the exact published
build could be fetched and diffed. Record the source URL when vendoring and nobody has to do
that again.

`bio-pv.min.js`, `csv.min.js` and the sb-admin-2 theme remain unrecorded; `bio-pv.min.js` is
referenced by no template.

| file | version | bytes | sha256 | source |
|---|---|---|---|---|
| `../js/igv.min.js` | **3.8.5** | 1502117 | `62fc5c7860306e7567e191e997fac2391d3a80e05fdd832619082f2a60c588cf` | https://unpkg.com/igv@3.8.5/dist/igv.min.js |
| `../js/bio-pv.min.js` | *unknown* | 146432 | `bf03692bc2440179584bbf10d612cf8ead92b566f9751f7c6f28c9ff1b0b7bcb` | *unrecorded* |
| `../js/csv.min.js` | *unknown* | 5030 | `781a36a1356cf67eddd6d349b5faffc027e8618d3bcbb16bec13edc525c332b6` | *unrecorded* |
| `../js/sb-admin-2.min.js` | *unknown* | 845 | `634679a53e2a3c66a85121e8c56f89f1f2168d09e373bbf4dd6044527b7d490d` | *unrecorded* |
| `../css/sb-admin-2.css` | *unknown* | 8303 | `f214c13c1eaabd8ed93c9c3753bfd54d3c82818a00949c5263c1d1da0cc3994e` | *unrecorded* |

### What igv.js 3.8.5 can do that we are not using

Confirmed in this bundle rather than in the docs: inline `features:` arrays
(`new Tl({features: t.features})`, `loadFeaturesNoIndex`), `updateFeatures()` for changing a
track's data without a reload, and `sampleKeys` for row-per-sample `seg`/`mut` tracks. The
browse page passes `tracks: []`, so none of it is in use yet.
