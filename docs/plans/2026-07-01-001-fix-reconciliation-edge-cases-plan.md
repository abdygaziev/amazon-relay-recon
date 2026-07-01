---
title: "fix: Harden reconciliation edge cases"
created_at: "2026-07-01"
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
plan_depth: standard
---

# fix: Harden reconciliation edge cases

## Goal Capsule

| Field | Value |
|---|---|
| Objective | Fix three reviewed reconciliation defects: Pacific daylight saving week classification, payment total over-deduplication, and advertised `.xls` upload support. |
| Authority | User-requested plan from code review findings. |
| Execution profile | Focused bug-fix work in the existing Streamlit + pandas app. |
| Stop conditions | Stop if Amazon Relay exports use timezone or payment-line identifiers that contradict the assumptions below. |
| Tail ownership | Implementation should leave the existing unit test suite green and add targeted regression coverage for each finding. |

---

## Product Contract

### Summary

RelayRecon should classify completed loads into the correct Amazon payment week, total payment rows without dropping legitimate repeated charges, and only advertise payment file formats the installed dependencies can read.

### Problem Frame

The current reconciliation logic can misclassify summer Sunday deliveries because it treats Pacific time as a fixed UTC-8 offset.
The payment total logic can also collapse distinct same-amount charge rows when they share the same load, trip, gross pay, and item type.
The UI accepts `.xls` payment uploads even though the dependency set only installs `openpyxl`, leaving legacy Excel uploads dependent on an undeclared reader.

### Requirements

- R1. Payment-week classification must use Pacific calendar dates with daylight saving handled correctly when a stop UTC offset is present.
- R2. Payment-week classification must preserve current behavior when no UTC offset is available.
- R3. Total paid must dedupe true duplicate payment rows without collapsing distinct repeated charges for the same load or trip.
- R4. Invoice summary-row handling must keep returning the matching summary total when blank-ID summary rows equal the detail total.
- R5. Payment upload format support must match dependencies and error behavior for `.csv`, `.xlsx`, and `.xls`.
- R6. Regression tests must cover each reviewed defect and preserve existing paid, missing, cancelled, next-week, and total-paid behavior.

### Acceptance Examples

- AE1. Given a completed load at `07/05/2026 00:30` with stop offset `-7` and selected week ending `2026-07-04`, when reconciliation runs, then the load is classified as next week rather than missing.
- AE2. Given two distinct `$25.00` accessorial rows for the same load and item type with different detail fields, when total paid is calculated, then both rows contribute to the total.
- AE3. Given two exact duplicate payment rows for the same load and amount, when total paid is calculated, then the duplicate contributes once and the duplicate warning remains.
- AE4. Given a legacy `.xls` upload, when the app accepts the extension, then the required reader dependency is installed and the file path is tested or the extension is no longer offered.

### Scope Boundaries

In scope:

- Fix the reviewed defects in reconciliation logic, payment file reading, dependencies, tests, and any directly related README text.
- Keep the current Streamlit interaction model and current payment-week input model.
- Preserve current exports and table shapes unless a column is needed only for internal dedupe logic.

Deferred to follow-up work:

- Full sample-file fixtures for real Amazon Relay exports.
- UI redesign, analytics changes, or new report sections.
- A broader payment schema normalization layer beyond the fields needed to make totals correct.

---

## Planning Contract

### Key Technical Decisions

- KTD1. Use IANA timezone conversion for Pacific dates. Python's standard `zoneinfo` module supports IANA zones and daylight saving behavior, so the payment-week date should derive from `America/Los_Angeles` rather than a fixed UTC-8 offset.
- KTD2. Treat stop offsets as the source timezone for naive Amazon export timestamps. The app currently parses completion timestamps as naive local stop times and stores `Stop 2 UTC Offset`; localization should attach the stop offset before converting to Pacific.
- KTD3. Dedupe payment totals by row identity before semantic fallback. Exact duplicate rows are safe to collapse, but same load/trip/amount/item-type rows can be legitimate separate payments; prefer exact normalized row duplicates unless Amazon exports provide a stronger unique line identifier.
- KTD4. Keep invoice-summary matching ahead of detail-row dedupe. The current summary-row behavior is intentional and tested; it should still win when blank-ID summary rows match the detail total.
- KTD5. Resolve `.xls` support explicitly. Either add `xlrd` and test `.xls` reading or remove `.xls` from accepted upload types; because the UI currently advertises `.xls`, the plan prefers adding `xlrd` unless implementation discovers deployment constraints.

### Assumptions

- Amazon Relay completion timestamps represent the local time at the stop when a stop UTC offset is present.
- Amazon payment weeks are Sunday through Saturday in Pacific time, matching the app's sidebar copy.
- Distinct payment rows may share load ID, trip ID, gross pay, and item type; an exact duplicate row across all source columns is a stronger duplicate signal than that subset.

### Sources and Research

- Existing code: `reconcile.py` owns column detection, date parsing, payment-week classification, total paid calculation, and result construction.
- Existing UI: `app.py` owns payment upload type filters and Excel sheet selection.
- Existing tests: `tests/test_reconcile.py` uses `unittest` with pandas DataFrames and already covers next-week rollover, duplicate totals, summary totals, and validation errors.
- Python docs: `zoneinfo` provides IANA time zone support and uses system timezone data or the first-party `tzdata` package when available: https://docs.python.org/3/library/zoneinfo.html
- pandas docs: `read_excel` supports Excel formats through reader engines; `.xls` support requires an engine capable of the legacy format: https://pandas.pydata.org/docs/reference/api/pandas.read_excel.html
- xlrd docs: `xlrd` reads historical `.xls` files and no longer reads newer Excel formats, making it suitable only for the legacy branch: https://xlrd.readthedocs.io/

---

## Implementation Units

### U1. Correct Pacific payment-week date conversion

- **Goal:** Classify completion dates using the correct Pacific calendar date across standard time and daylight saving time.
- **Requirements:** R1, R2, R6; covers AE1.
- **Dependencies:** None.
- **Files:** `reconcile.py`, `tests/test_reconcile.py`.
- **Approach:** Replace the fixed `pacific_offset = -8` conversion with timezone-aware conversion. Convert the parsed naive completion timestamp into a timezone-aware timestamp using the provided stop UTC offset, then convert that instant to `America/Los_Angeles` and return the Pacific date. Keep the existing no-offset branch returning `completion.date()` so legacy exports without offsets preserve current behavior.
- **Patterns to follow:** Keep the logic inside `payment_week_date()` and retain the existing `_parse_utc_offset()` boundary. Add focused tests alongside the existing `test_multi_stop_trip_rolls_to_next_week_when_last_stop_finishes_after_week_end`.
- **Test scenarios:**
  - Covers AE1. A Sunday `07/05/2026 00:30` completion with offset `-7` and week end `2026-07-04` returns Pacific date `2026-07-05` and lands in `next_week_df`.
  - A winter Sunday `01/04/2026 00:30` completion with offset `-8` and week end `2026-01-03` returns Pacific date `2026-01-04` and lands in `next_week_df`.
  - A completion with no offset keeps current behavior by returning the parsed completion date.
  - A non-Pacific stop offset that converts across the Pacific date boundary uses the converted Pacific date, not the stop-local date.
- **Verification:** The existing suite passes, and the new tests prove the previous fixed-offset failure no longer reproduces.

### U2. Preserve legitimate repeated payment rows in totals

- **Goal:** Make total paid calculation collapse true duplicate exports without dropping distinct repeated charge rows.
- **Requirements:** R3, R4, R6; covers AE2 and AE3.
- **Dependencies:** None.
- **Files:** `reconcile.py`, `tests/test_reconcile.py`.
- **Approach:** Keep the current gross-pay parsing and summary-row matching, then change keyed detail dedupe to use exact normalized payment-row identity rather than only trip/load/gross/item type. Normalize cells enough to avoid false differences from pandas `NaN` and numeric string formatting, but do not collapse rows that differ in description, invoice line metadata, date, or other source columns.
- **Patterns to follow:** Keep total logic in `calculate_total_paid()` and keep warnings returned through the existing `warnings` list. Mirror the existing `test_payment_total_deduplicates_same_load_and_amount` and `test_payment_total_uses_blank_id_invoice_summary_when_it_matches_details` style.
- **Test scenarios:**
  - Covers AE2. Two same-load `$25.00` rows with the same `Item Type` but different `Description` values total `$50.00`.
  - Covers AE3. Two exact duplicate rows still total once and emit the duplicate payment warning.
  - A blank-ID summary row matching detail total still returns the summary amount.
  - A keyed detail set with a duplicate row and one distinct repeated charge returns the sum of one duplicate plus the distinct charge.
- **Verification:** Total-paid tests demonstrate both preservation of distinct repeated charges and continued duplicate suppression.

### U3. Align Excel upload support with dependencies

- **Goal:** Ensure every payment upload extension accepted by the Streamlit UI has an installed reader and a clear read path.
- **Requirements:** R5, R6; covers AE4.
- **Dependencies:** None.
- **Files:** `app.py`, `requirements.txt`, `README.md`, tests if app file-reading helpers are extracted or covered.
- **Approach:** Prefer declaring `xlrd` for legacy `.xls` support while keeping `openpyxl` for `.xlsx`. Make the `.xls` branch explicit enough that future dependency changes are obvious. If implementation discovers `xlrd` is unsuitable for deployment, remove `.xls` from the accepted uploader types and README instead; do not leave the UI advertising unsupported files.
- **Patterns to follow:** Keep `read_payment_file()` responsible for selecting Excel sheets and keep the "Payment Details" sheet preference. Preserve CSV behavior.
- **Test scenarios:**
  - `.xlsx` files continue to use the current Excel path and prefer the `Payment Details` sheet when present.
  - `.csv` files continue to read through `pd.read_csv`.
  - Covers AE4. `.xls` support is either backed by an installed `xlrd` dependency and an explicit read path, or the accepted upload types and README no longer claim `.xls`.
  - Unsupported Excel read failures still surface through the existing Streamlit error path with the file name.
- **Verification:** Dependency declaration and UI accepted file types agree, and any helper-level tests or manual checks prove the selected branch for `.xls` is intentional.

### U4. Refresh documentation and regression coverage

- **Goal:** Keep docs and tests aligned with the corrected reconciliation behavior.
- **Requirements:** R5, R6.
- **Dependencies:** U1, U2, U3.
- **Files:** `README.md`, `tests/test_reconcile.py`, optional new test file if app helper tests are split out.
- **Approach:** Update README file-format notes only where behavior changes. Keep reconciliation tests focused on business logic; add app helper tests only if file-reading logic is made importable without executing Streamlit top-level UI.
- **Patterns to follow:** Existing README is short and user-facing; keep updates concise. Existing tests use direct DataFrame construction and should remain readable.
- **Test scenarios:**
  - The full existing reconciliation suite still passes after all changes.
  - New tests cover the DST boundary, repeated-charge total, exact-duplicate total, and selected `.xls` support decision.
  - No test depends on real carrier data or private invoice files.
- **Verification:** The plan is complete when regression tests fail on the reviewed defects before implementation and pass after the fixes.

---

## Verification Contract

| Gate | Applies To | Done Signal |
|---|---|---|
| Unit regression suite | U1, U2, U4 | `tests/test_reconcile.py` covers DST payment-week conversion, repeated charges, exact duplicates, and existing classification behavior. |
| App upload support check | U3 | Accepted upload extensions in `app.py`, dependencies in `requirements.txt`, and README file-format docs agree. |
| Full test suite | All units | Existing and new tests pass under the repo's pytest suite. |
| Manual Streamlit smoke check | U3, U4 | Upload controls still accept the intended file types and validation errors remain user-readable. |

---

## Risks & Dependencies

- **Timezone data availability:** `zoneinfo` depends on system timezone data or the `tzdata` package. macOS and most Linux deployments have system data, but adding `tzdata` may be prudent if deployment targets are slim containers.
- **Excel engine behavior:** `xlrd` supports historical `.xls` only. The implementation must not route `.xlsx` through `xlrd`.
- **Payment duplicate ambiguity:** Without a stable Amazon invoice line ID, exact row dedupe is the conservative correction. If future exports include a line identifier, a follow-up can move dedupe to that key.

---

## Definition of Done

- DOD1. `payment_week_date()` handles Pacific daylight saving boundaries and preserves no-offset behavior.
- DOD2. `calculate_total_paid()` preserves distinct repeated charge rows while still deduping exact duplicate payment rows.
- DOD3. `.xls` support is either fully dependency-backed or removed from the advertised upload surface and documentation.
- DOD4. Regression tests cover all three reviewed findings.
- DOD5. Existing paid, missing, cancelled, next-week, duplicate-total, summary-total, and validation tests remain green.
- DOD6. No unrelated UI redesign, report reshaping, or broad parser refactor is included.
