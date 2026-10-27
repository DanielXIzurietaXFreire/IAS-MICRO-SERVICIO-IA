# -*- coding: utf-8 -*-
"""
Reglas de negocio: deciden cuando una deteccion se convierte en alerta.

Aqui vive el objetivo especifico 2 de la tesis: separar al personal autorizado
del no autorizado con tres criterios contextuales.

  1. Caracteristicas observables: arma y rostro cubierto (amenaza), guardia (autorizado).
     El arma prevalece sobre el guardia.
  2. Horario: dentro o fuera de la jornada laboral configurada.
  3. Ubicacion: dentro o fuera de una zona restringida de esa camara.

Todo son funciones puras: no tocan la camara, ni la red, ni el reloj del sistema
salvo cuando se les pasa la hora. Asi se pueden probar sin instalar nada pesado.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time

# Mensajes canonicos de cada alerta. Se escriben una sola vez para que el texto
# que llega al operador sea siempre el mismo.
MENSAJE_ARMA = "Arma detectada en zona vigilada"
MENSAJE_ROSTRO = "Rostro cubierto detectado, posible intruso"
MENSAJE_ZONA = "Persona no autorizada en zona restringida"


@dataclass(frozen=True)
class Amenaza:
    """Resultado de evaluar un fotograma."""

    es_amenaza: bool
    mensaje: str | None = None
    clase: str | None = None
    confianza: float = 0.0
    regla: str | None = None


def es_hora_laboral(
    ahora: datetime,
    inicio: time = time(8, 0),
    fin: time = time(18, 0),
    dias_laborales: tuple[int, ...] = (0, 1, 2, 3, 4),
) -> bool:
    """True si el momento cae dentro de la jornada laboral configurada."""
    return ahora.weekday() in dias_laborales and inicio <= ahora.time() <= fin


def punto_en_poligono(punto: tuple[float, float], poligono: list[tuple[float, float]]) -> bool:
    """
    Algoritmo del rayo: cuenta cuantos lados cruza una semirrecta horizontal.
    Impar = dentro. Se implementa a mano para no depender de OpenCV.
    """
    if len(poligono) < 3:
        return False
    x, y = punto
    dentro = False
    for i in range(len(poligono)):
        x1, y1 = poligono[i]
        x2, y2 = poligono[(i + 1) % len(poligono)]
        if (y1 > y) != (y2 > y):
            corte = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < corte:
                dentro = not dentro
    return dentro


def centro_normalizado(bbox: list[float], ancho: int, alto: int) -> tuple[float, float]:
    """Centro geometrico del recuadro, en coordenadas de 0 a 1."""
    x1, y1, x2, y2 = bbox[:4]
    return ((x1 + x2) / 2 / ancho, (y1 + y2) / 2 / alto)


def dentro_de_zona_restringida(
    bbox: list[float],
    camara_id: int,
    forma_fotograma: tuple[int, int],
    zonas: dict[int, list[tuple[float, float]]],
) -> bool:
    """True si el centro del recuadro cae dentro de la zona restringida de la camara."""
    poligono = zonas.get(camara_id)
    if not poligono:
        return False
    alto, ancho = forma_fotograma[0], forma_fotograma[1]
    if not ancho or not alto:
        return False
    return punto_en_poligono(centro_normalizado(bbox, ancho, alto), poligono)


def evaluar_amenaza(
    detecciones: list[dict],
    camara_id: int,
    forma_fotograma: tuple[int, int],
    ahora: datetime,
    zonas: dict[int, list[tuple[float, float]]] | None = None,
    hora_inicio: time = time(8, 0),
    hora_fin: time = time(18, 0),
    dias_laborales: tuple[int, ...] = (0, 1, 2, 3, 4),
) -> Amenaza:
    """
    Aplica las cuatro reglas, en este orden:

      Regla 1  hay un arma  ->  alerta inmediata, aunque haya un guardia en la escena.
      Regla 2  hay un guardia y no hay arma  ->  no se alerta (personal autorizado).
      Regla 3  hay un rostro cubierto  ->  alerta inmediata.
      Regla 4  hay una persona dentro de zona restringida y fuera de horario  ->  alerta.

    El arma va primero por decision del autor (29/09/2026). Antes, un guardia en el
    encuadre apagaba tambien la alerta del arma, y entonces un asalto con un guardia
    presente no generaba ningun aviso: justo el caso que el sistema tiene que cazar.
    El guardia sigue apagando el rostro cubierto y la persona en zona restringida.
    """
    zonas = zonas or {}
    clases = {d.get("clase") for d in detecciones}

    def mas_confiable(clase):
        return max((d for d in detecciones if d.get("clase") == clase),
                   key=lambda d: d.get("confianza", 0.0))

    if "arma" in clases:
        det = mas_confiable("arma")
        return Amenaza(True, MENSAJE_ARMA, "arma", float(det.get("confianza", 0.0)),
                       "1: arma detectada, prevalece sobre el personal autorizado")

    if "guardia" in clases:
        return Amenaza(False, regla="2: personal autorizado en la escena")

    if "rostro_cubierto" in clases:
        det = mas_confiable("rostro_cubierto")
        return Amenaza(True, MENSAJE_ROSTRO, "rostro_cubierto",
                       float(det.get("confianza", 0.0)),
                       "3: caracteristica observable critica")

    if "persona" in clases and not es_hora_laboral(ahora, hora_inicio, hora_fin, dias_laborales):
        for det in detecciones:
            if det.get("clase") != "persona":
                continue
            if dentro_de_zona_restringida(det.get("bbox", []), camara_id, forma_fotograma, zonas):
                return Amenaza(True, MENSAJE_ZONA, "persona",
                               float(det.get("confianza", 0.0)),
                               "4: ubicacion y horario")

    return Amenaza(False, regla="ninguna regla se cumple")


@dataclass
class ControlDeRepeticion:
    """
    Evita inundar al operador: la misma alerta, en la misma camara, no se repite
    hasta que pasan los segundos de espera configurados.
    """

    segundos: int = 60
    _ultimas: dict[tuple[int, str], datetime] = field(default_factory=dict)

    def permite(self, camara_id: int, clase: str, ahora: datetime) -> bool:
        anterior = self._ultimas.get((camara_id, clase))
        if anterior is not None and (ahora - anterior).total_seconds() < self.segundos:
            return False
        self._ultimas[(camara_id, clase)] = ahora
        return True
