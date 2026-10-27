# -*- coding: utf-8 -*-
"""Pruebas del registro de latencias: de aqui salen las cifras de la tesis."""
import csv

from src.metrics import COLUMNAS, Medicion, RegistroDeLatencias


def test_la_medicion_se_fecha_sola():
    assert Medicion().momento_utc != ""


def test_escribe_cabecera_una_sola_vez(tmp_path):
    registro = RegistroDeLatencias(ruta=tmp_path / "latencias.csv")
    registro.anotar(Medicion(camara_id=1, clase="arma", ms_total=812.37))
    registro.anotar(Medicion(camara_id=2, clase="persona", ms_total=1200.0))

    with (tmp_path / "latencias.csv").open(encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    assert len(filas) == 2
    assert list(filas[0]) == COLUMNAS
    assert filas[0]["ms_total"] == "812.4"      # se redondea a una decima


def test_resumen_calcula_mediana_y_media(tmp_path):
    registro = RegistroDeLatencias(ruta=tmp_path / "latencias.csv")
    for ms in (100.0, 200.0, 300.0):
        registro.anotar(Medicion(ms_total=ms))
    resumen = registro.resumen()
    assert resumen == {"alertas": 3, "ms_minimo": 100.0, "ms_mediana": 200.0,
                       "ms_media": 200.0, "ms_maximo": 300.0}


def test_resumen_sin_archivo_no_revienta(tmp_path):
    assert RegistroDeLatencias(ruta=tmp_path / "no-existe.csv").resumen() == {"alertas": 0}
