"""Regresión de geometría PDF a píxel con MediaBox desplazado y rotaciones.

Se mide la cobertura de tinta para verificar que las cajas protegen el contenido
completo sin depender de una segunda lectura OCR.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import pipeline  # noqa: E402

TEXTO = "12.345.678-5"  # solo el valor: la etiqueta no se tacha y ensuciaría la medición de cobertura
# Relleno con >30 alfanuméricos: bajo ese umbral la página se va a OCR (espacio de
# píxeles, sin el bug) y la prueba no ejercita la capa de texto, que es la que falla.
RELLENO = "Resolucion exenta numero cuarenta y dos de la Subsecretaria"


def _mini_pdf(mediabox, at=(130, 420), cropbox=None, rotate=None, size=24) -> bytes:
    """PDF de una página escrito a mano: Helvetica, texto en coordenadas conocidas."""
    x, y = at
    content = (f"BT /F1 {size} Tf {x} {y + 3 * size} Td ({RELLENO}) Tj ET "
               f"BT /F1 {size} Tf {x} {y} Td ({TEXTO}) Tj ET").encode()
    extra = f" /CropBox [{' '.join(str(v) for v in cropbox)}]" if cropbox else ""
    extra += f" /Rotate {rotate}" if rotate else ""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [{' '.join(str(v) for v in mediabox)}]{extra} "
         f"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>").encode(),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


def _ink_bbox(img):
    """Caja de la tinta por perfil de filas y columnas (nada de números mágicos)."""
    g = img.convert("L")
    cols = [x for x in range(g.width) if any(g.getpixel((x, y)) < 128 for y in range(0, g.height, 2))]
    rows = [y for y in range(g.height) if any(g.getpixel((x, y)) < 128 for x in range(0, g.width, 2))]
    assert cols and rows, "la página rasterizada no tiene tinta"
    return min(cols), min(rows), max(cols), max(rows)


CASOS = {
    "control_origen_00": dict(mediabox=[0, 0, 595, 842], at=(100, 400)),
    "mediabox_desplazado": dict(mediabox=[30, 20, 625, 862]),
    "cropbox_distinto": dict(mediabox=[0, 0, 595, 842], cropbox=[25, 15, 570, 827], at=(100, 400)),
    "desplazado_y_cropbox": dict(mediabox=[30, 20, 625, 862], cropbox=[55, 35, 600, 847]),
    "desplazado_y_rotado": dict(mediabox=[30, 20, 625, 862], rotate=90),
}


@pytest.mark.parametrize("caso", CASOS)
def test_cajas_contienen_la_tinta(caso, tmp_path):
    from PIL import Image
    src = tmp_path / f"{caso}.pdf"
    src.write_bytes(_mini_pdf(**CASOS[caso]))
    e = pipeline.extract(src, tmp_path / "work")
    page = e["pages"][0]
    assert page["words"], caso
    x0 = min(w[1] for w in page["words"]); y0 = min(w[2] for w in page["words"])
    x1 = max(w[3] for w in page["words"]); y1 = max(w[4] for w in page["words"])
    ink = _ink_bbox(Image.open(tmp_path / "work" / page["image"]))
    tol = 3  # la caja puede quedar holgada; la tinta no puede salirse más que esto
    assert x0 - tol <= ink[0] and y0 - tol <= ink[1] and ink[2] <= x1 + tol and ink[3] <= y1 + tol, \
        f"{caso}: caja x[{x0},{x1}] y[{y0},{y1}] no contiene la tinta x[{ink[0]},{ink[2]}] y[{ink[1]},{ink[3]}]"


def test_tachado_cubre_el_dato_extremo_a_extremo(tmp_path):
    """Procesa un PDF con origen desplazado y comprueba sobre el PDF de salida que la
    tinta original del RUT quedó cubierta de verdad (píxeles, no verify). Comparar la
    caja del hallazgo consigo misma no sirve: la caja siempre queda negra porque es
    donde se pinta; lo que importa es que tape donde estaba el dato."""
    from pdf2image import convert_from_path
    src = tmp_path / "desplazado.pdf"
    src.write_bytes(_mini_pdf(mediabox=[30, 20, 625, 862]))
    workdir = tmp_path / "work"
    extraction = pipeline.extract(src, workdir)
    findings = pipeline.detect_findings(extraction, ["RUT"], workdir)
    assert len(findings) == 1, findings

    # Tinta real del RUT en la entrada: línea inferior de tinta (el relleno va arriba).
    entrada = convert_from_path(str(src), dpi=pipeline.DPI)[0].convert("L")
    filas = [y for y in range(entrada.height) if any(entrada.getpixel((x, y)) < 128 for x in range(0, entrada.width, 2))]
    cortes = [i for i in range(1, len(filas)) if filas[i] - filas[i - 1] > 10]
    linea = filas[cortes[-1]:] if cortes else filas
    oscuros = [(x, y) for y in linea for x in range(entrada.width) if entrada.getpixel((x, y)) < 128]
    assert oscuros

    out = tmp_path / "salida.pdf"
    pipeline.apply_findings(src, extraction, findings, workdir, out, "redact")
    salida = convert_from_path(str(out), dpi=pipeline.DPI)[0].convert("L")
    sx, sy = salida.width / entrada.width, salida.height / entrada.height
    cubiertos = sum(salida.getpixel((round(x * sx), round(y * sy))) < 55 for x, y in oscuros)
    assert cubiertos / len(oscuros) > 0.98, f"tinta del RUT aún visible: cubierta {cubiertos / len(oscuros):.0%}"
