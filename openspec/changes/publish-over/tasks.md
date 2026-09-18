## 1. Factor the publish tail

- [ ] 1.1 `src/sartre/repository.py`: extract the post-entry-building body of `publish` — acquire lease → heartbeat → upload the given sources (`known_hash` dedup) → self-check → `commit` → self-check → `set_pointer` CAS → release — into a private helper taking `(coord, entries, uploads: Mapping[str, bytes | Path], *, pointer, expected, metadata, actor, reason)`. `publish` calls it with every path as an upload; behavior unchanged (existing publish tests stay green).

## 2. publish_over

- [ ] 2.1 `Repository.publish_over(coord, base: Ref = HEAD, *, set={}, remove=(), pointer="head", metadata=None, actor="unknown", reason=None) -> Version`: resolve `base` → snapshot; seed `{path: Entry}` from its entries; drop `remove` paths (raise a typed error — `PathError`/`NotFound` — if a removed path is absent); for each `set` source, `normalize_path`, hash it (pass 1) into a fresh `Entry`, override/add; run `check_no_case_collisions` on the merged set; compute `version`/`hashes` over all merged entries; determine the pointer's current value for the CAS; call the §1 helper with `uploads = the set sources only`.
- [ ] 2.2 `AsyncRepository.publish_over`: awaitable wrapper offloading to the sync method.

## 3. CLI

- [ ] 3.1 `src/sartre/cli/ops.py`: `publish_over(repo, coord, base_ref, changes: Mapping[str, Path], *, remove, pointer, also_alias, metadata, actor, reason)` (Typer-free) → `repo.publish_over(...)`; reuse `gather_sources` for the `changes` map.
- [ ] 3.2 `src/sartre/cli/app.py`: `publish-over` command — args `<coord> [sources…]`, options `--from <ref>` (base, default head), `--rm <path>` (repeatable), and the publish set (`-p/--pointer`, `--point`, `--as/--author`, `-m/--message`, `--meta`). Parse `--from` via the ref grammar; emit the new version id (human / `--json`).

## 4. Tests

- [ ] 4.1 Change-one-file: derive with `set={"cfg.json": …}` over a multi-file base → new version has the new cfg + unchanged rest; a counting backend shows only the changed blob staged (unchanged neither uploaded nor re-hashed).
- [ ] 4.2 Property (Hypothesis): no-op derive (`set={}`, `remove=()`) yields the base's version id; overriding a path with byte-identical content yields the base's version id and uploads nothing.
- [ ] 4.3 `remove` drops a path; removing an absent path raises the typed error.
- [ ] 4.4 Reused blobs resolvable: derived version resolves and unchanged files' bytes come from the pre-existing blobs (e.g. via a cold cache / direct remote).
- [ ] 4.5 Provenance + pointer: derived version's tip event carries actor/reason; head (or `--point` alias) advances via CAS.
- [ ] 4.6 CLI e2e: `publish-over` against head changing one file (+ `--rm`, `--from @version`); `--json` output; author required.

## 5. Gates

- [ ] 5.1 `ruff` clean, `pyright` clean, full default suite green.
- [ ] 5.2 `openspec validate publish-over --strict` passes.
