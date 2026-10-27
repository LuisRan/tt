"""Tablas estáticas (geografía pública, reglas RENAPO). Ningún dato personal."""
from __future__ import annotations

from typing import NamedTuple


class Entidad(NamedTuple):
    num: int        # clave INEGI 01..32 (clave de elector y campo "estado" de la INE)
    curp: str       # código de 2 letras en la CURP
    nombre: str     # mayúsculas sin acentos
    abrev: str      # abreviatura usada en el domicilio de la credencial


ENTIDADES: list[Entidad] = [
    Entidad(1, "AS", "AGUASCALIENTES", "AGS"),
    Entidad(2, "BC", "BAJA CALIFORNIA", "BC"),
    Entidad(3, "BS", "BAJA CALIFORNIA SUR", "BCS"),
    Entidad(4, "CC", "CAMPECHE", "CAMP"),
    Entidad(5, "CL", "COAHUILA", "COAH"),
    Entidad(6, "CM", "COLIMA", "COL"),
    Entidad(7, "CS", "CHIAPAS", "CHIS"),
    Entidad(8, "CH", "CHIHUAHUA", "CHIH"),
    Entidad(9, "DF", "CIUDAD DE MEXICO", "CDMX"),
    Entidad(10, "DG", "DURANGO", "DGO"),
    Entidad(11, "GT", "GUANAJUATO", "GTO"),
    Entidad(12, "GR", "GUERRERO", "GRO"),
    Entidad(13, "HG", "HIDALGO", "HGO"),
    Entidad(14, "JC", "JALISCO", "JAL"),
    Entidad(15, "MC", "MEXICO", "MEX"),
    Entidad(16, "MN", "MICHOACAN", "MICH"),
    Entidad(17, "MS", "MORELOS", "MOR"),
    Entidad(18, "NT", "NAYARIT", "NAY"),
    Entidad(19, "NL", "NUEVO LEON", "NL"),
    Entidad(20, "OC", "OAXACA", "OAX"),
    Entidad(21, "PL", "PUEBLA", "PUE"),
    Entidad(22, "QT", "QUERETARO", "QRO"),
    Entidad(23, "QR", "QUINTANA ROO", "QROO"),
    Entidad(24, "SP", "SAN LUIS POTOSI", "SLP"),
    Entidad(25, "SL", "SINALOA", "SIN"),
    Entidad(26, "SR", "SONORA", "SON"),
    Entidad(27, "TC", "TABASCO", "TAB"),
    Entidad(28, "TS", "TAMAULIPAS", "TAMPS"),
    Entidad(29, "TL", "TLAXCALA", "TLAX"),
    Entidad(30, "VZ", "VERACRUZ", "VER"),
    Entidad(31, "YN", "YUCATAN", "YUC"),
    Entidad(32, "ZS", "ZACATECAS", "ZAC"),
]
# Nacido en el extranjero (solo CURP; sin número INEGI).
NACIDO_EXTRANJERO = Entidad(0, "NE", "NACIDO EN EL EXTRANJERO", "NE")

BY_CURP = {e.curp: e for e in ENTIDADES + [NACIDO_EXTRANJERO]}
BY_NUM = {e.num: e for e in ENTIDADES}
BY_ABREV = {e.abrev: e for e in ENTIDADES}
BY_NOMBRE = {e.nombre: e for e in ENTIDADES}

# Diccionario oficial del dígito verificador CURP.
CURP_DICT = "0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ"

# Anexo 2 del instructivo RENAPO: palabras inconvenientes (2a letra -> X).
PALABRAS_INCONVENIENTES = frozenset(
    """BACA BAKA BUEI BUEY CACA CACO CAGA CAGO CAKA CAKO COGE COGI COJA COJE COJI
    COJO COLA CULO FALO FETO GETA GUEI GUEY JETA JOTO KACA KACO KAGA KAGO KAKA KAKO
    KOGE KOGI KOJA KOJE KOJI KOJO KOLA KULO LILO LOCA LOCO LOKA LOKO MAME MAMO MEAR
    MEAS MEON MIAR MION MOCO MOKO MULA MULO NACA NACO PEDA PEDO PENE PIPI PITO POPO
    PUTA PUTO QULO RATA ROBA ROBE ROBO RUIN SENO TETA VACA VAGA VAGO VAKA VUEI VUEY
    WUEI WUEY""".split()
)

# Preposiciones, conjunciones y contracciones que se ignoran en nombres/apellidos compuestos.
PARTICULAS = frozenset("DA DAS DE DEL DER DI DIE DD EL LA LOS LAS LE LES MAC MC VAN VON Y".split())

# Primer nombre que se salta cuando el nombre es compuesto (JOSÉ/MARÍA y abreviaturas).
NOMBRES_SALTABLES = frozenset("JOSE J MARIA MA M".split())

MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

# Domicilios en el exterior (INE variante extranjero). Solo geografía pública.
# (ciudad, estado/provincia, país, prefijo de CP | "CA" = formato canadiense)
CIUDADES_EXTRANJERO: list[tuple[str, str, str, str]] = [
    ("LOS ANGELES", "CA", "ESTADOS UNIDOS", "900"),
    ("SAN DIEGO", "CA", "ESTADOS UNIDOS", "921"),
    ("SAN JOSE", "CA", "ESTADOS UNIDOS", "951"),
    ("FRESNO", "CA", "ESTADOS UNIDOS", "937"),
    ("SACRAMENTO", "CA", "ESTADOS UNIDOS", "958"),
    ("HOUSTON", "TX", "ESTADOS UNIDOS", "770"),
    ("DALLAS", "TX", "ESTADOS UNIDOS", "752"),
    ("SAN ANTONIO", "TX", "ESTADOS UNIDOS", "782"),
    ("EL PASO", "TX", "ESTADOS UNIDOS", "799"),
    ("AUSTIN", "TX", "ESTADOS UNIDOS", "787"),
    ("CHICAGO", "IL", "ESTADOS UNIDOS", "606"),
    ("PHOENIX", "AZ", "ESTADOS UNIDOS", "850"),
    ("TUCSON", "AZ", "ESTADOS UNIDOS", "857"),
    ("DENVER", "CO", "ESTADOS UNIDOS", "802"),
    ("LAS VEGAS", "NV", "ESTADOS UNIDOS", "891"),
    ("ATLANTA", "GA", "ESTADOS UNIDOS", "303"),
    ("SEATTLE", "WA", "ESTADOS UNIDOS", "981"),
    ("MIAMI", "FL", "ESTADOS UNIDOS", "331"),
    ("NEW YORK", "NY", "ESTADOS UNIDOS", "100"),
    ("ALBUQUERQUE", "NM", "ESTADOS UNIDOS", "871"),
    ("TORONTO", "ON", "CANADA", "CA"),
    ("MONTREAL", "QC", "CANADA", "CA"),
    ("VANCOUVER", "BC", "CANADA", "CA"),
    ("MADRID", "MADRID", "ESPAÑA", "280"),
    ("BARCELONA", "BARCELONA", "ESPAÑA", "080"),
]
CALLES_EXTRANJERO = [
    "MAIN", "OAK", "PINE", "MAPLE", "CEDAR", "ELM", "WASHINGTON", "LINCOLN", "PARK",
    "LAKE", "HILL", "SUNSET", "MISSION", "VALLEY", "RIVER", "SPRING", "HIGHLAND",
    "FOREST", "MEADOW", "WILLOW", "CHERRY", "FRANKLIN", "JEFFERSON", "MADISON",
]
SUFIJOS_CALLE = ["ST", "AVE", "BLVD", "DR", "RD", "LN", "CT", "WAY"]

NACIONALIDADES_EXTRANJERAS = [
    "ESTADOUNIDENSE", "GUATEMALTECA", "COLOMBIANA", "ESPAÑOLA", "VENEZOLANA",
    "HONDUREÑA", "CUBANA", "ARGENTINA", "SALVADOREÑA",
]
