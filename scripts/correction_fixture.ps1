param([Parameter(Mandatory=$true)][string]$StatePath)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = 'KK correction acceptance WinForms'
$form.Size = New-Object System.Drawing.Size(640,300)
$form.StartPosition = 'Manual'
$form.Location = New-Object System.Drawing.Point(300,260)
$form.TopMost = $true

$label = New-Object System.Windows.Forms.Label
$label.Text = 'Выберите нужную кнопку'
$label.Font = New-Object System.Drawing.Font('Segoe UI',18)
$label.AutoSize = $true
$label.Location = New-Object System.Drawing.Point(150,35)
$form.Controls.Add($label)

$left = New-Object System.Windows.Forms.Button
$left.Text = 'Левая кнопка'
$left.Name = 'LeftButton'
$left.Size = New-Object System.Drawing.Size(190,75)
$left.Location = New-Object System.Drawing.Point(65,110)
$left.Add_Click({ '{"selected":"left"}' | Set-Content -LiteralPath $StatePath -Encoding utf8 })
$form.Controls.Add($left)

$right = New-Object System.Windows.Forms.Button
$right.Text = 'Правая кнопка'
$right.Name = 'RightButton'
$right.Size = New-Object System.Drawing.Size(190,75)
$right.Location = New-Object System.Drawing.Point(365,110)
$right.Add_Click({ '{"selected":"right"}' | Set-Content -LiteralPath $StatePath -Encoding utf8 })
$form.Controls.Add($right)

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 120000
$timer.Add_Tick({ $form.Close() })
$timer.Start()
[void]$form.ShowDialog()
