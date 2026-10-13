$ErrorActionPreference = 'Stop'
$docPath = 'D:\秋招项目MESAI\docs\FactoryPilot代码执行链路与评测面试手册_修订版.docx'
$qaPath = 'D:\秋招项目MESAI\.docx-code-walkthrough-revision-qa'
$pdfPath = Join-Path $qaPath 'FactoryPilot修订版检查.pdf'
New-Item -ItemType Directory -Force -Path $qaPath | Out-Null
$wordInstance = New-Object -ComObject Word.Application
$wordInstance.Visible = $false
$wordInstance.DisplayAlerts = 0
$docInstance = $null
try {
    $docInstance = $wordInstance.Documents.Open($docPath, $false, $true)
    $docInstance.Repaginate()
    $docInstance.ExportAsFixedFormat($pdfPath, 17)
    Write-Output ('Pages: ' + $docInstance.ComputeStatistics(2))
    Write-Output $pdfPath
} finally {
    if ($null -ne $docInstance) { $docInstance.Close([ref]0) }
    $wordInstance.Quit([ref]0)
    [Runtime.InteropServices.Marshal]::ReleaseComObject($wordInstance) | Out-Null
}
