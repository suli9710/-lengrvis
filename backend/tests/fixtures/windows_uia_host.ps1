param(
    [Parameter(Mandatory=$true)][string]$StateDirectory,
    [switch]$Discoverable,
    [ValidateRange(15, 900)][int]$LifetimeSeconds = 60
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -ReferencedAssemblies System.Windows.Forms -TypeDefinition @'
public class FixtureForm : System.Windows.Forms.Form {
    protected override bool ShowWithoutActivation { get { return true; } }
    protected override System.Windows.Forms.CreateParams CreateParams {
        get {
            var parameters = base.CreateParams;
            parameters.ExStyle |= 0x08000000; // WS_EX_NOACTIVATE
            return parameters;
        }
    }
}
'@
$form = New-Object FixtureForm
$form.Text = 'Lengrvis isolated UIA fixture'
$form.ShowInTaskbar = $false
$form.Opacity = 0
$editor = New-Object System.Windows.Forms.TextBox
$editor.Name = 'editor'
$editor.AccessibleName = 'Fixture editor'
$editor.Text = 'Isolated source text'
$button = New-Object System.Windows.Forms.Button
$button.Name = 'submit'
$button.Text = 'Apply'
$status = New-Object System.Windows.Forms.TextBox
$status.Name = 'status'
$status.AccessibleName = 'Fixture status'
$status.ReadOnly = $true
$button.Add_Click({ $status.Text = $editor.Text })
$form.Controls.AddRange(@($editor, $button, $status))
# Allocate real native control handles and pump messages without showing a
# window, taking focus, using the clipboard, or interacting with user apps.
if ($Discoverable) { $form.Show() }
$handles = @{
    process_id = $PID
    window = $form.Handle.ToInt64()
    editor = $editor.Handle.ToInt64()
    button = $button.Handle.ToInt64()
    status = $status.Handle.ToInt64()
}
[System.IO.File]::WriteAllText((Join-Path $StateDirectory 'ready.json'), ($handles | ConvertTo-Json -Compress))
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 100
$startedAt = [DateTime]::UtcNow
$timer.Add_Tick({
    if ((Test-Path -LiteralPath (Join-Path $StateDirectory 'stop')) -or ([DateTime]::UtcNow - $startedAt).TotalSeconds -gt $LifetimeSeconds) {
        [System.Windows.Forms.Application]::ExitThread()
    }
})
$timer.Start()
try { [System.Windows.Forms.Application]::Run() }
finally { $timer.Dispose(); $form.Dispose() }
