# Fluent theme subset

Source: https://github.com/ejacques11/pyside6-fluent-ui, version 0.7.0-rc.1.
Copied from the installed skill on 2026-10-06: token resolver, theme manager,
QSS renderer, base QSS, token resources and two symbolic spin-button SVGs.
The workbench-specific selectors at the start of `fluent.qss.in` and the
primary-button disabled-state override are local additions.
Window chrome remains native; the optional workbench-shell templates are not used.
The package is self-contained and has no dependency on a user's installed skill path.

The MIT license and Microsoft Fluent token/icon notices are retained in
`LICENSE` and `NOTICE.md`. Exact Microsoft token provenance is in
`resources/source-manifest.json` (Fluent theme 9.2.1).
Application behavior and Chinese font selection live outside this upstream subset.
