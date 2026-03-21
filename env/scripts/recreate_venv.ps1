<#
.SYNOPSIS
    기존 .venv를 삭제하고 Python 3.11로 새 가상환경을 생성한다.
.DESCRIPTION
    1. 기존 .venv 디렉토리 삭제
    2. Python 3.11로 venv 생성
    3. pip 업그레이드
    4. 의존성 설치 (env/requirements/all.txt + google-genai + certifi)
.NOTES
    사전 조건: Python 3.11이 설치되어 있어야 한다.
    설치 방법: https://www.python.org/downloads/release/python-3119/
    설치 시 "Add python.exe to PATH" 체크 해제 권장 (Anaconda 충돌 방지)
#>

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path "$PSScriptRoot\..\.."
$VenvDir = Join-Path $ProjectRoot ".venv"
$RequirementsFile = Join-Path $ProjectRoot "env\requirements\all.txt"

# --- Python 3.11 탐색 ---
$PythonCandidates = @(
    "py -3.11"                                                          # py launcher
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"            # 기본 설치 경로
    "C:\Python311\python.exe"                                           # 커스텀 경로
)

$PythonCmd = $null

# py launcher 우선 시도
try {
    $ver = & py -3.11 --version 2>&1
    if ($ver -match "Python 3\.11") {
        $PythonCmd = "py -3.11"
    }
} catch { }

# 직접 경로 탐색
if (-not $PythonCmd) {
    foreach ($candidate in $PythonCandidates[1..($PythonCandidates.Length - 1)]) {
        if (Test-Path $candidate) {
            $ver = & $candidate --version 2>&1
            if ($ver -match "Python 3\.11") {
                $PythonCmd = $candidate
                break
            }
        }
    }
}

if (-not $PythonCmd) {
    Write-Host ""
    Write-Host "[ERROR] Python 3.11을 찾을 수 없습니다." -ForegroundColor Red
    Write-Host ""
    Write-Host "설치 방법:" -ForegroundColor Yellow
    Write-Host "  1. https://www.python.org/downloads/release/python-3119/ 에서 다운로드"
    Write-Host "  2. 'Windows installer (64-bit)' 선택"
    Write-Host "  3. 설치 시 'Add python.exe to PATH' 체크 해제 (Anaconda 충돌 방지)"
    Write-Host "  4. 'Customize installation' -> 기본 옵션 유지 -> Install"
    Write-Host "  5. 이 스크립트를 다시 실행"
    Write-Host ""
    exit 1
}

Write-Host "=== venv 재생성 스크립트 ===" -ForegroundColor Cyan
Write-Host "Python: $PythonCmd"
Write-Host "프로젝트: $ProjectRoot"
Write-Host ""

# --- 1. 기존 .venv 삭제 ---
if (Test-Path $VenvDir) {
    Write-Host "[1/4] 기존 .venv 삭제 중..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $VenvDir
    Write-Host "  삭제 완료." -ForegroundColor Green
} else {
    Write-Host "[1/4] 기존 .venv 없음, 건너뜀." -ForegroundColor Gray
}

# --- 2. 새 venv 생성 ---
Write-Host "[2/4] Python 3.11 venv 생성 중..." -ForegroundColor Yellow
if ($PythonCmd -eq "py -3.11") {
    & py -3.11 -m venv $VenvDir
} else {
    & $PythonCmd -m venv $VenvDir
}
Write-Host "  venv 생성 완료." -ForegroundColor Green

# --- 3. pip 업그레이드 ---
$PipExe = Join-Path $VenvDir "Scripts\pip.exe"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "[3/4] pip 업그레이드 중..." -ForegroundColor Yellow
& $PythonExe -m pip install --upgrade pip 2>&1 | Out-Null
Write-Host "  pip 업그레이드 완료." -ForegroundColor Green

# --- 4. 의존성 설치 ---
Write-Host "[4/4] 의존성 설치 중..." -ForegroundColor Yellow
& $PipExe install -r $RequirementsFile google-genai certifi
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 의존성 설치 실패." -ForegroundColor Red
    exit 1
}
Write-Host "  의존성 설치 완료." -ForegroundColor Green

# --- 완료 ---
Write-Host ""
Write-Host "=== 완료 ===" -ForegroundColor Cyan
Write-Host "활성화 명령:" -ForegroundColor Yellow
Write-Host "  .\.venv\Scripts\Activate.ps1     (PowerShell)"
Write-Host "  source .venv/Scripts/activate     (Git Bash)"
Write-Host ""

# 버전 확인
& $PythonExe --version
& $PipExe --version
