# Sharia Scope Regression QA

Date: 14 June 2026

## Verdict

This revision is a meaningful improvement. The live archive works, stored files are intact, load isolation is fixed, validation coverage is improved, generated reports render cleanly, and the app is bound to localhost.

The app is suitable for a supervised local demonstration after the saved-record status collision is fixed. It is not yet suitable for public deployment or an unsupervised compliance workflow.

## Verification summary

| Area | Result |
|---|---|
| Unit suite without live credentials | 22 passed, 1 live test skipped |
| Python compilation | Passed |
| Streamlit health | Passed |
| Server binding | `127.0.0.1:8501` only |
| Firebase key permissions | Owner-only (`600`) |
| Browser console | No errors or warnings |
| Validation dataset | 535 rows: 466 agree, 12 disagree, 57 indeterminate; 97.49% determinate agreement |
| Firestore records | 3 readable records |
| Cloud Storage | Reachable |
| Stored reports | 3/3 valid one-page PDFs |
| Stored source files | LUCK source present; valid 43-page PDF |
| SHA-256 verification | Every stored source/report checksum matched its downloaded bytes |
| Desktop UI | Functional and readable |
| Mobile width | No horizontal page overflow at 390 px |

## Findings

### P1: Archive status overwrites Shariah compliance status

`build_record()` saves `status` as the compliance state, such as `compliant` or `non_compliant`. `storage.save_run()` then replaces that same field with `complete` or `partial` to represent archive completion.

Consequences:

- Every current Firestore record has `status = complete`, not its compliance status.
- Saved-run badge color lookup cannot identify compliant/non-compliant records, so it falls back to grey.
- Filters or analytics based on `status` would be incorrect.

Fix: preserve `status` for compliance and introduce a separate `archive_status` field.

### P1: Reloading an AI run changes its provenance when re-saved

When a saved run is loaded, the app sets `from_ai = False`. A subsequent save therefore records the run as manual entry and automatically treats it as verified, even when the original record says `data_source = ai` and `verified = false`.

The current save also uses the provider/model currently selected in the sidebar rather than immutable provenance from the loaded/extracted run.

Fix: keep original `data_source`, provider, model, and verification state in a run-context object. Editing a loaded run should create an explicit derived revision rather than silently changing provenance.

### P1: Existing backfilled records do not contain the claimed provenance

The binaries were successfully backfilled, but the three existing records have no `data_source`, provider, model, evidence, verification, app version, rule version, extraction notes, or purification record.

The Saved UI currently converts these missing values into `manual entry` and `unverified`, which is not evidence that they were manual. LUCK is known to have been produced from an AI-assisted extraction.

Fix: display `Legacy / provenance unavailable` for missing values. Backfill only facts supported by the original run artifacts.

### P1: Delete has no confirmation and can orphan files

Clicking Delete immediately removes the Firestore record. Blob deletion exceptions are silently ignored, after which the record is deleted anyway.

Fix: require explicit confirmation showing company/ticker and document count. If blob deletion fails, retain the record with `archive_status = deletion_failed` so cleanup remains possible.

### P2: Backfill completion accepts a partial upload as complete

`attach_files_to_run()` marks a run complete when either a source or report path exists. If both files were requested and one upload fails, the run can still be labelled complete.

Fix: calculate completeness against the files expected for that run, as `save_run()` attempts to do.

### P2: Verification is not a true save gate

An AI-extracted run can still be saved and reported while unverified. The warning is clear, but the archive can accumulate records that look final.

Fix: distinguish Draft and Verified runs visibly. Permit draft saves, but require verification before `Final report` or `Complete` status.

### P2: PDF reports omit audit provenance

The generated tear-sheet does not show whether figures were verified, the AI provider/model, rule version, extraction evidence, checksums, or archive ID. The stored Lucky Cement report says only `Source: Archived run`.

Fix: add a compact audit section and generate a separate evidence appendix when AI extraction was used.

### P2: Mobile navigation remains clipped

At 390 px, the Methodology tab appears as an unclear clipped `M>` fragment. The five saved metrics also become a very long vertical stack.

Fix: use compact navigation on mobile and present saved metrics in a two-column grid or table.

### P3: Saved Runs remains difficult to scale

The dropdown is acceptable for three records but will become cumbersome. There is no search, filtering, pagination, or archive-health filtering.

Fix: replace it with a searchable run table once the archive grows beyond V1 demo size.

### P3: Loading a run emits a Streamlit widget-state warning

The load path sets purification widget state while those widgets also declare default values. It does not crash, but it creates noisy runtime warnings.

Fix: initialize widget defaults only when the session keys do not already exist.

## Confirmed improvements

- Cross-run source and extraction metadata contamination is fixed.
- Stale screening results are invalidated after input changes.
- Evidence, confidence, rationale, token usage, and cost are supported for new AI extractions.
- Market-price absence is explained clearly.
- PDF escaping and metadata wrapping are fixed.
- All twelve validation mismatches now contain explanatory notes.
- Validation includes the no-recent-financials appendix.
- Storage connection messaging is now truthful.
- Duplicate saved-run labels no longer hide records.
- Stored source/report checksums are correct.
- Localhost-only binding and credential file permissions are correct.

## Existing archive state

- LUCK: source PDF and tear-sheet present; both checksums valid.
- EFERT: tear-sheet present and checksum valid; no source document.
- HBL: tear-sheet present and checksum valid; no source document.
- All three records currently use `status = complete` because of the field collision.
- All three records lack the newly introduced provenance fields because they predate that schema.

## Release recommendation

Fix the status collision and AI-provenance preservation before creating more saved runs. Add delete confirmation before normal use. The remaining UI redesign can follow without blocking a supervised local demonstration.
