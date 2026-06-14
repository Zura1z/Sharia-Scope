# Sharia Scope Firebase and UI Review

Date: 14 June 2026

## Executive verdict

The application is still correctly shaped as an analyzer rather than a lookup tool. The screening engine remains a good V1 foundation, and several earlier QA findings have been fixed.

The Firebase revision is not yet a complete or dependable run archive. Firestore works, but Cloud Storage is not enabled, the three saved records contain no source document or tear-sheet, and session-state contamination can associate a previous company's document and period with a newly loaded analysis.

The interface is functional but feels like a developer console. Provider credentials, Firebase credentials, bucket configuration, validation, methodology, manual inputs, extraction, reports, and saved runs all compete at the same visual level. This is the main reason it still does not feel like a finished finance product.

## Verified improvements

- 21/21 automated tests pass.
- Stale results are hidden when a visible financial input changes.
- PDF text is escaped and long metadata values wrap.
- The validation parser now provides notes for all twelve mismatches.
- AWS default-credential detection is better than the previous revision.
- The share-count help text now warns about unit scale.
- Firestore connectivity works and the Saved tab reads three live records.
- Desktop and 390 px mobile layouts have no horizontal page overflow.

## Live database state

Project: `shariascope`

- Firestore records: 3
- LUCK: metadata only; no source document; no tear-sheet
- EFERT: metadata only; no source document; no tear-sheet
- HBL: metadata only; no source document; no tear-sheet
- Cloud Storage buckets: 0

The UI currently says that each run is archived in full. That statement is false for every existing run.

## Critical findings

### P0: Firebase Admin was exposed through a network-wide Streamlit process

The live process was listening on `*:8501` while holding an unrestricted Firebase service-account credential. The app has no login or authorization boundary. Another device able to reach that port could browse runs and trigger writes through the server-side admin credential.

Immediate mitigation completed during review:

- Relaunched Streamlit on `127.0.0.1:8501` only.
- Changed the downloaded service-account file from mode `644` to `600`.

Required code/config fix: commit a local-only Streamlit configuration and do not deploy this Firebase Admin version publicly. A public version requires real user authentication, per-user ownership, and server-side authorization.

### P0: Loading a run can retain another company's source and extraction metadata

`pending_load` restores financial fields but does not clear or replace `source_doc` and `extraction_meta`. This was reproduced directly: loading `Loaded Co` left `OLD_COMPANY.pdf`, `OLD PERIOD`, and `OLD UNIT` in session state.

Impact: saving the loaded analysis can archive the wrong source PDF and generate a report with the wrong period or unit.

Required fix: treat each analysis as one explicit run object. Loading or starting a run must atomically replace company inputs, source document, extraction metadata, purification data, and result state.

### P1: “Full run archive” is not currently true

Cloud Storage has no bucket, so only Firestore metadata is saved. Existing records do not contain `source_path` or `report_path`. The sidebar still says “Each run is archived in full,” and the Saved tab introduction promises both documents.

Required fix: show separate connection states for Firestore and Storage. Disable “Save full run” unless both are available, or call it “Save metadata only” and show a persistent incomplete badge.

### P1: Saved records are not reproducible audit packages

The Firestore record omits important provenance:

- AI provider and model
- extraction notes and field-level evidence
- source filename when upload fails
- extraction timestamp
- methodology/rule version
- application version
- prompt/schema version
- purification inputs and output
- report checksum and source checksum
- verification/approval state

Loading also recomputes the verdict using current rules instead of clearly preserving the original run result.

Required fix: introduce a versioned run schema and an immutable manifest stored alongside the documents.

### P1: AI extraction remains unsupported by evidence

Classification-heavy values still have no page reference, source label, raw amount, confidence, or inclusion/exclusion rationale. The previous real-document test demonstrated model-dependent classification differences.

Required fix: require page-level evidence and user confirmation before a run can be marked Verified or saved as final.

## Persistence findings

### P1: Source capture happens only after successful AI extraction

Uploading a statement does not archive it unless extraction succeeds. If extraction fails, or the user uploads a new file without rerunning extraction, the saved source can be missing or stale.

### P1: File upload and Firestore write are not atomic

Files upload before the Firestore document is written. A Firestore failure can leave orphaned blobs. A partial file failure can create a record with only one document.

Use a staged status such as `saving`, write the manifest first, upload files, then finalize as `complete`. Add cleanup for failed runs.

### P2: Saved-run selector can hide duplicate records

Runs are converted to a dictionary keyed by ticker, company, status, and minute. Two identical runs saved in the same minute produce the same key, so one disappears from the selector.

Use the document ID as the select value and a non-unique human label only for display.

### P2: No lifecycle controls

There is no delete, archive, rename, note, verification status, or duplicate-run protection. The fixed 100-record limit has no pagination.

### P2: Storage functions lack meaningful automated coverage

The added test only checks optional credential resolution. Save, partial failure, list, duplicate labels, download, stale source handling, and load round-trip are not unit-tested.

### P3: Type contract is incorrect

`save_run()` is annotated as returning `str` but returns a dictionary. This weakens static checking and API clarity.

## Interface findings

### P1: The interface exposes infrastructure instead of the user workflow

The permanent sidebar is dominated by API keys, model selection, Firebase key upload, bucket name, and methodology reference. These are settings, not primary analysis tasks.

Move all provider and database configuration into a Settings dialog. The normal interface should only show concise connection indicators.

### P1: The Analyze screen has no visible workflow

The primary experience is a collapsed AI uploader followed by a long blank form. Users are not guided through document selection, extraction review, market-data completion, screening, and saving.

Use three explicit stages:

1. Source: upload a report or choose manual entry; select period and units.
2. Verify: review extracted fields with source page/evidence and resolve warnings.
3. Result: verdict, failed rules, purification, report, and save state.

### P1: Saved-run verdict is visually broken

The HBL verdict renders as `Non-Com...` in the four-column metric row. A status must never be truncated, especially on a compliance screen.

Render status as a dedicated badge above the metrics. Use the remaining space for all five financial checks.

### P1: Mobile navigation does not fit

At 390 px, the fourth tab is clipped into an unclear `M>` fragment. Four text-heavy tabs are unsuitable for this width.

Use a compact navigation menu or three destinations: Analyze, Runs, More. Put Validation and Methodology under More.

### P2: Saved Runs is a dropdown, not a run-management view

The page is difficult to scan and will degrade badly beyond a few records. It omits investment ratio, NLA check, source status, file status, verification state, provider, model, and methodology version.

Use a searchable table with columns for company, ticker, period, verdict, verification, source/report availability, and date. Selecting a row should open a full detail view.

### P2: “Load into Analyze” does not take the user to Analyze

The data loads, but the Saved tab remains selected. The user must manually switch tabs to discover that anything happened.

Navigate automatically or show a clear success message with a single “Open analysis” action.

### P2: Decorative pills consume the highest-value space

The five feature pills repeat static capabilities while important run state is absent. Replace them with document, extraction, verification, price, and save-status indicators.

### P2: Terminology is inconsistent

The interface says “Computes the 6 ratios,” but the methodology displays five financial checks plus a separate business screen. Use “five financial screens plus business activity screening.”

### P3: Visual language feels like a prototype

Emoji-led tabs, dense dark surfaces, a large configuration sidebar, default Streamlit controls, and equal emphasis on every section make the product feel assembled rather than designed. The dark theme is readable, but hierarchy is weak.

## Recommended interface structure

### Global shell

- Top bar: Sharia Scope, New analysis, Runs, Methodology, Settings icon.
- No permanent credential form.
- Small connection indicators: AI Ready, Archive Incomplete, or Offline.

### Analyze

- Header: company, ticker, period, unit, analysis state.
- Step 1 panel: source document and input method.
- Step 2 verification table: field, extracted value, source page, evidence, confidence, confirmed checkbox.
- Separate market-price panel with price date and source.
- Sticky action bar: Run screening.
- Result band: status badge and concise explanation.
- Ratio table/cards grouped by Pass, Fail, Missing.
- Actions: Generate report, Save verified run.

### Runs

- Search, status filter, period filter, document-status filter.
- Table/list instead of a dropdown.
- Detail drawer showing all inputs, outputs, evidence, source/report files, and archive status.
- Actions: Open, duplicate, export package, archive/delete.

### Validation and Methodology

- Move out of the primary workflow.
- Validation should be an internal QA/admin screen.
- Methodology should remain readable reference material, not a peer of the daily Analyze action on mobile.

## Recommended architecture decision

For a single-user local V1, the simplest dependable archive remains a local run folder containing a JSON manifest, source document, and generated report, optionally indexed by SQLite. It is easier to inspect, back up, and keep complete.

If Firebase is retained, treat it as a real multi-user backend project: no service-account upload in the normal UI, no public Streamlit process with admin credentials, authenticated ownership, versioned run manifests, atomic save states, and enabled Cloud Storage.

## Release decision

- Formula engine: suitable for supervised V1 use.
- Firestore metadata history: working prototype.
- Full document archival: not working yet.
- Interface: functional, but requires workflow redesign.
- Public deployment: unsafe in the current Firebase Admin architecture.
- Local supervised demonstration: acceptable after fixing cross-run source contamination and truthful archive status.
