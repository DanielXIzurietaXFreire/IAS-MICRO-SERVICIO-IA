# Microservicio de IA — Sistema de Seguridad Inteligente Sisa

Procesa el video de las cámaras IP, detecta amenazas con un modelo YOLOv8 propio,
sube la evidencia a Supabase Storage y crea la notificación en el backend, que la
emite por SignalR al frontend Angular.

    Cámara IP (RTSP) → YOLOv8 → reglas → Supabase Storage → POST /api/notificaciones
                                                          → SignalR → Angular

## Estructura

    microservicio-ia/
      src/
        config.py      configuración desde .env y zonas.json
        camera.py      captura RTSP con reconexión automática
        detector.py    modelo YOLOv8 (carga perezosa)
        rules.py       las cuatro reglas de negocio: es el objetivo específico 2
        storage.py     subida de la imagen a Supabase Storage
        notifier.py    login, cámaras y creación de la notificación en el backend
        metrics.py     registro de latencias en metricas/latencias.csv
        main.py        bucle principal, una hebra por cámara y el supervisor de cámaras
      tests/           99 pruebas con pytest, sin red y sin GPU
      models/          aquí va best.pt cuando termine el entrenamiento
      metricas/        latencias.csv, del que salen las cifras de la tesis
      zonas.json       plantilla pública de zonas y cámaras
      zonas.local.json configuración local de cámaras (no se sube a git)
      .env             claves y URL (nunca se sube a git)

## Puesta en marcha

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    copy .env.example .env        # y se rellenan las claves
    copy zonas.json zonas.local.json  # y se configuran zonas y cámaras

Después se copia el modelo entrenado en `models/best.pt` y se ajusta la configuración
local de cada cámara en `zonas.local.json`. Este archivo puede contener direcciones
RTSP y credenciales, por lo que está excluido de Git.

## Uso

    python -m src.main                  todas las cámaras, sin parar
    python -m src.main --una-vez        un fotograma por cámara y termina
    python -m src.main --camara 3       solo esa cámara
    python -m src.main --sin-envio      detecta y mide, pero no sube ni notifica
    python -m src.main --resumen        resumen de latencias medidas
    python -m src.main --detalle        registro detallado

Se detiene con Ctrl+C: cierra las cámaras y escribe el resumen de latencias.

## Las reglas

| Orden | Regla | Qué hace |
|---|---|---|
| 1 | hay un **arma** | alerta inmediata, **aunque haya un guardia** en la escena |
| 2 | hay un **guardia** y no hay arma | no se alerta: es personal autorizado |
| 3 | hay un **rostro cubierto** | alerta inmediata, a cualquier hora |
| 4 | hay una **persona** en zona restringida **fuera de horario** | alerta |

**El arma va primero** por decisión del autor (29/09/2026). Antes el guardia apagaba
también la alerta del arma, así que un asalto con un guardia en el encuadre no avisaba a
nadie. El guardia sigue apagando el rostro cubierto y la persona en zona restringida.

El horario laboral y las zonas se configuran, no están escritos en el código. Una misma alerta no se repite en la misma cámara hasta
que pasan los segundos de `SEGUNDOS_DE_ESPERA_POR_ALERTA`, para no saturar al operador.

## De dónde salen las cámaras

Primero se piden al backend (`GET /api/camaras`), de modo que la cámara que se registra
en la aplicación entra en el análisis sin tocar nada. Si el backend no responde al
arrancar, se usan las de `zonas.json`.

La lista **no se lee una sola vez**. Cada `SEGUNDOS_ENTRE_REVISIONES_DE_CAMARAS` (60 s por
defecto), el `SupervisorDeCamaras` la vuelve a pedir y relee `zonas.json`:

- abre la hebra de una cámara nueva;
- cierra la de una cámara retirada;
- reinicia la de una cámara cuyo flujo cambió.

No hace falta reiniciar el microservicio. Si en una revisión el backend no responde, se
mantiene lo que está en marcha.

## Autenticación

**Desde el 08/10/2026 el microservicio tiene que autenticarse.** Se quitó el
`[AllowAnonymous]` de `POST /api/notificaciones` y Supabase ya no tiene políticas para el
rol anónimo, porque la de lectura dejaba ver todo el historial de alertas a quien tuviera
la clave anónima. Sin `BACKEND_EMAIL` y `BACKEND_PASSWORD` el sistema no registra nada. Aun así conviene darle su propio usuario en `BACKEND_EMAIL` y
`BACKEND_PASSWORD`: con token, el backend comprueba que la cámara existe y devuelve sus
datos. Si el token caduca, el cliente vuelve a iniciar sesión solo.

El backend devuelve el token en el campo **`token`** (no `accessToken`).

## Pruebas

    .venv\Scripts\python -m pytest
    .venv\Scripts\python -m pytest --cov=src --cov-report=term-missing

99 pruebas. No necesitan cámara, ni modelo, ni red: todo se sustituye
por dobles de prueba. Cubren las cuatro reglas (incluidos los bordes del horario y del
polígono de la zona), el cliente del backend con su reintento por token caducado, la
subida a Storage, el registro de latencias y el flujo completo de una alerta.

## Medición de latencias

Cada alerta deja una fila en `metricas/latencias.csv` con los milisegundos de inferencia,
de subida de la imagen y de creación de la notificación. `--resumen` da el mínimo, la
mediana, la media y el máximo. Son las cifras de latencia del objetivo específico 3 de la
tesis: medidas, no estimadas.
