#!/usr/bin/env python3
"""Test de esfuerzo HTTP contra una instalación de la plataforma.

Simula N sesiones concurrentes (NFR-1: 40) que suben documentos del corpus
sintético y espera a que todos lleguen a revisión. Con --protect, el propio
cliente de prueba confirma cada revisión con un tratamiento explícito.

Uso:
  .venv/bin/python tools/stress_http.py --url http://localhost:8000 --password "$ADMIN_PASSWORD" \
      --docs 100 --sessions 40 [--protect --treatment redact] [--max-kb 300] [--filtro pdf] [--timeout 1200]

Reporta: latencia de carga p50/p95, 5xx, tiempo hasta revisión p50/p95/max,
rendimiento (docs/min), profundidad de cola observada y salud final.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import statistics
import sys
import time
from pathlib import Path

import httpx

CORPUS = Path(__file__).resolve().parents[2] / "corpus-tachado-sintetico"


def pick_files(docs: int, filtro: str, max_kb: int) -> list[Path]:
    rows = list(csv.DictReader((CORPUS / "manifest.csv").open(encoding="utf-8")))
    files = [CORPUS / r["archivo"] for r in rows
             if filtro in r["archivo"] and (not max_kb or int(r["bytes"]) <= max_kb * 1024)]
    if not files:
        sys.exit("ningún archivo del corpus cumple el filtro")
    return [files[i % len(files)] for i in range(docs)]


def pctl(xs, p):
    return round(statistics.quantiles(xs, n=100)[p - 1], 2) if len(xs) > 1 else (round(xs[0], 2) if xs else None)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", required=True)
    ap.add_argument("--docs", type=int, default=100)
    ap.add_argument("--sessions", type=int, default=40)
    ap.add_argument("--protect", action="store_true",
                    help="confirma cada documento revisado y espera su protección")
    ap.add_argument("--treatment", choices=("redact", "anonymize", "pseudonymize", "mask_full", "mask_partial"),
                    default="redact", help="tratamiento usado con --protect")
    ap.add_argument("--filtro", default="")
    ap.add_argument("--max-kb", type=int, default=0, help="omitir archivos del corpus mayores a este tamaño")
    ap.add_argument("--timeout", type=int, default=1200)
    args = ap.parse_args()

    files = pick_files(args.docs, args.filtro, args.max_kb)
    total_mb = sum(f.stat().st_size for f in files) / 1048576
    final_states = {"protected", "protected_with_warnings"} if args.protect else {"review", "protected"}
    print(f"→ {len(files)} documentos ({total_mb:.1f} MB) · {args.sessions} sesiones concurrentes · protect={args.protect}")

    limits = httpx.Limits(max_connections=args.sessions + 5)
    async with httpx.AsyncClient(base_url=args.url, timeout=120, limits=limits) as probe:
        # sesiones independientes (cookies distintas) = usuarios concurrentes reales
        clients = []
        for _ in range(args.sessions):
            c = httpx.AsyncClient(base_url=args.url, timeout=300, limits=limits)
            r = await c.post("/api/auth/login", json={"username": args.user, "password": args.password})
            r.raise_for_status()
            clients.append(c)

        up_lat, up_errors, doc_ids, backpressure = [], [], {}, [0]
        sem = asyncio.Semaphore(args.sessions)
        t0 = time.perf_counter()

        async def upload(i, path):
            async with sem:
                c = clients[i % len(clients)]
                data = {"document_type": "otro"}
                t = time.perf_counter()
                retries_503 = 0
                try:
                    while True:
                        r = await c.post("/api/v1/documents", data=data,
                                         files={"files": (f"{i:03d}_{path.name}", path.read_bytes())})
                        if r.status_code == 503 and retries_503 < 60:
                            # backpressure por diseño: el cliente espera y reintenta
                            retries_503 += 1
                            await asyncio.sleep(5)
                            continue
                        break
                    up_lat.append(time.perf_counter() - t)
                    if retries_503:
                        backpressure[0] += retries_503
                    if r.status_code == 201:
                        doc_ids[r.json()["documents"][0]["id"]] = time.perf_counter()
                    else:
                        up_errors.append((r.status_code, r.text[:120]))
                except Exception as e:  # noqa: BLE001
                    up_errors.append(("EXC", f"{type(e).__name__}: {e}"))

        health_samples = []

        async def watch_health():
            while True:
                try:
                    h = (await probe.get("/api/health")).json()
                    health_samples.append(h)
                except Exception:  # noqa: BLE001
                    health_samples.append({"status": "sin respuesta"})
                await asyncio.sleep(5)

        watcher = asyncio.create_task(watch_health())
        await asyncio.gather(*(upload(i, p) for i, p in enumerate(files)))
        t_upload = time.perf_counter() - t0
        print(f"→ carga completa en {t_upload:.1f}s · {len(doc_ids)} aceptados · {len(up_errors)} errores de carga")

        # esperar el procesamiento
        done, ttr, failed, approved = {}, [], [], set()
        deadline = time.perf_counter() + args.timeout
        while len(done) < len(doc_ids) and time.perf_counter() < deadline:
            await asyncio.sleep(3)
            pend = [d for d in doc_ids if d not in done]
            for i in range(0, len(pend), 25):
                for d in pend[i:i + 25]:
                    try:
                        st = (await clients[0].get(f"/api/v1/documents/{d}")).json()["status"]
                    except Exception:  # noqa: BLE001
                        continue
                    if args.protect and st == "review" and d not in approved:
                        response = await clients[0].post(
                            f"/api/v1/documents/{d}/approve", json={"treatment": args.treatment}
                        )
                        if response.status_code == 200:
                            approved.add(d)
                        else:
                            done[d] = time.perf_counter()
                            failed.append((d, f"approve:{response.status_code}"))
                    elif st in final_states:
                        done[d] = time.perf_counter()
                        ttr.append(done[d] - t0)
                    elif st in ("failed", "verification_failed"):
                        done[d] = time.perf_counter()
                        failed.append((d, st))
        watcher.cancel()
        t_total = time.perf_counter() - t0

        h = (await probe.get("/api/health")).json()
        vr = (await clients[0].get("/api/audit/verify")).json()
        for c in clients:
            await c.aclose()

    ok = len(done) - len(failed)
    stuck = len(doc_ids) - len(done)
    q_max = max((sum((s.get("queue") or {}).values()) for s in health_samples), default=0)
    caidas = sum(1 for s in health_samples if s.get("status") == "sin respuesta")
    print(f"""
================= RESULTADO =================
documentos:        {len(files)} enviados · {len(doc_ids)} aceptados · {ok} completados · {len(failed)} fallidos · {stuck} sin terminar
carga:             {t_upload:.1f}s total · latencia p50 {pctl(up_lat, 50)}s · p95 {pctl(up_lat, 95)}s · errores {len(up_errors)}
hasta {'protegido' if args.protect else 'revisión'}:   p50 {pctl(ttr, 50)}s · p95 {pctl(ttr, 95)}s · máx {round(max(ttr), 1) if ttr else '-'}s
rendimiento:       {ok / max(t_total / 60, 0.01):.1f} docs/min · duración total {t_total:.1f}s
cola máx (health): {q_max} · esperas por backpressure (503+reintento): {backpressure[0]} · sin respuesta: {caidas}
salud final:       {h.get('status')} · db={h.get('db')} · disco={h.get('disk_free_mb')}MB · cola={h.get('queue')}
bitácora íntegra:  {vr}
""")
    if up_errors:
        from collections import Counter
        print("errores de carga:", Counter(e[0] for e in up_errors))
        for e in up_errors[:5]:
            print("  ", e)
    if failed:
        print("fallidos:", failed[:10])
    # el 503 de backpressure es comportamiento diseñado (el cliente reintenta y termina);
    # lo que NO se admite son 500 (errores reales) o caídas del servidor.
    err5xx = sum(1 for e in up_errors if isinstance(e[0], int) and e[0] >= 500)
    print(f"NFR-1 (sin errores 5xx reales con {args.sessions} sesiones): {'CUMPLE' if err5xx == 0 and caidas == 0 else 'NO CUMPLE'}")


if __name__ == "__main__":
    asyncio.run(main())
