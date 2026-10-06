# 🎧 TuboPlay

Reproductor de música de **YouTube** (el YouTube normal, no YouTube Music) para
el escritorio: **Windows, macOS y Linux**.

Lo especial de TuboPlay es que **lee las pestañas que ya tienes abiertas en tu
navegador** y te deja reproducir directamente las que tienen música, sin tener
que copiar ni pegar nada.

---

## Qué hace

| | |
|---|---|
| 📑 **Lee tus pestañas abiertas** | Firefox (en vivo), Chrome / Edge / Brave / Vivaldi / Opera (en vivo con el puerto de depuración, o desde la última sesión guardada). |
| 🎛️ **Pestaña «Mix»** | Monta mezclas con lo que ya tienes abierto y con las que propone YouTube; una sola de ellas se añade a la cola entera. |
| 🎤 **Control por voz** | Pulsa el micrófono y di «**pon play**», «pausa», «siguiente», «sube el volumen»…  Funciona **sin internet** (el reconocimiento es local). |
| 📱 **Control desde el móvil** | El PC sirve una página en tu red local: abres una URL en el navegador del teléfono y tienes pestañas, mezclas y cola.  La música suena en el PC; el móvil es el mando. |
| ▶️ **Reproduce solo el audio** | Extrae la pista de audio con `yt-dlp` y la suena con **VLC**. Sin video, sin ventana de navegador, sin anuncios de la interfaz de YouTube. |
| 🔎 **Busca** | Escribe cualquier texto en la caja superior y se busca en YouTube. |
| 📋 **Cola de reproducción** | Añade vídeos sueltos o playlists completas, ordena, quita, salta. |
| 🔀🔁 **Aleatorio y repetición** | Barajar la cola, repetir todo o repetir la pista actual. |
| ⌨️ **Atajos** | `Espacio` reproducir/pausa · `←/→` ±5 s · `↑/↓` volumen. |

---

## Instalación

### En Windows, sin Python (doble clic)

En `dist/` hay dos archivos listos para usar en cualquier PC Windows:

| Archivo | Qué es |
|---|---|
| `TuboPlay-Instalador.exe` (73 MB) | **Un único .exe** con todo dentro.  Doble clic y a instalar: no pide administrador, se va a `%LOCALAPPDATA%\TuboPlay\app` y crea los accesos directos del escritorio y del Menú Inicio.  Si falta VLC, te avisa. |
| `TuboPlay-portable.zip` (56 MB) | La carpeta de la aplicación: se descomprime y se ejecuta `TuboPlay\TuboPlay.exe`.  Para llevarlo en un USB o una carpeta de red. |

Para desinstalar, ejecuta `%LOCALAPPDATA%\TuboPlay\app\Desinstalar.bat`
(borra el programa y los accesos directos, y **conserva** el modelo de voz).

También puedes instalarlo sin ventana (para un despliegue o una prueba):

```bat
TuboPlay-Instalador.exe --auto
```

El código de salida es `0` si fue bien y el detalle queda en
`%TEMP%\tuboplay-instalador.log`.

### Desde el código fuente

Necesitas **Python 3.10+** y **VLC** (VLC 3.x, que probablemente ya tengas).

```bash
cd tuboplay
pip install -r requirements.txt
```

### Arrancar

```bash
python main.py
```

O simplemente doble clic en `start.bat` (Windows) / `start.sh` (macOS, Linux).

### Otras opciones

```bash
python main.py --selftest          # comprueba dependencias y pestañas
python main.py --headless          # comprueba que VLC reproduce un stream real
python main.py --play "URL"        # arranca ya reproduciendo algo
python main.py --install-voice     # instala el reconocimiento de voz
python main.py --version
```

### Control por voz (opcional)

El reconocimiento de voz se instala **una sola vez** y luego funciona sin
internet:

```bash
python main.py --install-voice
```

o, más fácil, pulsa el **micrófono** 🎤 de la ventana y acepta cuando el
programa te lo pida.  Descarga `vosk`, `sounddevice` y el modelo español
(`vosk-model-small-es-0.42`, ~40 MB) en tu carpeta de datos
(`%LOCALAPPDATA%\TuboPlay` en Windows).

Una vez activo, pulsa el micrófono y di en voz alta:

| Dices | Hace |
|---|---|
| «**pon play**» / «reproduce» / «mezcla» | Si la cola está vacía, arranca la primera mezcla de la pestaña **Mix**; si no, continúa. |
| «pausa» / «para» / «detente» | Pausa o reanuda. |
| «siguiente» / «anterior» | Salta de pista. |
| «sube el volumen» / «baja el volumen» | ±10 %. |

---

## Cómo detecta las pestañas abiertas

TuboPlay usa tres mecanismos, del más fiable al más simple:

1. **Puerto de depuración de Chromium** (`--remote-debugging-port=9222`).
   Devuelve las pestañas **en vivo**, con título y URL.  Como ningún navegador
   lo abre así por defecto, la app tiene el botón
   **«Conectar navegador (pestañas en vivo)»**: eliges el navegador y, si está
   abierto, TuboPlay **te lo pregunta antes** de cerrarlo (se restaura la sesión
   al volver a abrirlo con esa opción).  Tus pestañas actuales aparecen al
   instante.  Si cancelas, no se toca nada y sigues viendo las pestañas ya
   detectadas.

2. **`sessionstore.jsonlz4` de Firefox**.  Firefox lo reescribe cada ~15
   segundos mientras está abierto, así que lo que sale está prácticamente al
   día.  Requiere el paquete `lz4` (en `requirements.txt`).

3. **Archivos de sesión de Chromium** (`Sessions/Session_*`).  Es el respaldo:
   Chromium bloquea el fichero en vivo, así que se lee el último guardado.  Las
   pestañas se marcan como *«sesión guardada»* y los títulos se completan con
   la API **oEmbed** de YouTube (una petición HTTP, muy rápida).

Los filtros son estrictos: solo se muestran URLs de YouTube como reproducibles,
y cada URL aparece una sola vez aunque la tengan varios navegadores.

---

## La pestaña «Mix»

YouTube no deja leer su portada sin iniciar sesión (la home devuelve una
pantalla vacía «Busca algo para comenzar»), así que TuboPlay monta las mezclas
por otras dos vías, y lo dice en la propia lista:

1. **Tus mezclas (sacadas de tus pestañas)**: de cada pestaña de YouTube que
   tengas abierta se deriva su mezcla personal (`watch?v=…&list=RD…`), que es
   la misma que YouTube genera abajo de un vídeo.  Si la pestaña ya es de una
   mezcla, se reutiliza tal cual.
2. **Mezclas que te propone YouTube**: se leen las que aparecen en una búsqueda
   de «mix» en YouTube (con idioma y país de España) y se filtran las que
   empiezan por `RD`.

Al pulsar una mezcla (o al decir «**pon play**») se expande a cola:

* la **primera tanda** son 60 canciones y tarda ~5 s: es lo que hace que la
  música arranque rápido;
* el resto (hasta 200) se pide **en segundo plano** y se añade al final, sin
  interrumpir lo que ya suena.

---

## Control desde el móvil

TuboPlay abre un **servidor web local** para que el teléfono sea el mando a
distancia.  El ordenador sigue siendo quien reproduce.

1. Pulsa el botón 📱 de la barra superior.
2. Copia la dirección que aparece (algo como
   `http://192.168.1.42:8765/t/…`).
3. En el teléfono, con ambos en la **misma red Wi-Fi**, ábrela en el
   navegador.  Opción → *«Añadir a pantalla de inicio»* y queda como una app.

La página muestra la barra de reproducción (con progreso y volumen), y las
mismas tres vistas que el programa: **Pestañas**, **Mix** y **Cola**, además
de una caja para pegar una URL o buscar.  Todo lo que pulses allí actúa sobre
el PC.

Detalles:

* **Seguridad**: la dirección lleva una clave aleatoria.  Sin esa clave el
  servidor responde `404`, como si no existiera: quien entre en tu red no ve
  nada ni manda órdenes.
* **Sin coste**: el servidor solo calcula el estado cuando alguien está
  mirando la página (y como mucho cada 0,4 s); si no hay visitas, no consume
  nada.  Se apaga al cerrar TuboPlay.
* **Sin hilos peligrosos**: el servidor nunca toca la interfaz; deja las
  órdenes en una cola que el bucle de la aplicación vacía, y lee una
  instantánea del estado que la aplicación sustituye entera.

---

## Cómo reproduce el audio

```
URL de la pestaña ──▶ yt-dlp ──▶ URL directa del stream de audio
                                      │
                                      ▼
                                VLC (libvlc) ──▶ altavoces
```

* `yt-dlp` elige `bestaudio[ext=m4a]/bestaudio/best` (m4a si existe, que es lo
  que mejor lleva VLC) y devuelve los encabezados HTTP que YouTube exige.
* Se **precarga** la siguiente pista de la cola mientras suena la actual, para
  que no haya esperas al saltar.
* Las URLs de stream caducan a las pocas horas, por eso se vuelven a pedir en
  el momento de reproducir y no se guardan.
* Las miniaturas se descargan una vez y se guardan en la caché temporal del
  sistema (`%TEMP%\tuboplay_thumbs`).

---

## Estructura

```
tuboplay/
├── main.py               # lanzador
├── requirements.txt
├── requirements-voice.txt  # opcional: control por voz
├── start.bat / start.sh
├── packaging/
│   ├── build.py          # genera el instalador y el .zip de distribución
│   ├── instalador.py     # interfaz de instalación para Windows (+ --auto)
│   └── make_icon.py      # genera tuboplay.ico a partir del icono dibujado
└── tuboplay/
    ├── __main__.py       # CLI (--selftest, --headless, --play, --install-voice)
    ├── app.py            # interfaz (CustomTkinter)
    ├── models.py         # Track, Tab, utilidades de URL de YouTube
    ├── tabs.py           # detección de pestañas (Firefox/Chromium/DevTools)
    ├── mixes.py          # mezclas: de tus pestañas + las que propone YouTube
    ├── voice.py          # reconocimiento de voz local (vosk)
    ├── web.py            # servidor local: control desde el móvil
    ├── extractor.py      # yt-dlp: metadatos, audio, búsquedas, oEmbed
    ├── player.py         # reproductor (libvlc)
    └── util.py           # hilos, caché de miniaturas e iconos propios
```

### Generar los instaladores

En Windows, con `pip install pyinstaller`:

```bash
python packaging/build.py             # icono + app + instalador + .zip
python packaging/build.py --zip-only  # sólo reconstruye el .zip
```

Deja en `dist/` los dos archivos descritos arriba.  El proceso ya está
probado de punta a punta: el `.exe` generado pasa `--selftest`, abre el
servidor del móvil y el instalador copia 2132 archivos en ~13 s.

---

## Preguntas frecuentes

**¿Por qué no suena nada?**
Ejecuta `python main.py --selftest` y `python main.py --headless`.  Si el
segundo falla, VLC no está instalado o YouTube ha cambiado algo: actualiza
`yt-dlp` con `pip install -U yt-dlp`.

**¿Detecta pestañas de Chrome sin tocar nada?**
Sí, pero como *sesión guardada* (Chromium bloquea el fichero en vivo).  Para
verlas en vivo usa el botón «Conectar navegador».

**¿Y YouTube Music?**
Las URLs de `music.youtube.com` también funcionan: se convierten a la URL
equivalente de `youtube.com` y se reproducen igual.

**¿Funciona con playlists?**
Sí: pega la URL de la playlist y se añaden todos los vídeos a la cola (hasta
200).

**¿Por qué la pestaña Mix no me muestra la portada de YouTube?**
Porque YouTube no enseña su portada a quien no ha iniciado sesión, y leerla
por fuera devuelve una página vacía.  Por eso TuboPlay arma las mezclas con lo
que sí puede ver: tus pestañas abiertas y las mezclas que YouTube propone en
una búsqueda de «mix».

**¿Me tiene que oír el micro siempre?**
No.  El micro solo se enciende cuando pulsas el botón 🎤, y todo el
reconocimiento se hace **en tu equipo** con Vosk: ni audio ni texto salen del
ordenador.  Vuelve a pulsar el botón para apagarlo.

**¿No me entiende «pon play»?**
El modelo español pronuncia «play» raro.  TuboPlay acepta además
«plei», «reproduce», «pon la música», «mezcla», «pón play» y variantes
parecidas.  Habla despacio y a menos de un metro del micro.

**¿Se puede instalar TuboPlay en el teléfono?**
No: es un programa de escritorio y necesita VLC.  Lo que sí puedes es
**controlarlo desde el móvil** con la página web local (botón 📱): el
teléfono manda las órdenes y la música suena en el PC.

**¿El móvil no conecta con la dirección?**
Casi siempre es que no están en la misma red (por ejemplo, el PC con cable
y el móvil con los datos móviles) o que hay un invitado/apaislamiento Wi-Fi.
Comprueba que ambos tengan la misma red y vuelve a abrir la ventana 📱 para
que te dé la dirección nueva; si cambias de red, cambia la IP.

**¿Y en macOS y Linux?**
La aplicación funciona (Python + VLC), pero el instalador de `dist/` es
solo para Windows.  En macOS y Linux se instala desde el código fuente con
`pip install -r requirements.txt` y `python main.py`.  El control móvil
funciona igual en todos.

---

## Aviso

TuboPlay reproduce el audio de los vídeos que tú mismo tienes abiertos o que tú
mismo pegas; no descarga, no distribuye y no guarda contenido.  Úsalo
respetando los términos de servicio de YouTube y los derechos de autor de cada
obra.
