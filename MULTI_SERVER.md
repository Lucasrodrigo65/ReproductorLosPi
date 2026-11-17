# Servidores independientes con el mismo bot

Un solo proceso mantiene la conexión del bot a Discord. Cada servidor autorizado
tiene su propia reproducción, cola, pausa, archivos temporales y temporizador.
Puede haber una conexión de voz por servidor, simultáneamente.

Cada clave de acceso identifica exactamente un servidor. No hay una clave global
de administrador en la API: una clave no permite listar salas, leer estados,
reutilizar audios ni controlar la reproducción de otro servidor.

## Conservar un servidor existente

Si Railway ya tiene CONTROL_KEY y ALLOWED_GUILD_IDS, se conserva la misma clave
siempre que ALLOWED_GUILD_IDS contenga exactamente un ID de servidor.
Una lista con varios IDs o una clave sin servidor asociado impide iniciar el
servicio: no se convierte en acceso global accidentalmente.

## Agregar otro servidor

1. Invitar al bot y darle Ver canal, Conectar y Hablar en las salas deseadas.
2. Generar una clave aleatoria distinta para ese grupo (mínimo 32 caracteres).
3. En las variables privadas del servicio de música en Railway, definir
   GUILD_ACCESS_KEYS como un objeto JSON con IDs y claves. Ejemplo de formato:

   {"111111111111111111":"CLAVE_NUEVA_DE_AL_MENOS_32_CARACTERES"}

4. Conservar CONTROL_KEY y su único ALLOWED_GUILD_IDS para el grupo original.
   GUILD_ACCESS_KEYS puede contener los demás grupos. Si incluye el servidor
   original, su clave explícita reemplaza a CONTROL_KEY para ese servidor.
5. Desplegar el servicio. El reinicio vacía las colas temporales.
6. Compartir por privado con cada grupo solo su clave y la URL del servicio.
   En cada reproductor, configurar Servidor compartido, clave y apodo; actualizar
   las salas. El widget usa la configuración cifrada de ese mismo usuario.

No repetir claves entre servidores. No publicar GUILD_ACCESS_KEYS ni el token
del bot en GitHub, en el ZIP o dentro del ejecutable. El token del bot solo se
guarda en Railway. Para retirar acceso, quitar/rotar la clave correspondiente
y volver a desplegar el servicio.

## Desconexión por inactividad

Cada servidor sale de su propio canal de voz tras cinco minutos sin canción
actual y con la cola vacía. Las pistas pausadas mantienen la sesión. Al agregar
una nueva canción, el bot vuelve a conectarse. Detener, saltar o cambiar de sala
afecta únicamente al servidor asociado a la clave.

Usar una sola réplica del servicio; no iniciar otro proceso con el mismo token.
La carga de CPU, memoria y transferencia aumenta con las reproducciones
simultáneas. Esta separación no amplía los créditos ni recursos del hosting.
No requiere cambios en discord-tracker ni actualizar el reproductor o widget.

## Verificación

python -m unittest test_multi_guild test_shared_server test_discord_player

Las pruebas simulan dos servidores concurrentes y comprueban el aislamiento de
claves, salas, colas, audios, pausas, fin de pista y desconexión por inactividad.
