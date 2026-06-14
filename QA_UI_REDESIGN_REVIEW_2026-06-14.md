# Sharia Scope UI Redesign QA Review

Date: 14 June 2026

## Executive assessment

The latest redesign is a substantial product improvement. The permanent configuration sidebar is gone, the Analyze workflow is easier to follow, Saved runs are scannable, and the app remains operational against the live Firebase project. The formula engine and existing storage records are healthy.

The build is not ready to be treated as an auditable analyzer yet. Two provenance/archive defects can make a loaded or derived run look more trustworthy and more complete than it actually is. These should be fixed before calling V1 complete.

## Findings

### P1: Loaded AI and legacy runs are falsely presented as manual and verified

When any saved run is loaded, `from_ai` is forced to `False`. The Analyze workflow then treats every non-AI state as verified, labels it "Manual entry," suppresses the AI verification warning, and writes `Verified: Yes` into the PDF audit section.

This affects an unverified AI run as well as a legacy run whose provenance is explicitly unknown. The saved-record logic later preserves the original `verified=False`, so the UI/PDF and Firestore metadata can disagree about the same analysis.

Evidence:

- `app.py:303` sets `from_ai = False` for every loaded run.
- `app.py:319-320` defines all non-AI state as verified.
- `app.py:333-334` renders green "Manual entry" and "Verified" chips.
- `app.py:383-384` tells the user there is no AI extraction to verify.
- `app.py:465-468` exports the loaded run as verified in the PDF audit.
- AppTest with a synthetic `data_source=ai`, `verified=False` record reproduced the green Verified state and no warning while `verified_chk` remained false.

Recommendation: derive the UI and PDF audit state from `loaded_provenance` when present. Use three explicit origins: manual, current AI extraction, and loaded run. Unknown legacy provenance should display "Verification unknown," never "Verified."

### P1: Derived revisions can lose their source document and still be marked complete

Loading a saved run always clears `source_doc`. Re-saving that run therefore creates a derived revision without the original source statement. Storage completeness only checks files supplied to the current call, so a report-only revision is marked `archive_status=complete`. If both source and report are absent, `_is_complete` also returns true.

This conflicts with the product promise that every run is a complete, reproducible package.

Evidence:

- `app.py:297` discards the loaded source document.
- `app.py:503-507` saves only the current `source_doc` and generated report.
- `storage.py:162-169` uses a vacuous completeness check; no expected files returns `True`.
- Direct regression probe confirmed `_is_complete({}, None, None) is True`.

Recommendation: either download and carry the parent source into the revision, or store an immutable source reference inherited from the parent. Define required artifacts explicitly and never mark a run complete without its required report and source/reference.

### P2: The workflow shows "Verified" before any analysis exists

On a fresh blank screen, the state strip shows green "Manual entry" and "Verified" chips even though no source or figures have been entered. A single numeric field is enough to classify the source as manual and verified.

This weakens the meaning of the most important trust indicator in the product.

Evidence: `app.py:319-336`; reproduced in desktop and mobile browser QA.

Recommendation: use neutral states such as "Input not started" and "Verification not applicable" until enough data exists. Verification should only become positive after an explicit manual confirmation or a completed manual-entry validation step.

### P2: Loading from Saved provides no visible completion or navigation

The Load button sets `pending_load` and reruns while leaving the user in Saved. There is no toast, success message, or automatic switch to Analyze, so the action appears to do nothing. The user must infer that they should click Analyze.

Evidence: `app.py:587-590`; reproduced against the live LUCK record.

Recommendation: store navigation in an explicit session-state key and switch to Analyze after loading. At minimum show a confirmation naming the loaded company.

### P2: Saved runs remain awkward on mobile

At 390 px width, the table hides most columns and truncates "Non-Compliant by Nature." Opening a run requires the small selection checkbox; clicking the row text did not open it. This is functional, but not an ergonomic mobile history experience.

Recommendation: render mobile runs as compact selectable rows/cards with ticker, company, full verdict, source, and file status. Keep the data table for desktop.

### P3: New analysis does not explicitly clear the upload widget

`reset_analysis()` clears stored document bytes and analysis state, but the financial-statement uploader has no explicit key and is not reset. Streamlit may retain the selected upload after "New analysis," allowing accidental re-extraction of the previous company.

Evidence: `app.py:245-251` and `app.py:350-351`.

Recommendation: give the uploader a versioned key and increment it during reset.

### P3: Archive connectivity is cached for the whole session

The Storage reachability result is cached under the bucket name and never refreshed. A transient failure, newly enabled bucket, or corrected permissions can leave the header stuck on "metadata only" until the session is restarted.

Evidence: `app.py:115-118`.

Recommendation: add a refresh action or a short TTL, and invalidate the check whenever credentials or bucket settings change.

## Verification performed

- `pytest`: 22 passed, 1 skipped. The skipped test is the credential-gated live Firebase round trip.
- Python compilation: passed.
- Streamlit AppTest: app renders without exceptions; loaded-AI provenance regression reproduced.
- Live Firebase, read-only: three records found; compliance status and `archive_status` are separated correctly. LUCK has source + report; EFERT and HBL have reports.
- Browser QA: desktop Analyze, Settings, Saved, load flow, and 390x844 mobile Analyze/Saved views.
- Browser console: no application errors; only WebSocket-close warnings caused by deliberate reloads.
- Paid AI calls: none. This review used existing records and synthetic local data.

## Release recommendation

Fix the two P1 items before tagging V1. The remaining P2/P3 items can follow immediately after, but the verification strip should be corrected in the same pass because it is central to the analyzer's credibility.

