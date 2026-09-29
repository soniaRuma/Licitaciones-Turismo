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
import urllib.parse
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
        "historico": True,   # en la carga histórica se leen también meses anteriores
    },
    "PLACSP_MENOR": {
        "id": "1143",
        "base": "contratosMenoresPerfilesContratantes",
        "capa": "capa1",
        "historico": False,  # los contratos menores ya nacen adjudicados: no hace falta histórico
    },
}

# Feed "en directo" de PLACSP: el fichero más reciente, que enlaza (rel="next")
# con los anteriores. Leyendo solo las páginas de los últimos días evitamos
# descargar el mes entero cada día.
URL_FEED = "https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_{sid}/{base}.atom"
DIAS_NOVEDADES = 3      # margen de seguridad: si un día falla la ejecución, el siguiente lo recupera
MAX_PAGINAS_FEED = 150  # tope por si el enlace "next" no terminara nunca

# Carga histórica (solo cuando se lanza a mano con "meses_historico" > 0):
# lee los N últimos meses completos (incluido el actual) para recuperar las
# licitaciones que siguen en plazo aunque se publicaran hace semanas.
try:
    MESES_HISTORICO = int(os.environ.get("MESES_HISTORICO", "0") or 0)
except ValueError:
    MESES_HISTORICO = 0

URL_TEMPLATE = "https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_{sid}/{base}_{yyyymm}.zip"

# ---------------------------------------------------------------------------
# FILTROS (revisados el 29/09/2026)
# Antes se buscaban las palabras en TODO el texto del expediente, incluidos los
# nombres de los organismos "padre" (p.ej. "Consejería de Cultura, Turismo y
# Deporte"), lo que colaba contratos sin relación (un ecógrafo, formación de
# empleo...). Ahora se busca solo en el OBJETO del contrato (título, nombre y
# descripción del proyecto) y, para Digital, también en los códigos CPV.
# ---------------------------------------------------------------------------

# Palabras clave de Turismo (se buscan en el objeto del contrato).
PALABRAS_CLAVE = [
    "turismo", "turístic", "turistic", "turisme", "turística", "turístico",
    "promoción turística", "promocion turistica", "marca turística", "marca destino",
    "oficina de turismo", "destino turístico",
    "convention bureau", "señalética turística", "senaletica turistica",
    "app turística", "plan de marketing turístico", "posicionamiento turístico",
    "fitur", "feria de turismo", "oferta turística", "recursos turísticos",
]

# Organismos cuya actividad ES el turismo (patronatos, consorcios, Turespaña...).
# Todo lo que contraten cuenta como Turismo. Se excluyen los organismos grandes
# que solo llevan "Turismo" en su nombre junto a otras áreas (ministerios,
# consejerías...), porque contratan de todo.
ORGANISMO_TURISTICO = ["turism", "turisme", "turístic", "turistic", "turespaña", "turespana"]
ORGANISMO_GENERICO = [
    "ministerio", "consejería", "consejeria", "conselleria", "consellería",
    "departamento", "departament", "vicepresidencia", "vicepresidència",
    "secretaría general", "secretaria general",
]

# CPV de marketing/publicidad/turismo: cuentan como Turismo solo si el objeto
# del contrato también habla de turismo o destino.
CPV_RELEVANTES_PREFIJOS = [
    "7952",  # servicios de publicidad
    "7954",  # servicios de promoción
    "7934",  # servicios de marketing / relaciones públicas
    "6339",  # información turística
    "5511",  # servicios de alojamiento
    "9832",  # fotografía / producción audiovisual
]

# Palabras clave de Desarrollo Digital (en el objeto del contrato).
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

# CPV de Desarrollo Digital: basta con que el contrato tenga uno de ellos.
CPV_DIGITAL_PREFIJOS = [
    "722",       # 72200000-72268000: programación, consultoría, desarrollo y mantenimiento de software
    "7241",      # 72410000-72417000: proveedores de servicios (diseño web 72413, hosting 72415, aplicaciones/SaaS 72416...)
    "72000000",  # servicios TI genéricos
]

NS_ATOM = {"atom": "http://www.w3.org/2005/Atom"}

DEBUG = "--debug" in sys.argv


# ---------------------------------------------------------------------------
# DESCARGA
# ---------------------------------------------------------------------------

def descargar_url(url: str, timeout: int = 90) -> bytes | None:
    """Descarga una URL mostrando el progreso (tamaño y tiempo)."""
    inicio = datetime.datetime.now()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "minube-agente-licitaciones/1.0"})
        partes, total, siguiente_aviso = [], 0, 20 * 1024 * 1024
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            while True:
                bloque = resp.read(1024 * 1024)
                if not bloque:
                    break
                partes.append(bloque)
                total += len(bloque)
                if total >= siguiente_aviso:
                    seg = (datetime.datetime.now() - inicio).total_seconds()
                    print(f"    ... {total / 1e6:.0f} MB descargados en {seg:.0f} s")
                    siguiente_aviso += 20 * 1024 * 1024
        return b"".join(partes)
    except Exception as e:
        print(f"  [aviso] no se pudo descargar {url}: {e}")
        return None


def descargar_zip(sindicacion_id: str, base: str, yyyymm: str) -> bytes | None:
    url = URL_TEMPLATE.format(sid=sindicacion_id, base=base, yyyymm=yyyymm)
    inicio = datetime.datetime.now()
    datos = descargar_url(url, timeout=120)
    if datos:
        seg = (datetime.datetime.now() - inicio).total_seconds()
        print(f"  Fichero {yyyymm}: {len(datos) / 1e6:.1f} MB en {seg:.0f} s")
    return datos


def _fecha_entrada(entry):
    texto = entry.findtext("atom:updated", default="", namespaces=NS_ATOM).strip()
    try:
        dt = datetime.datetime.fromisoformat(texto.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def leer_feed_novedades(sindicacion_id: str, base: str, dias: int) -> list:
    """
    Lee el feed en directo de PLACSP página a página (de la más reciente hacia
    atrás) hasta cubrir los últimos `dias` días. Devuelve las entradas en orden
    cronológico (la más antigua primero), para que al guardar gane la versión
    más reciente de cada expediente. Lanza una excepción si algo falla.
    """
    limite = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=dias)
    url = URL_FEED.format(sid=sindicacion_id, base=base)
    vistos, entradas, paginas = set(), [], 0
    while url and paginas < MAX_PAGINAS_FEED and url not in vistos:
        vistos.add(url)
        datos = descargar_url(url)
        if datos is None:
            raise RuntimeError(f"no se pudo leer la página {paginas + 1} del feed")
        raiz = ET.fromstring(datos)
        paginas += 1
        pagina = raiz.findall("atom:entry", NS_ATOM)
        fechas = [f for f in (_fecha_entrada(e) for e in pagina) if f]
        for e in pagina:
            f = _fecha_entrada(e)
            if f is None or f >= limite:
                entradas.append(e)
        # Si TODA la página es anterior al límite, ya hemos cubierto el periodo.
        if not pagina or (fechas and max(fechas) < limite):
            break
        siguiente = raiz.find("atom:link[@rel='next']", NS_ATOM)
        url = urllib.parse.urljoin(url, siguiente.get("href")) if siguiente is not None else None
    if paginas == 0:
        raise RuntimeError("el feed no devolvió ninguna página")
    print(f"  Feed en directo: {paginas} páginas leídas, {len(entradas)} entradas de los últimos {dias} días.")
    entradas.reverse()
    return entradas


def _meses_atras(n: int) -> list[str]:
    """Los n últimos meses (incluido el actual) como 'YYYYMM', del más antiguo al actual."""
    hoy = datetime.date.today()
    año, mes, meses = hoy.year, hoy.month, []
    for _ in range(max(n, 1)):
        meses.append(f"{año}{mes:02d}")
        mes -= 1
        if mes == 0:
            año, mes = año - 1, 12
    return list(reversed(meses))


def obtener_lotes(sindicacion_id: str, base: str, historico: bool = False):
    """
    Devuelve, uno a uno y en orden cronológico, los lotes de entradas a procesar:
      - Carga histórica (MESES_HISTORICO > 0 y la fuente lo admite): un lote por mes.
      - Día normal: las novedades de los últimos DIAS_NOVEDADES días desde el feed
        en directo; si el feed falla, se recurre (como antes) al fichero del mes.
    """
    if historico and MESES_HISTORICO > 0:
        print(f"  CARGA HISTÓRICA: últimos {MESES_HISTORICO} meses.")
        for ym in _meses_atras(MESES_HISTORICO):
            datos = descargar_zip(sindicacion_id, base, ym)
            if datos:
                yield ym, extraer_entradas_atom(datos)
        return

    try:
        yield "novedades", leer_feed_novedades(sindicacion_id, base, DIAS_NOVEDADES)
        return
    except Exception as e:
        print(f"  [aviso] el feed en directo falló ({e}). Uso el fichero mensual como respaldo.")

    hoy = datetime.date.today()
    meses = _meses_atras(2) if hoy.day <= DIAS_NOVEDADES else _meses_atras(1)
    for ym in meses:
        datos = descargar_zip(sindicacion_id, base, ym)
        if datos:
            yield ym, extraer_entradas_atom(datos)


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


def texto_objeto(entry) -> str:
    """Título + nombre y descripción del proyecto (y de sus lotes). Sin organismos ni resúmenes."""
    partes = [entry.findtext("atom:title", default="", namespaces=NS_ATOM)]
    for e in entry.iter():
        if e.tag.split("}")[-1] == "ProcurementProject":
            for hijo in e:
                if hijo.tag.split("}")[-1] in ("Name", "Description") and hijo.text:
                    partes.append(hijo.text.strip())
    return " ".join(p for p in partes if p)


def es_organismo_turistico(organismo: str) -> bool:
    org = (organismo or "").lower()
    return any(k in org for k in ORGANISMO_TURISTICO) and not any(g in org for g in ORGANISMO_GENERICO)


def es_relevante_turismo(titulo: str, organismo: str, cpvs: list[str], objeto: str = "") -> bool:
    texto = f"{titulo or ''} {objeto or ''}".lower()
    if any(palabra in texto for palabra in PALABRAS_CLAVE):
        return True
    if es_organismo_turistico(organismo):
        return True
    for cpv in cpvs:
        if any(cpv.startswith(p) for p in CPV_RELEVANTES_PREFIJOS):
            if any(p in texto for p in ["turis", "destino", "fitur"]):
                return True
    return False


def es_relevante_digital(titulo: str, cpvs: list[str], objeto: str = "") -> bool:
    """Categoría independiente de Turismo: cualquier organismo, cualquier área."""
    texto = f"{titulo or ''} {objeto or ''}".lower()
    if any(palabra in texto for palabra in PALABRAS_CLAVE_DIGITAL):
        return True
    return any(cpv.startswith(p) for cpv in cpvs for p in CPV_DIGITAL_PREFIJOS)


def extraer_organismo(entry):
    """
    Órgano de contratación. En CODICE el nombre está en
    LocatedContractingParty > Party > PartyName > Name (el texto NO está en
    PartyName directamente). Se evita coger el de ParentLocatedParty, que es el
    organismo "padre" (p.ej. la consejería de la que depende).
    """
    for e in entry.iter():
        if e.tag.split("}")[-1] == "LocatedContractingParty":
            for hijo in e:
                if hijo.tag.split("}")[-1] == "Party":
                    for n in hijo.iter():
                        if n.tag.split("}")[-1] == "Name" and n.text and n.text.strip():
                            return n.text.strip()
    # Respaldo: primer nombre de parte que aparezca, o el autor del feed.
    for e in entry.iter():
        if e.tag.split("}")[-1] == "PartyName":
            for n in e.iter():
                if n.text and n.text.strip():
                    return n.text.strip()
    return buscar_texto_por_tag(entry, "RegisteredName") or buscar_texto_por_tag(entry, "name")


def extraer_fecha_limite(entry):
    """
    Fecha fin de presentación de ofertas. Antes se cogía la PRIMERA <EndDate> del
    XML, que a veces es la fecha fin de EJECUCIÓN del contrato. Ahora se busca
    dentro de <TenderSubmissionDeadlinePeriod>, que es el plazo de ofertas.
    """
    for e in entry.iter():
        if e.tag.split("}")[-1] == "TenderSubmissionDeadlinePeriod":
            for hijo in e.iter():
                if hijo.tag.split("}")[-1] == "EndDate" and hijo.text:
                    return parsear_fecha(hijo.text.strip())
    return None


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
    organismo = extraer_organismo(entry)
    cpvs = buscar_todos_por_tag(entry, "ItemClassificationCode")
    importe = parsear_importe(
        buscar_texto_por_tag(entry, "EstimatedOverallContractAmount")
        or buscar_texto_por_tag(entry, "TotalAmount")
        or buscar_texto_por_tag(entry, "TaxExclusiveAmount")
    )
    fecha_pub = parsear_fecha(buscar_texto_por_tag(entry, "updated") or buscar_texto_por_tag(entry, "IssueDate"))
    fecha_limite = extraer_fecha_limite(entry)
    expediente = buscar_texto_por_tag(entry, "ContractFolderID")

    objeto = texto_objeto(entry)

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

    es_turismo = es_relevante_turismo(titulo, organismo, cpvs, objeto)
    es_digital = es_relevante_digital(titulo, cpvs, objeto)

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

TAMANO_LOTE = 500  # registros por petición a Supabase


def guardar_en_supabase(registros: list[dict], fuente: str):
    """
    Guarda (inserta o ACTUALIZA) los registros en Supabase, en lotes de 500.

    CORREGIDO (28/09/2026): antes se enviaba un registro por petición y sin
    indicar la columna de conflicto. Como la clave primaria de la tabla es
    `id` (y no `atom_entry_id`), Supabase no sabía que debía actualizar el
    registro existente y devolvía un error 409 "duplicate key" por cada
    licitación ya guardada. Consecuencias: (1) miles de peticiones fallidas,
    que hacían que el paso tardara casi una hora, y (2) los cambios de estado
    (p.ej. una licitación que pasa de abierta a adjudicada) NUNCA se guardaban.
    Ahora usamos `?on_conflict=atom_entry_id` y enviamos en lotes.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("  [aviso] SUPABASE_URL / SUPABASE_SERVICE_KEY no configuradas — modo prueba, no se guarda nada.")
        return

    # Un mismo expediente puede aparecer varias veces en el fichero del mes (cada
    # actualización es una entrada nueva con el mismo id). Nos quedamos con la
    # última, que es la más reciente; además, Supabase rechaza un lote que
    # contenga dos veces el mismo atom_entry_id.
    ahora = datetime.datetime.now(datetime.timezone.utc).isoformat()
    unicos = {}
    for r in registros:
        if r.get("atom_entry_id"):
            unicos[r["atom_entry_id"]] = {**r, "actualizado_en": ahora}
    registros = list(unicos.values())

    endpoint = f"{url}/rest/v1/licitaciones?on_conflict=atom_entry_id"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    guardadas, errores = 0, []
    for i in range(0, len(registros), TAMANO_LOTE):
        lote = registros[i:i + TAMANO_LOTE]
        try:
            resp = requests.post(endpoint, headers=headers, json=lote, timeout=120)
            if resp.status_code in (200, 201, 204):
                guardadas += len(lote)
                continue
            msg = f"lote {i // TAMANO_LOTE + 1}: {resp.status_code} {resp.text[:300]}"
        except Exception as e:
            msg = f"lote {i // TAMANO_LOTE + 1}: error de red {e}"
        print(f"  [aviso] fallo al guardar {msg}")
        errores.append(msg)

    try:
        requests.post(
            f"{url}/rest/v1/ejecuciones_log",
            headers={k: v for k, v in headers.items() if k != "Prefer"},
            json={"fuente": fuente, "nuevas": guardadas, "actualizadas": 0,
                  "errores": " | ".join(errores)[:2000]},
            timeout=15,
        )
    except Exception:
        pass

    print(f"  -> {guardadas} de {len(registros)} registros guardados/actualizados de {fuente}.")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def cerrar_vencidas():
    """
    Marca como 'cerrada' toda licitación 'abierta' cuya fecha límite ya pasó.
    Así la web solo muestra lo que está EN PLAZO, aunque PLACSP no haya publicado
    todavía el cambio de estado del expediente.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return
    hoy = datetime.date.today().isoformat()
    try:
        resp = requests.patch(
            f"{url}/rest/v1/licitaciones?estado=eq.abierta&fecha_limite=lt.{hoy}",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json", "Prefer": "return=minimal,count=exact"},
            json={"estado": "cerrada"}, timeout=60,
        )
        cerradas = resp.headers.get("Content-Range", "").split("/")[-1]
        print(f"Plazos vencidos: {cerradas or '?'} licitaciones pasadas a 'cerrada' (respuesta {resp.status_code}).")
    except Exception as e:
        print(f"  [aviso] no se pudieron cerrar las vencidas: {e}")


def main():
    total_relevantes = 0

    for fuente, cfg in SINDICACIONES.items():
        print(f"Consultando {fuente}...")
        registros, total_entradas = [], 0
        for etiqueta, entradas in obtener_lotes(cfg["id"], cfg["base"], cfg.get("historico", False)):
            total_entradas += len(entradas)
            antes = len(registros)
            for entry in entradas:
                reg = entry_a_registro(entry, fuente, cfg["capa"])
                if reg:
                    registros.append(reg)
            print(f"  [{etiqueta}] {len(entradas)} entradas, {len(registros) - antes} relevantes.")

        conteo = {}
        for r in registros:
            for c in r["categoria"].split(","):
                conteo[c] = conteo.get(c, 0) + 1
            conteo[r["estado"]] = conteo.get(r["estado"], 0) + 1
        print(f"  Total {fuente}: {total_entradas} entradas, {len(registros)} relevantes. Desglose: {conteo}")

        if DEBUG and registros:
            print("  --- Ejemplo de registro extraído (--debug) ---")
            print(registros[-1])

        guardar_en_supabase(registros, fuente)
        total_relevantes += len(registros)

    cerrar_vencidas()
    print(f"\nHecho. Total de licitaciones relevantes procesadas: {total_relevantes}")


if __name__ == "__main__":
    main()
