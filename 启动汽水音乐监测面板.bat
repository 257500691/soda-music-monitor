@echo off
setlocal enabledelayedexpansion
title 汽水音乐监测面板

set "APP=%~dp0panel.py"
if not exist "%APP%" (
    echo [错误] 找不到 panel.py
    echo        本 bat 必须和 panel.py 放在同一目录。
    pause
    exit /b 1
)

set "FOUND="
set "PYW="

rem ================= 按优先级找一个「带 tkinter」的 Python =================
rem 逐个候选执行 python.exe -c "import tkinter" 校验，第一个通过的就用它，
rem 这样即使 PATH 里有个残缺的 python，也不会挑错。

rem 1) DSH 运行时自带的 Python（本机已实测可用）
for /d %%D in ("%USERPROFILE%\.dsh\dsh-runtimes\*") do (
    if not defined FOUND call :try "%%~fD\dependencies\python\python.exe"
)

rem 2) 常见安装位置
call :try "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python39\python.exe"
call :try "C:\Python313\python.exe"
call :try "C:\Python312\python.exe"
call :try "C:\Python311\python.exe"

rem 3) PATH 里的 python.exe
for %%C in (python.exe) do call :try "%%~$PATH:C"

rem 4) 都没有就退而用 py 启动器
if not defined FOUND (
    where py >nul 2>nul
    if not errorlevel 1 (
        start "" py -3 "%APP%" %*
        exit /b 0
    )
    echo [错误] 未找到可用的 Python 3（需要 3.8+ 且带 tkinter）。
    echo.
    echo 可以这样做：
    echo   1. 安装 Python 3.8+，安装时务必勾选 tcl/tk 和 Add to PATH
    echo   2. 或用 -Check 参数查看检测详情: %~nx0 -Check
    echo.
    pause
    exit /b 1
)

rem 优先用同目录的 pythonw.exe 启动（不弹黑框），没有就用 python.exe
set "PYW=%FOUND:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%FOUND%"

if /i "%~1"=="-Check" (
    echo [Check] python exe : %FOUND%
    echo [Check] launch exe : %PYW%
    "%FOUND%" -c "import sys, tkinter; print('[Check] version', sys.version.split()[0], '/ tkinter', tkinter.TkVersion)"
    pause
    exit /b 0
)

start "" "%PYW%" "%APP%" %*
exit /b 0

rem ------------------------------------------------------------------
:try
if defined FOUND exit /b 0
if "%~1"=="" exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import tkinter" >nul 2>nul
if errorlevel 1 exit /b 0
set "FOUND=%~1"
exit /b 0
