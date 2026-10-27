# -*- coding: utf-8 -*-
"""
Microservicio de IA — bucle principal.

    Camara IP (RTSP) -> YOLOv8 -> reglas -> Supabase Storage -> backend -> SignalR

Una hebra por camara. Cada alerta deja su medicion de latencia en
metricas/latencias.csv.

La lista de camaras no se lee una sola vez: un supervisor la vuelve a pedir al
backend cada SEGUNDOS_ENTRE_REVISIONES_DE_CAMARAS y abre, cierra o reinicia las
hebras que hagan falta. Asi, una camara registrada, retirada o con otro flujo en la
aplicacion web entra en el analisis sin reiniciar el microservicio. En la misma
revision se relee la configuracion local de zonas.

Uso:
    python -m src.main                     procesa todas las camaras, sin parar
    python -m src.main --una-vez           un fotograma por camara y termina
    python -m src.main --camara 3          solo esa camara
    python -m src.main --sin-envio         detecta y mide, pero no sube ni notifica
    python -m src.main --resumen           imprime el resumen de latencias y termina
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from datetime import datetime, timezone

from . import config as configuracion
from .camera import CameraCapture
from .detector import Detector
from .metrics import Medicion, RegistroDeLatencias
from .notifier import BackendClient
from .rules import ControlDeRepeticion, evaluar_amenaza
from .storage import StorageService

log = logging.getLogger("microservicio")
_parar = threading.Event()


def configurar_registro(nivel=logging.INFO):
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def faltan_dependencias() -> list[str]:
    """Nombres de los paquetes pesados que no estan instalados."""
    faltan = []
    for modulo, paquete in (("cv2", "opencv-python"), ("ultralytics", "ultralytics")):
        try:
            __import__(modulo)
        except ImportError:
            faltan.append(paquete)
    return faltan


def avisar_si_la_clave_no_es_de_servicio(cfg) -> str | None:
    """
    Avisa si SUPABASE_SERVICE_KEY no es la clave de servicio.

    Con la clave anonima ahi puesta, el microservicio arranca sin quejarse y **las alertas
    llegan SIN IMAGEN**: Storage rechaza la subida, la notificacion se crea igual y el
    operador ve el aviso sin la evidencia. Paso el 13/10/2026 con el .env del autor, y por
    eso se comprueba al arrancar en lugar de descubrirlo en la primera alerta real.
    """
    rol = cfg.rol_de_la_clave_de_storage()
    if rol == "service_role":
        return rol
    if rol is None:
        log.warning("SUPABASE_SERVICE_KEY no parece una clave de Supabase: si Storage la "
                    "rechaza, las alertas llegaran SIN IMAGEN.")
    else:
        log.error("SUPABASE_SERVICE_KEY tiene el rol '%s' y hace falta 'service_role'. "
                  "Storage rechazara las subidas y las alertas llegaran SIN IMAGEN. "
                  "Copia la service key del panel de Supabase.", rol)
    return rol


def camaras_a_procesar(cfg, backend: BackendClient, solo: int | None) -> list[dict]:
    """Las del backend; si no responde, las de la configuracion local de zonas."""
    camaras = backend.consultar_camaras() if cfg.hay_backend() else None
    if camaras is not None:
        log.info("%s camaras leidas del backend", len(camaras))
    else:
        camaras = cfg.camaras_locales
        if camaras:
            log.warning("Se usan las %s camaras de la configuracion local", len(camaras))
    if solo is not None:
        camaras = [c for c in camaras if int(c.get("id", 0)) == solo]
    return camaras


class ZonasCompartidas:
    """
    Zonas restringidas que todas las hebras leen en cada fotograma.

    El supervisor las sustituye al releer la configuracion local, de modo que la zona de una
    camara nueva tampoco exige reiniciar.
    """

    def __init__(self, zonas: dict[int, list[tuple[float, float]]] | None = None):
        self._zonas = dict(zonas or {})
        self._candado = threading.Lock()

    def actuales(self) -> dict[int, list[tuple[float, float]]]:
        with self._candado:
            return self._zonas

    def reemplazar(self, zonas: dict[int, list[tuple[float, float]]]) -> None:
        with self._candado:
            self._zonas = dict(zonas)


def cambios_de_camaras(en_marcha: dict[int, str], nuevas: list[dict]) -> tuple[list[dict], list[int]]:
    """
    Que hebras hay que abrir y cuales cerrar para que coincidan con la lista nueva.

    en_marcha es {id_camara: flujo}. Una camara cuyo flujo cambio se cierra y se
    vuelve a abrir con el flujo nuevo. Las camaras sin flujo se ignoran.
    """
    nuevas_por_id = {int(c["id"]): c for c in nuevas if c.get("stream")}
    detener = [cid for cid, flujo in en_marcha.items()
               if cid not in nuevas_por_id or nuevas_por_id[cid]["stream"] != flujo]
    iniciar = [c for cid, c in nuevas_por_id.items()
               if cid not in en_marcha or c["stream"] != en_marcha[cid]]
    return iniciar, detener


class SupervisorDeCamaras:
    """Mantiene una hebra por camara y la lista al dia sin reiniciar el microservicio."""

    def __init__(self, arrancar_hebra, backend: BackendClient, cfg, solo: int | None = None,
                 zonas: ZonasCompartidas | None = None, leer_zonas=configuracion.cargar_zonas):
        self.arrancar_hebra = arrancar_hebra      # (camara, evento_de_parada) -> Thread
        self.backend = backend
        self.cfg = cfg
        self.solo = solo
        self.zonas = zonas
        self.leer_zonas = leer_zonas
        self.hebras: dict[int, tuple[str, threading.Thread, threading.Event]] = {}

    def en_marcha(self) -> dict[int, str]:
        return {cid: flujo for cid, (flujo, _, _) in self.hebras.items()}

    def aplicar(self, camaras: list[dict]) -> tuple[list[dict], list[int]]:
        """Abre y cierra hebras hasta que coincidan con la lista recibida."""
        if self.solo is not None:
            camaras = [c for c in camaras if int(c.get("id", 0)) == self.solo]
        iniciar, detener = cambios_de_camaras(self.en_marcha(), camaras)
        for cid in detener:
            _, _, evento = self.hebras.pop(cid)
            evento.set()
            log.info("Camara %s: se deja de analizar", cid)
        for camara in iniciar:
            evento = threading.Event()
            hebra = self.arrancar_hebra(camara, evento)
            self.hebras[int(camara["id"])] = (camara["stream"], hebra, evento)
        return iniciar, detener

    def revisar(self) -> tuple[list[dict], list[int]]:
        """
        Vuelve a leer la configuracion local de zonas y a pedir las camaras al backend.

        Si el backend no responde, no se cierra ninguna hebra: se mantiene lo que
        haya en marcha hasta la siguiente revision.
        """
        try:
            zonas, locales = self.leer_zonas()
        except Exception as error:                 # noqa: BLE001
            log.warning("No se pudo releer la configuracion local de zonas: %s", error)
            zonas, locales = None, None
        if self.zonas is not None and zonas is not None:
            self.zonas.reemplazar(zonas)

        if self.cfg.hay_backend():
            camaras = self.backend.consultar_camaras()
        else:
            camaras = locales
        if camaras is None:
            log.warning("No se pudo revisar la lista de camaras: siguen las %s en marcha",
                        len(self.hebras))
            return [], []
        iniciar, detener = self.aplicar(camaras)
        if iniciar or detener:
            log.info("Revision de camaras: %s nuevas o cambiadas, %s retiradas",
                     len(iniciar), len(detener))
        return iniciar, detener

    def detener_todas(self) -> None:
        for _, _, evento in self.hebras.values():
            evento.set()

    def esperar(self, tiempo_maximo: float | None = None) -> None:
        for _, hebra, _ in list(self.hebras.values()):
            hebra.join(timeout=tiempo_maximo)


def procesar_camara(camara: dict, cfg, detector: Detector, storage: StorageService | None,
                    backend: BackendClient, registro: RegistroDeLatencias,
                    control: ControlDeRepeticion, una_vez: bool, sin_envio: bool,
                    parar_camara: threading.Event | None = None,
                    zonas: ZonasCompartidas | None = None):
    """Bucle de una camara: capturar, detectar, aplicar reglas, avisar."""
    try:
        _procesar_camara(camara, cfg, detector, storage, backend, registro,
                         control, una_vez, sin_envio, parar_camara, zonas)
    except Exception as error:                     # noqa: BLE001
        log.error("Camara %s detenida por un error: %s", camara.get("id"), error)


def _procesar_camara(camara: dict, cfg, detector: Detector, storage: StorageService | None,
                     backend: BackendClient, registro: RegistroDeLatencias,
                     control: ControlDeRepeticion, una_vez: bool, sin_envio: bool,
                     parar_camara: threading.Event | None = None,
                     zonas: ZonasCompartidas | None = None):
    camara_id = int(camara["id"])
    captura = CameraCapture(camara["stream"], camara_id)
    log.info("Camara %s (%s) en marcha", camara_id, camara.get("nombre", ""))

    def debe_parar() -> bool:
        return _parar.is_set() or (parar_camara is not None and parar_camara.is_set())

    while not debe_parar():
        fotograma = captura.capturar_frame()
        if fotograma is None:
            if una_vez:
                break
            time.sleep(2)
            continue

        inicio = time.perf_counter()
        detecciones, anotado = detector.detectar(fotograma)
        ms_inferencia = (time.perf_counter() - inicio) * 1000

        if detecciones:
            ahora = datetime.now()
            zonas_actuales = zonas.actuales() if zonas is not None else cfg.zonas_restringidas
            amenaza = evaluar_amenaza(
                detecciones, camara_id, fotograma.shape, ahora,
                zonas_actuales, cfg.hora_inicio_laboral,
                cfg.hora_fin_laboral, cfg.dias_laborales)

            if amenaza.es_amenaza and control.permite(camara_id, amenaza.clase or "", ahora):
                log.warning("Camara %s: %s (%s, confianza %.2f)",
                            camara_id, amenaza.mensaje, amenaza.regla, amenaza.confianza)
                medicion = Medicion(camara_id=camara_id, clase=amenaza.clase or "",
                                    confianza=amenaza.confianza, regla=amenaza.regla or "",
                                    ms_inferencia=ms_inferencia)
                url = None
                if not sin_envio and storage is not None:
                    t = time.perf_counter()
                    url = storage.subir_imagen(anotado, camara_id)
                    medicion.ms_subida = (time.perf_counter() - t) * 1000
                if not sin_envio:
                    t = time.perf_counter()
                    creada = backend.crear_notificacion(
                        amenaza.mensaje or "", url, camara_id,
                        datetime.now(timezone.utc))
                    medicion.ms_notificacion = (time.perf_counter() - t) * 1000
                    medicion.enviada = creada is not None
                    medicion.notificacion_id = str((creada or {}).get("id", ""))
                medicion.ms_total = (medicion.ms_inferencia + medicion.ms_subida
                                     + medicion.ms_notificacion)
                registro.anotar(medicion)
                log.info("Camara %s: alerta gestionada en %.0f ms", camara_id, medicion.ms_total)

        if una_vez:
            break
        time.sleep(cfg.segundos_entre_fotogramas)

    captura.liberar()
    log.info("Camara %s detenida", camara_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Microservicio de IA del sistema de seguridad")
    parser.add_argument("--una-vez", action="store_true", help="un fotograma por camara y salir")
    parser.add_argument("--camara", type=int, default=None, help="procesar solo esta camara")
    parser.add_argument("--sin-envio", action="store_true", help="no subir imagenes ni notificar")
    parser.add_argument("--resumen", action="store_true", help="resumen de latencias y salir")
    parser.add_argument("--detalle", action="store_true", help="registro detallado")
    args = parser.parse_args(argv)

    configurar_registro(logging.DEBUG if args.detalle else logging.INFO)
    registro = RegistroDeLatencias()

    if args.resumen:
        print(registro.resumen())
        return 0

    faltan = faltan_dependencias()
    if faltan:
        log.error("Faltan dependencias para funcionar: %s", ", ".join(faltan))
        log.error("Instalalas con:  pip install -r requirements.txt")
        return 3

    cfg = configuracion.cargar()
    log.info("Microservicio de IA iniciado")

    backend = BackendClient(cfg.backend_url, cfg.backend_email, cfg.backend_password)
    backend.iniciar_sesion()

    camaras = camaras_a_procesar(cfg, backend, args.camara)
    if not camaras and args.una_vez:
        log.error("No hay camaras que procesar. Registra camaras en el sistema "
                  "o anotalas en zonas.local.json")
        return 2

    detector = Detector(cfg.ruta_modelo, cfg.confianza_minima, cfg.clases)
    storage = (StorageService(cfg.supabase_url, cfg.supabase_service_key, cfg.supabase_bucket)
               if cfg.hay_storage() else None)
    if storage is None:
        log.warning("Supabase Storage no esta configurado: las alertas iran sin imagen")
    else:
        avisar_si_la_clave_no_es_de_servicio(cfg)
    control = ControlDeRepeticion(cfg.segundos_de_espera_por_alerta)
    zonas = ZonasCompartidas(cfg.zonas_restringidas)

    def detener(*_):
        log.info("Deteniendo el microservicio")
        _parar.set()

    signal.signal(signal.SIGINT, detener)
    signal.signal(signal.SIGTERM, detener)

    def arrancar_hebra(camara: dict, parar_camara: threading.Event) -> threading.Thread:
        hebra = threading.Thread(
            target=procesar_camara,
            args=(camara, cfg, detector, storage, backend, registro, control,
                  args.una_vez, args.sin_envio),
            kwargs={"parar_camara": parar_camara, "zonas": zonas},
            name=f"camara-{camara['id']}", daemon=True)
        hebra.start()
        return hebra

    supervisor = SupervisorDeCamaras(arrancar_hebra, backend, cfg, solo=args.camara, zonas=zonas)
    supervisor.aplicar(camaras)

    if args.una_vez:
        supervisor.esperar()
    else:
        if not camaras:
            log.warning("Todavia no hay camaras: se revisara la lista cada %s s",
                        cfg.segundos_entre_revisiones_de_camaras)
        try:
            while not _parar.wait(cfg.segundos_entre_revisiones_de_camaras):
                supervisor.revisar()
        except KeyboardInterrupt:
            detener()
        supervisor.detener_todas()
        supervisor.esperar(tiempo_maximo=5)

    resumen = registro.resumen()
    log.info("Resumen de latencias: %s", resumen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
