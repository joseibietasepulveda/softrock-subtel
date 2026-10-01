#!/usr/bin/env python3
"""Corpus sintético de documentos legales chilenos para test de esfuerzo.

Genera 50 documentos (resoluciones, dictámenes, oficios, actas, sentencias,
contratos, planillas, notificaciones) en 8 formatos y 5 rangos de largo, con
manifest.csv y ground_truth.json del PII inyectado.

Uso: .venv/bin/python tools/gen_corpus.py [destino]

Todo el PII es ficticio: dominios .test (RFC 6761), IPs RFC 5737, tarjetas de
prueba, RUT con dígito verificador válido pero cuerpo inventado.
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
import string as _string
import sys
import textwrap
from pathlib import Path

SEED = 20260830
ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "corpus-tachado-sintetico"

# ---------------------------------------------------------------------------
# Pools (todo ficticio)
# ---------------------------------------------------------------------------

NOMBRES_M = ["Matías", "Ignacio", "Tomás", "Javier", "Rodrigo", "Cristóbal", "Sebastián",
             "Gonzalo", "Andrés", "Nicolás", "Felipe", "Álvaro", "Esteban", "Patricio", "Hernán"]
NOMBRES_F = ["Daniela", "Camila", "Sofía", "Valentina", "Fernanda", "Antonia", "Macarena",
             "Paulina", "Constanza", "Bárbara", "Josefa", "Catalina", "Marcela", "Romina", "Ximena"]
APELLIDOS = ["Rojas", "Contreras", "Soto", "Díaz", "Pérez", "Silva", "Fuentes", "Núñez",
             "González", "Vergara", "Cáceres", "Bustamante", "Salazar", "Cornejo", "Riquelme",
             "Aravena", "Zúñiga", "Peñailillo", "Ibáñez", "Mardones", "Villalobos", "Tapia"]
EMPRESAS = ["Telecomunicaciones Austral SpA", "Redes del Pacífico Limitada",
            "Andes Conectividad S.A.", "Fibra Sur Comunicaciones SpA", "Enlace Digital Ltda.",
            "Corporación Móvil del Norte S.A.", "Servicios Satelitales Patagonia SpA",
            "Banda Ancha Regional Limitada"]
COMUNAS = ["Providencia", "Ñuñoa", "Maipú", "Puente Alto", "La Florida", "Valparaíso",
           "Concepción", "Temuco", "Antofagasta", "Punta Arenas", "Rancagua", "Chillán",
           "Iquique", "Osorno", "Coyhaique", "Arica", "Talca", "Valdivia", "Curicó", "Copiapó"]
CALLES = ["Avenida Los Carrera", "Calle Serrano", "Pasaje Los Aromos", "Avenida Alemania",
          "Calle Prat", "Camino El Roble", "Avenida Costanera", "Calle Bulnes",
          "Pasaje Las Acacias", "Avenida Portales", "Calle Freire", "Avenida Pedro Aguirre Cerda"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
NORMAS = ["Ley N° 18.168, General de Telecomunicaciones",
          "Ley N° 19.880, sobre bases de los procedimientos administrativos",
          "Ley N° 21.719, sobre protección y tratamiento de datos personales",
          "Ley N° 20.285, sobre acceso a la información pública",
          "Ley N° 19.628, sobre protección de la vida privada",
          "Ley N° 18.575, orgánica constitucional de bases generales de la Administración del Estado",
          "Decreto Supremo N° 18, de 2014, del Ministerio de Transportes y Telecomunicaciones",
          "Resolución N° 7, de 2019, de la Contraloría General de la República"]
SERVICIOS = ["servicio público telefónico", "servicio de acceso a Internet fijo",
             "servicio de acceso a Internet móvil", "servicio limitado de televisión",
             "servicio intermedio de telecomunicaciones", "servicio público de voz sobre IP",
             "servicio de radiodifusión sonora en frecuencia modulada"]
CARGOS = ["Jefe de la División Fiscalización", "Jefa del Departamento Jurídico",
          "Fiscalizador de la Oficina Regional", "Subsecretaria de Telecomunicaciones (S)",
          "Ministro de Fe", "Secretaria Regional Ministerial", "Abogado de la Unidad de Sanciones"]

VISTOS = [
    "Lo dispuesto en la {norma};",
    "El Decreto Ley N° 1.762, de 1977, que crea la Subsecretaría de Telecomunicaciones;",
    "Lo prescrito en la {norma}, y sus modificaciones posteriores;",
    "El Reglamento del Servicio Público Telefónico, y demás normativa técnica aplicable;",
    "La necesidad de resguardar la continuidad y calidad de los servicios de telecomunicaciones;",
    "La delegación de facultades contenida en la Resolución Exenta N° {num}, de esta Subsecretaría;",
]

# Considerandos con PII. Los slots entre llaves se registran como ground truth.
CONS_PII = [
    "Que, con fecha {fec}, {tr} {per}, cédula nacional de identidad N° {rut}, domiciliado en "
    "{dir}, comuna de {com}, presentó ante esta Subsecretaría un reclamo por presuntas fallas "
    "en el {serv} contratado con {emp}.",
    "Que la presentación indicada fue ingresada bajo el folio N° {fol} y notificada al correo "
    "electrónico {mail}, sin que a la fecha conste respuesta de la concesionaria.",
    "Que, requerida la reclamante al teléfono {tel}, ratificó los hechos expuestos en su "
    "presentación y acompañó los antecedentes que obran en el expediente.",
    "Que consta en autos el acta de fiscalización de fecha {fec2}, suscrita por {func2} "
    "{per2}, en la que se dejó constancia de las deficiencias técnicas detectadas en el nodo "
    "emplazado en {dir}.",
    "Que, según el registro técnico acompañado, el equipo terminal asociado a la conexión "
    "reclamada registra la dirección IP {ip}, la que fue verificada por personal de la División "
    "Fiscalización con fecha {fec3}.",
    "Que el vehículo institucional patente {pat} fue utilizado en la diligencia de inspección "
    "practicada en terreno el día {fec2}, según consta en la hoja de ruta respectiva.",
    "Que el pago de la multa deberá enterarse en la cuenta corriente N° {cta}, del Banco del "
    "Estado de Chile, a nombre de la Tesorería General de la República.",
    "Que el interesado acompañó copia de su pasaporte N° {pas}, por tratarse de un ciudadano "
    "extranjero que no cuenta con cédula de identidad chilena vigente.",
    "Que el solicitante acreditó su identidad señalando su fecha de nacimiento: {nac}, dato "
    "coincidente con los registros del Servicio de Registro Civil e Identificación.",
    "Que la resolución que se dicta será publicada en el sitio {web}, en cumplimiento de las "
    "obligaciones de transparencia activa que pesan sobre este Servicio.",
    "Que el pago de los derechos se efectuó mediante tarjeta N° {tar}, según comprobante "
    "acompañado a fojas {num} del expediente administrativo.",
    "Que {tr} {per}, RUT {rut}, en su calidad de representante legal de {emp}, evacuó los "
    "descargos con fecha {fec2}, solicitando el rechazo de los cargos formulados.",
    "Que la notificación fue practicada por carta certificada dirigida a {dir}, comuna de "
    "{com}, con fecha {fec3}, entendiéndose practicada al tercer día hábil siguiente.",
    "Que quien instruye el procedimiento, {tr2} {per2}, propuso en su vista fiscal la aplicación de una "
    "multa ascendente a {mon} unidades tributarias mensuales.",
    "Que, sin perjuicio de lo anterior, {tr} {per}, en su calidad de usuario(a), manifestó su conformidad con la "
    "solución técnica ofrecida, según consta en el correo de fecha {fec2} remitido desde {mail}.",
]

# Considerandos de relleno: sin datos personales.
CONS_FILL = [
    "Que, en consecuencia, corresponde analizar si los antecedentes reunidos en el expediente "
    "permiten tener por configurada la infracción imputada a la concesionaria.",
    "Que el artículo {art} de la {norma} impone a las concesionarias el deber de mantener la "
    "continuidad del {serv}, sin perjuicio de las interrupciones programadas que se informen "
    "con la antelación que el reglamento señala.",
    "Que la jurisprudencia administrativa de la Contraloría General de la República ha "
    "sostenido, de manera reiterada, que los actos administrativos deben bastarse a sí mismos y "
    "expresar los fundamentos de hecho y de derecho que los sustentan.",
    "Que el principio de proporcionalidad exige que la sanción guarde correspondencia con la "
    "entidad de la infracción, la extensión del daño causado y la conducta anterior del "
    "infractor.",
    "Que la interrupción afectó a {num} usuarios de la comuna respectiva, según los registros "
    "técnicos remitidos por la concesionaria en cumplimiento de la normativa vigente.",
    "Que el procedimiento administrativo se ha desarrollado con estricta sujeción a los "
    "principios de contradictoriedad e imparcialidad consagrados en la {norma}.",
    "Que resulta pertinente tener presente que el plazo de {plazo} días hábiles establecido "
    "para formular descargos se encuentra vencido a la fecha de dictación del presente acto.",
    "Que la División Fiscalización informó que la concesionaria no acreditó la ejecución de las "
    "medidas correctivas comprometidas en el plan de acción respectivo.",
    "Que los antecedentes técnicos permiten concluir que la degradación del servicio obedeció a "
    "una falla en el equipamiento de acceso y no a un caso fortuito o fuerza mayor.",
    "Que corresponde desestimar la alegación relativa a la caducidad del procedimiento, toda "
    "vez que las actuaciones se practicaron dentro de los plazos legales.",
    "Que la potestad sancionatoria de este Servicio se ejerce sin perjuicio de las acciones "
    "civiles o penales que pudieren corresponder conforme al ordenamiento jurídico.",
    "Que las alegaciones de la concesionaria relativas a la falta de tipicidad de la conducta "
    "deben ser desestimadas, atendido el claro tenor del artículo {art} de la {norma}.",
    "Que la reincidencia en conductas de la misma especie constituye una circunstancia agravante "
    "que debe ser ponderada al momento de determinar el quantum de la sanción.",
    "Que se ha dado cumplimiento al trámite de audiencia previa, garantizándose el debido "
    "proceso administrativo en todas sus etapas.",
    "Que la información técnica acompañada fue analizada por la unidad especializada, la que "
    "concluyó que los indicadores de calidad de servicio se mantuvieron bajo el umbral exigido "
    "durante el período fiscalizado.",
    "Que el mérito del expediente permite formar convicción respecto de la efectividad de los "
    "hechos materia del presente procedimiento sancionatorio.",
    "Que, atendida la naturaleza del {serv}, resulta exigible un estándar de diligencia "
    "reforzado en la mantención de la infraestructura crítica de telecomunicaciones.",
    "Que no obsta a lo anterior la circunstancia de haberse restablecido el servicio con "
    "posterioridad al inicio del procedimiento, sin perjuicio de su ponderación como atenuante.",
]

RESUELVO_PII = [
    "APLÍCASE a {emp} una multa de {mon} unidades tributarias mensuales por la infracción "
    "descrita en los considerandos precedentes.",
    "NOTIFÍQUESE la presente resolución a {tr} {per}, RUT {rut}, en el domicilio ubicado en "
    "{dir}, comuna de {com}, y al correo electrónico {mail}.",
    "TÉNGASE por acogido el reclamo folio N° {fol} presentado con fecha {fec}, debiendo la "
    "concesionaria informar las medidas adoptadas dentro de {plazo} días hábiles.",
    "DESÍGNASE como fiscal instructor del procedimiento a {tr2} {per2}, funcionario(a) de esta "
    "Subsecretaría, quien deberá evacuar su informe a más tardar el {fec3}.",
]
RESUELVO_FILL = [
    "DÉJASE constancia que en contra del presente acto administrativo procede el recurso de "
    "reposición, dentro del plazo de cinco días hábiles contado desde su notificación.",
    "PUBLÍQUESE un extracto de la presente resolución en el sitio electrónico institucional, "
    "conforme a la normativa de transparencia activa.",
    "REQUIÉRASE a la concesionaria la remisión de los antecedentes técnicos del período "
    "fiscalizado, en formato electrónico, dentro del plazo de {plazo} días hábiles.",
    "INSTRÚYESE a la División Fiscalización para que verifique el cumplimiento de lo resuelto e "
    "informe a la brevedad a la autoridad respectiva.",
    "ARCHÍVESE el expediente una vez ejecutoriada la presente resolución.",
]

# ---------------------------------------------------------------------------
# Fábricas de datos
# ---------------------------------------------------------------------------

def _rut(rng):
    body = rng.randint(5_000_000, 24_999_999)
    s, m = 0, 2
    for d in reversed(str(body)):
        s += int(d) * m
        m = 2 if m == 7 else m + 1
    r = 11 - s % 11
    dv = "0" if r == 11 else "K" if r == 10 else str(r)
    return f"{body:,}".replace(",", ".") + "-" + dv


def _sin_tildes(s):
    tab = str.maketrans("áéíóúñÁÉÍÓÚÑ", "aeiounAEIOUN")
    return s.translate(tab)


def _fecha(rng, y=(2024, 2026)):
    d, m, a = rng.randint(1, 28), rng.randint(0, 11), rng.randint(*y)
    if rng.random() < 0.45:
        return f"{d:02d}/{m + 1:02d}/{a}"
    return f"{d} de {MESES[m]} de {a}"


PLATE_L = "BCDFGHJKLPRSTVWXYZ"


def _persona(rng):
    """Nombre + tratamiento concordante (don/doña), para que el texto lea natural."""
    m = rng.random() < 0.5
    nom = rng.choice(NOMBRES_M if m else NOMBRES_F)
    return (f"{nom} {rng.choice(APELLIDOS)} {rng.choice(APELLIDOS)}",
            "don" if m else "doña", "el funcionario" if m else "la funcionaria")


def slots(rng):
    """Valores concretos para un documento. Se registran al usarse."""
    per, tr, func = _persona(rng)
    per2, tr2, func2 = _persona(rng)
    mail = _sin_tildes(per.lower().replace(" ", ".")) + rng.choice(["@correo.test", "@example.test", "@mail.test"])
    return {
        "per": per, "per2": per2, "tr": tr, "tr2": tr2, "func": func, "func2": func2, "rut": _rut(rng), "rut2": _rut(rng), "mail": mail,
        "tel": rng.choice([f"+56 9 {rng.randint(3000, 9999)} {rng.randint(1000, 9999)}",
                           f"+56 2 2{rng.randint(100, 999)} {rng.randint(1000, 9999)}",
                           f"(2) 2{rng.randint(100, 999)} {rng.randint(1000, 9999)}"]),
        "dir": f"{rng.choice(CALLES)} N° {rng.randint(120, 4899)}, oficina {rng.randint(11, 908)}",
        "fec": _fecha(rng), "fec2": _fecha(rng), "fec3": _fecha(rng), "fec4": _fecha(rng),
        "nac": _fecha(rng, (1955, 2003)),
        "fol": rng.choice([f"2026-{rng.randint(1000, 99999):06d}", f"A-{rng.randint(1000, 9999)}/2026",
                           f"{rng.randint(100000, 999999)}"]),
        "ip": rng.choice([f"192.0.2.{rng.randint(2, 250)}", f"198.51.100.{rng.randint(2, 250)}",
                          f"203.0.113.{rng.randint(2, 250)}"]),
        "pat": rng.choice(["".join(rng.choice(PLATE_L) for _ in range(4)) + f" {rng.randint(10, 99)}",
                           "".join(rng.choice(PLATE_L) for _ in range(2)) + f" {rng.randint(1000, 9999)}"]),
        "cta": f"000{rng.randint(1000000, 9999999)}{rng.randint(10, 99)}",
        "pas": "P" + "".join(rng.choice(_string.digits) for _ in range(7)),
        "tar": rng.choice(["4111 1111 1111 1111", "5555 5555 5555 4444", "4012 8888 8888 1881"]),
        "web": rng.choice(["https://www.tramites.example.test/consulta/2026",
                           "https://perfil.example.test/usuario/registro", "@usuario_ficticio_test"]),
        # No-PII
        "emp": rng.choice(EMPRESAS), "com": rng.choice(COMUNAS), "norma": rng.choice(NORMAS),
        "serv": rng.choice(SERVICIOS), "mon": str(rng.choice([25, 50, 75, 100, 150, 200])),
        "num": str(rng.randint(12, 980)), "art": str(rng.randint(3, 45)),
        "plazo": rng.choice(["cinco", "diez", "quince", "veinte"]),
        "cargo": rng.choice(CARGOS), "cargo2": rng.choice(CARGOS),
    }

CODE_OF = {"per": "NOMBRE", "per2": "NOMBRE", "rut": "RUT", "rut2": "RUT", "mail": "EMAIL",
           "tel": "TELEFONO", "dir": "DIRECCION", "fec": "FECHA", "fec2": "FECHA",
           "fec3": "FECHA", "fec4": "FECHA", "nac": "FECHA_NACIMIENTO", "fol": "FOLIO",
           "ip": "IP", "pat": "PATENTE", "cta": "CUENTA_BANCARIA", "pas": "PASAPORTE",
           "tar": "TARJETA", "web": "WEB_REDES"}


class _Reg(dict):
    """dict de slots que registra cada acceso como una ocurrencia de PII."""

    def __init__(self, vals, doc):
        super().__init__(vals)
        self.doc = doc

    def __getitem__(self, k):
        v = super().__getitem__(k)
        if k in CODE_OF:
            self.doc.pii.append((CODE_OF[k], v))
        return v


class Doc:
    def __init__(self, kind, size, rng, nopii=False):
        self.kind, self.size, self.rng, self.nopii = kind, size, rng, nopii
        self.blocks: list[tuple] = []
        self.firma_rotulada = None  # sólo aplica a formatos de imagen
        self.pii: list[tuple[str, str]] = []
        self.v = slots(rng)
        self.s = _Reg(self.v, self)

    def f(self, tpl):
        return _string.Formatter().vformat(tpl, (), self.s)

    def h(self, t):
        self.blocks.append(("h", self.f(t)))

    def p(self, t):
        self.blocks.append(("p", self.f(t)))

    def t(self, rows):
        self.blocks.append(("t", [[self.f(str(c)) for c in r] for r in rows]))

    def sig(self, nombre_tpl, cargo_tpl):
        self.blocks.append(("sig", (self.f(nombre_tpl), self.f(cargo_tpl))))

    def reroll(self):
        """Datos nuevos (persona, RUT, fechas...) para la siguiente fila/párrafo."""
        self.s.update(slots(self.rng))

    def trows(self, header, tpl_rows):
        """Tabla en que cada fila estrena datos personales distintos."""
        out = [[self.f(str(c)) for c in header]]
        for r in tpl_rows:
            self.reroll()
            out.append([self.f(str(c)) for c in r])
        self.blocks.append(("t", out))

    @property
    def text(self):
        out = []
        for k, v in self.blocks:
            if k in ("h", "p"):
                out.append(v)
            elif k == "t":
                out += [" ".join(r) for r in v]
            else:
                out += list(v)
        return "\n".join(out)

    @property
    def words(self):
        return len(self.text.split())


# ---------------------------------------------------------------------------
# Constructores de documentos
# ---------------------------------------------------------------------------

N_ITEMS = {"XS": 2, "S": 6, "M": 18, "L": 58, "XL": 380}


def _cuerpo(d, n, pii_ratio):
    """n considerandos numerados; pii_ratio ~ fracción con datos personales."""
    for i in range(n):
        if n >= 30 and i and i % 14 == 0:
            d.reroll()  # documentos largos: nuevos intervinientes por sección
        pool = CONS_PII if (not d.nopii and d.rng.random() < pii_ratio) else CONS_FILL
        d.p(f"{i + 1}.- " + d.rng.choice(pool))


def _ratio(d, n):
    if d.nopii:
        return 0.0
    return max(0.10, min(1.0, 14 / max(n, 1)))


def doc_resolucion(d):
    n = N_ITEMS[d.size]
    d.h("SUBSECRETARÍA DE TELECOMUNICACIONES")
    d.h("RESOLUCIÓN EXENTA N° {num}")
    d.p("Santiago, marzo de 2026." if d.nopii else "Santiago, {fec}.")
    d.h("VISTOS:")
    for v in d.rng.sample(VISTOS, k=min(4, len(VISTOS))):
        d.p("- " + v)
    d.h("CONSIDERANDO:")
    _cuerpo(d, n, _ratio(d, n))
    d.h("RESUELVO:")
    for i in range(max(2, n // 12)):
        pool = RESUELVO_FILL if d.nopii or d.rng.random() < 0.5 else RESUELVO_PII
        d.p(f"{i + 1}°.- " + d.rng.choice(pool))
    d.p("ANÓTESE, NOTIFÍQUESE Y ARCHÍVESE.")
    if d.nopii:
        d.sig("SUBSECRETARÍA DE TELECOMUNICACIONES", "Ministerio de Transportes y Telecomunicaciones")
    else:
        d.sig("{per2}", "{cargo}")


def doc_dictamen(d):
    n = N_ITEMS[d.size]
    d.h("CONTRALORÍA GENERAL DE LA REPÚBLICA")
    d.h("DIVISIÓN JURÍDICA")
    d.p("DICTAMEN N° {num}.{art}, de 2026." if d.nopii else "DICTAMEN N° {num}.{art}, de {fec}.")
    d.p("Se ha dirigido a esta Entidad de Control una presentación mediante la cual se solicita "
        "un pronunciamiento acerca de la juridicidad del procedimiento sancionatorio aplicado "
        "por la Subsecretaría de Telecomunicaciones en la materia que se indica.")
    d.h("ANTECEDENTES:")
    _cuerpo(d, n, _ratio(d, n))
    d.h("CONCLUSIÓN:")
    d.p("En mérito de lo expuesto, cumple esta Contraloría General con manifestar que el acto "
        "administrativo examinado se ajusta a derecho, sin perjuicio de las observaciones "
        "formuladas en los párrafos precedentes, las que deberán ser subsanadas dentro del "
        "plazo de {plazo} días hábiles.")
    d.p("Transcríbase a la Subsecretaría de Telecomunicaciones y a la Unidad de Seguimiento.")
    d.sig("SUBSECRETARÍA DE TELECOMUNICACIONES" if d.nopii else "{per2}",
          "Abogado, División Jurídica" if d.nopii else "{cargo2}")


def doc_oficio(d):
    n = N_ITEMS[d.size]
    d.h("OFICIO ORDINARIO N° {num}")
    d.p("ANT.: Presentación folio N° {fol}." if not d.nopii else "ANT.: Presentación ciudadana.")
    d.p("MAT.: Informa sobre fiscalización del {serv}.")
    d.p("Santiago, marzo de 2026." if d.nopii else "Santiago, {fec}.")
    d.p("DE: Subsecretaría de Telecomunicaciones")
    d.p("A : Gerencia General, {emp}")
    d.p("Junto con saludar, y en relación con la materia del antecedente, cumplo con informar a "
        "usted lo siguiente:")
    _cuerpo(d, n, _ratio(d, n))
    d.p("Sin otro particular, saluda atentamente a usted,")
    d.sig("SUBSECRETARÍA DE TELECOMUNICACIONES" if d.nopii else "{per2}",
          "Jefatura de División" if d.nopii else "{cargo}")


def doc_acta(d):
    n = max(3, N_ITEMS[d.size] // 3)
    d.h("ACTA DE FISCALIZACIÓN EN TERRENO N° {num}")
    d.p("En {com}, a {fec}, siendo las 10:30 horas, el funcionario fiscalizador que suscribe se "
        "constituyó en el domicilio ubicado en {dir}, con el objeto de verificar el cumplimiento "
        "de las condiciones técnicas del {serv} prestado por {emp}.")
    d.p("Atendió la diligencia {tr} {per}, cédula de identidad N° {rut}, quien exhibió los "
        "antecedentes que se detallan en la tabla siguiente.")
    rows = [["N°", "Hallazgo", "Referencia normativa", "Fecha", "Responsable"]]
    for i in range(n):
        rows.append([str(i + 1),
                     d.rng.choice(["Potencia fuera de norma", "Falta de señalética",
                                   "Registro de continuidad incompleto", "Equipo sin certificación",
                                   "Antena sin memoria técnica", "Reclamo no tramitado"]),
                     "Art. {art} " + d.rng.choice(["Ley N° 18.168", "DS N° 18/2014"]),
                     "{fec2}" if i % 3 == 0 else "{fec3}",
                     "{per2}" if i % 2 == 0 else "{per}"])
    d.t(rows)
    _cuerpo(d, max(2, N_ITEMS[d.size] // 4), _ratio(d, N_ITEMS[d.size]))
    d.p("Se deja constancia que el representante fue notificado en el acto, en el correo {mail} "
        "y al teléfono {tel}, del plazo de {plazo} días hábiles para formular descargos.")
    d.p("Leída el acta, firman los comparecientes.")
    d.sig("{per2}", "{cargo}")


def doc_sentencia(d):
    n = N_ITEMS[d.size]
    d.h("JUZGADO DE POLICÍA LOCAL DE {com}")
    d.p("ROL N° {num}-2026")
    d.p("{com}, {fec}." if not d.nopii else "{com}, marzo de 2026.")
    d.h("VISTOS:")
    d.p("Se inició esta causa por denuncia de {tr} {per}, RUT {rut}, domiciliado en {dir}, en "
        "contra de {emp}, representada legalmente por {tr2} {per2}, ambos domiciliados para estos "
        "efectos en la comuna de {com}.")
    d.h("CONSIDERANDO:")
    _cuerpo(d, n, _ratio(d, n))
    d.h("Y VISTO además lo dispuesto en la Ley N° 18.287 y en la {norma}, SE RESUELVE:")
    d.p("I.- Que se acoge la denuncia deducida y se condena a la denunciada al pago de una multa "
        "de {mon} unidades tributarias mensuales, a beneficio municipal.")
    d.p("II.- Que se condena en costas a la parte vencida.")
    d.p("Anótese, notifíquese y archívese en su oportunidad.")
    d.sig("{per2}", "Juez de Policía Local")


def doc_contrato(d):
    n = max(6, N_ITEMS[d.size] // 4)
    ordinales = ["PRIMERA", "SEGUNDA", "TERCERA", "CUARTA", "QUINTA", "SEXTA", "SÉPTIMA",
                 "OCTAVA", "NOVENA", "DÉCIMA", "UNDÉCIMA", "DUODÉCIMA"]
    d.h("CONTRATO DE PRESTACIÓN DE SERVICIOS")
    d.p("En Santiago de Chile, a {fec}, entre la SUBSECRETARÍA DE TELECOMUNICACIONES, RUT "
        "{rut2}, representada por {tr2} {per2}, {cargo}, ambos domiciliados en Amunátegui 139, "
        "Santiago; y {emp}, representada legalmente por {tr} {per}, cédula nacional de identidad "
        "N° {rut}, con domicilio en {dir}, comuna de {com}, se ha convenido lo siguiente:")
    for i in range(min(n, len(ordinales))):
        d.h(f"CLÁUSULA {ordinales[i]}:")
        pool = CONS_PII if (not d.nopii and d.rng.random() < 0.35) else CONS_FILL
        d.p(d.rng.choice(pool))
        d.p(d.rng.choice(CONS_FILL))
    d.p("Para todos los efectos legales las partes fijan domicilio en la comuna de {com} y se "
        "someten a la competencia de sus tribunales de justicia. El presente instrumento se "
        "firma en dos ejemplares de igual tenor y fecha.")
    d.p("Las notificaciones se practicarán al correo {mail} y al teléfono {tel}.")
    d.sig("{per}", "Representante legal, {emp}")


def doc_reclamo(d):
    n = max(2, N_ITEMS[d.size] // 6)
    d.h("PRESENTACIÓN CIUDADANA - RECLAMO POR CALIDAD DE SERVICIO")
    d.p("Señor(a) Subsecretario(a) de Telecomunicaciones")
    d.p("PRESENTE")
    d.p("Yo, {per}, cédula nacional de identidad N° {rut}, fecha de nacimiento: {nac}, con "
        "domicilio en {dir}, comuna de {com}, correo electrónico {mail} y teléfono {tel}, vengo "
        "en presentar el siguiente reclamo en contra de {emp}:")
    _cuerpo(d, n, 0.9 if not d.nopii else 0.0)
    d.p("Adjunto comprobante de pago efectuado con tarjeta N° {tar} y copia de la boleta "
        "asociada al folio N° {fol}.")
    d.p("Por tanto, solicito a usted tener por presentado este reclamo, acogerlo a tramitación y "
        "ordenar a la concesionaria la restitución del servicio.")
    d.sig("{per}", "RUT {rut}")


def doc_transparencia(d):
    n = max(4, N_ITEMS[d.size] // 5)
    d.h("RESPUESTA A SOLICITUD DE ACCESO A LA INFORMACIÓN PÚBLICA")
    d.p("Solicitud N° AK{num}-2026, Ley N° 20.285.")
    d.p("Santiago, {fec}." if not d.nopii else "Santiago, marzo de 2026.")
    d.p("En respuesta a su solicitud, se remite la información individualizada en la tabla "
        "siguiente, con las reservas del artículo 21 N° 2 de la citada ley respecto de los datos "
        "personales de terceros.")
    rows = []
    for i in range(n):
        rows.append(["{fol}", "{fec2}" if i % 2 else "{fec3}", "{per}" if i % 2 else "{per2}",
                     "{rut}" if i % 2 else "{rut2}",
                     d.rng.choice(["Cobertura móvil", "Multas cursadas", "Contratos vigentes",
                                   "Concesiones otorgadas", "Reclamos por comuna"]),
                     d.rng.choice(["Respondida", "En trámite", "Derivada"])])
    d.trows(["Folio", "Fecha ingreso", "Solicitante", "RUT", "Materia", "Estado"], rows)
    _cuerpo(d, n, _ratio(d, n))
    d.p("Se hace presente que contra esta respuesta procede amparo ante el Consejo para la "
        "Transparencia dentro del plazo de {plazo} días hábiles.")
    d.sig("{per2}", "{cargo2}")


def doc_memo(d):
    n = max(2, N_ITEMS[d.size] // 8)
    d.h("MEMORÁNDUM INTERNO N° {num}")
    d.p("DE: {cargo}")
    d.p("PARA: {cargo2}")
    d.p("FECHA: {fec}" if not d.nopii else "FECHA: marzo de 2026")
    d.p("MATERIA: Remite antecedentes para dictación de acto administrativo.")
    _cuerpo(d, n, 0.8 if not d.nopii else 0.0)
    d.p("Saluda atentamente,")
    d.sig("SUBSECRETARÍA DE TELECOMUNICACIONES" if d.nopii else "{per2}",
          "Jefatura" if d.nopii else "{cargo}")


def doc_notificacion(d):
    d.h("CARTA DE NOTIFICACIÓN")
    d.p("Santiago, {fec}.")
    d.p("Señor(a) {per}")
    d.p("RUT {rut}")
    d.p("{dir}, {com}")
    d.p("Por medio de la presente se notifica a usted la Resolución Exenta N° {num}, de esta "
        "Subsecretaría, que resolvió el reclamo folio N° {fol} presentado con fecha {fec2} en "
        "contra de {emp}, por deficiencias en el {serv}.")
    _cuerpo(d, max(2, N_ITEMS[d.size] // 8), 0.6)
    d.p("Cualquier consulta puede dirigirla al correo {mail} o al teléfono {tel}, o revisar el "
        "estado de su presentación en {web}.")
    d.sig("{per2}", "{cargo}")


def doc_planilla(d):
    n = {"XS": 6, "S": 18, "M": 70, "L": 320, "XL": 1200}[d.size]
    d.h("REGISTRO DE RECLAMOS Y SANCIONES - {com}")
    d.p("Planilla de seguimiento generada el {fec}. Uso interno." if not d.nopii
        else "Planilla de seguimiento. Uso interno.")
    rows = []
    for i in range(n):
        if d.nopii:
            rows.append([str(i + 1), "(reservado)", "(reservado)", "(reservado)", "(reservado)",
                         "(reservado)", "(reservado)", "(reservado)", "{emp}", "(reservado)",
                         d.rng.choice(["Cerrado", "Abierto"]), str(d.rng.randint(5, 200))])
            continue
        # PII disperso: sólo algunas filas traen todos los campos.
        dense = d.rng.random() < 0.5
        rows.append([
            str(i + 1), "{fol}", "{fec2}" if i % 2 else "{fec3}",
            "{per}" if dense else "{per2}", "{rut}" if dense else "{rut2}",
            "{mail}" if dense else "-", "{tel}" if dense else "-",
            "{dir}" if dense else "-", "{emp}", "{ip}" if not dense else "-",
            d.rng.choice(["Cerrado", "Abierto", "En análisis"]), str(d.rng.randint(5, 200)),
        ])
    d.trows(["N°", "Folio", "Fecha", "Reclamante", "RUT", "Correo", "Teléfono", "Domicilio",
             "Concesionaria", "IP registrada", "Estado", "Multa (UTM)"], rows)


BUILDERS = {"resolucion": doc_resolucion, "dictamen": doc_dictamen, "oficio": doc_oficio,
            "acta": doc_acta, "sentencia": doc_sentencia, "contrato": doc_contrato,
            "reclamo": doc_reclamo, "transparencia": doc_transparencia, "memo": doc_memo,
            "notificacion": doc_notificacion, "planilla": doc_planilla}


# ---------------------------------------------------------------------------
# Renderizadores
# ---------------------------------------------------------------------------

FONTS = [("/System/Library/Fonts/Supplemental/Times New Roman.ttf",
          "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf"),
         ("/System/Library/Fonts/Supplemental/Arial.ttf",
          "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
         ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf")]
PAGE_W, PAGE_H, MARGIN, FSIZE, LEAD = 1654, 2339, 150, 30, 46  # A4 @200 dpi


def _pick_font():
    for reg, bold in FONTS:
        if Path(reg).exists():
            return reg, bold if Path(bold).exists() else reg
    raise SystemExit("No se encontró una fuente TTF utilizable")


def render_txt(doc, path):
    out = []
    for k, v in doc.blocks:
        if k == "h":
            out += ["", v.upper(), "=" * min(len(v), 90), ""]
        elif k == "p":
            out += [textwrap.fill(v, 96), ""]
        elif k == "t":
            w = [max(len(str(r[i])) for r in v) for i in range(len(v[0]))]
            for j, r in enumerate(v):
                out.append("  ".join(str(c).ljust(w[i])[:60] for i, c in enumerate(r)))
                if j == 0:
                    out.append("-" * min(sum(w) + 2 * len(w), 200))
            out.append("")
        else:
            out += ["", "", f"    {v[0]}", f"    {v[1]}", ""]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return None


def render_docx(doc, path):
    from docx import Document
    from docx.shared import Pt
    dx = Document()
    dx.styles["Normal"].font.name = "Times New Roman"
    dx.styles["Normal"].font.size = Pt(11)
    for k, v in doc.blocks:
        if k == "h":
            dx.add_heading(v, level=2)
        elif k == "p":
            dx.add_paragraph(v)
        elif k == "t":
            tb = dx.add_table(rows=len(v), cols=len(v[0]))
            tb.style = "Table Grid"
            for i, row in enumerate(v):
                for j, cell in enumerate(row):
                    tb.cell(i, j).text = str(cell)
        else:
            dx.add_paragraph("")
            dx.add_paragraph(v[0])
            dx.add_paragraph(v[1])
    dx.save(path)
    return None


def render_xlsx(doc, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "documento"
    r = 1
    for k, v in doc.blocks:
        if k == "h":
            ws.cell(r, 1, v).font = Font(bold=True, size=13)
            r += 2
        elif k == "p":
            ws.cell(r, 1, v)
            r += 1
        elif k == "t":
            for row in v:
                for j, cell in enumerate(row, start=1):
                    ws.cell(r, j, str(cell))
                if r == 1 or row is v[0]:
                    for j in range(1, len(row) + 1):
                        ws.cell(r, j).font = Font(bold=True)
                r += 1
            r += 1
        else:
            r += 1
            ws.cell(r, 1, v[0])
            ws.cell(r + 1, 1, v[1])
            r += 3
    for col, w in zip("ABCDEFGHIJKL", [8, 16, 16, 30, 15, 34, 20, 40, 34, 16, 14, 12]):
        ws.column_dimensions[col].width = w
    wb.save(path)
    return None


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_pdf(doc, path):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["BodyText"], fontName="Times-Roman", fontSize=10.5,
                          leading=15, alignment=4, spaceAfter=6)
    head = ParagraphStyle("head", parent=ss["Heading3"], fontName="Times-Bold", fontSize=12,
                          spaceBefore=10, spaceAfter=6)
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=9)
    story = []
    for k, v in doc.blocks:
        if k == "h":
            story.append(Paragraph(_esc(v), head))
        elif k == "p":
            story.append(Paragraph(_esc(v), body))
        elif k == "t":
            data = [[Paragraph(_esc(str(c)), small) for c in row] for row in v]
            t = Table(data, repeatRows=1)
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story += [t, Spacer(1, 8)]
        else:
            story += [Spacer(1, 34), Paragraph(_esc(v[0]), body), Paragraph(_esc(v[1]), body)]
    SimpleDocTemplate(str(path), pagesize=A4, topMargin=22 * mm, bottomMargin=20 * mm,
                      leftMargin=25 * mm, rightMargin=20 * mm,
                      title=doc.kind, author="corpus sintetico").build(story)
    with __import__("pdfplumber").open(str(path)) as pdf:
        return len(pdf.pages)


def _layout_lines(doc, fr, fb, maxw):
    """Devuelve [(estilo, texto)] listo para paginar."""
    def wrap(txt, font):
        words, line, out = txt.split(), "", []
        for w in words:
            probe = (line + " " + w).strip()
            if font.getlength(probe) <= maxw:
                line = probe
            else:
                out.append(line)
                line = w
        out.append(line)
        return out

    lines = []
    for k, v in doc.blocks:
        if k == "h":
            lines += [("gap", "")] + [("h", x) for x in wrap(v.upper(), fb)] + [("gap", "")]
        elif k == "p":
            lines += [("p", x) for x in wrap(v, fr)] + [("gap", "")]
        elif k == "t":
            # Sin truncar: un dato partido a la mitad no sería culpa del motor.
            for j, r in enumerate(v):
                acc, out = "", []
                for cell in (str(c) for c in r):
                    probe = f"{acc}   |   {cell}" if acc else cell
                    if fr.getlength(probe) <= maxw:
                        acc = probe
                    else:
                        out.append(acc)
                        acc = cell
                out.append(acc)
                lines += [("th" if j == 0 else "t", x) for x in out]
            lines.append(("gap", ""))
        else:
            lines += [("gap", ""), ("gap", ""), ("sigmark", ""), ("p", v[0]), ("p", v[1])]
    return lines


def render_images(doc, rng, scan=False):
    from PIL import Image, ImageDraw, ImageFont
    reg, bold = _pick_font()
    fr, fb = ImageFont.truetype(reg, FSIZE), ImageFont.truetype(bold, FSIZE)
    doc.firma_rotulada = rng.random() < 0.55
    lines = _layout_lines(doc, fr, fb, PAGE_W - 2 * MARGIN)
    per_page = (PAGE_H - 2 * MARGIN) // LEAD
    pages, buf = [], []
    for ln in lines:
        buf.append(ln)
        if len(buf) >= per_page:
            pages.append(buf)
            buf = []
    if buf:
        pages.append(buf)

    out = []
    for pn, page in enumerate(pages, 1):
        img = Image.new("L", (PAGE_W, PAGE_H), 255)
        dr = ImageDraw.Draw(img)
        y = MARGIN
        for style, txt in page:
            if style == "sigmark":
                x0 = MARGIN + 40
                if doc.firma_rotulada:  # la mitad de los escaneos trae el rótulo "Firma:"
                    dr.text((MARGIN, y + 30), "Firma:", font=fr, fill=15)
                    x0 = MARGIN + int(fr.getlength("Firma:")) + 40
                y0 = y + 6
                pts = [(x0 + i * 22, y0 + 60 - int(55 * abs(rng.random() - 0.5) * 2))
                       for i in range(18)]
                dr.line(pts, fill=20, width=5, joint="curve")
                dr.arc([x0 - 20, y0, x0 + 300, y0 + 80], 200, 340, fill=20, width=4)
                doc.pii.append(("FIRMA", "(firma manuscrita)"))
                y += LEAD * 2
                continue
            dr.text((MARGIN, y), txt, font=(fb if style in ("h", "th") else fr), fill=15)
            y += LEAD
        dr.text((PAGE_W - MARGIN - 120, PAGE_H - 90), f"Pág. {pn}/{len(pages)}", font=fr, fill=90)
        if scan:
            img = img.rotate(rng.uniform(-0.55, 0.55), resample=Image.BICUBIC, fillcolor=255)
            img = Image.blend(img, Image.effect_noise((PAGE_W, PAGE_H), 22).convert("L"), 0.11)
        out.append(img)
    return out


def render_pdf_scan(doc, path, rng, tmp):
    import img2pdf
    pages = render_images(doc, rng, scan=True)
    paths = []
    for i, im in enumerate(pages):  # JPEG: un escaneo real no llega en PNG sin pérdida
        p = tmp / f"{path.stem}_{i}.jpg"
        im.convert("RGB").save(p, quality=75)
        paths.append(str(p))
    path.write_bytes(img2pdf.convert(paths, layout_fun=img2pdf.get_fixed_dpi_layout_fun((200, 200))))
    for p in paths:
        Path(p).unlink()
    return len(pages)


def render_image(doc, path, rng):
    pages = render_images(doc, rng, scan=True)
    ext = path.suffix.lower()
    if ext in (".tif", ".tiff"):
        pages[0].save(path, save_all=True, append_images=pages[1:], compression="tiff_deflate")
        return len(pages)
    im = pages[0]
    if ext in (".jpg", ".jpeg"):
        im.convert("RGB").save(path, quality=72)
    else:
        im.save(path)
    return 1


# ---------------------------------------------------------------------------
# Plan del corpus: 50 documentos
# ---------------------------------------------------------------------------
# (tipo, largo, formato, sin_pii)
PLAN = [
    ("resolucion", "XS", "txt", False),
    ("resolucion", "S", "pdf", False),
    ("resolucion", "M", "docx", False),
    ("resolucion", "L", "pdf", False),
    ("resolucion", "XL", "pdf", False),
    ("resolucion", "M", "pdf_scan", False),
    ("resolucion", "S", "png", False),
    ("resolucion", "M", "txt", True),
    ("dictamen", "S", "pdf", False),
    ("dictamen", "M", "pdf", False),
    ("dictamen", "L", "docx", False),
    ("dictamen", "XL", "txt", False),
    ("dictamen", "M", "pdf_scan", False),
    ("dictamen", "XS", "docx", False),
    ("oficio", "XS", "pdf", False),
    ("oficio", "S", "docx", False),
    ("oficio", "M", "pdf", False),
    ("oficio", "S", "jpg", False),
    ("oficio", "L", "txt", False),
    ("oficio", "S", "pdf_scan", False),
    ("acta", "S", "pdf", False),
    ("acta", "M", "docx", False),
    ("acta", "M", "tif", False),
    ("acta", "L", "pdf", False),
    ("acta", "XS", "png", False),
    ("sentencia", "M", "pdf", False),
    ("sentencia", "L", "pdf", False),
    ("sentencia", "S", "docx", False),
    ("sentencia", "XL", "docx", False),
    ("sentencia", "M", "pdf_scan", False),
    ("contrato", "M", "pdf", False),
    ("contrato", "L", "docx", False),
    ("contrato", "S", "txt", False),
    ("contrato", "M", "tif", False),
    ("reclamo", "XS", "txt", False),
    ("reclamo", "S", "png", False),
    ("reclamo", "M", "pdf", False),
    ("reclamo", "S", "jpg", False),
    ("reclamo", "S", "docx", False),
    ("transparencia", "M", "xlsx", False),
    ("transparencia", "S", "pdf", False),
    ("transparencia", "M", "docx", False),
    ("transparencia", "L", "xlsx", False),
    ("memo", "XS", "txt", False),
    ("memo", "S", "pdf_scan", False),
    ("memo", "XS", "docx", True),
    ("notificacion", "XS", "pdf", False),
    ("notificacion", "S", "tif", False),
    ("planilla", "M", "xlsx", False),
    ("planilla", "L", "xlsx", False),
    ("planilla", "XL", "xlsx", False),
    ("planilla", "S", "xlsx", True),
    ("acta", "L", "pdf_scan", False),
]

EXT = {"pdf": ".pdf", "pdf_scan": ".pdf", "docx": ".docx", "xlsx": ".xlsx", "txt": ".txt",
       "png": ".png", "jpg": ".jpg", "tif": ".tif"}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_tmp"
    tmp.mkdir(exist_ok=True)
    rng = random.Random(SEED)
    rows, gt = [], {}

    for i, (kind, size, fmt, nopii) in enumerate(PLAN, 1):
        doc = Doc(kind, size, random.Random(SEED + i * 7), nopii=nopii)
        BUILDERS[kind](doc)
        name = f"{i:02d}_{kind}_{size}{'_sin_pii' if nopii else ''}{EXT[fmt]}"
        dst = OUT / name
        if fmt == "txt":
            pages = render_txt(doc, dst)
        elif fmt == "docx":
            pages = render_docx(doc, dst)
        elif fmt == "xlsx":
            pages = render_xlsx(doc, dst)
        elif fmt == "pdf":
            pages = render_pdf(doc, dst)
        elif fmt == "pdf_scan":
            pages = render_pdf_scan(doc, dst, rng, tmp)
        else:
            pages = render_image(doc, dst, rng)

        by_code: dict[str, list[str]] = {}
        for code, val in doc.pii:
            by_code.setdefault(code, []).append(val)
        gt[name] = by_code
        rows.append({
            "archivo": name, "tipo": kind, "formato": fmt, "largo": size,
            "paginas": pages if pages else "", "palabras": doc.words,
            "bytes": dst.stat().st_size, "pii_total": len(doc.pii),
            "entidades": "|".join(f"{c}:{len(v)}" for c, v in sorted(by_code.items())),
            "firma_rotulada": "" if doc.firma_rotulada is None else ("si" if doc.firma_rotulada else "no"),
            "sha256": hashlib.sha256(dst.read_bytes()).hexdigest()[:16],
        })
        print(f"{name:52s} {rows[-1]['palabras']:>7} palabras  {len(doc.pii):>4} PII  "
              f"{rows[-1]['bytes'] / 1024:>8.0f} KB")

    with (OUT / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (OUT / "ground_truth.json").write_text(json.dumps(gt, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.rmdir()

    tot_w = sum(r["palabras"] for r in rows)
    tot_p = sum(r["pii_total"] for r in rows)
    print(f"\n{len(rows)} documentos | {tot_w:,} palabras | {tot_p:,} instancias de PII | "
          f"{sum(r['bytes'] for r in rows) / 1e6:.1f} MB -> {OUT}")


if __name__ == "__main__":
    main()
