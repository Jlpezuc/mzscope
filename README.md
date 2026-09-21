# MicroZohm Scope

Osciloscopio y registrador para el MicroZohm. Sustituye al JavaScope: se conecta al mismo
servidor TCP del A53 (puerto 1000) y habla el mismo protocolo, así que **no requiere ningún
cambio en el software de la placa**.

El software de la placa (PL, R5, A53) está en el repositorio
[mz-sw](https://github.com/Jlpezuc/mz-sw). El scope lee los nombres de las señales y los
botones de su `vitis/software/Baremetal/src/app/javascope.h`: si `mzscope` y `mz-sw` están
en la misma carpeta lo encuentra solo; si no, se elige el fichero en la pestaña *Conexión*
(también vale el `javascope.h` original de `mz-sw (old)`, o `headers/javascope_original.h`).

* Python 3.10+ · PySide6 (Qt) · pyqtgraph · numpy · scipy. Multiplataforma (probado en Windows 11).
* Eje de tiempo correcto: la base de tiempo es el periodo real del ISR que mide la placa
  (`ISR_Period_us`, dato lento), y las muestras perdidas en la cola del A53 se detectan por
  el contador `slowDataID` (avanza 1 por ISR) y se saltan en el eje de tiempo.
* Varios gráficos apilados con eje X común; cada uno de los 20 canales se asigna al gráfico
  que se quiera, con color, escala y offset propios, y un check *Ver* para ocultarlo sin perder
  la asignación.
* **Acoplamiento y filtro por gráfico** (botón *Ajustes* del canal o *Filtro y orden de las trazas…*
  en Gráficos). El acoplamiento DC/AC se aplica a todas las trazas del gráfico y al nivel de disparo.
  Un filtro por gráfico, aplicado a todas sus señales: media móvil, RMS móvil, armónicos (de DC al
  100.º, con un slider de dos tiradores), pasa bajos / pasa altos / pasa banda / rechaza banda
  (Butterworth) y notch; se ve la señal original, el filtro o ambas. Los filtros arrancan fuera de
  la pantalla (se piden muestras anteriores a la ventana), así que no hay transitorio al principio.
  En la misma ventana se ordenan las trazas (señales y filtros) de atrás hacia delante.
* Dos modos de visualización: **Continuo** (registrador, la señal avanza) y **Disparo**
  (osciloscopio: fuente, flanco, nivel arrastrable, histéresis, pre-trigger, barrido
  auto/normal/único).
* FFT de cualquier canal (ventana Hann, amplitud de pico, marcador de pico, THD).
* Exportación: CSV del buffer o de la ventana visible, imagen PNG, y **grabación** (botón
  ● Grabar / tecla R) a `outputs/mzscope_<fecha>.csv` de los canales marcados en *Ver* con su
  escala y offset, escrita por un hilo aparte.
* Panel de control: Enable System / Enable Control / STOP / Error Reset, LEDs, botones de usuario,
  consignas, lecturas y tabla de datos lentos. Todo sale de
  `vitis/software/Baremetal/src/app/javascope.h`: un boton por cada `My_Button_n, /* texto */` y una
  consigna por cada `Set_Send_Field_n, /* variable [unidad] */` del `enum gui_button_mapping`
  (el indice del enum es el id que se envia); los datos lentos con comentario `/* etiqueta [unidad] */`
  se muestran como lecturas. Las senales de la aplicacion se definen con una
  linea `X(nombre)` en la lista `MZ_SCOPE_SIGNALS` de `globalData.h` (crea `Global_Data.sv.nombre` y la
  entrada del scope a la vez); mzscope expande esas listas igual que el preprocesador de C.
  También lee el `javascope.h` **original** (`mz-sw (old)`, JavaScope): detecta el formato solo
  (`Error_Reset` al final del enum en vez de en la posición 4) y saca las etiquetas del bloque
  comentado `Visualization Config for GUI` (`SND_FLD_*`, `SND_LABELS_*`, `MYBUTTONS_LABELS_*`,
  `SLOWDAT_DISPLAY_*`, `RCV_FLD_*`, `RCV_LABELS_*`).
* Tema claro/oscuro; configuración persistente en `mzscope_settings.json`.

## Instalación

```
cd mz-sw/mzscope
python -m pip install -r requirements.txt
```

## Uso

```
run.bat            (o  python -m mzscope)      con consola: se ven los mensajes y errores
run.vbs            (o  doble clic en mzscope.pyw)   SIN ventana de consola (pythonw.exe)
```

Sin consola, los errores van a `outputs/mzscope.log`. Para un acceso directo en el escritorio con
el icono del scope: destino `pythonw.exe -m mzscope`, "iniciar en" la carpeta `mzscope`, e icono
`mzscope/assets/mzscope.ico` (o crea el acceso directo sobre `run.vbs` y cámbiale el icono).

1. Pestaña **Conexión**: IP de la placa (`192.168.1.233`, ver la configuración de red en la
   documentación). Con *Conectar automáticamente* (por defecto) el scope se queda esperando a la
   placa y se conecta solo cuando responde, también tras perder la conexión; si no, **Conectar**.
2. Pestaña **Canales**: elige la señal de la placa de cada canal (lista de `javascope.h`), el
   gráfico en el que se dibuja (`—` = oculto) y el color. El botón **Ajustes** (muestra
   `×escala +offset`) abre la ventana del canal: escala y offset (`y = escala · x + offset`;
   admiten `,` o `.` como decimal, hasta 12 decimales y expresiones como `1/65536`), el
   **filtro del gráfico** al que pertenece y el **orden de dibujo** de sus trazas.
   **Enviar selección a la placa** manda los comandos 201..220 (por defecto se envían al conectar).
3. Pestaña **Gráficos**: añadir/quitar gráficos, título, etiqueta Y, escala Y automática o fija,
   y **Filtro y orden de las trazas…** (la misma ventana, sin la parte del canal). La casilla de
   cada gráfico lo oculta sin quitarlo (conserva sus canales y ajustes, y no se calcula mientras
   está oculto); ▲/▼ (o Ctrl+↑/↓) cambian el orden de arriba a abajo, y los canales siguen a su gráfico.
4. Pestaña **Control**: máquina de estados, botones y consignas.
5. Barra superior: **Pausar** (espacio) muestra todo el buffer y permite zoom/pan con el ratón
   (rueda = zoom, arrastrar = mover, clic derecho = menú de pyqtgraph); **Seguir** vuelve a
   desplazar la ventana con los datos nuevos; **Captura única** limpia y captura una ventana;
   **FFT** abre el panel de espectro; **Guardar CSV / Guardar imagen**; **● Grabar** graba
   desde que se pulsa hasta que se vuelve a pulsar en `outputs/` (canales con *Ver* marcado,
   `time_s` desde el inicio de la grabación).

### Modo disparo

Con **Modo: Disparo** (barra superior o pestaña **Disparo**) la pantalla no avanza: se captura
una ventana alrededor del instante en que la señal fuente cruza el nivel con el flanco elegido,
con `t = 0` en el disparo y el porcentaje de pre-trigger configurable. El nivel se puede
arrastrar en el gráfico; *Nivel al 50 %* lo coloca en el punto medio de la señal. La
histéresis evita disparos múltiples con ruido. Barrido *Auto* (muestra la señal libre si no hay
disparo), *Normal* (solo actualiza con disparo) o *Único* (una captura y *Armar*). El disparo
se evalúa en el PC sobre las muestras recibidas: no cambia nada en la placa.

### Rendimiento

La placa espera la respuesta de cada paquete antes de enviar el siguiente y, si el PC tarda,
no pierde muestras: las acumula en la cola del A53 (hasta 10^6) y lo que se ve es cada vez
más antiguo. Por eso la red va en un proceso aparte que contesta inmediatamente, y el dibujo
se limita al rango visible con decimación mín/máx (≤ 4000 puntos por curva). Si aun así
llegan menos muestras de las que genera el ISR, la barra de estado lo avisa en rojo
("se acumula retraso").

### Base de tiempo

`t = índice_ISR × Ts`. `Ts` es `ISR_Period_us` medido por el R5 (mostrado en *Conexión*); si la
placa no envía ese dato lento se puede fijar manualmente. La barra de estado muestra el número de
muestras perdidas (cola del A53 llena) y repetidas (R5 parado, la placa reenvía la última).

## Probar sin placa

```
sim.bat --drop 0.001        (o  python -m mzscope.sim)
```

arranca una placa simulada en `127.0.0.1:1000` con señales sintéticas (tensiones de 50 Hz con
armónicos, corrientes, `lifecheck`, ...), datos lentos, máquina de estados y respuesta a los
botones. Conectar el scope a `127.0.0.1`.

```
sim_old.bat                 (o  python -m mzscope.sim --old)
```

simula una placa con el **software original** (`mz-sw (old)` / JavaScope), usando la copia
`headers/javascope_original.h`; en el scope hay que cargar ese mismo fichero en *Conexión*.
`--header <ruta>` usa cualquier otro `javascope.h`.

La placa (real o simulada) y el scope tienen que usar el mismo `javascope.h`: el número de datos
lentos fija el contador de secuencia `slowDataID`, y si no coincide casi todas las muestras se dan
por perdidas. El scope lo detecta (los ids recibidos no encajan) y lo avisa en la pestaña
*Conexión* y en la barra de estado.

## Hilos: la recepción nunca se bloquea

| Dónde | Qué hace | Coste típico |
|---|---|---|
| **Proceso de red** (`client.py`, proceso hijo) | recibe los paquetes, **contesta a la placa antes de tocar nada** (la placa espera la respuesta para enviar el siguiente) y los pasa a la GUI por una cola sin límite | — |
| **Hilo de la GUI** | vacía la cola (`client.poll()` cada 33 ms), mete las muestras en el buffer, graba el CSV, busca el disparo, y **solo pinta arrays ya calculados** (`setData`) | 1–5 ms por ciclo |
| **Hilo de render** (`render.py`) | escala, acoplamiento, filtros, recorte y diezmado (`PlotStack.compute`) | lo que cueste el filtro |

El hilo de render trabaja con la política *el último gana*: si un cálculo tarda más que un ciclo,
el siguiente frame usa los datos más recientes y los intermedios se saltan (`render.dropped`),
nunca se encolan frames viejos ni se pierden muestras. Con un filtro artificialmente lento
(600 ms/frame) la recepción sigue a 667 paquetes/s, 0 muestras perdidas y la GUI responde.
Numpy/scipy sueltan el GIL en las operaciones pesadas, así que el render no frena la ingestión;
y aunque lo hiciera, la recepción es otro proceso y no se entera.

## Acoplamiento y filtro del gráfico

Cada gráfico tiene un acoplamiento y un filtro (`PlotConfig.coupling`, `PlotConfig.filter`), que
se aplican en la GUI sobre el tramo de muestras que se dibuja (no cambian el buffer ni lo que se
graba en CSV) a **todas** sus señales ya escaladas:

1. **Acoplamiento**: DC deja la señal tal cual; AC le resta la media del tramo visible (la centra
   en 0). Afecta a la traza original, a la filtrada y al **disparo**: el nivel se compara con la
   señal acoplada, así que con AC el nivel 0 es el cruce por la media.
2. **Filtro** (uno solo; en *Mostrar* se elige si se dibuja la señal, el filtro o ambas):

| Filtro | Parámetros | Qué hace |
|---|---|---|
| Media móvil / RMS móvil | ancho [muestras], centrada | ventana de N muestras (ISR); causal como un filtro real o centrada sin retardo (el final, que aún no tiene muestras "futuras", se deja vacío) |
| Armónicos | fundamental (0 = automática por FFT), rango k_min..k_max | suma de los armónicos `k_min..k_max` de la fundamental (0 = DC, 1 = fundamental, hasta 100); los coeficientes se calculan por proyección sobre un número entero de periodos y la serie se interpola, así que el coste no crece con el número de armónicos |
| Pasa bajos / pasa altos | f_c, orden | Butterworth de orden 1..12 (6 dB/octava por orden) |
| Pasa banda / rechaza banda | f_inf, f_sup, orden | Butterworth |
| Notch | f_0, Q | rechazo estrecho (ancho = f_0 / Q) |

Los IIR pueden ser causales (`sosfilt`, arrancando en su estado estacionario) o de *fase cero*
(`sosfiltfilt`, sin retardo). Extras comunes: desplazar N muestras (+ = retrasar) e invertir el signo.

Para que un filtro causal no arranque "en frío" al principio de la pantalla, los gráficos piden
al buffer `warmup_samples()` muestras anteriores a la ventana visible (ancho de la media, unos
`4·orden·fs/f_c` para los IIR, `4·Q·fs/f_0` para el notch), y posteriores si el filtro es centrado /
de fase cero; en modo disparo la captura incluye esas muestras. El transitorio queda fuera de la
pantalla, como si el filtro llevase corriendo desde antes.

La lista *Orden de dibujo* enumera las trazas del gráfico (señales y filtros) de atrás hacia
delante: la última queda por encima. Se mueven con ▲/▼ o Ctrl+flechas y el orden se recuerda
aunque se desactive el filtro.

## Estructura

```
mzscope/
├── mzscope/
│   ├── protocol.py      formato de los paquetes (NetworkSendStruct) y comandos (APU_to_RPU_t)
│   ├── header.py        lectura de javascope.h (enums y etiquetas de la GUI)
│   ├── client.py        proceso TCP: recibe paquetes, contesta con los comandos pendientes
│   ├── buffer.py        buffer circular con base de tiempo y detección de huecos
│   ├── logger.py        CSV (exportación y grabación en hilo aparte -> outputs/)
│   ├── settings.py      configuración persistente (JSON) y modelo canales/gráficos/filtro
│   ├── render.py        hilo de render (calculo de las trazas fuera de la GUI, el ultimo gana)
│   ├── filters.py       acoplamiento y filtros de las trazas (media/RMS, armónicos, IIR con scipy)
│   ├── theme.py         temas claro/oscuro
│   ├── sim.py           placa simulada
│   ├── icon.py          icono de la aplicacion (assets/mzscope.svg -> ventanas, barra de tareas, .png/.ico)
│   ├── assets/          mzscope.svg / .png / .ico
│   └── ui/              main_window, plots, fft, channels, channel_dialog, range_slider, trigger_panel, control
├── requirements.txt
├── headers/         javascope_original.h (copia del software original, para sim --old)
├── run.bat / run.vbs / mzscope.pyw / sim.bat / sim_old.bat
└── README.md
```

## Protocolo (resumen)

La placa envía paquetes de 1324 bytes: `uint32 status` + `float slowDataContent[15]` +
`float val_01..20[15]` + `float slowDataID[15]` (15 muestras por paquete, una por ISR).
Tras cada paquete espera 8 bytes de respuesta (`uint32 id`, `float value`): `id = 0` sin efecto,
`1..18` botones/consignas (`gui_button_mapping`), `201..220` selección de señal de cada canal.
El `status` lleva los LEDs (bits 0..3) y los indicadores de los botones de usuario (bits 4..11).
