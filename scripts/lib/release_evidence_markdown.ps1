function New-ReleaseEvidencePacketMarkdown {
    param(
        [Parameter(Mandatory = $true)]$Packet,
        [Parameter(Mandatory = $true)][int]$SettingsArtifactsPresent,
        [Parameter(Mandatory = $true)][int]$SettingsArtifactCount
    )

$markdownLines = New-Object System.Collections.Generic.List[string]
$markdownLines.Add("# Release Evidence Packet Summary")
$markdownLines.Add("")
$markdownLines.Add("- Generated: $($Packet.generated_at_utc)")
$markdownLines.Add("- Candidate: release_version=$($Packet.candidate_context.release_version); current_release_evidence=$($Packet.candidate_context.current_release_evidence); evidence_present=$($Packet.candidate_context.current_release_evidence_present).")
$markdownLines.Add("- JSON: $($Packet.outputs.redacted_json)")
$markdownLines.Add("- Markdown: $($Packet.outputs.redacted_markdown)")
$markdownLines.Add("- Status: $($Packet.summary.packet_status)")
$markdownLines.Add("- Packet role: evidence index only; packet_is_pass=false; evidence bucket count is not an acceptance count.")
$markdownLines.Add("- Release readiness: release_ready=$($Packet.summary.release_ready); claimable_release_signoff=$($Packet.summary.claimable_release_signoff); blocker_count=$($Packet.summary.release_readiness_blocker_count).")
$markdownLines.Add("- Scope: no product process starts, no network requests, no backend/desktop/mobile product changes.")
$markdownLines.Add("")
$markdownLines.Add("## Not Sign-Off")
foreach ($item in $Packet.not_clean_machine_or_signoff) {
    $markdownLines.Add("- $item")
}
$markdownLines.Add("")
$rcHandoff = $Packet.rc_handoff_requirements
$latestRcHandoffTemplate = $Packet.evidence.rc_handoff_template.latest_redacted_handoff_template
$markdownLines.Add("## RC Handoff Requirements")
$markdownLines.Add("- Status: $($rcHandoff.status); release_candidate_signoff=$($rcHandoff.release_candidate_signoff); packet_is_rc_signoff=$($rcHandoff.packet_is_rc_signoff).")
$markdownLines.Add("- Beginner instruction: $($rcHandoff.beginner_instruction)")
$markdownLines.Add("- Latest RC handoff template: found=$($latestRcHandoffTemplate.found), status=$($latestRcHandoffTemplate.handoff_status), source_contract=$($latestRcHandoffTemplate.source_contract_status), missing_required_fields=$($latestRcHandoffTemplate.missing_required_fields_count), required_fields_recorded=$($latestRcHandoffTemplate.required_fields_recorded), release_candidate_signoff=$($latestRcHandoffTemplate.release_candidate_signoff), claim_allowed=$($latestRcHandoffTemplate.claim_allowed).")
$markdownLines.Add("- Latest RC handoff counts: artifacts=$($latestRcHandoffTemplate.artifact_label_count), gate_entries=$($latestRcHandoffTemplate.gate_result_count), manual_p1_checks=$($latestRcHandoffTemplate.manual_p1_check_count), waivers=$($latestRcHandoffTemplate.waiver_count), residual_risks=$($latestRcHandoffTemplate.residual_risk_count); commands_run_by_helper=$($latestRcHandoffTemplate.gate_commands_run_by_this_helper).")
$markdownLines.Add("- Required before RC sign-off:")
foreach ($item in $rcHandoff.required_before_rc_signoff) {
    $markdownLines.Add("  - $item")
}
$markdownLines.Add("- Must not be recorded as:")
foreach ($item in $rcHandoff.must_not_be_recorded_as) {
    $markdownLines.Add("  - $item")
}
$markdownLines.Add("")
$markdownLines.Add("## Release Readiness Blockers")
foreach ($blocker in $Packet.release_readiness_blockers) {
    $markdownLines.Add("- $($blocker.id): status=$($blocker.status); claim_allowed=$($blocker.claim_allowed); required=$($blocker.required_evidence); next=$($blocker.beginner_next_step); must_not_claim=$($blocker.must_not_claim).")
}
$markdownLines.Add("")
$markdownLines.Add("## Evidence")
$markdownLines.Add("")
$markdownLines.Add("- Mobile LAN/WSS preflight: $($Packet.evidence.mobile_lan_wss_preflight.status); latest summary result=$($Packet.evidence.mobile_lan_wss_preflight.latest_redacted_summary.result)")
$androidTemplate = $Packet.evidence.android_real_device_evidence_template.latest_redacted_template
$markdownLines.Add("- Android real-device evidence template: $($Packet.evidence.android_real_device_evidence_template.status); found=$($androidTemplate.found); template_status=$($androidTemplate.template_status); real_device_result=$($androidTemplate.real_device_result); pass_claim_allowed=$($androidTemplate.pass_claim_allowed); not_signoff=fail-closed template only, not QR/HTTPS/WSS/certificate/screen/input/revoke/expiry pass evidence.")
$androidGate = $Packet.evidence.android_release_gate.latest_redacted_summary
$markdownLines.Add("- Android release gate: $($Packet.evidence.android_release_gate.status); latest status=$($androidGate.status); release_ready=$($androidGate.release_ready); preflight_only=$($androidGate.preflight_only); install_claim_allowed=$($androidGate.claim_controls.installable_android_app_claim_allowed); remote_claim_allowed=$($androidGate.claim_controls.real_device_remote_control_claim_allowed); artifact_label=$($androidGate.android_artifact.label); not_signoff=indexed redacted Android gate evidence only, not an APK/install/WSS pass created by this packet.")
$markdownLines.Add("- Mobile remote-input active-grant contract: $($Packet.evidence.mobile_remote_input_active_grant_contract.status); scope=$($Packet.evidence.mobile_remote_input_active_grant_contract.automated_scope); latest_execution=$($Packet.evidence.mobile_remote_input_active_grant_contract.latest_execution_status); verify=$($Packet.evidence.mobile_remote_input_active_grant_contract.verify_command); not_signoff=source/client contract only, not live device/WSS.")
$portableCompletionEvidence = $Packet.evidence.portable_first_screen_smoke.latest_redacted_status_log.natural_language_completion_evidence
$markdownLines.Add("- Portable first-screen smoke: found=$($Packet.evidence.portable_first_screen_smoke.latest_redacted_status_log.found), read_only_pass=$($Packet.evidence.portable_first_screen_smoke.latest_redacted_status_log.first_screen_read_only_pass), natural_language=$($Packet.evidence.portable_first_screen_smoke.latest_redacted_status_log.natural_language_submission_evidence), completion_evidence.level=$($portableCompletionEvidence.level), result_verified=$($portableCompletionEvidence.result_verified), completed_result_evidence=$($portableCompletionEvidence.completed_result_evidence).")
$markdownLines.Add("- Ollama/local-model contracts: $($Packet.evidence.ollama_local_model_contracts.contract_count) backend contract tests counted; latest execution not run by this packet.")
$localModelLatest = $Packet.evidence.local_model_clean_machine_template.latest_redacted_clean_machine_template
$markdownLines.Add("- Local model clean-machine template: found=$($localModelLatest.found), template_status=$($localModelLatest.template_status), artifact_status=$($localModelLatest.artifact_build_profile.status), runtime=$($localModelLatest.runtime.name) $($localModelLatest.runtime.version) [$($localModelLatest.runtime.status)], model=$($localModelLatest.model.name) $($localModelLatest.model.version) [$($localModelLatest.model.status)], install=$($localModelLatest.clean_machine_run.install.status), start=$($localModelLatest.clean_machine_run.start.status), pull=$($localModelLatest.clean_machine_run.pull.status), task_smoke=$($localModelLatest.clean_machine_run.task_smoke.status), clean_machine_signoff=$($localModelLatest.clean_machine_signoff), task_smoke_pass=$($localModelLatest.local_model_task_smoke_pass).")
$markdownLines.Add("- Diagnostics external review: expected status=$($Packet.evidence.diagnostics_external_review.expected_external_review_status), public_safe=$($Packet.evidence.diagnostics_external_review.expected_public_safe), machine_chain_status=$($Packet.evidence.diagnostics_external_review.machine_chain_status), manual_content_review_only_remaining=$($Packet.evidence.diagnostics_external_review.manual_content_review_only_remaining).")
$diagnosticsReviewPacket = $Packet.evidence.diagnostics_external_review.latest_redacted_review_packet
$markdownLines.Add("- Diagnostics external review packet: found=$($diagnosticsReviewPacket.found), review_status=$($diagnosticsReviewPacket.review_status), public_safe=$($diagnosticsReviewPacket.public_safe), claim_allowed=$($diagnosticsReviewPacket.claim_allowed), review_fields_complete=$($diagnosticsReviewPacket.review_fields_complete), actual_package_content_review_completed=$($diagnosticsReviewPacket.actual_package_content_review_completed), external_sharing_blocked=$($diagnosticsReviewPacket.external_sharing_blocked), separate_human_content_review_required=$($diagnosticsReviewPacket.separate_human_content_review_required).")
$markdownLines.Add("- Result-quality review packet: found=$($Packet.evidence.result_quality_review.latest_redacted_review_packet.found), review_status=$($Packet.evidence.result_quality_review.latest_redacted_review_packet.review_status), result_quality_signoff=$($Packet.evidence.result_quality_review.latest_redacted_review_packet.result_quality_signoff), completed_result_evidence=$($Packet.evidence.result_quality_review.latest_redacted_review_packet.completed_result_evidence).")
$rcHandoffTemplate = $Packet.evidence.rc_handoff_template.latest_redacted_handoff_template
$markdownLines.Add("- RC handoff template: found=$($rcHandoffTemplate.found), handoff_status=$($rcHandoffTemplate.handoff_status), release_candidate_signoff=$($rcHandoffTemplate.release_candidate_signoff), claim_allowed=$($rcHandoffTemplate.claim_allowed), gate_commands_run_by_this_helper=$($rcHandoffTemplate.gate_commands_run_by_this_helper), missing_required_fields=$($rcHandoffTemplate.missing_required_fields_count).")
$markdownLines.Add("- Settings local-model smoke: $SettingsArtifactsPresent/$($SettingsArtifactCount) expected screenshot artifacts present.")
$markdownLines.Add("")
$markdownLines.Add("## Next Manual Evidence")
foreach ($item in $Packet.next_manual_evidence_needed) {
    $markdownLines.Add("- $item")
}
return ($markdownLines -join "`n")
}
