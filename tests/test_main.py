# -*- coding: utf-8 -*-
"""
Prueba del flujo completo, con dobles de prueba en lugar de camara, modelo y red.

Recorre toda la cadena: fotograma -> deteccion -> reglas -> imagen -> notificacion
-> medicion de latencia. Es la version reducida de la prueba end-to-end del sistema.
"""
import threading
from datetime import time

from src import main as principal
from src.config import Config
from src.metrics import RegistroDeLatencias
from src.rules import MENSAJE_ARMA, MENSAJE_ZONA, ControlDeRepeticion


class FotogramaFalso:
    shape = (720, 1280, 3)


class DetectorFalso:
    """Devuelve siempre las mismas detecciones, sin cargar YOLO."""

    def __init__(self, detecciones):
        self.detecciones = detecciones

    def detectar(self, fotograma):
        return self.detecciones, fotograma


class CamaraFalsa:
    def __init__(self, url, camara_id, **kwargs):
        self.camara_id = camara_id
        self.liberada = False

    def capturar_frame(self):
        return FotogramaFalso()

    def liberar(self):
        self.liberada = True


class StorageFalso:
    def __init__(self):
        self.subidas = []

    def subir_imagen(self, fotograma, camara_id, momento=None):
        self.subidas.append(camara_id)
        return f"https://storage/camara_{camara_id}.jpg"


class BackendFalso:
    def __init__(self):
        self.notificaciones = []

    def crear_notificacion(self, mensaje, imagen_url, camara_id, momento=None):
        self.notificaciones.append(
            {"mensaje": mensaje, "imagenUrl": imagen_url, "camaraId": camara_id})
        return {"id": len(self.notificaciones)}


def configuracion(zonas=None):
    return Config(
        zonas_restringidas=zonas or {1: [(0.2, 0.3), (0.8, 0.3), (0.8, 0.9), (0.2, 0.9)]},
        segundos_entre_fotogramas=0,
        hora_inicio_laboral=time(8, 0),
        hora_fin_laboral=time(18, 0),
    )


def deteccion(clase, bbox=(600, 300, 700, 500)):
    return {"clase": clase, "clase_id": 0, "confianza": 0.91, "bbox": list(bbox)}


def ejecutar(detecciones, tmp_path, monkeypatch, sin_envio=False, zonas=None):
    monkeypatch.setattr(principal, "CameraCapture", CamaraFalsa)
    storage, backend = StorageFalso(), BackendFalso()
    registro = RegistroDeLatencias(ruta=tmp_path / "latencias.csv")
    principal.procesar_camara(
        {"id": 1, "nombre": "Caja", "stream": "rtsp://falsa"},
        configuracion(zonas), DetectorFalso(detecciones), storage, backend,
        registro, ControlDeRepeticion(segundos=60), una_vez=True, sin_envio=sin_envio)
    return storage, backend, registro


def test_un_arma_recorre_toda_la_cadena(tmp_path, monkeypatch):
    storage, backend, registro = ejecutar([deteccion("arma")], tmp_path, monkeypatch)

    assert storage.subidas == [1]
    assert len(backend.notificaciones) == 1
    aviso = backend.notificaciones[0]
    assert aviso["mensaje"] == MENSAJE_ARMA
    assert aviso["camaraId"] == 1
    assert aviso["imagenUrl"].endswith("camara_1.jpg")

    resumen = registro.resumen()
    assert resumen["alertas"] == 1


def test_un_guardia_no_genera_nada(tmp_path, monkeypatch):
    storage, backend, registro = ejecutar([deteccion("guardia")], tmp_path, monkeypatch)
    assert storage.subidas == []
    assert backend.notificaciones == []
    assert registro.resumen() == {"alertas": 0}


def test_modo_sin_envio_mide_pero_no_avisa(tmp_path, monkeypatch):
    storage, backend, registro = ejecutar([deteccion("arma")], tmp_path, monkeypatch,
                                          sin_envio=True)
    assert storage.subidas == []
    assert backend.notificaciones == []
    assert registro.resumen()["alertas"] == 1       # la latencia si se mide


def test_persona_fuera_de_la_zona_no_avisa(tmp_path, monkeypatch):
    _, backend, _ = ejecutar([deteccion("persona", bbox=(0, 0, 30, 30))],
                             tmp_path, monkeypatch)
    assert backend.notificaciones == []


def test_camaras_del_backend_se_filtran_por_identificador():
    class BackendConCamaras:
        def consultar_camaras(self):
            return [{"id": 1, "stream": "a"}, {"id": 3, "stream": "b"}]

    cfg = configuracion()
    assert len(principal.camaras_a_procesar(cfg, BackendConCamaras(), None)) == 2
    solo = principal.camaras_a_procesar(cfg, BackendConCamaras(), 3)
    assert [c["id"] for c in solo] == [3]


def test_si_el_backend_no_responde_usa_las_camaras_locales():
    class BackendMudo:
        def consultar_camaras(self):
            return None          # no responde

    cfg = Config(camaras_locales=[{"id": 9, "stream": "rtsp://local"}])
    camaras = principal.camaras_a_procesar(cfg, BackendMudo(), None)
    assert [c["id"] for c in camaras] == [9]


def test_si_el_backend_responde_sin_camaras_no_usa_las_locales():
    class BackendVacio:
        def consultar_camaras(self):
            return []            # responde, pero no hay camaras registradas

    cfg = Config(camaras_locales=[{"id": 9, "stream": "rtsp://local"}])
    assert principal.camaras_a_procesar(cfg, BackendVacio(), None) == []


# ------------------------------------------------ revision de camaras sin reiniciar
def test_cambios_detecta_camaras_nuevas_y_retiradas():
    en_marcha = {1: "rtsp://uno", 2: "rtsp://dos"}
    nuevas = [{"id": 1, "stream": "rtsp://uno"}, {"id": 3, "stream": "rtsp://tres"}]

    iniciar, detener = principal.cambios_de_camaras(en_marcha, nuevas)

    assert [c["id"] for c in iniciar] == [3]
    assert detener == [2]


def test_un_flujo_cambiado_reinicia_la_camara():
    iniciar, detener = principal.cambios_de_camaras(
        {1: "rtsp://viejo"}, [{"id": 1, "stream": "rtsp://nuevo"}])

    assert [c["stream"] for c in iniciar] == ["rtsp://nuevo"]
    assert detener == [1]


def test_una_camara_sin_flujo_no_se_analiza():
    iniciar, detener = principal.cambios_de_camaras({}, [{"id": 5, "stream": ""}])
    assert iniciar == [] and detener == []


class BackendCambiante:
    """Devuelve una lista distinta en cada consulta; None simula que no responde."""

    def __init__(self, *listas):
        self.listas = list(listas)

    def consultar_camaras(self):
        return self.listas.pop(0)


def supervisor_de_prueba(backend, cfg=None, solo=None, zonas=None, leer_zonas=None):
    arrancadas = []

    def arrancar(camara, evento):
        arrancadas.append((camara["id"], evento))
        return threading.Thread(target=lambda: None)

    sup = principal.SupervisorDeCamaras(
        arrancar, backend, cfg or Config(), solo=solo, zonas=zonas,
        leer_zonas=leer_zonas or (lambda: ({}, [])))
    return sup, arrancadas


def test_la_revision_abre_y_cierra_hebras_sin_reiniciar():
    backend = BackendCambiante([{"id": 1, "stream": "a"}, {"id": 2, "stream": "b"}])
    sup, arrancadas = supervisor_de_prueba(backend)
    sup.aplicar([{"id": 1, "stream": "a"}])
    evento_1 = arrancadas[0][1]

    iniciar, detener = sup.revisar()          # aparece la camara 2
    assert [c["id"] for c in iniciar] == [2]
    assert detener == []
    assert sorted(sup.en_marcha()) == [1, 2]

    sup.backend = BackendCambiante([{"id": 2, "stream": "b"}])
    _, detener = sup.revisar()                # se retira la camara 1
    assert detener == [1]
    assert evento_1.is_set()                  # su hebra recibe la orden de parar
    assert sorted(sup.en_marcha()) == [2]


def test_si_el_backend_no_responde_la_revision_no_toca_nada():
    sup, _ = supervisor_de_prueba(BackendCambiante(None))
    sup.aplicar([{"id": 1, "stream": "a"}])

    assert sup.revisar() == ([], [])
    assert sup.en_marcha() == {1: "a"}


def test_la_revision_respeta_el_filtro_de_camara():
    backend = BackendCambiante([{"id": 1, "stream": "a"}, {"id": 3, "stream": "c"}])
    sup, arrancadas = supervisor_de_prueba(backend, solo=3)

    sup.revisar()

    assert [cid for cid, _ in arrancadas] == [3]


def test_la_revision_relee_las_zonas():
    zonas = principal.ZonasCompartidas({1: [(0, 0), (1, 0), (1, 1)]})
    nuevas = {7: [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9)]}
    sup, _ = supervisor_de_prueba(BackendCambiante([]), zonas=zonas,
                                  leer_zonas=lambda: (nuevas, []))

    sup.revisar()

    assert zonas.actuales() == nuevas


def test_sin_backend_la_revision_usa_zonas_json():
    cfg = Config(backend_url="")
    sup, arrancadas = supervisor_de_prueba(
        None, cfg=cfg, leer_zonas=lambda: ({}, [{"id": 4, "stream": "rtsp://local"}]))

    sup.revisar()

    assert [cid for cid, _ in arrancadas] == [4]


def test_una_camara_se_detiene_con_su_propio_evento(monkeypatch, tmp_path):
    monkeypatch.setattr(principal, "CameraCapture", CamaraFalsa)
    parar = threading.Event()
    parar.set()
    backend = BackendFalso()

    principal.procesar_camara(
        {"id": 1, "stream": "rtsp://falsa"}, configuracion(), DetectorFalso([deteccion("arma")]),
        StorageFalso(), backend, RegistroDeLatencias(ruta=tmp_path / "l.csv"),
        ControlDeRepeticion(segundos=60), una_vez=False, sin_envio=False, parar_camara=parar)

    assert backend.notificaciones == []      # salio sin procesar ningun fotograma


def test_las_hebras_leen_las_zonas_compartidas(monkeypatch, tmp_path):
    # La camara 1 no tiene zona en la configuracion, pero si en las zonas compartidas.
    monkeypatch.setattr(principal, "CameraCapture", CamaraFalsa)
    monkeypatch.setattr(principal, "datetime", type("Reloj", (), {
        "now": staticmethod(lambda tz=None: __import__("datetime").datetime(2026, 9, 27, 23, 0)),
    }))
    zonas = principal.ZonasCompartidas({1: [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]})
    backend = BackendFalso()

    principal.procesar_camara(
        {"id": 1, "stream": "rtsp://falsa"}, Config(zonas_restringidas={}, segundos_entre_fotogramas=0),
        DetectorFalso([deteccion("persona")]), StorageFalso(), backend,
        RegistroDeLatencias(ruta=tmp_path / "l.csv"), ControlDeRepeticion(segundos=60),
        una_vez=True, sin_envio=False, zonas=zonas)

    assert [n["mensaje"] for n in backend.notificaciones] == [MENSAJE_ZONA]
