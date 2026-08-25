# RUC Data Quality Cleanup Plan

This is a plan only. Do not clean or delete any listed data without a fresh
backup and explicit human approval.

## 1. Duplicate DUIDs In `globe_nlz`

Current evidence:

- Duplicate DUID values exist in `globe_nlz`.
- NL106 appears 3 times in `globe_nlz`.
- Examples seen during audit include NL595, NL1168, NL128, NL154, NL110,
  NL133, NL163, NL165, NL168, NL545, and NL106.

Potential impact:

- Site selection may be confusing.
- Automated linking to a reference DUID can pick ambiguous reference data.

Proposed correction:

- Export duplicate groups for human review.
- Decide which row is canonical for each DUID.
- Preserve historical reference rows if they represent legitimate history.
- Add documentation or a uniqueness rule only after the business rule is clear.

Required backup:

- PostgreSQL backup.
- Master Tracker/reference workbook backup if reference data is synchronized.

Required approval:

- Project owner or Super Admin must approve each canonical DUID decision.

Validation:

- Confirm active operational sites still resolve correctly.
- Confirm search and site detail screens still show expected data.

Rollback:

- Restore database backup or reinsert exported rows.

## 2. NL106 Duplicate / Reference History

Current evidence:

- NL106 appears 3 times in `globe_nlz`.
- Current operational `telecom_sites` record remains Planning / 25 / Active /
  MISSING.
- Current Team Leader scope denies Benjamin access to NL106.

Potential impact:

- Reference/history rows can be mistaken for the operational site.

Proposed correction:

- Review NL106 reference rows with operations.
- Mark historical/reference rows clearly if they should remain.
- Avoid deleting until business ownership is confirmed.

Required backup:

- PostgreSQL backup and current Stage 6 disaster backup.

Required approval:

- Super Admin and operations owner.

Validation:

- Confirm NL106 operational site, daily log, and attendance remain unchanged.

Rollback:

- Restore database backup.

## 3. Numeric-Like `towerco` Values

Current evidence:

- 252 `globe_nlz.towerco` values look numeric or coordinate-like.

Potential impact:

- TowerCo reporting may be inaccurate.
- Site display may show coordinate-like data in a vendor/company field.

Proposed correction:

- Compare `globe_nlz` import columns against the original tracker/source file.
- Correct column mapping only after confirming the source layout.

Required backup:

- PostgreSQL backup.
- Source/reference workbook backup.

Required approval:

- Reference-data owner.

Validation:

- Re-run TowerCo/site reports and sample site detail pages.

Rollback:

- Restore database backup or reverse from exported pre-cleanup CSV.

## 4. Employee Mobile Beginning With `=`

Current evidence:

- One employee mobile value starts with `=`, which can be an Excel formula-risk
  pattern.

Potential impact:

- Excel exports could interpret the value as a formula if not safely handled.

Proposed correction:

- Confirm the correct phone number with HR.
- Store a safe literal value.
- Review Excel export escaping for formula-like values.

Required backup:

- PostgreSQL backup and affected project workbook backup.

Required approval:

- HR or Super Admin.

Validation:

- Confirm employee detail, search, and workbook display show the intended phone.

Rollback:

- Restore the previous field value from backup/export.

## 5. Orphan / Unmatched Workbooks

Current evidence:

- `excel_files\34983.xlsx` has no current `projects` row.
- `excel_files\50126.xlsx` has no current `projects` row.

Potential impact:

- Operators may confuse old/historical workbooks with active projects.

Proposed correction:

- Determine whether each workbook is historical, test data, or an accidentally
  detached real project.
- Archive rather than delete if business ownership is unclear.

Required backup:

- Stage 6 disaster backup and separate workbook copy.

Required approval:

- Super Admin.

Validation:

- Active workbooks `52143.xlsx` and `29297.xlsx` remain available.

Rollback:

- Restore archived workbook to `excel_files`.

## 6. BEN-POLISH Historical ID Artifacts

Current evidence:

- `id_cards\BEN-POLISH-174557_front.png` exists.
- `id_cards\BEN-POLISH-174557_back.png` exists.

Potential impact:

- Review/test ID outputs may be confused with official ID cards.

Proposed correction:

- Decide whether to archive or delete after manual visual review is complete.

Required backup:

- Stage 6 disaster backup.

Required approval:

- Super Admin or ID-card workflow owner.

Validation:

- Legitimate Benjamin ID artifacts remain intact.

Rollback:

- Restore files from backup.

## 7. Old NL106 Generated Handover Drafts

Current evidence:

- `generated_reports\NL106_HANDOVER_DRAFT_20260821_112157_92eb6492.zip`
  exists.
- `generated_reports\NL106_HANDOVER_DRAFT_20260821_120039_931f782d.zip`
  exists.

Potential impact:

- Draft packages may be mistaken for the latest handover output.

Proposed correction:

- Confirm whether drafts have business value.
- Archive or label old drafts rather than deleting immediately.

Required backup:

- Stage 6 disaster backup.

Required approval:

- Super Admin and operations owner.

Validation:

- Latest handover generation still works and historical reports policy is clear.

Rollback:

- Restore report ZIPs from backup.

## 8. `site_assignments` DUIDs Not Present In `telecom_sites`

Current evidence:

- Two `site_assignments` rows reference DUID values not present in
  `telecom_sites`.

Potential impact:

- Assignment reports may include historical/reference assignments without a
  current operational site.
- Team Leader scope may be broader or narrower than expected if assignments and
  teams diverge.

Proposed correction:

- Identify whether each assignment is historical, planned, or missing a site
  record.
- Either create the approved operational site record or close/archive the
  assignment after approval.

Required backup:

- PostgreSQL backup.

Required approval:

- Super Admin and operations owner.

Validation:

- Team Leader scope tests still pass.
- Assignment and site reports reconcile.

Rollback:

- Restore database backup or reinsert exported assignment rows.
