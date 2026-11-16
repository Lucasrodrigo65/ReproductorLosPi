# Música en un canal de voz de Discord

## Usar un bot propio

1. Abrí el [portal de desarrolladores de Discord](https://discord.com/developers/applications), creá una aplicación y entrá en **Bot** para obtener su token. El token se pega únicamente en **Configurar bot** dentro del reproductor y se guarda cifrado con Windows para tu usuario.
2. En **OAuth2 → URL Generator**, elegí el scope **bot** y los permisos **View Channels**, **Connect** y **Speak**. Abrí el enlace generado para invitar el bot a tu servidor. Necesitás permiso para administrar el servidor. No hacen falta intents privilegiados ni permiso de administrador.
3. En el reproductor, abrí **Configurar bot**, ingresá el token y pulsá **Guardar**. Se consultarán los canales de voz disponibles para el bot.
4. Elegí el servidor y la sala en **Sala de voz**. La selección se guarda automáticamente; usá **Actualizar salas** para consultar los permisos y canales de nuevo.
5. Seleccioná una canción en los resultados y pulsá **Reproducir en Discord**. También podés pegar una URL de YouTube y pulsar ese botón si no hay un resultado seleccionado. La selección de la lista tiene prioridad sobre la URL pegada.
6. El bot entra al canal y reproduce la canción para todos. Otra canción enviada reemplaza la anterior. **Desconectar bot** detiene la música y retira el bot del canal. Al cerrar el programa se desconecta también.

El programa debe permanecer abierto. Los controles locales de pausa, volumen, avance y siguiente siguen controlando el audio de tu PC; la reproducción en Discord se maneja con sus propios botones. Esta versión envía una canción por vez, sin cola ni avance automático en Discord. FFmpeg está incluido en el ejecutable; el audio de Discord funciona sin depender de VLC.

Si no conecta, verificá el token, que el bot esté invitado al servidor y los permisos específicos del canal. Los canales de escenario no están soportados. YouTube puede rechazar videos privados, restringidos o solicitudes de extracción; el programa muestra el error recibido.

Si el bot entra pero no escuchás música, pulsá **Probar sonido**: reemplaza la canción
por un tono de tres segundos, sin acceder a YouTube. Si escuchás el tono pero no la
canción, el problema está en la fuente o su decodificación. Si tampoco escuchás el tono,
hay que revisar el envío de voz, el volumen del bot y la salida de audio de Discord.
El bot entra autoensordecido (`self_deaf=True`); esto evita que reciba audio de otros
usuarios y no impide que transmita. El estado de ensordecimiento del servidor es distinto.
La app ahora informa cuando termina de enviar paquetes y cuando FFmpeg no produce audio;
un envío de paquetes no confirma por sí solo que los oyentes puedan escucharlos.

### Compatibilidad de cifrado de voz

Esta versión fija `discord.py==2.7.1` junto con `davey==0.1.7`. No actualices
`davey` a 0.2 por separado: cambió el manejo de transiciones y claves, y requiere
cambios en la integración de Discord. Una combinación incompatible puede conectarse
y mostrar actividad sin producir audio audible para los demás. La compilación verifica
ambas versiones. Después de actualizar, desconectá el bot y reiniciá la app para cargar
la biblioteca nativa corregida.

Referencias técnicas: [migración de davey 0.2](https://github.com/Snazzah/davey/wiki/Migrating-to-0.2.0),
[adaptación de discord.py](https://github.com/Rapptz/discord.py/pull/10548).

## Compilar

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm overlay.spec
```

El resultado es `dist/ReproductorLosPi.exe`. No se incluye ningún token dentro del ejecutable.

Referencias: [crear e invitar un bot](https://discordpy.readthedocs.io/en/stable/discord.html), [API de voz](https://discordpy.readthedocs.io/en/stable/api.html#voice-related).


## Recordar el token y el canal

En **Configurar bot**, ingresá el token y pulsá **Guardar** una vez. Elegí el canal en **Sala de voz**.
Se recuperan automáticamente al abrir el reproductor. La edición pública no tiene un canal
predeterminado. La app no entra a la sala ni inicia audio hasta pulsar
**Reproducir en Discord** o **Probar sonido**.

La configuración se guarda en `%LOCALAPPDATA%/ReproductorLosPiPublic/discord.json`,
separada del ejecutable, por lo que se conserva al actualizar o mover el `.exe`.
El token se cifra con DPAPI para el usuario de Windows actual; no se incluye en el
`.exe` ni se escribe como texto visible en el archivo. Para cambiar el bot o el
canal, editá los datos y pulsá **Guardar** otra vez. Si el archivo está dañado o
Windows no puede descifrarlo, la app pide configurar nuevamente.


## Selector de salas

Al abrir la app con un token guardado se consulta la lista de salas. La consulta conecta
el bot a Discord, pero no lo hace entrar a un canal de voz ni inicia audio.
El selector muestra `Servidor / Categoría / Sala` y solo incluye canales normales con
permisos Ver canal, Conectar y Hablar. Si no aparece una sala, revisá los permisos del bot
y pulsá **Actualizar salas**.

Elegir una sala guarda el destino. Al pulsar **Reproducir en Discord** o **Probar sonido**,
el bot se conecta o se mueve a ese canal. La música actual no se mueve solo por cambiar
la selección. Si la sala guardada dejó de estar disponible, se pide elegir otra.
