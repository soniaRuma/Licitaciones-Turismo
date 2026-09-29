"""
Agente de licitaciones de Turismo — minube
Capa 2: plataformas autonómicas de contratación pública (fuera de PLACSP).

Este fichero está organizado como una colección de "conectores", uno por
territorio. Cada conector es una función que devuelve una lista de
licitaciones ya normalizadas (mismo formato que en ingest_placsp.py) para
que se puedan guardar en la misma tabla de Supabase.

ESTADO ACTUAL DE CADA CONECTOR:
  ⏸  CATALUÑA (Socrata) — desactivado el 29/09/2026: Cataluña se lee ahora
                  desde el feed agregado de abajo (trae licitaciones abiertas).
  ✅ CATALUÑA, EUSKADI, GALICIA, MADRID, ANDALUCÍA, NAVARRA, LA RIOJA — un único
                  conector: el feed oficial de PLACSP "Plataformas agregadas"
                  (sindicación 1044). Por ley (LCSP), las plataformas
                  autonómicas reenvían a PLACSP sus convocatorias y resultados
                  mediante "agregación". El formato es el mismo CODICE/ATOM de
                  la Capa 1, así que reutilizamos su parser. Cada registro se
                  etiqueta con su territorio (ver `detectar_territorio`).
                  Pendiente de confirmar en la primera ejecución real con --debug.
  ℹ️  COMUNITAT VALENCIANA — no tiene plataforma agregada: la Generalitat
                  publica directamente en PLACSP, así que ya entra por la Capa 1
                  (ingest_placsp.py). No necesita conector propio.
"""

import os
import sys
import re
import datetime
import requests

# Reutilizamos descarga + parser CODICE + filtros Turismo/Digital de la Capa 1.
# (Funciona porque ambos scripts están en la misma carpeta `scripts/`.)
import ingest_placsp as capa1

DEBUG = "--debug" in sys.argv

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


def conector_cataluna(dias_atras: int = 7) -> list[dict]:
    """
    Consulta el dataset Socrata de PSCP, filtrando por texto de Turismo Y por
    fecha de publicación reciente (últimos `dias_atras` días).

    IMPORTANTE (corregido tras detectar el fallo): la primera versión de este
    conector NO filtraba por fecha, así que traía todo el histórico del
    dataset (hasta registros de 2018-2025), no solo lo publicado recientemente.
    Ahora limitamos con $where sobre "data_publicacio_contracte".
    """
    fecha_corte = (
        datetime.date.today() - datetime.timedelta(days=dias_atras)
    ).strftime("%Y-%m-%dT00:00:00.000")

    resultados = {}
    primero_impreso = False
    for palabra in ["turisme", "turístic", "turística", "promoció turística", "destinació turística"]:
        params = {
            "$q": palabra,
            "$where": f"data_publicacio_contracte >= '{fecha_corte}'",
            "$limit": 500,
        }
        try:
            resp = requests.get(CATALUNYA_SODA_URL, params=params, timeout=30)
            resp.raise_for_status()
            filas = resp.json()
            if not primero_impreso and filas:
                print(f"  [debug] campos reales de un registro: {list(filas[0].keys())}")
                print(f"  [debug] registro completo de ejemplo: {filas[0]}")
                primero_impreso = True
            for fila in filas:
                # Clave única real: id_intern es un identificador estable por publicación.
                # (ANTES usábamos fila.get("id")/"numexp", que no existen en este dataset
                # -> todos los registros generaban "CAT-None" y se pisaban entre sí).
                clave = fila.get("id_intern") or f"{fila.get('codi_expedient')}-{fila.get('numero_lot')}"
                resultados[clave] = fila
        except Exception as e:
            cuerpo = getattr(e, "response", None)
            detalle = cuerpo.text[:300] if cuerpo is not None else ""
            print(f"  [aviso] fallo consultando Cataluña con '{palabra}': {e} {detalle}")

    registros = []
    for fila in resultados.values():
        # Campos reales confirmados el 24/09/2026 mirando un registro de ejemplo real
        # (ver comentario [debug] en el log de ejecución). Antes de esto, estábamos
        # adivinando nombres que no existían.
        enlace_obj = fila.get("enllac_publicacio")
        enlace = enlace_obj.get("url") if isinstance(enlace_obj, dict) else enlace_obj

        # Verificado (24/09/2026): en la muestra real, todos los registros con
        # fase_publicacio "Publicació agregada de contractes" son contratos
        # menores YA resueltos (obligación trimestral de transparencia), igual
        # que "PLACSP_MENOR". Los tratamos como "cerrada". Cualquier otra fase
        # (p.ej. un anuncio de licitación real en curso) se deja como "abierta".
        # También seguimos usando la fecha de adjudicación como señal adicional.
        fase = (fila.get("fase_publicacio") or "").lower()
        estado = "cerrada" if ("agregada" in fase or fila.get("data_adjudicacio_contracte")) else "abierta"

        registros.append({
            "fuente": "CATALUNYA_PSCP",
            "capa": "capa2",
            "expediente": fila.get("codi_expedient"),
            "atom_entry_id": f"CAT-{fila.get('id_intern') or fila.get('codi_expedient')}",
            "organismo": fila.get("nom_organ"),
            "titulo": fila.get("denominacio") or fila.get("objecte_contracte"),
            "importe": _parse_float(
                fila.get("pressupost_licitacio_sense") or fila.get("import_adjudicacio_sense")
            ),
            "fecha_publicacion": (fila.get("data_publicacio_contracte") or "")[:10] or None,
            "fecha_limite": None,  # este dataset no trae plazo de presentación de ofertas
            "enlace": enlace,
            "estado": estado,
            "categoria": "turismo",
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
# 🆕 EUSKADI, GALICIA, MADRID, ANDALUCÍA, NAVARRA, LA RIOJA
#    Fuente: PLACSP — "Licitaciones publicadas en la Plataforma mediante
#    mecanismos de agregación, excluyendo los contratos menores" (sindicación 1044).
#    Descarga oficial del Ministerio de Hacienda, mismo formato que la Capa 1.
# ---------------------------------------------------------------------------

AGREGADAS_ID = "1044"
AGREGADAS_BASE = "PlataformasAgregadasSinMenores"

# Territorios que queremos guardar desde el feed agregado.
# Cataluña se EXCLUYE a propósito porque ya la cubre el conector Socrata de
# arriba (si no, saldría duplicada con otro id). Si algún día prefieres usar
# este feed también para Cataluña (trae plazos y estado real de la licitación),
# añade "CATALUNYA" aquí y desactiva el conector Socrata.
TERRITORIOS_INCLUIDOS = {"EUSKADI", "GALICIA", "MADRID_CCAA", "ANDALUCIA", "NAVARRA", "LA_RIOJA", "CATALUNYA"}
# (29/09/2026) Cataluña pasa a leerse desde este feed: trae licitaciones abiertas
# con su plazo, mientras que el conector Socrata traía casi solo contratos
# menores ya cerrados (3 relevantes frente a 13.000 entradas catalanas al mes).

# Pista 1: dominios web de cada plataforma autonómica. Se buscan en cualquier
# URL que aparezca dentro de la entrada (perfil del contratante, enlaces, etc.).
DOMINIOS_TERRITORIO = {
    "EUSKADI":     ["euskadi.eus", "euskadi.net"],
    "GALICIA":     ["contratosdegalicia.gal", "xunta.gal", "xunta.es"],
    "MADRID_CCAA": ["comunidad.madrid", "madrid.org"],
    "ANDALUCIA":   ["juntadeandalucia.es"],
    "NAVARRA":     ["navarra.es"],
    "LA_RIOJA":    ["larioja.org"],
    "CATALUNYA":   ["gencat.cat", "contractaciopublica.cat"],
}

# Pista 2: código NUTS-2 de la comunidad (campo CountrySubentityCode de CODICE).
NUTS_TERRITORIO = {
    "ES21": "EUSKADI", "ES11": "GALICIA", "ES30": "MADRID_CCAA",
    "ES61": "ANDALUCIA", "ES22": "NAVARRA", "ES23": "LA_RIOJA", "ES51": "CATALUNYA",
}

# Pista 3: nombres en la jerarquía del órgano (ParentLocatedParty / PartyName).
NOMBRES_TERRITORIO = {
    "EUSKADI":     ["país vasco", "pais vasco", "euskadi", "gobierno vasco", "eusko jaurlaritza"],
    "GALICIA":     ["galicia", "xunta"],
    "MADRID_CCAA": ["comunidad de madrid"],
    "ANDALUCIA":   ["andalucía", "andalucia", "junta de andalucía"],
    "NAVARRA":     ["navarra", "nafarroa"],
    "LA_RIOJA":    ["la rioja"],
    "CATALUNYA":   ["cataluña", "catalunya", "generalitat de catalunya"],
}


def detectar_territorio(entry) -> tuple[str | None, str]:
    """
    Devuelve (territorio, pista_usada). Prueba las pistas de más a menos fiable.
    Si ninguna encaja, devuelve (None, "sin_clasificar") y el registro se
    descarta (en --debug se imprimen ejemplos para poder afinar las listas).
    """
    urls, nuts, nombres = [], [], []
    for e in entry.iter():
        local = e.tag.split("}")[-1]
        href = e.get("href")
        if href:
            urls.append(href.lower())
        texto = (e.text or "").strip()
        if not texto:
            continue
        if texto.lower().startswith("http"):
            urls.append(texto.lower())
        if local == "CountrySubentityCode":
            nuts.append(texto.upper())
        if local in ("PartyName", "Name", "CountrySubentity", "CityName"):
            nombres.append(texto.lower())

    for territorio, dominios in DOMINIOS_TERRITORIO.items():
        if any(d in u for u in urls for d in dominios):
            return territorio, "dominio"

    for codigo in nuts:
        if codigo[:4] in NUTS_TERRITORIO:
            return NUTS_TERRITORIO[codigo[:4]], "nuts"

    texto_nombres = " | ".join(nombres)
    for territorio, claves in NOMBRES_TERRITORIO.items():
        if any(c in texto_nombres for c in claves):
            return territorio, "nombre"

    return None, "sin_clasificar"


def conector_placsp_agregadas() -> list[dict]:
    # Misma lógica de lectura que la Capa 1: novedades de los últimos días (o
    # carga histórica si se lanza con meses_historico), con respaldo mensual.
    registros = []
    conteo_territorio, conteo_pista, sin_clasificar, total = {}, {}, [], 0
    for etiqueta, entradas in capa1.obtener_lotes(AGREGADAS_ID, AGREGADAS_BASE, historico=True):
        total += len(entradas)
        for entry in entradas:
            territorio, pista = detectar_territorio(entry)
            conteo_territorio[territorio or "?"] = conteo_territorio.get(territorio or "?", 0) + 1
            conteo_pista[pista] = conteo_pista.get(pista, 0) + 1
            if territorio is None and len(sin_clasificar) < 3:
                sin_clasificar.append(entry)
            if territorio not in TERRITORIOS_INCLUIDOS:
                continue
            reg = capa1.entry_a_registro(entry, fuente=territorio, capa="capa2")
            if reg:
                registros.append(reg)

    print(f"  {total} entradas en el feed agregado.")
    print(f"  Reparto por territorio (todas las entradas): {conteo_territorio}")
    print(f"  Pista usada para clasificar: {conteo_pista}")
    por_fuente = {}
    for r in registros:
        por_fuente[r["fuente"]] = por_fuente.get(r["fuente"], 0) + 1
    print(f"  Relevantes (Turismo/Digital) por territorio: {por_fuente}")

    if DEBUG:
        for e in sin_clasificar:
            texto = " ".join((x.text or "").strip() for x in e.iter() if x.text and x.text.strip())
            print(f"  [debug] entrada sin clasificar: {texto[:600]}")
        if registros:
            print(f"  [debug] ejemplo de registro: {registros[-1]}")

    return registros


# ---------------------------------------------------------------------------
# REGISTRO DE CONECTORES ACTIVOS
# (según se vayan verificando el resto, se añaden aquí sin tocar nada más)
# ---------------------------------------------------------------------------

CONECTORES_ACTIVOS = {
    # Un solo conector para los 7 territorios (Cataluña, Euskadi, Galicia, Madrid,
    # Andalucía, Navarra, La Rioja). Cada registro sale con su propio "fuente".
    # El conector Socrata de Cataluña (conector_cataluna) queda desactivado pero
    # se conserva arriba por si hiciera falta volver a él.
    "PLACSP_AGREGADAS": conector_placsp_agregadas,
}


def guardar_en_supabase(registros: list[dict], fuente: str):
    # Misma función que la Capa 1 (upsert por atom_entry_id, en lotes).
    capa1.guardar_en_supabase(registros, fuente)


def main():
    total = 0
    for nombre, funcion in CONECTORES_ACTIVOS.items():
        print(f"Consultando {nombre}...")
        registros = funcion()
        print(f"  {len(registros)} licitaciones relevantes encontradas.")
        total += len(registros)
        guardar_en_supabase(registros, nombre)
    print(f"\nHecho. Total Capa 2 hoy: {total}")


if __name__ == "__main__":
    main()
