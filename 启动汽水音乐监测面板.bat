@echo off
setlocal
title 汽水音乐监测面板

set "APP=%~dp0panel.py"
if not exist "%APP%" (
    echo [错误] 本文件需要和 panel.py 放在同一目录。
    pause
    exit /b 1
)

set "PYW="

rem 1) PATH 中的 pythonw
if not defined PYW for %%C in (pythonw.exe) do if not "%%~$PATH:C"=="" set "PYW=%%~$PATH:C"

rem 2) 常见安装位置的 pythonw（取第一个存在的）
if not defined PYW (
    for %%P in (
        "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
        "%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe"
        "C:\Python313\pythonw.exe"
        "C:\Python312\pythonw.exe"
    ) do if not defined PYW if exist %%P set "PYW=%%~P"
)

rem 3) 退而使用 py 启动器
if not defined PYW (
    where py >nul 2>nul
    if not errorlevel 1 (
        start "" py -3 "%APP%" %*
        exit /b 0
    )
)

if not defined PYW (
    echo [错误] 未找到 Python 3 运行环境。
    echo        请安装 Python 3.8+（安装时勾选 tcl/tk），
    echo        或把 pythonw.exe 所在目录加入 PATH。
    pause
    exit /b 1
)

start "" "%PYW%" "%APP%" %*
exit /b 0
