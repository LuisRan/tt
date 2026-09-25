"""
infrastructure/regex/regex_heuristic_adapter.py
Extractor determinista para los 4 documentos soportados.
Diseñado para el formato multilinea que produce Docling (etiqueta en línea propia,
valor(es) en líneas siguientes).

Estructura real por documento (según imágenes oficiales):

INE:
  NOMBRE            → línea 1: primer apellido, línea 2: segundo apellido, línea 3: nombre(s)
  DOMICILIO         → línea 1: calle+número, línea 2: col+CP, línea 3: municipio+ciudad
  CLAVE DE ELECTOR  → inline o línea siguiente (18 chars)
  CURP              → inline
  AÑO DE REGISTRO   → inline  ej. "2016 01"
  ESTADO            → inline  ej. "09"
  MUNICIPIO         → inline  ej. "014"
  SECCIÓN           → inline  ej. "4373"
  LOCALIDAD         → inline  ej. "0001"
  EMISIÓN           → inline  ej. "2017"
  VIGENCIA          → inline  ej. "2027"
  FECHA DE NACIMIENTO → línea siguiente o inline  ej. "18/08/1976"
  SEXO              → inline  ej. "H"

ACTA DE NACIMIENTO:
  Folio de Impresión       → línea siguiente
  Identificador Electrónico → línea siguiente
  Número de Certificado de Nacimiento → línea siguiente
  Entidad de Registro      → línea siguiente
  Municipio de Registro    → línea siguiente
  Fecha de Registro        → línea siguiente
  Oficialía / Libro / Número de Acta → inline o línea siguiente
  Nombre(s) / Primer Apellido / Segundo Apellido → cada campo en su línea
  Sexo / Fecha de Nacimiento / Lugar de Nacimiento → inline o línea siguiente
  Datos padre: Nombre(s), Primer Apellido, Segundo Apellido, Nacionalidad, CURP
  Datos madre: igual

PASAPORTE:
  Pasaporte No.     → inline  ej. "E1234567"
  Tipo              → inline  "P"
  País expedición   → inline  "MEX"
  Apellidos         → línea siguiente
  Nombres           → línea siguiente
  Nacionalidad      → línea siguiente
  CURP              → inline
  Fecha nacimiento  → inline  ej. "20 11 1997"
  Sexo              → inline  "F"/"M"
  Lugar nacimiento  → inline  ej. "IRAPUATO, GTO., MEX"
  Fecha expedición  → línea siguiente
  Fecha caducidad   → línea siguiente
  MRZ línea 1 y 2

CURP:
  Clave             → línea siguiente (18 chars)
  Nombre            → línea siguiente (nombre completo)
  Fecha inscripción → inline
  Folio             → inline
  Entidad registro  → inline
  CURPs asociadas   → línea siguiente (si existen)
  Código verificación → línea siguiente
"""
from __future__ import annotations
import logging
import re
from typing import Optional

from domain.entities.documento import TipoDocumento
from domain.ports.interfaces import IFieldExtractorPort
from infrastructure.regex.correccion_ocr import (
    corregir_clave_elector,
    corregir_curp,
    curp_valida,
    es_etiqueta_ruidosa,
    limpiar_linea_nombre,
    normalizar_curp,
    similitud_etiqueta,
)

logger = logging.getLogger(__name__)


# ── Patrones globales ─────────────────────────────────────────────────────────

PATRON_CURP = re.compile(
    r"\b([A-Z]{4}\d{6}[HM][A-Z]{2}[BCDFGHJKLMNÑPQRSTVWXYZ]{3}[A-Z0-9]{1,2}\d?)\b"
)
PATRON_FECHA_SLASH = re.compile(r"\b(\d{1,2}[/]\d{1,2}[/]\d{2,4})\b")
PATRON_FECHA_ESPACIO = re.compile(r"\b(\d{1,2}\s+\d{1,2}\s+\d{4})\b")
PATRON_FECHA_CUALQUIERA = re.compile(r"\b(\d{1,2}[/\-\s]\d{1,2}[/\-\s]\d{2,4})\b")
PATRON_CLAVE_ELECTOR = re.compile(r"\b([A-Z]{6}\d{8}[A-Z]\d{3})\b")
PATRON_NUM_PASAPORTE = re.compile(r"\b([A-Z]\d{8})\b")
PATRON_MRZ = re.compile(r"[A-Z0-9<]{20,}")

# Líneas que son encabezado/ruido de Docling
_RUIDO = re.compile(
    r"^(?:<!--.*-->|MÉXICO|MEXICO|ESTADOS UNIDOS MEXICANOS|"
    r"INSTITUTO NACIONAL ELECTORAL|CREDENCIAL PARA VOTAR|"
    r"SECRETAR[IÍ]A DE RELACIONES EXTERIORES|SECRETAR[IÍ]A DE GOBERNACI[ÓO]N|"
    r"REGISTRO CIVIL|ACTA DE NACIMIENTO|PASAPORTE(?: MEXICANO)?|"
    r"CONSTANCIA DE LA CLAVE [ÚU]NICA|CLAVE [ÚU]NICA DE REGISTRO|"
    r"SOY M[EÉ]XICO|SEGOB|INE|RENAPO|PRESENTE|TRAMITE GRATUITO|"
    r"DIRECTORA? GENERAL.*|FIRMA ELECTR[ÓO]NICA.*|TIPO\.?|TYPE)$",
    re.I,
)

# Etiquetas que indican que comienza otro campo (para detener lectura de valor)
_ETIQUETAS_CONOCIDAS = [
    r"^NOMBRE[S]?$", r"^DOMICIL[IO]+$", r"^FECHA DE NACIMIENTO$",
    r"^FECHA DE EXPEDICI[ÓO]N$", r"^FECHA DE VENCIMIENTO$", r"^FECHA DE CADUCIDAD$",
    r"^CLAVE DE ELECTOR$", r"^CURP$", r"^SECCI[ÓO]N$", r"^MUNICIPIO$",
    r"^ESTADO$", r"^LOCALIDAD$", r"^VIGENCIA$", r"^EMISI[ÓO]N$",
    r"^A[ÑN]O DE REGISTRO$", r"^SEXO$", r"^APELLIDOS?$", r"^NOMBRES?$",
    r"^NACIONALIDAD$", r"^LUGAR DE NACIMIENTO$", r"^PASAPORTE\s+NO\.?$",
    r"^FOLIO DE IMPRESI[ÓO]N$", r"^IDENTIFICADOR ELECTR[ÓO]NICO$",
    r"^N[ÚU]MERO DE CERTIFICADO$", r"^ENTIDAD DE REGISTRO$",
    r"^MUNICIPIO DE REGISTRO$", r"^FECHA DE REGISTRO$", r"^OFICIAL[IÍ]A$",
    r"^LIBRO$", r"^N[ÚU]MERO DE ACTA$", r"^DATOS? DE FILIACI[ÓO]N$",
    r"^CLAVE[:\s]", r"^NOMBRE[:\s]", r"^FOLIO[:\s]", r"^FECHA DE INSCRIPCI[ÓO]N$",
    r"^ENTIDAD DE REGISTRO$",
]


def _lineas(texto: str) -> list[str]:
    """
    Limpia y devuelve lineas no vacias, eliminando ruido conocido.
    Tambien limpia prefijos de markdown (## ) que Docling agrega a headers.
    """
    resultado = []
    for l in texto.splitlines():
        # Quitar prefijo markdown header: "## TEXTO" -> "TEXTO"
        l = re.sub(r"^#{1,6}\s+", "", l).strip()
        if not l:
            continue
        if _RUIDO.match(l):
            continue
        resultado.append(l)
    return resultado


def _es_etiqueta(linea: str) -> bool:
    return any(re.match(p, linea.upper()) for p in _ETIQUETAS_CONOCIDAS)


def _normalizar_fecha(f: str) -> str:
    """dd mm yyyy o dd-mm-yyyy → dd/mm/yyyy"""
    return re.sub(r"[\s\-]", "/", f.strip())


def _primera_fecha_en(texto: str) -> Optional[str]:
    m = PATRON_FECHA_SLASH.search(texto)
    if m:
        return m.group(1)
    m = PATRON_FECHA_ESPACIO.search(texto)
    if m:
        return _normalizar_fecha(m.group(1))
    return None


def _valor_tras_etiqueta(
    lineas: list[str],
    patrones: list[str],
    max_lineas: int = 1,
) -> list[str]:
    """
    Busca la primera línea que coincida con algún patrón.
    Soporta:
      - Etiqueta en línea propia  → retorna las siguientes max_lineas líneas
      - Etiqueta con valor inline → retorna el valor de la misma línea
    """
    for i, linea in enumerate(lineas):
        for p in patrones:
            m = re.match(r"^(?:" + p + r")\s*[:\-]?\s*(.*)$", linea, re.I | re.U)
            if not m:
                continue
            valor_inline = m.group(1).strip() if m.lastindex else ""
            if valor_inline:
                return [valor_inline]
            # Etiqueta sola → leer líneas siguientes
            resultado = []
            for j in range(i + 1, min(i + 1 + max_lineas, len(lineas))):
                sig = lineas[j].strip()
                if _es_etiqueta(sig):
                    break
                if sig:
                    resultado.append(sig)
            return resultado if resultado else []
    return []


def _sexo_desde_curp(curp: Optional[str]) -> Optional[str]:
    if curp and len(curp) >= 11:
        c = curp[10]
        return c if c in ("H", "M") else None
    return None


def _sexo_texto(texto: str) -> Optional[str]:
    m = re.search(
        r"\bSEXO\s*[:\-]?\s*([HMF])\b|"
        r"\b(MASCULINO|HOMBRE)\b|"
        r"\b(FEMENINO|MUJER)\b",
        texto, re.I
    )
    if not m:
        return None
    if m.group(1):
        v = m.group(1).upper()
        return "M" if v == "F" else v
    if m.group(2):
        return "H"
    if m.group(3):
        return "M"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# INE
# ─────────────────────────────────────────────────────────────────────────────

_RE_ETIQUETA_INE = re.compile(
    r"^(FECHA|SEXO|CURP|CLAVE|SECCI|ESTADO|MUNICIPIO|LOCALIDAD|EMISI|VIGENCIA|A[ÑN]O\s+DE|FOLIO|DOMICIL)",
    re.I,
)


def _extraer_ine(texto: str) -> dict:
    """
    Extractor de la INE robusto a OCR ruidoso (fotos/escaneos):
      - etiquetas buscadas de forma difusa (el OCR lee 'estaco', 'uceumo'...)
      - nombre/apellidos limpiando lo que se cuela de la columna derecha
      - CURP y clave de elector corregidas por validación cruzada
        (dígito verificador + consistencia con nombre, fecha y sexo)
      - año de emisión/vigencia por la regla vigencia = emisión + 10
    """
    ls = _lineas(texto)
    corregidos: list[str] = []

    # ── Nombre en 3 líneas: primer apellido, segundo apellido, nombre(s) ─────
    nombre_lineas: list[str] = []
    for i, linea in enumerate(ls):
        primera = linea.split()[0] if linea.split() else ""
        if similitud_etiqueta(primera, "NOMBRE") >= 0.7 or re.match(r"^NOMBRES?\b", linea, re.I):
            resto = linea[len(primera):].strip()
            candidatas = ([resto] if resto else []) + ls[i + 1:i + 6]
            for c in candidatas:
                if _RE_ETIQUETA_INE.match(c) and (":" in c or re.match(r"^DOMICIL", c, re.I)):
                    break
                if similitud_etiqueta(c.split()[0] if c.split() else "", "DOMICILIO") >= 0.7:
                    break
                limpio = limpiar_linea_nombre(c)
                if limpio:
                    nombre_lineas.append(limpio)
                if len(nombre_lineas) == 3:
                    break
            break

    if len(nombre_lineas) < 2:
        # El OCR a veces pierde la etiqueta NOMBRE: el bloque de nombre son las
        # (hasta) 3 líneas inmediatamente anteriores a DOMICILIO.
        for i, linea in enumerate(ls):
            primera = linea.split()[0] if linea.split() else ""
            if re.match(r"^DOMICIL", linea, re.I) or similitud_etiqueta(primera, "DOMICILIO") >= 0.7:
                previas: list[str] = []
                for c in reversed(ls[max(0, i - 6):i]):
                    limpio = limpiar_linea_nombre(c)
                    if limpio is None:
                        if previas and es_etiqueta_ruidosa(c):
                            break
                        continue
                    previas.insert(0, limpio)
                    if len(previas) == 3:
                        break
                if len(previas) >= 2:
                    nombre_lineas = previas
                break

    ap_paterno = ap_materno = nombres_pila = nombre_completo = None
    if len(nombre_lineas) >= 3:
        ap_paterno, ap_materno, nombres_pila = nombre_lineas[:3]
    elif len(nombre_lineas) == 2:
        ap_paterno, nombres_pila = nombre_lineas
    elif len(nombre_lineas) == 1:
        nombre_completo = nombre_lineas[0]

    # ── Domicilio en hasta 3 líneas ───────────────────────────────────────────
    dom_lineas: list[str] = []
    for i, linea in enumerate(ls):
        primera = linea.split()[0] if linea.split() else ""
        if re.match(r"^DOMICIL", linea, re.I) or similitud_etiqueta(primera, "DOMICILIO") >= 0.7:
            inline = re.sub(r"^\S+\s*[:\-]?\s*", "", linea).strip()
            siguientes = ls[i + 1:i + 4]
            if len(re.sub(r"[^A-ZÁÉÍÓÚÑ0-9]", "", inline)) >= 6:
                siguientes = [inline] + siguientes[:2]
            for c in siguientes:
                if re.search(r"CLAVE|ELECTOR|CURP", c, re.I) or similitud_etiqueta(c.split()[0], "CLAVE") >= 0.7:
                    break
                if _RE_ETIQUETA_INE.match(c) and c is not inline:
                    break
                if len(re.sub(r"[^A-ZÁÉÍÓÚÑ0-9]", "", c)) >= 4:
                    dom_lineas.append(re.sub(r"\s+[+|*]+$", "", c).strip())
            break
    dom_calle = dom_lineas[0] if len(dom_lineas) >= 1 else None
    dom_col = dom_lineas[1] if len(dom_lineas) >= 2 else None
    dom_mun = dom_lineas[2] if len(dom_lineas) >= 3 else None
    domicilio = ", ".join(dom_lineas) or None

    # ── Fecha de nacimiento y sexo ───────────────────────────────────────────
    fecha_nac_m = re.search(
        r"FECHA\s+DE\s+NACIMIENTO[\s\S]{0,60}?(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})", texto, re.I
    ) or re.search(r"\b(\d{2}/\d{2}/(?:19|20)\d{2})\b", texto)
    fecha_nacimiento = _normalizar_fecha(fecha_nac_m.group(1)) if fecha_nac_m else None
    sexo = _sexo_texto(texto)
    if not sexo:
        m = re.search(r"^\s*[a-zA-Z]{2,5}\s+([HM])\s*$", texto, re.M)  # 'SEXO H' mal leído: 'wo H'
        sexo = m.group(1) if m else None

    # ── CURP (validación cruzada) ────────────────────────────────────────────
    curp_m = PATRON_CURP.search(texto)
    curp_leida = normalizar_curp(curp_m.group(1)) if curp_m else None
    curp = curp_leida if curp_valida(curp_leida) else None
    if curp and curp != curp_m.group(1):
        corregidos.append("curp")  # p. ej. 'LO8' -> 'L08' (homoclave numérica)
    if not curp:
        curp, corr = corregir_curp(texto, nombres_pila, ap_paterno, ap_materno, fecha_nacimiento, sexo)
        if curp and corr:
            corregidos.append("curp")
        # Sin corrección confiable, se conserva la lectura literal para que el
        # usuario la revise (la validación de formato la marcará si es inválida)
        curp = curp or curp_leida
    if curp:
        # Las iniciales de la CURP confirman el orden de los apellidos
        if ap_paterno and ap_materno and ap_paterno[0] == curp[2] and ap_materno[0] == curp[0]:
            ap_paterno, ap_materno = ap_materno, ap_paterno
        if not sexo:
            sexo = _sexo_desde_curp(curp)
        # Fecha de la CURP (validada por dígito verificador) si el OCR no la leyó
        if not fecha_nacimiento and curp[4:10].isdigit():
            fecha_nacimiento = f"{curp[8:10]}/{curp[6:8]}/{'19' if int(curp[4:6]) > 30 else '20'}{curp[4:6]}"
    if ap_paterno and nombres_pila:
        nombre_completo = " ".join(x for x in (nombres_pila, ap_paterno, ap_materno) if x)

    # ── Clave de elector (validación cruzada) ────────────────────────────────
    clave_m = PATRON_CLAVE_ELECTOR.search(texto)
    clave_elector = clave_m.group(1) if clave_m else None
    if not clave_elector:
        clave_elector, corr = corregir_clave_elector(
            texto, nombres_pila, ap_paterno, ap_materno, fecha_nacimiento, sexo, curp
        )
        if clave_elector and corr:
            corregidos.append("clave_elector")

    # ── Año de registro ("2016 01") ─────────────────────────────────────────
    reg_m = re.search(r"A[ÑN]O\s+DE\s+REGISTRO\s+(\d{4}\s*\d{2})", texto, re.I) \
        or re.search(r"\b((?:19|20)\d{2})\s+(0\d|1[0-2])\b", texto)
    año_registro = None
    if reg_m:
        año_registro = re.sub(r"\s+", " ", " ".join(g for g in reg_m.groups() if g)).strip()
        if re.fullmatch(r"\d{6}", año_registro.replace(" ", "")):
            año_registro = año_registro.replace(" ", "")[:4] + " " + año_registro.replace(" ", "")[4:]

    # ── Estado / Municipio / Sección (misma línea: 2, 3 y 4 dígitos) ─────────
    estado = municipio = seccion = localidad = None
    for linea in ls:
        m = re.search(r"\b(\d{2})\b\D{0,25}\b(\d{3})\b\D{0,25}\b(\d{4})\b", linea)
        if m and re.search(r"SECC|MUNIC|ESTAD|ESTAC", linea, re.I):
            estado, municipio, seccion = m.groups()
            break
    estado = estado or _grupo(re.search(r"\bESTADO\s+(\d{2})\b", texto, re.I))
    municipio = municipio or _grupo(re.search(r"\bMUNICIPIO\s+(\d{3})\b", texto, re.I))
    seccion = seccion or _grupo(re.search(r"\bSECCI[ÓO]N\s+(\d{4})\b", texto, re.I))

    # ── Localidad / Emisión / Vigencia (última línea de la credencial) ───────
    año_emision = _grupo(re.search(r"\bEMISI[ÓO]N\s+(\d{4})\b", texto, re.I))
    año_vigencia = _grupo(re.search(r"\bVIGENCIA\s+(\d{4})\b", texto, re.I))
    localidad = _grupo(re.search(r"\bLOCALIDAD\s+(\d{4})\b", texto, re.I))
    for linea in ls:
        m = re.search(r"\b(\d{4})\b\D{0,25}\b((?:19|20)\d{2})\b\D{0,25}\b((?:20)\d{2})\b", linea)
        if m and int(m.group(3)) - int(m.group(2)) == 10:
            localidad = localidad or m.group(1)
            año_emision = año_emision or m.group(2)
            año_vigencia = año_vigencia or m.group(3)
            break
    if not (año_emision and año_vigencia):
        # La credencial vigente dura 10 años: buscar el par (a, a+10)
        años = [int(a) for a in re.findall(r"\b((?:19|20)\d{2})\b", texto)]
        for a in años:
            if a + 10 in años and 2000 <= a <= 2100:
                año_emision = año_emision or str(a)
                año_vigencia = año_vigencia or str(a + 10)
                break

    return {
        "nombre_completo":           nombre_completo,
        "primer_apellido":           ap_paterno,
        "segundo_apellido":          ap_materno,
        "nombres":                   nombres_pila,
        "curp":                      curp,
        "fecha_nacimiento":          fecha_nacimiento,
        "sexo":                      sexo,
        "clave_elector":             clave_elector,
        "domicilio_calle":           dom_calle,
        "domicilio_colonia":         dom_col,
        "domicilio_municipio_ciudad": dom_mun,
        "domicilio":                 domicilio,
        "estado":                    estado,
        "municipio":                 municipio,
        "seccion":                   seccion,
        "localidad":                 localidad,
        "año_registro":              año_registro,
        "año_emision":               año_emision,
        "año_vigencia":              año_vigencia,
        "_corregidos":               corregidos,
    }


def _grupo(m) -> Optional[str]:
    return m.group(1) if m else None


# ─────────────────────────────────────────────────────────────────────────────
# ACTA DE NACIMIENTO
# ─────────────────────────────────────────────────────────────────────────────

def _extraer_acta(texto: str) -> dict:
    ls = _lineas(texto)

    # Identificadores del acta
    folio_imp = _valor_tras_etiqueta(ls, [r"FOLIO\s+DE\s+IMPRESI[ÓO]N"])
    id_elec   = _valor_tras_etiqueta(ls, [r"IDENTIFICADOR\s+ELECTR[ÓO]NICO"])
    cert_nac  = _valor_tras_etiqueta(ls, [r"N[ÚU]MERO\s+DE\s+CERTIFICADO(?:\s+DE\s+NACIMIENTO)?"])
    num_acta  = _valor_tras_etiqueta(ls, [r"N[ÚU]MERO\s+DE\s+ACTA"])

    # Registro
    entidad_reg  = _valor_tras_etiqueta(ls, [r"ENTIDAD\s+DE\s+REGISTRO"])
    mun_reg      = _valor_tras_etiqueta(ls, [r"MUNICIPIO\s+DE\s+REGISTRO"])
    fecha_reg    = _valor_tras_etiqueta(ls, [r"FECHA\s+DE\s+REGISTRO"])
    oficialia    = _valor_tras_etiqueta(ls, [r"OFICIAL[IÍ]A"])
    libro        = _valor_tras_etiqueta(ls, [r"LIBRO"])

    # Registrado
    curp_m    = PATRON_CURP.search(texto)
    nombres_r = _valor_tras_etiqueta(ls, [r"NOMBRE[S]?"])
    ap1_r     = _valor_tras_etiqueta(ls, [r"PRIMER\s+APELLIDO"])
    ap2_r     = _valor_tras_etiqueta(ls, [r"SEGUNDO\s+APELLIDO"])
    sexo_v    = _valor_tras_etiqueta(ls, [r"SEXO"])
    fec_nac   = _valor_tras_etiqueta(ls, [r"FECHA\s+DE\s+NACIMIENTO"])
    lugar_nac = _valor_tras_etiqueta(ls, [r"LUGAR\s+DE\s+NACIMIENTO"])

    nombres_str  = nombres_r[0] if nombres_r else None
    ap1_str      = ap1_r[0] if ap1_r else None
    ap2_str      = ap2_r[0] if ap2_r else None
    nombre_completo = " ".join(filter(None, [nombres_str, ap1_str, ap2_str])) or None

    fecha_nac_str = None
    if fec_nac:
        fecha_nac_str = _primera_fecha_en(fec_nac[0]) or fec_nac[0]
    if not fecha_nac_str:
        m = re.search(r"FECHA\s+DE\s+NACIMIENTO[\s\S]{0,40}?(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})", texto, re.I)
        if m:
            fecha_nac_str = _normalizar_fecha(m.group(1))

    curp = curp_m.group(1) if curp_m else None
    sexo = (_sexo_texto(sexo_v[0]) if sexo_v else None) or _sexo_texto(texto) or _sexo_desde_curp(curp)

    # Filiacion padre/madre - el orden de Docling es ambiguo en actas.
    # En el formato unico nacional, el orden es: PADRE primero, MADRE despues.
    # Pero Docling puede mezclarlas. Dejamos que el LLM extraiga estos campos
    # con su comprension del contexto (CURP empezando con H/M indica genero).
    # Solo extraemos los CURPs por patron - el LLM los asociara correctamente.
    todos_curps = PATRON_CURP.findall(texto)
    # No asignamos orden aqui - se queda en None para que el LLM los maneje
    # con base en el contexto correcto.

    return {
        "nombre_completo":           nombre_completo,
        "primer_apellido":           ap1_str,
        "segundo_apellido":          ap2_str,
        "nombres":                   nombres_str,
        "curp":                      curp,
        "fecha_nacimiento":          fecha_nac_str,
        "sexo":                      sexo,
        "folio_impresion":           folio_imp[0] if folio_imp else None,
        "identificador_electronico": id_elec[0] if id_elec else None,
        "numero_certificado_nac":    cert_nac[0] if cert_nac else None,
        "numero_acta":               num_acta[0] if num_acta else None,
        "entidad_registro":          entidad_reg[0] if entidad_reg else None,
        "municipio_registro":        mun_reg[0] if mun_reg else None,
        "fecha_registro":            fecha_reg[0] if fecha_reg else None,
        "oficialia":                 oficialia[0] if oficialia else None,
        "libro":                     libro[0] if libro else None,
        "lugar_nacimiento":          lugar_nac[0] if lugar_nac else None,
        # Filiacion: se deja al LLM
        "nombre_padre":              None,
        "curp_padre":                None,
        "nombre_madre":              None,
        "curp_madre":                None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PASAPORTE
# ─────────────────────────────────────────────────────────────────────────────

def _extraer_pasaporte(texto: str) -> dict:
    """
    Extractor robusto para pasaportes mexicanos.
    El OCR de pasaportes suele venir muy sucio por el layout multi-columna
    y el texto en 3 idiomas (español/ingles/frances). Estrategia:
    - Identificar valores por patron, no por etiqueta
    - Filtrar lineas con ruido OCR conocido
    """
    ls = _lineas(texto)

    # ── Numero de pasaporte (letra + 8 digitos) ───────────────────────────────
    num_m = PATRON_NUM_PASAPORTE.search(texto)
    numero_pasaporte = num_m.group(1) if num_m else None

    # ── CURP ──────────────────────────────────────────────────────────────────
    curp_m = PATRON_CURP.search(texto)
    curp   = curp_m.group(1) if curp_m else None

    # ── Tipo y pais ───────────────────────────────────────────────────────────
    tipo_pas = "P" if re.search(r"\bP\b", texto) else None
    pais_exp = "MEX" if re.search(r"\bMEX\b", texto) else None

    # ── Nacionalidad ──────────────────────────────────────────────────────────
    nac_m = re.search(r"\b(MEXICANA|MEXICANO)\b", texto)
    nacionalidad = nac_m.group(1) if nac_m else None

    # ── Apellidos y nombres ───────────────────────────────────────────────────
    # Estrategia: buscar lineas que sean NOMBRES PROPIOS validos
    # (solo letras mayusculas + espacios, 2-5 palabras, sin numeros ni "/")
    # y descartar las que contienen ruido OCR conocido
    RUIDO_OCR = {
        "AGORIE", "SUMAME", "SURNAME", "NOM", "GIVEN", "RANE", "PEGA", "DIERTE",
        "NALORALO", "NATONALIS", "NATIONALITY", "OAS", "BUTY", "DERE", "NASSGOCE",
        "CODE", "SAS", "SEA", "PAD", "ESE", "DETER", "DETE", "DILOMENO",
        "APORT", "HEN", "PASAPORTE", "PASSPORT", "MEXICANOS", "ESTADOS",
        "OBSERVACIONES", "REMETA", "OSSERVALICOS", "AUMONTY", "HUSOME", "AUTORIDAD",
        "CLAVE", "EXPEDICION", "EXPEDICIÓN", "ISSCING", "STATE", "CEDE",
        "UNIDOS", "SUMARNE", "OMS", "BIRTH", "FECHA", "NACIMIENTO", "CADUCIDAD",
        "EXPIRATION", "CURP", "REMARKS", "OBSERVATIONS", "SEXO",
    }

    def es_nombre_valido(linea: str) -> bool:
        # Solo letras mayusculas, espacios y acentos
        if not re.fullmatch(r"[A-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑ]+)*", linea):
            return False
        palabras = linea.split()
        if not (1 <= len(palabras) <= 5):
            return False
        # Si CUALQUIER palabra es ruido OCR -> descartar
        if any(p in RUIDO_OCR for p in palabras):
            return False
        # Cada palabra debe tener al menos 3 letras (filtrar "F", "M", "P")
        if any(len(p) < 3 for p in palabras):
            return False
        return True

    nombres_candidatos = [l for l in ls if es_nombre_valido(l)]

    # En el OCR del pasaporte real:
    #   primer candidato valido -> apellidos (CEJUDO AYALA)
    #   segundo candidato valido -> nombres (ENIO FERNANDO)
    apellidos = nombres_candidatos[0] if len(nombres_candidatos) > 0 else None
    nombres_p = nombres_candidatos[1] if len(nombres_candidatos) > 1 else None
    nombre_completo = f"{nombres_p} {apellidos}" if nombres_p and apellidos else (apellidos or nombres_p)

    # ── Fechas (formato "dd mm yyyy" o "dd/mm/yyyy") ──────────────────────────
    # El OCR de pasaporte produce texto fragmentado. Buscamos todas las fechas
    # posibles en TODO el texto (no solo en líneas enteras) y luego las asignamos.
    PATRON_FECHA_PAS = re.compile(r"\b(\d{1,2})\s+(\d{1,2})\s+(\d{4})\b|\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b")

    def _parsear_fecha_m(fm) -> str:
        if fm.group(1):
            return f"{fm.group(1).zfill(2)}/{fm.group(2).zfill(2)}/{fm.group(3)}"
        return f"{fm.group(4).zfill(2)}/{fm.group(5).zfill(2)}/{fm.group(6)}"

    fechas_encontradas = [_parsear_fecha_m(fm) for fm in PATRON_FECHA_PAS.finditer(texto)]

    # Fecha nacimiento: buscar por etiqueta con ventana amplia (200 chars),
    # aceptar "dd mm yyyy" o "dd/mm/yyyy"
    fecha_nac = None
    m = re.search(
        r"FECHA\s+DE\s+NACIMIENTO[\s\S]{0,200}?(\d{1,2}[\s/\-]\d{1,2}[\s/\-]\d{4})",
        texto, re.I
    )
    if m:
        fecha_nac = _normalizar_fecha(m.group(1))
    elif fechas_encontradas:
        fecha_nac = fechas_encontradas[0]

    # Fecha expedicion: buscar por etiqueta con ventana amplia
    fecha_exp = None
    m = re.search(
        r"FECHA\s+DE\s+EXPEDICI[ÓO]N[\s\S]{0,200}?(\d{1,2}[\s/\-]\d{1,2}[\s/\-]\d{4})",
        texto, re.I
    )
    if m:
        fecha_exp = _normalizar_fecha(m.group(1))
    elif len(fechas_encontradas) > 1:
        fecha_exp = fechas_encontradas[1]

    # Fecha caducidad: buscar por etiqueta con ventana amplia
    fecha_cad = None
    m = re.search(
        r"FECHA\s+DE\s+(?:CADUCIDAD|VENCIMIENTO|EXPIR\w*)[\s\S]{0,200}?(\d{1,2}[\s/\-]\d{1,2}[\s/\-]\d{4})",
        texto, re.I
    )
    if m:
        fecha_cad = _normalizar_fecha(m.group(1))
    elif len(fechas_encontradas) > 2:
        fecha_cad = fechas_encontradas[2]

    # Fallback posicional: si faltan expedicion/caducidad pero tenemos fechas,
    # asignar por posicion (el pasaporte mexicano siempre tiene: nac, exp, cad en ese orden)
    if fecha_exp is None and len(fechas_encontradas) >= 2:
        restantes = [f for f in fechas_encontradas if f != fecha_nac]
        if restantes:
            fecha_exp = restantes[0]
        if fecha_cad is None and len(restantes) > 1:
            fecha_cad = restantes[1]

    # Recuperar fecha_expedicion cuando el OCR la truncó (tiene "dd mm" sin año)
    # cerca de la etiqueta. Si tenemos fecha_caducidad, inferimos:
    # exp = cad - 3 años (vigencia estándar pasaporte mexicano 3 o 10 años)
    if fecha_exp is None and fecha_cad is not None:
        m_parcial = re.search(
            r"FECHA\s+DE\s+EXPEDICI[ÓO]N[\s\S]{0,300}?(\d{1,2})\s+(\d{1,2})(?!\s*\d)",
            texto, re.I
        )
        if m_parcial:
            dd_exp = m_parcial.group(1).zfill(2)
            mm_exp = m_parcial.group(2).zfill(2)
            # Inferir año desde caducidad menos 3 años
            try:
                partes_cad = fecha_cad.split("/")
                año_cad = int(partes_cad[2])
                año_exp = año_cad - 3
                fecha_exp_candidata = f"{dd_exp}/{mm_exp}/{año_exp}"
                logger.info("fecha_expedicion_inferida_de_caducidad", extra={
                    "candidata": fecha_exp_candidata, "caducidad": fecha_cad
                })
                fecha_exp = fecha_exp_candidata
            except Exception:
                pass

    # ── Sexo ──────────────────────────────────────────────────────────────────
    # En pasaporte mexicano: F = Femenino (mujer), M = Masculino (hombre)
    # En nuestro JSON: M = mujer, H = hombre
    sexo = None
    m = re.search(r"\bSEXO[/\s\w]{0,30}?\b([FM])\b", texto, re.I)
    if m:
        v = m.group(1).upper()
        sexo = "M" if v == "F" else "H"  # F (femenino) -> M, M (masculino) -> H
    if not sexo and curp:
        sexo = _sexo_desde_curp(curp)

    # ── Lugar de nacimiento ───────────────────────────────────────────────────
    # Suele ser "CIUDAD, EDO., MEX" o "MEXICO, D.F."
    # Debe contener una coma (o punto seguido de letra) para distinguir de nacionalidad
    lugar_nac = None
    for l in ls:
        # Excluir nacionalidades comunes
        if l.upper() in ("MEXICANA", "MEXICANO"):
            continue
        # Patron: "PALABRA, ABREV." o "PALABRA, EDO., MEX"
        if re.fullmatch(r"[A-ZÁÉÍÓÚÑ]{4,}\s*,\s*[A-Z\.]{2,8}\.?(?:\s*,\s*MEX)?", l):
            lugar_nac = l.strip()
            break
        if re.fullmatch(r"[A-ZÁÉÍÓÚÑ\s]{6,}\s*,\s*[A-Z\.]{2,8}\.?,\s*MEX$", l):
            lugar_nac = l.strip()
            break

    # ── MRZ (Machine Readable Zone) ───────────────────────────────────────────
    # Lineas largas con muchos < o que empiezan con P<MEX
    mrz_lines = []
    for l in ls:
        if re.match(r"^P<[A-Z]{3}", l):  # primera linea MRZ
            mrz_lines.append(l)
        elif re.match(r"^[A-Z0-9]{9,}<", l):  # segunda linea MRZ
            mrz_lines.append(l)
        elif l.count("<") >= 5 and len(l) >= 30:
            mrz_lines.append(l)

    # ── Respaldo MRZ para fechas faltantes ───────────────────────────────────
    # MRZ línea 2 formato: PASSNUM<CHECK NACIONDD BIRTHCHECK SEX EXPIRCHECK...
    # Posiciones (0-based): 0-8 num pasaporte, 13-18 fecha nac (AAMMDD), 19 check,
    # 20 sexo, 21-26 fecha exp (AAMMDD), 27 check
    def _fecha_desde_mrz2(mrz2: Optional[str], start: int, end: int,
                          futura: bool = False) -> Optional[str]:
        if not mrz2 or len(mrz2) < end:
            return None
        s = mrz2[start:end].replace("<", "").strip()
        if len(s) == 6 and s.isdigit():
            aa, mm, dd = s[0:2], s[2:4], s[4:6]
            if not (1 <= int(mm) <= 12 and 1 <= int(dd) <= 31):
                return None
            # La caducidad siempre es de este siglo; el nacimiento puede ser 19xx
            anio = f"20{aa}" if futura or int(aa) <= 25 else f"19{aa}"
            return f"{dd}/{mm}/{anio}"
        return None

    mrz2 = mrz_lines[1] if len(mrz_lines) > 1 else (mrz_lines[0] if mrz_lines else None)
    # Solo usar MRZ si la línea tiene el patrón de segunda línea MRZ (no P<MEX)
    if mrz2 and mrz2.startswith("P<"):
        mrz2 = None  # es línea 1, no línea 2

    if mrz2:
        if not fecha_nac:
            fecha_nac = _fecha_desde_mrz2(mrz2, 13, 19)
        cad_mrz = _fecha_desde_mrz2(mrz2, 21, 27, futura=True)
        # Si la "caducidad" leida es igual a la expedicion, el OCR repitio la
        # misma fecha: la MRZ es mas confiable.
        if cad_mrz and (not fecha_cad or fecha_cad == fecha_exp):
            fecha_cad = cad_mrz

    return {
        "nombre_completo":  nombre_completo,
        "primer_apellido":  apellidos,
        "nombres":          nombres_p,
        "curp":             curp,
        "fecha_nacimiento": fecha_nac,
        "sexo":             sexo,
        "numero_pasaporte": numero_pasaporte,
        "tipo_pasaporte":   tipo_pas,
        "pais_expedicion":  pais_exp,
        "nacionalidad":     nacionalidad,
        "lugar_nacimiento": lugar_nac,
        "fecha_expedicion": fecha_exp,
        "fecha_caducidad":  fecha_cad,
        "mrz_linea1":       mrz_lines[0] if len(mrz_lines) > 0 else None,
        "mrz_linea2":       mrz_lines[1] if len(mrz_lines) > 1 else None,
        "observaciones":    None,  # Solo se extrae si esta limpio
    }


# ─────────────────────────────────────────────────────────────────────────────
# CURP
# ─────────────────────────────────────────────────────────────────────────────

def _extraer_curp_doc(texto: str) -> dict:
    ls = _lineas(texto)

    # ── CURP: buscar primero tras "Clave:", luego en cualquier parte ─────────
    clave_val = _valor_tras_etiqueta(ls, [r"CLAVE[:\s]*"])
    curp_m    = PATRON_CURP.search(texto)
    curp = None
    if clave_val:
        m = PATRON_CURP.search(clave_val[0])
        curp = m.group(1) if m else None
    if not curp and curp_m:
        curp = curp_m.group(1)

    # ── Nombre completo ──────────────────────────────────────────────────────
    # Estrategia 1: tras "Nombre:"
    nom_val = _valor_tras_etiqueta(ls, [r"NOMBRE[:\s]*"])
    nombre_completo = nom_val[0] if nom_val else None

    # Estrategia 2: nombre en linea propia como header (Docling lo extrae como
    # "## JOSE TADEO TELLEZ GARCIA" - header markdown)
    # Buscar lineas que sean solo nombres en mayusculas (3-5 palabras)
    if not nombre_completo:
        for linea in ls:
            l = linea.strip().lstrip("#").strip()
            if (re.fullmatch(r"[A-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑ]+){2,4}", l)
                and "PRESENTE" not in l
                and "MEXICANOS" not in l
                and "REGISTRO" not in l
                and "GOBERNACION" not in l
                and "POBLACION" not in l
                and len(l.split()) <= 5):
                nombre_completo = l
                break

    # Descomponer nombre: en CURP (constancia) viene como "NOMBRES APELLIDO1 APELLIDO2"
    primer_ap = segundo_ap = nombres_pila = None
    if nombre_completo:
        partes = nombre_completo.split()
        if len(partes) >= 4:
            # 4 palabras: 2 nombres + 2 apellidos (orden: nombre nombre ap1 ap2)
            nombres_pila = " ".join(partes[:2])
            primer_ap    = partes[2]
            segundo_ap   = partes[3]
        elif len(partes) == 3:
            nombres_pila = partes[0]
            primer_ap    = partes[1]
            segundo_ap   = partes[2]
        elif len(partes) == 2:
            nombres_pila = partes[0]
            primer_ap    = partes[1]

    # ── Fecha de inscripcion / folio / entidad ────────────────────────────────
    fecha_insc = folio = entidad = None
    for i, linea in enumerate(ls):
        if re.search(r"FECHA\s+DE\s+INSCRIPCI[ÓO]N", linea, re.I):
            if i + 1 < len(ls):
                sig = ls[i + 1]
                partes = re.split(r"\s{2,}", sig.strip())
                if len(partes) >= 1:
                    m_fecha = re.search(r"(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})", partes[0])
                    if m_fecha:
                        fecha_insc = _normalizar_fecha(m_fecha.group(1))
                if len(partes) >= 2:
                    folio = partes[1].strip() if re.match(r"^\d+$", partes[1].strip()) else None
                if len(partes) >= 3:
                    entidad = partes[2].strip()
            break

    # Tesseract separa las columnas con un solo espacio:
    # "12/09/2014 203272766 GUANAJUATO"
    if not (fecha_insc and folio and entidad):
        m_fila = re.search(
            r"(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})\s+(\d{6,12})\s+([A-ZÁÉÍÓÚÑ]{3,}(?:[ \t]+[A-ZÁÉÍÓÚÑ]{2,}){0,3})",
            texto,
        )
        if m_fila:
            fecha_insc = fecha_insc or _normalizar_fecha(m_fila.group(1))
            folio = folio or m_fila.group(2)
            entidad = entidad or m_fila.group(3).strip()

    # Fallbacks individuales
    if not fecha_insc:
        m = re.search(r"(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})", texto)
        if m: fecha_insc = _normalizar_fecha(m.group(1))
    if not folio:
        folio_m = re.search(r"\bFOLIO\s+(\d{7,12})\b", texto, re.I)
        folio = folio_m.group(1) if folio_m else None
    if not entidad:
        entidad_val = _valor_tras_etiqueta(ls, [r"ENTIDAD\s+DE\s+REGISTRO"])
        entidad = entidad_val[0] if entidad_val else None

    # CURPs asociadas por correccion
    todos_curps = list(dict.fromkeys(PATRON_CURP.findall(texto)))  # sin duplicados
    otros = [c for c in todos_curps if c != curp]
    curps_asociadas = ", ".join(otros) if otros else None

    # Si la CURP esta disponible, usarla para separar nombre/apellidos:
    # posicion 0 = inicial del primer apellido, 2 = inicial del segundo.
    if nombre_completo and curp:
        partes = nombre_completo.split()
        if (len(partes) >= 3 and partes[-2][:1] == curp[0]
                and partes[-1][:1] == curp[2]):
            primer_ap, segundo_ap = partes[-2], partes[-1]
            nombres_pila = " ".join(partes[:-2])

    # Codigo de verificacion (numero largo de barras)
    cod_m = re.search(r"C[ÓO]DIGO\s+DE\s+VERIFICACI[ÓO]N\s*\n?\s*(\d{15,})", texto, re.I)
    codigo_verificacion = cod_m.group(1) if cod_m else None

    # Fecha de emision de la constancia: "Ciudad de México, a 12 de mayo de 2022"
    # Se usa para la regla RN-06 (antigüedad maxima de 3 meses).
    fecha_emision = _fecha_emision_constancia(texto)

    # Sexo desde CURP
    sexo = _sexo_texto(texto) or _sexo_desde_curp(curp)

    # Fecha nacimiento desde CURP (posicion 4-9: AAMMDD)
    fecha_nac = None
    if curp and len(curp) >= 10:
        try:
            aa = curp[4:6]
            mm = curp[6:8]
            dd = curp[8:10]
            anio = f"19{aa}" if int(aa) > 25 else f"20{aa}"
            fecha_nac = f"{dd}/{mm}/{anio}"
        except Exception:
            pass

    return {
        "nombre_completo":    nombre_completo,
        "primer_apellido":    primer_ap,
        "segundo_apellido":   segundo_ap,
        "nombres":            nombres_pila,
        "curp":               curp,
        "fecha_nacimiento":   fecha_nac,
        "sexo":               sexo,
        "fecha_inscripcion":  fecha_insc,
        "folio":              folio,
        "entidad_registro":   entidad,
        "curps_asociadas":    curps_asociadas,
        "codigo_verificacion": codigo_verificacion,
        "fecha_emision":      fecha_emision,
    }


_MESES = {
    "ENERO": "01", "FEBRERO": "02", "MARZO": "03", "ABRIL": "04", "MAYO": "05",
    "JUNIO": "06", "JULIO": "07", "AGOSTO": "08", "SEPTIEMBRE": "09", "SETIEMBRE": "09",
    "OCTUBRE": "10", "NOVIEMBRE": "11", "DICIEMBRE": "12",
}


def _fecha_emision_constancia(texto: str):
    """Extrae 'a 12 de mayo de 2022' -> '12/05/2022' (fecha de impresion de la constancia)."""
    m = re.search(
        r"\ba\s+(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóú]+)\s+(?:de|del)\s+(\d{4})",
        texto, re.I,
    )
    if not m:
        return None
    mes = _MESES.get(m.group(2).upper().replace("Í", "I").replace("É", "E"))
    if not mes:
        return None
    return f"{int(m.group(1)):02d}/{mes}/{m.group(3)}"


# ─────────────────────────────────────────────────────────────────────────────
# Adaptador principal
# ─────────────────────────────────────────────────────────────────────────────

_EXTRACTORES = {
    TipoDocumento.INE:             _extraer_ine,
    TipoDocumento.PASAPORTE:       _extraer_pasaporte,
    TipoDocumento.ACTA_NACIMIENTO: _extraer_acta,
    TipoDocumento.CURP:            _extraer_curp_doc,
}


class RegexHeuristicAdapter(IFieldExtractorPort):

    @property
    def nombre_adaptador(self) -> str:
        return "regex_heuristic"

    def extraer_campos(self, texto: str, tipo_documento: TipoDocumento) -> dict:
        extractor = _EXTRACTORES.get(tipo_documento)
        if not extractor:
            logger.warning("tipo_sin_extractor_regex", extra={"tipo": tipo_documento.value})
            return {}

        campos = extractor(texto)
        encontrados = sum(1 for v in campos.values() if v)
        logger.info("regex_extraccion_ok", extra={
            "tipo": tipo_documento.value, "campos_encontrados": encontrados
        })
        return campos
