"""Pixel regressions for multiline replacements and text placement."""
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont, ImageChops

from backend import pipeline


@pytest.mark.parametrize('treatment', list(pipeline.TREATMENTS))
def test_wrapped_name_preserves_unrelated_prose(treatment, tmp_path):
    image = Image.new('RGB', (600, 180), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=24)
    boxes = []
    for text, x, y in [('Ana', 480, 20), ('Morales', 20, 65)]:
        draw.text((x, y), text, fill='black', font=font)
        boxes.append(list(draw.textbbox((x, y), text, font=font)))
    draw.text((100, 25), 'Contenido que debe conservarse', fill='black', font=font)
    source = tmp_path / 'page.png'
    image.save(source)
    extraction = {'kind': 'paged', 'format': 'png', 'pages': [
        {'n': 1, 'image': 'page.png', 'width': 600, 'height': 180,
         'words': [['Ana', *boxes[0]], ['Morales', *boxes[1]]]}]}
    finding = pipeline._finding('NOMBRE', 'Ana\nMorales', page=1, boxes=boxes)
    output = tmp_path / 'protected.png'
    pipeline.apply_findings(source, extraction, [finding], tmp_path, output, treatment)
    protected = Image.open(output).convert('RGB')
    # This text lies inside the old union rectangle, between the name's lines.
    untouched = (100, 20, 455, 52)
    assert ImageChops.difference(image.crop(untouched), protected.crop(untouched)).getbbox() is None
    # Neither erasure nor the replacement can escape the local word rectangles.
    outside = ImageChops.difference(image, protected)
    erase = ImageDraw.Draw(outside)
    for x0, y0, x1, y1 in boxes:
        erase.rectangle((x0-3, y0-3, x1+3, y1+3), fill='black')
    assert outside.getbbox() is None


def test_replacement_segments_keep_lines_and_columns_separate():
    boxes = [[10, 10, 35, 30], [40, 11, 80, 30], [320, 10, 360, 30],
             [10, 48, 100, 68]]
    assert pipeline._replacement_segments(boxes) == [
        (10, 10, 80, 30), (320, 10, 360, 30), (10, 48, 100, 68)]


@pytest.mark.parametrize('treatment', [t for t in pipeline.TREATMENTS if t != 'redact'])
def test_inflated_ocr_boxes_do_not_enlarge_replacement_text(treatment, tmp_path):
    image = Image.new('RGB', (1000, 1000), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)
    draw.text((100, 100), 'Juan Morales', fill='black', font=font)
    source = tmp_path / 'page.png'
    image.save(source)
    boxes = [[100, 100, 350, 230], [360, 100, 700, 230]]
    extraction = {'kind': 'paged', 'format': 'png', 'pages': [
        {'n': 1, 'image': 'page.png', 'width': 1000, 'height': 1000,
         'words': [['Juan', *boxes[0]], ['Morales', *boxes[1]]]}]}
    finding = pipeline._finding('NOMBRE', 'Juan Morales', page=1, boxes=boxes)
    output = tmp_path / 'protected.png'
    pipeline.apply_findings(source, extraction, [finding], tmp_path, output, treatment)
    # Inspect the actual replacement ink, rather than the oversized OCR boxes.
    ink = Image.open(output).convert('L').point(lambda pixel: 255 if pixel < 80 else 0).getbbox()
    assert ink is not None
    assert ink[3] - ink[1] <= 30
