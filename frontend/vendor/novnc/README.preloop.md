# noVNC (vendored)

Unmodified copy of `@novnc/novnc` 1.7.0 from the npm registry
(https://registry.npmjs.org/@novnc/novnc/-/novnc-1.7.0.tgz), trimmed to the
files the console imports: `core/`, `vendor/pako/` and the licence files.
`package.json` is upstream's with `scripts` and `devDependencies` removed (npm
would otherwise install noVNC's own build toolchain into the console).

Used by `src/components/desktop-view.ts` to render the brokered live desktop
view (#870) in view-only mode.

## Licence

noVNC is licensed under the Mozilla Public License 2.0 (`LICENSE.txt`,
`docs/LICENSE.MPL-2.0`). Bundled third-party parts keep their own licences:
pako (MIT, `vendor/pako/LICENSE`) and the files listed in `LICENSE.txt`
(BSD-2-Clause, BSD-3-Clause, OFL-1.1, see `docs/`). The rest of the Preloop
console is Apache-2.0; MPL-2.0 is file-level copyleft, so these files stay
under MPL-2.0 and any change to them must be published under the same terms.

`SHA256SUMS` records the hashes of the upstream files. To verify:
`cd frontend/vendor/novnc && shasum -a 256 -c SHA256SUMS`.

## Updating

Download the new tarball, replace `core/` and `vendor/`, copy the licence
files, regenerate `SHA256SUMS` and bump the version here and in
`package.json`. Then run `npm install` in `frontend/` to refresh the lockfile.
