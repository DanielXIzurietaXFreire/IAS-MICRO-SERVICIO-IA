# -*- coding: utf-8 -*-
"""Deja la raiz del microservicio en el path para que 'import src...' funcione."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
