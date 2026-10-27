# -*- coding: utf-8 -*-
"""
Pruebas de la captura y del detector, con dobles: no hacen falta OpenCV,
ni Ultralytics, ni una camara encendida.
"""
import pytest

from src.camera import CameraCapture
from src.detector import Detector


class CapturaFalsa:
    """Imita cv2.VideoCapture: devuelve la secuencia de lecturas que se le da."""

    def __init__(self, lecturas):
        self.lecturas = list(lecturas)
        self.liberada = 0

    def read(self):
        return self.lecturas.pop(0) if self.lecturas else (False, None)

    def release(self):
        self.liberada += 1


class CajaFalsa:
    def __init__(self, clase_id, confianza, bbox):
        self.cls = [clase_id]
        self.conf = [confianza]
        self.xyxy = [type("V", (), {"tolist": lambda self_, b=bbox: list(b)})()]


class ResultadoFalso:
    def __init__(self, cajas):
        self.boxes = cajas

    def plot(self):
        return "fotograma-anotado"


class ModeloFalso:
    def __init__(self, cajas):
        self.cajas = cajas
        self.llamadas = []

    def __call__(self, fotograma, conf=0.5, verbose=False):
        self.llamadas.append({"conf": conf})
        return [ResultadoFalso(self.cajas)]


# ------------------------------------------------------------------ camara
def test_devuelve_el_fotograma_cuando_el_flujo_responde():
    camara = CameraCapture("rtsp://x", 1, captura=CapturaFalsa([(True, "fotograma")]))
    assert camara.capturar_frame() == "fotograma"


def test_se_reconecta_cuando_se_cae_el_flujo(monkeypatch):
    """Tras una lectura fallida, libera la captura y vuelve a abrir."""
    monkeypatch.setattr("src.camera._time.sleep", lambda s: None)
    primera = CapturaFalsa([(False, None)])
    segunda = CapturaFalsa([(True, "fotograma-nuevo")])
    camara = CameraCapture("rtsp://x", 1, captura=primera)
    monkeypatch.setattr(camara, "_abrir", lambda: segunda)

    assert camara.capturar_frame() == "fotograma-nuevo"
    assert primera.liberada == 1


def test_se_rinde_tras_los_reintentos(monkeypatch):
    monkeypatch.setattr("src.camera._time.sleep", lambda s: None)
    camara = CameraCapture("rtsp://x", 1, reintentos=0, captura=CapturaFalsa([(False, None)]))
    monkeypatch.setattr(camara, "_abrir", lambda: CapturaFalsa([(False, None)]))
    assert camara.capturar_frame() is None


def test_liberar_dos_veces_no_revienta():
    captura = CapturaFalsa([])
    camara = CameraCapture("rtsp://x", 1, captura=captura)
    camara.liberar()
    camara.liberar()
    assert captura.liberada == 1


# ---------------------------------------------------------------- detector
def test_traduce_las_cajas_a_detecciones():
    modelo = ModeloFalso([CajaFalsa(1, 0.876543, (10, 20, 30, 40)),
                          CajaFalsa(3, 0.5, (0, 0, 5, 5))])
    detector = Detector(confianza=0.6, modelo=modelo)
    detecciones, anotado = detector.detectar("fotograma")

    assert detecciones == [
        {"clase_id": 1, "clase": "arma", "confianza": 0.8765, "bbox": [10.0, 20.0, 30.0, 40.0]},
        {"clase_id": 3, "clase": "guardia", "confianza": 0.5, "bbox": [0.0, 0.0, 5.0, 5.0]},
    ]
    assert anotado == "fotograma-anotado"
    assert modelo.llamadas[0]["conf"] == 0.6      # respeta el umbral configurado


def test_sin_cajas_devuelve_el_fotograma_original():
    detector = Detector(modelo=ModeloFalso([]))
    detecciones, anotado = detector.detectar("fotograma")
    assert detecciones == []
    assert anotado == "fotograma"


def test_avisa_claro_si_falta_el_modelo_entrenado(tmp_path):
    detector = Detector(ruta_modelo=tmp_path / "best.pt")
    with pytest.raises(FileNotFoundError, match="best.pt"):
        _ = detector.modelo


# -------------------------------------------- la espera creciente al reconectar
# Las pruebas de arriba ponen la espera a cero para que corran rapido, asi que nadie
# comprobaba que la espera EXISTE y que crece. Si alguien borrara el sleep, el
# microservicio reintentaria en bucle contra una camara caida y saturaria la red.
# Anadidas el 14/10/2026.
def test_la_espera_al_reconectar_crece_con_cada_fallo(monkeypatch):
    esperas = []
    monkeypatch.setattr("src.camera._time.sleep", esperas.append)
    camara = CameraCapture("rtsp://x", 1, reintentos=3,
                           captura=CapturaFalsa([(False, None)]))
    monkeypatch.setattr(camara, "_abrir", lambda: CapturaFalsa([(False, None)]))

    for _ in range(3):
        camara.capturar_frame()

    assert esperas == [2, 4, 8], "la espera debe duplicarse: 2, 4 y 8 segundos"


def test_la_espera_al_reconectar_tiene_tope(monkeypatch):
    """Sin tope, al octavo fallo esperaria 256 s y la camara quedaria muerta un rato largo."""
    esperas = []
    monkeypatch.setattr("src.camera._time.sleep", esperas.append)
    camara = CameraCapture("rtsp://x", 1, reintentos=20,
                           captura=CapturaFalsa([(False, None)]))
    monkeypatch.setattr(camara, "_abrir", lambda: CapturaFalsa([(False, None)]))

    for _ in range(8):
        camara.capturar_frame()

    assert max(esperas) == 10, "el tope son 10 segundos"


def test_tras_un_fotograma_bueno_la_espera_vuelve_a_empezar(monkeypatch):
    esperas = []
    monkeypatch.setattr("src.camera._time.sleep", esperas.append)
    # falla, se recupera, y vuelve a fallar: la segunda caida empieza otra vez por 2 s
    camara = CameraCapture("rtsp://x", 1, captura=CapturaFalsa([(False, None)]))
    monkeypatch.setattr(camara, "_abrir", lambda: CapturaFalsa([(True, "fotograma")]))
    camara.capturar_frame()
    primera_tanda = list(esperas)

    camara._cap = CapturaFalsa([(False, None)])
    camara.capturar_frame()

    assert primera_tanda == [2]
    assert esperas == [2, 2]
