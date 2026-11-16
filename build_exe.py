"""
Script para construir el ejecutable de Reproductor de Discord de Los π
Ejecuta este archivo para crear el .exe automáticamente
"""
import os
import sys
import subprocess

def build_exe():
    print("=== Construyendo Reproductor de Discord de Los π ===\n")
    
    # 1. Verificar que PyInstaller esté instalado
    try:
        import PyInstaller
        print("✓ PyInstaller encontrado")
    except ImportError:
        print("✗ PyInstaller no encontrado. Instalando...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        print("✓ PyInstaller instalado")
    
    # Use the shared specification, including Discord voice and FFmpeg.
    print("Construyendo ejecutable...")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "overlay.spec"]

    try:
        subprocess.check_call(cmd)
        print("\n✓ Ejecutable creado exitosamente!")
        print(f"\n📁 El archivo .exe está en: {os.path.abspath('dist/ReproductorLosPi.exe')}")
        print("\n📝 Notas importantes:")
        print("   - El .exe está en la carpeta 'dist'")
        print("   - Puede tardar unos segundos en iniciar la primera vez")
        print("   - Tus amigos necesitarán tener VLC instalado en su PC")
        print("   - El archivo pesa ~50-100MB porque incluye todas las dependencias")
    except subprocess.CalledProcessError as e:
        print(f"\n✗ Error al construir: {e}")
        return False
    
    return True

if __name__ == "__main__":
    build_exe()
