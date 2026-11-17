# Los π: un bot online, varias PCs

El servidor es el único proceso que usa el token y conecta el bot a Discord.
Las apps de escritorio son controles remotos autenticados mediante una clave de
acceso al servidor. Cerrar una app no detiene la música para los demás.

## Comportamiento compartido

- Cada pedido agrega una canción a la cola de su servidor, con el nombre de quien la pidió.
- Los pedidos simultáneos se procesan de uno en uno; no crean conexiones paralelas.
- Un pedido reenviado por una interrupción de red no agrega una canción duplicada.
- La cola avanza automáticamente al terminar una pista.
- Todos ven la canción actual, el estado de pausa y las próximas tres canciones.
- Todos los que tienen la clave pueden pausar, continuar, saltar y detener la sesión.
- El bot puede reproducir simultáneamente en varios servidores, en una sala por
  servidor. Las claves, colas y controles están aislados entre grupos. Para cambiar
  de sala dentro del mismo servidor, se detiene su sesión y se elige otro destino.
- El tono de prueba solo se permite cuando no hay una sesión activa.
- El bot sale del canal de voz tras cinco minutos con la cola vacía y sin canción
  actual. Una pista pausada conserva la sesión. Al agregar música vuelve a entrar.

La cola es temporal: se vacía si el servidor se reinicia o se actualiza. Esta versión
no usa una base de datos para conservar canciones pendientes.

Ver [MULTI_SERVER.md](MULTI_SERVER.md) para habilitar otros servidores sin mezclar sesiones.

## Servidor online

La imagen Docker instala Python, FFmpeg y las dependencias de voz. No necesita Windows
ni VLC. Se inicia con `python shared_server.py`.

Variables del servidor:

| Variable | Uso |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Token del bot, ingresado como secreto en el proveedor de hosting. |
| `CONTROL_KEY` | Clave existente de un único servidor, asociado con ALLOWED_GUILD_IDS. |
| `ALLOWED_GUILD_IDS` | Un único ID para la clave heredada CONTROL_KEY; no admite acceso global. |
| `GUILD_ACCESS_KEYS` | JSON privado que asigna IDs de servidores a claves distintas. Ver MULTI_SERVER.md. |
| `PORT` | Puerto HTTP asignado por el proveedor; por defecto 8080. |

El token y la clave no se escriben en el repositorio ni dentro del ejecutable.
La clave de control permite manejar la sesión del grupo, pero no se puede usar como
token del bot. Para retirar el acceso al grupo, cambiá `CONTROL_KEY` en el servidor.

### Railway

1. Crear un servicio desde el repositorio con el `Dockerfile` de la raíz.
2. Cargar las variables secretas en Railway y generar un dominio HTTPS.
3. Mantener **una réplica**, sin escalado horizontal ni suspensión por inactividad.
4. `railway.json` configura la comprobación de salud y el cierre de la versión anterior.
5. No ejecutar al mismo tiempo las versiones anteriores que conectan el bot localmente.

El bloqueo de instancia impide iniciar dos procesos en el mismo host o contenedor;
no es un bloqueo distribuido entre varios servicios independientes. No despliegues
el mismo bot en otro servicio ni aumentes el número de réplicas.

El hosting puede tener costo según el plan y el consumo. La app nueva descarga el
audio desde la PC de quien agrega la canción y lo envía por HTTPS. Railway reproduce
el archivo recibido; no necesita acceder a YouTube para esos pedidos. La descarga
local también puede fallar si YouTube restringe ese video.

`/healthz` comprueba que el proceso HTTP está activo. La validación real del token,
los permisos y el audio requiere consultar las salas y probar el tono en Discord.

## App para los pibes

Usar **LosPiCompartido.exe**, generado con:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm shared.spec
```

En **Servidor compartido**, ingresar:

1. La dirección HTTPS del servidor.
2. La clave de acceso compartida.
3. El nombre que se mostrará en la cola.

Elegir la sala y pulsar **Agregar a Discord** para enviar una canción.
Esperar a que termine la preparación y transferencia antes de cerrar la app. Una vez
confirmado el pedido, la canción queda en el servidor y ya no requiere esa PC encendida.
No es una transmisión en vivo: se transfiere primero el archivo completo.

Límites: 64 MB por canción, 256 MB temporales en total y dos transferencias simultáneas.
Los audios se borran al terminar, saltar o detener; los archivos sin pedido asociado
caducan a los 30 minutos y se limpian al recibir otra transferencia. No se conservan
tras reiniciar el servidor. Actualizar tanto servidor como ejecutable para este modo.

 La app conserva
la conexión configurada en `%LOCALAPPDATA%/LosPiCompartido/connection.json`, con la
clave cifrada por Windows DPAPI. No lee el token guardado por el reproductor personal.

Los controles locales de audio siguen afectando solo a la PC. Usar los botones
**Siguiente en Discord**, **Pausar en Discord** y **Detener sesión** para el bot.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_shared_server test_shared_desktop
```

Incluyen veinte pedidos concurrentes, reintentos, cambios de sala, avance de la cola,
rechazo de comandos sin clave, cifrado local y aislamiento de las credenciales del bot.
