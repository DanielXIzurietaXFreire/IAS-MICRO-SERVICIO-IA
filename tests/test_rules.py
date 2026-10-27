# -*- coding: utf-8 -*-
"""
Pruebas de las reglas de negocio: son el objetivo especifico 2 de la tesis.

Los nombres llevan el NUMERO VIGENTE de cada regla (ver rules.py y CLAUDE.md §2):
    regla 1  arma, aunque haya guardia
    regla 2  guardia sin arma, no se alerta
    regla 3  rostro cubierto
    regla 4  persona en zona restringida fuera de horario
Se renumeraron el 14/10/2026: desde el 29/09 el arma prevalece sobre el guardia, y los
nombres se habian quedado con la numeracion anterior.
"""
from datetime import datetime, time

import pytest

from src.rules import (
    MENSAJE_ARMA,
    MENSAJE_ROSTRO,
    MENSAJE_ZONA,
    ControlDeRepeticion,
    centro_normalizado,
    dentro_de_zona_restringida,
    es_hora_laboral,
    evaluar_amenaza,
    punto_en_poligono,
)

FORMA = (720, 1280, 3)                       # alto, ancho, canales
ZONA_CENTRAL = {1: [(0.2, 0.3), (0.8, 0.3), (0.8, 0.9), (0.2, 0.9)]}
LUNES_10H = datetime(2026, 9, 21, 10, 0)     # dentro de la jornada
LUNES_23H = datetime(2026, 9, 21, 23, 0)     # fuera de la jornada
DOMINGO_10H = datetime(2026, 9, 20, 10, 0)   # fin de semana


def deteccion(clase, bbox=(600, 300, 700, 500), confianza=0.9):
    return {"clase": clase, "clase_id": 0, "confianza": confianza, "bbox": list(bbox)}


# ------------------------------------------------------------------ horario
@pytest.mark.parametrize("momento, esperado", [
    (LUNES_10H, True),
    (LUNES_23H, False),
    (DOMINGO_10H, False),
    (datetime(2026, 9, 21, 8, 0), True),      # justo al abrir
    (datetime(2026, 9, 21, 18, 0), True),     # justo al cerrar
    (datetime(2026, 9, 21, 7, 59), False),
])
def test_es_hora_laboral(momento, esperado):
    assert es_hora_laboral(momento) is esperado


def test_horario_configurable():
    assert es_hora_laboral(datetime(2026, 9, 21, 20, 0), time(6, 0), time(22, 0)) is True


# -------------------------------------------------------------------- zonas
def test_punto_dentro_y_fuera_del_poligono():
    poligono = ZONA_CENTRAL[1]
    assert punto_en_poligono((0.5, 0.5), poligono) is True
    assert punto_en_poligono((0.05, 0.5), poligono) is False
    assert punto_en_poligono((0.5, 0.95), poligono) is False


def test_poligono_invalido_no_revienta():
    assert punto_en_poligono((0.5, 0.5), [(0.1, 0.1), (0.2, 0.2)]) is False


def test_centro_normalizado():
    assert centro_normalizado([0, 0, 1280, 720], 1280, 720) == (0.5, 0.5)


def test_camara_sin_zona_definida_no_restringe():
    assert dentro_de_zona_restringida([600, 300, 700, 500], 99, FORMA, ZONA_CENTRAL) is False


def test_persona_dentro_de_la_zona():
    assert dentro_de_zona_restringida([600, 300, 700, 500], 1, FORMA, ZONA_CENTRAL) is True


def test_persona_fuera_de_la_zona():
    assert dentro_de_zona_restringida([0, 0, 40, 40], 1, FORMA, ZONA_CENTRAL) is False


# ------------------------------------------------------------------ reglas
def test_regla_2_guardia_no_genera_alerta():
    r = evaluar_amenaza([deteccion("guardia"), deteccion("persona")], 1, FORMA,
                        LUNES_23H, ZONA_CENTRAL)
    assert r.es_amenaza is False
    assert "autorizado" in r.regla


def test_el_arma_prevalece_sobre_el_guardia():
    """
    Decision del autor (29/09/2026): un arma alerta aunque haya un guardia en el
    encuadre. Antes el guardia apagaba tambien esta alerta, y entonces un asalto
    con un guardia presente no avisaba a nadie.
    """
    r = evaluar_amenaza([deteccion("guardia"), deteccion("arma")], 1, FORMA, LUNES_10H)
    assert r.es_amenaza is True
    assert r.mensaje == MENSAJE_ARMA
    assert r.clase == "arma"
    assert "prevalece" in r.regla


def test_el_guardia_sigue_apagando_el_rostro_cubierto():
    r = evaluar_amenaza([deteccion("guardia"), deteccion("rostro_cubierto")], 1, FORMA, LUNES_10H)
    assert r.es_amenaza is False
    assert "autorizado" in r.regla


def test_el_guardia_sigue_apagando_la_persona_en_zona_restringida():
    r = evaluar_amenaza([deteccion("guardia"), deteccion("persona")], 1, FORMA,
                        LUNES_23H, ZONA_CENTRAL)
    assert r.es_amenaza is False


def test_regla_1_arma_alerta_inmediata_aunque_sea_horario_laboral():
    r = evaluar_amenaza([deteccion("arma", confianza=0.77)], 1, FORMA, LUNES_10H)
    assert r.es_amenaza is True
    assert r.mensaje == MENSAJE_ARMA
    assert r.clase == "arma"
    assert r.confianza == 0.77


def test_regla_3_rostro_cubierto():
    r = evaluar_amenaza([deteccion("rostro_cubierto")], 1, FORMA, LUNES_10H)
    assert r.es_amenaza is True
    assert r.mensaje == MENSAJE_ROSTRO


def test_regla_1_arma_toma_la_deteccion_mas_confiable():
    r = evaluar_amenaza([deteccion("arma", confianza=0.55), deteccion("arma", confianza=0.93)],
                        1, FORMA, LUNES_10H)
    assert r.confianza == 0.93


def test_regla_4_persona_en_zona_fuera_de_horario():
    r = evaluar_amenaza([deteccion("persona")], 1, FORMA, LUNES_23H, ZONA_CENTRAL)
    assert r.es_amenaza is True
    assert r.mensaje == MENSAJE_ZONA


def test_regla_4_persona_en_zona_dentro_de_horario_no_alerta():
    r = evaluar_amenaza([deteccion("persona")], 1, FORMA, LUNES_10H, ZONA_CENTRAL)
    assert r.es_amenaza is False


def test_regla_4_persona_fuera_de_la_zona_no_alerta():
    r = evaluar_amenaza([deteccion("persona", bbox=(0, 0, 40, 40))], 1, FORMA,
                        LUNES_23H, ZONA_CENTRAL)
    assert r.es_amenaza is False


def test_sin_detecciones_no_hay_alerta():
    assert evaluar_amenaza([], 1, FORMA, LUNES_23H, ZONA_CENTRAL).es_amenaza is False


# ------------------------------------------------------- control de repeticion
def test_no_repite_la_misma_alerta_antes_de_tiempo():
    control = ControlDeRepeticion(segundos=60)
    momento = datetime(2026, 9, 21, 23, 0, 0)
    assert control.permite(1, "arma", momento) is True
    assert control.permite(1, "arma", datetime(2026, 9, 21, 23, 0, 30)) is False
    assert control.permite(1, "arma", datetime(2026, 9, 21, 23, 1, 1)) is True


def test_camaras_y_clases_distintas_no_se_bloquean():
    control = ControlDeRepeticion(segundos=60)
    momento = datetime(2026, 9, 21, 23, 0, 0)
    assert control.permite(1, "arma", momento) is True
    assert control.permite(2, "arma", momento) is True
    assert control.permite(1, "rostro_cubierto", momento) is True


# ------------------------- lo que no dependia del momento ni del lugar
# Las pruebas de arriba comprueban el arma y el rostro cubierto un lunes a las 10:00 y sin
# zona definida. Eso dejaba sin fijar lo que mas importa de las reglas 1 y 3: que alertan
# SIEMPRE, a cualquier hora y en cualquier sitio. Anadidas el 14/10/2026.
@pytest.mark.parametrize("momento", [LUNES_10H, LUNES_23H, DOMINGO_10H],
                         ids=["dentro de horario", "de noche", "domingo"])
def test_regla_1_el_arma_alerta_a_cualquier_hora(momento):
    r = evaluar_amenaza([deteccion("arma")], 1, FORMA, momento, ZONA_CENTRAL)

    assert r.es_amenaza is True
    assert r.mensaje == MENSAJE_ARMA


@pytest.mark.parametrize("momento", [LUNES_10H, LUNES_23H, DOMINGO_10H],
                         ids=["dentro de horario", "de noche", "domingo"])
def test_regla_3_el_rostro_cubierto_alerta_a_cualquier_hora(momento):
    r = evaluar_amenaza([deteccion("rostro_cubierto")], 1, FORMA, momento, ZONA_CENTRAL)

    assert r.es_amenaza is True
    assert r.mensaje == MENSAJE_ROSTRO


@pytest.mark.parametrize("bbox, donde", [
    ((600, 300, 700, 500), "dentro de la zona restringida"),
    ((0, 0, 40, 40), "fuera de la zona restringida"),
])
def test_las_reglas_1_y_3_no_miran_la_zona(bbox, donde):
    """Un arma o un rostro cubierto son amenaza este donde este: la zona solo importa en la 4."""
    for clase, mensaje in (("arma", MENSAJE_ARMA), ("rostro_cubierto", MENSAJE_ROSTRO)):
        r = evaluar_amenaza([deteccion(clase, bbox=bbox)], 1, FORMA, LUNES_10H, ZONA_CENTRAL)

        assert r.es_amenaza is True, f"{clase} {donde}"
        assert r.mensaje == mensaje


def test_regla_3_el_rostro_cubierto_toma_la_deteccion_mas_confiable():
    # El arma ya lo comprobaba; el rostro cubierto, no.
    r = evaluar_amenaza([deteccion("rostro_cubierto", confianza=0.61),
                         deteccion("rostro_cubierto", confianza=0.88)],
                        1, FORMA, LUNES_10H)

    assert r.clase == "rostro_cubierto"
    assert r.confianza == 0.88
