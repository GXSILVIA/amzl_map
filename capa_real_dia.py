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


def _norm_cp(v):
    try:
        return str(int(float(v))).strip().zfill(5)
    except Exception:
        return str(v).strip().replace(".0", "").zfill(5)


def _norm_fecha(serie):
    """Devuelve la fecha como texto dd/mm/YYYY, aceptando datetime o texto."""
    s = pd.to_datetime(serie, errors="coerce", dayfirst=True)
    return s.dt.strftime("%d/%m/%Y")


def _fecha_a_dow(fecha_txt):
    """'dd/mm/YYYY' → 'MONDAY'/'TUESDAY'/... (en inglés, como el seteo)."""
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
    Lee CSV o XLSX desde bytes según extensión/nombre.
    OPTIMIZADO para velocidad en archivos grandes:
      • CSV  → intenta engine 'pyarrow' (multinúcleo, muy rápido); si no está,
               cae al engine C de pandas. usecols reduce memoria/tiempo.
      • XLSX → openpyxl en modo read_only vía pandas (más ligero en RAM).
    usecols: lista opcional de columnas a leer (acelera mucho si el archivo
             trae muchas columnas que no se usan).
    """
    nombre = (nombre or "").lower()
    es_excel = nombre.endswith((".xlsx", ".xls", ".xlsm"))
    if es_excel:
        try:
            return pd.read_excel(io.BytesIO(data_bytes),
                                 sheet_name=sheet if sheet is not None else 0,
                                 usecols=usecols, engine="openpyxl")
        except Exception:
            return pd.read_excel(io.BytesIO(data_bytes),
                                 sheet_name=sheet if sheet is not None else 0)
    # CSV: pyarrow primero (muy rápido), luego C-engine como respaldo
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
# 📥 CARGA + NORMALIZACIÓN de los archivos
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
    _cols_seteo = ["Store Name", "Status", "ZipCode", "Geocodes",
                   "Day of Week", "Supply Window", "SW Max Cap",
                   "SW Coverage", "SW Status"]
    try:
        seteo = _read_tabla(seteo_bytes, seteo_name, usecols=_cols_seteo)
    except Exception:
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
    _cf = next((c for c in ["event_day", "fecha"] if c in elig.columns), None)
    _cz = next((c for c in ["zone", "nodo", "station_code"] if c in elig.columns), None)
    _ccp = next((c for c in ["destination_postal_code", "postal_code", "ZipCode", "cp"] if c in elig.columns), None)
    _cl = next((c for c in ["libre", "free", "disponible"] if c in elig.columns), None)
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


def fechas_disponibles(seteo, geo):
    """
    Días con geopuntos cuyo DÍA DE LA SEMANA existe en el seteo (ENABLED+Activo).
    El seteo no tiene fecha calendario, así que la disponibilidad la marcan
    los geopuntos; cada fecha se valida contra Day of Week del seteo.
    """
    dows_set = set(seteo["_dow"].dropna().unique())
    f_geo = sorted(set(geo["_fecha"].dropna()),
                   key=lambda d: (d[6:], d[3:5], d[:2]))
    out = [d for d in f_geo if _fecha_a_dow(d) in dows_set]
    return out


# ═══════════════════════════════════════════════════════════════════════
# 🎲 TRASLAPE del día (Monte Carlo vectorizado, por zona y total)
# ═══════════════════════════════════════════════════════════════════════
def calcular_traslape_dia(zonas, n=8000, seed=42):
    np.random.seed(seed)
    res = {}
    if not zonas:
        return res, 0.0
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
def procesar_dia(seteo, geo, tr2emp, tr2status, elig, dia, nodo=None, total=None):
    """Devuelve un dict 'R' con todo lo necesario para pintar el mapa y las tablas."""
    dow = _fecha_a_dow(dia)

    # 1) ZONAS del día (Store Active + SW ENABLED + Day of Week == dow)
    #    Sumar SW Max Cap de las ventanas ENABLED de ese día por tienda.
    sd = seteo[seteo["_dow"] == dow].copy()
    if nodo and "_nodo" in geo.columns:
        pass  # el seteo no trae station code fiable; el filtro de nodo se aplica a puntos/elig

    # agrupar por tienda sumando capacidad de las SW ENABLED
    agg = (sd.groupby("_sn")
             .agg(NOM=("Store Name", "first"),
                  LAT=("LATITUD", "first"),
                  LON=("LONGITUD", "first"),
                  RAD=("SW Coverage", "max"),
                  MAXCAP=("SW Max Cap", "sum"),
                  CP=("_cp", "first"))
             .reset_index())
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
    ed = elig[elig["_fecha"] == dia].copy()
    if nodo and "_zona" in ed.columns:
        ed = ed[ed["_zona"] == _norm_txt(nodo)]
    elig_por_cp = ed.groupby("_cp")["libre"].sum().to_dict() if len(ed) else {}

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

    R = {
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
    }
    return R


# ═══════════════════════════════════════════════════════════════════════
# 📊 TABLAS de análisis descargable
# ═══════════════════════════════════════════════════════════════════════
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
            "Histórico": h.get("hist", ""),
            "Máximo": h.get("max", ""),
            "Mínimo": h.get("min", ""),
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
        if vals:
            hist[sn] = {"hist": int(round(np.mean(vals))), "max": int(np.max(vals)), "min": int(np.min(vals))}
    return hist


# ═══════════════════════════════════════════════════════════════════════
# 🎯 MOTOR "CONFIRMATION PLAN"
# ═══════════════════════════════════════════════════════════════════════
TOPE_ZONA = 96
MIN_ZONA = 32
W_HIST = 0.5
W_DEMANDA = 0.5


def historico_total_por_zona(seteo, geo, tr2emp, dias, total=None):
    """
    Peso histórico por zona. Si hay Total_Data, usa 'routed' real (promedio por
    zona); si no, promedio de despacho reconstruido de geopuntos.
    """
    if total is not None and "routed" in total.columns:
        g = total.groupby("_sn")["routed"].mean()
        d = {sn: float(v) for sn, v in g.items() if pd.notna(v)}
        if d:
            return d
    acum = {}
    for d in dias:
        dow = _fecha_a_dow(d)
        sd = seteo[seteo["_dow"] == dow]
        sns = set(sd["_sn"])
        gd = geo[geo["_fecha"] == d]
        if gd.empty:
            continue
        emp = gd["tracking_id"].map(tr2emp)
        z = emp.where(emp.isin(sns))
        vc = z.dropna().map(_norm_txt).value_counts().to_dict()
        for sn in sns:
            acum.setdefault(sn, []).append(int(vc.get(sn, 0)))
    return {sn: float(np.mean(v)) for sn, v in acum.items() if v}


def demanda_libre_por_zona(R, gdf_cp_wgs84=None):
    """Demanda libre (elegibles 'libre') asociada a cada zona ENABLED del día."""
    zonas = R["zonas"]
    elig_cp = R["elig_por_cp"]
    demanda = {z["_sn"]: 0.0 for z in zonas}
    if not zonas:
        return demanda
    if gdf_cp_wgs84 is None or getattr(gdf_cp_wgs84, "empty", True):
        for z in zonas:
            demanda[z["_sn"]] = float(elig_cp.get(_norm_cp(z.get("CP", "")), 0.0))
        return demanda
    try:
        import geopandas as gpd
        cob = gdf_cp_wgs84[["CP", "geometry"]].copy()
        cob["CP"] = cob["CP"].astype(str).map(_norm_cp)
        cent = cob.geometry.centroid
        cx = cent.x.to_numpy(); cy = cent.y.to_numpy()
        cps = cob["CP"].to_numpy()
        sz_lat = np.array([z["LAT"] for z in zonas])
        sz_lon = np.array([z["LON"] for z in zonas])
        sz_rad = np.array([z["RAD"] for z in zonas])
        sz_sn = np.array([z["_sn"] for z in zonas])
        cos_lat = np.cos(np.radians(float(sz_lat.mean())))
        dlat = (cy[:, None] - sz_lat[None, :]) * M_GRADO
        dlon = (cx[:, None] - sz_lon[None, :]) * M_GRADO * cos_lat
        d2 = dlat ** 2 + dlon ** 2
        dentro = d2 <= (sz_rad[None, :] ** 2)
        d2m = np.where(dentro, d2, np.inf)
        idx = np.argmin(d2m, axis=1)
        tiene = np.isfinite(d2m[np.arange(len(cps)), idx])
        for k in range(len(cps)):
            if tiene[k]:
                sn = sz_sn[idx[k]]
                demanda[sn] = demanda.get(sn, 0.0) + float(elig_cp.get(cps[k], 0.0))
    except Exception:
        for z in zonas:
            demanda[z["_sn"]] = float(elig_cp.get(_norm_cp(z.get("CP", "")), 0.0))
    return demanda


def distribuir_confirmation_plan(R, plan_total, hist_zona, demanda_zona,
                                 w_hist=W_HIST, w_dem=W_DEMANDA, tope=TOPE_ZONA):
    """Reparte plan_total entre las zonas ENABLED del día (histórico + demanda, ajustado por traslape)."""
    zonas = R["zonas"]
    if not zonas or plan_total <= 0:
        return pd.DataFrame(columns=["VR (Store Name)", "Seteo actual", "Seteo sugerido",
                                     "% del plan", "Δ", "Razón"])
    total_por_sn = R.get("total_por_sn", {})
    sns = [z["_sn"] for z in zonas]
    hist = np.array([max(0.0, hist_zona.get(sn, 0.0)) for sn in sns])
    dem = np.array([max(0.0, demanda_zona.get(sn, 0.0)) for sn in sns])
    tras = np.array([R["traslape_zona"].get(z["NOM"], {}).get("pct", 0.0) for z in zonas]) / 100.0

    def _norm(a):
        s = a.sum()
        return a / s if s > 0 else np.ones_like(a) / len(a)

    peso = (w_hist * _norm(hist) + w_dem * _norm(dem)) * (1.0 - tras * 0.5)
    peso = np.where(peso < 0, 0, peso)
    if peso.sum() <= 0:
        peso = np.ones(len(sns))

    n = len(sns)
    piso_total = MIN_ZONA * n
    techo_total = tope * n
    asign = np.full(n, float(MIN_ZONA))
    restante = float(plan_total) - piso_total

    if restante > 0:
        libres = np.ones(n, dtype=bool)
        for _ in range(200):
            p = peso * libres
            if p.sum() <= 0 or restante <= 0.5:
                break
            add = restante * (p / p.sum())
            nuevo_val = asign + add
            exceso = (nuevo_val > tope) & libres
            if exceso.any():
                asign[exceso] = tope
                libres[exceso] = False
                restante = float(plan_total) - asign.sum()
            else:
                asign = np.where(libres, nuevo_val, asign)
                restante = 0.0
                break
    elif restante < 0:
        asign = np.clip(float(plan_total) * (peso / peso.sum()), 0, tope)

    sugerido = np.round(asign).astype(int)
    diff = int(plan_total) - int(sugerido.sum())
    if diff != 0 and piso_total <= plan_total <= techo_total:
        orden = np.argsort(-peso) if diff > 0 else np.argsort(peso)
        i = 0
        while diff != 0 and i < len(orden) * 6:
            k = orden[i % len(orden)]
            cand = sugerido[k] + (1 if diff > 0 else -1)
            if MIN_ZONA <= cand <= tope:
                sugerido[k] = cand
                diff += -1 if diff > 0 else 1
            i += 1

    filas = []
    plan = max(1, int(plan_total))
    for j, z in enumerate(zonas):
        td = total_por_sn.get(z["_sn"], {})
        # Seteo actual: preferir set_capacity real si existe, si no SW Max Cap
        actual = int(td["set_capacity"]) if td.get("set_capacity") is not None else int(z["MAXCAP"])
        sug = int(sugerido[j])
        delta = sug - actual
        razon = [f"hist≈{hist[j]:.0f}", f"libre≈{dem[j]:.0f}"]
        if td.get("risk"):
            razon.append(td["risk"].split("-")[0].strip().lower())
        if tras[j] >= 0.5:
            razon.append(f"⚠️traslape {tras[j]*100:.0f}%")
        if sug >= tope:
            razon.append("tope 96")
        if sug <= MIN_ZONA:
            razon.append("mín 32")
        filas.append({
            "VR (Store Name)": z["NOM"],
            "Histórico (routed/despacho)": int(round(hist[j])),
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

    m = folium.Map(location=[c_lat, c_lon], zoom_start=11,
                   tiles="https://tile.openstreetmap.de/{z}/{x}/{y}.png",
                   attr="&copy; OpenStreetMap", prefer_canvas=True)

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
            join = gpd.sjoin(gpts, cob, how="left", predicate="within").dropna(subset=["CP"])
            _pdp_cp = join[join["_cat"] == "PDP"].groupby("CP").size().to_dict()
            _pdnp_cp = join[join["_cat"] == "PDNP"].groupby("CP").size().to_dict()
            cob["_pdp"] = cob["CP"].map(lambda c: int(_pdp_cp.get(c, 0)))
            cob["_pdnp"] = cob["CP"].map(lambda c: int(_pdnp_cp.get(c, 0)))
            cob["_elegibles"] = cob["CP"].map(lambda c: int(R["elig_por_cp"].get(c, 0)))

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
                    fields=["CP", "_pdp", "_pdnp", "_elegibles"],
                    aliases=["Código Postal:", "PDP (✔ perfil):", "PDNP (✘ perfil):",
                             "Elegibles (Aurora):"],
                    localize=True),
                popup=folium.GeoJsonPopup(
                    fields=["CP", "_pdp", "_pdnp", "_elegibles"],
                    aliases=["Código Postal:", "PDP (✔ perfil):", "PDNP (✘ perfil):",
                             "Elegibles (Aurora):"], localize=True),
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
    for i, z in enumerate(zonas):
        tinfo = R["traslape_zona"].get(z["NOM"], {})
        desp = int(R["cap_por_zona"].get(z["_sn"], 0))
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
            f"Volumen Promedio: {desp}",
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
                "_color": "#e74c3c" if tinfo.get("pct", 0) >= 75 else
                          ("#e67e22" if tinfo.get("pct", 0) >= 50 else "#2ecc71"),
            },
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })
    if features:
        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=lambda f: {"fillColor": f["properties"]["_color"],
                                      "color": "black", "weight": 1, "fillOpacity": 0.25},
            tooltip=folium.GeoJsonTooltip(fields=["_tt"], aliases=[""], localize=True),
            popup=folium.GeoJsonPopup(fields=["_tt"], aliases=[""], max_width=360),
        ).add_to(fg_z)
    fg_z.add_to(m)

    # ── PUNTOS: 🟢 PDP  /  🔴 PDNP  /  ⚪ no asignado ──
    #    El color = categoría (PDP/PDNP). Los no asignados (sin zona) van en gris
    #    aunque sean PDP/PDNP, para destacarlos como huérfanos.
    fg_pdp = folium.FeatureGroup(name="🟢 PDP (cumplen perfil)", show=True)
    fg_pdnp = folium.FeatureGroup(name="🔴 PDNP (no cumplen)", show=True)
    fg_no = folium.FeatureGroup(name="⚪ No asignados", show=True)
    for _, p in pts.iterrows():
        es_pdp = (p.get("_cat") == "PDP")
        asignado = bool(p.get("_asignado"))
        if not asignado:
            folium.CircleMarker(
                [p["latitude"], p["longitude"]], radius=2.5,
                color="#616161", weight=0.5, fill=True, fill_color="#bdbdbd", fill_opacity=0.9,
                tooltip=f"⚪ No asignado<br>{p['tracking_id']}<br>{p.get('_cat','')}",
            ).add_to(fg_no)
        elif es_pdp:
            folium.CircleMarker(
                [p["latitude"], p["longitude"]], radius=2.5,
                color="#1b5e20", weight=0.5, fill=True, fill_color="#2ecc71", fill_opacity=0.95,
                tooltip=f"🟢 PDP (✔ perfil)<br>{p['tracking_id']}<br>Zona: {p['_zona_final']}",
            ).add_to(fg_pdp)
        else:
            folium.CircleMarker(
                [p["latitude"], p["longitude"]], radius=2.5,
                color="#b71c1c", weight=0.5, fill=True, fill_color="#e74c3c", fill_opacity=0.95,
                tooltip=f"🔴 PDNP (✘ perfil)<br>{p['tracking_id']}<br>Zona: {p['_zona_final']}",
            ).add_to(fg_pdnp)
    fg_pdp.add_to(m)
    fg_pdnp.add_to(m)
    fg_no.add_to(m)

    # ── HEATMap ──
    if HeatMap is not None and len(pts) > 0:
        fg_heat = folium.FeatureGroup(name="🔥 Mapa de calor", show=False)
        HeatMap(pts[["latitude", "longitude"]].values.tolist(), radius=14, blur=18).add_to(fg_heat)
        fg_heat.add_to(m)

    folium.LayerControl(position="topright", collapsed=False).add_to(m)
    m.get_root().html.add_child(folium.Element(_JS_BUSCADOR_APILADO))
    return m.get_root().render()


# JS: buscador (CP/zona), modo hover/click apilado, cuadro info izquierdo.
_JS_BUSCADOR_APILADO = r"""
    <style>
      #cpSearchBar * { box-sizing:border-box; }
      .api-zona-item { padding:7px 9px; margin:3px 0; border:1px solid #e2e8f0; border-radius:6px;
          cursor:pointer; background:#f8fafc; transition:background .15s; }
      .api-zona-item:hover { background:#e0edff; }
      .api-volver { display:inline-block; margin-bottom:8px; padding:5px 10px; background:#2563eb;
          color:white; border-radius:6px; cursor:pointer; font-weight:600; font-size:12px; }
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
        z-index:9999; background:white; padding:8px 14px; border-radius:10px;
        box-shadow:0 4px 16px rgba(0,0,0,0.25); display:flex; align-items:center; gap:8px;
        font-family:sans-serif;">
        <span style="font-size:16px">🔍</span>
        <input id="cpInput" type="text" placeholder="Buscar CP o Zona..."
            onkeyup="if(event.key==='Enter')buscarCP(this.value)"
            style="border:1px solid #e2e8f0; border-radius:6px; padding:6px 12px;
            font-size:14px; width:170px; outline:none;" />
        <button onclick="buscarCP(document.getElementById('cpInput').value)"
            style="background:#2563eb; color:white; border:none; border-radius:6px;
            padding:6px 14px; font-size:13px; font-weight:600; cursor:pointer;">Buscar</button>
        <button id="cpClearBtn" onclick="limpiarBusqueda()" title="Quitar resaltado"
            style="background:#e2e8f0; color:#475569; border:none; border-radius:6px;
            padding:6px 11px; font-size:13px; font-weight:700; cursor:pointer;">✕</button>
        <span id="cpResult" style="font-size:12px; max-width:350px; white-space:nowrap;
            overflow:hidden; text-overflow:ellipsis;"></span>
    </div>
    <div id="infoPanel">
        <div class="ip-head">
            <div style="font-weight:bold;color:#2563eb;">ℹ️ Información</div>
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
        ly.setStyle({fillColor:'#2563eb',fillOpacity:0.7,color:'#2563eb',weight:3});
        try{ if(ly.bringToFront)ly.bringToFront(); }catch(e){}
        _hl=ly; if(ly.getBounds&&map)map.fitBounds(ly.getBounds(),{padding:[50,50],maxZoom:14}); if(ly.openTooltip)ly.openTooltip();
    }
    window.limpiarBusqueda=function(){
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
        if(lyZona){ _resaltarCapa(lyZona); if(rd){rd.innerHTML='✔ Zona '+clave+' localizada';rd.style.color='#16a34a';} }
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
        var out=['<div style="font-weight:bold;color:#2563eb;margin-bottom:6px;">🔎 '+arr.length+' zonas aquí — elige una:</div>'];
        for(var i=0;i<arr.length;i++){ var nom=arr[i].nom||('Zona '+(i+1));
            out.push('<div class="api-zona-item" data-idx="'+i+'"><span style="color:#2563eb;font-weight:600;">▸</span> <b>'+nom+'</b></div>'); }
        return out.join('');
    }
    function _vistaDetalle(idx){ var arr=window._apiladas||[]; if(idx<0||idx>=arr.length)return '';
        return '<div class="api-volver" data-volver="1">← Volver a la lista</div><div>'+(arr[idx].tt||'')+'</div>'; }
    function _fmtCP(pr){
        if(!pr)return '';
        var cp=(pr.CP!==undefined&&pr.CP!==null)?String(pr.CP).replace(/\.0$/,''):'';
        var filas=[['Código Postal',cp],['PDP (✔ perfil)',pr._pdp!=null?pr._pdp:0],['PDNP (✘ perfil)',pr._pdnp!=null?pr._pdnp:0],['Elegibles (Aurora)',pr._elegibles!=null?pr._elegibles:0]];
        var out=['<div style="font-weight:bold;color:#0e7490;margin-bottom:4px;">📮 CP '+cp+'</div>'];
        for(var i=0;i<filas.length;i++){ out.push('<div><b>'+filas[i][0]+':</b> '+filas[i][1]+'</div>'); }
        return out.join('');
    }
    var _zonaHL=null,_zonaHL_os=null;
    function _quitarResaltadoZona(){ if(_zonaHL&&_zonaHL_os){try{_zonaHL.setStyle(_zonaHL_os);}catch(e){}} _zonaHL=null;_zonaHL_os=null; }
    function _ensureInfoPanel(){ var panel=document.getElementById('infoPanel'); if(panel)return panel;
        panel=document.createElement('div'); panel.id='infoPanel';
        panel.style.cssText='position:fixed;left:12px;bottom:85px;z-index:99999;width:320px;max-height:46vh;overflow-y:auto;background:rgba(255,255,255,0.55);-webkit-backdrop-filter:blur(5px);backdrop-filter:blur(5px);border:1px solid rgba(203,213,225,0.6);border-radius:12px;box-shadow:0 6px 22px rgba(0,0,0,0.20);padding:12px 14px;font-family:sans-serif;font-size:12px;color:#1e293b;display:none;';
        panel.innerHTML='<div class="ip-head" style="display:flex;justify-content:space-between;align-items:center;margin:-2px 0 8px 0;"><div style="font-weight:bold;color:#2563eb;">ℹ️ Información</div><button class="ip-close" onclick="cerrarInfoPanel()" title="Cerrar" style="cursor:pointer;background:#e2e8f0;color:#475569;border:none;border-radius:6px;padding:2px 8px;font-weight:700;font-size:13px;">✕</button></div><div id="infoPanelBody"></div>';
        document.body.appendChild(panel); return panel;
    }
    function _panelMostrar(html){ var panel=_ensureInfoPanel(); var body=document.getElementById('infoPanelBody'); if(!panel||!body)return; body.innerHTML=html; panel.style.display='block'; }
    window.cerrarInfoPanel=function(){ var panel=document.getElementById('infoPanel'); if(panel)panel.style.display='none'; try{_quitarResaltadoZona();}catch(e){} };
    function _resaltarZonaPorZid(zid){ var map=window._map||_findMap(); var ly=(window._zonaLayerByZid||{})[zid]; _quitarResaltadoZona(); if(!ly)return;
        _zonaHL_os={ fillColor:ly.options.fillColor||'#9e9e9e', fillOpacity:(typeof ly.options.fillOpacity!=='undefined')?ly.options.fillOpacity:0.45, color:ly.options.color||'black', weight:ly.options.weight||1 };
        try{ ly.setStyle({fillColor:'#2563eb',fillOpacity:0.7,color:'#2563eb',weight:3}); }catch(e){}
        try{ if(ly.bringToFront)ly.bringToFront(); }catch(e){} _zonaHL=ly;
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
            if(btn){btn.innerHTML='👆 Info: Click (apilado)';btn.style.background='#2563eb';} if(hint)hint.innerHTML='Clic → elige entre las encimadas';
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
        f_seteo = st.file_uploader("1) Seteo (searchHubsResponse .csv)", type=["csv"], key="rd_seteo")
        f_pdp = st.file_uploader("2a) PDP — entregados que CUMPLEN perfil (.xlsx/.csv)", type=["xlsx", "csv"], key="rd_pdp")
        f_pdnp = st.file_uploader("2b) PDNP — entregados que NO cumplen (.xlsx/.csv)", type=["xlsx", "csv"], key="rd_pdnp")
    with c2:
        f_raw = st.file_uploader("3) Raw Data at Tracking (.xlsx/.csv)", type=["xlsx", "csv"], key="rd_raw")
        f_elig = st.file_uploader("4) Elegibilidad / Data Aurora Dia (.xlsx)", type=["xlsx"], key="rd_elig")
        f_total = st.file_uploader("5) Total_Data (opcional — enriquece plan)", type=["xlsx", "csv"], key="rd_total")

    if not (f_seteo and f_pdp and f_pdnp and f_raw and f_elig):
        st.info("Sube al menos los archivos 1–4 (Total_Data es opcional) para habilitar el análisis.")
        return

    # Hoja de elegibilidad
    try:
        xls = pd.ExcelFile(io.BytesIO(f_elig.getvalue()))
        hoja = next((h for h in xls.sheet_names if "aurora" in h.lower()), xls.sheet_names[0])
    except Exception:
        hoja = 0

    total_bytes = f_total.getvalue() if f_total else None
    total_name = f_total.name if f_total else ""

    # ── BARRA DE PROGRESO de carga de archivos ──
    # La carga real (cargar_archivos) está cacheada, así que al repetir el
    # mismo set de archivos es casi instantánea. La barra informa las etapas;
    # la primera vez acompaña la lectura/normalización real de los 4-5 archivos.
    _firma = (f_seteo.name, len(f_seteo.getvalue()),
              f_pdp.name, f_pdnp.name, f_raw.name, f_elig.name, total_name)
    _ya_cargado = st.session_state.get("rd_firma") == _firma

    if _ya_cargado and "rd_cache" in st.session_state:
        seteo, geo, tr2emp, tr2status, elig, total = st.session_state["rd_cache"]
    else:
        _prog = st.progress(0, text="Preparando carga de archivos…")
        _etapas = [
            (10, "📥 Leyendo SETEO (searchHubsResponse)…"),
            (30, "🟢 Leyendo PDP (cumplen perfil)…"),
            (45, "🔴 Leyendo PDNP (no cumplen)…"),
            (60, "📦 Leyendo Raw Data (repartidores)…"),
            (75, "📋 Leyendo Elegibilidad (Aurora)…"),
            (88, "🎯 Leyendo Total_Data (opcional)…"),
            (95, "⚙️ Normalizando y cruzando datos…"),
        ]
        for _pc, _msg in _etapas:
            _prog.progress(_pc, text=_msg)
        # Carga real (cacheada): se ejecuta una sola vez por set de archivos.
        seteo, geo, tr2emp, tr2status, elig, total = cargar_archivos(
            f_seteo.getvalue(), f_seteo.name,
            f_pdp.getvalue(), f_pdp.name,
            f_pdnp.getvalue(), f_pdnp.name,
            f_raw.getvalue(), f_raw.name,
            f_elig.getvalue(), f_elig.name, hoja,
            total_bytes, total_name,
        )
        _prog.progress(100, text="✅ Archivos cargados")
        _prog.empty()
        st.session_state["rd_firma"] = _firma
        st.session_state["rd_cache"] = (seteo, geo, tr2emp, tr2status, elig, total)

    dias = fechas_disponibles(seteo, geo)
    if not dias:
        st.warning("No hay días con seteo (Store Active + SW ENABLED en ese día de la semana) y geopuntos.")
        return

    cc1, cc2 = st.columns([2, 1])
    with cc1:
        dia = st.selectbox("📅 Día a analizar:", dias, index=len(dias) - 1)
    with cc2:
        # nodos desde los geopuntos (nodo/zone)
        nodos = sorted([n for n in geo["_nodo"].dropna().unique() if n]) if "_nodo" in geo.columns else [nodo_default]
        if not nodos:
            nodos = [nodo_default]
        nodo = st.selectbox("🏢 Nodo:", nodos, index=nodos.index(nodo_default) if nodo_default in nodos else 0)

    if st.button("🚀 Analizar día", type="primary", use_container_width=True):
        with st.spinner(f"Procesando {dia} ({_fecha_a_dow(dia)})…"):
            R = procesar_dia(seteo, geo, tr2emp, tr2status, elig, dia, nodo=nodo, total=total)
            hist = calcular_historico_periodo(seteo, geo, tr2emp, elig, dias, nodo=nodo)
            st.session_state["rd_R"] = R
            st.session_state["rd_hist"] = hist
            st.session_state["rd_mapa"] = construir_mapa_dia_html(R, gdf_cp_wgs84=gdf_cp_wgs84)
            st.session_state["rd_total"] = total
            st.session_state["rd_dias"] = dias
            st.session_state["rd_seteo"] = seteo
            st.session_state["rd_geo"] = geo
            st.session_state["rd_tr2emp"] = tr2emp

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
            _hist_z = historico_total_por_zona(
                st.session_state.get("rd_seteo", seteo), st.session_state.get("rd_geo", geo),
                st.session_state.get("rd_tr2emp", tr2emp), st.session_state.get("rd_dias", dias),
                total=st.session_state.get("rd_total", total))
            _dem_z = demanda_libre_por_zona(R, gdf_cp_wgs84=gdf_cp_wgs84)
            _df_plan = distribuir_confirmation_plan(R, int(_plan_total), _hist_z, _dem_z)
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
        st.download_button("📊 Descargar Análisis Excel", data=buf.getvalue(),
                           file_name=f"Analisis_Real_{R['nodo']}_{dia.replace('/','-')}.xlsx",
                           mime="application/vnd.ms-excel", use_container_width=True)
