$ErrorActionPreference = 'Stop'

$TaskName = 'Atualizar Imóveis'
$ProjectDir = 'C:\Users\user\Desktop\Atualizacao_ZAP'
$PythonExe = 'C:\Users\user\AppData\Local\Programs\Python\Python313\python.exe'
$ScriptPath = Join-Path $ProjectDir 'atualizacao_zap.py'
$BatPath = Join-Path $ProjectDir 'executar_atualizacao_zap.bat'
$LogDir = Join-Path $ProjectDir 'logs'
$LogPath = Join-Path $LogDir 'scheduler_run.log'

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

function Invoke-SchtasksStrict {
    param([Parameter(Mandatory=$true)][string]$Arguments)
    $r = Invoke-SchtasksRaw -Arguments $Arguments
    if ($r.ExitCode -ne 0) {
        throw "Falha no schtasks (exit=$($r.ExitCode)):`n$($r.Output -join "`n")"
    }
    return $r
}

Write-Host '== Configuração definitiva do Agendador (23:00) =='
Write-Host "Tarefa: $TaskName"
Write-Host "Projeto: $ProjectDir"
Write-Host "Launcher BAT: $BatPath"

if (!(Test-Path -LiteralPath $ProjectDir)) { throw "Diretório do projeto não encontrado: $ProjectDir" }
if (!(Test-Path -LiteralPath $PythonExe)) { throw "Python não encontrado: $PythonExe" }
if (!(Test-Path -LiteralPath $ScriptPath)) { throw "Script principal não encontrado: $ScriptPath" }
if (!(Test-Path -LiteralPath $BatPath)) { throw "Launcher BAT não encontrado: $BatPath" }

$null = New-Item -ItemType Directory -Force -Path $LogDir
if (!(Test-Path -LiteralPath $LogPath)) {
    New-Item -ItemType File -Path $LogPath | Out-Null
}

$escapedTask = '"' + $TaskName + '"'
$escapedBat = '"' + $BatPath + '"'

# Consulta tarefa antiga sem quebrar fluxo se não existir
$old = Invoke-SchtasksRaw -Arguments "/Query /TN $escapedTask /FO LIST /V"
if ($old.ExitCode -eq 0) {
    Write-Host "Tarefa antiga encontrada. Removendo '$TaskName'..."
    Invoke-SchtasksStrict -Arguments "/Delete /TN $escapedTask /F" | Out-Null
} else {
    Write-Host 'Tarefa antiga não encontrada. Prosseguindo.'
}

# Cria tarefa oficial chamando APENAS o launcher BAT
Invoke-SchtasksStrict -Arguments "/Create /TN $escapedTask /SC DAILY /ST 23:00 /TR $escapedBat /F" | Out-Null
Invoke-SchtasksStrict -Arguments "/Change /TN $escapedTask /ENABLE" | Out-Null

# Validação pós-criação
$validation = Invoke-SchtasksStrict -Arguments "/Query /TN $escapedTask /FO LIST /V"
$txt = $validation.Output -join "`n"

if ($txt -notmatch '(?im)^Hora de início\s*:\s*23:00:00\b') {
    throw 'Tarefa criada, mas horário não ficou em 23:00:00.'
}
if ($txt -notmatch [Regex]::Escape($BatPath)) {
    throw 'Tarefa criada, mas comando não aponta para o launcher BAT esperado.'
}
if ($txt -match '(?im)^Status\s*:\s*Desabilitado\b') {
    throw 'Tarefa criada, porém está desabilitada.'
}

Write-Host "`n== Sucesso: tarefa recriada e validada =="
$validation.Output
