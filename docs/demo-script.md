# Lengrvis Demo Script

Last reviewed: 2026-06-08

These scripts keep demos tied to verified product paths. Use a clean test workspace with disposable files only.

## Preparation

- Start backend and desktop from a clean profile.
- Prepare a test folder with PDFs/DOCX/TXT, a few large dummy files, and a small Downloads-like folder.
- Pair one Android device or emulator when showing companion flows.
- Do not use personal files, real tokens, browser cookies, payment pages, or private screenshots.

## 60 Seconds

Goal: prove Lengrvis is a local OS agent, not a dressed-up chat box.

Data: no private files required; this path can run against an empty test profile.

1. Open the desktop home task workbench.
2. Point to the five task templates and their trust labels: local processing, cloud boundary, approval, rollback, estimated time.
3. Run "检查电脑状态".
4. Show the visible result boundary: the first screen template names the expected output, the Home 成果区 either shows a recent system-check result or the "等待只读快照" fallback, and the Computer page says "只读诊断，不改设置".
5. Open the timeline and show the Trust Manifest when a real task exists; for the read-only computer check, say explicitly that the smoke proves a local diagnostics path and no chat/run task is created.
6. Close with Settings model boundary profile: quick, privacy, hybrid. Capture the recommended model, estimated size, hardware state, speed estimate, repair action, and the "privacy failures do not auto-fall back to cloud" note.

Expected screen: the first template path produces visible local evidence or an honest fallback, with a clear next step and no silent write action.

Fallback: if backend health is unavailable, show the Home readiness item, the 成果区 "等待只读快照" card, and the disabled send-state copy; explain the exact failing dependency instead of claiming a completed task.

Smoke evidence: `npm --prefix desktop run smoke:first-launch` verifies the "检查电脑状态" template button, 成果区 recent-result/fallback card, Task Workspace read-only boundary, Computer page local diagnostics copy, and the no-chat/run invariant. `npm --prefix desktop run smoke:browser-activity` keeps the empty-profile fallback on desktop and mobile viewports.

## 3 Minutes

Goal: show the first useful local task and the mobile companion loop.

Data: use the prepared Downloads-like test folder and paired Android device or emulator.

1. Run "整理下载目录" against the prepared test folder.
2. Show dry-run style output: grouping and cleanup suggestions, with no deletion before approval.
3. Open mobile Companion and show the task in the task list.
4. Pause the task from mobile, then resume it.
5. Trigger or show an approval preview and reject it from mobile.
6. Open remote screen in read-only mode, then show the remote input grant state and "结束接管".

Expected screen: mobile can supervise, pause, resume, cancel, approve, reject, and view without becoming an independent high-privilege executor.

Fallback: if mobile LAN pairing fails, show desktop timeline plus the mobile API test result from `python -m pytest backend\tests\test_mobile_pairing.py -q`.

## 10 Minutes

Goal: make the platform thesis credible.

Data: use the prepared document set, large dummy files, and one non-private Skill/App integration sample.

1. Show a local file organization task.
2. Show document QA with citation labels from prepared local documents.
3. Show Trust Workspace elements: manifest, timeline, preview, approval decision, rollback entry when available.
4. Switch between quick, privacy, and hybrid modes in Settings; explain the model boundary cards.
5. Open Skills and show one non-private Skill/App integration sample with Product Manifest cards for file read/write, UI control, network, messaging, delete, preview, and rollback/handoff.
6. Show mobile task supervision and read-only screen stream.
7. Run `npm run qa:gate` or present the latest recorded result.

Expected story: Lengrvis wins by local execution, inspectable permissions, mobile supervision, and extensible app skills. It does not claim to beat OS vendors on distribution or frontier labs on model quality.

Fallback: if `npm run qa:gate` is too slow or blocked by artifacts, present the most recent dated command output for backend mobile tests, desktop/mobile typecheck, mobile smoke, and desktop browser activity smoke; mark the missing full gate as a demo risk instead of calling it passed.

Stop conditions:

- Do not demo on private user data.
- Do not claim iOS support.
- Do not claim cloud never sees anything unless privacy mode and the task path have been verified.

Evidence to record:

- Settings model boundary profile: quick/privacy/hybrid state, local model readiness or blocked reason.
- Local model smoke/readiness: command or UI result, date, machine, and model.
- Mobile companion flow: pairing, approval list, approve/reject, and read-only screen state when shown.
- Skill sample: package name, manifest cards, and whether preview/rollback/handoff was verified or waived.
- Document citation: source label, page/section when available, and disposable data set name.
- Template path: 60-second, 3-minute, or 10-minute path used, whether the outcome came from a real run or a smoke/mock result, the visible next step, and residual risks.
