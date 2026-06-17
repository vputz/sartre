---
name: openspec-preview
description: Render an OpenSpec change's artifacts (proposal → design → specs → tasks) into a single styled HTML page and open it in the browser for human review. Use after an /opsx:propose, before /opsx:apply, when the user wants to read/preview a change's specs rather than apply them.
argument-hint: [change-name]
---

Preview an OpenSpec change in the browser so a human can read the full proposal +
design + specs + tasks as one rendered page before applying it.

This is a thin wrapper around `bin/openspec-preview` — all logic lives in that
script so it's equally runnable by hand (`bin/openspec-preview <change>`).

## Run

```bash
bin/openspec-preview <change-name>
```

- With no argument the script lists the available changes under `openspec/changes/`.
- It concatenates the change's artifacts in OpenSpec build order
  (`proposal.md` → `design.md` → `specs/<capability>/spec.md` sorted → `tasks.md`),
  skipping any that don't exist, renders with `pandoc` (TOC + readable CSS), and
  opens the result with `xdg-open`.
- Output path defaults to `/tmp/openspec-preview.html`; override with `PREVIEW_OUT`.
- On a headless machine (no `DISPLAY`/`WAYLAND_DISPLAY`) it prints the HTML path
  instead of opening a browser.

## When to use

- Right after `/opsx:propose`, when the user says they want to **preview / review /
  read** the specs before `/opsx:apply`.
- Do **not** use this to apply or modify a change — it is read-only rendering.

## Requirements

`pandoc` (declared in `shell.nix`). `xdg-open` and a display are needed only for
the auto-open step; rendering works regardless.

## Report back

After running, tell the user the rendered path and that the browser was opened (or,
if headless, the path to open manually). Don't paste the HTML.
