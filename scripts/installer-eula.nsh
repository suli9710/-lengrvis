#include "MUI2.nsh"

# ============================================================================
# Lengrvis NSIS Installer — EULA Page Integration
# Touchpoint 1: Windows installer EULA display before installation
#
# This file is included by the main .nsi script via:
#   !include "installer-eula.nsh"
#
# After including, call: ShowEulaPage in the page flow.
# It pushes a custom EULA page between the welcome page and the directory page.
# ============================================================================

# --- Path to bundled EULA (set PROJECT_ROOT before including this file) ---
!ifdef PROJECT_ROOT
  !define EULA_FILE "${PROJECT_ROOT}\docs\legal\eula.md"
!else
  !define EULA_FILE "docs\legal\eula.md"
!endif

# --- Custom page variables ---
Var EulaAccepted
Var EulaDialog
Var EulaText
Var EulaCheckbox

Function ShowEulaPage
  ; Check if EULA file exists; if not, skip this page
  IfFileExists "${EULA_FILE}" +3 0
    MessageBox MB_OK|MB_ICONEXCLAMATION "EULA file not found. Installation will continue without license agreement."
    Goto SkipEulaPage

  ; Create custom dialog
  nsDialogs::Create 1018
  Pop $EulaDialog

  ; Title label
  ${NSD_CreateLabel} 0 0 100% 20u "\u6700\u7ec8\u7528\u6237\u8bb8\u53ef\u534f\u8bae"
  Pop $0
  SetCtlColors $0 "" "ffffff"

  ; Scrollable text area for EULA (fixed height ~150u = ~300px)
  ${NSD_CreateText} 0 25u 100% 150u ""
  Pop $EulaText
  SetCtlColors $EulaText "" "ffffff"

  ; Read the EULA file into the text control
  ClearErrors
  FileOpen $1 "${EULA_FILE}" r
  IfErrors EulaReadDone
  StrCpy $2 ""
  EulaReadLoop:
    FileRead $1 $3
    IfErrors EulaReadDone
    StrCpy $2 "$2$3"
    Goto EulaReadLoop
  EulaReadDone:
  FileClose $1
  SendMessage $EulaText ${WM_SETTEXT} 0 "STR:$2"

  ; Checkbox: "I have read and agree"
  ${NSD_CreateCheckbox} 0 180u 100% 15u "\u2611 \u6211\u5df2\u9605\u8bfb\u5e76\u540c\u610f\u4e0a\u8ff0\u8bb8\u53ef\u534f\u8bae"
  Pop $EulaCheckbox
  SetCtlColors $EulaCheckbox "" "ffffff"

  ; Track checkbox state
  StrCpy $EulaAccepted 0
  ${NSD_OnClick} $EulaCheckbox EulaCheckboxClick

  ; Show the dialog
  nsDialogs::Show

  SkipEulaPage:
FunctionEnd

Function EulaCheckboxClick
  ; Toggle acceptance state
  ${NSD_GetState} $EulaCheckbox $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $EulaAccepted 1
  ${Else}
    StrCpy $EulaAccepted 0
  ${EndIf}
FunctionEnd

Function ValidateEulaPage
  ; Block Next if EULA not accepted
  ${If} $EulaAccepted == 0
    MessageBox MB_OK|MB_ICONINFORMATION "\u8bf7\u52fe\u9009\u300c\u6211\u5df2\u9605\u8bfb\u5e76\u540c\u610f\u300d\u624d\u80fd\u7ee7\u7eed\u5b89\u88c5\u3002"
    Abort
  ${EndIf}
FunctionEnd

# --- Write consent.json after installation completes ---
Function WriteConsentRecord
  ; Resolve data directory
  ReadEnvStr $0 "LENGRVIS_DATA_DIR"
  StrCmp $0 "" 0 +2
    StrCpy $0 "$APPDATA\Lengrvis"

  ; Create directory if needed
  IfFileExists $0 +3 0
    CreateDirectory $0
    IfFileExists $0 0 Done

  ; Write consent.json line by line to avoid escaping issues
  FileOpen $2 "$0\consent.json" w
  IfErrors Done
  FileWrite $2 '{$\r\n'
  FileWrite $2 '  "eula_version": "v1.0",$\r\n'
  FileWrite $2 '  "eula_accepted_at": "__installer__",$\r\n'
  FileWrite $2 '  "privacy_version": "v1.0",$\r\n'
  FileWrite $2 '  "privacy_accepted_at": null,$\r\n'
  FileWrite $2 '  "installer_version": "${VERSION}",$\r\n'
  FileWrite $2 '  "platform": "windows"$\r\n'
  FileWrite $2 '}'
  FileClose $2

  Done:
FunctionEnd

# --- Integration instructions for the main .nsi script: ---
# In the page sections, add:
#   Page custom ShowEulaPage ValidateEulaPage
#   ; ... existing pages ...
#
# In the .onInstSuccess section (or after -InstallFiles):
#   Call WriteConsentRecord
#
# The desktop app's consentManager.readConsentRecord() will pick up the
# eula_accepted_at = "__installer__" and replace it with a real timestamp
# on first launch with a precise value.
