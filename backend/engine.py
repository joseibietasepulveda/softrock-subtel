"""
Motor de detección y protección de datos personales.

Única acción soportada en esta versión: REDACT (tachado irreversible).
- Texto (DOCX/XLSX/TXT): cada carácter del dato se reemplaza por '█'.
- PDF e imágenes: se rasteriza la página, se pintan rectángulos negros sobre las
  palabras detectadas y se reconstruye el archivo sin metadatos.

Sin dependencias de red. Sin base de datos. Solo rutas de entrada/salida.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ENGINE_VERSION = "0.4.12"
BLOCK = "█"
# Perfil sRGB ICC v2 (CC0, github.com/saucecontrol/Compact-ICC-Profiles). img2pdf lo embebe
# como OutputIntent y así todo PDF de salida es PDF/A-1b; PDF/A-1 no admite perfiles v4.
SRGB_ICC = str(Path(__file__).with_name("sRGB-v2-micro.icc"))

# ---------------------------------------------------------------------------
# Catálogo de tipos de dato sensible (lo que el usuario elige en el desplegable)
# ---------------------------------------------------------------------------

ENTITY_TYPES: dict[str, str] = {
    "NOMBRE": "Nombre de persona",
    "RUT": "RUT",
    "DIRECCION": "Dirección",
    "EMAIL": "Correo electrónico",
    "TELEFONO": "Teléfono",
    "FECHA_NACIMIENTO": "Fecha de nacimiento",
    "FECHA": "Fecha (cualquiera)",
    "PATENTE": "Patente vehicular",
    "IP": "Dirección IP",
    "CUENTA_BANCARIA": "Cuenta bancaria",
    "PASAPORTE": "Pasaporte",
    "TARJETA": "Tarjeta de crédito/débito",
    "WEB_REDES": "Sitio web / red social",
    "FOLIO": "N° de folio / expediente / ingreso",
    "FIRMA": "Firma manuscrita",
}

# Detección heurística de nombres mediante vocabulario y contexto.
FIRST_NAMES = set("""
abel abraham adolfo adrian adriana agustin agustina aida alan alba alberto aldo alejandra alejandro alex alexis alfonso alfredo alicia alonso alvaro amanda amaro amelia ana anais andrea andres angel angela angelica angelo antonia antonio araceli ariel armando arturo aurora axel bastian beatriz belen benjamin bernardo berta blanca boris bruno camila camilo carla carlos carmen carolina catalina cecilia cesar christian cindy clara claudia claudio constanza consuelo cristian cristina cristobal damian daniel daniela dario david denisse diego domingo dominga dora edgardo eduardo elena eliana elias elisa elizabeth eloisa elsa emilia emilio emma enrique erick ernesto esteban estefania esther eugenia eugenio eva evelyn fabian fabiola federico felipe fernanda fernando fidel flavia florencia francisca francisco franco gabriel gabriela gaspar genoveva georgina gerardo german gladys gloria gonzalo graciela gregorio guillermo gustavo hector hernan hilda horacio hugo humberto ignacia ignacio ines irene iris isabel isidora ivan ivonne jacinta jaime javier javiera jesus jimena joaquin jorge jose josefa josefina juan juana julia julian julio karen karina karla katherine laura leandro leonardo leonor leticia lidia liliana lorena lorenzo lucas lucia luciano luis luisa luz macarena magdalena manuel manuela marcela marcelo marcia marco marcos margarita maria mariana mariano marina mario marisol marta martin matias mauricio maximiliano mercedes miguel milena mireya miriam moises monica natalia nelson nicolas nicole noemi norma octavio olga olivia omar orlando oscar osvaldo pablo paloma pamela paola patricia patricio paula paulina pedro pilar rafael ramon raquel raul rebeca renato rene ricardo roberto rocio rodolfo rodrigo rolando romina rosa rosario ruben ruth salvador samuel sandra santiago sara sebastian sergio silvia simon sofia soledad sonia susana tamara teresa tomas trinidad ulises valentina valeria valentin vanessa veronica vicente victor victoria violeta viviana walter ximena yolanda
""".split())
FIRST_NAMES.update("""
adelita adela adelaida agnes alessandra alessandro alexander alexandre alison allison
anastasia anibal antoine arlette astrid aurelio brenda brian brigitte bryan celine
charles charlotte christopher ciro dalia daphne debora derek diana dominique doris
edgar edith edmond edwin eleonora elodie eloy erica erika ethan etienne ezequiel
fabrizio fatima felicia fiorella flavio freddy geoffrey gerald geraldine giancarlo
gianfranco gina giorgio giovanna giovanni guido harold harry henri henry ian ingrid
irving jacques jan janet janette jannette jean jeanette jeannette jeannine jennifer
jenny jeremy jessica joan johann johanna john johnny jonathan josue joyce judith
kevin kurt lars leopoldo leon lionel lisette liz lorna ludwig marc margot marilyn
marlene marvin mathieu maureen melanie melissa michael michel michele michelle
mila miranda nadia nancy naomi nathalie nathan nathaniel nilda noel norberto
pablina pamela pascal patrick philippe pierre rachel regina regis richard roland
romualdo ronald roxana sabrina samantha sean sharon shirley stephanie stephen
steven tatiana thiago thierry timothy ursula vania vladimir wilfredo william
willy winston yadira yanet yanina yasmin yenny yuri zoe
""".split())
# Nombres de pila adicionales para ampliar la cobertura del vocabulario.
FIRST_NAMES.update({"dionisio", "dinson", "jenson", "aaron", "leslie", "giovani",
                    "galvarino", "eladio", "mabel"})

# Apoyo para nombres de pila fuera del catálogo: se requieren dos apellidos,
# no basta con encontrar una palabra que también pueda ser lugar o empresa.
CHILEAN_SURNAMES = set("""
abarca acuna aguilar aguirre alarcon albornoz alfaro almonacid alvarado alvarez
andrade angulo antilef arancibia aravena araya arriagada avila bahamondes barrera
barrientos barrios bascunan becerra beltran benavides bravo briones bustamante
bustos cabrera caceres calderon campos canales cardenas carrasco carrillo cartagena
carvajal castillo castro catalan catrileo cerda ceron cespedes cid cisternas
colipan colque contreras cornejo cortes covarrubias cuevas curihual diaz donoso
duarte duran echeverria encina escobar espinoza espinosa estay estrada farias
fernandez ferrada fierro figueroa flores fuentes gallardo galaz gallegos garcia
garrido godoy gomez gonzalez guerra guerrero gutierrez guzman henriquez hermosilla
hernandez herrera hidalgo huenchuleo huenchuman huilcaman ibanez jara jerez
jimenez lagos lara leal leiva letelier lillo llancaman llanos lobos lopez loyola
luengo macaya maldonado manriquez mardones marin marquez martinez matus medina
mella melillan melo meneses millapan millar miranda molina montecinos montes
montoya mora morales moreno munoz nahuel nahuelpan navarrete navarro neira nunez
ojeda oliva olivares olmos orellana ormeno ortega ortiz ossa osorio oyarzun
pacheco paillalef palacios palma paredes parra pavez pena penaloza pereira perez
pichun pincheira pino poblete ponce prado prieto puelma quezada quinones quinteros
quintana ramirez ramos rebolledo reyes riquelme rivas rivera robles rocha rodriguez
rojas romero rosales rozas ruiz saavedra salazar salgado salinas sanchez sandoval
sanhueza santibanez sepulveda silva solar solis soto tapia toledo toro torres
troncoso trujillo uribe urrutia valdes valdivia valencia valenzuela valladares
vargas vasquez vega velasquez venegas vera vergara vidal villalobos villanueva
villegas vivanco yanez zambrano zamorano zuniga
""".split())
CHILEAN_SURNAMES.update({"ordenes", "villar", "mena", "bernal", "saez", "galleguillos",
                        "olave", "baez", "tirado", "veas", "rios", "veliz", "mercado",
                        "inostroza", "medel", "romo", "cordova", "burgos", "cruz",
                        "oyarce", "millones", "baack", "quintero", "cochrane",
                        "tomasello", "hart", "kriman", "avello", "concha"})
_NON_PERSON_STARTS = {"colegio", "universidad", "constructora", "inmobiliaria",
                      "consultora", "fundacion", "asociacion", "comite", "familia",
                      "que", "el", "la", "los", "las", "de", "del", "y"}

# Términos institucionales que nunca son nombres de persona.
WHITELIST = set("""
subsecretaria subsecretario subsecretaría ministerio ministro ministra division división departamento gobierno chile santiago region región republica república ley decreto resolucion resolución exento exenta oficio ordinario telecomunicaciones direccion dirección oficina servicio superintendencia empresa sociedad limitada spa ltda transparencia unidad seccion sección gabinete fiscalia fiscalía contraloria contraloría general nacional publica pública publico público municipalidad ilustre honorable senado camara cámara diputados tribunal corte suprema apelaciones consejo comision comisión estado fiscal jefe jefa secretaria secretaría alcalde salud director directora desam proveedor comercial comercializadora asegurado asegurada asegurando abogado abogada integrante fecha
""".split())
WHITELIST.add("programa")

_RUT_RE = re.compile(
    r"(?<![\d.])\d{1,2}(?:[^\S\r\n]*\.?[^\S\r\n]*\d{3}){2}"
    r"[^\S\r\n]*-?\s*[\dkK](?![\d])"
)
_EMAIL_RE = re.compile(r"[\w.%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}", re.UNICODE)
# Hueco que meten pdfplumber y Tesseract al trocear la página en palabras: la dirección
# llega partida ("soporte@softrock. cl") o cortada por el salto de línea de la celda, y
# el patrón estricto —que no admite separadores— deja de verla.
_HUECO = r"[^\S\r\n]{0,2}(?:\n[^\S\r\n]{0,2})?"
_ARROBA = (r"(?:@|[\(\[]" + _HUECO + r"(?:arroba|at)" + _HUECO + r"[\)\]]|arroba|"
           r"[\(\[]" + _HUECO + r"[A-Za-z0-9]" + _HUECO + r"[\)\]]?)")
_EMAIL_LOOSE_RE = re.compile(
    r"(?<![\w.%+-])"
    r"[\w%+-]{1,64}(?:" + _HUECO + r"\." + _HUECO + r"[\w%+-]{1,64}){0,8}"
    + _HUECO + _ARROBA + _HUECO +
    r"[A-Za-z0-9_-]{1,63}(?:" + _HUECO + r"\." + _HUECO + r"[A-Za-z0-9_-]{1,63}){0,4}"
    + _HUECO + r"\." + _HUECO + r"[A-Za-z]{2,24}"
    r"(?![\w-])",
    re.IGNORECASE | re.UNICODE,
)

# Homóglifos habituales al copiar desde Word/PDF o al salir del OCR. La tabla es 1:1:
# el texto normalizado conserva la longitud, así los desplazamientos calculados sobre él
# siguen apuntando al mismo lugar del texto original.
_HOMOGLIFOS = str.maketrans({
    "\u00a0": " ", "\u2007": " ", "\u2009": " ", "\u200a": " ", "\u202f": " ",
    "\uff20": "@", "\ufe6b": "@", "\u24d0": "@",
    "\uff0e": ".", "\u2024": ".",
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
    "\u2212": "-", "\uff0d": "-",
})


def _canon(text: str) -> str:
    """Normaliza homóglifos sin alterar la longitud del texto."""
    return text.translate(_HOMOGLIFOS)


_PHONE_RE = re.compile(
    r"(?<![\d.])(?:\+?56[\s.-]?)?(?:\(?\s?(?:9|2|3[2-9]|4[1-5]|5[1-8]|6[1-7]|7[1-5])\s?\)?[\s.-]?)\d{3,4}[\s.-]?\d{4}(?![\d])"
)
_LABELED_LEGACY_PHONE_RE = re.compile(
    r"\b(?:tel[eé]fono|tel\s+fono|fono|celular)[^\S\r\n]*[:.]?[^\S\r\n]*"
    r"(0[2-9][\s.-]?\d{7,8})(?!\d)", re.IGNORECASE,
)
_ADDRESS_RE = re.compile(
    r"\b(?:Av\.|Avda\.|Avenida|Calle|Pasaje|Pje\.|Camino|Ruta|Callej[oó]n|Diagonal|Alameda|Parcela|Lote|Km\.?)"
    r"[^\S\r\n]+[A-ZÁÉÍÓÚÑ0-9][^\n,;]{1,60}?[^\S\r\n]*(?:N[°º][^\S\r\n]*)?\d{1,5}"
    r"(?:[^\S\r\n]*(?:,[^\S\r\n]*)?(?:depto\.?|departamento|oficina|of\.?|piso|casa|local|block)"
    r"[^\S\r\n]*\w{1,6})*"
    r"(?:[^\S\r\n]*,[^\S\r\n]*(?:comuna[^\S\r\n]+de[^\S\r\n]+)?"
    r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:[^\S\r\n]+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)?",
    re.IGNORECASE,
)
_MONTH = r"(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)"
_DATE_CORE = (
    r"(?:(?<!\d)\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}(?!\d)|"
    r"(?<!\d)\d{1,2}\s+de\s+" + _MONTH + r"\s+(?:de\s+)?\d{4}(?!\d))"
)
_DATE_RE = re.compile(_DATE_CORE, re.IGNORECASE)
_BIRTH_RE = re.compile(
    r"(?:fecha\s+de\s+nacimiento|nacid[oa]\s+(?:el|en)|nacimiento|f\.\s*nac\.?)\s*[:.]?\s*(" + _DATE_CORE + ")",
    re.IGNORECASE,
)
_PLATE_RE = re.compile(
    r"(?<![A-Z0-9])(?:[B-DF-HJ-LP-TV-Z]{4}[.\-\s]?\d{2}|[A-Z]{2}[.\-\s]?\d{4})(?![A-Z0-9])"
)
_IP_RE = re.compile(r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?!\d)")
_ACCOUNT_RE = re.compile(
    r"(?:cuenta\s+(?:corriente|vista|rut|de\s+ahorro|bancaria)?|cta\.?\s*(?:cte\.?)?|n[°º]\s*de\s*cuenta)"
    r"\s*(?:n\s*[^A-Za-z0-9\s]{0,3}\s*)?[:.]?\s*(\d[\d\s.-]{5,22}\d)",
    re.IGNORECASE,
)
_PASSPORT_RE = re.compile(
    r"pasaporte\s*(?:n\s*[^A-Za-z0-9\s]{0,3}\s*)?[:.]?\s*([A-Z0-9]{6,12})",
    re.IGNORECASE,
)
_CARD_RE = re.compile(r"(?<![\d])(?:\d{4}[\s-]?){3}\d{4}(?![\d])")
# La URL puede llegar partida al ancho de la columna (CV de diseño, celdas): la
# continuación en la línea siguiente solo entra si la URL termina en un carácter
# explícito de continuación (/-_=?&); una URL que termina limpia no arrastra la línea.
_WEB_RE = re.compile(r"(?:https?://|www\.)\S+(?:(?<=[/\-_=?&])\n[^\s@]+)?|(?<![\w@.])@[A-Za-z0-9_.]{3,30}\b", re.IGNORECASE)
_FOLIO_RE = re.compile(
    r"(?:folio|expediente|exp\.|ingreso|(?:causa\s+)?rol|n[°º]\s*de\s*(?:ingreso|solicitud|caso|ticket))"
    r"\s*(?:n[°º]\.?)?\s*[:.]?\s*([A-Z]{0,3}[-\s]?\d[\d.\-/]{2,20})",
    re.IGNORECASE,
)

_CAP = r"(?:[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü]{1,40}|[A-ZÁÉÍÓÚÑÜ]{2,40})"
_CONN = r"(?:de|del|la|las|los|y|San|Santa|Da|Di|Van|Von|De|Del|La)"
_HSPACE = r"[^\S\r\n]"
_NAME_SEQ_RE = re.compile(rf"(?=(\b{_CAP}(?:{_HSPACE}+(?:{_CONN}{_HSPACE}+)?{_CAP}){{1,4}}\b))")
_TITLE_TRIGGER = r"(?i:\b(?:Sr|Sra|Srta|Dn|Dña|Don|Doña|Señor|Señora|Abogado|Abogada|Ing|Dr|Dra))\.?"
_FIELD_TRIGGER = (
    rf"(?i:(?:nombre(?:{_HSPACE}+completo)?|representante(?:{_HSPACE}+legal)?|solicitante|denunciante|"
    rf"reclamante|funcionari[oa]|interesad[oa]|contratista|trabajador[a]?|firmante|apoderad[oa]))"
    rf"{_HSPACE}*:{_HSPACE}*"
)
_NAME_TRIGGER_RE = re.compile(
    rf"(?:{_TITLE_TRIGGER}{_HSPACE}+|{_FIELD_TRIGGER})"
    rf"({_CAP}(?:{_HSPACE}+(?:{_CONN}{_HSPACE}+)?{_CAP}){{0,4}})",
)
_COURT_PARTY_RE = re.compile(
    rf"(?i:\bcaratulad[oa]){_HSPACE}*[:.]?{_HSPACE}*[\"“~]?"
    rf"({_CAP})(?=\s+(?i:con)\b)",
)


def _strip(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def _telefono_plausible(v: str) -> bool:
    """Filtra los rangos de años de una línea de tiempo («2023\n2013»), que calzan con
    el patrón como área 2 + 023 + 2013. Con prefijo de país siempre pasa."""
    digitos = re.sub(r"\D", "", v)
    if digitos.startswith("56") and len(digitos) > 9:
        return True
    if "\n" in v.strip():
        return False  # un teléfono real no llega partido por un salto de línea
    if len(digitos) == 8 and all(1900 <= int(digitos[i:i + 4]) <= 2099 for i in (0, 4)):
        return False  # dos años pegados, no un número
    if re.fullmatch(r"\s*\d{4}-\d{4}\s*", v):
        return False  # rol judicial sin la letra C (p. ej. 2861-2018), no teléfono
    return True


def rut_valido(rut: str) -> bool:
    cuerpo = re.sub(r"[^0-9kK]", "", rut)
    if len(cuerpo) < 8:
        return False
    num, dv = cuerpo[:-1], cuerpo[-1].upper()
    if int(num) >= 50_000_000:
        return False  # persona jurídica: no se trata como dato personal
    s, m = 0, 2
    for d in reversed(num):
        s += int(d) * m
        m = 2 if m == 7 else m + 1
    r = 11 - (s % 11)
    esperado = "0" if r == 11 else "K" if r == 10 else str(r)
    return dv == esperado


def _patente_plausible(value: str) -> bool:
    compacta = re.sub(r"[^A-Z0-9]", "", value.upper())
    return not compacta.startswith("DE")


def _fecha_plausible(value: str) -> bool:
    m = re.match(r"\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\s*$", value)
    if m:
        from datetime import date
        year_text = m.group(3)
        if len(year_text) not in (2, 4):
            return False
        year = int(year_text)
        year = 2000 + year if len(year_text) == 2 else year
        if not 1900 <= year <= 2100:
            return False
        try:
            date(year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return False
        return True
    m = re.match(r"\s*(\d{1,2})\s+de\s+", value, re.IGNORECASE)
    return not m or 1 <= int(m.group(1)) <= 31


def _luhn(num: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", num)]
    if len(digits) < 13:
        return False
    total, alt = 0, False
    for d in reversed(digits):
        if alt:
            d = d * 2
            d = d - 9 if d > 9 else d
        total += d
        alt = not alt
    return total % 10 == 0


@dataclass
class Span:
    start: int
    end: int
    code: str


def _names(text: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []

    def trimmed(start: int, value: str, minimum: int = 2):
        tokens = list(re.finditer(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü]+", value))
        if tokens and _strip(tokens[0].group()) in {"integrante", "fecha", "ministro", "secretario"}:
            return None
        stop = len(tokens)
        for i, token in enumerate(tokens[1:], 1):
            # «Rene Valencia Uribe y Diego Vergara Arriagada» son dos personas.
            # Conservar, en cambio, el conector de «José Ortega y Gasset».
            if (token.group().lower() == "y" and i >= 2 and i + 1 < len(tokens)
                    and _strip(tokens[i + 1].group()) in FIRST_NAMES):
                stop = i
                break
            if _strip(token.group()) in WHITELIST:
                stop = i
                if i > 1 and _strip(tokens[i - 1].group()) in {"de", "del", "la", "las", "los", "y"}:
                    stop -= 1
                break
        if stop < minimum:
            return None
        return start, start + tokens[stop - 1].end()

    for m in _NAME_TRIGGER_RE.finditer(text):
        span = trimmed(m.start(1), m.group(1), minimum=1)
        if span and _strip(text[span[0]:span[1]]) not in WHITELIST:
            out.append(span)
    for m in _COURT_PARTY_RE.finditer(text):
        if _strip(m.group(1)) not in WHITELIST | _NON_PERSON_STARTS:
            out.append((m.start(1),m.end(1)))
    for m in _NAME_SEQ_RE.finditer(text):
        candidate = m.group(1)
        first = re.match(_CAP, candidate)
        span = trimmed(m.start(1), candidate)
        if first and span:
            tokens = _strip(text[span[0]:span[1]]).split()
            known_first = _strip(first.group()) in FIRST_NAMES
            surname_support = (3 <= len(tokens) <= 5
                               and tokens[0] not in WHITELIST | _NON_PERSON_STARTS
                               and all(t in CHILEAN_SURNAMES for t in tokens[-2:]))
            if known_first or surname_support:
                out.append(span)
    return list(dict.fromkeys(out))


def detect(text: str, codes: list[str] | set[str]) -> list[Span]:
    """Devuelve spans (start, end, code) sin solapamientos. Orden de prioridad = orden de esta función."""
    codes = set(codes)
    canon = _canon(text)
    found: list[Span] = []

    def add(rx, code, group=0, validator=None, evitar=None):
        """`evitar`: rangos ya cubiertos por una pasada previa del mismo tipo."""
        if code not in codes:
            return []
        spans = []
        for m in rx.finditer(canon):
            val = m.group(group)
            if validator and not validator(val):
                continue
            start, end = m.start(group), m.end(group)
            while end > start and canon[end - 1] in ".,;:)":
                end -= 1
            if evitar and any(start < e and end > s for s, e in evitar):
                continue
            found.append(Span(start, end, code))
            spans.append((start, end))
        return spans

    # El correo se busca dos veces. Primero el patrón estricto; después el tolerante a
    # cortes, y solo donde el estricto no encontró nada: así una dirección bien formada
    # nunca arrastra el texto vecino, y una partida deja de perderse.
    estrictos = add(_EMAIL_RE, "EMAIL")
    add(_EMAIL_LOOSE_RE, "EMAIL", evitar=estrictos)
    add(_WEB_RE, "WEB_REDES")
    add(_RUT_RE, "RUT", validator=rut_valido)
    add(_IP_RE, "IP")
    add(_CARD_RE, "TARJETA", validator=_luhn)
    add(_ACCOUNT_RE, "CUENTA_BANCARIA", group=1)
    add(_PASSPORT_RE, "PASAPORTE", group=1)
    add(_FOLIO_RE, "FOLIO", group=1)
    add(_BIRTH_RE, "FECHA_NACIMIENTO", group=1)
    add(_PHONE_RE, "TELEFONO", validator=_telefono_plausible)
    add(_LABELED_LEGACY_PHONE_RE, "TELEFONO", group=1)
    add(_PLATE_RE, "PATENTE", validator=_patente_plausible)
    add(_ADDRESS_RE, "DIRECCION")
    add(_DATE_RE, "FECHA", validator=_fecha_plausible)
    if "NOMBRE" in codes:
        found += [Span(s, e, "NOMBRE") for s, e in _names(canon)]

    # dedupe: el primero (mayor prioridad) gana; se descartan solapados
    found.sort(key=lambda s: (s.start, -(s.end - s.start)))
    result: list[Span] = []
    taken: list[tuple[int, int]] = []
    for sp in sorted(found, key=lambda s: _PRIORITY.get(s.code, 99)):
        if any(sp.start < e and sp.end > s for s, e in taken):
            continue
        taken.append((sp.start, sp.end))
        result.append(sp)
    return sorted(result, key=lambda s: s.start)


_PRIORITY = {c: i for i, c in enumerate(
    ["EMAIL", "WEB_REDES", "RUT", "IP", "TARJETA", "CUENTA_BANCARIA", "PASAPORTE", "FOLIO",
     "FECHA_NACIMIENTO", "TELEFONO", "PATENTE", "DIRECCION", "NOMBRE", "FECHA"])}


def redact_text(text: str, codes) -> tuple[str, list[Span]]:
    spans = detect(text, codes)
    chars = list(text)
    for sp in spans:
        for i in range(sp.start, sp.end):
            if not chars[i].isspace():
                chars[i] = BLOCK
    return "".join(chars), spans


# ---------------------------------------------------------------------------
# Formatos
# ---------------------------------------------------------------------------

SUPPORTED = {".pdf", ".docx", ".xlsx", ".txt", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".doc", ".xls"}


class EngineError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _count(spans: list[Span], stats: dict):
    for sp in spans:
        stats[sp.code] = stats.get(sp.code, 0) + 1


# ---- PDF / imágenes ---------------------------------------------------------

# El paréntesis puede salir con cualquier letra dentro —(W, (a), (Q), (G)…—, así que se
# acepta un carácter cualquiera: el contexto (palabra antes, dominio con TLD después) es
# lo que evita los falsos positivos.
_OCR_AT_RE = re.compile(
    r"(?<=[\w.+-])(?:[\(\[]\s?[A-Za-z0-9]\s?[\)\]]?|[©®ⓐ])(?=[\w.-]+\.\w{2,})",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def _ocr_language() -> str | None:
    """Elige el mejor conjunto instalado sin hacer que OCR dependa rígidamente de `spa`."""
    configured = os.environ.get("OCR_LANG", "").strip()
    if configured:
        return configured
    import pytesseract
    available = set(pytesseract.get_languages(config=""))
    if {"spa", "eng"} <= available:
        return "spa+eng"
    if "spa" in available:
        return "spa"
    if "eng" in available:
        return "eng"
    return None


def _ocr_words(img) -> list[tuple[str, int, int, int, int]]:
    import pytesseract
    kwargs = {"output_type": pytesseract.Output.DICT}
    lang = _ocr_language()
    if lang:
        kwargs["lang"] = lang
    d = pytesseract.image_to_data(img, **kwargs)
    words = []
    for i, t in enumerate(d["text"]):
        t = t.strip()
        if not t:
            continue
        # Tesseract suele leer '@' como '(W', '(a)', '(Q'. Corrección mínima para no perder correos.
        t = _OCR_AT_RE.sub("@", t)
        x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
        words.append((t, x, y, x + w, y + h))
    return words


def _same_text_line(a, b) -> bool:
    """Determina si dos palabras con coordenadas pertenecen a la misma línea visual."""
    _, _, ay0, _, ay1 = a
    _, _, by0, _, by1 = b
    ah, bh = max(ay1 - ay0, 1), max(by1 - by0, 1)
    overlap = min(ay1, by1) - max(ay0, by0)
    if overlap >= min(ah, bh) * 0.35:
        return True
    ac, bc = (ay0 + ay1) / 2, (by0 + by1) / 2
    return abs(ac - bc) <= max(ah, bh) * 0.45


def _words_text_map(words) -> tuple[str, list[int | None]]:
    """Reconstruye texto conservando líneas y su mapa carácter → palabra."""
    parts: list[str] = []
    idx_at: list[int | None] = []
    previous = None
    for i, word in enumerate(words):
        if previous is not None:
            same_line = _same_text_line(previous, word)
            ph = max(previous[4] - previous[2], word[4] - word[2], 1)
            # En tablas, pdfplumber entrega consecutivas celdas distintas de una misma
            # fila. Una separación grande es un cambio de columna, no otro apellido.
            large_column_gap = word[1] - previous[3] > ph * 3
            separator = " " if same_line and not large_column_gap else "\n"
            parts.append(separator)
            idx_at.append(None)
        parts.append(word[0])
        idx_at.extend([i] * len(word[0]))
        previous = word
    return "".join(parts), idx_at


_FIRMA_KW_RE = re.compile(r"^firmad?[oa]?s?[:.]?$|^firma[:.]?$", re.IGNORECASE)


def _signature_boxes(img, words) -> list[tuple[int, int, int, int]]:
    """Heurística FR-24: mancha de tinta junto a (o sobre) la palabra 'Firma'.
    Excluye la propia palabra y las líneas delgadas de subrayado. Score bajo; siempre revisable."""
    boxes = []
    gray = img.convert("L")
    W, H = gray.size
    min_h = max(8, H // 140)

    def scan(rx0, ry0, rx1, ry1):
        rx0, ry0, rx1, ry1 = max(0, rx0), max(0, ry0), min(W, rx1), min(H, ry1)
        if rx1 - rx0 < 10 or ry1 - ry0 < 10:
            return None
        region = gray.crop((rx0, ry0, rx1, ry1)).point(lambda v: 255 if v < 100 else 0)
        bbox = region.getbbox()
        if not bbox:
            return None
        bx0, by0, bx1, by1 = bbox
        if by1 - by0 < min_h:  # línea de subrayado u otro trazo delgado
            return None
        dark = sum(1 for v in region.crop(bbox).getdata() if v > 0)
        if dark < 80 or dark / max((bx1 - bx0) * (by1 - by0), 1) < 0.01:
            return None
        return (rx0 + bx0, ry0 + by0, rx0 + bx1, ry0 + by1)

    for text, x0, y0, x1, y1 in words:
        if not _FIRMA_KW_RE.match(text):
            continue
        same_line = [w for w in words if _same_text_line((text, x0, y0, x1, y1), w)]
        if len(same_line) > 5 and not text.rstrip().endswith(":"):
            continue  # "a la firma de esta transacción" en prosa no es un campo Firma
        line_h = max(y1 - y0, H // 180, 10)
        # La búsqueda se limita a la línea de firma. Las ventanas basadas en H
        # capturaban antes el folio y la observación de las líneas vecinas.
        right = scan(x1 + 5, y0 - line_h, x1 + W // 2, y1 + int(line_h * 1.25))
        above = scan(x0 - W // 60, y0 - int(line_h * 1.5), x1 + W // 3, y0 - 2)
        candidate = right or above
        if candidate:
            boxes.append(candidate)
    # Fallback de página completa: una plancha que es solo la firma manuscrita no trae
    # texto que ancle la búsqueda (0 palabras de OCR). Si no hubo ancla y la página está
    # casi vacía de texto, se busca la mancha en toda la página; se exige trazo delgado
    # (densidad ≤ 25% del bbox) para no marcar fotos o zonas oscuras como firma.
    if not boxes and len(words) < 5 and not any(_FIRMA_KW_RE.match(t) for t, *_ in words):
        b = scan(0, 0, W, H)
        if b:
            bx0, by0, bx1, by1 = b
            region = gray.crop(b).point(lambda v: 255 if v < 100 else 0)
            dark = sum(1 for v in region.getdata() if v > 0)
            if dark / max((bx1 - bx0) * (by1 - by0), 1) <= 0.25:
                boxes.append(b)

    return boxes


def _redact_image(img, words, codes, stats) -> "Image":
    """words: (texto, x0, y0, x1, y1) en píxeles de img. Pinta negro sobre las palabras detectadas."""
    from PIL import ImageDraw
    text, idx_at = _words_text_map(words)
    spans = detect(text, codes)
    _count(spans, stats)
    hit = set()
    email_last = set()
    for sp in spans:
        span_words = sorted({i for i in idx_at[sp.start:sp.end] if i is not None})
        hit.update(span_words)
        if sp.code == "EMAIL" and span_words:
            email_last.add(span_words[-1])
    sig_boxes = _signature_boxes(img, words) if "FIRMA" in codes else []
    if sig_boxes:
        stats["FIRMA"] = stats.get("FIRMA", 0) + len(sig_boxes)
    if not hit and not sig_boxes:
        return img
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img)
    for i in hit:
        _, x0, y0, x1, y1 = words[i]
        pad = max(1, int((y1 - y0) * 0.12))
        right_pad = max(pad, int((y1 - y0) * 1.8)) if i in email_last else pad
        draw.rectangle([x0 - pad, y0 - pad, x1 + right_pad, y1 + pad], fill="black")
    for b in sig_boxes:
        draw.rectangle(list(b), fill="black")
    return img


def pdf_word_boxes(page, img_width: int, img_height: int, pw: list) -> list:
    """Palabras de pdfplumber → píxeles de la página rasterizada por pdftoppm (MediaBox).

    Transformación afín, no escala pura: el MediaBox no siempre parte en (0,0) —recortes
    de Acrobat/Vista Previa, archivos de diseño, extracción de páginas, sangrado— y con
    escala sola las cajas quedaban corridas ~70% de una línea: tapaban el tercio superior
    del carácter y el dato seguía legible. Los signos están medidos, no deducidos
    (tests/test_geometria_pdf.py): en X se resta el origen, al `top` de pdfplumber se
    suma, y con rotación impar (90/270) los componentes del origen se intercambian.
    Única fuente de esta regla: la usan la extracción (vista de revisión + hallazgos
    persistidos) y la ruta directa /api/v1/redact; separadas se desalinean entre sí.
    """
    sx, sy = img_width / float(page.width), img_height / float(page.height)
    try:
        mb = page.page_obj.mediabox
        ox, oy = float(mb[0]), float(mb[1])
    except Exception:  # noqa: BLE001 — sin MediaBox legible se asume origen (0,0)
        ox = oy = 0.0
    if (getattr(page, "rotation", 0) or 0) % 180:
        ox, oy = oy, ox
    return [(w["text"], int((w["x0"] - ox) * sx), int((w["top"] + oy) * sy),
             int((w["x1"] - ox) * sx) + 1, int((w["bottom"] + oy) * sy) + 1) for w in pw]


def _redact_pdf(src: Path, dst: Path, codes, stats, dpi: int = 200):
    import img2pdf
    import pdfplumber
    from pdf2image import convert_from_path

    try:
        pdf = pdfplumber.open(src)
    except Exception as e:  # cifrado / corrupto
        raise EngineError("CORRUPT_FILE", f"No se pudo abrir el PDF: {e}")
    if pdf.metadata is None:
        pass
    pages_out = []
    images = convert_from_path(str(src), dpi=dpi)
    stats["pages"] = len(images)
    stats["ocr_pages"] = 0
    for page, img in zip(pdf.pages, images):
        pw = page.extract_words(use_text_flow=True) or []
        alnum = sum(ch.isalnum() for w in pw for ch in w["text"])
        if alnum >= 30:
            words = pdf_word_boxes(page, img.width, img.height, pw)
        else:
            stats["ocr_pages"] += 1
            words = _ocr_words(img)
        out = _redact_image(img, words, codes, stats)
        buf = io.BytesIO()
        out.convert("RGB").save(buf, format="JPEG", quality=85)
        pages_out.append(buf.getvalue())
    pdf.close()
    # Salida PDF/A-1b formada por imágenes, sin la capa de texto del original.
    dst.write_bytes(img2pdf.convert(pages_out, pdfa=SRGB_ICC))


def _redact_imagefile(src: Path, dst: Path, codes, stats):
    from PIL import Image, ImageSequence
    im = Image.open(src)
    frames = []
    for frame in ImageSequence.Iterator(im):
        f = frame.convert("RGB")
        frames.append(_redact_image(f, _ocr_words(f), codes, stats))
    stats["pages"] = len(frames)
    stats["ocr_pages"] = len(frames)
    ext = dst.suffix.lower()
    if ext in (".tif", ".tiff") and len(frames) > 1:
        frames[0].save(dst, save_all=True, append_images=frames[1:], compression="tiff_deflate")
    else:
        # sin EXIF/XMP: Pillow no copia metadatos al no pasarlos
        frames[0].save(dst, quality=90) if ext in (".jpg", ".jpeg") else frames[0].save(dst)


# ---- DOCX -------------------------------------------------------------------

def _docx_paras(d):
    """Itera (location, [w:t...]) de forma determinista: cuerpo + cabeceras/pies."""
    from docx.oxml.ns import qn
    roots = [("body", d.element.body)]
    for si, s in enumerate(d.sections):
        for name, part in (("header", s.header), ("footer", s.footer), ("first_header", s.first_page_header),
                           ("first_footer", s.first_page_footer), ("even_header", s.even_page_header),
                           ("even_footer", s.even_page_footer)):
            try:
                roots.append((f"{name}/{si}", part._element))
            except Exception:
                pass
    for prefix, root in roots:
        for i, p in enumerate(root.iter(qn("w:p"))):
            ts = list(p.iter(qn("w:t")))
            if ts:
                yield f"{prefix}/p/{i}", ts


_MAILTO_RE = re.compile(r"^\s*mailto:", re.IGNORECASE)
_RT_HYPERLINK = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"


def _docx_strip_mailto(d) -> int:
    """El texto visible del hipervínculo se tacha, pero su destino (`mailto:...`) viaja en
    los `.rels` del paquete y sobrevive intacto: se lee con abrir el .docx como ZIP.
    Aquí se neutraliza el destino en todas las partes (cuerpo, cabeceras y pies) sin tocar
    el texto. Devuelve cuántos enlaces se neutralizaron."""
    try:
        partes = list(d.part.package.iter_parts())
    except Exception:  # noqa: BLE001 — versiones antiguas de python-docx
        partes = [d.part]
    n = 0
    for parte in partes:
        try:
            rels = list(parte.rels.values())
        except Exception:  # noqa: BLE001
            continue
        for rel in rels:
            if not rel.is_external or rel.reltype != _RT_HYPERLINK:
                continue
            if _MAILTO_RE.match(str(rel._target or "")):
                rel._target = "about:blank"
                n += 1
    return n


def _docx_clean_props(d):
    cp = d.core_properties
    for attr in ("author", "last_modified_by", "title", "subject", "keywords", "comments", "category"):
        setattr(cp, attr, "")


def _redact_docx(src: Path, dst: Path, codes, stats):
    import docx

    d = docx.Document(str(src))
    for _loc, ts in _docx_paras(d):
            full = "".join(t.text or "" for t in ts)
            new, spans = redact_text(full, codes)
            _count(spans, stats)
            if not spans:
                continue
            pos = 0
            for t in ts:
                n = len(t.text or "")
                t.text = new[pos:pos + n]
                pos += n
    if "EMAIL" in set(codes):
        enlaces = _docx_strip_mailto(d)
        if enlaces:
            stats["enlaces_mailto"] = stats.get("enlaces_mailto", 0) + enlaces
    _docx_clean_props(d)
    d.save(str(dst))


# ---- XLSX -------------------------------------------------------------------

def _redact_xlsx(src: Path, dst: Path, codes, stats):
    import openpyxl
    wb = openpyxl.load_workbook(str(src))
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    new, spans = redact_text(cell.value, codes)
                    _count(spans, stats)
                    if spans:
                        cell.value = new
                if cell.comment is not None:
                    cell.comment = None
    for attr in ("creator", "lastModifiedBy", "title", "subject", "description", "keywords"):
        setattr(wb.properties, attr, "")  # con None, openpyxl escribe "openpyxl" como creator
    wb.save(str(dst))


# ---- TXT --------------------------------------------------------------------

def _redact_txt(src: Path, dst: Path, codes, stats):
    raw = src.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    new, spans = redact_text(text, codes)
    _count(spans, stats)
    dst.write_text(new, encoding="utf-8")


# ---- DOC / XLS (vía LibreOffice) ---------------------------------------------

def _soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice") or (
        "/Applications/LibreOffice.app/Contents/MacOS/soffice" if Path("/Applications/LibreOffice.app/Contents/MacOS/soffice").exists() else None)


def _convert(src: Path, fmt: str, outdir: Path) -> Path:
    exe = _soffice()
    if not exe:
        raise EngineError("UNSUPPORTED_FORMAT", "Formato DOC/XLS requiere LibreOffice instalado en el servidor.")
    subprocess.run([exe, "--headless", "--convert-to", fmt, "--outdir", str(outdir), str(src)],
                   check=True, capture_output=True, timeout=180)
    out = outdir / (src.stem + "." + fmt)
    if not out.exists():
        raise EngineError("CONVERSION_FAILED", "LibreOffice no generó el archivo convertido.")
    return out


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def redact_file(src: Path, dst: Path, codes: list[str]) -> dict:
    """Tacha `src` y escribe `dst` (mismo formato). Devuelve estadísticas {codigo: n, pages, ...}."""
    src, dst = Path(src), Path(dst)
    ext = src.suffix.lower()
    if ext not in SUPPORTED:
        raise EngineError("UNSUPPORTED_FORMAT", f"Formato no soportado: {ext or '(sin extensión)'}")
    stats: dict = {}
    dst.parent.mkdir(parents=True, exist_ok=True)
    if ext == ".pdf":
        _redact_pdf(src, dst, codes, stats)
    elif ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
        _redact_imagefile(src, dst, codes, stats)
    elif ext == ".docx":
        _redact_docx(src, dst, codes, stats)
    elif ext == ".xlsx":
        _redact_xlsx(src, dst, codes, stats)
    elif ext == ".txt":
        _redact_txt(src, dst, codes, stats)
    elif ext in (".doc", ".xls"):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            modern = _convert(src, "docx" if ext == ".doc" else "xlsx", td)
            clean = td / ("clean" + modern.suffix)
            (_redact_docx if ext == ".doc" else _redact_xlsx)(modern, clean, codes, stats)
            back = _convert(clean, ext[1:], td)
            shutil.copy(back, dst)
    stats["engine_version"] = ENGINE_VERSION
    stats["total"] = sum(v for k, v in stats.items() if k in ENTITY_TYPES)
    return stats
