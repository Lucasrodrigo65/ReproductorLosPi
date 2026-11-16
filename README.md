# Reproductor de Discord de Los π

Un reproductor de música de escritorio para Windows, creado como proyecto personal para compartir canciones con amigos en Discord.

Permite buscar música en YouTube, reproducirla en la PC y enviarla a un canal de voz mediante un bot propio. La idea nació para simplificar las sesiones de música del servidor: elegir una canción en la app y hacerla sonar para todos.

<p align="center">
  <img src="assets/server.png" alt="Imagen del reproductor de Los π" width="180">
</p>

## Mismo bot desde varias PCs

El modo compartido usa un servidor online como única conexión del bot a Discord.
Las PCs se conectan por HTTPS con una clave de acceso; no necesitan el token del bot.
Incluye cola común, pausa, siguiente y actualización de estado en todos los clientes.
Cada PC prepara y envía el audio por HTTPS antes de agregarlo a la cola; el servidor
reproduce ese archivo temporal sin descargarlo desde YouTube.

Leé [SHARED.md](SHARED.md) para desplegar el servidor y configurar los controles.
El servidor usa `Dockerfile`, `requirements-server.txt` y `railway.json`.
La app para los amigos se genera con:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm shared.spec
```

El resultado es `dist/LosPiCompartido.exe`. Los pasos de bot local que siguen
corresponden al modo original; no se debe ejecutar ese modo al mismo tiempo que el
servidor online con el mismo token.

## Funciones

- Búsqueda de canciones y reproducción mediante enlaces de YouTube.
- Reproducción local con pausa, volumen, barra de progreso y avance o retroceso.
- Envío de la canción seleccionada a un canal de voz de Discord.
- Tono de prueba para comprobar la transmisión de audio.
- Guardado del token del bot cifrado con Windows DPAPI y del canal elegido.
- Atajo `Ctrl + Alt + M` para mostrar u ocultar la ventana.
- Generación de un ejecutable con icono personalizado y FFmpeg incluido.

## Tecnologías

| Componente | Tecnología |
| --- | --- |
| Interfaz | Python y Tkinter |
| Búsqueda y extracción de audio | yt-dlp |
| Reproducción local | VLC / python-vlc |
| Bot y conexión de voz | discord.py, PyNaCl y davey |
| Conversión de audio | FFmpeg mediante imageio-ffmpeg |
| Protección del token guardado | Windows DPAPI |
| Empaquetado | PyInstaller |

## Requisitos

- Windows 10 u 11 de 64 bits.
- Python 3.14 de 64 bits para ejecutar el código o compilarlo.
- Conexión a Internet.
- VLC Media Player de 64 bits para escuchar música localmente.
- Un bot propio de Discord e invitación al servidor donde se reproducirá música.

Si utilizás un ejecutable ya compilado, no necesitás instalar Python. La reproducción del bot usa FFmpeg y no depende de VLC.

## Instalación

Descargá el repositorio y abrí PowerShell en su carpeta:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe overlay.py
```

## Configurar Discord

Este repositorio se entrega **sin token, sin canal predeterminado y sin configuración personal**. Cada usuario debe configurar su propio bot.

1. Creá una aplicación en el [Discord Developer Portal](https://discord.com/developers/applications).
2. En **Bot**, obtené el token del bot.
3. En **OAuth2 → URL Generator**, seleccioná el scope `bot` y los permisos **View Channels**, **Connect** y **Speak**. Invitá el bot al servidor con el enlace generado.
4. Activá el **Modo desarrollador** en los ajustes avanzados de Discord y copiá el ID del canal de voz.
5. En la app, abrí **Configurar bot**, ingresá el token y el ID del canal y pulsá **Guardar**.
6. Entrá al canal de voz, elegí una canción en la app y pulsá **Reproducir en Discord**.

No se necesitan intents privilegiados. Más detalles y diagnóstico en [DISCORD.md](DISCORD.md).

### Configuración local

El token se ingresa en el programa, no en el código. Se guarda cifrado para el usuario de Windows actual en:

```text
%LOCALAPPDATA%\ReproductorLosPiPublic\discord.json
```

El canal se guarda en ese mismo archivo. La configuración permanece fuera del repositorio y del ejecutable. Esta edición usa una carpeta distinta de la edición personal para no cargar sus credenciales guardadas.

## Uso

Buscá una canción o pegá su enlace de YouTube. Para escuchar en la PC, usá los controles locales. Para compartirla, seleccioná un resultado y pulsá **Reproducir en Discord**; si no hay un resultado seleccionado, se usa la URL pegada o la canción actual.

Enviar otra canción reemplaza la anterior. **Desconectar bot** detiene la reproducción en Discord y retira el bot del canal. **Probar sonido** envía un tono de tres segundos y reemplaza el audio actual.

La app debe permanecer abierta para mantener el bot conectado.

## Compilar el ejecutable

Con las dependencias instaladas:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm overlay.spec
```

El resultado estará en:

```text
dist/ReproductorLosPi.exe
```

También podés usar `build.bat` desde la carpeta del proyecto. Los archivos de `build/`, `dist/` y los ejecutables están excluidos del control de versiones.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_discord_player test_discord_settings test_room_selector test_shared_server test_shared_desktop
```

Las pruebas de voz usan conexiones simuladas. Las de configuración comprueban el cifrado real de Windows, la recuperación desde otro proceso y la conservación del archivo ante errores de guardado. La escucha real en Discord requiere una prueba con el bot configurado.

## Estructura

```text
overlay.py                 Interfaz y controles del reproductor
discord_player.py          Conexión y transmisión de voz a Discord
discord_settings.py        Configuración local y cifrado del token
overlay.spec               Configuración de PyInstaller
requirements.txt           Dependencias
assets/                    Imagen e iconos de la app
test_discord_player.py     Pruebas de voz
test_discord_settings.py   Pruebas de configuración
DISCORD.md                 Guía de configuración y diagnóstico
```

## Alcance actual

- El modo original reproduce una canción por vez. El modo compartido agrega cola y avance automático.
- Los controles locales de pausa, volumen y progreso no controlan el audio del bot.
- Admite canales de voz normales; los canales de escenario no están soportados.
- La extracción de audio depende de la disponibilidad del video y de los cambios de YouTube.
- El guardado cifrado de credenciales está implementado para Windows.

Las versiones de `discord.py` y `davey` están fijadas juntas porque su compatibilidad afecta al cifrado de voz. La compilación verifica esa combinación; ver [la explicación técnica](DISCORD.md#compatibilidad-de-cifrado-de-voz).

## Sobre el proyecto

Un proyecto personal para aprender y practicar interfaces de escritorio, programación asíncrona, integración con Discord y distribución de aplicaciones Python en Windows, con una utilidad concreta para compartir música entre amigos.
