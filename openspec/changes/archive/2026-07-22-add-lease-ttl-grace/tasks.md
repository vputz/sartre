## 1. Model (verified basis, already run)

- [x] 1.1 Confirm `model/GCLease.tla` + `GCLease_fixed.cfg` (SelfCheck=TRUE → PASS) and `GCLease_buggy.cfg` (SelfCheck=FALSE → CHECK_FAILED) pass the pinned TLC protocol (`.claude/skills/tla-verify/scripts/run_tlc.sh`).
- [x] 1.2 Confirm `model/GCGrace.tla` + `GCGrace_ok.cfg` (Grace>MaxGap → PASS, non-vacuous GCSweep) and `GCGrace_bad.cfg` (Grace<=MaxGap → CHECK_FAILED) pass the protocol.

## 2. Registry lease surface: TTL + durability

- [x] 2.1 `src/sartre/ports.py`: update the `Registry` lease-surface signatures — `acquire_lease(version, hashes, ttl)`, add `renew_lease(lease_id, ttl) -> bool`; document expiry-filtered `active_leased_hashes`/`active_leased_versions`.
- [x] 2.2 `src/sartre/_sql.py`: add a `leases` table to `_schema()` (`lease_id` seq PK, `version`, `hashes` JSON/TEXT, `expires_at`). Dialect seam for the clock: a `_now_sql` / `_expiry_sql(ttl)` hook so SQLite and Postgres compute `expires_at` and compare `expires_at > now` server-side.
- [x] 2.3 `_sql.py`: implement `acquire_lease` (INSERT with registry-clock expiry), `renew_lease` (`UPDATE leases SET expires_at=<now+ttl> WHERE lease_id=? AND expires_at > <now>`, return `rowcount==1`), `release_lease` (DELETE), and expiry-filtered `active_leased_hashes`/`active_leased_versions` (`WHERE expires_at > <now>`). Drop the in-memory `_leases` dict.
- [x] 2.4 `src/sartre/sqlite.py`: provide the SQLite clock hook (`strftime`/`julianday` or store epoch seconds; keep it comparable and monotonic). `src/sartre/postgres.py`: provide the Postgres clock hook (`now()` / `CURRENT_TIMESTAMP` + interval).
- [x] 2.5 `src/sartre/memory.py`: replace the process-lease map with an expiry-aware map plus an **injectable clock** (default wall-clock); `acquire`/`renew` stamp `now()+ttl`, active-root queries filter unexpired. Keep observational equivalence for unexpired leases.

## 3. Publish: heartbeat + self-check

- [x] 3.1 `src/sartre/repository.py`: `publish` acquires the lease with a default `ttl`; start a background heartbeat thread renewing every `ttl/2`; stop it in `finally`.
- [x] 3.2 `publish` self-check: before `commit` and before `set_pointer`, call `renew_lease`; on `False` release the lease and raise a retryable error (do not commit/advance). Ensure the existing conflict-retry path and the new lapse-abort path compose.
- [x] 3.3 Choose defaults (ttl, heartbeat interval) as named constants; document that they are liveness/throughput knobs, not safety.

## 4. Grace-period backstop

- [x] 4.1 `src/sartre/store.py` (+ `ports.py`): add a blob mtime accessor (`Store.mtime(hash)` / `BlobBackend` via `fs.info(...)["mtime"]`), returning a comparable timestamp.
- [x] 4.2 `src/sartre/repository.py`: `RetentionPolicy`/`gc` gains an optional `grace` (default 0). In mark **and** the re-validate-at-sweep step, add "younger than `grace`" to the live set (blob-only). Keep aged-out unreferenced blobs collectable.
- [x] 4.3 Thread `grace` sensibly (via `gc(policy)`); default 0 preserves current lease-only behavior.

## 5. Tests

- [x] 5.1 Real-Postgres multi-writer lease visibility: two `PostgresRegistry` instances over one DSN — A acquires a lease, B's `active_leased_hashes` sees it; with a short ttl it vanishes for both after expiry (gated by the `postgres_dsn` fixture).
- [x] 5.2 SQLite/memory: TTL expiry drops a lease from the active set; `renew_lease` returns `True` while valid, `False` once lapsed.
- [x] 5.3 Property/Hypothesis: publish self-check abort — a lease forced to lapse mid-publish makes `publish` raise (retryable) and never leaves a committed manifest over a swept blob; `BlobSafe` holds on the live system.
- [x] 5.4 Grace-period: with an injected fake clock + a settable-mtime blob backend, a young unleased blob is retained and an aged unleased blob is reclaimed by `gc`.
- [x] 5.5 Differential machine stays green: leases now durable but observationally equivalent when unexpired (no lease is allowed to expire mid-sequence in the differential run).

## 6. Gates

- [x] 6.1 `pyright` clean, `ruff` clean, full default suite green (Postgres/S3 groups skip cleanly without infra).
- [x] 6.2 Run the Postgres group locally (docker available) to confirm cross-instance lease visibility + expiry actually pass.
- [x] 6.3 `openspec validate add-lease-ttl-grace --strict` passes.
