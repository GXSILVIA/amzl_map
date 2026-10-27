# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════
 CAPA "ANÁLISIS REAL POR DÍA"  —  módulo complementario de app_sistema_pro.py
═══════════════════════════════════════════════════════════════════════════
Analiza un DÍA concreto con 4 archivos reales:

  1. SETEO del día (searchHubsResponse): Store Name, LATITUD, LONGITUD,
     SW Max Cap (volumen solicitado), SW Coverage (radio m), SW Status
     (ENABLED/DISABLED), fecha, Station Code (nodo), ZipCode.
     → Solo se dibujan las zonas ENABLED (círculo = Store Name).

  2. GEOPUNTOS (paquetes entregados): event_day, zone(nodo), tracking_id,
     latitude, longitude.  → puntitos en el mapa (+ heatmap).
     Los que NO traen coordenadas se descartan.

  3. RAW DATA (detalle por paquete): Dispatch Day, tracking_id, Employee,
     postal_code, last_shipment_status, ...
     → se usa para mapear tracking_id → Employee.

  4. ELEGIBILIDAD (Data Aurora Dia): event_day, zone(nodo),
     destination_postal_code, libre.  → paquetes elegibles por CP y día.

FLUJO DE ASIGNACIÓN de cada paquete (punto) a su zona (Store Name):
  a) tracking_id del geopunto → Employee (Raw) → Store Name (Seteo).
     (Employee == Store Name: el repartidor ES la zona.)
  b) RESPALDO geométrico: si el tracking no cruza, el punto se asigna a la
     zona cuyo círculo lo contiene; si lo contienen varias (traslape), a la
     MÁS CERCANA al centro.
  c) Si no cae en ninguna zona ni cruza por tracking → NO ASIGNADO
     (se pinta en color distinto).

Uso desde app_sistema_pro.py:
    from capa_real_dia import render_analisis_real_dia
    render_analisis_real_dia()          # pinta su propio bloque de uploaders+mapa

El módulo es auto-contenido: no modifica la lógica existente de la app.
"""

import io
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
# 📥 CARGA + NORMALIZACIÓN de los 4 archivos
# ═══════════════════════════════════════════════════════════════════════
@st.cache_data(show_spinner=False)
def cargar_archivos(seteo_bytes, geo_bytes, raw_bytes, elig_bytes, elig_sheet):
    """Lee los 4 archivos (desde bytes para que el cache funcione) y normaliza."""
    # --- SETEO ---
    seteo = pd.read_csv(io.BytesIO(seteo_bytes))
    seteo.columns = [str(c).strip() for c in seteo.columns]
    seteo["_sn"] = seteo["Store Name"].map(_norm_txt)
    seteo["_fecha"] = _norm_fecha(seteo["fecha"])
    for c in ["LATITUD", "LONGITUD", "SW Coverage", "SW Max Cap"]:
        seteo[c] = pd.to_numeric(seteo[c], errors="coerce")
    seteo["_status"] = seteo["SW Status"].map(_norm_txt)

    # --- GEOPUNTOS (descartar sin coordenadas) ---
    geo = pd.read_csv(io.BytesIO(geo_bytes))
    geo.columns = [str(c).strip() for c in geo.columns]
    geo["tracking_id"] = geo["tracking_id"].astype(str).str.strip()
    geo["latitude"] = pd.to_numeric(geo["latitude"], errors="coerce")
    geo["longitude"] = pd.to_numeric(geo["longitude"], errors="coerce")
    geo = geo.dropna(subset=["latitude", "longitude"])
    geo = geo[(geo["latitude"] != 0) & (geo["longitude"] != 0)]
    geo["_fecha"] = _norm_fecha(geo["event_day"])

    # --- RAW: tracking → Employee (match global por tracking) ---
    raw = pd.read_csv(io.BytesIO(raw_bytes))
    raw.columns = [str(c).strip() for c in raw.columns]
    raw["tracking_id"] = raw["tracking_id"].astype(str).str.strip()
    raw["_emp"] = raw["Employee"].map(_norm_txt)
    if "postal_code" in raw.columns:
        raw["_cp"] = raw["postal_code"].map(_norm_cp)
    tr2emp = dict(zip(raw["tracking_id"], raw["_emp"]))
    tr2status = dict(zip(raw["tracking_id"],
                         raw.get("last_shipment_status", pd.Series(index=raw.index)).map(_norm_txt)))

    # --- ELEGIBILIDAD (Aurora) ---
    elig = pd.read_excel(io.BytesIO(elig_bytes), sheet_name=elig_sheet)
    elig.columns = [str(c).strip() for c in elig.columns]
    elig["_fecha"] = _norm_fecha(elig["event_day"])
    elig["_cp"] = elig["destination_postal_code"].map(_norm_cp)
    elig["_zona"] = elig["zone"].map(_norm_txt)
    elig["libre"] = pd.to_numeric(elig["libre"], errors="coerce").fillna(0)

    return seteo, geo, tr2emp, tr2status, elig


def fechas_disponibles(seteo, geo):
    """Días que tienen seteo ENABLED y geopuntos — ordenados cronológicamente."""
    f_set = set(seteo.loc[seteo["_status"] == "ENABLED", "_fecha"].dropna())
    f_geo = set(geo["_fecha"].dropna())
    comun = f_set & f_geo
    return sorted(comun, key=lambda d: (d[6:], d[3:5], d[:2]))


# ═══════════════════════════════════════════════════════════════════════
# 🎲 TRASLAPE del día (Monte Carlo vectorizado, por zona y total)
# ═══════════════════════════════════════════════════════════════════════
def calcular_traslape_dia(zonas, n=8000, seed=42):
    """
    zonas: lista de dicts {NOM, LAT, LON, RAD}
    Devuelve: {NOM: {"pct": float, "nivel": str, "desglose": [{NOM,PCT}]}}, pct_total
    """
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
def procesar_dia(seteo, geo, tr2emp, tr2status, elig, dia, nodo=None):
    """
    Devuelve un dict 'R' con todo lo necesario para pintar el mapa y las tablas.
    """
    # 1) ZONAS ENABLED del día
    sd = seteo[(seteo["_fecha"] == dia) & (seteo["_status"] == "ENABLED")].copy()
    sd = sd.dropna(subset=["LATITUD", "LONGITUD", "SW Coverage"])
    if nodo and "Station Code" in sd.columns:
        sd = sd[sd["Station Code"].map(_norm_txt) == _norm_txt(nodo)]
    zonas = []
    for _, r in sd.iterrows():
        zonas.append({
            "NOM": str(r["Store Name"]),
            "_sn": r["_sn"],
            "LAT": float(r["LATITUD"]),
            "LON": float(r["LONGITUD"]),
            "RAD": float(r["SW Coverage"]),
            "MAXCAP": float(r["SW Max Cap"]) if pd.notna(r["SW Max Cap"]) else 0.0,
            "CP": _norm_cp(r["ZipCode"]) if "ZipCode" in sd.columns else "",
        })
    set_sn = {z["_sn"] for z in zonas}

    # 2) GEOPUNTOS del día (con coords)
    gd = geo[geo["_fecha"] == dia].copy()
    gd["_emp"] = gd["tracking_id"].map(tr2emp)
    gd["_status_entrega"] = gd["tracking_id"].map(tr2status)
    # Asignación por tracking→Employee→StoreName (solo si el Employee es zona ENABLED)
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
        d2 = dlat ** 2 + dlon ** 2                       # (P, Z)
        dentro = d2 <= (sz_rad[None, :] ** 2)            # (P, Z)
        d2m = np.where(dentro, d2, np.inf)
        idx_min = np.argmin(d2m, axis=1)
        tiene = np.isfinite(d2m[np.arange(len(gd)), idx_min])
        zona_geom = np.where(tiene, sz_sn[idx_min], None)
        dentro_alguna = tiene
    gd["_zona_geom"] = zona_geom

    # 4) ZONA FINAL: tracking si existe, si no geometría
    zt = gd["_zona_tracking"]
    gd["_zona_final"] = zt.where(zt.notna(), pd.Series(gd["_zona_geom"].values, index=gd.index))
    gd["_asignado"] = gd["_zona_final"].notna()
    gd["_origen_asig"] = np.where(zt.notna(), "tracking",
                                  np.where(gd["_zona_geom"].astype(object).notna(), "geometria", "sin_asignar"))

    # 5) MÉTRICAS por zona
    cap_por_zona = gd.loc[gd["_asignado"], "_zona_final"].map(_norm_txt).value_counts().to_dict()

    # 6) ELEGIBILIDAD del día por CP (nodo opcional)
    ed = elig[elig["_fecha"] == dia].copy()
    if nodo:
        ed = ed[ed["_zona"] == _norm_txt(nodo)]
    elig_por_cp = ed.groupby("_cp")["libre"].sum().to_dict()

    # 7) TRASLAPE del día
    traslape_zona, traslape_total = calcular_traslape_dia(zonas)

    # 8) Puntos por CP (según postal_code del raw por tracking, si existe; si no, no se cuenta aquí)
    #    El conteo de puntos dentro de cada polígono de CP se hace en la app con las
    #    geometrías GeoJSON; aquí entregamos el dict de elegibilidad y los puntos.
    total_puntos = len(gd)
    total_asignados = int(gd["_asignado"].sum())
    total_no_asig = total_puntos - total_asignados

    R = {
        "dia": dia,
        "nodo": nodo or "DVR2",
        "zonas": zonas,
        "puntos": gd[["tracking_id", "latitude", "longitude", "_zona_final",
                      "_asignado", "_origen_asig", "_status_entrega"]].copy(),
        "cap_por_zona": cap_por_zona,
        "elig_por_cp": elig_por_cp,
        "traslape_zona": traslape_zona,
        "traslape_total": traslape_total,
        "total_puntos": total_puntos,
        "total_asignados": total_asignados,
        "total_no_asignados": total_no_asig,
    }
    return R


# ═══════════════════════════════════════════════════════════════════════
# 📊 TABLAS de análisis descargable
# ═══════════════════════════════════════════════════════════════════════
def construir_entregas_no_asignadas(R):
    """
    PAQUETES POR LLEVAR por zona = puntos (morados) NO asignados por tracking
    que SÍ caen DENTRO del radio de alguna zona → esa zona aún los puede llevar.
    Cada punto se cuenta a la zona MÁS CERCANA entre las que lo contienen.

    Devuelve 2 DataFrames:
      A) Resumen por zona: nº de paquetes por llevar (morados dentro de su radio).
      B) Detalle punto-por-punto (tracking, coords, zona dentro de cuyo radio cae).
    """
    zonas = R["zonas"]
    pts = R["puntos"].copy()
    # "morado" = no asignado por tracking. R['puntos'] trae _origen_asig.
    if "_origen_asig" in pts.columns:
        es_morado = pts["_origen_asig"] != "tracking"
    else:
        es_morado = ~pts["_asignado"]
    no = pts[es_morado].copy()

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
        dentro = d2 <= (szrad[None, :] ** 2)          # dentro del radio de la zona
        d2m = np.where(dentro, d2, np.inf)
        im = np.argmin(d2m, axis=1)
        cae = np.isfinite(d2m[np.arange(len(no)), im])  # cae en ≥1 zona
        trk = no["tracking_id"].to_numpy()
        for k in range(len(no)):
            if cae[k]:
                zn = sznom[im[k]]
                conteo[zn] = conteo.get(zn, 0) + 1
                filas.append({
                    "tracking_id": trk[k],
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
        columns=["tracking_id", "latitude", "longitude", "Zona (dentro de su radio)"])
    return dfA, dfB


def construir_tabla_zonas(R, hist_por_zona=None):
    """
    Tabla por VR (estilo imagen): Seteo(SW Max Cap), Volumen Despachado(captados),
    Ocupación %, Traslape, Nivel, + Histórico/Máx/Mín/Probabilidad.
    hist_por_zona: {sn_upper: {"hist":x,"max":y,"min":z}} (de días previos del periodo).
    """
    filas = []
    for z in R["zonas"]:
        sn = z["_sn"]
        desp = int(R["cap_por_zona"].get(sn, 0))
        maxcap = int(z["MAXCAP"]) if z["MAXCAP"] else 0
        ocup = round((desp / maxcap) * 100, 1) if maxcap > 0 else 0.0
        tinfo = R["traslape_zona"].get(z["NOM"], {})
        pct_t = tinfo.get("pct", 0.0)
        # Probabilidad de recibir más volumen (regla por ocupación)
        if ocup < 70:
            prob = "Alta"
        elif ocup <= 90:
            prob = "Media"
        else:
            prob = "Baja"
        h = (hist_por_zona or {}).get(sn, {})
        filas.append({
            "VR (Store Name)": z["NOM"],
            "Seteo (SW Max Cap)": maxcap,
            "SW Coverage (m)": int(z["RAD"]),
            "Volumen Despachado": desp,
            "Ocupación": f"{ocup}%",
            "Histórico": h.get("hist", ""),
            "Máximo": h.get("max", ""),
            "Mínimo": h.get("min", ""),
            "% Traslape": f"{pct_t}%",
            "Nivel Traslape": tinfo.get("nivel", "⚪ Sin datos"),
            "Prob. más volumen": prob,
        })
    df = pd.DataFrame(filas)
    if not df.empty:
        df = df.sort_values("Volumen Despachado", ascending=False).reset_index(drop=True)
    return df


def calcular_historico_periodo(seteo, geo, tr2emp, elig, dias, nodo=None):
    """
    Histórico/Máx/Mín del volumen despachado por zona a lo largo de los días del periodo.
    (Se calcula una sola vez; cacheado por la app.)
    """
    acum = {}  # sn_upper -> lista de despachados por día
    set_all = seteo[seteo["_status"] == "ENABLED"]
    for d in dias:
        sd = set_all[set_all["_fecha"] == d]
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
# 🗺️ MAPA del día (Folium) — círculos + puntos + heatmap + buscador + apilados
# ═══════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════
# 🎯 MOTOR "CONFIRMATION PLAN" — distribuye un volumen total objetivo entre
#    las zonas ENABLED del día, 50% por histórico (todo el mes) + 50% por
#    demanda libre del polígono, ajustado por traslape. Σ sugeridos = plan.
#    Tope por zona = 96 (excedente se redistribuye entre las demás).
# ═══════════════════════════════════════════════════════════════════════
TOPE_ZONA = 96          # máximo de volumen por zona
W_HIST = 0.5            # peso histórico de despacho
W_DEMANDA = 0.5         # peso demanda libre del polígono


def historico_total_por_zona(seteo, geo, tr2emp, dias):
    """Promedio de despacho por zona usando TODO el mes cargado (peso histórico)."""
    set_all = seteo[seteo["_status"] == "ENABLED"]
    acum = {}
    for d in dias:
        sd = set_all[set_all["_fecha"] == d]
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
    """
    Demanda libre (elegibles 'libre') asociada a cada zona ENABLED del día.
    - Si hay polígonos de CP: suma 'libre' de los CPs cuyo centroide cae dentro
      del círculo de la zona (reparto a la zona más cercana si traslapan).
    - Si NO hay polígonos: aproxima por el CP sede de la zona (ZipCode del seteo).
    """
    zonas = R["zonas"]
    elig_cp = R["elig_por_cp"]
    demanda = {z["_sn"]: 0.0 for z in zonas}
    if not zonas:
        return demanda

    # Fallback simple: elegibilidad del CP sede (ZipCode) de cada zona
    if gdf_cp_wgs84 is None or getattr(gdf_cp_wgs84, "empty", True):
        for z in zonas:
            demanda[z["_sn"]] = float(elig_cp.get(_norm_cp(z.get("CP", "")), 0.0))
        return demanda

    # Con polígonos: centroide del CP → zona más cercana cuyo círculo lo contiene
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
        # distancia² CP×zona en metros
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
    """
    Reparte plan_total entre las zonas ENABLED del día.
    peso = w_hist*hist_norm + w_dem*demanda_norm, ajustado por (1 - traslape/2).
    Aplica tope por zona y redistribuye el excedente iterativamente.
    Devuelve DataFrame: Zona, Seteo actual, Seteo sugerido, % del plan, Δ, Razón.
    """
    zonas = R["zonas"]
    if not zonas or plan_total <= 0:
        return pd.DataFrame(columns=["VR (Store Name)", "Seteo actual", "Seteo sugerido",
                                     "% del plan", "Δ", "Razón"])

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

    # Reparto con tope + redistribución iterativa del excedente
    asign = np.zeros(len(sns))
    libres = np.ones(len(sns), dtype=bool)
    restante = float(plan_total)
    for _ in range(100):
        p = peso * libres
        if p.sum() <= 0 or restante <= 0.5:
            break
        add = restante * (p / p.sum())
        nuevo = asign + add
        # aplicar tope
        exceso_mask = nuevo > tope
        if exceso_mask.any():
            asign[exceso_mask] = tope
            libres[exceso_mask] = False
            restante = float(plan_total) - asign.sum()
        else:
            asign = nuevo
            restante = 0.0
            break

    sugerido = np.round(asign).astype(int)
    # Ajuste fino para que la suma sea EXACTA al plan (por redondeos)
    diff = int(plan_total) - int(sugerido.sum())
    if diff != 0:
        orden = np.argsort(-peso)  # ajustar primero a las de mayor peso
        i = 0
        while diff != 0 and i < len(orden) * 3:
            k = orden[i % len(orden)]
            cand = sugerido[k] + (1 if diff > 0 else -1)
            if 0 <= cand <= tope:
                sugerido[k] = cand
                diff += -1 if diff > 0 else 1
            i += 1

    filas = []
    plan = max(1, int(plan_total))
    for j, z in enumerate(zonas):
        actual = int(z["MAXCAP"])
        sug = int(sugerido[j])
        delta = sug - actual
        razon = []
        razon.append(f"hist≈{hist[j]:.0f}")
        razon.append(f"libre≈{dem[j]:.0f}")
        if tras[j] >= 0.5:
            razon.append(f"⚠️traslape {tras[j]*100:.0f}%")
        if sug >= tope:
            razon.append("tope 96")
        filas.append({
            "VR (Store Name)": z["NOM"],
            "Seteo actual": actual,
            "Seteo sugerido": sug,
            "% del plan": f"{round(sug/plan*100,1)}%",
            "Δ": (f"+{delta}" if delta > 0 else str(delta)),
            "Razón": ", ".join(razon),
        })
    df = pd.DataFrame(filas).sort_values("Seteo sugerido", ascending=False).reset_index(drop=True)
    return df


def construir_mapa_dia_html(R, gdf_cp_wgs84=None):
    """
    gdf_cp_wgs84: GeoDataFrame opcional con polígonos de CP (col 'CP' y geometry)
                  para contar puntos por CP y mostrar elegibilidad en tooltip.
    """
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

    m = folium.Map(location=[c_lat, c_lon], zoom_start=11,
                   tiles="https://tile.openstreetmap.de/{z}/{x}/{y}.png",
                   attr="&copy; OpenStreetMap", prefer_canvas=True)

    # ── (opcional) Conteo de puntos por CP y elegibilidad en tooltip ──
    fg_cp = folium.FeatureGroup(name="CP + Elegibilidad", show=bool(gdf_cp_wgs84 is not None))
    if gdf_cp_wgs84 is not None and not gdf_cp_wgs84.empty:
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            gpts = gpd.GeoDataFrame(
                pts.copy(),
                geometry=[Point(xy) for xy in zip(pts["longitude"], pts["latitude"])],
                crs="EPSG:4326",
            )
            cob = gdf_cp_wgs84[["CP", "geometry"]].copy()
            cob["CP"] = cob["CP"].astype(str).map(_norm_cp)
            join = gpd.sjoin(gpts, cob, how="left", predicate="within")
            cnt_cp = join.dropna(subset=["CP"]).groupby("CP").size().to_dict()
            cob["_entregados"] = cob["CP"].map(lambda c: int(cnt_cp.get(c, 0)))
            cob["_elegibles"] = cob["CP"].map(lambda c: int(R["elig_por_cp"].get(c, 0)))
            cob["_diferencia"] = cob["_elegibles"] - cob["_entregados"]
            gj = cob.to_json()
            folium.GeoJson(
                gj,
                style_function=lambda f: {"fillColor": "#3388ff", "color": "#ffffff",
                                          "weight": 1, "fillOpacity": 0.12},
                tooltip=folium.GeoJsonTooltip(
                    fields=["CP", "_entregados", "_elegibles", "_diferencia"],
                    aliases=["CP:", "Entregados hoy:", "Elegibles (libre):", "Diferencia:"],
                    localize=True),
            ).add_to(fg_cp)
        except Exception as e:
            folium.map.Marker([c_lat, c_lon],
                              tooltip=f"(CP overlay no disponible: {e})").add_to(fg_cp)
    fg_cp.add_to(m)

    # ── CÍRCULOS de zonas ENABLED (una capa) ──
    fg_z = folium.FeatureGroup(name="Zonas", show=True)
    features = []
    for i, z in enumerate(zonas):
        tinfo = R["traslape_zona"].get(z["NOM"], {})
        desp = int(R["cap_por_zona"].get(z["_sn"], 0))
        ocup = round((desp / z["MAXCAP"]) * 100, 1) if z["MAXCAP"] else 0.0
        tt = ("<b>Zona: {nom}</b><br>SW Max Cap: {mc}<br>Radio: {rad} m<br>"
              "Despachado: {d}<br>Ocupación: {o}%<br>"
              "<b>Traslape: {t}% — {nv}</b>").format(
            nom=z["NOM"], mc=int(z["MAXCAP"]), rad=int(z["RAD"]), d=desp, o=ocup,
            t=tinfo.get("pct", 0.0), nv=tinfo.get("nivel", "⚪"))
        # círculo como polígono aproximado (para GeoJson + buscador + apilados)
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
            popup=folium.GeoJsonPopup(fields=["_tt"], aliases=[""], max_width=320),
        ).add_to(fg_z)
    fg_z.add_to(m)

    # ── PUNTOS: blancos = asignados por TRACKING; morados = NO asignados por
    #    tracking (aunque caigan dentro de un círculo → son los "por llevar").
    #    El color se decide por el ORIGEN de la asignación, no por si cae o no
    #    en un círculo: así los puntos sin tracking salen morados como pediste.
    fg_ok = folium.FeatureGroup(name="📦 Entregados", show=True)
    fg_no = folium.FeatureGroup(name="❗ No asignados", show=True)
    if "_origen_asig" in pts.columns:
        _es_track = pts["_origen_asig"] == "tracking"
    else:
        _es_track = pts["_asignado"]
    pts_ok = pts[_es_track]
    pts_no = pts[~_es_track]
    for _, p in pts_ok.iterrows():
        folium.CircleMarker(
            [p["latitude"], p["longitude"]], radius=2.5,
            color="#333333", weight=0.5, fill=True, fill_color="#ffffff", fill_opacity=0.95,
            tooltip=f"✅ {p['tracking_id']}<br>Zona: {p['_zona_final']}",
        ).add_to(fg_ok)
    for _, p in pts_no.iterrows():
        folium.CircleMarker(
            [p["latitude"], p["longitude"]], radius=2.5,
            color="#7b1fa2", weight=0.5, fill=True, fill_color="#e040fb", fill_opacity=0.9,
            tooltip=f"❗ No asignado<br>{p['tracking_id']}",
        ).add_to(fg_no)
    fg_ok.add_to(m)
    fg_no.add_to(m)

    # ── HEATMap de todos los puntos ──
    if HeatMap is not None and len(pts) > 0:
        fg_heat = folium.FeatureGroup(name="🔥 Mapa de calor", show=False)
        HeatMap(pts[["latitude", "longitude"]].values.tolist(), radius=14, blur=18).add_to(fg_heat)
        fg_heat.add_to(m)

    folium.LayerControl(position="topright", collapsed=False).add_to(m)

    # ── Buscador + apilados + cuadro info (JS) — basado en la 1ª capa ──
    m.get_root().html.add_child(folium.Element(_JS_BUSCADOR_APILADO))
    return m.get_root().render()


# JS: buscador (CP/zona), modo hover/click apilado, cuadro info izquierdo.
_JS_BUSCADOR_APILADO = r"""
<style>
  .api-zona-item{padding:7px 9px;margin:3px 0;border:1px solid #e2e8f0;border-radius:6px;
     cursor:pointer;background:#f8fafc;}
  .api-zona-item:hover{background:#e0edff;}
  #infoPanel{position:fixed;left:12px;bottom:85px;z-index:99999;width:320px;max-height:46vh;
     overflow-y:auto;background:rgba(255,255,255,0.92);border:1px solid #cbd5e1;border-radius:12px;
     box-shadow:0 6px 22px rgba(0,0,0,0.2);padding:12px 14px;font-family:sans-serif;font-size:12px;
     color:#1e293b;display:none;}
  #cpSearchBar{position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:9999;background:white;
     padding:8px 14px;border-radius:10px;box-shadow:0 4px 16px rgba(0,0,0,0.25);display:flex;
     align-items:center;gap:8px;font-family:sans-serif;}
</style>
<div id="cpSearchBar">
  <span>🔍</span>
  <input id="cpInput" type="text" placeholder="Buscar Zona o CP..."
     onkeyup="if(event.key==='Enter')buscar(this.value)"
     style="border:1px solid #e2e8f0;border-radius:6px;padding:6px 12px;font-size:14px;width:180px;">
  <button onclick="buscar(document.getElementById('cpInput').value)"
     style="background:#2563eb;color:white;border:none;border-radius:6px;padding:6px 14px;cursor:pointer;">Buscar</button>
  <button onclick="limpiar()" style="background:#e2e8f0;border:none;border-radius:6px;padding:6px 11px;cursor:pointer;">✕</button>
  <span id="cpResult" style="font-size:12px;"></span>
</div>
<div id="infoPanel"><div style="font-weight:bold;color:#2563eb;margin-bottom:6px;">ℹ️ Información
  <button onclick="document.getElementById('infoPanel').style.display='none'"
    style="float:right;background:#e2e8f0;border:none;border-radius:6px;cursor:pointer;">✕</button></div>
  <div id="infoBody"></div></div>
<script>
function _findMap(){for(var k in window){try{if(k.indexOf('map_')===0&&window[k]&&window[k].eachLayer)return window[k];}catch(e){}}return null;}
function _walk(l,cb){cb(l);if(l.eachLayer)l.eachLayer(function(s){_walk(s,cb);});if(l._layers){for(var id in l._layers)_walk(l._layers[id],cb);}}
function _dist(a,b,c,d){var R=6371000,dx=(c-a)*Math.PI/180,dy=(d-b)*Math.PI/180;var x=Math.sin(dx/2)**2+Math.cos(a*Math.PI/180)*Math.cos(c*Math.PI/180)*Math.sin(dy/2)**2;return R*2*Math.atan2(Math.sqrt(x),Math.sqrt(1-x));}
var _hl=null,_os=null,_zidx={},_zonasG=[],_map=null,_mode='hover',_clickH=null,_apil=[];
window.addEventListener('load',function(){setTimeout(init,1200);});
function init(){_map=_findMap();if(!_map)return;
  _map.eachLayer(function(l){_walk(l,function(ly){
    if(ly.feature&&ly.feature.properties){var p=ly.feature.properties;
      if(p.NOMBRE){_zidx[String(p.NOMBRE).toUpperCase()]=ly;}
      if(typeof p._rad_m!=='undefined'){_zonasG.push({nom:p.NOMBRE,lat:+p._clat,lon:+p._clon,rad:+p._rad_m,tt:p._tt,ly:ly});}
    }});});
  var rd=document.getElementById('cpResult');if(rd){rd.innerHTML=Object.keys(_zidx).length+' zonas ✓';rd.style.color='#16a34a';setTimeout(function(){rd.innerHTML='';},2500);}
  _injectModeBtn();
}
function _injectModeBtn(){try{var lc=document.querySelector('.leaflet-control-layers-list');
  if(lc&&!document.getElementById('mBtn')){var r=document.createElement('div');r.style.cssText='padding:4px 2px;';
    r.innerHTML='<button id="mBtn" onclick="toggleMode()" style="width:100%;background:#16a34a;color:white;border:none;border-radius:5px;padding:5px;cursor:pointer;">🖱️ Info: Hover</button>';
    lc.appendChild(r);}}catch(e){}}
function _resaltar(ly){if(_hl&&_os){try{_hl.setStyle(_os);}catch(e){}}
  _os={fillColor:ly.options.fillColor,fillOpacity:ly.options.fillOpacity,color:ly.options.color,weight:ly.options.weight};
  ly.setStyle({fillColor:'#2563eb',fillOpacity:0.55,color:'#2563eb',weight:3});try{ly.bringToFront();}catch(e){}
  _hl=ly;if(ly.getBounds&&_map)_map.fitBounds(ly.getBounds(),{padding:[50,50],maxZoom:14});}
window.limpiar=function(){if(_hl&&_os){try{_hl.setStyle(_os);}catch(e){}}_hl=null;_os=null;
  var i=document.getElementById('cpInput');if(i)i.value='';document.getElementById('cpResult').innerHTML='';}
window.buscar=function(v){var raw=String(v||'').trim();if(!raw){limpiar();return;}
  var k=raw.toUpperCase(),ly=_zidx[k];
  if(!ly){var c=Object.keys(_zidx).filter(function(x){return x.indexOf(k)!==-1;});if(c.length)ly=_zidx[c[0]];}
  var rd=document.getElementById('cpResult');
  if(ly){_resaltar(ly);if(rd){rd.innerHTML='✔ '+(ly.feature.properties.NOMBRE||k);rd.style.color='#16a34a';}
    _panel(ly.feature.properties._tt||'');}
  else if(rd){rd.innerHTML='✘ No encontrado';rd.style.color='#dc2626';}}
function _panel(html){var p=document.getElementById('infoPanel'),b=document.getElementById('infoBody');
  if(p&&b){b.innerHTML=html;p.style.display='block';}}
window.toggleMode=function(){var b=document.getElementById('mBtn');
  if(_mode==='hover'){_walk(_map,function(ly){if(ly.getTooltip&&ly.getTooltip())ly.unbindTooltip();});
    _clickH=function(e){_apilado(e.latlng);};_map.on('click',_clickH);_mode='click';
    if(b){b.innerHTML='👆 Info: Click';b.style.background='#2563eb';}}
  else{if(_clickH){_map.off('click',_clickH);_clickH=null;}_mode='hover';
    if(b){b.innerHTML='🖱️ Info: Hover';b.style.background='#16a34a';}}}
function _apilado(ll){var enc=[];for(var i=0;i<_zonasG.length;i++){var z=_zonasG[i];
    if(_dist(ll.lat,ll.lng,z.lat,z.lon)<=z.rad)enc.push(z);}
  _apil=enc;if(!enc.length){return;}
  if(enc.length===1){_panel(enc[0].tt);try{_resaltar(enc[0].ly);}catch(e){}}
  else{var h='<div style="font-weight:bold;color:#2563eb;margin-bottom:6px;">🔎 '+enc.length+' zonas aquí:</div>';
    for(var j=0;j<enc.length;j++)h+='<div class="api-zona-item" data-i="'+j+'">▸ <b>'+enc[j].nom+'</b></div>';_panel(h);}}
document.addEventListener('click',function(ev){var t=ev.target;while(t&&t!==document){
  if(t.classList&&t.classList.contains('api-zona-item')){var i=+t.getAttribute('data-i');
    if(_apil[i]){_panel('<div class="api-zona-item" data-volver="1">← Volver</div>'+_apil[i].tt);try{_resaltar(_apil[i].ly);}catch(e){}}return;}
  if(t.getAttribute&&t.getAttribute('data-volver')==='1'){_apilado({lat:_apil[0].lat,lng:_apil[0].lon});return;}
  t=t.parentNode;}});
</script>
"""


# ═══════════════════════════════════════════════════════════════════════
# 🖥️ UI de Streamlit — bloque completo para incrustar en la app
# ═══════════════════════════════════════════════════════════════════════
def render_analisis_real_dia(gdf_cp_wgs84=None, nodo_default="DVR2"):
    """
    Pinta el bloque completo de la capa 'Análisis Real por Día'.
    gdf_cp_wgs84: (opcional) polígonos de CP para contar puntos y elegibilidad por CP.
                  Puedes pasar st.session_state.get('gdf_cobertura_global') reproyectado a 4326.
    """
    st.markdown("## Análisis")

    c1, c2 = st.columns(2)
    with c1:
        f_seteo = st.file_uploader("1) Seteo del día (searchHubsResponse .csv)", type=["csv"], key="rd_seteo")
        f_geo = st.file_uploader("2) GeoPuntos entregas (.csv)", type=["csv"], key="rd_geo")
    with c2:
        f_raw = st.file_uploader("3) Raw Data at Tracking (.csv)", type=["csv"], key="rd_raw")
        f_elig = st.file_uploader("4) Elegibilidad / Data Aurora Dia (.xlsx)", type=["xlsx"], key="rd_elig")

    if not (f_seteo and f_geo and f_raw and f_elig):
        st.info("Sube los 4 archivos para habilitar el análisis por día.")
        return

    # Hoja de elegibilidad
    try:
        xls = pd.ExcelFile(io.BytesIO(f_elig.getvalue()))
        hoja = next((h for h in xls.sheet_names if "aurora" in h.lower()), xls.sheet_names[0])
    except Exception:
        hoja = 0

    seteo, geo, tr2emp, tr2status, elig = cargar_archivos(
        f_seteo.getvalue(), f_geo.getvalue(), f_raw.getvalue(), f_elig.getvalue(), hoja
    )

    dias = fechas_disponibles(seteo, geo)
    if not dias:
        st.warning("No hay días con seteo ENABLED y geopuntos simultáneamente.")
        return

    cc1, cc2 = st.columns([2, 1])
    with cc1:
        dia = st.selectbox("📅 Día a analizar:", dias, index=len(dias) - 1)
    with cc2:
        nodos = sorted(seteo["Station Code"].map(_norm_txt).dropna().unique()) if "Station Code" in seteo.columns else [nodo_default]
        nodo = st.selectbox("🏢 Nodo:", nodos, index=nodos.index(nodo_default) if nodo_default in nodos else 0)

    if st.button("🚀 Analizar día", type="primary", use_container_width=True):
        with st.spinner(f"Procesando {dia}…"):
            R = procesar_dia(seteo, geo, tr2emp, tr2status, elig, dia, nodo=nodo)
            hist = calcular_historico_periodo(seteo, geo, tr2emp, elig, dias, nodo=nodo)
            st.session_state["rd_R"] = R
            st.session_state["rd_hist"] = hist
            st.session_state["rd_mapa"] = construir_mapa_dia_html(R, gdf_cp_wgs84=gdf_cp_wgs84)

    R = st.session_state.get("rd_R")
    if R is None:
        return

    # KPIs
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Zonas", len(R["zonas"]))
    k2.metric("Paquetes (puntos)", R["total_puntos"])
    k3.metric("Entregados", R["total_asignados"])
    k4.metric("Traslape total", f"{R['traslape_total']}%")

    # Mapa a ancho completo (la info de cada zona se ve en el tooltip / panel
    # apilado del propio mapa, igual que la capa de cobertura — sin panel lateral).
    mapa = st.session_state.get("rd_mapa")
    if mapa:
        components.html(mapa, height=620)

    # Tablas completas debajo del mapa
    df_z = construir_tabla_zonas(R, hist_por_zona=st.session_state.get("rd_hist"))
    st.markdown("### 📋 Análisis por Zona (VR)")
    st.dataframe(df_z, use_container_width=True, hide_index=True)

    st.markdown("### 📮 Elegibilidad vs. Entregado por CP")
    df_cp = pd.DataFrame([{"CP": cp, "Elegibles (libre)": int(v)} for cp, v in sorted(R["elig_por_cp"].items())])
    st.dataframe(df_cp, use_container_width=True, hide_index=True)

    # ═══════════════════════════════════════════════════════════════════
    # 🎯 PLAN DE CONFIRMACIÓN — distribuye un volumen total objetivo entre
    #    las zonas ENABLED del día (50% histórico + 50% demanda libre).
    # ═══════════════════════════════════════════════════════════════════
    st.markdown("### 🎯 Plan de Confirmación — sugerir seteo por zona")
    st.caption("Escribe el volumen TOTAL objetivo del día; se reparte entre las zonas "
               "ENABLED según su histórico del mes (50%) y la demanda libre de su "
               "polígono (50%), ajustado por traslape. Tope por zona: 96.")

    pc1, pc2 = st.columns([1, 1])
    with pc1:
        _plan_total = st.number_input("📦 Confirmation Plan (volumen total objetivo):",
                                      min_value=0, step=10, value=0, key="rd_plan_val")
    with pc2:
        _btn_plan = st.button("🎯 Distribuir plan entre zonas", use_container_width=True, key="rd_plan_btn")

    if _btn_plan and _plan_total > 0:
        with st.spinner("Distribuyendo el plan según histórico y demanda libre…"):
            _hist_z = historico_total_por_zona(seteo, geo, tr2emp, dias)
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
            st.warning(f"La suma ({_suma}) difiere del plan ({_plan_obj}); "
                       f"posible efecto del tope 96 en varias zonas.")
        st.dataframe(_df_plan, use_container_width=True, hide_index=True)

        # Excel del plan sugerido
        _bufp = io.BytesIO()
        with pd.ExcelWriter(_bufp, engine="xlsxwriter") as _w:
            _df_plan.to_excel(_w, index=False, sheet_name="Seteo Sugerido")
        st.download_button("📥 Descargar Seteo Sugerido (Excel)", data=_bufp.getvalue(),
                           file_name=f"Seteo_Sugerido_{R['nodo']}_{dia.replace('/','-')}_plan{_plan_obj}.xlsx",
                           mime="application/vnd.ms-excel", use_container_width=True)

    # ── 📦 PAQUETES POR LLEVAR por zona = morados (no asignados por tracking)
    #    que caen DENTRO del radio de la zona → los que esa zona aún puede llevar.
    st.markdown("### 📦 Paquetes por llevar por zona")
    st.caption("Puntos morados (no asignados por tracking) que caen dentro del radio de cada zona.")
    _df_llevar, _df_det = construir_entregas_no_asignadas(R)
    st.dataframe(_df_llevar, use_container_width=True, hide_index=True, height=320)
    _buf_na = io.BytesIO()
    with pd.ExcelWriter(_buf_na, engine="xlsxwriter") as _wna:
        _df_llevar.to_excel(_wna, index=False, sheet_name="Por llevar por zona")
        _df_det.to_excel(_wna, index=False, sheet_name="Detalle (puntos morados)")
    st.download_button("📥 Descargar Paquetes por Llevar (Excel)", data=_buf_na.getvalue(),
                       file_name=f"Paquetes_por_llevar_{R['nodo']}_{dia.replace('/','-')}.xlsx",
                       mime="application/vnd.ms-excel", use_container_width=True)

    # Descargas
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
                "Día": dia, "Nodo": R["nodo"], "Zonas ENABLED": len(R["zonas"]),
                "Paquetes": R["total_puntos"], "Asignados": R["total_asignados"],
                "No asignados": R["total_no_asignados"], "Traslape total": f"{R['traslape_total']}%",
            }])
            resumen.to_excel(w, index=False, sheet_name="Resumen")
        st.download_button("📊 Descargar Análisis Excel", data=buf.getvalue(),
                           file_name=f"Analisis_Real_{R['nodo']}_{dia.replace('/','-')}.xlsx",
                           mime="application/vnd.ms-excel", use_container_width=True)
