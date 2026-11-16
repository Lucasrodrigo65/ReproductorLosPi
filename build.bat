@echo off
echo ========================================
echo   Reproductor de Discord de Los π - Build Script
echo ========================================
echo.

REM Activar entorno virtual si existe
if exist .venv\Scripts\activate.bat (
    echo Activando entorno virtual...
    call .venv\Scripts\activate.bat
)

REM Instalar las dependencias de audio y Discord
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

REM Conservar los otros archivos de dist y usar la configuracion compartida
python -m PyInstaller --noconfirm overlay.spec

if %errorlevel% equ 0 (
    echo.
    echo ========================================
    echo   BUILD EXITOSO!
    echo ========================================
    echo.
    echo El archivo .exe esta en: dist\ReproductorLosPi.exe
    echo.
    echo IMPORTANTE:
    echo - Tus amigos necesitan tener VLC instalado
    echo - El .exe puede tardar unos segundos en iniciar
    echo - Pesa aproximadamente 50-100MB
    echo.
    pause
) else (
    echo.
    echo ========================================
    echo   ERROR EN EL BUILD
    echo ========================================
    echo.
    pause
)
