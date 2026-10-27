"""Validadores con casos válidos/inválidos y valores de referencia calculados a mano
o tomados de los especímenes públicos de ICAO 9303 (no son datos de personas)."""
from __future__ import annotations

from datetime import date

import pytest

from src.data_injection.text import icao_transliterate, upper_id
from src.data_injection.validators import (
    build_clave_elector,
    build_curp,
    build_td1_ine,
    build_td3_passport,
    clave_elector_letters,
    curp_check_digit,
    curp_consonants,
    curp_name_letters,
    format_date,
    is_valid_curp,
    is_valid_date,
    mrz_check_digit,
    mrz_name_field,
    parse_date,
    validate_clave_elector,
    validate_curp,
    validate_curp_person,
    validate_td1,
    validate_td3,
)

_DICT = "0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ"


def _independent_curp_digit(curp17: str) -> str:
    total = sum(_DICT.index(c) * w for c, w in zip(curp17, range(18, 1, -1)))
    return str((10 - total % 10) % 10)


# ----------------------------------------------------------------- CURP
def test_curp_reference_example_computed_by_hand():
    # Persona inventada: QUINTERO LEDESMA ROSALBA, 14/02/1987, mujer, Jalisco (JC), homoclave 0.
    # Letras: Q + 1a vocal interna U, L (2o apellido), R (nombre) -> QULR; fecha 870214; sexo M;
    # entidad JC; consonantes internas N (QUINTERO), D (LEDESMA), S (ROSALBA) -> NDS; homoclave 0.
    # Suma ponderada (pesos 18..2, Ñ=24 en el diccionario) = 2675 -> (10 - 5) % 10 = 5  (cálculo manual).
    curp = build_curp("ROSALBA", "QUINTERO", "LEDESMA", date(1987, 2, 14), "M", "JC", "0")
    assert curp == "QULR870214MJCNDS05"
    assert is_valid_curp(curp)


def test_curp_check_digit_matches_independent_implementation():
    for s in ["QULR870214MJCNDS0", "BXEY010101HJCSLYA", "OXXJ991231HNEXXS9", "AAAA000229MASXXXB"]:
        assert curp_check_digit(s) == _independent_curp_digit(s)


def test_curp_altered_digit_or_structure_invalid():
    good = "QULR870214MJCNDS05"
    assert validate_curp(good) == []
    assert validate_curp(good[:17] + "7")
    assert validate_curp("QULR870214MJCNDS0")            # corta
    assert validate_curp("Q0LR870214MJCNDS05")           # dígito donde va letra
    assert validate_curp("QULR871314MJCNDS05")           # mes 13
    assert validate_curp("QULR870214MZZNDS05")           # entidad inexistente
    assert validate_curp("QULR870231MJCNDS0" + curp_check_digit("QULR870231MJCNDS0"))  # 31-feb


def test_curp_inconvenient_word_replaced():
    # B U E Y -> BXEY
    assert curp_name_letters("YOLANDA", "BUENO", "ESTRADA") == "BXEY"
    assert curp_name_letters("ANA", "PEREZ", "LOPEZ") == "PELA"


def test_curp_compound_names_particles_and_enye():
    # JOSÉ/MARÍA se saltan en nombre compuesto; partículas se ignoran; Ñ -> X
    assert curp_name_letters("MARÍA JOSÉ", "DE LA O", "ÑUÑEZ") == "OXXJ"
    assert curp_consonants("MARÍA JOSÉ", "DE LA O", "ÑUÑEZ") == "XXS"
    assert curp_name_letters("JOSE", "GARCIA", "LOPEZ") == "GALJ"        # simple: no se salta
    assert curp_name_letters("JOSE LUIS", "GARCIA", "LOPEZ") == "GALL"
    assert curp_name_letters("MARIA DEL CARMEN", "DEL RIO", "SOTO") == "RISC"
    assert curp_consonants("MARIA DEL CARMEN", "DEL RIO", "SOTO") == "XTR"  # RIO sin consonante interna
    assert curp_consonants("IÑIGO", "PEÑA", "MUÑOZ") == "XXX"
    assert curp_name_letters("ANA", "ÑANDU", "RUIZ") == "XARA"
    assert curp_name_letters("ANA", "PEREZ", None) == "PEXA"             # sin 2o apellido


def test_curp_homoclave_century_rule():
    with pytest.raises(ValueError):
        build_curp("ANA", "PEREZ", "LOPEZ", date(2001, 1, 1), "M", "JC", "5")
    with pytest.raises(ValueError):
        build_curp("ANA", "PEREZ", "LOPEZ", date(1999, 1, 1), "M", "JC", "A")
    c = build_curp("ANA", "PEREZ", "LOPEZ", date(2001, 1, 1), "M", "JC", "A")
    assert c[16] == "A" and is_valid_curp(c)


def test_curp_person_coherence():
    curp = "QULR870214MJCNDS05"
    kw = dict(nombres="ROSALBA", ap1="QUINTERO", ap2="LEDESMA", birth=date(1987, 2, 14), sexo="M", entidad_curp="JC")
    assert validate_curp_person(curp, **kw) == []
    assert validate_curp_person(curp, **{**kw, "sexo": "H"})
    assert validate_curp_person(curp, **{**kw, "birth": date(1987, 2, 15)})
    assert validate_curp_person(curp, **{**kw, "nombres": "LAURA"})
    assert validate_curp_person(curp, **{**kw, "entidad_curp": "DF"})


# ---------------------------------------------------------- Clave de elector
def test_clave_elector():
    # Cálculo manual (inicial + primera consonante interna): QUINTERO -> QN, LEDESMA -> LD,
    # ROSALBA -> RS => QNLDRS; + 870214 (AAMMDD) + 14 (Jalisco) + M + homoclave 100.
    assert clave_elector_letters("ROSALBA", "QUINTERO", "LEDESMA") == "QNLDRS"
    clave = build_clave_elector("ROSALBA", "QUINTERO", "LEDESMA", date(1987, 2, 14), 14, "M", "100")
    assert clave == "QNLDRS87021414M100"
    kw = dict(nombres="ROSALBA", ap1="QUINTERO", ap2="LEDESMA", birth=date(1987, 2, 14), sexo="M", entidad_num=14)
    assert validate_clave_elector(clave, **kw) == []
    assert validate_clave_elector(clave, **{**kw, "entidad_num": 9})
    assert validate_clave_elector(clave, **{**kw, "sexo": "H"})
    assert validate_clave_elector("QNLDRS87021414X100")
    assert validate_clave_elector("QNLDRS87133209M100")           # fecha y entidad inválidas
    assert validate_clave_elector("QNLDR87021414M100")


# ----------------------------------------------------------------- MRZ
def test_mrz_check_digit_icao_specimens():
    # Especímenes públicos de ICAO Doc 9303.
    assert mrz_check_digit("L898902C3") == "6"
    assert mrz_check_digit("740812") == "2"
    assert mrz_check_digit("120415") == "9"
    assert mrz_check_digit("ZE184226B<<<<<") == "1"
    assert mrz_check_digit("D23145890") == "7"
    l2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"
    assert mrz_check_digit(l2[0:10] + l2[13:20] + l2[21:43]) == l2[43] == "0"
    td1_l1, td1_l2 = "I<UTOD231458907<<<<<<<<<<<<<<<", "7408122F1204159UTO<<<<<<<<<<<6"
    assert mrz_check_digit(td1_l1[5:30] + td1_l2[0:7] + td1_l2[8:15] + td1_l2[18:29]) == "6"


def test_icao_transliteration_and_name_field():
    assert icao_transliterate("Peña-Nieto") == "PENA<NIETO"
    assert icao_transliterate("José Ángel") == "JOSE<ANGEL"
    assert icao_transliterate("D'Ávila") == "D<AVILA"
    assert mrz_name_field("PEREZ LOPEZ", "ANA", 30) == "PEREZ<LOPEZ<<ANA".ljust(30, "<")
    long = mrz_name_field("HERNANDEZ GUTIERREZ", "MARIA GUADALUPE", 30)
    assert len(long) == 30 and long == "HERNANDEZ<GUTIERREZ<<MARIA<GUA"
    very = mrz_name_field("DE LA GARZA MONTEMAYOR VILLARREAL", "MARIA", 30)
    assert len(very) == 30 and very.endswith("<<M") and very.startswith("DE<LA<GARZA")


def _td1():
    return build_td1_ine(cic="123456789", ocr="0123456789012", birth=date(1987, 2, 14), sexo="M",
                         vigencia_year=2030, ap1="QUINTERO", ap2="PEÑA", nombres="MARÍA JOSÉ")


def test_td1_valid_structure():
    lines = _td1()
    assert [len(x) for x in lines] == [30, 30, 30]
    assert lines[0] == "IDMEX123456789" + mrz_check_digit("123456789") + "<<0123456789012"
    assert lines[1].startswith("870214" + mrz_check_digit("870214") + "F301231")
    assert lines[1][15:18] == "MEX"
    assert lines[2] == "QUINTERO<PENA<<MARIA<JOSE".ljust(30, "<")
    assert validate_td1(lines) == []


@pytest.mark.parametrize("line,pos", [(0, 14), (1, 6), (1, 14), (1, 29)])
def test_td1_altered_digit_detected(line, pos):
    lines = _td1()
    s = lines[line]
    lines[line] = s[:pos] + str((int(s[pos]) + 1) % 10) + s[pos + 1:]
    assert validate_td1(lines)


def test_td1_bad_shape():
    lines = _td1()
    assert validate_td1(lines[:2])
    assert validate_td1([lines[0] + "<", lines[1], lines[2]])
    assert validate_td1([lines[0].lower(), lines[1], lines[2]])


def _td3():
    return build_td3_passport(numero="G12345678", birth=date(1990, 2, 3), sexo="F",
                              expiry=date(2031, 5, 6), apellidos="NÚÑEZ DE LA O", nombres="ANA SOFÍA")


def test_td3_valid_structure():
    l1, l2 = _td3()
    assert len(l1) == len(l2) == 44
    assert l1 == "P<MEXNUNEZ<DE<LA<O<<ANA<SOFIA".ljust(44, "<")
    assert l2[:10] == "G12345678" + mrz_check_digit("G12345678")
    assert l2[10:13] == "MEX" and l2[13:20] == "900203" + mrz_check_digit("900203")
    assert l2[20] == "F" and l2[21:28] == "310506" + mrz_check_digit("310506")
    assert l2[28:42] == "<" * 14
    assert validate_td3([l1, l2]) == []


@pytest.mark.parametrize("pos", [9, 19, 27, 43])
def test_td3_altered_digit_detected(pos):
    l1, l2 = _td3()
    l2 = l2[:pos] + str((int(l2[pos]) + 1) % 10) + l2[pos + 1:]
    assert validate_td3([l1, l2])


def test_td3_optional_personal_number():
    l1, l2 = build_td3_passport(numero="N00000001", birth=date(2005, 1, 1), sexo="M",
                                expiry=date(2030, 1, 1), apellidos="X", nombres="Y", optional="ABC123")
    assert l2[28:42] == "ABC123<<<<<<<<" and l2[42] == mrz_check_digit("ABC123<<<<<<<<")
    assert validate_td3([l1, l2]) == []


# ----------------------------------------------------------------- Fechas y texto
def test_dates():
    assert parse_date("14/02/1987") == date(1987, 2, 14)
    assert parse_date("14 02 1987", "spaced") == date(1987, 2, 14)
    assert format_date(date(2001, 2, 3)) == "03/02/2001"
    assert format_date(date(2001, 2, 3), "spaced") == "03 02 2001"
    assert not is_valid_date("31/02/2020")
    assert not is_valid_date("2020-01-01")
    assert not is_valid_date("14/02/1987", "spaced")
    assert is_valid_date("29/02/2020")


def test_upper_id_keeps_enye_drops_accents():
    assert upper_id("  Peña   Núñez Güemes ") == "PEÑA NUÑEZ GUEMES"
