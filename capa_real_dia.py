# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════
 CAPA "ANÁLISIS REAL POR DÍA"  —  módulo complementario de app_sistema_pro.py
 (VERSIÓN ADAPTADA a los archivos nuevos — 1 al 7 de Nov)
═══════════════════════════════════════════════════════════════════════════
Analiza un DÍA concreto con los archivos reales:

  1. SETEO (searchHubsResponse .csv)  ← NUEVO FORMATO
     Columnas: Store ID, Store Name, Status, City, State, ZipCode,
     Delivery Radius, Geocodes("lat,lon"), Date, Day of Week, Supply Window,
     SW Max Cap, SW Coverage, SW Status, ...
     REGLAS DE FILTRADO:
       • Status == "Store Active"      (solo tiendas activas)
       • SW Status == "ENABLED"        (solo ventanas encendidas)
       • La FECHA del día se convierte a DÍA DE LA SEMANA (Day of Week)
         y se cruza con la columna Day of Week del seteo.
       • Si una tienda tiene varias SW ENABLED ese día → se SUMA su SW Max Cap.
       • Coordenadas desde Geocodes ("lat,lon"); radio = SW Coverage; CP = ZipCode.
     → Solo se dibujan las zonas que cumplen todo lo anterior (círculo = Store Name).

  2. GEOPUNTOS — DOS archivos (paquetes ENTREGADOS):
       • PDP  (.xlsx/.csv): paquetes entregados que SÍ cumplen perfil/cobertura.
       • PDNP (.xlsx/.csv): paquetes entregados que NO cumplen perfil o cobertura.
     Columnas: tracking_id, nodo, fecha, latitud, longitud (+ tiene_cobertura,
     cumple_perfil en PDNP).  → puntitos en el mapa:
       🟢 PDP  = verde     🔴 PDNP = rojo     ⚪ no asignado = gris
     Los que NO traen coordenadas se descartan.

  3. RAW DATA (Raw_Data_at_Tracking .xlsx/.csv): Dispatch Day, tracking_id,
     Delivery Station, Employee, state, postal_code, last_shipment_status, ...
     → se usa para mapear tracking_id → Employee (repartidor = zona = Store Name).

  4. ELEGIBILIDAD (Data Aurora Dia .xlsx): event_day, zone(nodo),
     destination_postal_code, libre.  → paquetes elegibles por CP y día.

  5. TOTAL_DATA (Total_Data_for_Downl... .xlsx)  ← OPCIONAL (enriquecimiento)
     Columnas: event_day, station_code, store_name, store_id,
     Tier_Allocated_Volume, Tier_Capacity_Setting, risk_level,
     allocated_packages, set_capacity, routed, PVA, ...
     → usado en el PLAN DE CONFIRMACIÓN como histórico (routed) y
       capacidad real (set_capacity), + risk_level/PVA para probabilidad.
     Si no se sube, el plan usa la lógica histórica desde geopuntos.

FLUJO DE ASIGNACIÓN de cada paquete (punto) a su zona (Store Name):
  a) tracking_id del geopunto → Employee (Raw) → Store Name (Seteo).
     (Employee == Store Name: el repartidor ES la zona.)
  b) RESPALDO geométrico: si el tracking no cruza, el punto se asigna a la
     zona cuyo círculo lo contiene; si lo contienen varias (traslape), a la
     MÁS CERCANA al centro.
  c) Si no cae en ninguna zona ni cruza por tracking → NO ASIGNADO (gris).

El color del punto (🟢 PDP / 🔴 PDNP) depende SIEMPRE de su archivo de origen,
no de la asignación. El conteo por círculo es COMPLETO: un punto dentro de
varias zonas cuenta para cada una.

Uso desde app_sistema_pro.py:
    from capa_real_dia import render_analisis_real_dia
    render_analisis_real_dia()
"""

import io
import os
import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

try:
    import folium
    from folium.plugins import HeatMap
except Exception:
    folium = None
    HeatMap = None

M_GRADO = 111139.0  # metros por grado (aprox.)

# Mapeo día de la semana (inglés, como viene en el seteo) → índice weekday()
_DOW_EN = {
    0: "MONDAY", 1: "TUESDAY", 2: "WEDNESDAY", 3: "THURSDAY",
    4: "FRIDAY", 5: "SATURDAY", 6: "SUNDAY",
}


# ═══════════════════════════════════════════════════════════════════════
# 🔧 utilidades
# ═══════════════════════════════════════════════════════════════════════
def _norm_txt(s):
    return str(s).strip().upper()


def _norm_trk(serie):
    """
    Normaliza tracking_id para que el MATCH entre PDP/PDNP y Raw sea robusto.
    Resuelve los casos que rompen el cruce:
      • float '762817576009.0' → '762817576009' (quita el .0 que mete excel/parquet)
      • espacios, mayúsculas, caracteres invisibles
      • NaN/None → cadena vacía
    Se aplica IGUAL en ambos lados (geo y raw) para que coincidan.
    """
    s = serie.astype(str).str.strip().str.upper()
    # quitar sufijo .0 (y .00, etc.) típico de lecturas numéricas
    s = s.str.replace(r"\.0+$", "", regex=True)
    # quitar espacios internos y caracteres no imprimibles
    s = s.str.replace(r"\s+", "", regex=True)
    s = s.replace({"NAN": "", "NONE": "", "NAT": ""})
    return s


def _norm_cp(v):
    try:
        return str(int(float(v))).strip().zfill(5)
    except Exception:
        return str(v).strip().replace(".0", "").zfill(5)


def _norm_fecha(serie):
    """
    Normaliza fechas a texto 'dd/mm/YYYY', robusto ante CUALQUIER mes y formato:
      • datetime64 / Timestamp (lo que entrega parquet/pyarrow)
      • ISO 'YYYY-MM-DD[ HH:MM]'
      • 'DD/MM/YYYY' y 'MM/DD/YYYY' (desambigua por >12; ambiguo → DD/MM)
    Procesa la serie de forma vectorizada; si una fecha no parsea, queda NaT.
    """
    s = pd.Series(serie)

    # 1) Si ya es datetime (parquet suele entregar datetime64) → formato directo
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.strftime("%d/%m/%Y")

    # 2) Normalizar a texto y limpiar hora
    txt = s.astype(str).str.strip()
    txt = txt.str.split(" ").str[0].str.split("T").str[0]
    txt = txt.replace({"": None, "nan": None, "NaT": None, "None": None, "nat": None})

    # 3) ISO YYYY-MM-DD  → parse directo (dayfirst no aplica)
    _is_iso = txt.str.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$", na=False)
    out = pd.Series([None] * len(s), index=s.index, dtype=object)
    if _is_iso.any():
        d_iso = pd.to_datetime(txt[_is_iso], errors="coerce", format="mixed")
        out.loc[_is_iso] = d_iso.dt.strftime("%d/%m/%Y")

    # 4) Resto (dd/mm o mm/dd) → desambiguar por número >12; default dayfirst
    _resto = ~_is_iso & txt.notna()
    if _resto.any():
        d_day = pd.to_datetime(txt[_resto], errors="coerce", dayfirst=True)
        out.loc[_resto] = d_day.dt.strftime("%d/%m/%Y")

    return out


def _fecha_a_dow(fecha_txt):
    """'dd/mm/YYYY' → 'MONDAY'/'TUESDAY'/... (en inglés, como el seteo).
    Usa dayfirst=True porque _norm_fecha ya normaliza TODO a dd/mm/YYYY."""
    d = pd.to_datetime(fecha_txt, errors="coerce", dayfirst=True)
    if pd.isna(d):
        return None
    return _DOW_EN.get(d.weekday())


def _split_geocodes(serie):
    """'lat,lon' → (lat, lon) como dos Series numéricas."""
    s = serie.astype(str).str.replace('"', "", regex=False).str.strip()
    partes = s.str.split(",", n=1, expand=True)
    lat = pd.to_numeric(partes[0], errors="coerce")
    lon = pd.to_numeric(partes[1], errors="coerce") if partes.shape[1] > 1 else pd.Series(np.nan, index=serie.index)
    return lat, lon


def _read_tabla(data_bytes, nombre="", sheet=None, usecols=None):
    """
    Lee CSV / XLSX / CSV.GZ / PARQUET desde bytes según extensión/nombre.
    OPTIMIZADO para velocidad en archivos grandes:
      • PARQUET (.parquet/.pq) → el más rápido y compacto (recomendado para
        PDP/PDNP/Raw pesados). Lee solo las columnas de usecols si se indica.
      • CSV.GZ (.csv.gz/.gz)   → CSV comprimido; pandas lo descomprime solo.
      • CSV  → engine 'pyarrow' (multinúcleo, muy rápido); fallback a C-engine.
      • XLSX → openpyxl (ligero en RAM).
    usecols: lista opcional de columnas a leer (acelera y reduce memoria).
    """
    nombre = (nombre or "").lower()

    # ── PARQUET ──
    if nombre.endswith((".parquet", ".pq")):
        try:
            return pd.read_parquet(io.BytesIO(data_bytes), columns=usecols)
        except Exception:
            # si usecols no coincide con el esquema, leer todo
            return pd.read_parquet(io.BytesIO(data_bytes))

    # ── EXCEL ──
    if nombre.endswith((".xlsx", ".xls", ".xlsm")):
        try:
            return pd.read_excel(io.BytesIO(data_bytes),
                                 sheet_name=sheet if sheet is not None else 0,
                                 usecols=usecols, engine="openpyxl")
        except Exception:
            return pd.read_excel(io.BytesIO(data_bytes),
                                 sheet_name=sheet if sheet is not None else 0)

    # ── CSV GZIP ── (pandas detecta compresión por extensión; forzamos gzip)
    if nombre.endswith((".csv.gz", ".gz", ".gzip")):
        try:
            return pd.read_csv(io.BytesIO(data_bytes), usecols=usecols,
                               compression="gzip", low_memory=False)
        except Exception:
            return pd.read_csv(io.BytesIO(data_bytes), compression="gzip",
                               low_memory=False)

    # ── CSV normal: pyarrow primero (muy rápido), luego C-engine ──
    try:
        return pd.read_csv(io.BytesIO(data_bytes), usecols=usecols,
                           engine="pyarrow")
    except Exception:
        pass
    try:
        return pd.read_csv(io.BytesIO(data_bytes), usecols=usecols,
                           low_memory=False)
    except Exception:
        # última opción: era excel disfrazado de csv
        return pd.read_excel(io.BytesIO(data_bytes),
                             sheet_name=sheet if sheet is not None else 0)


def clasificar_nivel_traslape(pct):
    if pct < 25:
        return "🟢 BAJO"
    elif pct < 50:
        return "🟡 MEDIO"
    elif pct < 75:
        return "🟠 ALTO"
    else:
        return "🔴 CRÍTICO"


# ═══════════════════════════════════════════════════════════════════════
# 📥 CARGA + NORMALIZACIÓN — funciones INDIVIDUALES por archivo (cacheadas)
#    Permiten mostrar progreso real archivo-por-archivo en la UI. Cada una
#    está cacheada por sus propios bytes, así repetir un solo archivo no
#    recarga los demás.
# ═══════════════════════════════════════════════════════════════════════
@st.cache_data(show_spinner=False)
def _leer_seteo(seteo_bytes, seteo_name):
    """Lee y normaliza el SETEO (Store Active + SW ENABLED + día semana)."""
    # Leer todo el seteo (las columnas varían: algunas versiones no traen ZipCode).
    seteo = _read_tabla(seteo_bytes, seteo_name)
    seteo.columns = [str(c).strip() for c in seteo.columns]
    seteo["_store_status"] = seteo["Status"].map(_norm_txt)
    seteo = seteo[seteo["_store_status"] == "STORE ACTIVE"].copy()
    seteo["_sw_status"] = seteo["SW Status"].map(_norm_txt)
    seteo = seteo[seteo["_sw_status"] == "ENABLED"].copy()
    seteo["_sn"] = seteo["Store Name"].map(_norm_txt)
    seteo["_dow"] = seteo["Day of Week"].map(_norm_txt)
    _lat, _lon = _split_geocodes(seteo["Geocodes"])
    seteo["LATITUD"] = _lat
    seteo["LONGITUD"] = _lon
    for c in ["SW Coverage", "SW Max Cap"]:
        seteo[c] = pd.to_numeric(seteo[c], errors="coerce")
    seteo["_cp"] = seteo["ZipCode"].map(_norm_cp) if "ZipCode" in seteo.columns else ""
    seteo = seteo.dropna(subset=["LATITUD", "LONGITUD", "SW Coverage"])
    return seteo


@st.cache_data(show_spinner=False)
def _leer_geo(geo_items, categoria):
    """
    Lee UNO o VARIOS archivos de geopuntos (PDP o PDNP) y los combina.
    geo_items: lista de tuplas (bytes, nombre) — acepta varios parquet/csv/xlsx.
    Detecta columnas de lat/lon, fecha y nodo con flexibilidad.
    Devuelve un DataFrame combinado con _cat = categoria.
    """
    if not isinstance(geo_items, (list, tuple)):
        geo_items = [geo_items]
    _frames = []
    for _bts, _name in geo_items:
        if _bts is None:
            continue
        g = _read_tabla(_bts, _name)
        g.columns = [str(c).strip() for c in g.columns]
        if "tracking_id" not in g.columns:
            # buscar variante
            _ct = next((c for c in g.columns if c.lower() in ("tracking_id", "tracking", "trackingid")), None)
            if _ct:
                g = g.rename(columns={_ct: "tracking_id"})
            else:
                continue
        g["tracking_id"] = _norm_trk(g["tracking_id"])
        # Detección CASE-INSENSITIVE y con nombres alternativos (los archivos
        # nuevos usan vended_latitude/longitude, delivery_date, station_code).
        _low = {str(c).strip().lower(): c for c in g.columns}
        def _c(cands):
            for cand in cands:
                if cand.lower() in _low:
                    return _low[cand.lower()]
            return None
        col_lat = _c(["latitud", "latitude", "vended_latitude", "lat"])
        col_lon = _c(["longitud", "longitude", "vended_longitude", "lon", "lng"])
        g["latitude"] = pd.to_numeric(g[col_lat], errors="coerce") if col_lat else np.nan
        g["longitude"] = pd.to_numeric(g[col_lon], errors="coerce") if col_lon else np.nan
        # coords 0 → NaN (se tratan como "sin coordenada", pero NO se descartan:
        # un tracking distinto con coord vacía igual se dibuja dentro de su zona)
        g.loc[g["latitude"] == 0, "latitude"] = np.nan
        g.loc[g["longitude"] == 0, "longitude"] = np.nan
        col_f = _c(["fecha", "event_day", "delivery_date", "date", "dispatch_day"])
        g["_fecha"] = _norm_fecha(g[col_f]) if col_f else None
        col_n = _c(["nodo", "zone", "station_code", "delivery_station", "station"])
        g["_nodo"] = g[col_n].map(_norm_txt) if col_n else ""
        g["_cat"] = categoria
        _frames.append(g[["tracking_id", "latitude", "longitude", "_fecha", "_nodo", "_cat"]].copy())
    if not _frames:
        return pd.DataFrame(columns=["tracking_id", "latitude", "longitude", "_fecha", "_nodo", "_cat"])
    _out = pd.concat(_frames, ignore_index=True)
    # DEDUPLICAR por tracking_id: el MISMO paquete (tracking) no se cuenta/dibuja
    # dos veces, aunque venga repetido con distintas coords. Tracking distinto
    # (aunque coords iguales/vacías) SÍ se conserva.
    _out = _out.drop_duplicates(subset=["tracking_id"], keep="first").reset_index(drop=True)
    return _out


@st.cache_data(show_spinner=False)
def _leer_raw(raw_items):
    """
    Lee UNO o VARIOS archivos Raw y los combina. Devuelve:
      • tr2emp  : dict tracking_id → Employee (zona que lo entregó)
      • tr2status: dict tracking_id → estatus (si existe la columna)
      • raw_pts : DataFrame de los paquetes ENTREGADOS por zona, con columnas
                  tracking_id, latitude, longitude, _emp, _nodo, _fecha.
                  (Son los puntos NEGROS del mapa.)

    raw_items: lista de tuplas (bytes, nombre). Acepta el formato nuevo con
    columnas: dispatch_day, tracking_id, delivery_station, employee,
    latitud, longitud  — y también el formato viejo (Employee, postal_code...).
    Detección de columnas CASE-INSENSITIVE.
    """
    if not isinstance(raw_items, (list, tuple)):
        raw_items = [raw_items]

    _frames = []
    for _bts, _name in raw_items:
        if _bts is None:
            continue
        r = _read_tabla(_bts, _name)
        r.columns = [str(c).strip() for c in r.columns]
        _low = {str(c).strip().lower(): c for c in r.columns}
        def _c(cands):
            for cand in cands:
                if cand.lower() in _low:
                    return _low[cand.lower()]
            return None
        _ctrk = _c(["tracking_id", "tracking", "trackingid"])
        _cemp = _c(["employee", "empleado", "repartidor"])
        _clat = _c(["latitud", "latitude", "lat"])
        _clon = _c(["longitud", "longitude", "lon", "lng"])
        _cnod = _c(["delivery_station", "nodo", "station_code", "station", "node"])
        _cfec = _c(["dispatch_day", "fecha", "event_day", "date"])
        _cst = _c(["last_shipment_status", "last_shipment_status_code", "delivery status"])
        if _ctrk is None:
            continue
        d = pd.DataFrame()
        d["tracking_id"] = _norm_trk(r[_ctrk])
        d["_emp"] = r[_cemp].map(_norm_txt) if _cemp else ""
        d["latitude"] = pd.to_numeric(r[_clat], errors="coerce") if _clat else np.nan
        d["longitude"] = pd.to_numeric(r[_clon], errors="coerce") if _clon else np.nan
        d["_nodo"] = r[_cnod].map(_norm_txt) if _cnod else ""
        d["_fecha"] = _norm_fecha(r[_cfec]) if _cfec else None
        d["_status"] = r[_cst].map(_norm_txt) if _cst else ""
        _frames.append(d)

    if not _frames:
        return {}, {}, pd.DataFrame(columns=["tracking_id", "latitude", "longitude",
                                             "_emp", "_nodo", "_fecha", "_status"])

    raw = pd.concat(_frames, ignore_index=True)
    # diccionarios para el cruce (último gana si hay duplicados)
    _conemp = raw[raw["_emp"] != ""]
    tr2emp = dict(zip(_conemp["tracking_id"], _conemp["_emp"]))
    _const = raw[raw["_status"] != ""]
    tr2status = dict(zip(_const["tracking_id"], _const["_status"])) if len(_const) else {}
    # puntos Raw (con coordenadas válidas) = paquetes entregados por zona
    # NO descartar los sin coordenada: se conservan con lat/lon NaN para que
    # la app les asigne un punto DENTRO del círculo de su zona (employee).
    raw_pts = raw.copy()
    # marcar las coords 0 como NaN (inválidas) para tratarlas como "sin coord"
    raw_pts.loc[raw_pts["latitude"] == 0, "latitude"] = np.nan
    raw_pts.loc[raw_pts["longitude"] == 0, "longitude"] = np.nan
    return tr2emp, tr2status, raw_pts


@st.cache_data(show_spinner=False)
def _leer_elig(elig_bytes, elig_name, elig_sheet):
    """Lee Elegibilidad (Aurora) y normaliza columnas."""
    elig = _read_tabla(elig_bytes, elig_name, sheet=elig_sheet)
    elig.columns = [str(c).strip() for c in elig.columns]
    # Detección de columnas CASE-INSENSITIVE (AURORA usa FECHA, ZONA, CP, VOLUMEN
    # en mayúsculas; otros archivos usan event_day, zone, destination_postal_code,
    # libre). Se busca sin importar mayúsculas/minúsculas.
    _low = {str(c).strip().lower(): c for c in elig.columns}
    def _col(cands):
        for cand in cands:
            if cand.lower() in _low:
                return _low[cand.lower()]
        return None
    _cf = _col(["event_day", "fecha", "date", "dia"])
    _cz = _col(["zone", "zona", "nodo", "station_code", "station"])
    _ccp = _col(["destination_postal_code", "postal_code", "cp", "codigo_postal", "zipcode"])
    _cl = _col(["libre", "volumen", "volume", "free", "disponible", "vol"])
    elig["_fecha"] = _norm_fecha(elig[_cf]) if _cf else None
    elig["_cp"] = elig[_ccp].map(_norm_cp) if _ccp else ""
    elig["_zona"] = elig[_cz].map(_norm_txt) if _cz else ""
    elig["libre"] = pd.to_numeric(elig[_cl], errors="coerce").fillna(0) if _cl else 0
    return elig


@st.cache_data(show_spinner=False)
def _leer_total(total_bytes, total_name):
    """Lee Total_Data (opcional) y normaliza columnas."""
    if total_bytes is None:
        return None
    try:
        total = _read_tabla(total_bytes, total_name)
        total.columns = [str(c).strip() for c in total.columns]
        total["_sn"] = total["store_name"].map(_norm_txt)
        total["_fecha"] = _norm_fecha(total["event_day"]) if "event_day" in total.columns else None
        for c in ["set_capacity", "routed", "allocated_packages", "PVA"]:
            if c in total.columns:
                total[c] = pd.to_numeric(total[c], errors="coerce")
        if "station_code" in total.columns:
            total["_nodo"] = total["station_code"].map(_norm_txt)
        return total
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════
# 📥 CARGA + NORMALIZACIÓN de los archivos (versión monolítica — compatibilidad)
# ═══════════════════════════════════════════════════════════════════════
@st.cache_data(show_spinner=False)
def cargar_archivos(seteo_bytes, seteo_name,
                    pdp_bytes, pdp_name,
                    pdnp_bytes, pdnp_name,
                    raw_bytes, raw_name,
                    elig_bytes, elig_name, elig_sheet,
                    total_bytes=None, total_name=""):
    """Lee todos los archivos (desde bytes para que el cache funcione) y normaliza."""

    # ───────────────── SETEO (NUEVO FORMATO) ─────────────────
    # Solo leemos las columnas que usamos (acelera mucho: el seteo trae 17
    # columnas y cientos de miles de filas). Si alguna no existe, se ignora.
    # Leer todo el seteo (las columnas varían: algunas versiones no traen ZipCode).
    seteo = _read_tabla(seteo_bytes, seteo_name)
    seteo.columns = [str(c).strip() for c in seteo.columns]
    # Filtro 1: tienda activa
    seteo["_store_status"] = seteo["Status"].map(_norm_txt)
    seteo = seteo[seteo["_store_status"] == "STORE ACTIVE"].copy()
    # Filtro 2: ventana encendida
    seteo["_sw_status"] = seteo["SW Status"].map(_norm_txt)
    seteo = seteo[seteo["_sw_status"] == "ENABLED"].copy()
    # Nombres / día de la semana
    seteo["_sn"] = seteo["Store Name"].map(_norm_txt)
    seteo["_dow"] = seteo["Day of Week"].map(_norm_txt)
    # Coordenadas desde Geocodes "lat,lon"
    _lat, _lon = _split_geocodes(seteo["Geocodes"])
    seteo["LATITUD"] = _lat
    seteo["LONGITUD"] = _lon
    for c in ["SW Coverage", "SW Max Cap"]:
        seteo[c] = pd.to_numeric(seteo[c], errors="coerce")
    seteo["_cp"] = seteo["ZipCode"].map(_norm_cp) if "ZipCode" in seteo.columns else ""
    seteo = seteo.dropna(subset=["LATITUD", "LONGITUD", "SW Coverage"])

    # ───────────────── GEOPUNTOS: PDP y PDNP ─────────────────
    def _prep_geo(bts, name, categoria):
        g = _read_tabla(bts, name)
        g.columns = [str(c).strip() for c in g.columns]
        # tracking
        g["tracking_id"] = g["tracking_id"].astype(str).str.strip()
        # lat/lon: aceptar latitud/longitud (sin e) o latitude/longitude
        col_lat = "latitud" if "latitud" in g.columns else ("latitude" if "latitude" in g.columns else None)
        col_lon = "longitud" if "longitud" in g.columns else ("longitude" if "longitude" in g.columns else None)
        g["latitude"] = pd.to_numeric(g[col_lat], errors="coerce") if col_lat else np.nan
        g["longitude"] = pd.to_numeric(g[col_lon], errors="coerce") if col_lon else np.nan
        g = g.dropna(subset=["latitude", "longitude"])
        g = g[(g["latitude"] != 0) & (g["longitude"] != 0)]
        # fecha: aceptar 'fecha' o 'event_day'
        col_f = "fecha" if "fecha" in g.columns else ("event_day" if "event_day" in g.columns else None)
        g["_fecha"] = _norm_fecha(g[col_f]) if col_f else None
        # nodo
        col_n = "nodo" if "nodo" in g.columns else ("zone" if "zone" in g.columns else None)
        g["_nodo"] = g[col_n].map(_norm_txt) if col_n else ""
        g["_cat"] = categoria  # "PDP" | "PDNP"
        cols = ["tracking_id", "latitude", "longitude", "_fecha", "_nodo", "_cat"]
        return g[cols].copy()

    pdp = _prep_geo(pdp_bytes, pdp_name, "PDP")
    pdnp = _prep_geo(pdnp_bytes, pdnp_name, "PDNP")
    geo = pd.concat([pdp, pdnp], ignore_index=True)

    # ───────────────── RAW: tracking → Employee ─────────────────
    raw = _read_tabla(raw_bytes, raw_name)
    raw.columns = [str(c).strip() for c in raw.columns]
    raw["tracking_id"] = raw["tracking_id"].astype(str).str.strip()
    raw["_emp"] = raw["Employee"].map(_norm_txt)
    if "postal_code" in raw.columns:
        raw["_cp"] = raw["postal_code"].map(_norm_cp)
    tr2emp = dict(zip(raw["tracking_id"], raw["_emp"]))
    _col_st = next((c for c in ["last_shipment_status", "last_shipment_status_code",
                                "Delivery Status"] if c in raw.columns), None)
    if _col_st:
        tr2status = dict(zip(raw["tracking_id"], raw[_col_st].map(_norm_txt)))
    else:
        tr2status = {}

    # ───────────────── ELEGIBILIDAD (Aurora) ─────────────────
    elig = _read_tabla(elig_bytes, elig_name, sheet=elig_sheet)
    elig.columns = [str(c).strip() for c in elig.columns]
    # Detección de columnas CASE-INSENSITIVE (AURORA usa FECHA, ZONA, CP, VOLUMEN
    # en mayúsculas; otros archivos usan event_day, zone, destination_postal_code,
    # libre). Se busca sin importar mayúsculas/minúsculas.
    _low = {str(c).strip().lower(): c for c in elig.columns}
    def _col(cands):
        for cand in cands:
            if cand.lower() in _low:
                return _low[cand.lower()]
        return None
    _cf = _col(["event_day", "fecha", "date", "dia"])
    _cz = _col(["zone", "zona", "nodo", "station_code", "station"])
    _ccp = _col(["destination_postal_code", "postal_code", "cp", "codigo_postal", "zipcode"])
    _cl = _col(["libre", "volumen", "volume", "free", "disponible", "vol"])
    elig["_fecha"] = _norm_fecha(elig[_cf]) if _cf else None
    elig["_cp"] = elig[_ccp].map(_norm_cp) if _ccp else ""
    elig["_zona"] = elig[_cz].map(_norm_txt) if _cz else ""
    elig["libre"] = pd.to_numeric(elig[_cl], errors="coerce").fillna(0) if _cl else 0

    # ───────────────── TOTAL_DATA (opcional) ─────────────────
    total = None
    if total_bytes is not None:
        try:
            total = _read_tabla(total_bytes, total_name)
            total.columns = [str(c).strip() for c in total.columns]
            total["_sn"] = total["store_name"].map(_norm_txt)
            total["_fecha"] = _norm_fecha(total["event_day"]) if "event_day" in total.columns else None
            for c in ["set_capacity", "routed", "allocated_packages", "PVA"]:
                if c in total.columns:
                    total[c] = pd.to_numeric(total[c], errors="coerce")
            if "station_code" in total.columns:
                total["_nodo"] = total["station_code"].map(_norm_txt)
        except Exception:
            total = None

    return seteo, geo, tr2emp, tr2status, elig, total


def fechas_disponibles(seteo, geo, raw_pts=None):
    """
    Días disponibles para analizar. La disponibilidad la marcan los GEOPUNTOS
    (PDP/PDNP) y/o el RAW (entregados por zona) — cualquier fuente que tenga
    fechas sirve. Se valida que el día de la semana exista en el seteo, PERO
    si el seteo cubre todos los días (lo normal), no descarta nada.

    Devuelve lista de fechas 'dd/mm/YYYY' ordenadas cronológicamente.
    """
    dows_set = set(str(d).strip().upper() for d in seteo["_dow"].dropna().unique())
    # Reunir fechas de TODAS las fuentes con fecha
    _fechas = set()
    if geo is not None and "_fecha" in geo.columns:
        _fechas |= set(geo["_fecha"].dropna())
    if raw_pts is not None and "_fecha" in getattr(raw_pts, "columns", []):
        _fechas |= set(raw_pts["_fecha"].dropna())
    def _key(d):
        try:
            return (d[6:10], d[3:5], d[:2])
        except Exception:
            return ("", "", "")
    f_todas = sorted(_fechas, key=_key)
    # Filtrar por día de semana del seteo SOLO si el seteo no cubre todos los días
    if dows_set and len(dows_set) < 7:
        out = [d for d in f_todas if _fecha_a_dow(d) in dows_set]
        # si el filtro vacía todo pero hay fechas, no descartar (mostrar todas)
        if not out and f_todas:
            out = f_todas
    else:
        out = f_todas
    return out


# ═══════════════════════════════════════════════════════════════════════
# 🎲 TRASLAPE del día (Monte Carlo vectorizado, por zona y total)
# ═══════════════════════════════════════════════════════════════════════
def calcular_traslape_dia(zonas, n=8000, seed=42):
    np.random.seed(seed)
    res = {}
    if not zonas:
        return res, 0.0
    # ⚡ Salvaguarda de velocidad: con muchas zonas, bajar nº de simulaciones
    #    (la precisión sigue siendo buena y evita que el traslape tarde).
    _nz = len(zonas)
    if _nz > 400:
        n = 1500
    elif _nz > 150:
        n = 3000
    elif _nz > 80:
        n = 5000
    lats = np.array([z["LAT"] for z in zonas])
    lons = np.array([z["LON"] for z in zonas])
    rads = np.array([z["RAD"] for z in zonas])
    noms = np.array([z["NOM"] for z in zonas])
    cos_lat = np.cos(np.radians(float(np.mean(lats))))

    pct_list = []
    for i, z in enumerate(zonas):
        otros = [j for j in range(len(zonas)) if j != i]
        if not otros:
            res[z["NOM"]] = {"pct": 0.0, "nivel": clasificar_nivel_traslape(0.0), "desglose": []}
            pct_list.append(0.0)
            continue
        ang = np.random.uniform(0, 2 * np.pi, n)
        rad = np.sqrt(np.random.uniform(0, 1, n)) * z["RAD"]
        p_lat = z["LAT"] + ((rad * np.sin(ang)) / M_GRADO)
        p_lon = z["LON"] + ((rad * np.cos(ang)) / (M_GRADO * cos_lat))
        lo = lats[otros]; ln = lons[otros]; rr = rads[otros]; nm = noms[otros]
        d2 = ((p_lat[:, None] - lo) ** 2 + ((p_lon[:, None] - ln) * cos_lat) ** 2) * (M_GRADO ** 2)
        en = d2 <= rr ** 2
        pct = float(np.any(en, axis=1).mean() * 100)
        pct = round(pct, 1)
        conteo = en.sum(axis=0)
        desg = [{"NOM": nm[k], "PCT": round(float(conteo[k] / n * 100), 1)}
                for k in range(len(otros)) if conteo[k] > 0]
        desg.sort(key=lambda d: d["PCT"], reverse=True)
        res[z["NOM"]] = {"pct": pct, "nivel": clasificar_nivel_traslape(pct), "desglose": desg}
        pct_list.append(pct)

    pct_total = round(float(np.mean(pct_list)), 2) if pct_list else 0.0
    return res, pct_total


# ═══════════════════════════════════════════════════════════════════════
# 🎯 NÚCLEO: procesar un día → zonas, puntos asignados, métricas
# ═══════════════════════════════════════════════════════════════════════
def procesar_dia(seteo, geo, tr2emp, tr2status, elig, dia, nodo=None, total=None, raw_pts=None):
    """Devuelve un dict 'R' con todo lo necesario para pintar el mapa y las tablas."""
    dow = _fecha_a_dow(dia)

    # 1) ZONAS del día (Store Active + SW ENABLED + Day of Week == dow)
    #    Sumar SW Max Cap de las ventanas ENABLED de ese día por tienda.
    sd = seteo[seteo["_dow"] == dow].copy()

    # agrupar por tienda sumando capacidad de las SW ENABLED
    agg = (sd.groupby("_sn")
             .agg(NOM=("Store Name", "first"),
                  LAT=("LATITUD", "first"),
                  LON=("LONGITUD", "first"),
                  RAD=("SW Coverage", "max"),
                  MAXCAP=("SW Max Cap", "sum"),
                  CP=("_cp", "first"))
             .reset_index())

    # ══════════════════════════════════════════════════════════════════
    # ⚡ FILTRO POR NODO — reduce de ~3000 zonas (todo el país) a las ~57
    #    del nodo seleccionado → acelera MUCHÍSIMO el traslape y el mapa.
    #    El seteo no trae station code, así que identificamos las zonas del
    #    nodo por DOS vías combinadas:
    #      (a) EXACTA: los Employees (repartidores) que entregaron paquetes
    #          del nodo ese día SON las zonas del nodo (Employee == Store Name).
    #      (b) GEOMÉTRICA (respaldo): zonas cuyo centro cae cerca de la nube
    #          de geopuntos del nodo (dentro de ~8 km del punto más cercano).
    #    Si no se puede determinar el nodo, se usan TODAS (comportamiento previo).
    # ══════════════════════════════════════════════════════════════════
    #   SOLO por tracking → Employee: las zonas del nodo son EXACTAMENTE los
    #   repartidores (Employee) que entregaron paquetes de ese nodo ese día.
    #   Un repartidor pertenece a un único nodo, así que esto elimina de raíz
    #   que aparezcan círculos de nodos vecinos del mismo estado.
    _sns_del_nodo = None
    if nodo:
        _nodo_u = _norm_txt(nodo)
        _emps_nodo = set()
        # (1) FUENTE PRINCIPAL: el RAW (delivery_station = nodo, employee = zona).
        #     Es lo más confiable: cada repartidor pertenece a un nodo.
        if raw_pts is not None and len(raw_pts) and "_emp" in raw_pts.columns:
            _rpn = raw_pts
            if "_fecha" in _rpn.columns and _rpn["_fecha"].notna().any():
                _rpn = _rpn[_rpn["_fecha"] == dia]
            if "_nodo" in _rpn.columns:
                _rpn = _rpn[_rpn["_nodo"] == _nodo_u]
            _emps_nodo |= set(_rpn["_emp"].map(_norm_txt).dropna().unique())
        # (2) RESPALDO: geopuntos PDP/PDNP del nodo cruzados por tracking → Employee.
        if "_nodo" in geo.columns:
            _gd_nodo = geo[(geo["_fecha"] == dia) & (geo["_nodo"] == _nodo_u)]
            if len(_gd_nodo):
                _emps2 = _gd_nodo["tracking_id"].map(tr2emp).dropna()
                _emps_nodo |= set(_emps2.map(_norm_txt).unique())
        _emps_nodo.discard("")
        if _emps_nodo:
            _sns_del_nodo = _emps_nodo

    if _sns_del_nodo is not None:
        agg = agg[agg["_sn"].isin(_sns_del_nodo)].copy()
    # Nota: si _sns_del_nodo quedó None (no se pudo determinar el nodo), se
    # muestran TODAS las zonas activas del día — es el único caso en que
    # aparecerían círculos de toda la república.

    zonas = []
    for _, r in agg.iterrows():
        if pd.isna(r["LAT"]) or pd.isna(r["LON"]) or pd.isna(r["RAD"]):
            continue
        zonas.append({
            "NOM": str(r["NOM"]).strip(),
            "_sn": r["_sn"],
            "LAT": float(r["LAT"]),
            "LON": float(r["LON"]),
            "RAD": float(r["RAD"]),
            "MAXCAP": float(r["MAXCAP"]) if pd.notna(r["MAXCAP"]) else 0.0,
            "CP": _norm_cp(r["CP"]) if r["CP"] else "",
        })
    set_sn = {z["_sn"] for z in zonas}

    # 2) GEOPUNTOS del día (con coords) — PDP + PDNP
    gd = geo[geo["_fecha"] == dia].copy()
    if nodo and "_nodo" in gd.columns:
        gd = gd[(gd["_nodo"] == _norm_txt(nodo)) | (gd["_nodo"] == "")]
    gd["_emp"] = gd["tracking_id"].map(tr2emp)
    gd["_status_entrega"] = gd["tracking_id"].map(tr2status)
    gd["_zona_tracking"] = gd["_emp"].where(gd["_emp"].isin(set_sn))

    # Asignar coordenada a los geopuntos (PDP/PDNP) SIN coord: se dibujan DENTRO
    # del círculo de su zona (la del employee vía tracking→Raw), posición
    # determinística por tracking_id. Los que no tienen zona conocida se descartan.
    _sin_g = gd["latitude"].isna() | gd["longitude"].isna()
    if _sin_g.any() and zonas:
        _zc = {z["_sn"]: (z["LAT"], z["LON"], z["RAD"]) for z in zonas}
        _emp_g = gd["_emp"].map(lambda e: _norm_txt(e) if pd.notna(e) else "")
        _trk_g = gd["tracking_id"].astype(str)
        _gla = gd["latitude"].to_numpy(dtype=float).copy()
        _glo = gd["longitude"].to_numpy(dtype=float).copy()
        for _k in np.where(_sin_g.to_numpy())[0]:
            _c = _zc.get(_emp_g.iloc[_k])
            if _c is None:
                continue
            _hh = abs(hash(_trk_g.iloc[_k]))
            _ang = (_hh % 3600) / 3600.0 * 2 * np.pi
            _rr = ((_hh // 3600) % 1000) / 1000.0
            _radpt = np.sqrt(_rr) * _c[2] * 0.9
            _cl = np.cos(np.radians(_c[0]))
            _gla[_k] = _c[0] + (_radpt * np.sin(_ang)) / M_GRADO
            _glo[_k] = _c[1] + (_radpt * np.cos(_ang)) / (M_GRADO * _cl)
        gd["latitude"] = _gla
        gd["longitude"] = _glo
    # descartar los que siguen sin coord (sin zona conocida)
    gd = gd.dropna(subset=["latitude", "longitude"]).copy()

    plat = gd["latitude"].to_numpy()
    plon = gd["longitude"].to_numpy()

    # 3) Asignación geométrica (respaldo) — punto dentro del círculo; si varios, el más cercano
    zona_geom = np.array([None] * len(gd), dtype=object)
    dentro_alguna = np.zeros(len(gd), dtype=bool)
    if zonas and len(gd) > 0:
        sz_lat = np.array([z["LAT"] for z in zonas])
        sz_lon = np.array([z["LON"] for z in zonas])
        sz_rad = np.array([z["RAD"] for z in zonas])
        sz_sn = np.array([z["_sn"] for z in zonas])
        cos_lat = np.cos(np.radians(float(sz_lat.mean())))
        dlat = (plat[:, None] - sz_lat[None, :]) * M_GRADO
        dlon = (plon[:, None] - sz_lon[None, :]) * M_GRADO * cos_lat
        d2 = dlat ** 2 + dlon ** 2
        dentro = d2 <= (sz_rad[None, :] ** 2)
        d2m = np.where(dentro, d2, np.inf)
        idx_min = np.argmin(d2m, axis=1)
        tiene = np.isfinite(d2m[np.arange(len(gd)), idx_min])
        zona_geom = np.where(tiene, sz_sn[idx_min], None)
        dentro_alguna = tiene
    gd["_zona_geom"] = zona_geom
    gd["_dentro_zona"] = dentro_alguna

    # 4) ZONA FINAL: tracking si existe, si no geometría
    zt = gd["_zona_tracking"]
    gd["_zona_final"] = zt.where(zt.notna(), pd.Series(gd["_zona_geom"].values, index=gd.index))
    gd["_asignado"] = gd["_zona_final"].notna()
    gd["_origen_asig"] = np.where(zt.notna(), "tracking",
                                  np.where(gd["_zona_geom"].astype(object).notna(), "geometria", "sin_asignar"))

    # 5) MÉTRICAS por zona (conteo de asignados por tracking/geometría)
    cap_por_zona = gd.loc[gd["_asignado"], "_zona_final"].map(_norm_txt).value_counts().to_dict()

    # 5b) CONTEO POR CÍRCULO separado PDP / PDNP (COMPLETO: cuenta a todas las
    #     zonas que cubren el punto, no solo la más cercana).
    pdp_por_sn, pdnp_por_sn = {}, {}
    if zonas and len(gd) > 0:
        sz_lat = np.array([z["LAT"] for z in zonas])
        sz_lon = np.array([z["LON"] for z in zonas])
        sz_rad = np.array([z["RAD"] for z in zonas])
        sz_sn = np.array([z["_sn"] for z in zonas])
        cos_lat = np.cos(np.radians(float(sz_lat.mean())))
        for cat, dest in (("PDP", pdp_por_sn), ("PDNP", pdnp_por_sn)):
            sub = gd[gd["_cat"] == cat]
            if len(sub) == 0:
                for z in zonas:
                    dest[z["_sn"]] = 0
                continue
            pla = sub["latitude"].to_numpy(); plo = sub["longitude"].to_numpy()
            d2 = ((pla[:, None] - sz_lat[None, :]) * M_GRADO) ** 2 + \
                 ((plo[:, None] - sz_lon[None, :]) * M_GRADO * cos_lat) ** 2
            dentro = d2 <= (sz_rad[None, :] ** 2)
            conteo = dentro.sum(axis=0)
            for k, z in enumerate(zonas):
                dest[z["_sn"]] = int(conteo[k])

    # 6) ELEGIBILIDAD del día por CP (nodo opcional)
    # Elegibilidad del día. Diagnóstico: contar por fecha y por fecha+nodo para
    # detectar si el filtro por nodo (o la fecha) está vaciando los CPs.
    _ed_fecha = elig[elig["_fecha"] == dia].copy()
    _cps_solo_fecha = _ed_fecha["_cp"].nunique()
    _nodos_aurora = sorted([n for n in _ed_fecha["_zona"].dropna().unique() if n]) if "_zona" in _ed_fecha.columns else []
    ed = _ed_fecha
    if nodo and "_zona" in ed.columns:
        _ed_nodo = ed[ed["_zona"] == _norm_txt(nodo)]
        # Si el filtro por nodo deja vacío PERO sí hay CPs en la fecha, es que el
        # nombre del nodo en Aurora no coincide → usar TODA la fecha (no perder datos).
        if len(_ed_nodo) == 0 and len(ed) > 0:
            _elig_nodo_mismatch = True
        else:
            _elig_nodo_mismatch = False
            ed = _ed_nodo
    else:
        _elig_nodo_mismatch = False
    elig_por_cp = ed.groupby("_cp")["libre"].sum().to_dict() if len(ed) else {}
    _elig_diag = {
        "cps_solo_fecha": int(_cps_solo_fecha),
        "cps_final": len(elig_por_cp),
        "nodos_aurora": _nodos_aurora[:20],
        "nodo_pedido": _norm_txt(nodo) if nodo else "",
        "nodo_mismatch": _elig_nodo_mismatch,
    }

    # 7) TRASLAPE del día
    traslape_zona, traslape_total = calcular_traslape_dia(zonas)

    # 8) TOTAL_DATA del día por zona (enriquecimiento opcional)
    total_por_sn = {}
    if total is not None:
        td = total.copy()
        if "_fecha" in td.columns and td["_fecha"].notna().any():
            # usar la fecha más cercana disponible si no coincide exacto
            if dia in set(td["_fecha"]):
                td = td[td["_fecha"] == dia]
        if nodo and "_nodo" in td.columns:
            td = td[(td["_nodo"] == _norm_txt(nodo)) | (td["_nodo"].isna())]
        for _, r in td.iterrows():
            total_por_sn[_norm_txt(r.get("_sn", ""))] = {
                "set_capacity": float(r["set_capacity"]) if pd.notna(r.get("set_capacity")) else None,
                "routed": float(r["routed"]) if pd.notna(r.get("routed")) else None,
                "allocated": float(r["allocated_packages"]) if pd.notna(r.get("allocated_packages")) else None,
                "pva": float(r["PVA"]) if pd.notna(r.get("PVA")) else None,
                "risk": str(r.get("risk_level", "")).strip(),
            }

    total_puntos = len(gd)
    total_asignados = int(gd["_asignado"].sum())
    total_no_asig = total_puntos - total_asignados
    total_dentro_zonas = int(gd["_dentro_zona"].sum())
    total_pdp = int((gd["_cat"] == "PDP").sum())
    total_pdnp = int((gd["_cat"] == "PDNP").sum())

    # ── PUNTOS RAW (negros) = paquetes ENTREGADOS por zona ese día/nodo ──
    #    Se filtran por fecha y nodo (delivery_station). Su "zona" es el Employee.
    _raw_dia = pd.DataFrame(columns=["tracking_id", "latitude", "longitude", "_emp", "_nodo"])
    if raw_pts is not None and len(raw_pts):
        _rp = raw_pts
        if "_fecha" in _rp.columns and _rp["_fecha"].notna().any():
            _rp = _rp[_rp["_fecha"] == dia]
        if nodo and "_nodo" in _rp.columns:
            # ESTRICTO: solo los del nodo seleccionado (no colar vacíos/otros nodos)
            _rp = _rp[_rp["_nodo"] == _norm_txt(nodo)]
        # DEDUPLICAR por tracking_id: el Raw repite el mismo paquete en varias
        # filas (intentos/escaneos). Cada paquete debe contar UNA vez.
        _rp = _rp.drop_duplicates(subset=["tracking_id"], keep="first")

        # ── ASIGNAR coordenada a los paquetes SIN geocoding ──
        # Si un paquete del Raw no trae coordenada (lat/lon NaN), se dibuja
        # DENTRO del círculo de SU zona (la del employee en el seteo), en una
        # posición pseudo-aleatoria estable (determinística por tracking_id).
        _rp = _rp.copy()
        _sin = _rp["latitude"].isna() | _rp["longitude"].isna()
        if _sin.any() and zonas:
            _zc = {z["_sn"]: (z["LAT"], z["LON"], z["RAD"]) for z in zonas}
            _emp_u = _rp["_emp"].map(_norm_txt)
            _trk = _rp["tracking_id"].astype(str)
            _new_lat = _rp["latitude"].to_numpy(dtype=float).copy()
            _new_lon = _rp["longitude"].to_numpy(dtype=float).copy()
            _idx = np.where(_sin.to_numpy())[0]
            for _k in _idx:
                _c = _zc.get(_emp_u.iloc[_k])
                if _c is None:
                    continue  # sin zona conocida → se queda sin coord (se filtrará)
                # ángulo y radio determinísticos a partir del hash del tracking
                _hh = abs(hash(_trk.iloc[_k]))
                _ang = (_hh % 3600) / 3600.0 * 2 * np.pi
                _rr = ((_hh // 3600) % 1000) / 1000.0
                _rad_pt = np.sqrt(_rr) * _c[2] * 0.9   # dentro del 90% del radio
                _cl = np.cos(np.radians(_c[0]))
                _new_lat[_k] = _c[0] + (_rad_pt * np.sin(_ang)) / M_GRADO
                _new_lon[_k] = _c[1] + (_rad_pt * np.cos(_ang)) / (M_GRADO * _cl)
            _rp["latitude"] = _new_lat
            _rp["longitude"] = _new_lon
        # descartar los que siguen sin coord (employee sin zona en el seteo)
        _rp = _rp.dropna(subset=["latitude", "longitude"])

        # ── LIMPIAR coordenadas erróneas ──
        # El employee del Raw = nombre de la zona (Store Name del seteo). Las
        # coordenadas del Raw a veces están mal (de otro estado / GPS malo), lo
        # que hace que un repartidor "aparezca por todo México". Anclamos cada
        # punto a SU zona: si el empleado tiene zona en el seteo, descartamos los
        # puntos que estén a más de RADIO_MAX del centro de su zona.
        if len(_rp) and zonas:
            _z_centro = {z["_sn"]: (z["LAT"], z["LON"]) for z in zonas}
            _RADIO_MAX_M = 25000.0   # 25 km del centro de su zona (tolerante)
            _emp_u = _rp["_emp"].map(_norm_txt).to_numpy()
            _pla = _rp["latitude"].to_numpy(dtype=float)
            _plo = _rp["longitude"].to_numpy(dtype=float)
            _keep = np.ones(len(_rp), dtype=bool)
            _cl = np.cos(np.radians(float(np.nanmean(_pla)))) if len(_pla) else 1.0
            for _k in range(len(_rp)):
                _c = _z_centro.get(_emp_u[_k])
                if _c is None:
                    continue  # empleado sin zona en el seteo → se deja (no se puede anclar)
                _dla = (_pla[_k] - _c[0]) * M_GRADO
                _dlo = (_plo[_k] - _c[1]) * M_GRADO * _cl
                if (_dla*_dla + _dlo*_dlo) > (_RADIO_MAX_M ** 2):
                    _keep[_k] = False   # punto absurdamente lejos de su zona → descartar
            _rp = _rp[_keep]
        _raw_dia = _rp.copy()

    # Conteo del RAW por zona (employee) = paquetes que CADA zona entregó ese día
    # (coincide con los puntos amarillos que se resaltan al seleccionar la zona).
    raw_por_sn = {}
    if len(_raw_dia) and "_emp" in _raw_dia.columns:
        raw_por_sn = _raw_dia["_emp"].map(_norm_txt).value_counts().to_dict()

    # Diagnóstico del MATCH tracking→Raw→zona:
    #   cuántos puntos cruzaron por tracking (tienen Employee en Raw) y cuántos
    #   de esos cayeron en una zona ENABLED del nodo.
    _tiene_emp = int(gd["_emp"].notna().sum()) if "_emp" in gd.columns else 0
    total_match_tracking = int((gd["_origen_asig"] == "tracking").sum()) if "_origen_asig" in gd.columns else 0

    R = {
        "total_con_employee": _tiene_emp,
        "total_match_tracking": total_match_tracking,
        "dia": dia,
        "dow": dow,
        "nodo": nodo or "DVR2",
        "zonas": zonas,
        "puntos": gd[["tracking_id", "latitude", "longitude", "_zona_final",
                      "_asignado", "_origen_asig", "_status_entrega", "_cat"]].copy(),
        "cap_por_zona": cap_por_zona,
        "pdp_por_sn": pdp_por_sn,
        "pdnp_por_sn": pdnp_por_sn,
        "elig_por_cp": elig_por_cp,
        "traslape_zona": traslape_zona,
        "traslape_total": traslape_total,
        "total_por_sn": total_por_sn,
        "total_puntos": total_puntos,
        "total_asignados": total_asignados,
        "total_no_asignados": total_no_asig,
        "total_dentro_zonas": total_dentro_zonas,
        "total_pdp": total_pdp,
        "total_pdnp": total_pdnp,
        "elig_diag": _elig_diag,
        "raw_pts": _raw_dia[["tracking_id", "latitude", "longitude", "_emp", "_nodo"]].copy() if len(_raw_dia) else _raw_dia,
        "raw_por_sn": raw_por_sn,
    }
    return R


# ═══════════════════════════════════════════════════════════════════════
# 📊 TABLAS de análisis descargable
# ═══════════════════════════════════════════════════════════════════════
def analizar_fuera_de_zona(R):
    """
    Analiza, con base en los PUNTOS RAW (paquetes entregados por repartidor),
    cuáles se entregaron FUERA de la zona propia del repartidor.

    Zona propia = el círculo cuyo Store Name == employee (el repartidor es su
    zona). Un paquete está "dentro" si cae en el radio de SU propia zona.
    Si cae fuera, se identifica a qué OTRA zona corresponde (el círculo que lo
    contiene; si varios, el más cercano al centro).

    Devuelve 3 DataFrames:
      • resumen : por repartidor → volumen bruto (total), neto (en su zona),
                  fuera de zona, % fuera.
      • matriz  : zona origen (repartidor) × zona destino (donde cayó) → conteo.
      • detalle : paquete por paquete fuera de zona (tracking, coords, zona
                  propia, zona destino).
    """
    zonas = R.get("zonas", [])
    raw = R.get("raw_pts", None)
    cols_resumen = ["Repartidor (zona)", "Volumen bruto (total)",
                    "Volumen neto (en su zona)", "Fuera de zona", "% fuera"]
    if raw is None or len(raw) == 0 or not zonas:
        return (pd.DataFrame(columns=cols_resumen),
                pd.DataFrame(columns=["Zona origen", "Zona destino", "Paquetes"]),
                pd.DataFrame(columns=["tracking_id", "latitude", "longitude",
                                      "Zona propia (repartidor)", "Zona destino (según coords)"]))

    # Geometría de zonas
    sz_lat = np.array([z["LAT"] for z in zonas], dtype=float)
    sz_lon = np.array([z["LON"] for z in zonas], dtype=float)
    sz_rad = np.array([z["RAD"] for z in zonas], dtype=float)
    sz_sn = np.array([z["_sn"] for z in zonas])          # Store Name normalizado
    sz_nom = np.array([z["NOM"] for z in zonas])         # Store Name display
    _sn2idx = {sn: i for i, sn in enumerate(sz_sn)}
    cos_lat = np.cos(np.radians(float(np.nanmean(sz_lat))))

    rp = raw.copy()
    rp["_emp"] = rp["_emp"].astype(str).map(_norm_txt)
    # SOLO repartidores cuya zona está ENABLED ese día (presente en R["zonas"]).
    # Las zonas que no salieron ese día en el seteo se excluyen del neto/bruto.
    _sns_enabled = set(z["_sn"] for z in zonas)
    rp = rp[rp["_emp"].isin(_sns_enabled)].copy()
    if rp.empty:
        return (pd.DataFrame(columns=cols_resumen),
                pd.DataFrame(columns=["Zona origen", "Zona destino", "Paquetes"]),
                pd.DataFrame(columns=["tracking_id", "latitude", "longitude",
                                      "Zona propia (repartidor)", "Zona destino (según coords)"]))
    plat = rp["latitude"].to_numpy(dtype=float)
    plon = rp["longitude"].to_numpy(dtype=float)
    emp = rp["_emp"].to_numpy()
    trk = rp["tracking_id"].astype(str).to_numpy()

    # distancia² de cada punto a CADA zona (metros²)
    dlat = (plat[:, None] - sz_lat[None, :]) * M_GRADO
    dlon = (plon[:, None] - sz_lon[None, :]) * M_GRADO * cos_lat
    d2 = dlat ** 2 + dlon ** 2
    dentro = d2 <= (sz_rad[None, :] ** 2)                 # (P, Z) ¿dentro del círculo?

    # Para "zona destino" del punto: círculo que lo contiene; si varios, el más cercano
    d2_dentro = np.where(dentro, d2, np.inf)
    idx_destino = np.argmin(d2_dentro, axis=1)
    tiene_destino = np.isfinite(d2_dentro[np.arange(len(rp)), idx_destino])

    resumen = {}   # emp -> {bruto, neto, fuera}
    matriz = {}    # (origen, destino) -> conteo
    filas_det = []

    for k in range(len(rp)):
        e = emp[k]
        if e not in resumen:
            resumen[e] = {"bruto": 0, "neto": 0, "fuera": 0}
        resumen[e]["bruto"] += 1

        # ¿tiene zona propia? (existe un círculo con su nombre)
        zi_propia = _sn2idx.get(e, None)
        en_su_zona = bool(dentro[k, zi_propia]) if zi_propia is not None else False

        if en_su_zona:
            resumen[e]["neto"] += 1
        else:
            resumen[e]["fuera"] += 1
            # zona destino = círculo que lo contiene (más cercano) si existe
            if tiene_destino[k]:
                zdest = sz_nom[idx_destino[k]]
            else:
                zdest = "FUERA DE TODA ZONA"
            matriz[(e, _norm_txt(zdest))] = matriz.get((e, _norm_txt(zdest)), 0) + 1
            filas_det.append({
                "tracking_id": trk[k],
                "latitude": float(plat[k]),
                "longitude": float(plon[k]),
                "Zona propia (repartidor)": e,
                "Zona destino (según coords)": zdest,
            })

    # DataFrame resumen
    fr = []
    for e, v in resumen.items():
        bruto = v["bruto"]; fuera = v["fuera"]
        pct = round(fuera / bruto * 100, 1) if bruto else 0.0
        fr.append({
            "Repartidor (zona)": e,
            "Volumen bruto (total)": bruto,
            "Volumen neto (en su zona)": v["neto"],
            "Fuera de zona": fuera,
            "% fuera": f"{pct}%",
        })
    df_resumen = pd.DataFrame(fr, columns=cols_resumen)
    if not df_resumen.empty:
        df_resumen = df_resumen.sort_values("Fuera de zona", ascending=False).reset_index(drop=True)

    # DataFrame matriz (origen → destino)
    fm = [{"Zona origen": o, "Zona destino": d, "Paquetes": c}
          for (o, d), c in matriz.items()]
    df_matriz = pd.DataFrame(fm, columns=["Zona origen", "Zona destino", "Paquetes"])
    if not df_matriz.empty:
        df_matriz = df_matriz.sort_values("Paquetes", ascending=False).reset_index(drop=True)

    df_detalle = pd.DataFrame(filas_det) if filas_det else pd.DataFrame(
        columns=["tracking_id", "latitude", "longitude",
                 "Zona propia (repartidor)", "Zona destino (según coords)"])
    return df_resumen, df_matriz, df_detalle


def construir_entregas_no_asignadas(R):
    """
    PAQUETES POR LLEVAR por zona = puntos NO asignados por tracking que SÍ caen
    DENTRO del radio de alguna zona → esa zona aún los puede llevar.
    Cada punto se cuenta a la zona MÁS CERCANA entre las que lo contienen.
    """
    zonas = R["zonas"]
    pts = R["puntos"].copy()
    if "_origen_asig" in pts.columns:
        es_sin = pts["_origen_asig"] != "tracking"
    else:
        es_sin = ~pts["_asignado"]
    no = pts[es_sin].copy()

    conteo = {}
    filas = []
    if len(no) and zonas:
        szlat = np.array([z["LAT"] for z in zonas])
        szlon = np.array([z["LON"] for z in zonas])
        szrad = np.array([z["RAD"] for z in zonas])
        sznom = np.array([z["NOM"] for z in zonas])
        cl = np.cos(np.radians(float(szlat.mean())))
        plat = no["latitude"].to_numpy()
        plon = no["longitude"].to_numpy()
        d2 = ((plat[:, None] - szlat[None, :]) * M_GRADO) ** 2 + \
             ((plon[:, None] - szlon[None, :]) * M_GRADO * cl) ** 2
        dentro = d2 <= (szrad[None, :] ** 2)
        d2m = np.where(dentro, d2, np.inf)
        im = np.argmin(d2m, axis=1)
        cae = np.isfinite(d2m[np.arange(len(no)), im])
        trk = no["tracking_id"].to_numpy()
        cat = no["_cat"].to_numpy() if "_cat" in no.columns else np.array([""] * len(no))
        for k in range(len(no)):
            if cae[k]:
                zn = sznom[im[k]]
                conteo[zn] = conteo.get(zn, 0) + 1
                filas.append({
                    "tracking_id": trk[k],
                    "categoria": cat[k],
                    "latitude": float(plat[k]),
                    "longitude": float(plon[k]),
                    "Zona (dentro de su radio)": zn,
                })

    dfA = pd.DataFrame([{
        "Zona": z["NOM"],
        "Paquetes por llevar (no asignados en su radio)": int(conteo.get(z["NOM"], 0)),
    } for z in zonas])
    if not dfA.empty:
        dfA = dfA.sort_values("Paquetes por llevar (no asignados en su radio)",
                              ascending=False).reset_index(drop=True)
    dfB = pd.DataFrame(filas) if filas else pd.DataFrame(
        columns=["tracking_id", "categoria", "latitude", "longitude", "Zona (dentro de su radio)"])
    return dfA, dfB


def construir_tabla_zonas(R, hist_por_zona=None):
    """
    Tabla por VR: Seteo(SW Max Cap), Volumen Despachado(asignados), PDP/PDNP dentro,
    Ocupación %, Traslape, Nivel, + Histórico/Máx/Mín/Probabilidad.
    Si hay Total_Data, añade columnas reales (routed, set_capacity, PVA, riesgo).
    """
    total_por_sn = R.get("total_por_sn", {})
    filas = []
    for z in R["zonas"]:
        sn = z["_sn"]
        desp = int(R["cap_por_zona"].get(sn, 0))
        pdp_in = int(R.get("pdp_por_sn", {}).get(sn, 0))
        pdnp_in = int(R.get("pdnp_por_sn", {}).get(sn, 0))
        tot_in = pdp_in + pdnp_in
        pct_cumpl = round(pdp_in / tot_in * 100, 1) if tot_in else 0.0
        maxcap = int(z["MAXCAP"]) if z["MAXCAP"] else 0
        ocup = round((desp / maxcap) * 100, 1) if maxcap > 0 else 0.0
        tinfo = R["traslape_zona"].get(z["NOM"], {})
        pct_t = tinfo.get("pct", 0.0)
        if ocup < 70:
            prob = "Alta"
        elif ocup <= 90:
            prob = "Media"
        else:
            prob = "Baja"
        h = (hist_por_zona or {}).get(sn, {})
        td = total_por_sn.get(sn, {})
        fila = {
            "VR (Store Name)": z["NOM"],
            "Seteo (SW Max Cap)": maxcap,
            "SW Coverage (m)": int(z["RAD"]),
            "Volumen Despachado": desp,
            "PDP dentro (✔ perfil)": pdp_in,
            "PDNP dentro (✘ perfil)": pdnp_in,
            "% Cumplimiento": f"{pct_cumpl}%",
            "Ocupación": f"{ocup}%",
            "Histórico (prom. semana)": h.get("hist", ""),
            "Máximo semana": h.get("max", ""),
            "Mínimo semana": h.get("min", ""),
            "% Traslape": f"{pct_t}%",
            "Nivel Traslape": tinfo.get("nivel", "⚪ Sin datos"),
            "Prob. más volumen": prob,
        }
        if total_por_sn:
            fila["Ruteado (real)"] = int(td["routed"]) if td.get("routed") is not None else ""
            fila["Set Capacity (real)"] = int(td["set_capacity"]) if td.get("set_capacity") is not None else ""
            fila["PVA"] = td.get("pva", "")
            fila["Riesgo"] = td.get("risk", "")
        filas.append(fila)
    df = pd.DataFrame(filas)
    if not df.empty:
        df = df.sort_values("Volumen Despachado", ascending=False).reset_index(drop=True)
    return df


def calcular_historico_periodo(seteo, geo, tr2emp, elig, dias, nodo=None):
    """Histórico/Máx/Mín del volumen despachado por zona a lo largo de los días."""
    acum = {}
    for d in dias:
        dow = _fecha_a_dow(d)
        sd = seteo[seteo["_dow"] == dow]
        sns = set(sd["_sn"])
        gd = geo[geo["_fecha"] == d].copy()
        if gd.empty:
            continue
        gd["_emp"] = gd["tracking_id"].map(tr2emp)
        gd["_z"] = gd["_emp"].where(gd["_emp"].isin(sns))
        vc = gd["_z"].dropna().map(_norm_txt).value_counts().to_dict()
        for sn in sns:
            acum.setdefault(sn, []).append(int(vc.get(sn, 0)))
    hist = {}
    for sn, vals in acum.items():
        # Solo días CON volumen (>0) cuentan para histórico/máx/mín:
        #   • Histórico = suma ÷ nº de días con volumen (promedio de días activos)
        #   • Máximo    = el día que más entregó
        #   • Mínimo    = el menor volumen entregado (ignorando los días en 0)
        _con_vol = [v for v in vals if v > 0]
        if _con_vol:
            hist[sn] = {
                "hist": int(round(sum(_con_vol) / len(_con_vol))),
                "max": int(max(_con_vol)),
                "min": int(min(_con_vol)),
            }
        else:
            hist[sn] = {"hist": 0, "max": 0, "min": 0}
    return hist


# ═══════════════════════════════════════════════════════════════════════
# 🎯 MOTOR "CONFIRMATION PLAN"
# ═══════════════════════════════════════════════════════════════════════
TOPE_ZONA = 96
MIN_ZONA = 32
W_HIST = 0.5
W_DEMANDA = 0.5


def historico_neto_max_por_zona(raw_pts_full, seteo, dias):
    """
    Histórico NETO MÁXIMO por zona: para cada día, cuenta los paquetes del Raw
    que cada zona (employee) entregó DENTRO de su propio círculo (volumen neto),
    y toma el MÁXIMO de la semana (capacidad probada).

    raw_pts_full: DataFrame del Raw SIN filtrar por día (con _fecha, _emp, coords).
    Devuelve dict: sn_upper -> volumen neto máximo semanal.
    """
    out = {}
    if raw_pts_full is None or len(raw_pts_full) == 0:
        return out
    # Centro y radio de cada zona del seteo (por Store Name = employee)
    set_all = seteo[seteo["_sw_status"] == "ENABLED"] if "_sw_status" in seteo.columns else seteo
    # centro/radio por _sn (tomando el primero)
    z_cen = {}
    for _, r in set_all.drop_duplicates("_sn").iterrows():
        if pd.notna(r.get("LATITUD")) and pd.notna(r.get("LONGITUD")) and pd.notna(r.get("SW Coverage")):
            z_cen[r["_sn"]] = (float(r["LATITUD"]), float(r["LONGITUD"]), float(r["SW Coverage"]))
    if not z_cen:
        return out
    rp = raw_pts_full.copy()
    rp["_emp"] = rp["_emp"].map(_norm_txt)
    # por día
    acum = {}  # sn -> lista de netos por día
    for d in dias:
        sub = rp[rp["_fecha"] == d]
        if sub.empty:
            continue
        sub = sub.drop_duplicates(subset=["tracking_id"])
        for sn, g in sub.groupby("_emp"):
            c = z_cen.get(sn)
            if c is None:
                continue
            _cl = np.cos(np.radians(c[0]))
            _dla = (g["latitude"].to_numpy() - c[0]) * M_GRADO
            _dlo = (g["longitude"].to_numpy() - c[1]) * M_GRADO * _cl
            _neto = int(np.sum((_dla*_dla + _dlo*_dlo) <= (c[2] ** 2)))
            acum.setdefault(sn, []).append(_neto)
    for sn, vals in acum.items():
        if vals:
            out[sn] = int(np.max(vals))   # MÁXIMO de la semana
    return out


def pdp_en_zona_por_sn(R):
    """PDP que caen dentro del círculo de cada zona (ya calculado en R)."""
    return {sn: int(v) for sn, v in R.get("pdp_por_sn", {}).items()}


def demanda_libre_por_zona(R, gdf_cp_wgs84=None):
    """
    Demanda LIBRE por zona = suma de 'libre' (Aurora) de los CPs cuyo territorio
    cae dentro del círculo de la zona, ponderado por el % del CP que ocupa.
    Si no hay polígonos de CP, se calcula geométricamente por centroide (del
    GeoJSON) — y si tampoco hay, por el CP sede de la zona.
    """
    zonas = R["zonas"]
    elig_cp = R.get("elig_por_cp", {})
    demanda = {z["_sn"]: 0.0 for z in zonas}
    if not zonas or not elig_cp:
        return demanda
    # Con polígonos: % del CP dentro del círculo × libre del CP
    _det = R.get("_cps_detalle_por_sn", None)
    if _det:
        for z in zonas:
            tot = 0.0
            for (cp, pct, lib) in _det.get(z["_sn"], []):
                tot += float(lib) * (pct / 100.0)
            demanda[z["_sn"]] = tot
        return demanda
    # Sin detalle: repartir la elegibilidad del nodo equitativamente como aprox.
    _tot_elig = sum(float(v) for v in elig_cp.values())
    if _tot_elig > 0 and zonas:
        _por_zona = _tot_elig / len(zonas)
        for z in zonas:
            demanda[z["_sn"]] = _por_zona
    return demanda


# Pesos de la fórmula de seteo sugerido (confirmados):
#   30% histórico NETO máx · 45% PDP en zona · 25% libre en CP.
#   Si una zona NO tiene PDP, su peso (45%) pasa al histórico (preferencia hist).
W_HIST_PLAN = 0.30
W_PDP_PLAN = 0.45
W_LIBRE_PLAN = 0.25


def distribuir_confirmation_plan(R, plan_total, hist_zona, pdp_zona, demanda_zona,
                                 tope=TOPE_ZONA):
    """
    Reparte plan_total entre las zonas ENABLED del día con la fórmula:
      peso = 0.30*hist_neto_norm + 0.45*pdp_norm + 0.25*libre_norm
    Si una zona no tiene PDP, su 45% se suma al histórico (preferencia).
    Respeta mínimo (32) y tope (96) por zona; redistribuye excedentes.
    """
    zonas = R["zonas"]
    cols = ["VR (Store Name)", "Histórico neto (máx sem)", "PDP en zona",
            "Libre (CP)", "Seteo actual", "Seteo sugerido", "% del plan", "Δ", "Razón"]
    if not zonas or plan_total <= 0:
        return pd.DataFrame(columns=cols)

    sns = [z["_sn"] for z in zonas]
    hist = np.array([max(0.0, float(hist_zona.get(sn, 0.0))) for sn in sns])
    pdp = np.array([max(0.0, float(pdp_zona.get(sn, 0.0))) for sn in sns])
    libre = np.array([max(0.0, float(demanda_zona.get(sn, 0.0))) for sn in sns])

    def _norm(a):
        s = a.sum()
        return a / s if s > 0 else np.zeros_like(a)

    _h, _p, _l = _norm(hist), _norm(pdp), _norm(libre)
    # Peso por zona: si no tiene PDP, su 45% pasa al histórico
    peso = np.zeros(len(sns))
    for i in range(len(sns)):
        if pdp[i] > 0:
            peso[i] = W_HIST_PLAN*_h[i] + W_PDP_PLAN*_p[i] + W_LIBRE_PLAN*_l[i]
        else:
            # sin PDP → histórico toma el 30%+45% = 75%, libre 25%
            peso[i] = (W_HIST_PLAN + W_PDP_PLAN)*_h[i] + W_LIBRE_PLAN*_l[i]
    if peso.sum() <= 0:
        peso = np.ones(len(sns))

    # Reparto con PISO (32) y TECHO (96); excedente redistribuido.
    n = len(sns)
    asign = np.full(n, float(MIN_ZONA))
    restante = float(plan_total) - MIN_ZONA * n
    if restante > 0:
        libres = np.ones(n, dtype=bool)
        for _ in range(200):
            pp = peso * libres
            if pp.sum() <= 0 or restante <= 0.5:
                break
            add = restante * (pp / pp.sum())
            nuevo = asign + add
            exc = (nuevo > tope) & libres
            if exc.any():
                asign[exc] = tope; libres[exc] = False
                restante = float(plan_total) - asign.sum()
            else:
                asign = np.where(libres, nuevo, asign); restante = 0.0; break
    elif restante < 0:
        asign = np.clip(float(plan_total) * (peso / peso.sum()), 0, tope)

    sugerido = np.round(asign).astype(int)
    # Ajuste fino para cuadrar al plan
    diff = int(plan_total) - int(sugerido.sum())
    if diff != 0 and MIN_ZONA*n <= plan_total <= tope*n:
        orden = np.argsort(-peso) if diff > 0 else np.argsort(peso)
        i = 0
        while diff != 0 and i < len(orden)*6:
            k = orden[i % len(orden)]
            cand = sugerido[k] + (1 if diff > 0 else -1)
            if MIN_ZONA <= cand <= tope:
                sugerido[k] = cand; diff += -1 if diff > 0 else 1
            i += 1

    total_por_sn = R.get("total_por_sn", {})
    filas = []
    plan = max(1, int(plan_total))
    for j, z in enumerate(zonas):
        td = total_por_sn.get(z["_sn"], {})
        actual = int(td["set_capacity"]) if td.get("set_capacity") is not None else int(z["MAXCAP"])
        sug = int(sugerido[j]); delta = sug - actual
        razon = [f"histN≈{hist[j]:.0f}", f"pdp≈{pdp[j]:.0f}", f"libre≈{libre[j]:.0f}"]
        if pdp[j] == 0:
            razon.append("sin PDP→hist")
        if sug >= tope: razon.append("tope 96")
        if sug <= MIN_ZONA: razon.append("mín 32")
        filas.append({
            "VR (Store Name)": z["NOM"],
            "Histórico neto (máx sem)": int(round(hist[j])),
            "PDP en zona": int(round(pdp[j])),
            "Libre (CP)": int(round(libre[j])),
            "Seteo actual": actual,
            "Seteo sugerido": sug,
            "% del plan": f"{round(sug/plan*100,1)}%",
            "Δ": (f"+{delta}" if delta > 0 else str(delta)),
            "Razón": ", ".join(razon),
        })
    df = pd.DataFrame(filas).sort_values("Seteo sugerido", ascending=False).reset_index(drop=True)
    return df


# ═══════════════════════════════════════════════════════════════════════
# 🗺️ MAPA del día (Folium)
# ═══════════════════════════════════════════════════════════════════════
def cargar_poligonos_cp(cps_interes, mapas_dir="mapas"):
    """
    Carga los polígonos de CP desde los GeoJSON de mapas_dir, filtrando a los
    CPs de interés. Devuelve (gdf, diagnostico). El diagnóstico explica por qué
    quedó vacío (geopandas faltante, carpeta no existe, CPs no coinciden...).
    """
    diag = {"ok": False, "motivo": "", "archivos": 0, "cps_en_geojson": 0, "cps_match": 0}
    try:
        import geopandas as _gpd
    except Exception as e:
        diag["motivo"] = f"geopandas no está instalado en el servidor ({e})"
        return None, diag
    if not os.path.isdir(mapas_dir):
        diag["motivo"] = f"la carpeta '{mapas_dir}/' no existe en el repo"
        return None, diag
    cps_interes = set(str(_norm_cp(c)) for c in cps_interes)
    if not cps_interes:
        diag["motivo"] = "no hay CPs de elegibilidad para buscar"
        return None, diag
    _gdfs = []
    _archivos = 0
    _cps_todos = set()
    for _f in os.listdir(mapas_dir):
        if not _f.lower().endswith(".geojson"):
            continue
        _archivos += 1
        try:
            _g = _gpd.read_file(os.path.join(mapas_dir, _f))
        except Exception:
            continue
        _cpcol = next((c for c in ["d_codigo", "d_cp", "CP", "CODIGOPOSTAL",
                                    "cp", "codigo_postal", "CODIGO"] if c in _g.columns), None)
        if _cpcol is None:
            continue
        _g["CP"] = _g[_cpcol].astype(str).map(_norm_cp)
        _cps_todos |= set(_g["CP"].unique())
        _g = _g[_g["CP"].isin(cps_interes)]
        if not _g.empty:
            _gdfs.append(_g[["CP", "geometry"]])
    diag["archivos"] = _archivos
    diag["cps_en_geojson"] = len(_cps_todos)
    if _archivos == 0:
        diag["motivo"] = f"no hay archivos .geojson en '{mapas_dir}/'"
        return None, diag
    if not _gdfs:
        _muestra_interes = list(cps_interes)[:5]
        _muestra_geo = list(_cps_todos)[:5]
        diag["motivo"] = (f"ningún CP de elegibilidad coincide con los GeoJSON. "
                          f"Ej. elegibilidad: {_muestra_interes} · GeoJSON: {_muestra_geo}")
        return None, diag
    try:
        gdf = _gpd.GeoDataFrame(pd.concat(_gdfs, ignore_index=True), crs="EPSG:4326")
        diag["ok"] = True
        diag["cps_match"] = gdf["CP"].nunique()
        return gdf, diag
    except Exception as e:
        diag["motivo"] = f"error al unir polígonos: {e}"
        return None, diag


def construir_mapa_dia_html(R, gdf_cp_wgs84=None, mapas_dir='mapas'):
    if folium is None:
        return "<p>Folium no disponible.</p>"

    zonas = R["zonas"]
    pts = R["puntos"]
    if zonas:
        c_lat = float(np.mean([z["LAT"] for z in zonas]))
        c_lon = float(np.mean([z["LON"] for z in zonas]))
    elif len(pts):
        c_lat = float(pts["latitude"].mean()); c_lon = float(pts["longitude"].mean())
    else:
        c_lat, c_lon = 19.19, -96.15

    if (gdf_cp_wgs84 is None or getattr(gdf_cp_wgs84, "empty", True)):
        try:
            import geopandas as _gpd
            _cps_interes = set(str(_norm_cp(c)) for c in R.get("elig_por_cp", {}).keys())
            if os.path.isdir(mapas_dir) and _cps_interes:
                _gdfs = []
                for _f in os.listdir(mapas_dir):
                    if not _f.lower().endswith(".geojson"):
                        continue
                    try:
                        _g = _gpd.read_file(os.path.join(mapas_dir, _f))
                    except Exception:
                        continue
                    _cpcol = next((c for c in ["d_codigo", "d_cp", "CP", "CODIGOPOSTAL", "cp"]
                                   if c in _g.columns), None)
                    if _cpcol is None:
                        continue
                    _g["CP"] = _g[_cpcol].astype(str).map(_norm_cp)
                    _g = _g[_g["CP"].isin(_cps_interes)]
                    if not _g.empty:
                        _gdfs.append(_g[["CP", "geometry"]])
                if _gdfs:
                    gdf_cp_wgs84 = _gpd.GeoDataFrame(pd.concat(_gdfs, ignore_index=True), crs="EPSG:4326")
        except Exception:
            pass

    # Mapa SIN tiles por defecto; el basemap se agrega con control=False para
    # que NO aparezca como opción (URL) en el control de "Filtros".
    m = folium.Map(location=[c_lat, c_lon], zoom_start=11,
                   tiles=None, prefer_canvas=True, control_scale=True)
    folium.TileLayer(
        tiles="https://tile.openstreetmap.de/{z}/{x}/{y}.png",
        attr="&copy; OpenStreetMap", name="Mapa base",
        control=False,   # ← no aparece en el control de capas/filtros
    ).add_to(m)

    # ── ZOOM automático al NODO: ajustar la vista a las zonas del nodo ──
    #    Así, al elegir un nodo, el mapa se acerca a donde están sus zonas
    #    (en vez de mostrar todo el país).
    if zonas:
        _zlats = [z["LAT"] for z in zonas]
        _zlons = [z["LON"] for z in zonas]
        try:
            _sw = [min(_zlats), min(_zlons)]
            _ne = [max(_zlats), max(_zlons)]
            if _sw != _ne:
                m.fit_bounds([_sw, _ne], padding=(40, 40))
        except Exception:
            pass

    # ── POLÍGONOS DE CP (elegibilidad) ──
    fg_cp = folium.FeatureGroup(name="CP + Elegibilidad", show=True)
    if gdf_cp_wgs84 is not None and not gdf_cp_wgs84.empty:
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            _pts2 = pts.copy()
            gpts = gpd.GeoDataFrame(
                _pts2,
                geometry=[Point(xy) for xy in zip(_pts2["longitude"], _pts2["latitude"])],
                crs="EPSG:4326",
            )
            cob = gdf_cp_wgs84[["CP", "geometry"]].copy()
            cob["CP"] = cob["CP"].astype(str).map(_norm_cp)
            # ⚡ Simplificar geometrías (~25 m) para aligerar el GeoJSON del mapa
            #    sin perder la forma perceptible de los polígonos de CP.
            try:
                _TOL = 25 / 111139.0
                cob["geometry"] = cob["geometry"].simplify(_TOL, preserve_topology=True)
            except Exception:
                pass
            join = gpd.sjoin(gpts, cob, how="left", predicate="within").dropna(subset=["CP"])
            _pdp_cp = join[join["_cat"] == "PDP"].groupby("CP").size().to_dict()
            _pdnp_cp = join[join["_cat"] == "PDNP"].groupby("CP").size().to_dict()
            cob["_pdp"] = cob["CP"].map(lambda c: int(_pdp_cp.get(c, 0)))
            cob["_pdnp"] = cob["CP"].map(lambda c: int(_pdnp_cp.get(c, 0)))
            cob["_elegibles"] = cob["CP"].map(lambda c: int(R["elig_por_cp"].get(c, 0)))
            # HUB = paquetes entregados por zona (Raw) que caen en cada CP
            _hub_cp = {}
            try:
                _rawp = R.get("raw_pts", None)
                if _rawp is not None and len(_rawp):
                    _grw = gpd.GeoDataFrame(
                        _rawp.copy(),
                        geometry=[Point(xy) for xy in zip(_rawp["longitude"], _rawp["latitude"])],
                        crs="EPSG:4326")
                    _jrw = gpd.sjoin(_grw, cob[["CP", "geometry"]], how="left",
                                     predicate="within").dropna(subset=["CP"])
                    _hub_cp = _jrw.groupby("CP").size().to_dict()
            except Exception:
                _hub_cp = {}
            cob["_hub"] = cob["CP"].map(lambda c: int(_hub_cp.get(c, 0)))

            def _col_elig(v):
                v = float(v)
                if v <= 0:   return "#9e9e9e"
                if v <= 100: return "#f1c40f"
                if v <= 200: return "#e67e22"
                if v <= 300: return "#e74c3c"
                if v <= 400: return "#8e44ad"
                return "#6d4c41"
            cob["_color"] = cob["_elegibles"].map(_col_elig)
            gj = cob.to_json()
            folium.GeoJson(
                gj,
                style_function=lambda f: {
                    "fillColor": f["properties"].get("_color", "#9e9e9e"),
                    "color": "#ffffff", "weight": 1.2, "fillOpacity": 0.45},
                tooltip=folium.GeoJsonTooltip(
                    fields=["CP", "_pdp", "_pdnp", "_hub", "_elegibles"],
                    aliases=["Código Postal:", "PDP (✔ perfil):", "PDNP (✘ perfil):",
                             "Entregados hub (Raw):", "Elegibles (Aurora):"],
                    localize=True, sticky=True),
                popup=folium.GeoJsonPopup(
                    fields=["CP", "_pdp", "_pdnp", "_hub", "_elegibles"],
                    aliases=["Código Postal:", "PDP (✔ perfil):", "PDNP (✘ perfil):",
                             "Entregados hub (Raw):", "Elegibles (Aurora):"], localize=True),
            ).add_to(fg_cp)
        except Exception as e:
            folium.map.Marker([c_lat, c_lon],
                              tooltip=f"(CP overlay no disponible: {e})").add_to(fg_cp)
    fg_cp.add_to(m)

    # ── CÍRCULOS de zonas (con conteo PDP/PDNP en tooltip) ──
    fg_z = folium.FeatureGroup(name="Zonas", show=True)
    features = []

    # 📮 DESGLOSE POR CP de cada zona: qué CPs cubre, % del CP que ocupa el
    #    círculo y la elegibilidad libre (Aurora) de cada CP. Se calcula solo
    #    si hay polígonos de CP (gdf_cp_wgs84). Usa proyección métrica 6372
    #    para áreas reales (intersección círculo ∩ polígono de CP).
    _cps_detalle_por_sn = {}
    try:
        if gdf_cp_wgs84 is not None and not getattr(gdf_cp_wgs84, "empty", True):
            import geopandas as _gpd2
            import shapely as _shp2
            _cpm = gdf_cp_wgs84[["CP", "geometry"]].copy()
            _cpm["CP"] = _cpm["CP"].astype(str).map(_norm_cp)
            _cpm = _cpm.to_crs("EPSG:6372")             # métrico para áreas
            _cpm["geometry"] = _cpm.geometry.buffer(0)   # repara geometrías
            _cpm["_acp"] = _cpm.geometry.area
            for _z in zonas:
                _cen = _gpd2.GeoSeries(
                    [_shp2.geometry.Point(_z["LON"], _z["LAT"])],
                    crs="EPSG:4326").to_crs("EPSG:6372").iloc[0]
                _circ = _cen.buffer(_z["RAD"])
                _sub = _cpm[_cpm.geometry.intersects(_circ)]
                _det = []
                for _, _cr in _sub.iterrows():
                    if _cr["_acp"] <= 0:
                        continue
                    _ai = _cr.geometry.intersection(_circ).area
                    _pct = round(min(100.0, _ai / _cr["_acp"] * 100.0))
                    if _pct <= 0:
                        continue
                    _lib = int(R.get("elig_por_cp", {}).get(_cr["CP"], 0))
                    _det.append((_cr["CP"], _pct, _lib))
                _det.sort(key=lambda t: t[1], reverse=True)
                _cps_detalle_por_sn[_z["_sn"]] = _det
    except Exception:
        _cps_detalle_por_sn = {}
    # Guardar el detalle de CP en R para que el Plan de Confirmación calcule
    # la demanda LIBRE ponderada por % del CP dentro del círculo.
    R["_cps_detalle_por_sn"] = _cps_detalle_por_sn
    for i, z in enumerate(zonas):
        tinfo = R["traslape_zona"].get(z["NOM"], {})
        desp = int(R["cap_por_zona"].get(z["_sn"], 0))
        # Entregados por la zona según el RAW (= puntos amarillos al seleccionarla)
        raw_zona = int(R.get("raw_por_sn", {}).get(z["_sn"], 0))
        pdp_in = int(R.get("pdp_por_sn", {}).get(z["_sn"], 0))
        pdnp_in = int(R.get("pdnp_por_sn", {}).get(z["_sn"], 0))
        tot_in = pdp_in + pdnp_in
        pct_cumpl = round(pdp_in / tot_in * 100, 1) if tot_in else 0.0
        ocup = round((desp / z["MAXCAP"]) * 100, 1) if z["MAXCAP"] else 0.0
        td = R.get("total_por_sn", {}).get(z["_sn"], {})
        _extra = ""
        if td:
            _extra = ("Ruteado (real): {rt}<br>"
                      "Set Capacity (real): {sc}<br>"
                      "Riesgo: {rk}<br>").format(
                rt=int(td["routed"]) if td.get("routed") is not None else "—",
                sc=int(td["set_capacity"]) if td.get("set_capacity") is not None else "—",
                rk=td.get("risk", "—"))
        # ── Tooltip estilo CAPA DE COBERTURA ──
        # Estructura idéntica: Zona Operativa / Rango / Volumen / Radio / Traslape
        # / ── Detalle Traslape ── / separador / CPs Ocupados (CP (%)), más las
        # líneas propias del análisis real (PDP/PDNP/cumplimiento/capacidad/Total_Data).
        # Rango de volumen (mismos cortes que obtener_color_rango_circulo de cobertura).
        _vol_zona = desp  # volumen despachado/asignado de la zona
        if _vol_zona == 0:
            _rango_txt = "⚪ R0"
        elif _vol_zona <= 15:
            _rango_txt = "🟡 R1-15"
        elif _vol_zona <= 20:
            _rango_txt = "🟠 R16-20"
        elif _vol_zona <= 30:
            _rango_txt = "🔴 R21-30"
        elif _vol_zona <= 40:
            _rango_txt = "🟣 R31-40"
        else:
            _rango_txt = "🟤 R41+"
        # Detalle de traslape (zonas que se enciman), igual que cobertura.
        _desg = tinfo.get("desglose", [])
        _det_tras_lines = ""
        if _desg:
            _det_tras_lines = "── Detalle Traslape ──<br>" + "".join(
                [f"&nbsp;&nbsp;• {d['NOM']}: {d['PCT']}%<br>" for d in _desg])
        # CPs ocupados con su % (mismo formato "CP (NN%)" de la capa de cobertura).
        _det_cp = _cps_detalle_por_sn.get(z["_sn"], [])
        _elig_total = sum(l for _, _, l in _det_cp)
        if _det_cp:
            _txt_cps = ", ".join([f"{c} ({p}%)" for c, p, l in _det_cp])
        else:
            _txt_cps = "Ninguno"
        _tt_lines = [
            f"<b>Zona Operativa: {z['NOM']}</b>",
            f"Rango: {_rango_txt}",
            f"Paquetes entregados por la zona (Raw): {raw_zona}",
            f"Radio: {int(z['RAD'])}m",
            f"SW Max Cap: {int(z['MAXCAP'])}",
            f"Capacidad usada: {ocup}%",
            f"<b>🟢 PDP (✔ perfil): {pdp_in}</b>",
            f"<b>🔴 PDNP (✘ perfil): {pdnp_in}</b>",
            f"Total dentro: {tot_in} — Cumplimiento: {pct_cumpl}%",
        ]
        if _extra:
            # _extra viene con <br> al final de cada línea; lo integramos como líneas.
            for _ln in _extra.split("<br>"):
                _ln = _ln.strip()
                if _ln:
                    _tt_lines.append(_ln)
        _tt_lines.append(f"<b>Traslape: {tinfo.get('pct', 0.0)}% — {tinfo.get('nivel', '⚪ Sin datos')}</b>")
        if _det_tras_lines:
            _tt_lines.append(_det_tras_lines.rstrip("<br>"))
        _tt_lines.append("-------------------------")
        # Histórico semanal (máx/mín) de la zona, si está disponible
        _h = R.get("hist_por_zona", {}).get(z["_sn"], {})
        if _h:
            _tt_lines.append(f"Histórico semana — máx: {_h.get('max','?')} · mín: {_h.get('min','?')} · prom: {_h.get('hist','?')}")
        _tt_lines.append(f"<b>Elegibilidad libre (Aurora): {int(_elig_total)}</b>")
        _tt_lines.append(f"<b>CPs Ocupados:</b> {_txt_cps}")
        tt = "<br>".join(_tt_lines)
        ang = np.linspace(0, 2 * np.pi, 48)
        cos_lat = np.cos(np.radians(z["LAT"]))
        ring = [[z["LON"] + (z["RAD"] * np.cos(a)) / (M_GRADO * cos_lat),
                 z["LAT"] + (z["RAD"] * np.sin(a)) / M_GRADO] for a in ang]
        ring.append(ring[0])
        features.append({
            "type": "Feature",
            "properties": {
                "NOMBRE": z["NOM"], "_zid": i, "_tt": tt,
                "_clat": z["LAT"], "_clon": z["LON"], "_rad_m": z["RAD"],
                # Color por VOLUMEN de paquetes despachados a la zona (mismos
                # rangos que la capa de cobertura — obtener_color_rango_circulo):
                #   0 gris · ≤15 amarillo · ≤20 naranja · ≤30 rojo · ≤40 morado · >40 café
                "_color": ("#9e9e9e" if desp == 0 else
                           "#f1c40f" if desp <= 15 else
                           "#e67e22" if desp <= 20 else
                           "#e74c3c" if desp <= 30 else
                           "#8e44ad" if desp <= 40 else
                           "#6d4c41"),
            },
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })
    if features:
        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=lambda f: {"fillColor": f["properties"]["_color"],
                                      "color": "black", "weight": 1, "fillOpacity": 0.25},
            tooltip=folium.GeoJsonTooltip(fields=["_tt"], aliases=[""], localize=True, sticky=True),
            popup=folium.GeoJsonPopup(fields=["_tt"], aliases=[""], max_width=360),
        ).add_to(fg_z)
    fg_z.add_to(m)

    # ══════════════════════════════════════════════════════════════════
    # PUNTOS como FeatureGroups nativos de Folium → aparecen en el control
    # de "Filtros", se pueden prender/apagar, y cada punto tiene HOVER + CLIC.
    #   🟢 PDP (verde) · 🔴 PDNP (rojo) · ⚫ Raw (negro, entregados por zona)
    # Límite por capa para no congelar el navegador (muestreo si hay más).
    # ══════════════════════════════════════════════════════════════════
    from folium.vector_layers import CircleMarker as _CM
    # Sin límite: con el filtro día+nodo los puntos ya son pocos; se dibujan TODOS.

    _mask_pdp = (pts["_cat"] == "PDP") if "_cat" in pts.columns else pd.Series(True, index=pts.index)
    _pts_pdp = pts[_mask_pdp]
    _pts_pdnp = pts[~_mask_pdp]

    def _pinta_geo(df_cat, nombre_capa, fill, etiqueta, show=True):
        """PDP/PDNP: relleno por categoría; borde+núcleo negro si lo entregó una zona."""
        fg = folium.FeatureGroup(name=nombre_capa, show=show)
        if df_cat is not None and len(df_cat):
            _df = df_cat   # TODOS los puntos del día/nodo (sin muestreo)
            _la = _df["latitude"].to_numpy(); _lo = _df["longitude"].to_numpy()
            _tk = _df["tracking_id"].astype(str).to_numpy()
            _zn = (_df["_zona_final"].astype(str).to_numpy()
                   if "_zona_final" in _df.columns else np.array([""]*len(_df)))
            _ez = ((_df["_origen_asig"].to_numpy() == "tracking")
                   if "_origen_asig" in _df.columns else np.zeros(len(_df), dtype=bool))
            for k in range(len(_df)):
                _entregado = bool(_ez[k])
                _borde = "#000000" if _entregado else "#ffffff"
                _peso = 1.4 if _entregado else 0.6
                _rad = 4.2 if _entregado else 3.6
                _z = _zn[k]
                _info = etiqueta + ("<br><b>Entregado por zona:</b> " + _z
                         if (_entregado and _z and _z not in ("None","nan","")) else "<br><i>sin zona</i>")
                _info += "<br>Tracking: " + _tk[k]
                _CM(location=[float(_la[k]), float(_lo[k])], radius=_rad, weight=_peso,
                    color=_borde, fill=True, fill_color=fill, fill_opacity=0.9,
                    tooltip=_info, popup=_info).add_to(fg)
        fg.add_to(m)
        return fg

    # Orden: PDNP primero (abajo), PDP después (arriba)
    _pinta_geo(_pts_pdnp, "🔴 PDNP (no cumplen)", "#e74c3c", "🔴 PDNP (✘ perfil)")
    _pinta_geo(_pts_pdp, "🟢 PDP (cumplen perfil)", "#2ecc71", "🟢 PDP (✔ perfil)")

    # ── RAW (negros) = entregados por zona. FeatureGroup con hover+clic+resaltado.
    _raw_dia = R.get("raw_pts", None)
    fg_raw = folium.FeatureGroup(name="⚫ Entregados por zona (Raw)", show=True)
    if _raw_dia is not None and len(_raw_dia):
        _rd = _raw_dia   # TODOS los puntos Raw del día/nodo (sin muestreo)
        _rla = _rd["latitude"].to_numpy(); _rlo = _rd["longitude"].to_numpy()
        _rtk = _rd["tracking_id"].astype(str).to_numpy()
        _remp = (_rd["_emp"].astype(str).to_numpy()
                 if "_emp" in _rd.columns else np.array([""]*len(_rd)))
        for k in range(len(_rd)):
            _info = "<b>⚫ Repartidor:</b> " + _remp[k] + "<br>Tracking: " + _rtk[k]
            _cm = _CM(location=[float(_rla[k]), float(_rlo[k])], radius=2.4, weight=0.4,
                      color="#000000", fill=True, fill_color="#000000", fill_opacity=0.85,
                      tooltip=_info, popup=_info)
            # clase CSS con la zona para el resaltado JS (atributo via options)
            _cm.options["className"] = "rawpt"
            _cm.add_to(fg_raw)
    fg_raw.add_to(m)

    # JS de resaltado Raw por zona: guarda los markers con su employee leyendo
    # el tooltip (que contiene 'Repartidor: NOMBRE').
    _js_resalta = """
    <script>
    (function() {
      function _bmR() { for (var k in window){ try{ if(k.indexOf('map_')===0 && window[k] && window[k].eachLayer) return window[k]; }catch(e){} } return null; }
      function _initRaw(intentos) {
        intentos = intentos || 0; var mapa = _bmR();
        if (!mapa) { if (intentos<40) setTimeout(function(){_initRaw(intentos+1);}, 300); return; }
        window._rawMarkers = [];
        function _walk(ly){ if(ly.options && ly.options.className==='rawpt'){ 
            var tt = ly.getTooltip ? ly.getTooltip() : null;
            var c = tt ? String(tt.getContent()) : '';
            var m = c.match(/Repartidor:<\\/b>\\s*([^<]+)/);
            var zona = m ? m[1].toUpperCase().replace(/\\s+/g,' ').trim() : '';
            window._rawMarkers.push({marker:ly, zona:zona});
          }
          if(ly.eachLayer) ly.eachLayer(_walk);
          if(ly._layers){ for(var id in ly._layers) _walk(ly._layers[id]); }
        }
        _walk(mapa);
      }
      window.resaltarZonaRaw = function(nombreZona) {
        var z = (nombreZona||'').toUpperCase().replace(/\\s+/g,' ').trim();
        if(!window._rawMarkers) return;
        for(var i=0;i<window._rawMarkers.length;i++){ var it=window._rawMarkers[i];
          if(z && it.zona===z){ it.marker.setStyle({radius:5,color:'#ff9800',weight:1.5,fillColor:'#ffeb3b',fillOpacity:1.0}); try{it.marker.bringToFront();}catch(e){} }
          else { it.marker.setStyle({radius:2.4,color:'#000000',weight:0.4,fillColor:'#000000',fillOpacity:0.85}); }
        }
      };
      if(document.readyState==='complete'){ setTimeout(function(){_initRaw(0);}, 700); }
      else { window.addEventListener('load', function(){ setTimeout(function(){_initRaw(0);}, 700); }); }
    })();
    </script>
    """
    m.get_root().html.add_child(folium.Element(_js_resalta))

    # ── HEATMap ──
    if HeatMap is not None and len(pts) > 0:
        fg_heat = folium.FeatureGroup(name="🔥 Mapa de calor", show=False)
        HeatMap(pts[["latitude", "longitude"]].values.tolist(), radius=14, blur=18).add_to(fg_heat)
        fg_heat.add_to(m)

    folium.LayerControl(position="topright", collapsed=False).add_to(m)

    # ── FIX HOVER ZONAS ──
    #    Los puntos (PDP/PDNP) se dibujan ENCIMA de los círculos y capturaban el
    #    hover, impidiendo ver el tooltip de la zona. Este JS trae al FRENTE las
    #    capas de zona (las que tienen _rad_m) tras cargar el mapa, para que
    #    reciban el hover. Como el relleno de la zona es semitransparente
    #    (fillOpacity 0.25), los puntos se siguen viendo a través de ellas.
    _js_hover_fix = """
    <script>
    (function() {
      function _buscarMapa() {
        for (var k in window) {
          try { if (k.indexOf('map_')===0 && window[k] && window[k].eachLayer) return window[k]; } catch(e) {}
        }
        return null;
      }
      function _traerZonasAlFrente(intentos) {
        intentos = intentos || 0;
        var mapa = _buscarMapa();
        if (!mapa) { if (intentos < 40) setTimeout(function(){ _traerZonasAlFrente(intentos+1); }, 300); return; }
        function _walk(layer, cb) {
          cb(layer);
          if (layer.eachLayer) layer.eachLayer(function(s){ _walk(s, cb); });
          if (layer._layers) { for (var id in layer._layers) _walk(layer._layers[id], cb); }
        }
        _walk(mapa, function(ly) {
          try {
            if (ly.feature && ly.feature.properties &&
                typeof ly.feature.properties._rad_m !== 'undefined' &&
                ly.bringToFront) {
              ly.bringToFront();
            }
          } catch(e) {}
        });
      }
      if (document.readyState === "complete") { setTimeout(function(){ _traerZonasAlFrente(0); }, 900); }
      else { window.addEventListener("load", function(){ setTimeout(function(){ _traerZonasAlFrente(0); }, 900); }); }
    })();
    </script>
    """
    m.get_root().html.add_child(folium.Element(_js_hover_fix))

    m.get_root().html.add_child(folium.Element(_JS_BUSCADOR_APILADO))
    return m.get_root().render()


# JS: buscador (CP/zona), modo hover/click apilado, cuadro info izquierdo.
_JS_BUSCADOR_APILADO = r"""
    <style>
      /* ═══ CALIDAD AMAZON — tipografía, paleta y acabado profesional ═══ */
      :root {
        --amz-ink: #0F1111;          /* Squid Ink (texto principal Amazon) */
        --amz-ink-soft: #565959;     /* gris secundario Amazon */
        --amz-orange: #FF9900;       /* Amazon Orange */
        --amz-blue: #146EB4;         /* Amazon Blue */
        --amz-blue-dark: #0F5A94;
        --amz-line: #D5D9D9;         /* borde suave Amazon */
        --amz-bg: #FFFFFF;
        --amz-shadow: 0 2px 8px rgba(15,17,17,0.12), 0 1px 2px rgba(15,17,17,0.08);
        --amz-shadow-lg: 0 6px 20px rgba(15,17,17,0.16);
        --amz-font: "Amazon Ember", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      }
      /* Tooltips de Leaflet con acabado Amazon (tarjeta limpia, sombra suave) */
      .leaflet-tooltip {
        font-family: var(--amz-font) !important;
        font-size: 12.5px !important; line-height: 1.5 !important;
        color: var(--amz-ink) !important;
        background: var(--amz-bg) !important;
        border: 1px solid var(--amz-line) !important;
        border-radius: 10px !important;
        box-shadow: var(--amz-shadow) !important;
        padding: 9px 12px !important;
        max-width: 300px !important;
      }
      .leaflet-tooltip b { color: var(--amz-ink); font-weight: 700; }
      .leaflet-tooltip-top:before, .leaflet-tooltip-bottom:before,
      .leaflet-tooltip-left:before, .leaflet-tooltip-right:before { border: none !important; }
      /* Popups Leaflet con el mismo lenguaje visual */
      .leaflet-popup-content-wrapper {
        font-family: var(--amz-font) !important; border-radius: 12px !important;
        box-shadow: var(--amz-shadow-lg) !important; color: var(--amz-ink) !important;
      }
      .leaflet-popup-content { font-size: 12.5px !important; line-height: 1.5 !important; margin: 12px 14px !important; }
      /* Control de capas elegante */
      .leaflet-control-layers {
        font-family: var(--amz-font) !important; border-radius: 10px !important;
        border: 1px solid var(--amz-line) !important; box-shadow: var(--amz-shadow) !important;
        padding: 4px !important;
      }
      .leaflet-control-layers-list { font-size: 12.5px !important; color: var(--amz-ink) !important; padding: 4px 6px !important; }
      .leaflet-control-layers label { margin: 3px 0 !important; font-weight: 500; }
      /* Controles de zoom con estilo Amazon */
      .leaflet-bar a {
        border-radius: 8px !important; color: var(--amz-ink) !important;
        font-family: var(--amz-font) !important;
      }
      .leaflet-bar a:hover { background: #F7FAFA !important; color: var(--amz-blue) !important; }
      #cpSearchBar * { box-sizing:border-box; }
      .api-zona-item { padding:8px 11px; margin:4px 0; border:1px solid var(--amz-line); border-radius:8px;
          cursor:pointer; background:#F7FAFA; transition:all .15s; font-family:var(--amz-font); }
      .api-zona-item:hover { background:#EAF3FA; border-color:var(--amz-blue); }
      .api-volver { display:inline-block; margin-bottom:8px; padding:6px 12px; background:var(--amz-blue);
          color:white; border-radius:8px; cursor:pointer; font-weight:600; font-size:12px; font-family:var(--amz-font); }
      #infoPanel { position:fixed; left:12px; bottom:85px; z-index:9998; width:320px; max-height:46vh;
          overflow-y:auto; background:rgba(255,255,255,0.55); -webkit-backdrop-filter:blur(5px);
          backdrop-filter:blur(5px); border:1px solid rgba(203,213,225,0.6); border-radius:12px;
          box-shadow:0 6px 22px rgba(0,0,0,0.20); padding:12px 14px; font-family:sans-serif;
          font-size:12px; color:#1e293b; display:none; }
      #infoPanel .ip-head { display:flex; justify-content:space-between; align-items:center; margin:-2px 0 8px 0; }
      #infoPanel .ip-close { cursor:pointer; background:#e2e8f0; color:#475569; border:none;
          border-radius:6px; padding:2px 8px; font-weight:700; font-size:13px; }
      #infoPanel .ip-close:hover { background:#cbd5e1; }
    </style>
    <div id="cpSearchBar" style="position:fixed; top:10px; left:50%; transform:translateX(-50%);
        z-index:9999; background:var(--amz-bg); padding:9px 14px; border-radius:12px;
        box-shadow:var(--amz-shadow-lg); border:1px solid var(--amz-line);
        display:flex; align-items:center; gap:8px; font-family:var(--amz-font);">
        <span style="font-size:16px">🔍</span>
        <input id="cpInput" type="text" placeholder="Buscar CP o Zona..."
            onkeyup="if(event.key==='Enter')buscarCP(this.value)"
            style="border:1px solid var(--amz-line); border-radius:8px; padding:7px 12px;
            font-size:14px; width:180px; outline:none; font-family:var(--amz-font);
            color:var(--amz-ink);" />
        <button onclick="buscarCP(document.getElementById('cpInput').value)"
            style="background:var(--amz-blue); color:white; border:none; border-radius:8px;
            padding:7px 16px; font-size:13px; font-weight:600; cursor:pointer; transition:background .15s;">Buscar</button>
        <button id="cpClearBtn" onclick="limpiarBusqueda()" title="Quitar resaltado"
            style="background:#e2e8f0; color:#475569; border:none; border-radius:6px;
            padding:6px 11px; font-size:13px; font-weight:700; cursor:pointer;">✕</button>
        <span id="cpResult" style="font-size:12px; max-width:350px; white-space:nowrap;
            overflow:hidden; text-overflow:ellipsis;"></span>
    </div>
    <div id="infoPanel">
        <div class="ip-head">
            <div style="font-weight:700;color:var(--amz-ink);font-family:var(--amz-font);">ℹ️ Información</div>
            <button class="ip-close" onclick="cerrarInfoPanel()" title="Cerrar">✕</button>
        </div>
        <div id="infoPanelBody"></div>
    </div>
    <script>
    window.addEventListener('load', function() { setTimeout(function() { initCPSearch(); }, 1500); });
    function _findMap() {
        for (var k in window) { try { if (k.indexOf('map_') === 0 && window[k] && window[k].eachLayer) return window[k]; } catch(e) {} }
        for (var k2 in window) { try { if (window[k2] && window[k2]._leaflet_id && window[k2]._container) return window[k2]; } catch(e) {} }
        return null;
    }
    function _walk(layer, cb) {
        cb(layer);
        if (layer.eachLayer) layer.eachLayer(function(sub){ _walk(sub, cb); });
        if (layer._layers) { for (var id in layer._layers) _walk(layer._layers[id], cb); }
    }
    function _distMetros(lat1, lon1, lat2, lon2) {
        var R = 6371000; var dLat = (lat2 - lat1) * Math.PI / 180; var dLon = (lon2 - lon1) * Math.PI / 180;
        var a = Math.sin(dLat/2)*Math.sin(dLat/2) + Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*Math.sin(dLon/2)*Math.sin(dLon/2);
        return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    }
    window._ttMode='hover'; window._ttStore=[]; window._popStore=[]; window._zonasGeom=[];
    window._apiladas=[]; window._clickHandler=null; window._ultimaBusqueda=null;
    function initCPSearch() {
        var map = _findMap();
        if (!map) { var rd0=document.getElementById('cpResult'); if(rd0){rd0.innerHTML='⚠ Mapa no encontrado';rd0.style.color='#d97706';} return; }
        window._map = map;
        var cpIdx={}, zonaIdx={}, totalFeatures=0;
        function esVisible(layer){ var fo=(layer.options&&typeof layer.options.fillOpacity!=='undefined')?layer.options.fillOpacity:0.45; return fo>0; }
        function indexLayer(layer){
            if (layer.feature && layer.feature.properties) {
                var props=layer.feature.properties; var visible=esVisible(layer);
                if ('CP' in props) { var cpVal=String(props.CP).replace(/\.0$/,'').trim(); while(cpVal.length<5)cpVal='0'+cpVal;
                    if(!(cpVal in cpIdx)||visible){ if(!(cpVal in cpIdx))totalFeatures++; cpIdx[cpVal]=layer; } }
                var zonaNom=props.NOMBRE||props.name||props.Name||null;
                if (zonaNom){ var zk=String(zonaNom).trim().toUpperCase(); if(!(zk in zonaIdx)||visible)zonaIdx[zk]=layer; }
            }
            if (layer.eachLayer) layer.eachLayer(function(sub){ indexLayer(sub); });
            if (layer._layers){ for(var id in layer._layers) indexLayer(layer._layers[id]); }
        }
        map.eachLayer(function(layer){ indexLayer(layer); });
        window._cpIdx=cpIdx; window._zonaIdx=zonaIdx;
        var rd=document.getElementById('cpResult');
        if(rd&&totalFeatures>0){ rd.innerHTML=totalFeatures+' CPs y '+Object.keys(zonaIdx).length+' zonas indexados ✓'; rd.style.color='#16a34a'; setTimeout(function(){rd.innerHTML='';},3000); }
        else if(rd){ rd.innerHTML='⚠ 0 CPs encontrados'; rd.style.color='#d97706'; }
        try {
            var _lc=document.querySelector('.leaflet-control-layers-list');
            // Título "Filtros" al inicio del control de capas (encabezado Amazon)
            if(_lc && !document.getElementById('lcTitulo')){
                var _tit=document.createElement('div'); _tit.id='lcTitulo';
                _tit.style.cssText='font-family:var(--amz-font,sans-serif); font-weight:700; font-size:13px; color:var(--amz-ink,#0F1111); padding:2px 4px 6px 4px; border-bottom:1px solid var(--amz-line,#D5D9D9); margin-bottom:6px;';
                _tit.innerHTML='🎛️ Filtros';
                _lc.insertBefore(_tit, _lc.firstChild);
            }
            if(_lc && !document.getElementById('ttModeRow')){
                var _sep=document.createElement('div'); _sep.className='leaflet-control-layers-separator'; _lc.appendChild(_sep);
                var _row=document.createElement('div'); _row.id='ttModeRow'; _row.style.cssText='padding:4px 2px; font-family:sans-serif;';
                _row.innerHTML='<button id="ttModeBtn" onclick="toggleTooltipMode()" style="width:100%; background:#16a34a; color:white; border:none; border-radius:5px; padding:5px 8px; font-size:12px; font-weight:600; cursor:pointer; white-space:nowrap;">🖱️ Info: Hover</button><div id="ttModeHint" style="font-size:10px; color:#94a3b8; margin-top:3px; text-align:center;">Zonas encimadas → Click</div>';
                _lc.appendChild(_row);
            }
        } catch(e) {}
    }
    var _hl=null,_os=null;
    function _resaltarCapa(ly){
        var map=window._map||_findMap();
        _os={ fillColor:ly.options.fillColor||'#9e9e9e', fillOpacity:ly.options.fillOpacity||0.45, color:ly.options.color||'#ffffff', weight:ly.options.weight||1.5 };
        ly.setStyle({fillColor:'#146EB4',fillOpacity:0.7,color:'#146EB4',weight:3});
        try{ if(ly.bringToFront)ly.bringToFront(); }catch(e){}
        _hl=ly; if(ly.getBounds&&map)map.fitBounds(ly.getBounds(),{padding:[50,50],maxZoom:14}); if(ly.openTooltip)ly.openTooltip();
    }
    window.limpiarBusqueda=function(){
        try{ if(window.resaltarZonaRaw) window.resaltarZonaRaw(""); }catch(e){}
        try{ if(typeof _quitarResaltadoZona==='function') _quitarResaltadoZona(); }catch(e){}
        if(_hl&&_os){ try{ _hl.setStyle(_os); if(_hl.closeTooltip)_hl.closeTooltip(); }catch(e){} }
        _hl=null;_os=null; window._ultimaBusqueda=null;
        var inp=document.getElementById('cpInput'); if(inp)inp.value='';
        var rd=document.getElementById('cpResult'); if(rd){rd.innerHTML='';rd.style.color='#64748b';}
    };
    window.buscarCP=function(valor){
        var rd=document.getElementById('cpResult'); var raw=String(valor).trim();
        var cpIdx=window._cpIdx||{}; var zonaIdx=window._zonaIdx||{};
        if(!raw){ window.limpiarBusqueda(); if(rd){rd.innerHTML='Escribe un CP o nombre de zona';rd.style.color='#64748b';} return; }
        if(window._ultimaBusqueda&&window._ultimaBusqueda===raw.toUpperCase()&&_hl){ window.limpiarBusqueda(); return; }
        if(_hl&&_os){ try{_hl.setStyle(_os);}catch(e){} }
        // Limpiar TODO resaltado previo (círculo del apilado + puntos amarillos)
        try{ if(typeof _quitarResaltadoZona==='function') _quitarResaltadoZona(); }catch(e){}
        try{ if(window.resaltarZonaRaw) window.resaltarZonaRaw(""); }catch(e){}
        window._ultimaBusqueda=raw.toUpperCase();
        var esCP=/^[0-9]+(\.0)?$/.test(raw);
        if(esCP){
            var cp=raw.replace(/\.0$/,''); while(cp.length<5)cp='0'+cp; var ly=cpIdx[cp];
            if(ly){ _resaltarCapa(ly); if(rd){ var pr=ly.feature.properties;
                rd.innerHTML='✔ CP '+cp+' — PDP: '+(pr._pdp||0)+' | PDNP: '+(pr._pdnp||0)+' | Elegibles: '+(pr._elegibles||0); rd.style.color='#16a34a'; } }
            else if(rd){ rd.innerHTML='⚠ El CP '+cp+' no está dentro de la cobertura'; rd.style.color='#dc2626'; }
            return;
        }
        var clave=raw.toUpperCase(); var lyZona=zonaIdx[clave];
        if(!lyZona){ var cands=Object.keys(zonaIdx).filter(function(k){return k.indexOf(clave)!==-1;}); if(cands.length>0){lyZona=zonaIdx[cands[0]];clave=cands[0];} }
        if(lyZona){ _resaltarCapa(lyZona); try{ if(window.resaltarZonaRaw) window.resaltarZonaRaw(clave); }catch(e){} if(rd){rd.innerHTML='✔ Zona '+clave+' localizada';rd.style.color='#16a34a';} }
        else if(rd){ rd.innerHTML='✘ No se encontró el CP ni la zona "'+raw+'"'; rd.style.color='#dc2626'; }
    };
    (function(){ var pr=new URLSearchParams(window.location.search).get('cp'); if(pr)setTimeout(function(){window.buscarCP(pr);},2000); })();
    window.addEventListener('message',function(e){ if(e.data&&e.data.action==='searchCP'&&e.data.cp)window.buscarCP(e.data.cp); });
    function _recolectarZonas(map){
        window._zonasGeom=[]; window._zonaLayerByZid={}; var _vistos={};
        _walk(map,function(ly){ try{
            if(ly.feature&&ly.feature.properties&&typeof ly.feature.properties._clat!=='undefined'&&typeof ly.feature.properties._rad_m!=='undefined'){
                var p=ly.feature.properties;
                if(typeof p._zid!=='undefined')window._zonaLayerByZid[p._zid]=ly;
                var _key=String(p.NOMBRE||'')+'|'+p._clat+'|'+p._clon; if(_vistos[_key])return; _vistos[_key]=true;
                window._zonasGeom.push({ nom:p.NOMBRE||'', zid:(typeof p._zid!=='undefined')?p._zid:null,
                    lat:parseFloat(p._clat), lon:parseFloat(p._clon), rad:parseFloat(p._rad_m), tt:p._tt||'' });
            } }catch(e){} });
    }
    function _vistaListaZonas(){
        var arr=window._apiladas||[];
        var out=['<div style="font-weight:bold;color:#146EB4;margin-bottom:6px;">🔎 '+arr.length+' zonas aquí — elige una:</div>'];
        for(var i=0;i<arr.length;i++){ var nom=arr[i].nom||('Zona '+(i+1));
            out.push('<div class="api-zona-item" data-idx="'+i+'"><span style="color:#146EB4;font-weight:600;">▸</span> <b>'+nom+'</b></div>'); }
        return out.join('');
    }
    function _vistaDetalle(idx){ var arr=window._apiladas||[]; if(idx<0||idx>=arr.length)return '';
        return '<div class="api-volver" data-volver="1">← Volver a la lista</div><div>'+(arr[idx].tt||'')+'</div>'; }
    function _fmtCP(pr){
        if(!pr)return '';
        var cp=(pr.CP!==undefined&&pr.CP!==null)?String(pr.CP).replace(/\.0$/,''):'';
        var filas=[['Código Postal',cp],['PDP (✔ perfil)',pr._pdp!=null?pr._pdp:0],['PDNP (✘ perfil)',pr._pdnp!=null?pr._pdnp:0],['Entregados hub (Raw)',pr._hub!=null?pr._hub:0],['Elegibles (Aurora)',pr._elegibles!=null?pr._elegibles:0]];
        var out=['<div style="font-weight:bold;color:#0e7490;margin-bottom:4px;">📮 CP '+cp+'</div>'];
        for(var i=0;i<filas.length;i++){ out.push('<div><b>'+filas[i][0]+':</b> '+filas[i][1]+'</div>'); }
        return out.join('');
    }
    var _zonaHL=null,_zonaHL_os=null;
    function _quitarResaltadoZona(){ if(_zonaHL&&_zonaHL_os){try{_zonaHL.setStyle(_zonaHL_os);}catch(e){}} _zonaHL=null;_zonaHL_os=null; }
    function _ensureInfoPanel(){ var panel=document.getElementById('infoPanel'); if(panel)return panel;
        panel=document.createElement('div'); panel.id='infoPanel';
        panel.style.cssText='position:fixed;left:12px;bottom:85px;z-index:99999;width:320px;max-height:46vh;overflow-y:auto;background:rgba(255,255,255,0.55);-webkit-backdrop-filter:blur(5px);backdrop-filter:blur(5px);border:1px solid rgba(203,213,225,0.6);border-radius:12px;box-shadow:0 6px 22px rgba(0,0,0,0.20);padding:12px 14px;font-family:sans-serif;font-size:12px;color:#1e293b;display:none;';
        panel.innerHTML='<div class="ip-head" style="display:flex;justify-content:space-between;align-items:center;margin:-2px 0 8px 0;"><div style="font-weight:700;color:var(--amz-ink);font-family:var(--amz-font);">ℹ️ Información</div><button class="ip-close" onclick="cerrarInfoPanel()" title="Cerrar" style="cursor:pointer;background:#e2e8f0;color:#475569;border:none;border-radius:6px;padding:2px 8px;font-weight:700;font-size:13px;">✕</button></div><div id="infoPanelBody"></div>';
        document.body.appendChild(panel); return panel;
    }
    function _panelMostrar(html){ var panel=_ensureInfoPanel(); var body=document.getElementById('infoPanelBody'); if(!panel||!body)return; body.innerHTML=html; panel.style.display='block'; }
    window.cerrarInfoPanel=function(){ var panel=document.getElementById('infoPanel'); if(panel)panel.style.display='none'; try{_quitarResaltadoZona();}catch(e){} };
    function _resaltarZonaPorZid(zid){ var map=window._map||_findMap(); var ly=(window._zonaLayerByZid||{})[zid]; _quitarResaltadoZona(); if(!ly)return;
        _zonaHL_os={ fillColor:ly.options.fillColor||'#9e9e9e', fillOpacity:(typeof ly.options.fillOpacity!=='undefined')?ly.options.fillOpacity:0.45, color:ly.options.color||'black', weight:ly.options.weight||1 };
        try{ ly.setStyle({fillColor:'#146EB4',fillOpacity:0.7,color:'#146EB4',weight:3}); }catch(e){}
        try{ if(ly.bringToFront)ly.bringToFront(); }catch(e){} _zonaHL=ly;
        try{ var _nz=(ly.feature&&ly.feature.properties)?(ly.feature.properties.NOMBRE||''):''; if(window.resaltarZonaRaw) window.resaltarZonaRaw(_nz); }catch(e){}
        if(ly.getBounds&&map){ try{map.fitBounds(ly.getBounds(),{padding:[60,60],maxZoom:14});}catch(e){} }
    }
    function _mostrarApilado(latlng,cpInfo){
        var map=window._map||_findMap(); if(!map)return; var encontradas=[];
        for(var i=0;i<window._zonasGeom.length;i++){ var z=window._zonasGeom[i];
            if(isNaN(z.lat)||isNaN(z.lon)||isNaN(z.rad))continue;
            if(_distMetros(latlng.lat,latlng.lng,z.lat,z.lon)<=z.rad)encontradas.push({z:z}); }
        window._apiladas=encontradas.map(function(x){return x.z;});
        if(!cpInfo&&encontradas.length===0)return; window._cpInfoActual=cpInfo||'';
        var htmlZonas='';
        if(encontradas.length===1){ htmlZonas=encontradas[0].z.tt; if(encontradas[0].z.zid!=null)_resaltarZonaPorZid(encontradas[0].z.zid); }
        else if(encontradas.length>1){ htmlZonas=_vistaListaZonas(); _quitarResaltadoZona(); }
        else { _quitarResaltadoZona(); }
        var partes=[]; if(cpInfo)partes.push(cpInfo); if(htmlZonas)partes.push(htmlZonas);
        _panelMostrar('<div id="apiBox">'+partes.join('<hr style="border:none;border-top:2px dashed #94a3b8;margin:8px 0;">')+'</div>');
    }
    function _onMapClick(e){ _mostrarApilado(e.latlng,null); }
    function _onLayerClick(e){
        if(e&&e.originalEvent){ try{L.DomEvent.stopPropagation(e);}catch(ex){} }
        var cpInfo=null;
        try{ var ly=e.target||e.layer;
            if(ly&&ly.feature&&ly.feature.properties&&typeof ly.feature.properties.CP!=='undefined')cpInfo=_fmtCP(ly.feature.properties);
        }catch(ex){} _mostrarApilado(e.latlng,cpInfo);
    }
    document.addEventListener('click',function(ev){
        var t=ev.target;
        while(t&&t!==document){
            if(t.classList&&t.classList.contains('api-zona-item')){
                var idx=parseInt(t.getAttribute('data-idx'),10); var arr=window._apiladas||[];
                if(arr[idx]&&arr[idx].zid!=null)_resaltarZonaPorZid(arr[idx].zid);
                var det=_vistaDetalle(idx); var pp=[]; if(window._cpInfoActual)pp.push(window._cpInfoActual); pp.push(det);
                _panelMostrar('<div id="apiBox">'+pp.join('<hr style="border:none;border-top:2px dashed #94a3b8;margin:8px 0;">')+'</div>'); return;
            }
            if(t.getAttribute&&t.getAttribute('data-volver')==='1'){
                _quitarResaltadoZona(); var pp2=[]; if(window._cpInfoActual)pp2.push(window._cpInfoActual); pp2.push(_vistaListaZonas());
                _panelMostrar('<div id="apiBox">'+pp2.join('<hr style="border:none;border-top:2px dashed #94a3b8;margin:8px 0;">')+'</div>'); return;
            }
            t=t.parentNode;
        }
    });
    window.toggleTooltipMode=function(){
        var map=window._map||_findMap(); var btn=document.getElementById('ttModeBtn'); var hint=document.getElementById('ttModeHint'); if(!map)return;
        if(window._ttMode==='hover'){
            _recolectarZonas(map); window._ttStore=[]; window._popStore=[];
            _walk(map,function(ly){
                if(ly.getTooltip&&ly.getTooltip()){ var tt=ly.getTooltip(); window._ttStore.push({layer:ly,content:tt.getContent(),options:tt.options}); ly.unbindTooltip(); }
                if(ly.getPopup&&ly.getPopup()){ var pp=ly.getPopup(); window._popStore.push({layer:ly,content:pp.getContent(),options:pp.options}); ly.unbindPopup(); }
                if(ly.off){ try{ly.off('mouseover');ly.off('mousemove');}catch(e){} }
                if(ly.feature&&ly.feature.properties&&ly.on&&(typeof ly.feature.properties._rad_m!=='undefined'||typeof ly.feature.properties.CP!=='undefined')){ try{ly.on('click',_onLayerClick);}catch(e){} }
            });
            window._clickHandler=_onMapClick; map.on('click',window._clickHandler); window._ttMode='click';
            if(btn){btn.innerHTML='👆 Info: Click (apilado)';btn.style.background='#146EB4';} if(hint)hint.innerHTML='Clic → elige entre las encimadas';
        } else {
            if(window._clickHandler){ try{map.off('click',window._clickHandler);}catch(e){} window._clickHandler=null; }
            try{map.closePopup();}catch(e){} try{ if(typeof cerrarInfoPanel==='function')cerrarInfoPanel(); }catch(e){}
            _walk(map,function(ly){ if(ly.off){ try{ly.off('click',_onLayerClick);}catch(e){} } });
            window._ttStore.forEach(function(rec){ try{rec.layer.bindTooltip(rec.content,rec.options);}catch(e){} });
            window._popStore.forEach(function(rec){ try{rec.layer.bindPopup(rec.content,rec.options);}catch(e){} });
            window._ttStore=[]; window._popStore=[]; window._ttMode='hover';
            if(btn){btn.innerHTML='🖱️ Info: Hover';btn.style.background='#16a34a';} if(hint)hint.innerHTML='Zonas encimadas → Click';
        }
    };
    </script>
    """


# ═══════════════════════════════════════════════════════════════════════
# 🖥️ UI de Streamlit — bloque completo para incrustar en la app
# ═══════════════════════════════════════════════════════════════════════
def render_analisis_real_dia(gdf_cp_wgs84=None, nodo_default="DVR2"):
    st.markdown("## Análisis Real por Día")

    c1, c2 = st.columns(2)
    with c1:
        f_seteo = st.file_uploader("1) Seteo (searchHubsResponse — csv/xlsx/parquet/csv.gz)", type=["csv", "xlsx", "parquet", "pq", "gz"], key="rd_seteo")
        f_pdp = st.file_uploader("2a) PDP — CUMPLEN perfil (1 o 2 archivos: csv/xlsx/parquet/csv.gz)", type=["xlsx", "csv", "parquet", "pq", "gz"], key="rd_pdp", accept_multiple_files=True)
        f_pdnp = st.file_uploader("2b) PDNP — NO cumplen (1 o 2 archivos: csv/xlsx/parquet/csv.gz)", type=["xlsx", "csv", "parquet", "pq", "gz"], key="rd_pdnp", accept_multiple_files=True)
    with c2:
        f_raw = st.file_uploader("3) Raw Data — entregados por zona (1 o 2 archivos: csv/xlsx/parquet/csv.gz)", type=["xlsx", "csv", "parquet", "pq", "gz"], key="rd_raw", accept_multiple_files=True)
        f_elig = st.file_uploader("4) Elegibilidad / Data Aurora Dia (.xlsx)", type=["xlsx"], key="rd_elig")
        f_total = st.file_uploader("5) Total_Data (opcional — csv/xlsx/parquet/csv.gz)", type=["xlsx", "csv", "parquet", "pq", "gz"], key="rd_total")

    if not (f_seteo and f_pdp and len(f_pdp) > 0 and f_pdnp and len(f_pdnp) > 0 and f_raw and len(f_raw) > 0 and f_elig):
        st.info("Sube al menos los archivos 1–4 (Total_Data es opcional) para habilitar el análisis.")
        return

    # ── Tamaño total de los archivos subidos (feedback inmediato) ──
    def _mb(f):
        try:
            return f.size / (1024 * 1024)
        except Exception:
            try:
                return len(f.getvalue()) / (1024 * 1024)
            except Exception:
                return 0.0
    _tot_mb = sum(_mb(f) for f in [f_seteo, f_elig] if f)
    _tot_mb += sum(_mb(fp) for fp in f_pdp)
    _tot_mb += sum(_mb(fp) for fp in f_pdnp)
    _tot_mb += sum(_mb(fr) for fr in f_raw)
    if f_total:
        _tot_mb += _mb(f_total)
    st.caption(f"📁 Archivos subidos: **{_tot_mb:.0f} MB** en total. "
               f"{'⚠️ Son archivos grandes; la primera carga puede tardar 1-2 min.' if _tot_mb > 120 else ''}")

    # Leemos los bytes UNA sola vez (evita duplicar ~200MB en memoria).
    _b_seteo = f_seteo.getvalue()
    _pdp_items = [(fp.getvalue(), fp.name) for fp in f_pdp]    # lista de PDP
    _pdnp_items = [(fp.getvalue(), fp.name) for fp in f_pdnp]  # lista de PDNP
    _b_pdp_sizes = tuple(len(b) for b, _ in _pdp_items)
    _b_pdnp_sizes = tuple(len(b) for b, _ in _pdnp_items)
    _raw_items = [(fr.getvalue(), fr.name) for fr in f_raw]  # lista de los Raw
    _b_raw_sizes = tuple(len(b) for b, _ in _raw_items)
    _b_elig = f_elig.getvalue()
    total_bytes = f_total.getvalue() if f_total else None
    total_name = f_total.name if f_total else ""

    # Hoja de elegibilidad
    try:
        xls = pd.ExcelFile(io.BytesIO(_b_elig))
        hoja = next((h for h in xls.sheet_names if "aurora" in h.lower()), xls.sheet_names[0])
    except Exception:
        hoja = 0

    # ── Firma para cachear por set de archivos (por nombre + tamaño) ──
    _firma = (f_seteo.name, len(_b_seteo),
              tuple(sorted(fp.name for fp in f_pdp)), _b_pdp_sizes,
              tuple(sorted(fp.name for fp in f_pdnp)), _b_pdnp_sizes,
              tuple(sorted(fr.name for fr in f_raw)), _b_raw_sizes,
              f_elig.name, len(_b_elig), total_name,
              (len(total_bytes) if total_bytes else 0))
    _ya_cargado = st.session_state.get("rd_firma") == _firma

    if _ya_cargado and "rd_cache" in st.session_state:
        seteo, geo, tr2emp, tr2status, elig, total, raw_pts = st.session_state["rd_cache"]
        st.success("✅ Archivos ya cargados (usando caché) — listo para analizar.")
    else:
        # ── BARRA DE PROGRESO de carga de los 5 archivos (+ Total_Data opcional) ──
        #    Con checklist visible: cada archivo marca ✅ al terminar. Se añade una
        #    micro-pausa para que el avance sea perceptible aunque la lectura sea
        #    muy rápida (archivos .parquet). La barra y el checklist se limpian al
        #    terminar; si algún archivo falla, se muestra el error y se detiene.
        import time as _t
        _prog = st.progress(0, text="⏳ Iniciando lectura de archivos…")
        _check = st.empty()

        # Pasos: (etiqueta visible, % al TERMINAR). Total_Data solo si se subió.
        _pasos = [
            ("1) Seteo (searchHubsResponse)", 20),
            ("2a) PDP — cumplen perfil", 38),
            ("2b) PDNP — no cumplen", 54),
            ("3) Raw Data at Tracking", 72),
            ("4) Elegibilidad (Aurora)", 88),
        ]
        if total_bytes:
            _pasos.append(("5) Total_Data (opcional)", 98))

        def _pintar_checklist(idx_actual):
            # ✅ los ya hechos, 🔄 el actual, ⬜ los pendientes
            _lineas = []
            for _k, (_lbl, _) in enumerate(_pasos):
                if _k < idx_actual:
                    _icono = "✅"
                elif _k == idx_actual:
                    _icono = "🔄"
                else:
                    _icono = "⬜"
                _lineas.append(f"{_icono} {_lbl}")
            _check.markdown("  \n".join(_lineas))

        try:
            # — 1) SETEO —
            _pintar_checklist(0)
            _prog.progress(5, text=f"📥 Leyendo Seteo ({_mb(f_seteo):.0f} MB)…")
            seteo = _leer_seteo(_b_seteo, f_seteo.name)
            _prog.progress(_pasos[0][1], text="✅ Seteo listo")
            _t.sleep(0.25)

            # — 2a) PDP —
            _pintar_checklist(1)
            _prog.progress(_pasos[0][1], text=f"🟢 Leyendo PDP ({sum(_mb(fp) for fp in f_pdp):.0f} MB)…")
            pdp = _leer_geo(_pdp_items, "PDP")
            _prog.progress(_pasos[1][1], text="✅ PDP listo")
            _t.sleep(0.25)

            # — 2b) PDNP —
            _pintar_checklist(2)
            _prog.progress(_pasos[1][1], text=f"🔴 Leyendo PDNP ({sum(_mb(fp) for fp in f_pdnp):.0f} MB)…")
            pdnp = _leer_geo(_pdnp_items, "PDNP")
            geo = pd.concat([pdp, pdnp], ignore_index=True)
            _prog.progress(_pasos[2][1], text="✅ PDNP listo")
            _t.sleep(0.25)

            # — 3) RAW —
            _pintar_checklist(3)
            _prog.progress(_pasos[2][1], text=f"📦 Leyendo Raw Data ({_mb(f_raw):.0f} MB)…")
            tr2emp, tr2status, raw_pts = _leer_raw(_raw_items)
            _prog.progress(_pasos[3][1], text="✅ Raw Data listo")
            _t.sleep(0.25)

            # — 4) ELEGIBILIDAD —
            _pintar_checklist(4)
            _prog.progress(_pasos[3][1], text=f"📋 Leyendo Elegibilidad ({_mb(f_elig):.0f} MB)…")
            elig = _leer_elig(_b_elig, f_elig.name, hoja)
            _prog.progress(_pasos[4][1], text="✅ Elegibilidad lista")
            _t.sleep(0.25)

            # — 5) TOTAL_DATA (opcional) —
            if total_bytes:
                _pintar_checklist(5)
                _prog.progress(_pasos[4][1], text="🎯 Leyendo Total_Data…")
                total = _leer_total(total_bytes, total_name)
                _prog.progress(_pasos[5][1], text="✅ Total_Data listo")
                _t.sleep(0.25)
            else:
                total = None
        except Exception as _e_carga:
            _prog.empty()
            _check.empty()
            st.error(f"❌ Error al leer un archivo: {_e_carga}")
            st.stop()

        _prog.progress(100, text="✅ ¡Los 5 archivos se cargaron correctamente!")
        _t.sleep(0.5)
        _prog.empty()
        _check.empty()
        st.session_state["rd_firma"] = _firma
        st.session_state["rd_cache"] = (seteo, geo, tr2emp, tr2status, elig, total, raw_pts)

    dias = fechas_disponibles(seteo, geo, raw_pts=raw_pts)
    if not dias:
        # Diagnóstico claro de por qué no hay días
        _d_geo = sorted(set(geo["_fecha"].dropna())) if "_fecha" in geo.columns else []
        _d_raw = sorted(set(raw_pts["_fecha"].dropna())) if (raw_pts is not None and "_fecha" in getattr(raw_pts, "columns", [])) else []
        _dows = sorted(set(seteo["_dow"].dropna().unique())) if "_dow" in seteo.columns else []
        st.warning("⚠️ No se encontraron días para analizar.")
        st.caption(f"• Fechas en geopuntos (PDP/PDNP): {', '.join(_d_geo[:12]) or '(ninguna)'}  \n"
                   f"• Fechas en Raw: {', '.join(_d_raw[:12]) or '(ninguna)'}  \n"
                   f"• Días de semana en el seteo: {', '.join(_dows) or '(ninguno)'}  \n"
                   f"Revisa que los archivos tengan columna de fecha legible y que el seteo "
                   f"tenga filas Store Active + SW ENABLED.")
        return

    cc1, cc2 = st.columns([2, 1])
    with cc1:
        dia = st.selectbox("📅 Día a analizar:", dias, index=len(dias) - 1)
    with cc2:
        # Nodos: combinar los de geopuntos (PDP/PDNP) Y los del RAW (delivery_station),
        # porque el Raw es la fuente de los puntos "entregados por zona".
        _nodos_set = set()
        if "_nodo" in geo.columns:
            _nodos_set |= set(n for n in geo["_nodo"].dropna().unique() if n)
        try:
            if raw_pts is not None and len(raw_pts) and "_nodo" in raw_pts.columns:
                _nodos_set |= set(n for n in raw_pts["_nodo"].dropna().unique() if n)
        except Exception:
            pass
        nodos = sorted(_nodos_set) if _nodos_set else [nodo_default]
        nodo = st.selectbox("🏢 Nodo:", nodos, index=nodos.index(nodo_default) if nodo_default in nodos else 0)

    if st.button("🚀 Analizar día", type="primary", use_container_width=True):
        # ── BARRA DE AVANCE del procesamiento del mapa ──
        #    Muestra las etapas reales: zonas+puntos → histórico → mapa (lo que
        #    más tarda: traslape Monte Carlo + render de puntos/polígonos).
        import time as _tp
        _pbar = st.progress(0, text=f"⏳ Procesando {dia} ({_fecha_a_dow(dia)})…")
        try:
            _pbar.progress(15, text="🎯 Calculando zonas, asignación de puntos y traslape…")
            R = procesar_dia(seteo, geo, tr2emp, tr2status, elig, dia, nodo=nodo, total=total, raw_pts=raw_pts)

            _pbar.progress(55, text="📊 Calculando histórico del periodo…")
            hist = calcular_historico_periodo(seteo, geo, tr2emp, elig, dias, nodo=nodo)
            R["hist_por_zona"] = hist   # para mostrar máx/mín en el tooltip de zona

            _pbar.progress(75, text="🗺️ Construyendo el mapa (zonas, puntos, CP, heatmap)…")
            # Si no llegaron polígonos de CP, intentar cargarlos desde mapas/ con
            # diagnóstico (para que el aviso sea real y no engañoso).
            _gdf_cp_usar = gdf_cp_wgs84
            _poly_diag = None
            if _gdf_cp_usar is None or getattr(_gdf_cp_usar, "empty", True):
                _gdf_cp_usar, _poly_diag = cargar_poligonos_cp(
                    R.get("elig_por_cp", {}).keys(), mapas_dir="mapas")
            st.session_state["rd_poly_diag"] = _poly_diag
            _mapa_html = construir_mapa_dia_html(R, gdf_cp_wgs84=_gdf_cp_usar)

            _pbar.progress(95, text="💾 Guardando resultados…")
            st.session_state["rd_R"] = R
            st.session_state["rd_hist"] = hist
            st.session_state["rd_mapa"] = _mapa_html
            st.session_state["rd_total_df"] = total
            st.session_state["rd_dias"] = dias
            st.session_state["rd_seteo_df"] = seteo
            st.session_state["rd_geo"] = geo
            st.session_state["rd_tr2emp"] = tr2emp

            _pbar.progress(100, text="✅ ¡Análisis listo!")
            _tp.sleep(0.4)
        except Exception as _e_proc:
            _pbar.empty()
            st.error(f"❌ Error al procesar el día: {_e_proc}")
            st.stop()
        _pbar.empty()

    R = st.session_state.get("rd_R")
    if R is None:
        return

    # KPIs
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Zonas", len(R["zonas"]))
    k2.metric("Paquetes (puntos)", R["total_puntos"])
    k3.metric("🟢 PDP", R.get("total_pdp", 0))
    k4.metric("🔴 PDNP", R.get("total_pdnp", 0))
    k5.metric("Traslape total", f"{R['traslape_total']}%")

    # ── Diagnóstico del MATCH tracking → Raw → zona ──
    _tp = R.get("total_puntos", 0)
    _tcm = R.get("total_match_tracking", 0)
    _tce = R.get("total_con_employee", 0)
    _pct_match = round(_tcm / _tp * 100, 1) if _tp else 0.0
    st.caption(
        f"🔗 **Match tracking→Raw→zona:** {_tce:,} de {_tp:,} puntos cruzaron con un "
        f"repartidor (Employee) en Raw; **{_tcm:,} ({_pct_match}%) quedaron asignados "
        f"a una zona ENABLED del nodo**. El resto se asigna por geometría o queda sin asignar."
    )
    if _tp and _tce == 0:
        st.caption("ℹ️ Nota: ningún tracking cruzó con el Raw este día/nodo. No afecta "
                   "el dibujado — cada capa (PDP/PDNP/Raw) usa sus propias coordenadas. "
                   "El cruce con Raw solo marca el borde negro de 'entregado por zona'.")

    # Diagnóstico de polígonos de CP (elegibilidad): si no hay, avisar por qué.
    _n_cps_elig = len(R.get("elig_por_cp", {}))
    _diag = R.get("elig_diag", {})
    if _diag.get("nodo_mismatch"):
        st.info(f"ℹ️ La elegibilidad (Aurora) del {dia} tiene "
                f"{_diag.get('cps_solo_fecha',0)} CPs, pero ninguno con el nodo "
                f"'{_diag.get('nodo_pedido','')}'. Nodos que SÍ trae Aurora ese día: "
                f"{', '.join(_diag.get('nodos_aurora', [])) or '—'}. "
                f"Se usan TODOS los CPs de la fecha para no perder datos.")
    if _n_cps_elig == 0:
        st.warning(f"⚠️ No hay elegibilidad (Aurora) para el {dia}: 0 CPs. "
                   f"Revisa que el archivo Aurora tenga filas con esa fecha "
                   f"(el formato de 'event_day' debe coincidir).")
    else:
        _pd = st.session_state.get("rd_poly_diag")
        if _pd is not None and not _pd.get("ok"):
            st.warning(f"⚠️ Hay {_n_cps_elig} CPs con elegibilidad, pero los polígonos "
                       f"NO se pudieron cargar: {_pd.get('motivo','?')}. "
                       f"(Archivos .geojson detectados: {_pd.get('archivos',0)}, "
                       f"CPs en GeoJSON: {_pd.get('cps_en_geojson',0)})")
        elif _pd is not None and _pd.get("ok"):
            st.success(f"✅ Polígonos de CP cargados desde mapas/: "
                       f"{_pd.get('cps_match',0)} CPs coincidieron y se dibujaron.")

    mapa = st.session_state.get("rd_mapa")
    if mapa:
        components.html(mapa, height=620)

    df_z = construir_tabla_zonas(R, hist_por_zona=st.session_state.get("rd_hist"))
    st.markdown("### 📋 Análisis por Zona (VR)")
    st.dataframe(df_z, use_container_width=True, hide_index=True)

    st.markdown("### 📮 Elegibilidad por CP")
    df_cp = pd.DataFrame([{"CP": cp, "Volumen libre (Aurora)": int(v)} for cp, v in sorted(R["elig_por_cp"].items())])
    st.dataframe(df_cp, use_container_width=True, hide_index=True)

    # ── PLAN DE CONFIRMACIÓN ──
    st.markdown("### 🎯 Plan de Confirmación — sugerir seteo por zona")
    st.caption("Escribe el volumen TOTAL objetivo del día; se reparte entre las zonas "
               "según su histórico (routed real si subiste Total_Data, 50%) y la demanda "
               "libre de su polígono (50%), ajustado por traslape. Tope por zona: 96, mínimo 32.")

    pc1, pc2 = st.columns([1, 1])
    with pc1:
        _plan_total = st.number_input("📦 Confirmation Plan (volumen total objetivo):",
                                      min_value=0, step=10, value=0, key="rd_plan_val")
    with pc2:
        _btn_plan = st.button("🎯 Distribuir plan entre zonas", use_container_width=True, key="rd_plan_btn")

    _nz = len(R["zonas"])
    _pmin, _pmax = 32 * _nz, 96 * _nz
    st.caption(f"ℹ️ Con {_nz} zonas, el plan factible va de **{_pmin}** (todas en 32) "
               f"a **{_pmax}** (todas en 96).")

    if _btn_plan and _plan_total > 0:
        with st.spinner("Distribuyendo el plan…"):
            # Histórico NETO máximo semanal (volumen dentro de su propia zona)
            _hist_z = historico_neto_max_por_zona(
                raw_pts,  # Raw COMPLETO (todos los días)
                st.session_state.get("rd_seteo_df", seteo),
                st.session_state.get("rd_dias", dias))
            # PDP que caen en cada zona (del día analizado)
            _pdp_z = pdp_en_zona_por_sn(R)
            # Libre (Aurora) ponderado por % del CP dentro del círculo
            _dem_z = demanda_libre_por_zona(R, gdf_cp_wgs84=gdf_cp_wgs84)
            _df_plan = distribuir_confirmation_plan(R, int(_plan_total), _hist_z, _pdp_z, _dem_z)
            st.session_state["rd_plan_df"] = _df_plan
            st.session_state["rd_plan_total"] = int(_plan_total)

    _df_plan = st.session_state.get("rd_plan_df")
    if _df_plan is not None and not _df_plan.empty:
        _suma = int(pd.to_numeric(_df_plan["Seteo sugerido"], errors="coerce").fillna(0).sum())
        _plan_obj = st.session_state.get("rd_plan_total", 0)
        pcol1, pcol2, pcol3 = st.columns(3)
        pcol1.metric("Plan objetivo", _plan_obj)
        pcol2.metric("Σ Seteo sugerido", _suma)
        pcol3.metric("Zonas", len(_df_plan))
        if _suma != _plan_obj:
            st.warning(f"La suma ({_suma}) difiere del plan ({_plan_obj}); posible efecto del tope 96.")
        st.dataframe(_df_plan, use_container_width=True, hide_index=True)
        _bufp = io.BytesIO()
        with pd.ExcelWriter(_bufp, engine="xlsxwriter") as _w:
            _df_plan.to_excel(_w, index=False, sheet_name="Seteo Sugerido")
        st.download_button("📥 Descargar Seteo Sugerido (Excel)", data=_bufp.getvalue(),
                           file_name=f"Seteo_Sugerido_{R['nodo']}_{dia.replace('/','-')}_plan{_plan_obj}.xlsx",
                           mime="application/vnd.ms-excel", use_container_width=True)

    # ── PAQUETES POR LLEVAR ──
    st.markdown("### 📦 Paquetes por llevar por zona")
    st.caption("Puntos no asignados por tracking que caen dentro del radio de cada zona.")
    _df_llevar, _df_det = construir_entregas_no_asignadas(R)
    st.dataframe(_df_llevar, use_container_width=True, hide_index=True, height=320)
    _buf_na = io.BytesIO()
    with pd.ExcelWriter(_buf_na, engine="xlsxwriter") as _wna:
        _df_llevar.to_excel(_wna, index=False, sheet_name="Por llevar por zona")
        _df_det.to_excel(_wna, index=False, sheet_name="Detalle (puntos)")
    st.download_button("📥 Descargar Paquetes por Llevar (Excel)", data=_buf_na.getvalue(),
                       file_name=f"Paquetes_por_llevar_{R['nodo']}_{dia.replace('/','-')}.xlsx",
                       mime="application/vnd.ms-excel", use_container_width=True)

    # ── 🚚 ENTREGAS FUERA DE ZONA (por repartidor) ──
    st.markdown("### 🚚 Entregas fuera de zona (por repartidor)")
    st.caption("Con base en el Raw: paquetes que un repartidor entregó FUERA de su "
               "propio círculo. Volumen bruto = total entregado; neto = los de su "
               "zona; fuera = los que cayeron en otra zona (se identifica cuál).")
    _df_fz, _df_mz, _df_det = analizar_fuera_de_zona(R)
    if _df_fz.empty:
        st.info("No hay datos de Raw para calcular entregas fuera de zona "
                "(sube los archivos Raw con coordenadas).")
    else:
        _fz1, _fz2, _fz3 = st.columns(3)
        _tot_bruto = int(pd.to_numeric(_df_fz["Volumen bruto (total)"], errors="coerce").sum())
        _tot_fuera = int(pd.to_numeric(_df_fz["Fuera de zona"], errors="coerce").sum())
        _fz1.metric("Volumen bruto total", _tot_bruto)
        _fz2.metric("Entregados fuera de zona", _tot_fuera)
        _fz3.metric("% fuera", f"{round(_tot_fuera/_tot_bruto*100,1) if _tot_bruto else 0}%")

        st.markdown("**Resumen por repartidor**")
        st.dataframe(_df_fz, use_container_width=True, hide_index=True, height=320)
        st.markdown("**Matriz: zona origen → zona destino** (a dónde se fueron los paquetes)")
        st.dataframe(_df_mz, use_container_width=True, hide_index=True, height=260)

        _buf_fz = io.BytesIO()
        with pd.ExcelWriter(_buf_fz, engine="xlsxwriter") as _wfz:
            _df_fz.to_excel(_wfz, index=False, sheet_name="Fuera de zona (repartidor)")
            _df_mz.to_excel(_wfz, index=False, sheet_name="Matriz origen-destino")
            _df_det.to_excel(_wfz, index=False, sheet_name="Detalle fuera de zona")
        st.download_button("📥 Descargar Fuera de Zona (Excel)", data=_buf_fz.getvalue(),
                           file_name=f"Fuera_de_zona_{R['nodo']}_{dia.replace('/','-')}.xlsx",
                           mime="application/vnd.ms-excel", use_container_width=True)

    # ── Descargas ──
    d1, d2 = st.columns(2)
    with d1:
        if mapa:
            st.download_button("💾 Descargar Mapa HTML", data=mapa,
                               file_name=f"Mapa_Real_{R['nodo']}_{dia.replace('/','-')}.html",
                               mime="text/html", use_container_width=True)
    with d2:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
            df_z.to_excel(w, index=False, sheet_name="Analisis por Zona")
            df_cp.to_excel(w, index=False, sheet_name="Elegibilidad por CP")
            resumen = pd.DataFrame([{
                "Día": dia, "Día semana": R["dow"], "Nodo": R["nodo"],
                "Zonas": len(R["zonas"]), "Paquetes": R["total_puntos"],
                "PDP": R.get("total_pdp", 0), "PDNP": R.get("total_pdnp", 0),
                "Asignados": R["total_asignados"], "No asignados": R["total_no_asignados"],
                "Traslape total": f"{R['traslape_total']}%",
            }])
            resumen.to_excel(w, index=False, sheet_name="Resumen")
            # Pestañas de ENTREGAS FUERA DE ZONA (resumen por repartidor + matriz)
            try:
                _df_fz, _df_mz, _df_det = analizar_fuera_de_zona(R)
                _df_fz.to_excel(w, index=False, sheet_name="Fuera de zona (repartidor)")
                _df_mz.to_excel(w, index=False, sheet_name="Matriz origen-destino")
                _df_det.to_excel(w, index=False, sheet_name="Detalle fuera de zona")
            except Exception:
                pass
        st.download_button("📊 Descargar Análisis Excel", data=buf.getvalue(),
                           file_name=f"Analisis_Real_{R['nodo']}_{dia.replace('/','-')}.xlsx",
                           mime="application/vnd.ms-excel", use_container_width=True)
