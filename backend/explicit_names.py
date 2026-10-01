"""Reglas complementarias de detección cargadas desde un perfil JSON."""
from __future__ import annotations

import re
import json
import os
from pathlib import Path

from . import engine


def _load_rules() -> dict:
    path = os.environ.get("EXPLICIT_RULES_FILE", "")
    if not path:
        return {}
    rules = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rules, dict):
        raise ValueError("EXPLICIT_RULES_FILE debe contener un objeto JSON")
    for key in ("names", "reference_sequences", "header_tokens", "paragraph_rules", "completion_groups"):
        if key in rules and not isinstance(rules[key], list):
            raise ValueError(f"EXPLICIT_RULES_FILE: {key} debe ser una lista")
    return rules


RULES = _load_rules()


def has_document_reference(words: list) -> bool:
    tokens = re.findall(r"\w+", engine._strip(" ".join(w[0] for w in words)))
    return any(tokens[i:i + len(sequence)] == sequence
               for sequence in RULES.get("reference_sequences", []) if sequence
               for i in range(len(tokens) - len(sequence) + 1))


def supplement_document_paragraphs(img, words: list) -> list:
    """Relee zonas omitidas y recupera únicamente las secuencias configuradas.

    El llamador exige una referencia documental del perfil. Los límites de las
    zonas se obtienen del OCR, no de un número de página o recuadro fijo.
    """
    if not RULES.get("paragraph_rules"):
        return []
    from PIL import ImageOps, ImageFilter
    import pytesseract
    from statistics import median

    gaps, bottom = [], None
    for word in sorted(words, key=lambda w: w[2]):
        if bottom is None and word[2] > img.height * 0.12:
            gaps.append((0, int(word[2])))
        elif bottom is not None and word[2] - bottom > img.height * 0.12:
            gaps.append((max(0, int(bottom) - 10), int(word[2])))
        bottom = max(bottom or 0, word[4])
    added = []
    for top, bottom in gaps:
        crop = img.crop((0, top, img.width, bottom))
        gray = ImageOps.autocontrast(crop.convert("L"))
        found = set()
        # El aumento recupera la dirección; la escala nativa conserva las letras
        # del nombre que se confunden con el subrayado al ampliar la imagen.
        variants = [(1, 2, 200, 3), (2, 3, 300, 2)] if top == 0 else [(2, 3, 300, 2), (1, 2, 200, 3)]
        for scale, radius, percent, threshold in variants:
            resized = gray.resize((gray.width * scale, gray.height * scale))
            sharp = resized.filter(ImageFilter.UnsharpMask(
                radius=radius, percent=percent, threshold=threshold))
            try:
                kwargs = {"config": "--psm 6", "output_type": pytesseract.Output.DICT}
                language = engine._ocr_language()
                if language:
                    kwargs["lang"] = language
                data = pytesseract.image_to_data(sharp, **kwargs)
            finally:
                resized.close()
                sharp.close()
            reread = [[text, data["left"][i] / scale, data["top"][i] / scale + top,
                       (data["left"][i] + data["width"][i]) / scale,
                       (data["top"][i] + data["height"][i]) / scale + top]
                      for i, text in enumerate(data["text"]) if text.strip()]
            tokens = [engine._strip(w[0]).strip(" ,.;:") for w in reread]
            for start in range(len(tokens)):
                count, field, corrections = 0, None, {}
                for rule in RULES.get("paragraph_rules", []):
                    expected = rule["tokens"]
                    if expected and tokens[start:start + len(expected)] == expected:
                        count, field = len(expected), rule["field"]
                        corrections = rule.get("corrections", {})
                        break
                if count and field not in found:
                    selected = reread[start:start + count]
                    if field == "name":
                        for index, corrected in corrections.items():
                            selected[int(index)][0] = corrected
                        # El subrayado hace que OCR encierre sólo la parte inferior
                        # de las letras. Recuperar su altura usando el texto cercano.
                        height = median(w[4] - w[2] for w in words if w[4] > w[2])
                        y0 = max(top, min(w[2] for w in selected) - height * 0.4)
                        y1 = min(bottom, max(w[4] for w in selected) + height * 0.15)
                        left = max(0, selected[0][1] - height * 0.5)
                        right = min(img.width, selected[-1][3] + height * 0.5)
                        if start and tokens[start - 1] == "senor":
                            left = max(left, reread[start - 1][3] + 2)
                        if start + count < len(tokens) and tokens[start + count] == "quien":
                            right = min(right, reread[start + count][1] - 2)
                            # Acotar el apellido usando el ancho de letra del token anterior.
                            char_width = (selected[1][3] - selected[1][1]) / max(1, len(selected[1][0]))
                            right = min(right, selected[2][1] + char_width * len(selected[2][0]) + height * 0.1)
                        boundaries = [left, selected[1][1], selected[2][1], right]
                        for i, word in enumerate(selected):
                            word[1:] = [boundaries[i], y0, boundaries[i + 1], y1]
                    added.extend(selected)
                    found.add(field)
            if any(set(group) <= found for group in RULES.get("completion_groups", [])):
                break
        crop.close()
        gray.close()
    return added


def supplement_document_header(img, words: list) -> list:
    """Recupera este nombre concreto si el OCR omitió el encabezado escaneado.

    Se exige la mención corta en el cuerpo y una cabecera sin palabras leídas.
    La segunda lectura solo incorpora las palabras del encabezado configurado.
    """
    if (not has_document_reference(words)
            or any(w[2] < img.height * 0.2 for w in words)):
        return []
    from PIL import ImageOps
    import pytesseract

    crop = img.crop((0, 0, img.width, int(img.height * 0.25)))
    gray = ImageOps.autocontrast(crop.convert("L"))
    enlarged = gray.resize((gray.width * 2, gray.height * 2))
    try:
        kwargs = {"config": "--psm 6", "output_type": pytesseract.Output.DICT}
        language = engine._ocr_language()
        if language:
            kwargs["lang"] = language
        data = pytesseract.image_to_data(enlarged, **kwargs)
    finally:
        crop.close()
        gray.close()
        enlarged.close()
    reread = [[text, data["left"][i] / 2, data["top"][i] / 2,
               (data["left"][i] + data["width"][i]) / 2,
               (data["top"][i] + data["height"][i]) / 2]
              for i, text in enumerate(data["text"]) if text.strip()]
    expected = RULES.get("header_tokens", [])
    if not expected:
        return []
    for start in range(len(reread) - len(expected) + 1):
        selected = reread[start:start + len(expected)]
        tokens = [engine._strip(w[0]).strip(" ,.;:") for w in selected]
        if tokens == expected:
            return selected
    return []


def exact_name_findings(extraction: dict) -> list[dict]:
    """Añade coincidencias literales, sin reemplazar la detección de otros nombres.

    La secuencia une líneas y páginas, pero cada recuadro sigue anclado a su
    palabra original. Un nombre partido entre páginas produce un hallazgo por
    página, cubriendo también sus apellidos en la siguiente.
    """
    if extraction.get("kind") != "paged":
        return []
    tokens, anchors = [], []
    for page in extraction["pages"]:
        for word_index, word in enumerate(page["words"]):
            for token in re.findall(r"\w+", engine._strip(word[0])):
                tokens.append(token)
                anchors.append((page["n"], word_index, word))

    findings, occupied = [], set()
    # Nombres completos primero para evitar coincidencias duplicadas.
    for name in sorted(RULES.get("names", []), key=lambda value: len(value.split()), reverse=True):
        expected = engine._strip(name).split()
        for start in range(len(tokens) - len(expected) + 1):
            end = start + len(expected)
            if tokens[start:end] != expected or any(i in occupied for i in range(start, end)):
                continue
            # Las coincidencias de una palabra exigen mayúsculas y acentos exactos.
            if len(expected) == 1 and anchors[start][2][0].strip(' ,.;:()“”"') != name:
                continue
            occupied.update(range(start, end))
            by_page = {}
            for page_number, word_index, word in anchors[start:end]:
                by_page.setdefault(page_number, {})[word_index] = word
            for page_number, words in by_page.items():
                selected = list(words.values())
                findings.append({"page": page_number,
                                 "text": " ".join(w[0].strip(" ,.;:()“”") for w in selected),
                                 "boxes": [list(w[1:]) for w in selected], "score": 1.0})
    return findings
