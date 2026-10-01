#!/usr/bin/env python3
"""Test de esfuerzo sobre el corpus sintético: mide tiempos y recall por entidad.

Uso:
  .venv/bin/python tools/stress_corpus.py                 # extract+detect+apply+verify
  .venv/bin/python tools/stress_corpus.py --solo-detectar # sin aplicar tachado (rápido)
  .venv/bin/python tools/stress_corpus.py --filtro pdf     # sólo archivos que contengan "pdf"

Escribe reporte_stress.csv en la carpeta del corpus.
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import engine, pipeline  # noqa: E402

CORPUS = Path(sys.argv[sys.argv.index("--corpus") + 1]) if "--corpus" in sys.argv \
    else Path(__file__).resolve().parents[2] / "corpus-tachado-sintetico"
APPLY = "--solo-detectar" not in sys.argv
FILTRO = sys.argv[sys.argv.index("--filtro") + 1] if "--filtro" in sys.argv else ""
CODES = list(engine.ENTITY_TYPES)


def match(a: str, b: str) -> bool:
    """Tolerante a bordes de span y a ruido de OCR en los extremos."""
    a, b = pipeline._norm(a), pipeline._norm(b)
    return bool(a) and bool(b) and (a in b or b in a)


def score(gt: dict[str, list[str]], findings: list[dict]):
    """Empareja cada instancia esperada con una detección del mismo tipo."""
    libres = [dict(f) for f in findings]
    tp, fn = [], []
    for code, valores in gt.items():
        for v in valores:
            hit = next((f for f in libres if f["entity_code"] == code and
                        (code == "FIRMA" or match(v, f["text"]))), None)
            if hit:
                libres.remove(hit)
                tp.append((code, v))
            else:
                fn.append((code, v))
    return tp, fn, libres


def main():
    rows = list(csv.DictReader((CORPUS / "manifest.csv").open(encoding="utf-8")))
    gt_all = json.loads((CORPUS / "ground_truth.json").read_text(encoding="utf-8"))
    rows = [r for r in rows if FILTRO in r["archivo"]]
    out, agg = [], {}
    t_total = time.perf_counter()

    for r in rows:
        src = CORPUS / r["archivo"]
        gt = gt_all[r["archivo"]]
        rec = {k: r[k] for k in ("archivo", "formato", "largo", "palabras", "paginas")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                wd = Path(tmp) / "work"
                t = time.perf_counter()
                extraction = pipeline.extract(src, wd)
                rec["s_extract"] = round(time.perf_counter() - t, 2)

                t = time.perf_counter()
                findings = pipeline.detect_findings(extraction, CODES, wd)
                rec["s_detect"] = round(time.perf_counter() - t, 2)

                tp, fn, fp = score(gt, findings)
                rec.update(gt_total=sum(len(v) for v in gt.values()), tp=len(tp), fn=len(fn),
                           fp=len(fp), detecciones=len(findings))
                rec["recall"] = round(len(tp) / max(len(tp) + len(fn), 1), 3)

                if APPLY:
                    dst = Path(tmp) / ("protegido" + src.suffix.lower())
                    t = time.perf_counter()
                    pipeline.apply_findings(src, extraction, findings, wd, dst)
                    rec["s_apply"] = round(time.perf_counter() - t, 2)
                    t = time.perf_counter()
                    # verify sobre lo que el motor dijo haber tachado: mide si el
                    # tachado es efectivo. Lo que no detectó ya lo mide el recall.
                    ver = pipeline.verify(dst, [f["text"] for f in findings])
                    rec["s_verify"] = round(time.perf_counter() - t, 2)
                    rec["verify_ok"] = ver["ok"]
                    rec["checks_fallidos"] = "; ".join(c["name"] for c in ver.get("checks", []) if not c["ok"])
                    rec["bytes_out"] = dst.stat().st_size
            rec["error"] = ""
            for code, _ in tp:
                agg.setdefault(code, [0, 0])[0] += 1
            for code, _ in fn:
                agg.setdefault(code, [0, 0])[1] += 1
            rec["fn_detalle"] = "; ".join(f"{c}={v[:40]}" for c, v in fn[:6])
            rec["fp_detalle"] = "; ".join(f"{f['entity_code']}={f['text'][:40]}" for f in fp[:6])
        except Exception as exc:  # el corpus también prueba el manejo de errores
            rec["error"] = f"{type(exc).__name__}: {exc}"
            traceback.print_exc(limit=2)
        out.append(rec)
        print(f"{rec['archivo']:52s} rec={rec.get('recall', '-'):<6} fp={rec.get('fp', '-'):<5} "
              f"t={rec.get('s_extract', 0)}+{rec.get('s_detect', 0)}"
              f"+{rec.get('s_apply', '-')}+{rec.get('s_verify', '-')}s {rec['error']}")

    cols = ["archivo", "formato", "largo", "palabras", "paginas", "s_extract", "s_detect",
            "s_apply", "s_verify", "gt_total", "detecciones", "tp", "fn", "fp", "recall",
            "verify_ok", "checks_fallidos", "bytes_out", "fn_detalle", "fp_detalle", "error"]
    with (CORPUS / "reporte_stress.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)

    tp = sum(r.get("tp", 0) for r in out)
    fn = sum(r.get("fn", 0) for r in out)
    fp = sum(r.get("fp", 0) for r in out)
    print(f"\n=== {len(out)} documentos en {time.perf_counter() - t_total:.0f}s ===")
    print(f"recall global {tp / max(tp + fn, 1):.3f}  ({tp} ok / {fn} perdidos)   falsos positivos: {fp}")
    print(f"{'entidad':<20}{'esperadas':>10}{'detectadas':>12}{'recall':>9}")
    for code in sorted(agg, key=lambda c: -sum(agg[c])):
        ok, miss = agg[code]
        print(f"{code:<20}{ok + miss:>10}{ok:>12}{ok / max(ok + miss, 1):>9.3f}")
    fallos = [r for r in out if r["error"]]
    if fallos:
        print(f"\n{len(fallos)} con error:")
        for r in fallos:
            print(f"  {r['archivo']}: {r['error']}")
    print(f"\nDetalle: {CORPUS / 'reporte_stress.csv'}")


if __name__ == "__main__":
    main()
