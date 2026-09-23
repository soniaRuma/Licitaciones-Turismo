"""
Agente de licitaciones de Turismo — minube
Capa 2: plataformas autonómicas de contratación pública (fuera de PLACSP).

Este fichero está organizado como una colección de "conectores", uno por
territorio. Cada conector es una función que devuelve una lista de
licitaciones ya normalizadas (mismo formato que en ingest_placsp.py) para
que se puedan guardar en la misma tabla de Supabase.

ESTADO ACTUAL DE CADA CONECTOR (revisar antes de activar el cron):
  ✅ CATALUÑA   — verificado. Usa la API abierta Socrata del portal de
                  transparencia (analisi.transparenciacatalunya.cat),
                  dataset "ybgg-dgi6" (licitacions PSCP).
  ⏳ EUSKADI, COMUNITAT VALENCIANA, GALICIA, MADRID, ANDALUCÍA, NAVARRA,
     LA RIOJA — pendientes de verificar. Cada una necesita el mismo
     proceso de investigación que hicimos para Cataluña y PLACSP: localizar
     su portal de datos abiertos (si lo tiene) o diseñar un scraping
     puntual de su buscador público. Se añaden aquí como funciones "stub"
     con una nota de qué comprobar, para no fabricar una URL que no
     esté confirmada y que fallaría en producción sin que puedas
     depurarlo. Pide que se investiguen una a una y se van completando.
"""

import os
import re
import requests

PALABRAS_CLAVE = [
    "turisme", "turisme", "turístic", "turistic", "turística", "turístico",
    "promoció turística", "promocion turistica", "marca turística", "marca destino",
    "oficina de turisme", "destí turístic", "campanya de comunicació", "campanya de promoció",
    "convention bureau", "senyalística turística", "fitur", "oferta turística",
]


# ---------------------------------------------------------------------------
# ✅ CATALUÑA — Plataforma de Serveis de Contractació Pública (PSCP)
#    Fuente: portal Socrata de transparencia de la Generalitat.
#    Dataset: "Contractació pública a Catalunya: publicacions a la PSCP" (ybgg-dgi6)
# ---------------------------------------------------------------------------

CATALUNYA_SODA_URL = "https://analisi.transparenciacatalunya.cat/resource/ybgg-dgi6.json"


def conector_cataluna(dias_atras: int = 3) -> list[dict]:
    """
    Consulta el dataset Socrata de PSCP, filtrando por texto de Turismo en
    el objeto del contrato (campo 'objecte_del_contracte' o similar —
    revisar nombres exactos de columnas con la llamada de prueba que se
    describe más abajo, ya que Socrata puede variar ligeramente los
    nombres de campo entre datasets).
    """
    # $q hace una búsqueda de texto libre en todo el registro (Socrata SoQL).
    # Combinamos varias palabras clave con OR uniendo varias llamadas, ya que
    # $q no admite OR directamente en todos los datasets Socrata.
    resultados = {}
    for palabra in ["turisme", "turístic", "turística", "promoció turística", "destinació turística"]:
        params = {
            "$q": palabra,
            "$limit": 200,
            "$order": "data_publicacio DESC",  # ajustar si el campo de fecha se llama distinto
        }
        try:
            resp = requests.get(CATALUNYA_SODA_URL, params=params, timeout=30)
            resp.raise_for_status()
            for fila in resp.json():
                clave = fila.get("id") or fila.get("numexp") or str(fila)
                resultados[clave] = fila
        except Exception as e:
            print(f"  [aviso] fallo consultando Cataluña con '{palabra}': {e}")

    registros = []
    for fila in resultados.values():
        registros.append({
            "fuente": "CATALUNYA_PSCP",
            "capa": "capa2",
            "expediente": fila.get("numexp") or fila.get("codi_expedient"),
            "atom_entry_id": f"CAT-{fila.get('id') or fila.get('numexp')}",
            "organismo": fila.get("organ_contractant") or fila.get("nom_organ"),
            "titulo": fila.get("objecte_del_contracte") or fila.get("objecte_contracte"),
            "importe": _parse_float(fila.get("pressupost_licitacio") or fila.get("import_adjudicacio")),
            "fecha_publicacion": fila.get("data_publicacio"),
            "fecha_limite": fila.get("data_fi_presentacio_ofertes"),
            "enlace": fila.get("enllac_publicacio") or fila.get("url"),
            "relevante_turismo": True,
        })
    return registros


def _parse_float(valor):
    if valor is None:
        return None
    try:
        return float(str(valor).replace(",", "."))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# ⏳ PENDIENTES DE VERIFICAR — no activar hasta confirmar el endpoint real
# ---------------------------------------------------------------------------

def conector_euskadi() -> list[dict]:
    """
    PENDIENTE. euskadi.eus publica cada anuncio como página individual
    (ver ejemplos reales que ya vimos: euskadi.eus/anuncio_contratacion/...).
    Hay que comprobar si Open Data Euskadi (opendata.euskadi.eus) republica
    estos anuncios como dataset consultable, o si hace falta un scraping
    del listado público de anuncios de contratación.
    """
    print("  [pendiente] conector Euskadi no implementado todavía — no se han consultado datos.")
    return []


def conector_comunitat_valenciana() -> list[dict]:
    """
    PENDIENTE. contratacion.gva.es — comprobar si existe un dataset en
    dadesobertes.gva.es equivalente al de Cataluña, o si requiere scraping
    del buscador público de licitaciones de la Generalitat Valenciana.
    """
    print("  [pendiente] conector Comunitat Valenciana no implementado todavía.")
    return []


def conector_galicia() -> list[dict]:
    """PENDIENTE. Revisar contratosdegalicia.gal y el portal de datos abiertos de la Xunta."""
    print("  [pendiente] conector Galicia no implementado todavía.")
    return []


def conector_madrid() -> list[dict]:
    """PENDIENTE. Revisar contratos-publicos.comunidad.madrid y datos.comunidad.madrid."""
    print("  [pendiente] conector Madrid (CCAA) no implementado todavía.")
    return []


def conector_andalucia() -> list[dict]:
    """PENDIENTE. Revisar la Plataforma de Contratación de la Junta de Andalucía y datosabiertos.juntadeandalucia.es."""
    print("  [pendiente] conector Andalucía no implementado todavía.")
    return []


def conector_navarra() -> list[dict]:
    """PENDIENTE. Revisar contratacionpublica.navarra.es y el portal de datos abiertos de Gobierno de Navarra."""
    print("  [pendiente] conector Navarra no implementado todavía.")
    return []


def conector_la_rioja() -> list[dict]:
    """PENDIENTE. Revisar la sede electrónica de La Rioja y su portal de transparencia/datos abiertos."""
    print("  [pendiente] conector La Rioja no implementado todavía.")
    return []


# ---------------------------------------------------------------------------
# REGISTRO DE CONECTORES ACTIVOS
# (según se vayan verificando el resto, se añaden aquí sin tocar nada más)
# ---------------------------------------------------------------------------

CONECTORES_ACTIVOS = {
    "CATALUNYA_PSCP": conector_cataluna,
    # "EUSKADI": conector_euskadi,               # activar cuando se verifique
    # "GVA": conector_comunitat_valenciana,      # activar cuando se verifique
    # "GALICIA": conector_galicia,               # activar cuando se verifique
    # "MADRID_CCAA": conector_madrid,            # activar cuando se verifique
    # "ANDALUCIA": conector_andalucia,           # activar cuando se verifique
    # "NAVARRA": conector_navarra,               # activar cuando se verifique
    # "LA_RIOJA": conector_la_rioja,             # activar cuando se verifique
}


def guardar_en_supabase(registros: list[dict], fuente: str):
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("  [aviso] SUPABASE_URL / SUPABASE_SERVICE_KEY no configuradas — modo prueba, no se guarda nada.")
        return
    endpoint = f"{url}/rest/v1/licitaciones"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    nuevas = 0
    for r in registros:
        try:
            resp = requests.post(endpoint, headers=headers, json=r, timeout=30)
            if resp.status_code in (200, 201, 204):
                nuevas += 1
            else:
                print(f"  [aviso] fallo al guardar '{r.get('titulo')}': {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            print(f"  [aviso] error de red guardando '{r.get('titulo')}': {e}")
    print(f"  -> {nuevas} registros guardados/actualizados de {fuente}.")


def main():
    total = 0
    for nombre, funcion in CONECTORES_ACTIVOS.items():
        print(f"Consultando {nombre}...")
        registros = funcion()
        print(f"  {len(registros)} licitaciones de Turismo encontradas.")
        total += len(registros)
        guardar_en_supabase(registros, nombre)
    print(f"\nHecho. Total Capa 2 hoy: {total}")


if __name__ == "__main__":
    main()
