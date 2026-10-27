# -*- coding: utf-8 -*-
"""Pruebas de la configuracion: .env y zonas.json."""
import json
from datetime import time

from src import config as configuracion


def test_lee_el_env_y_las_zonas(tmp_path, monkeypatch):
    for clave in ("SUPABASE_URL", "BACKEND_URL", "BACKEND_EMAIL", "CONFIANZA_MINIMA",
                  "HORA_INICIO_LABORAL", "SUPABASE_SERVICE_KEY", "BACKEND_PASSWORD"):
        monkeypatch.delenv(clave, raising=False)

    env = tmp_path / ".env"
    env.write_text(
        "# comentario\n"
        "SUPABASE_URL=https://proyecto.supabase.co/\n"
        'SUPABASE_SERVICE_KEY="clave"\n'
        "BACKEND_URL=http://localhost:5014\n"
        "BACKEND_EMAIL=ia@sisa.fin.ec\n"
        "BACKEND_PASSWORD=secreta\n"
        "CONFIANZA_MINIMA=0.6\n"
        "HORA_INICIO_LABORAL=07:30\n", encoding="utf-8")
    zonas = tmp_path / "zonas.json"
    zonas.write_text(json.dumps({
        "zonas_restringidas": {"3": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]},
        "camaras": [{"id": 3, "nombre": "Caja", "stream": "rtsp://demo"}],
    }), encoding="utf-8")

    cfg = configuracion.cargar(env, zonas)
    assert cfg.supabase_url == "https://proyecto.supabase.co"   # sin barra final
    assert cfg.supabase_service_key == "clave"                  # sin comillas
    assert cfg.confianza_minima == 0.6
    assert cfg.hora_inicio_laboral == time(7, 30)
    assert cfg.hay_backend() and cfg.hay_storage()
    assert cfg.zonas_restringidas[3][0] == (0.1, 0.1)
    assert cfg.camaras_locales[0]["id"] == 3


def test_sin_archivos_usa_valores_por_defecto(tmp_path, monkeypatch):
    for clave in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "BACKEND_EMAIL", "BACKEND_PASSWORD",
                  "CONFIANZA_MINIMA", "HORA_INICIO_LABORAL", "BACKEND_URL", "MODELO"):
        monkeypatch.delenv(clave, raising=False)
    cfg = configuracion.cargar(tmp_path / "no-hay.env", tmp_path / "no-hay.json")
    assert cfg.backend_url == "http://localhost:5014"
    assert cfg.confianza_minima == 0.5
    assert cfg.hora_inicio_laboral == time(8, 0)
    assert cfg.hay_storage() is False
    assert cfg.clases[1] == "arma"


def test_prioriza_zonas_locales_sobre_plantilla(tmp_path, monkeypatch):
    plantilla = tmp_path / "zonas.json"
    local = tmp_path / "zonas.local.json"
    plantilla.write_text(json.dumps({"camaras": [{"id": 1, "stream": "rtsp://plantilla"}]}),
                         encoding="utf-8")
    local.write_text(json.dumps({"camaras": [{"id": 2, "stream": "rtsp://local"}]}),
                     encoding="utf-8")
    monkeypatch.setattr(configuracion, "RAIZ", tmp_path)

    zonas, camaras = configuracion.cargar_zonas()

    assert zonas == {}
    assert camaras == [{"id": 2, "stream": "rtsp://local"}]


def test_hora_mal_escrita_no_revienta(tmp_path, monkeypatch):
    monkeypatch.setenv("HORA_FIN_LABORAL", "no-es-una-hora")
    cfg = configuracion.cargar(tmp_path / "no-hay.env", tmp_path / "no-hay.json")
    assert cfg.hora_fin_laboral == time(18, 0)


def test_intervalo_de_revision_de_camaras(tmp_path, monkeypatch):
    monkeypatch.delenv("SEGUNDOS_ENTRE_REVISIONES_DE_CAMARAS", raising=False)
    assert configuracion.cargar(tmp_path / "no.env", tmp_path / "no.json").segundos_entre_revisiones_de_camaras == 60
    monkeypatch.setenv("SEGUNDOS_ENTRE_REVISIONES_DE_CAMARAS", "15")
    assert configuracion.cargar(tmp_path / "no.env", tmp_path / "no.json").segundos_entre_revisiones_de_camaras == 15


def test_la_espera_entre_alertas_se_lee_del_env(tmp_path, monkeypatch):
    """
    Con una espera de 60 s, una persona quieta delante de una camara genera una alerta por
    minuto y no cinco por segundo. Es un ajuste que el autor va a querer mover en la
    cooperativa, asi que tiene que salir del .env y no del codigo (14/10/2026).
    """
    monkeypatch.delenv("SEGUNDOS_DE_ESPERA_POR_ALERTA", raising=False)
    env = tmp_path / ".env"
    env.write_text("SEGUNDOS_DE_ESPERA_POR_ALERTA=180\n", encoding="utf-8")

    cfg = configuracion.cargar(env, tmp_path / "no-hay.json")

    assert cfg.segundos_de_espera_por_alerta == 180


def test_sin_ese_ajuste_la_espera_son_sesenta_segundos(tmp_path, monkeypatch):
    monkeypatch.delenv("SEGUNDOS_DE_ESPERA_POR_ALERTA", raising=False)

    cfg = configuracion.cargar(tmp_path / "no-hay.env", tmp_path / "no-hay.json")

    assert cfg.segundos_de_espera_por_alerta == 60


def test_la_espera_del_env_llega_al_control_de_repeticion(tmp_path, monkeypatch):
    """No basta con leerla: hay que comprobar que es la que se usa de verdad."""
    from datetime import datetime

    from src.rules import ControlDeRepeticion

    monkeypatch.delenv("SEGUNDOS_DE_ESPERA_POR_ALERTA", raising=False)
    env = tmp_path / ".env"
    env.write_text("SEGUNDOS_DE_ESPERA_POR_ALERTA=120\n", encoding="utf-8")
    cfg = configuracion.cargar(env, tmp_path / "no-hay.json")

    control = ControlDeRepeticion(cfg.segundos_de_espera_por_alerta)
    primera = datetime(2026, 10, 14, 23, 0, 0)

    assert control.permite(1, "arma", primera) is True
    assert control.permite(1, "arma", datetime(2026, 10, 14, 23, 1, 30)) is False  # a los 90 s
    assert control.permite(1, "arma", datetime(2026, 10, 14, 23, 2, 1)) is True    # a los 121 s
