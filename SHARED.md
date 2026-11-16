# Los π: un bot online, varias PCs

El servidor es el único proceso que usa el token y conecta el bot a Discord.
Las apps de escritorio son controles remotos autenticados mediante una clave de
acceso al servidor. Cerrar una app no detiene la música para los demás.

## Comportamiento compartido

- Cada pedido agrega una canción a una cola común, con el nombre de quien la pidió.
- Los pedidos simultáneos se procesan de uno en uno; no crean conexiones paralelas.
- Un pedido reenviado por una interrupción de red no agrega una canción duplicada.
- La cola avanza automáticamente al terminar una pista.
- Todos ven la canción actual, el estado de pausa y las próximas tres canciones.
- Todos los que tienen la clave pueden pausar, continuar, saltar y detener la sesión.
- Un solo bot reproduce en una sola sala. Mientras esté ocupado, los pedidos de otra
  sala se rechazan. Para cambiar, se detiene la sesión y se elige el nuevo destino.
- El tono de prueba solo se permite cuando no hay una sesión activa.

La cola es temporal: se vacía si el servidor se reinicia o se actualiza. Esta versión
no usa una base de datos para conservar canciones pendientes.

## Servidor online

La imagen Docker instala Python, FFmpeg y las dependencias de voz. No necesita Windows
ni VLC. Se inicia con `python shared_server.py`.

Variables del servidor:

| Variable | Uso |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Token del bot, ingresado como secreto en el proveedor de hosting. |
| `CONTROL_KEY` | Clave aleatoria de al menos 32 caracteres que se comparte con los amigos. |
| `ALLOWED_GUILD_IDS` | Opcional: IDs de servidores autorizados, separados por comas. Vacío permite los servidores a los que pertenece el bot. |
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

El hosting puede tener costo según el plan y el consumo. La reproducción de YouTube
desde un centro de datos puede quedar bloqueada por YouTube aunque funcione desde
una PC doméstica; el tono de prueba permite separar ese problema del envío de voz.

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

Elegir la sala y pulsar **Agregar a Discord** para enviar una canción. La app conserva
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
