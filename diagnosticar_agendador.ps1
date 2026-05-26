$ErrorActionPreference = 'Stop'

$TaskName = 'Atualizar Imóveis'
$ProjectDir = 'C:\Users\user\Desktop\Atualizacao_ZAP'
$BatPath = Join-Path $ProjectDir 'executar_atualizacao_zap.bat'
$PythonExe = 'C:\Users\user\AppData\Local\Programs\Python\Python313\python.exe'
$ScriptPath = Join-Path $ProjectDir 'atualizacao_zap.py'
$LogPath = Join-Path $ProjectDir 'logs\scheduler_run.log'

function Invoke-SchtasksRaw {
    param([Parameter(Mandatory=$true)][string]$Arguments)
    $tmp = [System.IO.Path]::GetTempFileName()
    try {
        cmd.exe /c "schtasks $Arguments > `"$tmp`" 2>&1"
        $exitCode = $LASTEXITCODE
        $output = Get-Content -Path $tmp -ErrorAction SilentlyContinue
        return [PSCustomObject]@{ ExitCode = $exitCode; Output = $output }
    }
    finally {
        Remove-Item -Path $tmp -Force -ErrorAction SilentlyContinue
    }
}

function Get-TaskField {
    param([string]$Text, [string]$Field)
    $m = [regex]::Match($Text, '(?im)^' + [regex]::Escape($Field) + '\s*:\s*(.+)$')
    if ($m.Success) { return $m.Groups[1].Value.Trim() }
    return 'N/A'
}

Write-Host '== Diagnóstico do Agendador ZAP =='
Write-Host "Horário atual: $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')"
Write-Host "Projeto: $ProjectDir"

$escapedTask = '"' + $TaskName + '"'
$r = Invoke-SchtasksRaw -Arguments "/Query /TN $escapedTask /FO LIST /V"

if ($r.ExitCode -ne 0) {
    Write-Host 'Tarefa oficial existe?: NÃO'
    Write-Host "Detalhe: $($r.Output -join ' ')"
} else {
    $txt = $r.Output -join "`n"
    $status = Get-TaskField -Text $txt -Field 'Status'
    $hora = Get-TaskField -Text $txt -Field 'Hora de início'
    $acao = Get-TaskField -Text $txt -Field 'Tarefa a ser executada'
    $startIn = Get-TaskField -Text $txt -Field 'Iniciar em'
    $lastRun = Get-TaskField -Text $txt -Field 'Horário da última execução'
    $lastResult = Get-TaskField -Text $txt -Field 'Último resultado'

    Write-Host 'Tarefa oficial existe?: SIM'
    Write-Host "Status: $status"
    Write-Host "Horário: $hora"
    Write-Host "Comando: $acao"
    Write-Host "Iniciar em: $startIn"
    Write-Host "Última execução: $lastRun"
    Write-Host "Último resultado: $lastResult"
    Write-Host "Aponta para Atualizacao_ZAP?: $([bool]($acao -match [regex]::Escape($ProjectDir)))"
    Write-Host "Aponta indevidamente para imagem_crm?: $([bool]($acao -match 'imagem_crm'))"
}

Write-Host "BAT existe?: $([bool](Test-Path -LiteralPath $BatPath))"
Write-Host "Python existe?: $([bool](Test-Path -LiteralPath $PythonExe))"
Write-Host "atualizacao_zap.py existe?: $([bool](Test-Path -LiteralPath $ScriptPath))"
Write-Host "scheduler_run.log existe?: $([bool](Test-Path -LiteralPath $LogPath))"

if (Test-Path -LiteralPath $LogPath) {
    Write-Host "`n--- Últimas 30 linhas de logs\\scheduler_run.log ---"
    Get-Content -Path $LogPath -Tail 30
}

Write-Host "`n--- Tarefas correlatas (nome/comando com ZAP/Imoveis/Atualiz/imagem_crm) ---"
$allCsv = schtasks /Query /FO CSV /V
$all = $allCsv | ConvertFrom-Csv
$matches = $all | Where-Object {
    $_.'Nome da tarefa' -match 'Atualizar Imóveis|Atualizar Imoveis|Atualização ZAP|Atualizacao ZAP|Atualizar ZAP|Atualizar Imóveis ZAP|ZAP|Imoveis|Imóveis|Atualiz' -or
    $_.'Tarefa a ser executada' -match 'imagem_crm|Atualizacao_ZAP|atualizacao_zap|executar_atualizacao'
}
if ($matches) {
    $matches | Select-Object 'Nome da tarefa','Status','Hora de início','Tarefa a ser executada','Iniciar em','Horário da última execução','Último resultado' | Format-List
} else {
    Write-Host 'Nenhuma tarefa correlata encontrada.'
}
