"""API category modules — one per OpenAPI operation group of TDPAJE 1.5.1."""

from comptine.api.associer import Associer
from comptine.api.declarer import Declarer
from comptine.api.employeurs import Employeurs
from comptine.api.enfants import Enfants
from comptine.api.estimer import Estimer
from comptine.api.mandats import Mandats
from comptine.api.predeclarer import Predeclarer
from comptine.api.salaries import Salaries

__all__ = [
    "Associer",
    "Declarer",
    "Employeurs",
    "Enfants",
    "Estimer",
    "Mandats",
    "Predeclarer",
    "Salaries",
]
