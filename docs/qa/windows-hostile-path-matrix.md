# Windows Hostile Path Matrix

Date: 2026-06-08

Scope: backend path authorization, file mutation sandbox checks, and skill zip/package path safety evidence. This is a QA evidence matrix, not a claim that every Windows filesystem alias is closed.

## Audit Mapping

| Audit item | Status | Evidence | Remaining gap |
| --- | --- | --- | --- |
| P1-1 Windows hostile path regression | Partially covered | `backend/tests/test_path_security.py`, `backend/tests/test_file_tools_path_sandbox.py`, `backend/tests/test_app_skill_packages.py` | 8.3 short names inside allowed roots, Unicode normalization/confusable names, and broad reparse tags beyond symlink/junction need explicit implementation or manual release notes. |
| Zip-slip / package import escape | Covered for traversal members and schema path traversal | `test_zip_skill_import_rejects_zip_slip_member`, `test_zip_skill_import_rejects_manifest_schema_path_escape` | This covers local skill zip import only; it is not marketplace signing, provenance, or malware scanning. |
| File mutation TOCTOU through replaced parent | Covered for key file tools | `test_write_text_rechecks_parent_after_authorization`, `test_move_file_rechecks_destination_parent_after_authorization`, `test_trash_file_rechecks_target_parent_after_authorization` | Coverage is focused on write/move/trash. Copy/rename/create folder rely on the same helper path but do not have every race variant enumerated. |

## Current Automated Coverage

| Class | Current evidence | Notes |
| --- | --- | --- |
| `..` traversal and URL-encoded traversal | `test_rejects_paths_that_escape_workspace` | Uses the real `app.core.paths.resolve_authorized` contract. |
| Absolute POSIX and Windows system paths | `test_rejects_paths_that_escape_workspace` | Includes `/etc/passwd` and `C:\Windows\System32\drivers\etc\hosts`. |
| Windows UNC / namespace / device paths | `test_rejects_windows_namespace_paths` | Covers `\\localhost\C$`, `\\?\C:`, and `\\.\NUL` style inputs. |
| Windows ADS / colon stream syntax | `test_rejects_paths_that_escape_workspace` | Covers `safe.txt:stream` and `safe.txt:stream:$DATA`; `resolve_authorized` rejects colon stream syntax outside the drive/UNC anchor. |
| Symlink escape | `test_rejects_symlink_escape` | Skips only when the platform cannot create the symlink. |
| Junction / reparse-like directory escape for mutations | `backend/tests/test_file_tools_path_sandbox.py` | Uses symlink first, then Windows junction fallback through `mklink /J`. |
| Reparse point detection before writes | `_ensure_mutation_path_safe`, `_reject_reparse_points`, `_is_reparse_point` as exercised by file tool tests | Covers symlink and `Path.is_junction()` where available. |
| Parent swap after initial authorization | File tool TOCTOU tests | Confirms authorization is rechecked after a parent directory is replaced by an escape link. |
| Zip member traversal | `test_zip_skill_import_rejects_zip_slip_member` | Exercises the real `import_skill()` zip path before install. |
| Manifest schema path traversal | `test_zip_skill_import_rejects_manifest_schema_path_escape` | Confirms schema hydration cannot escape the package root. |

## Remaining Gaps

| Gap | Why it matters | Evidence rule |
| --- | --- | --- |
| 8.3 short names inside authorized roots | Short aliases can make review/audit paths ambiguous even when final resolution remains inside a root. | Record as manual residual risk unless a deterministic fixture can create and assert short-name behavior on the release host. |
| Unicode normalization and confusable names | Different tools may display visually similar names differently. | Do not claim normalization hardening without tests for NFC/NFD and confusable path display in audit/approval output. |
| Reparse tags beyond symlink/junction | Windows supports more reparse-point types than the helpers currently enumerate. | Keep the claim to symlink/junction unless a broader Windows attribute/tag check is added. |
| Full copy/rename/create-folder race matrix | Shared helpers reduce risk, but every mutation route does not have a dedicated race test. | For release sign-off, run the focused backend path tests and record any newly added race cases separately. |

## Suggested Evidence Command

```powershell
python -m pytest backend/tests/test_path_security.py backend/tests/test_file_tools_path_sandbox.py backend/tests/test_app_skill_packages.py -q
```

If a platform cannot create symlinks or junctions, record the skip reason as a platform limitation, not as proof that the Windows reparse path passed.
