import json
from pathlib import Path
import pytest
from backend import explicit_names, pipeline


@pytest.fixture(autouse=True)
def configured_rules(monkeypatch):
    rules = json.loads((Path(__file__).parent / "fixtures/explicit_rules.json").read_text())
    monkeypatch.setattr(explicit_names, "RULES", rules)



def _page(number, lines):
    words = []
    for line_number, line in enumerate(lines):
        x = 20
        for text in line.split():
            width = max(20, len(text) * 8)
            words.append([text, x, 40 + line_number * 40, x + width, 65 + line_number * 40])
            x += width + 8
    return {"n": number, "width": 2000, "height": 3000, "words": words}


def _extraction(pages):
    return {"kind": "paged", "format": "pdf", "pages": pages}


def test_nombres_configurados_cubren_nombres_y_apellidos():
    page = _page(1, [name + ", cédula" for name in explicit_names.RULES["names"]])
    findings = pipeline.detect_findings(_extraction([page]), ["NOMBRE"])
    assert {f["text"] for f in findings} == set(explicit_names.RULES["names"])
    assert len(findings) == len(explicit_names.RULES["names"])


def test_apellidos_partidos_entre_lineas_y_paginas_conservan_sus_cajas():
    p1 = _page(1, ["Don Juan Luis", "Rojas Ejemplo, cédula nacional",
                   "Don Pablo Eugenio"])
    p2 = _page(2, ["Ejemplos Rojas, cédula nacional", "Don Patricio Hernán",
                   "Campos Prueba, cédula nacional", "Don Ricardo Ivan",
                   "Campos Ejemplo, cédula nacional", "Don Salvador Rodrigo",
                   "Campos Prueba, cédula nacional"])
    findings = pipeline.detect_findings(_extraction([p1, p2]), ["NOMBRE"])
    assert {(f["page"], f["text"]) for f in findings} == {
        (1, "Juan Luis Rojas Ejemplo"), (1, "Pablo Eugenio"), (2, "Ejemplos Rojas"),
        (2, "Patricio Hernán Campos Prueba"), (2, "Ricardo Ivan Campos Ejemplo"),
        (2, "Salvador Rodrigo Campos Prueba"),
    }
    for page in [p1, p2]:
        for word in page["words"]:
            if word[0] in {"Don", "cédula", "nacional"}:
                assert not any(word[1:] in f["boxes"] for f in findings if f["page"] == page["n"])
            else:
                assert any(word[1:] in f["boxes"] for f in findings if f["page"] == page["n"])


def test_cargos_excluidos_y_otros_tipos_de_datos_conservados():
    page = _page(21, ["SR. MINISTRO DE TURNO", "Sr (a) Secretario de este Ilustrísimo Tribunal",
                     "don ALBERTO DAVID CAMPOS EJEMPLO, Notario", "correo: persona@example.cl"])
    ext = _extraction([page])
    findings = pipeline.detect_findings(ext, ["NOMBRE", "EMAIL"])
    assert [(f["entity_code"], f["text"]) for f in findings] == [
        ("EMAIL", "persona@example.cl"), ("NOMBRE", "ALBERTO DAVID CAMPOS EJEMPLO")]
    assert [f["entity_code"] for f in pipeline.detect_findings(ext, ["EMAIL"])] == ["EMAIL"]


def test_nombres_disponibles_en_cualquier_pdf_sin_excluir_otros():
    page = _page(1, ["Dionisio Andrés Campos Ejemplo", "Juan Luis",
                     "Rojas Ejemplo", "Pedro Soto"])
    ext = _extraction([page])
    names = {f["text"] for f in pipeline.detect_findings(ext, ["NOMBRE"])}
    assert names == {"Dionisio Andrés Campos Ejemplo", "Juan Luis Rojas Ejemplo", "Pedro Soto"}
    orders = _extraction([_page(1, ["Órdenes de servicio", "cumplir las órdenes", "Ejemplos, alumno"])])
    assert [f["text"] for f in explicit_names.exact_name_findings(orders)] == ["Ejemplos"]


def test_excepcion_de_cargos_es_explicita_y_no_se_extiende_a_otros():
    from backend import engine
    for text in ["SR. MINISTRO DE TURNO", "Sr. Secretario", "Sr. Ministro Juan Pérez"]:
        names = [text[s.start:s.end] for s in engine.detect(text, ["NOMBRE"])]
        assert names == (["Juan Pérez"] if "Juan Pérez" in text else [])
    text = "Sr. Capitán Zafiro"
    assert [text[s.start:s.end] for s in engine.detect(text, ["NOMBRE"])] == ["Capitán Zafiro"]
    page = _page(1, ["Sr. Capitán Zafiro", "Sr. Secretario Juan Pérez"])
    assert {f["text"] for f in pipeline.detect_findings(_extraction([page]), ["NOMBRE"])} == {
        "Capitán Zafiro", "Juan Pérez"}


def test_senor_y_dueno_no_es_persona_pero_senor_jose_si():
    page = _page(3, ["señor y dueño de ee ellas as", "señor José Castell Rojas"])
    findings = pipeline.detect_findings(_extraction([page]), ["NOMBRE"])
    assert [f["text"] for f in findings] == ["José Castell Rojas"]


def test_nombre_completo_castell_en_cualquier_pdf():
    page = _page(4, ["don JOSE ROBERTO CASTELL ROJAS, ejecutado en estos autos"])
    findings = pipeline.detect_findings(_extraction([page]), ["NOMBRE"])
    assert [f["text"] for f in findings] == ["JOSE ROBERTO CASTELL ROJAS"]
    assert findings[0]["boxes"] == [w[1:] for w in page["words"][1:5]]


def test_segunda_lectura_cubre_solo_nombre_confirmado_y_respeta_escala(monkeypatch):
    import pytesseract
    from PIL import Image
    from backend import engine

    image = Image.new("RGB", (1000, 1000), "gray")
    body = [[text, 100 + i * 100, 500, 190 + i * 100, 530]
            for i, text in enumerate(["JOSE", "CASTEL", "ROJAS"])]
    seen = []

    def ocr(crop, **kwargs):
        seen.append(crop.size)
        assert kwargs["config"] == "--psm 6"
        return {"text": ["don", "JOSE", "ROBERTO", "CASTELL", "ROJAS,", "ejecutado"],
                "left": [100, 200, 300, 400, 500, 600], "top": [100] * 6,
                "width": [80] * 6, "height": [40] * 6}

    monkeypatch.setattr(engine, "_ocr_language", lambda: "spa")
    monkeypatch.setattr(pytesseract, "image_to_data", ocr)
    added = explicit_names.supplement_document_header(image, body)
    assert [w[0] for w in added] == ["JOSE", "ROBERTO", "CASTELL", "ROJAS,"]
    assert added[0][1:] == [100, 50, 140, 70]
    assert seen == [(2000, 500)]
    assert explicit_names.supplement_document_header(image, [["Texto", 0, 0, 40, 20]] + body) == []
    assert explicit_names.supplement_document_header(image, [["Rojas", 0, 500, 40, 520]]) == []
    assert len(seen) == 1
    monkeypatch.setattr(pytesseract, "image_to_data", lambda *args, **kwargs: {
        "text": ["otra", "persona", "en", "texto"], "left": [0] * 4,
        "top": [100] * 4, "width": [10] * 4, "height": [20] * 4})
    assert explicit_names.supplement_document_header(image, body) == []
    image.close()


def test_parrafo_subrayado_recupera_nombre_y_direccion_sin_texto_vecino(monkeypatch):
    from PIL import Image
    import pytesseract
    from backend import engine

    image = Image.new("RGB", (1000, 1000), "gray")
    words = [["Antes", 20, 100, 100, 140], ["Después", 20, 500, 100, 540]]
    calls = []

    def ocr(crop, **kwargs):
        calls.append(crop.width)
        if crop.width == 2000:
            return {"text": ["calle", "Ejemplo", "N°", "1234", "donde"],
                    "left": [100, 200, 300, 350, 440], "top": [400] * 5,
                    "width": [80, 80, 30, 80, 80], "height": [60] * 5}
        return {"text": ["señor", "Jos!", "Castell", "Rojas", "quien"],
                "left": [50, 100, 150, 210, 270], "top": [100] * 5,
                "width": [40, 20, 45, 40, 40], "height": [12] * 5}

    monkeypatch.setattr(engine, "_ocr_language", lambda: "spa")
    monkeypatch.setattr(pytesseract, "image_to_data", ocr)
    added = explicit_names.supplement_document_paragraphs(image, words)
    assert [w[0] for w in added] == ["calle", "Ejemplo", "N°", "1234", "José", "Castell", "Rojas"]
    assert calls == [2000, 1000]
    assert added[0][1:] == [50, 330, 90, 360]  # escala y desplazamiento al párrafo
    assert added[4][1] >= 92 and added[6][3] <= 268  # conservar señor y quien
    assert added[4][2] < 230 and added[4][4] > 242  # cubrir altura perdida por subrayado
    assert explicit_names.has_document_reference(added)
    image.close()


def test_parrafos_no_incorporan_otras_personas_ni_otras_direcciones(monkeypatch):
    from PIL import Image
    import pytesseract
    from backend import engine

    image = Image.new("RGB", (1000, 1000), "gray")
    words = [["Antes", 20, 100, 100, 140], ["Después", 20, 500, 100, 540]]
    monkeypatch.setattr(engine, "_ocr_language", lambda: "spa")
    tokens = ["Juan", "Castell", "Rojas", "calle", "Ejemplo", "N°", "1235"]
    monkeypatch.setattr(pytesseract, "image_to_data", lambda *args, **kwargs: {
        "text": tokens, "left": list(range(0, 700, 100)), "top": [100] * 7,
        "width": [80] * 7, "height": [40] * 7})
    assert explicit_names.supplement_document_paragraphs(image, words) == []
    assert not explicit_names.has_document_reference([["Juan Castell Rojas", 0, 0, 100, 20]])
    image.close()


def test_bloque_superior_recupera_notario_y_ambas_fechas(monkeypatch):
    from PIL import Image
    import pytesseract
    from backend import engine

    image = Image.new("RGB", (1000, 1000), "gray")
    body = [["POR", 20, 600, 100, 640], ["TANTO", 120, 600, 220, 640]]
    tokens = ["fecha", "1", "de", "Junio", "de", "2017.",
              "fecha", "10", "de", "Octubre", "de", "2016",
              "don", "Alberto", "Campos", "Prueba,", "donde"]
    calls = []

    def ocr(crop, **kwargs):
        calls.append(crop.size)
        return {"text": tokens, "left": [20 + i * 50 for i in range(len(tokens))],
                "top": [100] * 6 + [200] * 6 + [300] * 5,
                "width": [45] * len(tokens), "height": [30] * len(tokens)}

    monkeypatch.setattr(engine, "_ocr_language", lambda: "spa")
    monkeypatch.setattr(pytesseract, "image_to_data", ocr)
    added = explicit_names.supplement_document_paragraphs(image, body)
    assert calls == [(1000, 600)]
    assert [w[0] for w in added] == tokens[1:6] + tokens[7:12] + tokens[13:16]
    ext = _extraction([{"n": 5, "width": 1000, "height": 1000, "words": added + body}])
    findings = pipeline.detect_findings(ext, ["NOMBRE", "FECHA"])
    assert {(f["entity_code"], f["text"]) for f in findings} == {
        ("NOMBRE", "Alberto Campos Prueba"), ("FECHA", "1 de Junio de 2017"),
        ("FECHA", "10 de Octubre de 2016"),
    }
    assert {f["entity_code"] for f in pipeline.detect_findings(ext, ["NOMBRE"])} == {"NOMBRE"}
    image.close()


def test_campos_prueba_disponible_fuera_del_escrito():
    page = _page(1, ["Alberto Campos", "Prueba, comparece ante el tribunal"])
    findings = pipeline.detect_findings(_extraction([page]), ["NOMBRE"])
    assert [f["text"] for f in findings] == ["Alberto Campos Prueba"]
