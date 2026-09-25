"""
Agente de licitaciones de Turismo — minube
Capa 1: descarga las licitaciones publicadas en PLACSP (datos abiertos oficiales),
las filtra por relevancia de Turismo, y las guarda en Supabase.

Fuentes oficiales usadas (Ministerio de Hacienda — sindicación PLACSP):
  - Sindicación 643:  Licitaciones (todas, excluyendo contratos menores)
  - Sindicación 1143: Contratos menores

Cómo se ejecuta:
    python scripts/ingest_placsp.py

Variables de entorno necesarias (se configuran como "Secrets" en GitHub, ver GUIA_INSTALACION.md):
    SUPABASE_URL          -> URL del proyecto de Supabase
    SUPABASE_SERVICE_KEY  -> "service_role key" de Supabase (NUNCA la "anon key" aquí)

NOTA IMPORTANTE PARA QUIEN MANTENGA ESTE CÓDIGO:
El formato de PLACSP (estándar CODICE, basado en UBL) es un XML gubernamental
extenso. Este script hace una extracción "best effort" de los campos más
habituales, buscando las etiquetas por su nombre sin importar el namespace
exacto (para ser más robusto a pequeños cambios de versión). Si algún campo
sale vacío de forma sistemática, ejecuta este script con --debug para imprimir
el XML crudo de una entrada y ajustar la función `extraer_campo`.
"""

import os
import re
import sys
import zipfile
import io
import datetime
import urllib.request
import xml.etree.ElementTree as ET

import requests

# ---------------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------------

SINDICACIONES = {
    "PLACSP": {
        "id": "643",
        "base": "licitacionesPerfilesContratanteCompleto3",
        "capa": "capa1",
    },
    "PLACSP_MENOR": {
        "id": "1143",
        "base": "contratosMenoresPerfilesContratantes",
        "capa": "capa1",
    },
}

URL_TEMPLATE = "https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_{sid}/{base}_{yyyymm}.zip"

# Palabras clave de Turismo (título/objeto). Ampliar aquí según se detecten falsos negativos.
PALABRAS_CLAVE = [
    "turismo", "turístic", "turistic", "turisme", "turística", "turístico",
    "promoción turística", "promocion turistica", "marca turística", "marca destino",
    "oficina de turismo", "destino turístico", "campaña de comunicación", "campaña de promoción",
    "convention bureau", "señalética turística", "senaletica turistica",
    "app turística", "plan de marketing turístico", "posicionamiento turístico",
    "fitur", "feria de turismo", "oferta turística", "recursos turísticos",
]

# Palabras clave de Desarrollo Digital (NUEVA CATEGORÍA, independiente de Turismo).
# Se buscan en TODOS los organismos, sin exigir relación con turismo.
PALABRAS_CLAVE_DIGITAL = [
    "desarrollo web", "desarrollo de la web", "diseño web", "página web", "pagina web",
    "portal web", "aplicación móvil", "aplicacion movil", "app móvil", "app movil",
    "desarrollo de aplicaciones", "desarrollo de software", "plataforma digital",
    "plataforma tecnológica", "plataforma web", "transformación digital",
    "inteligencia artificial", "agente ia", "agentes de ia", "chatbot", "asistente virtual",
    "machine learning", "aprendizaje automático", "sistema de información", "software a medida",
    "desarrollo de sistema", "e-commerce", "comercio electrónico", "sistema informático",
    "mantenimiento de aplicaciones", "mantenimiento web", "ciberseguridad",
]

# CPV relevantes (publicidad, marketing, comunicación, servicios turísticos, desarrollo web/apps)
CPV_RELEVANTES_PREFIJOS = [
    "7952",  # servicios de publicidad
    "7954",  # servicios de promoción
    "7934",  # servicios de marketing / relaciones públicas
    "6339",  # información turística
    "5511",  # servicios de alojamiento (a veces asociado a turismo institucional)
    "7220",  # programación de software / consultoría TI (desarrollo digital)
    "9832",  # fotografía / producción audiovisual
]

NS_ATOM = {"atom": "http://www.w3.org/2005/Atom"}

DEBUG = "--debug" in sys.argv


# ---------------------------------------------------------------------------
# DESCARGA
# ---------------------------------------------------------------------------

def descargar_zip(sindicacion_id: str, base: str, yyyymm: str) -> bytes | None:
    url = URL_TEMPLATE.format(sid=sindicacion_id, base=base, yyyymm=yyyymm)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "minube-agente-licitaciones/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except Exception as e:
        print(f"  [aviso] no se pudo descargar {url}: {e}")
        return None


def extraer_entradas_atom(zip_bytes: bytes):
    """Descomprime el zip y devuelve una lista de elementos <entry> (XML) de todos los .atom que contiene."""
    entradas = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for nombre in z.namelist():
            if not nombre.endswith(".atom"):
                continue
            with z.open(nombre) as f:
                try:
                    tree = ET.parse(f)
                    root = tree.getroot()
                    entries = root.findall("atom:entry", NS_ATOM)
                    entradas.extend(entries)
                except ET.ParseError as e:
                    print(f"  [aviso] error parseando {nombre}: {e}")
    return entradas


# ---------------------------------------------------------------------------
# EXTRACCIÓN DE CAMPOS (namespace-agnostic)
# ---------------------------------------------------------------------------

def buscar_texto_por_tag(elem, tag_local: str):
    """Busca recursivamente el primer subelemento cuyo tag (sin namespace) coincida, y devuelve su texto."""
    for e in elem.iter():
        local = e.tag.split("}")[-1]
        if local == tag_local and e.text and e.text.strip():
            return e.text.strip()
    return None


def buscar_todos_por_tag(elem, tag_local: str):
    resultados = []
    for e in elem.iter():
        local = e.tag.split("}")[-1]
        if local == tag_local and e.text and e.text.strip():
            resultados.append(e.text.strip())
    return resultados


def es_relevante_turismo(titulo: str, organismo: str, cpvs: list[str], texto_completo: str = "") -> bool:
    texto = f"{titulo or ''} {organismo or ''} {texto_completo or ''}".lower()
    if any(palabra in texto for palabra in PALABRAS_CLAVE):
        return True
    for cpv in cpvs:
        for prefijo in CPV_RELEVANTES_PREFIJOS:
            if cpv.startswith(prefijo):
                # Si el CPV es "genérico" (publicidad, TI...), exigimos también
                # que el título dé alguna pista de turismo para evitar demasiado ruido.
                if any(p in texto for p in ["turis", "destino", "fitur"]):
                    return True
    return False


def es_relevante_digital(titulo: str, organismo: str, texto_completo: str = "") -> bool:
    """Categoría independiente de Turismo: cualquier organismo, cualquier área."""
    texto = f"{titulo or ''} {organismo or ''} {texto_completo or ''}".lower()
    return any(palabra in texto for palabra in PALABRAS_CLAVE_DIGITAL)


def parsear_fecha(texto: str):
    if not texto:
        return None
    # Los formatos habituales en CODICE son ISO 8601 (YYYY-MM-DD[THH:MM:SS])
    m = re.match(r"(\d{4}-\d{2}-\d{2})", texto)
    return m.group(1) if m else None


def parsear_importe(texto: str):
    if not texto:
        return None
    try:
        return float(texto.replace(",", "."))
    except ValueError:
        return None


def entry_a_registro(entry, fuente: str, capa: str):
    entry_id = buscar_texto_por_tag(entry, "id")
    titulo = buscar_texto_por_tag(entry, "title") or buscar_texto_por_tag(entry, "Name")
    organismo = (
        buscar_texto_por_tag(entry, "PartyName")
        or buscar_texto_por_tag(entry, "RegisteredName")
        or buscar_texto_por_tag(entry, "author")
    )
    cpvs = buscar_todos_por_tag(entry, "ItemClassificationCode")
    importe = parsear_importe(
        buscar_texto_por_tag(entry, "EstimatedOverallContractAmount")
        or buscar_texto_por_tag(entry, "TotalAmount")
        or buscar_texto_por_tag(entry, "TaxExclusiveAmount")
    )
    fecha_pub = parsear_fecha(buscar_texto_por_tag(entry, "updated") or buscar_texto_por_tag(entry, "IssueDate"))
    fecha_limite = parsear_fecha(
        buscar_texto_por_tag(entry, "EndDate") or buscar_texto_por_tag(entry, "TenderSubmissionDeadlinePeriod")
    )
    expediente = buscar_texto_por_tag(entry, "ContractFolderID")

    # RED DE SEGURIDAD (añadida tras detectar que una licitación con "turística"
    # en el título no se estaba capturando): en vez de fiarnos solo del título ya
    # extraído, unimos el texto de TODOS los elementos del registro y buscamos
    # las palabras clave ahí también. Así, aunque la extracción del "título"
    # falle o coja el campo equivocado, si la palabra aparece en cualquier parte
    # del XML (objeto del contrato, descripción, etc.), igualmente se detecta.
    texto_completo = " ".join(
        (e.text or "").strip() for e in entry.iter() if e.text and e.text.strip()
    )

    # Código de estado real del expediente en PLACSP (PUB=publicada, EV=en evaluación,
    # ADJ=adjudicada, RES=resuelta, DES=desierta, ANUL=anulada, PRE/PPT=pendiente...).
    # Solo consideramos "abierta" (aceptando ofertas) el código PUB; el resto se marca
    # como "cerrada" para poder filtrarlas en la web sin depender de la fecha límite,
    # que no siempre viene informada en las actualizaciones posteriores del expediente.
    codigo_estado = buscar_texto_por_tag(entry, "ContractFolderStatusCode")
    estado = "abierta" if codigo_estado == "PUB" else "cerrada"

    # Enlace: PLACSP incluye un <link href="..."> con el detalle del expediente
    enlace = None
    for e in entry.iter():
        local = e.tag.split("}")[-1]
        if local == "link" and e.get("href"):
            enlace = e.get("href")
            break

    es_turismo = es_relevante_turismo(titulo, organismo, cpvs, texto_completo)
    es_digital = es_relevante_digital(titulo, organismo, texto_completo)

    if not es_turismo and not es_digital:
        return None

    categorias = []
    if es_turismo:
        categorias.append("turismo")
    if es_digital:
        categorias.append("digital")

    return {
        "fuente": fuente,
        "capa": capa,
        "expediente": expediente,
        "atom_entry_id": entry_id,
        "organismo": organismo,
        "titulo": titulo,
        "cpv": ", ".join(cpvs) if cpvs else None,
        "importe": importe,
        "fecha_publicacion": fecha_pub,
        "fecha_limite": fecha_limite,
        "enlace": enlace,
        "estado": estado,
        "categoria": ",".join(categorias),
        "relevante_turismo": True,
    }


# ---------------------------------------------------------------------------
# SUPABASE
# ---------------------------------------------------------------------------

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
        "Prefer": "resolution=merge-duplicates",  # upsert por atom_entry_id (unique)
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

    # Log de la ejecución
    try:
        requests.post(
            f"{url}/rest/v1/ejecuciones_log",
            headers=headers,
            json={"fuente": fuente, "nuevas": nuevas, "actualizadas": 0, "errores": ""},
            timeout=15,
        )
    except Exception:
        pass

    print(f"  -> {nuevas} registros guardados/actualizados de {fuente}.")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    hoy = datetime.date.today()
    yyyymm = hoy.strftime("%Y%m")

    total_relevantes = 0

    for fuente, cfg in SINDICACIONES.items():
        print(f"Descargando {fuente} ({yyyymm})...")
        zip_bytes = descargar_zip(cfg["id"], cfg["base"], yyyymm)
        if not zip_bytes:
            continue

        entradas = extraer_entradas_atom(zip_bytes)
        print(f"  {len(entradas)} entradas totales en el fichero de este mes.")

        registros = []
        for entry in entradas:
            reg = entry_a_registro(entry, fuente, cfg["capa"])
            if reg:
                registros.append(reg)

        print(f"  {len(registros)} relevantes de Turismo tras el filtro.")
        total_relevantes += len(registros)

        # Resumen de estados detectados, para verificar que la extracción del código
        # de estado funciona (si "desconocido" sale muy alto, revisar el nombre del tag).
        conteo_estados = {}
        for r in registros:
            e = r.get("estado", "desconocido")
            conteo_estados[e] = conteo_estados.get(e, 0) + 1
        print(f"  Desglose por estado: {conteo_estados}")

        if DEBUG and registros:
            print("  --- Ejemplo de registro extraído (--debug) ---")
            print(registros[0])

        guardar_en_supabase(registros, fuente)

    print(f"\nHecho. Total de licitaciones de Turismo detectadas hoy: {total_relevantes}")


if __name__ == "__main__":
    main()
