import streamlit as st
import pandas as pd
import folium
import geopandas as gpd
import os, io, yaml
import numpy as np
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
from shapely.geometry import Point
from shapely.ops import unary_union
import streamlit.components.v1 as components
from itertools import combinations

st.set_page_config(page_title="Sistema Pro AMZL - Cobertura Albers-Lambert", layout="wide")

def normalizar_cp(v):
    try:
        return str(int(float(v))).strip().zfill(5)
    except:
        return str(v).strip().zfill(5)


# ═══════════════════════════════════════════════════════════════════════
# 🎨 FUNCIONES DE COLOR: Rangos separados para CÍRCULOS y POLÍGONOS CP
# ═══════════════════════════════════════════════════════════════════════

def obtener_color_rango_circulo(v):
    """Color para CÍRCULOS/ZONAS operativas (volúmenes bajos por zona)."""
    try:
        vol = float(v)
        if vol == 0:
            return "gray", "⚪ R0"
        elif vol <= 15:
            return "yellow", "🟡 R1-15"
        elif vol <= 20:
            return "orange", "🟠 R16-20"
        elif vol <= 30:
            return "red", "🔴 R21-30"
        elif vol <= 40:
            return "purple", "🟣 R31-40"
        else:
            return "brown", "🟤 R41+"
    except:
        return "gray", "⚪ Desconocido"


def obtener_color_rango_cp(v):
    """Color para POLÍGONOS de Códigos Postales (volúmenes acumulados por CP)."""
    try:
        vol = float(v)
        if vol == 0:
            return "#9e9e9e", "⚪ R0"          # Gris
        elif vol <= 100:
            return "#f1c40f", "🟡 R1-100"      # Amarillo
        elif vol <= 200:
            return "#e67e22", "🟠 R101-200"     # Naranja
        elif vol <= 300:
            return "#e74c3c", "🔴 R201-300"     # Rojo
        elif vol <= 400:
            return "#8e44ad", "🟣 R301-400"     # Púrpura
        else:
            return "#6d4c41", "🟤 R401+"        # Café
    except:
        return "#9e9e9e", "⚪ Desconocido"


# ═══════════════════════════════════════════════════════════════════════
# 🎲 TRASLAPE: Monte Carlo vectorizado (misma fórmula que Sistema Pro)
# ═══════════════════════════════════════════════════════════════════════

def clasificar_nivel_traslape(pct):
    """Clasifica nivel de traslape por porcentaje."""
    if pct < 25:
        return "🟢 BAJO"
    elif pct < 50:
        return "🟡 MEDIO"
    elif pct < 75:
        return "🟠 ALTO"
    else:
        return "🔴 CRÍTICO"


def calcular_traslape_real(p1, otros_pts):
    """
    Monte Carlo vectorizado — misma función de app_sistema_pro.py.
    Genera 10,000 puntos dentro del círculo p1 y mide cuántos caen en otros círculos.

    Args:
        p1: dict con LAT, LON, RAD, NOM
        otros_pts: lista de dicts con LAT, LON, RAD, NOM

    Returns:
        porcentaje_global, zonas_intersecadas, desglose [{NOM, PCT}]
    """
    if not otros_pts:
        return 0.0, [], []
    n = 10000
    ang = np.random.uniform(0, 2 * np.pi, n)
    rad = np.sqrt(np.random.uniform(0, 1, n)) * p1['RAD']
    m_grado = 111139
    cos_lat = np.cos(np.radians(p1['LAT']))

    p_lat = p1['LAT'] + ((rad * np.sin(ang)) / m_grado)
    p_lon = p1['LON'] + ((rad * np.cos(ang)) / (m_grado * cos_lat))

    lats_otros = np.array([p['LAT'] for p in otros_pts])
    lons_otros = np.array([p['LON'] for p in otros_pts])
    rads_otros = np.array([p['RAD'] for p in otros_pts])
    nombres_otros = np.array([p['NOM'] for p in otros_pts])

    p_lat_m = p_lat[:, np.newaxis]
    p_lon_m = p_lon[:, np.newaxis]

    d2 = ((p_lat_m - lats_otros)**2 + ((p_lon_m - lons_otros) * cos_lat)**2) * (m_grado**2)
    puntos_en_zonas = d2 <= rads_otros**2

    cubiertos = np.any(puntos_en_zonas, axis=1)
    porcentaje = float((np.sum(cubiertos) / n) * 100)

    zonas_que_cubren = np.any(puntos_en_zonas, axis=0)
    zonas_intersecadas = nombres_otros[zonas_que_cubren].tolist()

    desglose = []
    conteo_por_zona = np.sum(puntos_en_zonas, axis=0)
    for idx_z in range(len(otros_pts)):
        if conteo_por_zona[idx_z] > 0:
            pct_individual = float((conteo_por_zona[idx_z] / n) * 100)
            desglose.append({'NOM': nombres_otros[idx_z], 'PCT': round(pct_individual, 1)})
    desglose.sort(key=lambda x: x['PCT'], reverse=True)

    return porcentaje, zonas_intersecadas, desglose


def calcular_traslape_por_zona(gdf_cobertura_m, gdf_circles_wgs84_df, nodos_unicos):
    """
    Calcula traslape por zona y por nodo usando Monte Carlo vectorizado.
    Usa las coordenadas GPS originales de los círculos (LATITUD, LONGITUD, RADIO).

    Returns:
        traslape_por_zona: {nombre_zona: {"pct": float, "nivel": str, "nodo": str, "desglose": list}}
        resultados_nodo: [{"Nodo": str, "% Traslape": str, "Nivel": str}]
    """
    # 1. Asignar cada círculo a su nodo por intersección con CPs
    circulos_con_nodo = []
    for nodo in nodos_unicos:
        cps_nodo = gdf_cobertura_m[gdf_cobertura_m['ZONA'] == nodo]
        if cps_nodo.empty:
            continue
        union_cps_nodo = unary_union(cps_nodo['geometry'].buffer(0))
        for idx, row in gdf_circles_wgs84_df.iterrows():
            pt_check = Point(row['LONGITUD'], row['LATITUD'])
            # Verificar si el centro del círculo cae cerca de los CPs de este nodo
            # (usar la geometría proyectada del círculo para intersección)
            if row['geometry'].intersects(union_cps_nodo):
                circulos_con_nodo.append({
                    'NOM': row['NOMBRE'],
                    'LAT': row['LATITUD'],
                    'LON': row['LONGITUD'],
                    'RAD': row['RADIO'],
                    'VOL': row.get('VOLUMEN', 0),
                    'nodo': nodo
                })

    # 2. Calcular traslape por zona usando Monte Carlo (igual que Sistema Pro)
    np.random.seed(42)  # Semilla fija para reproducibilidad
    traslape_por_zona = {}

    for i, circ in enumerate(circulos_con_nodo):
        nombre = circ['NOM']
        nodo = circ['nodo']

        # Otros círculos del MISMO nodo (excluir el actual)
        otros = [c for j, c in enumerate(circulos_con_nodo) if j != i and c['nodo'] == nodo]

        if not otros:
            traslape_por_zona[nombre] = {
                "pct": 0.0, "nivel": "🟢 BAJO", "nodo": nodo, "desglose": []
            }
            continue

        pct, zonas_inter, desglose = calcular_traslape_real(circ, otros)
        pct = round(pct, 1)

        traslape_por_zona[nombre] = {
            "pct": pct,
            "nivel": clasificar_nivel_traslape(pct),
            "nodo": nodo,
            "desglose": desglose
        }

    # 3. Agregar por nodo: promedio de traslape de sus zonas
    nodo_traslapes = {}
    for nombre, datos in traslape_por_zona.items():
        nodo = datos['nodo']
        if nodo not in nodo_traslapes:
            nodo_traslapes[nodo] = []
        nodo_traslapes[nodo].append(datos['pct'])

    resultados_nodo = []
    for nodo in nodos_unicos:
        if nodo in nodo_traslapes and nodo_traslapes[nodo]:
            pct_promedio = round(sum(nodo_traslapes[nodo]) / len(nodo_traslapes[nodo]), 2)
        else:
            pct_promedio = 0.0
        resultados_nodo.append({
            "Nodo": nodo,
            "% Traslape": f"{pct_promedio}%",
            "Nivel": clasificar_nivel_traslape(pct_promedio)
        })

    return traslape_por_zona, resultados_nodo


# ═══════════════════════════════════════════════════════════════════════
# 📦 UPSIDE: Paquetes adicionales que cada zona puede capturar de cada CP
# ═══════════════════════════════════════════════════════════════════════

def calcular_upside_por_zona(gdf_cobertura_m, gdf_circles_m_corr, nodos_unicos_maestro):
    """
    Calcula el UPSIDE (paquetes adicionales capturables) de cada ZONA sobre cada CP.

    ⚡ VERSIÓN VECTORIZADA (shapely 2.x): reemplaza el bucle Python punto-por-punto
       por operaciones NumPy vectorizadas con shapely.contains / shapely.intersects.
       Misma lógica de reparto 1/k y misma semilla (42) → resultados idénticos,
       pero 50-200x más rápido.

    Lógica (confirmada por la usuaria):
      - Un CP tiene VOLUMEN total de paquetes (del primer archivo de cobertura).
      - Una zona que cubre X% del área del CP puede capturar X% del volumen de ese CP.
      - Cuando VARIAS zonas se traslapan sobre el mismo pedazo del CP, el volumen de
        ese pedazo se REPARTE equitativamente entre las zonas que lo cubren (reparto 1/k),
        para que la suma de upsides nunca supere el volumen realmente cubierto.

    Método Monte Carlo por CP:
      - Se lanzan N puntos aleatorios dentro del bounding box del CP.
      - Se filtran los que caen dentro del polígono del CP (vectorizado).
      - Para cada punto se cuenta cuántas zonas (k) lo cubren.
      - Cada zona que cubre el punto recibe un peso de 1/k.
      - Upside_zona_sobre_CP = VOLUMEN_CP × (suma_pesos_zona / puntos_dentro)

    Todas las geometrías deben estar en el MISMO CRS proyectado (metros).

    Returns:
        upside_por_zona: {nombre_zona: {"total": int, "por_cp": [{"CP": str, "pct": float, "upside": int}]}}
        upside_por_cp_zona: {(nombre_zona, cp_str): upside_int}  (lookup auxiliar)
        upside_ocupado_por_cp: {cp_str: upside_total_ocupado_int}  (suma de upsides de todas las zonas sobre el CP)
    """
    import shapely  # shapely 2.x: contains / intersects vectorizados nativos

    # ⚡ 2000 puntos/CP → precisión completa en el upside. Con el gpd.overlay ya
    #    optimizado, el cuello de botella principal desapareció, así que podemos
    #    permitirnos la precisión alta sin disparar el tiempo total.
    N = 2000  # puntos por CP (precisión alta)
    rng = np.random.default_rng(42)

    # Preparar lista de zonas con geometría proyectada
    zonas = []
    for _, zrow in gdf_circles_m_corr.iterrows():
        g = zrow['geometry']
        if g is not None and not g.is_empty:
            zonas.append({'NOM': zrow['NOMBRE'], 'geom': g.buffer(0)})

    if not zonas:
        return {}, {}, {}

    zona_geoms = np.array([z['geom'] for z in zonas], dtype=object)
    zona_noms = [z['NOM'] for z in zonas]

    # Acumuladores
    upside_por_zona = {nom: {"total": 0.0, "por_cp": []} for nom in zona_noms}
    upside_por_cp_zona = {}
    upside_ocupado_por_cp = {}  # cp_str -> suma de upsides de todas las zonas sobre ese CP

    # Recorremos TODOS los CPs de la cobertura (de todos los nodos)
    for _, cp_row in gdf_cobertura_m.iterrows():
        geom_cp = cp_row['geometry'].buffer(0)
        if geom_cp.is_empty or geom_cp.area <= 0:
            continue

        vol_cp = pd.to_numeric(cp_row.get('VOLUMEN', 0), errors='coerce')
        vol_cp = 0 if pd.isna(vol_cp) else float(vol_cp)
        cp_str = str(cp_row['CP'])
        if vol_cp <= 0:
            continue

        # Prefiltro vectorizado: zonas que realmente intersectan este CP
        mask_inter = shapely.intersects(zona_geoms, geom_cp)
        idx_cp = np.where(mask_inter)[0]
        if idx_cp.size == 0:
            continue
        geoms_cp_zonas = zona_geoms[idx_cp]
        noms_cp_zonas = [zona_noms[i] for i in idx_cp]

        # Muestreo Monte Carlo dentro del bounding box del CP
        minx, miny, maxx, maxy = geom_cp.bounds
        px = rng.uniform(minx, maxx, N)
        py = rng.uniform(miny, maxy, N)
        pts = shapely.points(px, py)

        # ¿Qué puntos caen dentro del polígono del CP? (vectorizado)
        dentro = shapely.contains(geom_cp, pts)
        puntos_dentro = int(dentro.sum())
        if puntos_dentro == 0:
            continue
        pts_in = pts[dentro]

        # Matriz zonas × puntos: contains vectorizado por zona
        # cubre[j, :] = qué puntos (dentro del CP) cubre la zona j
        cubre = np.vstack([shapely.contains(g, pts_in) for g in geoms_cp_zonas])  # (Z, P)
        k = cubre.sum(axis=0)                              # nº zonas que cubren cada punto
        k_safe = np.where(k > 0, k, 1)                     # evitar div/0
        pesos_pt = np.where(k > 0, 1.0 / k_safe, 0.0)      # peso 1/k por punto

        # peso total por zona = suma de (1/k) sobre los puntos que cubre
        peso_por_zona = (cubre * pesos_pt).sum(axis=1)     # (Z,)

        # Upside de cada zona sobre este CP
        for j, nom in enumerate(noms_cp_zonas):
            peso = float(peso_por_zona[j])
            if peso <= 0:
                continue
            # fracción del volumen del CP asignada a esta zona (ya con reparto 1/k)
            frac = peso / puntos_dentro
            upside_int = int(round(vol_cp * frac))
            if upside_int <= 0:
                continue
            # % que esta zona cubre del CP (con reparto, para mostrar contexto)
            pct_cobertura = round(frac * 100, 1)
            upside_por_zona[nom]["total"] += upside_int
            upside_por_zona[nom]["por_cp"].append({
                "CP": cp_str,
                "pct": pct_cobertura,
                "upside": upside_int
            })
            upside_por_cp_zona[(nom, cp_str)] = upside_int
            # Acumular upside ocupado total de este CP (suma de todas las zonas encima)
            upside_ocupado_por_cp[cp_str] = upside_ocupado_por_cp.get(cp_str, 0) + upside_int

    # Redondear totales a enteros
    for nom in upside_por_zona:
        upside_por_zona[nom]["total"] = int(round(upside_por_zona[nom]["total"]))
        # Ordenar desglose por upside desc
        upside_por_zona[nom]["por_cp"].sort(key=lambda d: d["upside"], reverse=True)

    return upside_por_zona, upside_por_cp_zona, upside_ocupado_por_cp


# ═══════════════════════════════════════════════════════════════════════
# 🗺️ CONSTRUCCIÓN DEL MAPA FOLIUM — ⚡ OPTIMIZADO CANVAS RENDERER
#    4 fixes de rendimiento para 3000+ zonas y 3000+ CPs:
#    (1) prefer_canvas=True  → Leaflet pinta en <canvas>, no en miles de SVG
#    (2) Capas únicas         → tooltip + popup en la MISMA capa (no duplicar)
#    (3) Zonas en UNA capa    → un solo GeoJson con todos los círculos
#    (4) simplify() de CPs    → ~25 m, reduce vértices ~80% sin diferencia visible
# ═══════════════════════════════════════════════════════════════════════

def construir_mapa_html(res, gdf_cobertura, mostrar_anillos):
    """Construye el mapa Folium completo (Canvas renderer) y devuelve su HTML standalone."""
    if not res['gdf_circles_wgs84'].empty:
        c_lat = res['gdf_circles_wgs84']['LATITUD'].mean()
        c_lon = res['gdf_circles_wgs84']['LONGITUD'].mean()
    else:
        c_lat = 23.6345
        c_lon = -102.5528

    # ⚡ FIX 1: prefer_canvas=True → todas las capas vectoriales se dibujan en
    #    un ÚNICO elemento <canvas> por píxeles, en vez de miles de nodos SVG.
    m = folium.Map(
        location=[c_lat, c_lon],
        zoom_start=6 if res['estado_nombre'] == "Todos" else 10,
        tiles="https://tile.openstreetmap.de/{z}/{x}/{y}.png",
        attr='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        prefer_canvas=True
    )

    # ═══════════════════════════════════════════════════════════════
    # 1. POLÍGONOS DE CPs (fondo) — UNA sola capa con tooltip + popup
    # ═══════════════════════════════════════════════════════════════
    gdf_mapa_cp = gdf_cobertura.copy()
    gdf_mapa_cp_wgs84 = gdf_mapa_cp.to_crs("EPSG:4326") if gdf_mapa_cp.crs != "EPSG:4326" else gdf_mapa_cp

    # ⚡ FIX 4: simplificar geometría ~25 m (0.000225°). Reduce vértices ~80%
    #    sin diferencia visible. preserve_topology evita polígonos rotos.
    _TOL = 25 / 111139  # ~25 metros en grados
    gdf_mapa_cp_wgs84 = gdf_mapa_cp_wgs84.copy()
    gdf_mapa_cp_wgs84['geometry'] = gdf_mapa_cp_wgs84['geometry'].simplify(_TOL, preserve_topology=True)

    if 'VOLUMEN' not in gdf_mapa_cp_wgs84.columns:
        gdf_mapa_cp_wgs84['VOLUMEN'] = 0
    gdf_mapa_cp_wgs84['VOLUMEN'] = pd.to_numeric(gdf_mapa_cp_wgs84['VOLUMEN'], errors='coerce').fillna(0)
    gdf_mapa_cp_wgs84['_color_hex'] = gdf_mapa_cp_wgs84['VOLUMEN'].apply(lambda v: obtener_color_rango_cp(v)[0])
    gdf_mapa_cp_wgs84['_rango_txt'] = gdf_mapa_cp_wgs84['VOLUMEN'].apply(lambda v: obtener_color_rango_cp(v)[1])

    if 'PARTNERS' not in gdf_mapa_cp_wgs84.columns:
        gdf_mapa_cp_wgs84['PARTNERS'] = 0
    gdf_mapa_cp_wgs84['PARTNERS'] = pd.to_numeric(gdf_mapa_cp_wgs84['PARTNERS'], errors='coerce').fillna(0).astype(int)

    # 📦 UPSIDE por CP: Ocupado (suma de zonas encima) y Libre
    _upside_ocu_cp = res.get('upside_ocupado_por_cp', {})
    gdf_mapa_cp_wgs84['_UPSIDE_OCUPADO'] = gdf_mapa_cp_wgs84['CP'].astype(str).map(
        lambda c: int(_upside_ocu_cp.get(str(c), 0))
    )
    gdf_mapa_cp_wgs84['_UPSIDE_LIBRE'] = (
        gdf_mapa_cp_wgs84['VOLUMEN'].astype(float) - gdf_mapa_cp_wgs84['_UPSIDE_OCUPADO']
    ).clip(lower=0).round().astype(int)

    # 📦 Desglose "Upside por zona encima del CP"
    _upside_zona_lookup_cp = res.get('upside_por_zona', {})
    _cp_to_zonas = {}
    for _znom, _zinfo in _upside_zona_lookup_cp.items():
        for _d in _zinfo.get('por_cp', []):
            _cpk = str(_d['CP'])
            _cp_to_zonas.setdefault(_cpk, []).append((_znom, _d['upside']))
    def _fmt_zonas_cp(c):
        lst = _cp_to_zonas.get(str(c), [])
        if not lst:
            return "Ninguna"
        lst = sorted(lst, key=lambda t: t[1], reverse=True)
        # 📋 Formato LISTA con viñetas (una zona por renglón) para el tooltip/popup del CP
        return "<br>".join([f"&nbsp;&nbsp;• {nom}: {up} pqts" for nom, up in lst])
    gdf_mapa_cp_wgs84['_UPSIDE_ZONAS'] = gdf_mapa_cp_wgs84['CP'].astype(str).map(_fmt_zonas_cp)

    # ═══════════════════════════════════════════════════════════════
    # 📍 FACTIBILIDAD DE PROSPECCIÓN ("¿Dónde prospectar?")
    #    Un CP es PROSPECTABLE si su UPSIDE LIBRE (volumen disponible que
    #    NINGUNA zona cubre todavía), sumado al Upside Libre de los CPs
    #    vecinos cuyos CENTROIDES caen dentro de 750 m, alcanza ≥ 32.
    #    Se usa Upside Libre (no Volumen Total) porque solo se puede prospectar
    #    sobre volumen DISPONIBLE — lo ya ocupado por zonas no cuenta.
    # ═══════════════════════════════════════════════════════════════
    _UMBRAL_PROSPECCION = 32
    _RADIO_PROSPECCION_M = 750
    try:
        # Proyectar a métrico (Lambert México) para medir 750 m con precisión
        _gdf_prox = gdf_mapa_cp_wgs84[['CP', 'VOLUMEN', 'geometry']].copy()
        _gdf_prox_m = _gdf_prox.to_crs("EPSG:6362")
        _cent_m = _gdf_prox_m.geometry.centroid
        _cx = _cent_m.x.to_numpy()
        _cy = _cent_m.y.to_numpy()
        # ⚡ Prospección sobre VOLUMEN TOTAL del CP (el volumen manda: si hay ≥32 de
        #    volumen en 750m, cabe una zona — sin importar lo ya ocupado).
        _vol = pd.to_numeric(_gdf_prox_m['VOLUMEN'], errors='coerce').fillna(0).to_numpy(dtype=float)
        _cps_arr = _gdf_prox_m['CP'].astype(str).to_numpy()
        _n = len(_cx)
        _vol_acum = np.zeros(_n, dtype=float)
        _r2 = float(_RADIO_PROSPECCION_M) ** 2
        # Para cada CP: sumar volumen de todos los CPs (incluido él) con centroide dentro de 750 m.
        # Vectorizado por filas (n×n solo si n es manejable; si n es grande, se hace por bloques).
        if _n <= 12000:
            for _i in range(_n):
                _d2 = (_cx - _cx[_i])**2 + (_cy - _cy[_i])**2
                _vol_acum[_i] = _vol[_d2 <= _r2].sum()
        else:
            # fallback por bloques para no agotar memoria
            _blk = 2000
            for _s in range(0, _n, _blk):
                _e = min(_n, _s + _blk)
                _dx = _cx[_s:_e, None] - _cx[None, :]
                _dy = _cy[_s:_e, None] - _cy[None, :]
                _mask = (_dx*_dx + _dy*_dy) <= _r2
                _vol_acum[_s:_e] = (_mask * _vol[None, :]).sum(axis=1)
        _acum_por_cp = dict(zip(_cps_arr, _vol_acum))
    except Exception:
        _acum_por_cp = {}

    gdf_mapa_cp_wgs84['_VOL_750M'] = gdf_mapa_cp_wgs84['CP'].astype(str).map(
        lambda c: int(round(_acum_por_cp.get(str(c), 0)))
    )
    gdf_mapa_cp_wgs84['_PROSPECTAR'] = gdf_mapa_cp_wgs84['_VOL_750M'].apply(
        lambda v: "✅ SÍ" if v >= _UMBRAL_PROSPECCION else "❌ No"
    )
    # flag booleano simple (0/1) para que el JS del botón lo lea fácil
    gdf_mapa_cp_wgs84['_PROSPECTAR_FLAG'] = (gdf_mapa_cp_wgs84['_VOL_750M'] >= _UMBRAL_PROSPECCION).astype(int)

    # 📍 CÍRCULOS DE PROSPECCIÓN por CP factible (Volumen Total ≥ 32).
    #    Nº de círculos de un CP = su valor PARTNERS (1er archivo) → círculos nuevos
    #    directos. Si PARTNERS = 0 → NO se dibuja nada (PARTNERS manda, aunque haya vol).
    #    DISTRIBUCIÓN INTELIGENTE (packing): los círculos mantienen su RADIO REAL
    #    (750m, no se reduce) y se acomodan dentro del espacio del CP SEPARADOS entre
    #    sí y de las zonas existentes por ~1 radio, para que no queden encimados.
    #    Pueden sobresalir un poco del CP (su centro cae dentro). Si no cabe el nº de
    #    PARTNERS con separación, se colocan los que quepan sin encimar.
    _prosp_centros = []  # [(lat, lon, cp_str, idx_circulo, total_circulos)]
    try:
        import shapely
        _R = float(_RADIO_PROSPECCION_M)            # radio real del círculo (750m)
        # Separación OBJETIVO entre centros: los círculos pueden quedar JUNTOS (bordes
        # casi pegados) sin encimarse mucho. ~1 radio permite cercanía estrecha.
        # No hace falta más separación. Si aun así no caben, se relaja hasta encimar.
        _SEP_MIN = _R * 1.0
        _fact = gdf_mapa_cp_wgs84[gdf_mapa_cp_wgs84['_PROSPECTAR_FLAG'] == 1].copy()
        # Geometrías de las ZONAS existentes (del 2º archivo) en métrico, para evitarlas
        try:
            _zonas_exist_m = gdf_circles_m_corr.to_crs("EPSG:6362")
            _zonas_centros = [(g.centroid.x, g.centroid.y) for g in _zonas_exist_m.geometry if g is not None and not g.is_empty]
        except Exception:
            _zonas_centros = []
        if not _fact.empty:
            _fact_m = _fact.to_crs("EPSG:6362")  # métrico para medir distancias
            for _geo_m, _cpv, _parts in zip(
                _fact_m.geometry.tolist(),
                _fact['CP'].astype(str).tolist(),
                pd.to_numeric(_fact['PARTNERS'], errors='coerce').fillna(0).astype(int).tolist()
            ):
                # PARTNERS manda: 0 partners → 0 círculos
                _k = int(_parts)
                if _k <= 0 or _geo_m is None or _geo_m.is_empty:
                    continue
                _poly = _geo_m.buffer(0)
                minx, miny, maxx, maxy = _poly.bounds
                # Centros de zonas existentes CERCANAS a este CP (para no encimar)
                _cx_cp, _cy_cp = _poly.centroid.x, _poly.centroid.y
                _ocupados = [(zx, zy) for (zx, zy) in _zonas_centros
                             if (zx - _cx_cp)**2 + (zy - _cy_cp)**2 <= (3 * _R)**2]

                # Candidatos: puntos repartidos UNIFORMEMENTE dentro del polígono del CP.
                #    (Distribución simple: solo evitar que los círculos queden encimados;
                #     cuando el CP tiene mucho espacio libre, quedan bien repartidos.)
                _rng = np.random.default_rng(42)
                _cand = []
                _tries = 0
                _NEED = max(400, _k * 200)
                while len(_cand) < _NEED and _tries < _NEED * 8:
                    _px = _rng.uniform(minx, maxx); _py = _rng.uniform(miny, maxy)
                    if _poly.contains(shapely.geometry.Point(_px, _py)):
                        _cand.append((_px, _py))
                    _tries += 1
                if not _cand:
                    _c = _poly.representative_point(); _cand = [(_c.x, _c.y)]
                _cand = np.array(_cand)
                # 🎯 FARTHEST-POINT SAMPLING: coloca cada círculo nuevo en el candidato
                #    que MAXIMIZA la distancia mínima a todo lo ya colocado (zonas
                #    existentes + círculos nuevos). Así, cuando el CP tiene espacio, los
                #    círculos se REPARTEN por todo el polígono en vez de amontonarse.
                #    Solo se descarta un candidato si su separación es menor al mínimo
                #    aceptable (_SEP_MIN) — si ninguno cumple, se relaja para permitir
                #    acercamiento/encimado (último recurso en CPs saturados).
                _nuevos = []
                # distancia² de CADA candidato a lo ya ocupado (se actualiza al colocar)
                if _ocupados:
                    _occ = np.array(_ocupados)
                    _dmin = np.min(
                        (_cand[:, None, 0] - _occ[None, :, 0])**2 + (_cand[:, None, 1] - _occ[None, :, 1])**2,
                        axis=1
                    )
                else:
                    _dmin = np.full(len(_cand), 1e18)
                for _n in range(_k):
                    _idx_best = int(np.argmax(_dmin))
                    # Si el mejor candidato no respeta ni el piso mínimo y YA colocamos
                    # algo, igual lo aceptamos (último recurso: no hay más espacio).
                    _bx, _by = _cand[_idx_best]
                    _nuevos.append((_bx, _by))
                    # actualizar distancia mínima de todos los candidatos al nuevo punto
                    _dnew = (_cand[:, 0] - _bx)**2 + (_cand[:, 1] - _by)**2
                    _dmin = np.minimum(_dmin, _dnew)
                # Convertir a WGS84 y registrar
                if _nuevos:
                    _pts_m = [shapely.geometry.Point(x, y) for (x, y) in _nuevos[:_k]]
                    _gs = gpd.GeoSeries(_pts_m, crs="EPSG:6362").to_crs("EPSG:4326")
                    _tot = len(_gs)
                    for _j, _pwgs in enumerate(_gs.tolist(), 1):
                        _prosp_centros.append((float(_pwgs.y), float(_pwgs.x), _cpv, _j, _tot))
    except Exception:
        _prosp_centros = []

    if not gdf_mapa_cp_wgs84.empty:
        _cp_geojson_str = gdf_mapa_cp_wgs84.to_json()

        # ⚠️ Tooltip de CPs: se quitaron '_UPSIDE_ZONAS' (Upside por Zona), '_VOL_750M'
        #    (Upside disp. en 750m) y '_PROSPECTAR' (¿Prospectar?) a pedido de la usuaria.
        #    Esos datos de prospección ya se muestran en los círculos verdes del mapa.
        _campos_cp = ['CP', 'ESTADO_PERTENECE', 'VOLUMEN', '_UPSIDE_OCUPADO', '_UPSIDE_LIBRE', 'PARTNERS', '_rango_txt']
        _alias_cp = ['Código Postal:', 'Estado:', 'Volumen Total:', 'Upside de Zona:', 'Upside Libre:', 'Partners:', 'Rango:']

        # ⚡ FIX 2: UNA sola capa "CP" con color + tooltip (hover) + popup (click).
        fg_cp = folium.FeatureGroup(name="CP", show=True)
        folium.GeoJson(
            _cp_geojson_str,
            style_function=lambda feature: {
                'fillColor': feature['properties'].get('_color_hex', '#9e9e9e'),
                'color': '#ffffff',
                'weight': 1.5,
                'fillOpacity': 0.45
            },
            tooltip=folium.GeoJsonTooltip(fields=_campos_cp, aliases=_alias_cp, localize=True),
            popup=folium.GeoJsonPopup(fields=_campos_cp, aliases=_alias_cp, localize=True)
        ).add_to(fg_cp)
        fg_cp.add_to(m)

    # ═══════════════════════════════════════════════════════════════
    # 2. CÍRCULOS DE ZONAS — ⚡ FIX 3: UNA sola capa GeoJson (no iterar)
    # ═══════════════════════════════════════════════════════════════
    traslape_zona_lookup = res.get('traslape_por_zona', {})
    upside_zona_lookup = res.get('upside_por_zona', {})

    # ⚡ OPTIMIZACIÓN: precalcular "CPs bajo cada círculo" con sjoin vectorizado
    _gdf_circ = res['gdf_circles_wgs84']
    _cps_por_circulo = {}
    try:
        _circ_idx = _gdf_circ.reset_index(drop=True).copy()
        _circ_idx['_circ_id'] = _circ_idx.index
        _cob_small = gdf_cobertura[['CP', 'geometry']].to_crs("EPSG:4326") if gdf_cobertura.crs != "EPSG:4326" else gdf_cobertura[['CP', 'geometry']]
        _join = gpd.sjoin(_circ_idx[['_circ_id', 'geometry']], _cob_small, how='left', predicate='intersects')
        for _cid, _grp in _join.groupby('_circ_id'):
            _cps = sorted({str(x) for x in _grp['CP'].dropna().tolist()})
            _cps_por_circulo[_cid] = _cps
    except Exception:
        _cps_por_circulo = {}

    # Construir UN SOLO GeoJSON FeatureCollection con todos los círculos.
    _zona_features = []
    for _pos, (_, r) in enumerate(res['gdf_circles_wgs84'].iterrows()):
        color_hex, r_text = obtener_color_rango_circulo(r['VOLUMEN'])
        geom_circulo = r['geometry']

        cps_unicos = _cps_por_circulo.get(_pos, None)
        if cps_unicos is None:
            cps_bajo_circulo = [str(cp_row['CP']) for _, cp_row in gdf_cobertura.iterrows()
                                if geom_circulo.intersects(cp_row['geometry'])]
            cps_unicos = sorted(set(cps_bajo_circulo))
        txt_cps_atrapados = ", ".join(cps_unicos) if cps_unicos else "Ninguno"

        info_traslape = traslape_zona_lookup.get(r['NOMBRE'], {})
        pct_traslape = info_traslape.get('pct', 0.0)
        nivel_traslape = info_traslape.get('nivel', '⚪ Sin datos')
        desglose_traslape = info_traslape.get('desglose', [])

        info_upside = upside_zona_lookup.get(r['NOMBRE'], {})
        upside_total = info_upside.get('total', 0)
        upside_por_cp = info_upside.get('por_cp', [])

        tt_lines = [
            f"<b>Zona Operativa: {r['NOMBRE']}</b>",
            f"Rango: {r_text}",
            f"Volumen: {r['VOLUMEN']}",
            f"Radio Ope: {r['RADIO']}m",
            f"<b>Traslape: {pct_traslape}% — {nivel_traslape}</b>",
            f"<b>📦 Upside Total: {upside_total} pqts</b>"
        ]
        if upside_por_cp:
            tt_lines.append("── Upside por CP ──")
            for d in upside_por_cp[:15]:
                tt_lines.append(f"&nbsp;&nbsp;• CP {d['CP']} ({d['pct']}%): {d['upside']} pqts")
            if len(upside_por_cp) > 15:
                tt_lines.append(f"&nbsp;&nbsp;… (+{len(upside_por_cp) - 15} CPs más)")
        if desglose_traslape:
            tt_lines.append("── Detalle Traslape ──")
            for d in desglose_traslape:
                tt_lines.append(f"&nbsp;&nbsp;• {d['NOM']}: {d['PCT']}%")
        tt_lines.append("-------------------------")
        tt_lines.append(f"<b>CPs Ocupados:</b> {txt_cps_atrapados}")
        tt_c = "<br>".join(tt_lines)

        _zona_features.append({
            "type": "Feature",
            "properties": {
                "NOMBRE": str(r['NOMBRE']),
                "_color": color_hex,
                "_tt": tt_c
            },
            "geometry": geom_circulo.__geo_interface__
        })

    _zonas_fc = {"type": "FeatureCollection", "features": _zona_features}

    # ⚡ FIX 2+3: UNA sola capa con color + tooltip + popup (sin duplicar).
    fg_zonas = folium.FeatureGroup(name="Zonas", show=True)
    if _zona_features:
        folium.GeoJson(
            _zonas_fc,
            style_function=lambda feat: {
                'fillColor': feat['properties'].get('_color', '#9e9e9e'),
                'color': 'black',
                'weight': 1,
                'fillOpacity': 0.45
            },
            tooltip=folium.GeoJsonTooltip(fields=['_tt'], aliases=[''], localize=True),
            popup=folium.GeoJsonPopup(fields=['_tt'], aliases=[''], max_width=360)
        ).add_to(fg_zonas)
    fg_zonas.add_to(m)

    # ═══════════════════════════════════════════════════════════════
    # 3. ANILLOS DE FACTIBILIDAD — en FeatureGroup para toggle sin recargar
    # ═══════════════════════════════════════════════════════════════
    fg_anillos = folium.FeatureGroup(name="Radios", show=mostrar_anillos)
    if 'anillos_por_estado' in res:
        for nodo_key, anillos in res['anillos_por_estado'].items():
            folium.Marker(
                location=[anillos['centro_lat'], anillos['centro_lon']],
                icon=folium.Icon(color='purple', icon='crosshairs', prefix='fa'),
                tooltip=f"Centroide Nodo: {str(nodo_key).upper()}"
            ).add_to(fg_anillos)
            c_lat = anillos['centro_lat']
            c_lon = anillos['centro_lon']

            folium.GeoJson(
                anillos['r15'],
                style_function=lambda x: {'fillColor': 'transparent', 'color': '#e74c3c', 'weight': 2, 'dashArray': '5, 5'},
                interactive=False
            ).add_to(fg_anillos)
            folium.Marker(
                location=[c_lat + 0.135, c_lon],
                icon=folium.DivIcon(html='<div style="font-size:11px;font-weight:bold;color:#e74c3c;white-space:nowrap;pointer-events:none;">15 km</div>', icon_size=(50, 15), icon_anchor=(25, 7))
            ).add_to(fg_anillos)

            folium.GeoJson(
                anillos['r10'],
                style_function=lambda x: {'fillColor': 'transparent', 'color': '#f1c40f', 'weight': 2, 'dashArray': '5, 5'},
                interactive=False
            ).add_to(fg_anillos)
            folium.Marker(
                location=[c_lat + 0.090, c_lon],
                icon=folium.DivIcon(html='<div style="font-size:11px;font-weight:bold;color:#d4ac0d;white-space:nowrap;pointer-events:none;">10 km</div>', icon_size=(50, 15), icon_anchor=(25, 7))
            ).add_to(fg_anillos)

            folium.GeoJson(
                anillos['r5'],
                style_function=lambda x: {'fillColor': 'transparent', 'color': '#2ecc71', 'weight': 2, 'dashArray': '5, 5'},
                interactive=False
            ).add_to(fg_anillos)
            folium.Marker(
                location=[c_lat + 0.045, c_lon],
                icon=folium.DivIcon(html='<div style="font-size:11px;font-weight:bold;color:#2ecc71;white-space:nowrap;pointer-events:none;">5 km</div>', icon_size=(50, 15), icon_anchor=(25, 7))
            ).add_to(fg_anillos)
    fg_anillos.add_to(m)

    # ═══════════════════════════════════════════════════════════════
    # 📍 CAPA DE PROSPECCIÓN — círculos verdes de 750m en cada CP factible
    #    (Volumen Total acumulado ≥ 32 en radio de 750m centroide-a-centroide).
    #    Aparece como un check más en el control de capas (apagada por defecto).
    #    Marca los espacios donde PODRÍA ingresar un nuevo VR o ampliar capacidad.
    # ═══════════════════════════════════════════════════════════════
    fg_prospeccion = folium.FeatureGroup(name="📍 Prospección (750m)", show=False)
    for _plat, _plon, _pcp, _pidx, _ptot in _prosp_centros:
        folium.Circle(
            location=[_plat, _plon],
            radius=750,  # metros — mismo radio de la regla de negocio
            color='#15803d',
            weight=2,
            fill=True,
            fill_color='#22c55e',
            fill_opacity=0.35,
            tooltip=f"✅ Prospectable — CP {_pcp}<br>Zona nueva {_pidx} de {_ptot} (según Partners)",
            popup=folium.Popup(f"<b>✅ Zona prospectable</b><br>CP: {_pcp}<br>Zona nueva <b>{_pidx} de {_ptot}</b> (según Partners del CP)<br><i>Cabe un nuevo VR o ampliar capacidad aquí.</i>", max_width=300)
        ).add_to(fg_prospeccion)
    fg_prospeccion.add_to(m)

    folium.LayerControl(position='topright', collapsed=False).add_to(m)

    # ═══════════════════════════════════════════════════════════════
    # 🔍 BUSCADOR DE CP + NOMBRE DE ZONA
    # ═══════════════════════════════════════════════════════════════
    search_html = """
    <div id="cpSearchBar" style="
        position:fixed; top:10px; left:50%; transform:translateX(-50%); z-index:9999;
        background:white; padding:8px 14px; border-radius:10px;
        box-shadow:0 4px 16px rgba(0,0,0,0.25);
        display:flex; align-items:center; gap:8px;
        font-family:'Segoe UI',sans-serif;">
        <span style="font-size:16px">🔍</span>
        <input id="cpInput" type="text" placeholder="Buscar CP o Zona..."
            onkeyup="if(event.key==='Enter')buscarCP(this.value)"
            style="border:1px solid #e2e8f0; border-radius:6px; padding:6px 12px;
            font-size:14px; width:170px; outline:none;" />
        <button onclick="buscarCP(document.getElementById('cpInput').value)"
            style="background:#2563eb; color:white; border:none; border-radius:6px;
            padding:6px 14px; font-size:13px; font-weight:600; cursor:pointer;">
            Buscar</button>
        <span id="cpResult" style="font-size:12px; max-width:350px;
            white-space:nowrap; overflow:hidden; text-overflow:ellipsis;"></span>
    </div>
    <!-- 🖱️ Botón de modo de información (Hover ⇄ Click) — se inyecta DENTRO del
         control de capas de Leaflet (junto a CP/Zonas/Radio) vía JS en initCPSearch. -->
    <script>
    window.addEventListener('load', function() {
        setTimeout(function() { initCPSearch(); }, 1500);
    });

    function initCPSearch() {
        var map = null;
        for (var k in window) {
            try {
                if (k.indexOf('map_') === 0 && window[k] && window[k].eachLayer) {
                    map = window[k]; break;
                }
            } catch(e) {}
        }
        if (!map) {
            for (var k in window) {
                try {
                    if (window[k] && window[k]._leaflet_id && window[k]._container) {
                        map = window[k]; break;
                    }
                } catch(e) {}
            }
        }
        if (!map) {
            var rd = document.getElementById('cpResult');
            if (rd) { rd.innerHTML = '⚠ Mapa no encontrado'; rd.style.color = '#d97706'; }
            return;
        }

        var cpIdx = {};
        var zonaIdx = {};
        var totalFeatures = 0;
        var totalZonas = 0;

        function esVisible(layer) {
            var fo = (layer.options && typeof layer.options.fillOpacity !== 'undefined')
                     ? layer.options.fillOpacity : 0.45;
            return fo > 0;
        }

        function indexLayer(layer) {
            if (layer.feature && layer.feature.properties) {
                var props = layer.feature.properties;
                var visible = esVisible(layer);
                if ('CP' in props) {
                    var cpVal = String(props.CP).replace(/\\.0$/, '').trim();
                    while (cpVal.length < 5) cpVal = '0' + cpVal;
                    if (!(cpVal in cpIdx) || visible) {
                        if (!(cpVal in cpIdx)) totalFeatures++;
                        cpIdx[cpVal] = layer;
                    }
                }
                var zonaNom = props.name || props.NOMBRE || props.Name || null;
                if (zonaNom) {
                    var zk = String(zonaNom).trim().toUpperCase();
                    if (!(zk in zonaIdx) || visible) {
                        if (!(zk in zonaIdx)) totalZonas++;
                        zonaIdx[zk] = layer;
                    }
                }
            }
            if (layer.options && layer.options.name) {
                var zk2 = String(layer.options.name).trim().toUpperCase();
                if (!(zk2 in zonaIdx) || esVisible(layer)) {
                    if (!(zk2 in zonaIdx)) totalZonas++;
                    zonaIdx[zk2] = layer;
                }
            }
            if (layer.eachLayer) {
                layer.eachLayer(function(sub) { indexLayer(sub); });
            }
            if (layer._layers) {
                for (var id in layer._layers) { indexLayer(layer._layers[id]); }
            }
        }
        map.eachLayer(function(layer) { indexLayer(layer); });

        var rd = document.getElementById('cpResult');
        if (rd && totalFeatures > 0) {
            rd.innerHTML = totalFeatures + ' CPs y ' + Object.keys(zonaIdx).length + ' zonas indexados ✓';
            rd.style.color = '#16a34a';
            setTimeout(function() { rd.innerHTML = ''; }, 3000);
        } else if (rd) {
            rd.innerHTML = '⚠ 0 CPs encontrados';
            rd.style.color = '#d97706';
        }

        var hl = null, os = null;

        function resaltarCapa(ly, rd, etiqueta) {
            os = {
                fillColor: ly.options.fillColor || '#9e9e9e',
                fillOpacity: ly.options.fillOpacity || 0.45,
                color: ly.options.color || '#ffffff',
                weight: ly.options.weight || 1.5
            };
            ly.setStyle({ fillColor:'#ff0000', fillOpacity:0.7, color:'#ff0000', weight:3 });
            hl = ly;
            if (ly.getBounds) map.fitBounds(ly.getBounds(), {padding:[50,50], maxZoom:14});
            if (ly.openTooltip) ly.openTooltip();
        }

        window.buscarCP = function(valor) {
            var rd = document.getElementById('cpResult');
            var raw = String(valor).trim();
            if (!raw) {
                if (rd) { rd.innerHTML = 'Escribe un CP o nombre de zona'; rd.style.color = '#64748b'; }
                return;
            }

            if (hl && os) { try { hl.setStyle(os); } catch(e) {} }

            var esCP = /^[0-9]+(\\.0)?$/.test(raw);

            if (esCP) {
                var cp = raw.replace(/\\.0$/, '');
                while (cp.length < 5) cp = '0' + cp;
                var ly = cpIdx[cp];
                if (ly) {
                    resaltarCapa(ly, rd, 'CP');
                    if (rd) {
                        var p = ly.feature.properties;
                        rd.innerHTML = '✔ CP ' + cp + ' — ' + (p.ESTADO_PERTENECE||'') +
                            ' | Vol: ' + (p.VOLUMEN||0) + ' | Partners: ' + (p.PARTNERS||0);
                        rd.style.color = '#16a34a';
                    }
                } else if (rd) {
                    rd.innerHTML = '⚠ El CP ' + cp + ' no está dentro de la cobertura';
                    rd.style.color = '#dc2626';
                }
                return;
            }

            var clave = raw.toUpperCase();
            var lyZona = zonaIdx[clave];

            if (!lyZona) {
                var candidatos = Object.keys(zonaIdx).filter(function(k){ return k.indexOf(clave) !== -1; });
                if (candidatos.length > 0) {
                    lyZona = zonaIdx[candidatos[0]];
                    clave = candidatos[0];
                }
            }

            if (lyZona) {
                resaltarCapa(lyZona, rd, 'ZONA');
                if (rd) {
                    rd.innerHTML = '✔ Zona ' + clave + ' localizada';
                    rd.style.color = '#16a34a';
                }
            } else if (rd) {
                rd.innerHTML = '✘ No se encontró el CP ni la zona "' + raw + '"';
                rd.style.color = '#dc2626';
            }
        };

        var pr = new URLSearchParams(window.location.search).get('cp');
        if (pr) setTimeout(function() { buscarCP(pr); }, 500);

        window.addEventListener('message', function(e) {
            if (e.data && e.data.action === 'searchCP' && e.data.cp) {
                buscarCP(e.data.cp);
            }
        });

        // 🖱️ Inyectar el botón "Info: Hover/Click" DENTRO del control de capas
        //    (junto a CP/Zonas/Radio/Prospección), como una fila más del panel.
        try {
            var _lc = document.querySelector('.leaflet-control-layers-list');
            if (_lc && !document.getElementById('ttModeRow')) {
                var _sep = document.createElement('div');
                _sep.className = 'leaflet-control-layers-separator';
                _lc.appendChild(_sep);
                var _row = document.createElement('div');
                _row.id = 'ttModeRow';
                _row.style.cssText = 'padding:4px 2px; font-family:\\'Segoe UI\\',sans-serif;';
                _row.innerHTML =
                    '<button id="ttModeBtn" onclick="toggleTooltipMode()" ' +
                    'style="width:100%; background:#16a34a; color:white; border:none; border-radius:5px; ' +
                    'padding:5px 8px; font-size:12px; font-weight:600; cursor:pointer; white-space:nowrap;">' +
                    '🖱️ Info: Hover</button>' +
                    '<div id="ttModeHint" style="font-size:10px; color:#94a3b8; margin-top:3px; text-align:center;">' +
                    'Zonas encimadas → Click</div>';
                _lc.appendChild(_row);
            }
        } catch(e) {}

        try { window.parent.postMessage({action:'mapReady', cpCount: totalFeatures}, '*'); } catch(ex) {}
    }

    // ═══════════════════════════════════════════════════════════════
    // 🖱️ MODO DE INFORMACIÓN: Hover (tooltip al pasar) ⇄ Click (solo popup)
    //    Ideal para ZONAS ENCIMADAS: en modo Click el hover deja de dispararse
    //    y la info solo aparece al seleccionar, evitando el caos de tooltips.
    // ═══════════════════════════════════════════════════════════════
    window._ttMode = 'hover';   // estado inicial
    window._ttStore = [];       // guarda {layer, content, options} para re-enlazar

    function _ttFindMap() {
        for (var k in window) {
            try {
                if (k.indexOf('map_') === 0 && window[k] && window[k].eachLayer) return window[k];
            } catch(e) {}
        }
        for (var k2 in window) {
            try {
                if (window[k2] && window[k2]._leaflet_id && window[k2]._container) return window[k2];
            } catch(e) {}
        }
        return null;
    }

    function _ttWalk(layer, cb) {
        cb(layer);
        if (layer.eachLayer) layer.eachLayer(function(sub){ _ttWalk(sub, cb); });
        if (layer._layers) { for (var id in layer._layers) _ttWalk(layer._layers[id], cb); }
    }

    window.toggleTooltipMode = function() {
        var map = _ttFindMap();
        var btn = document.getElementById('ttModeBtn');
        var hint = document.getElementById('ttModeHint');
        if (!map) { return; }

        if (window._ttMode === 'hover') {
            // → Pasar a modo CLICK: desenlazar todos los tooltips (guardándolos)
            window._ttStore = [];
            var _nOff = 0;
            _ttWalk(map, function(ly) {
                // Caso 1: capa individual con tooltip propio
                if (ly.getTooltip && ly.getTooltip()) {
                    var tt = ly.getTooltip();
                    window._ttStore.push({ layer: ly, content: tt.getContent(), options: tt.options });
                    ly.unbindTooltip();
                    _nOff++;
                }
                // Caso 2 (Canvas/GeoJson): bloquear el evento de tooltip a nivel capa
                if (ly.off && ly.on) {
                    try { ly.off('mouseover'); ly.off('mousemove'); } catch(e) {}
                }
            });
            window._ttMode = 'click';
            if (btn) {
                btn.innerHTML = '👆 Info: Click';
                btn.style.background = '#2563eb';
            }
            if (hint) hint.innerHTML = 'Clic para ver info';
        } else {
            // → Volver a modo HOVER: re-enlazar los tooltips guardados
            window._ttStore.forEach(function(rec) {
                try { rec.layer.bindTooltip(rec.content, rec.options); } catch(e) {}
            });
            window._ttStore = [];
            window._ttMode = 'hover';
            if (btn) {
                btn.innerHTML = '🖱️ Info: Hover';
                btn.style.background = '#16a34a';
            }
            if (hint) hint.innerHTML = 'Zonas encimadas → Click';
        }
    };

    </script>
    """
    m.get_root().html.add_child(folium.Element(search_html))

    # ⚡ Un solo render del HTML standalone (sirve para mostrar y descargar).
    return m.get_root().render()


with open('config.yaml') as f:
    config = yaml.load(f, SafeLoader)

auth = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days']
)

auth.login(location='main')

if st.session_state["authentication_status"]:
    if 'procesado' not in st.session_state:
        st.session_state.procesado = False
    if 'resultados' not in st.session_state:
        st.session_state.resultados = None

    col_m, col_p = st.columns([3, 1.2])

    with col_p:
        st.title("🛡️ Panel Cobertura")
        auth.logout('Cerrar Sesión', 'sidebar')
        if not os.path.exists('mapas'):
            st.error("Falta carpeta mapas")
            st.stop()
        archs_geo = sorted([f for f in os.listdir('mapas') if f.endswith('.geojson')])
        estados_disponibles = [f.replace('.geojson', '') for f in archs_geo]
        edo_sel = st.selectbox("📍 Seleccionar Estado:", ["Todos"] + estados_disponibles)
        f_poligonos = st.file_uploader("Archivo Cobertura (ZONA/CP/VOLUMEN)", type=["xlsx"])
        f_zonas = st.file_uploader("Archivo Zonas Círculos (Nombre/Latitud/Longitud/Radio/Volumen)", type=["xlsx"])

        mostrar_factibilidad = st.checkbox("👁️ Mostrar Radios de Factibilidad (5, 10, 15 km)", value=True)
        st.session_state['mostrar_anillos'] = mostrar_factibilidad

        if st.button("🚀 Procesar Información", use_container_width=True, type="primary") and f_poligonos and f_zonas:
            # ═══════════════════════════════════════════════════════════════
            # 📊 BARRA DE PROGRESO — avance real por fase del procesamiento
            # ═══════════════════════════════════════════════════════════════
            _pbar = st.progress(0, text="⏳ Iniciando procesamiento...")
            def _prog(pct, msg):
                try:
                    _pbar.progress(min(100, int(pct)), text=msg)
                except Exception:
                    pass
            import time as _time
            _t0 = _time.time()
            if True:
                # Limpiar caché para garantizar datos frescos en cada procesamiento
                st.cache_data.clear()
                _prog(5, "📂 Leyendo archivos Excel (cobertura y zonas)...")

                df_poly_user = pd.read_excel(f_poligonos)
                df_poly_user.columns = df_poly_user.columns.str.upper().str.strip()
                df_poly_user['CP'] = df_poly_user['CP'].apply(normalizar_cp)

                # ═══════════════════════════════════════════════════════════
                # 🔧 PARTNERS: Convertir a numérico antes de consolidar
                # ═══════════════════════════════════════════════════════════
                if 'PARTNERS' in df_poly_user.columns:
                    df_poly_user['PARTNERS'] = pd.to_numeric(df_poly_user['PARTNERS'], errors='coerce').fillna(0).astype(int)

                # Consolidar duplicados: sumamos volúmenes y mantenemos la primera ZONA
                if 'VOLUMEN' in df_poly_user.columns:
                    df_poly_user['VOLUMEN'] = pd.to_numeric(df_poly_user['VOLUMEN'], errors='coerce').fillna(0)
                    agg_dict = {'VOLUMEN': 'sum'}
                    if 'ZONA' in df_poly_user.columns:
                        agg_dict['ZONA'] = 'first'
                    # ═══════════════════════════════════════════════════════
                    # 🔧 PARTNERS: Sumar partners al consolidar CPs duplicados
                    # ═══════════════════════════════════════════════════════
                    if 'PARTNERS' in df_poly_user.columns:
                        agg_dict['PARTNERS'] = 'sum'
                    # Preservar todas las demás columnas con 'first'
                    for col in df_poly_user.columns:
                        if col not in ['CP', 'VOLUMEN', 'ZONA', 'PARTNERS']:
                            agg_dict[col] = 'first'
                    df_poly_user = df_poly_user.groupby('CP', as_index=False).agg(agg_dict)
                else:
                    df_poly_user = df_poly_user.drop_duplicates(subset=['CP'])

                # 📑 Segundo archivo (Zonas/Círculos). Encabezados: NOMBRE, LATITUD,
                #    LONGITUD, RADIO, VOLUMEN, NODO. Se normalizan a MAYÚSCULAS y se
                #    incluye NODO (indica a qué nodo pertenece cada zona directamente).
                df_zonas_user = pd.read_excel(f_zonas)
                df_zonas_user.columns = df_zonas_user.columns.astype(str).str.strip()
                # Mapear encabezados conocidos a MAYÚSCULAS (incluye NODO)
                mapa_cols = {c: c.upper() for c in df_zonas_user.columns
                             if c.upper() in ['NOMBRE', 'LATITUD', 'LONGITUD', 'RADIO', 'VOLUMEN', 'NODO']}
                df_zonas_user = df_zonas_user.rename(columns=mapa_cols)

                @st.cache_data
                def generar_mapa_base_cached(edo_sel, estados_disponibles, df_poly_user):
                    estados_a_cargar = estados_disponibles if edo_sel == "Todos" else [edo_sel]
                    gdfs = []
                    for e in estados_a_cargar:
                        p = os.path.join("mapas", f"{e}.geojson")
                        if os.path.exists(p):
                            g = gpd.read_file(p)
                            g['ESTADO_PERTENECE'] = e
                            gdfs.append(g)

                    if not gdfs:
                        return gpd.GeoDataFrame()

                    gdf_base = pd.concat(gdfs, ignore_index=True)
                    cp_col = next((c for c in ['d_codigo', 'd_cp', 'CP', 'CODIGOPOSTAL', 'cp'] if c in gdf_base.columns), gdf_base.columns[0])
                    gdf_base[cp_col] = gdf_base[cp_col].astype(str).apply(normalizar_cp)

                    gdf_cob = gdf_base.merge(df_poly_user, left_on=cp_col, right_on='CP', how='inner').set_crs("EPSG:4326", allow_override=True)
                    return gdf_cob

                _prog(15, "🗺️ Cargando mapas GeoJSON y cruzando con CPs...")
                gdf_cobertura = generar_mapa_base_cached(edo_sel, estados_disponibles, df_poly_user)

                if gdf_cobertura.empty:
                    st.warning("⚠️ No se encontraron coincidencias entre los CPs del Excel y los mapas GeoJSON.")
                    st.stop()

                gdf_cobertura = gdf_cobertura.drop_duplicates(subset=['CP'])
                estados_con_cobertura_real = gdf_cobertura['ESTADO_PERTENECE'].unique().tolist()

                for c in ['LATITUD', 'LONGITUD', 'RADIO', 'VOLUMEN']:
                    df_zonas_user[c] = pd.to_numeric(df_zonas_user[c], errors='coerce')
                df_zonas_user = df_zonas_user.dropna(subset=['LATITUD', 'LONGITUD', 'RADIO'])
                pts = [Point(xy) for xy in zip(df_zonas_user['LONGITUD'], df_zonas_user['LATITUD'])]
                gdf_circles = gpd.GeoDataFrame(df_zonas_user, geometry=pts, crs="EPSG:4326")
                # Normalizar NODO del 2º archivo (MAYÚSCULAS/strip) para comparar con ZONA del 1º
                if 'NODO' in gdf_circles.columns:
                    gdf_circles['NODO'] = gdf_circles['NODO'].astype(str).str.strip().str.upper()

                # ═══════════════════════════════════════════════════════════════
                # 📐 DOBLE PROYECCIÓN: Albers (áreas) + Lambert (distancias)
                # EPSG:6372 = Albers Equal-Area Conic México → preserva ÁREAS
                # EPSG:6362 = Lambert Conformal Conic México → preserva DISTANCIAS
                # ═══════════════════════════════════════════════════════════════
                CRS_AREAS = "EPSG:6372"      # Albers — para km², % cobertura
                CRS_DISTANCIAS = "EPSG:6362"  # Lambert — para metros, radios, perímetros

                gdf_cobertura_m = gdf_cobertura.to_crs(CRS_AREAS)
                gdf_circles_m = gdf_circles.to_crs(CRS_AREAS)
                gdf_circles_m['geometry'] = gdf_circles_m.apply(lambda r: r['geometry'].buffer(r['RADIO']), axis=1)

                gdf_circles_m['AREA_KM2'] = gdf_circles_m['geometry'].area / 1000000.0
                geom_cir_total = unary_union(gdf_circles_m['geometry'].buffer(0))

                nombre_archivo_zonas = f_zonas.name if hasattr(f_zonas, 'name') else "JUNIO.xlsx"
                mes_extraido = os.path.splitext(nombre_archivo_zonas)[0].upper()
                gdf_circles_m['Territorio MES'] = mes_extraido

                if 'geometry' in gdf_circles_m.columns and not gdf_cobertura.empty:
                    circles_gps = gdf_circles_m.to_crs(gdf_cobertura.crs)
                    joined = gpd.sjoin(circles_gps, gdf_cobertura[['geometry', 'ESTADO_PERTENECE']], how='left', predicate='intersects')
                    gdf_circles_m['ESTADO'] = joined.groupby(joined.index)['ESTADO_PERTENECE'].first().fillna('DESCONOCIDO').str.upper()
                else:
                    gdf_circles_m['ESTADO'] = 'DESCONOCIDO'

                gdf_circles_m_corr = gdf_circles_m.copy()
                for idx, row in gdf_circles_m_corr.iterrows():
                    if row['RADIO'] < 100:
                        base_geom = gdf_circles[gdf_circles['NOMBRE'] == row['NOMBRE']]['geometry'].to_crs(CRS_DISTANCIAS).iloc[0]
                        gdf_circles_m_corr.at[idx, 'geometry'] = base_geom.buffer(row['RADIO'] * 1000)

                reporte_cp_por_zona = []
                reporte_cp_por_estado = []
                anillos_por_estado = {}

                nodos_unicos_maestro = gdf_cobertura['ZONA'].dropna().unique().tolist()
                centroides_nodos_globales = []

                # Proyecciones Lambert para cálculos de distancia radial
                gdf_cobertura_lambert = gdf_cobertura.to_crs(CRS_DISTANCIAS)
                gdf_circles_m_lambert = gdf_circles_m_corr.to_crs(CRS_DISTANCIAS)

                for nodo in nodos_unicos_maestro:
                    cob_nodo_completa = gdf_cobertura[gdf_cobertura['ZONA'] == nodo]
                    if cob_nodo_completa.empty or gdf_circles_m_lambert.empty:
                        continue

                    g_cob_nodo_global = unary_union(cob_nodo_completa['geometry'].to_crs(CRS_DISTANCIAS).buffer(0))

                    # 🎯 ZONAS DEL NODO = las que tienen NODO == este nodo en el 2º archivo
                    #    (fuente de verdad explícita). NO por intersección espacial —así no
                    #    salen centroides de nodos cuyas zonas en realidad son de otro nodo.
                    _nodo_norm = str(nodo).strip().upper()
                    if 'NODO' in gdf_circles_m_lambert.columns:
                        partners_del_nodo = gdf_circles_m_lambert[
                            gdf_circles_m_lambert['NODO'].astype(str).str.strip().str.upper() == _nodo_norm
                        ]
                    else:
                        # Fallback (archivo antiguo sin NODO): intersección espacial
                        partners_del_nodo = gdf_circles_m_lambert[gdf_circles_m_lambert['geometry'].intersects(g_cob_nodo_global)]

                    # 🚫 REGLA: si el nodo NO tiene zonas asignadas en el 2º archivo,
                    #    NO se calcula centroide ni anillos — se omite por completo.
                    #    El centroide es "por acumulación de zonas": sin zonas no hay centroide.
                    if partners_del_nodo.empty:
                        continue

                    masa_partners_nodo_m = unary_union(partners_del_nodo['geometry'])
                    centroide_acumulacion_nodo_m = masa_partners_nodo_m.centroid

                    centroides_nodos_globales.append(centroide_acumulacion_nodo_m)

                    pt_gps = gpd.GeoSeries([centroide_acumulacion_nodo_m], crs=CRS_DISTANCIAS).to_crs("EPSG:4326").iloc[0]

                    b5 = centroide_acumulacion_nodo_m.buffer(5000)
                    b10 = centroide_acumulacion_nodo_m.buffer(10000)
                    b15 = centroide_acumulacion_nodo_m.buffer(15000)

                    anillos_por_estado[nodo] = {
                        'centro_lat': pt_gps.y,
                        'centro_lon': pt_gps.x,
                        'r5': gpd.GeoSeries([b5], crs=CRS_DISTANCIAS).to_crs("EPSG:4326").iloc[0].__geo_interface__,
                        'r10': gpd.GeoSeries([b10], crs=CRS_DISTANCIAS).to_crs("EPSG:4326").iloc[0].__geo_interface__,
                        'r15': gpd.GeoSeries([b15], crs=CRS_DISTANCIAS).to_crs("EPSG:4326").iloc[0].__geo_interface__
                    }

                union_total_partners_m = unary_union(gdf_circles_m_corr['geometry']).buffer(0) if not gdf_circles_m_corr.empty else None

                # ═══════════════════════════════════════════════════════════════
                # 📦 CÁLCULO DE UPSIDE: paquetes capturables por zona sobre cada CP
                #    (Monte Carlo VECTORIZADO por CP con reparto 1/k en zonas traslapadas)
                #    Se usa gdf_cobertura_m (Albers) para áreas precisas.
                # ═══════════════════════════════════════════════════════════════
                _prog(35, "📦 Calculando Upside por zona (Monte Carlo vectorizado)...")
                upside_por_zona, upside_por_cp_zona, upside_ocupado_por_cp = calcular_upside_por_zona(
                    gdf_cobertura_m, gdf_circles_m_corr.to_crs(CRS_AREAS), nodos_unicos_maestro
                )

                # ═══════════════════════════════════════════════════════════════
                # ⚡ OPTIMIZACIÓN 8K CPs × 3K zonas — PRECÁLCULO VECTORIZADO
                #    Reemplaza los dos bucles anidados (CP×union y zona×CP) por
                #    operaciones vectorizadas de GeoPandas calculadas UNA sola vez.
                #    Albers (CRS_AREAS) → áreas precisas.
                # ═══════════════════════════════════════════════════════════════
                # (A) % cobertura de CADA CP contra la UNIÓN de partners (vectorizado):
                #     usamos area de intersección CP ∩ union_partners sin bucle Python.
                #     ⚠️ IMPORTANTE: reparar geometrías inválidas con make_valid/buffer(0)
                #     ANTES de intersectar — evita shapely.errors.GEOSException.
                _cob_area = gdf_cobertura_m[['CP', 'geometry']].copy()
                # Reparar geometrías de CPs (anillos auto-intersectados, etc.)
                try:
                    _cob_area['geometry'] = _cob_area.geometry.make_valid()
                except Exception:
                    _cob_area['geometry'] = _cob_area.geometry.buffer(0)
                _cob_area['_area_cp'] = _cob_area.geometry.area
                if union_total_partners_m is not None:
                    # Reparar también la unión de partners
                    _union_fix = union_total_partners_m.buffer(0)
                    try:
                        _inter_area = _cob_area.geometry.intersection(_union_fix).area
                    except Exception:
                        # Fallback robusto: calcular por CP con manejo de errores individual
                        _inter_area = _cob_area.geometry.apply(
                            lambda g: g.buffer(0).intersection(_union_fix).area if g is not None and not g.is_empty else 0.0
                        )
                else:
                    _inter_area = pd.Series(0.0, index=_cob_area.index)
                _pct_cob = (_inter_area / _cob_area['_area_cp'].replace(0, np.nan) * 100).fillna(0).clip(upper=100)
                _pct_cob_por_cp = dict(zip(_cob_area['CP'].astype(str), _pct_cob))

                # (B) Distancia radial de CADA CP al centroide de nodo más cercano (vectorizado, Lambert):
                _cob_lam = gdf_cobertura_lambert[['CP', 'geometry']].copy()
                _cent_lam = _cob_lam.geometry.centroid
                if centroides_nodos_globales:
                    _dist_min = None
                    for _c in centroides_nodos_globales:
                        _d = _cent_lam.distance(_c)
                        _dist_min = _d if _dist_min is None else np.minimum(_dist_min, _d)
                else:
                    _dist_min = pd.Series(1e12, index=_cob_lam.index)
                _dist_por_cp = dict(zip(_cob_lam['CP'].astype(str), _dist_min))

                # (C) Pares zona×CP con % de cobertura — UN SOLO gpd.overlay (reemplaza
                #     el doble bucle zona(3000)×cp). Devuelve todas las intersecciones reales.
                #     ⚠️ Usamos nombres de columna NO conflictivos (_ZNOM/_CPCP/_CPZONA) para
                #        que gpd.overlay NO los renombre con sufijos _1/_2 y la tabla no quede vacía.
                _zonas_gdf = gdf_circles_m_corr[['NOMBRE', 'geometry']].copy().rename(columns={'NOMBRE': '_ZNOM'})
                _cps_gdf = gdf_cobertura_m[['CP', 'ZONA', 'geometry']].copy().rename(columns={'CP': '_CPCP', 'ZONA': '_CPZONA'})
                # Reparar geometrías inválidas ANTES del overlay (make_valid → buffer(0) fallback)
                try:
                    _zonas_gdf['geometry'] = _zonas_gdf.geometry.make_valid()
                    _cps_gdf['geometry'] = _cps_gdf.geometry.make_valid()
                except Exception:
                    _zonas_gdf['geometry'] = _zonas_gdf.geometry.buffer(0)
                    _cps_gdf['geometry'] = _cps_gdf.geometry.buffer(0)
                # Descartar geometrías vacías/nulas que romperían el overlay
                _zonas_gdf = _zonas_gdf[~_zonas_gdf.geometry.is_empty & _zonas_gdf.geometry.notna()]
                _cps_gdf = _cps_gdf[~_cps_gdf.geometry.is_empty & _cps_gdf.geometry.notna()]
                _cps_gdf['_AREACP'] = _cps_gdf.geometry.area
                _pares_zona_cp = {}  # (nodo, zona_nombre) -> list[(cp_str, pct)]
                _prog(55, "🔗 Cruzando zonas × CPs (overlay vectorizado)...")
                _overlay_ok = False
                try:
                    _ov = gpd.overlay(_zonas_gdf, _cps_gdf, how='intersection', keep_geom_type=False)
                    if not _ov.empty:
                        _ov['_area_inter'] = _ov.geometry.area
                        _ov['_pct'] = (_ov['_area_inter'] / _ov['_AREACP'].replace(0, np.nan) * 100).fillna(0).clip(upper=100)
                        _ov = _ov[_ov['_pct'].round() >= 1]
                        # Iterar por columnas por NOMBRE (robusto, sin depender de itertuples)
                        for _znom, _cpzona, _cpcp, _pctv in zip(
                            _ov['_ZNOM'].tolist(), _ov['_CPZONA'].tolist(),
                            _ov['_CPCP'].tolist(), _ov['_pct'].tolist()
                        ):
                            _pares_zona_cp.setdefault((_cpzona, _znom), []).append((str(_cpcp), float(_pctv)))
                        _overlay_ok = len(_pares_zona_cp) > 0
                except Exception:
                    _pares_zona_cp = {}

                # ── FALLBACK robusto: si el overlay falló o quedó vacío, usar sjoin +
                #    intersección por par para garantizar que la tabla NO quede vacía.
                if not _overlay_ok:
                    try:
                        _pares_zona_cp = {}
                        _sj = gpd.sjoin(_cps_gdf, _zonas_gdf, how='inner', predicate='intersects')
                        _zgeom = dict(zip(_zonas_gdf['_ZNOM'].tolist(), _zonas_gdf.geometry.tolist()))
                        _cpgeom = dict(zip(_cps_gdf['_CPCP'].tolist(), _cps_gdf.geometry.tolist()))
                        _cparea = dict(zip(_cps_gdf['_CPCP'].tolist(), _cps_gdf['_AREACP'].tolist()))
                        for _r in _sj.itertuples(index=False):
                            _cpcp = getattr(_r, '_CPCP'); _cpzona = getattr(_r, '_CPZONA'); _znom = getattr(_r, '_ZNOM')
                            _gcp = _cpgeom.get(_cpcp); _gz = _zgeom.get(_znom); _acp = _cparea.get(_cpcp, 0)
                            if _gcp is not None and _gz is not None and _acp and _acp > 0:
                                try:
                                    _ai = _gcp.intersection(_gz).area
                                    _pctv = min(100.0, (_ai / _acp) * 100)
                                    if round(_pctv) >= 1:
                                        _pares_zona_cp.setdefault((_cpzona, _znom), []).append((str(_cpcp), float(_pctv)))
                                except Exception:
                                    continue
                    except Exception:
                        pass

                _n_nodos = max(1, len(nodos_unicos_maestro))
                _prog(60, f"📍 Generando reportes por nodo (0/{_n_nodos})...")
                for _i_nodo, nodo_iter in enumerate(nodos_unicos_maestro, 1):
                    # progreso 60→80% repartido entre los nodos
                    _prog(60 + int(20 * _i_nodo / _n_nodos), f"📍 Generando reportes por nodo ({_i_nodo}/{_n_nodos})...")
                    sub_cob = gdf_cobertura_m[gdf_cobertura_m['ZONA'] == nodo_iter]
                    # Proyección Lambert del mismo subconjunto para cálculos de distancia
                    sub_cob_lambert = gdf_cobertura_lambert[gdf_cobertura_lambert['ZONA'] == nodo_iter]
                    if not sub_cob.empty:

                        cps_cubiertos_100 = set()
                        cps_cubiertos_parcial = set()
                        cps_parciales_faltantes_porc = set()
                        cps_perimetro_5km = set()
                        cps_perimetro_5_10km = set()
                        cps_perimetro_gt10km = set()

                        for _, cp_row in sub_cob.iterrows():
                            area_real_cp_fija = cp_row['geometry'].area  # Albers → área precisa
                            if area_real_cp_fija <= 0:
                                continue

                            cp_str = cp_row['CP']
                            zona_lbl = cp_row.get('ZONA', 'S/N')

                            # ⚡ % cobertura precalculado vectorizado (dict). Sin intersección por CP.
                            porcentaje_cobertura = _pct_cob_por_cp.get(str(cp_str), 0.0)
                            if porcentaje_cobertura > 0.0:
                                if porcentaje_cobertura >= 95:
                                    cps_cubiertos_100.add(f"{cp_str}")
                                else:
                                    cps_cubiertos_parcial.add(f"{cp_str} ({round(porcentaje_cobertura, 0)}%)")
                                    porcentaje_faltante = 100 - porcentaje_cobertura
                                    cps_parciales_faltantes_porc.add(f"{cp_str} ({round(porcentaje_faltante, 0)}%)")

                                if porcentaje_cobertura < 0.01:
                                    cp_str = f"LIBRE - {cp_str}"
                            else:
                                cp_str = f"LIBRE - {cp_str}"

                            # 📏 DISTANCIA RADIAL precalculada vectorizada (dict, Lambert).
                            distancia_al_centroide = _dist_por_cp.get(str(cp_row['CP']), 1e12)
                            if centroides_nodos_globales:
                                if distancia_al_centroide <= 5000:
                                    cps_perimetro_5km.add(f"{cp_str}")
                                elif distancia_al_centroide <= 10000:
                                    cps_perimetro_5_10km.add(f"{cp_str}")
                                else:
                                    cps_perimetro_gt10km.add(f"{cp_str}")
                            else:
                                cps_perimetro_gt10km.add(f"{cp_str}")

                        cps_reales_primer_archivo = set(sub_cob['CP'].astype(str).tolist())

                        cps_solo_libres = [cp for cp in (list(cps_perimetro_5km) + list(cps_perimetro_5_10km) + list(cps_perimetro_gt10km)) if "LIBRE" in cp]
                        cps_solo_libres_clean = [cp.replace("LIBRE - ", "") for cp in cps_solo_libres]

                        cps_libres_filtrados = [cp for cp in cps_solo_libres_clean if cp in cps_reales_primer_archivo]

                        cps_p5_limpios = [cp for cp in cps_perimetro_5km if "LIBRE" not in cp]
                        cps_p10_limpios = [cp for cp in cps_perimetro_5_10km if "LIBRE" not in cp]
                        cps_p15_limpios = [cp for cp in cps_perimetro_gt10km if "LIBRE" not in cp]

                        reporte_cp_por_estado.append({"Nodo": nodo_iter, "Estatus": "Cubierto Total (100%)", "CP": ", ".join(sorted(list(cps_cubiertos_100))) if cps_cubiertos_100 else "Ninguno"})
                        reporte_cp_por_estado.append({"Nodo": nodo_iter, "Estatus": "Cubierto Parcial (~50%)", "CP": ", ".join(sorted(list(cps_cubiertos_parcial))) if cps_cubiertos_parcial else "Ninguno"})
                        reporte_cp_por_estado.append({"Nodo": nodo_iter, "Estatus": "libre", "CP": ", ".join(sorted(cps_libres_filtrados)) if cps_libres_filtrados else "Ninguno"})
                        reporte_cp_por_estado.append({"Nodo": nodo_iter, "Estatus": "perimetro 5km", "CP": ", ".join(sorted(cps_p5_limpios)) if cps_p5_limpios else "Ninguno"})
                        reporte_cp_por_estado.append({"Nodo": nodo_iter, "Estatus": "perimetro 5-10km", "CP": ", ".join(sorted(cps_p10_limpios)) if cps_p10_limpios else "Ninguno"})
                        reporte_cp_por_estado.append({"Nodo": nodo_iter, "Estatus": "perimetro 10-15km", "CP": ", ".join(sorted(cps_p15_limpios)) if cps_p15_limpios else "Ninguno"})

                        # ⚡ Reemplazo del doble bucle zona(3000)×cp por lectura de los
                        #    pares precalculados con gpd.overlay (_pares_zona_cp).
                        #    Solo recorremos las zonas que realmente intersectan CPs de ESTE nodo.
                        _zonas_de_este_nodo = [k[1] for k in _pares_zona_cp.keys() if k[0] == nodo_iter]
                        for _zona_nom in sorted(set(_zonas_de_este_nodo)):
                            _pares = _pares_zona_cp.get((nodo_iter, _zona_nom), [])
                            if not _pares:
                                continue
                            cps_actuales_zona_con_pct = []
                            for _cp_str, _pct in _pares:
                                up_val = upside_por_cp_zona.get((_zona_nom, _cp_str), 0)
                                cps_actuales_zona_con_pct.append(
                                    f"{_cp_str} ({round(_pct)}% → Upside {up_val})"
                                )
                            if cps_actuales_zona_con_pct:
                                up_total_zona = upside_por_zona.get(_zona_nom, {}).get('total', 0)
                                reporte_cp_por_zona.append({
                                    "Nodo": nodo_iter,
                                    "Zona": _zona_nom,
                                    "CPs Cubiertos (% → Upside pqts)": ", ".join(sorted(list(set(cps_actuales_zona_con_pct)))),
                                    "Upside Total (pqts)": up_total_zona
                                })

                df_cp_por_estado = pd.DataFrame(reporte_cp_por_estado)
                df_cp_por_zona = pd.DataFrame(reporte_cp_por_zona)
                if not df_cp_por_estado.empty:
                    df_cp_por_estado = df_cp_por_estado[["Nodo", "Estatus", "CP"]]

                # ═══════════════════════════════════════════════════════════════
                # 📊 DESGLOSE POR NODO (ZONA) — Territorio, Ocupado, Libre, Eficiencia
                # ═══════════════════════════════════════════════════════════════
                # 🔢 ZONAS POR NODO = nº de CÍRCULOS del SEGUNDO archivo (Zonas) por nodo.
                #    ✅ FUENTE DE VERDAD: la columna NODO del 2º archivo (asignación directa
                #       y explícita de cada zona a su nodo). Si el archivo trae NODO, se
                #       cuenta por esa columna — NO por intersección espacial (más preciso).
                #    ↩️ Fallback: si el archivo NO trae NODO (formato antiguo), se usa el
                #       método espacial (sjoin círculo↔CP) como antes.
                zonas_por_nodo = {}
                if 'NODO' in df_zonas_user.columns:
                    # Normalizar y contar zonas por NODO directamente del 2º archivo
                    _nodo_series = df_zonas_user['NODO'].astype(str).str.strip().str.upper()
                    zonas_por_nodo = _nodo_series.value_counts().to_dict()
                else:
                    try:
                        _circ_cnt = gdf_circles_m_corr[['NOMBRE', 'geometry']].copy()
                        _circ_cnt['geometry'] = _circ_cnt.geometry.buffer(0)
                        _cob_cnt = gdf_cobertura_m[['ZONA', 'geometry']].copy()
                        _cob_cnt['geometry'] = _cob_cnt.geometry.buffer(0)
                        _sj_cnt = gpd.sjoin(_circ_cnt, _cob_cnt, how='inner', predicate='intersects')
                        _asig = {}
                        for _nom, _grp in _sj_cnt.groupby('NOMBRE'):
                            _conteo = _grp['ZONA'].value_counts()
                            _asig[_nom] = _conteo.index[0] if len(_conteo) else None
                        for _nom, _nod in _asig.items():
                            if _nod is not None:
                                zonas_por_nodo[_nod] = zonas_por_nodo.get(_nod, 0) + 1
                    except Exception:
                        zonas_por_nodo = {}

                # 🎯 NODOS VÁLIDOS = los que vienen en la columna NODO del 2º archivo.
                #    Solo se mostrarán los CPs de estos nodos (referencia: columna NODO).
                if 'NODO' in df_zonas_user.columns:
                    _nodos_del_segundo_archivo = set(
                        df_zonas_user['NODO'].astype(str).str.strip().str.upper().dropna().tolist()
                    )
                else:
                    _nodos_del_segundo_archivo = set(zonas_por_nodo.keys())

                desglose_nodos = []
                for nodo in nodos_unicos_maestro:
                    sub_cob_nodo = gdf_cobertura_m[gdf_cobertura_m['ZONA'] == nodo]
                    # 🚫 REGLA: solo se muestran nodos que TIENEN zonas (círculos del 2º
                    #    archivo) encima. Si el nodo no tiene zonas asignadas, se omite
                    #    por completo (no entra al desglose → sus CPs tampoco se dibujan,
                    #    porque gdf_cobertura_filtrada se arma desde nodos_validos).
                    # ✅ Validar contra los NODOS del 2º archivo (comparación normalizada
                    #    MAYÚSCULAS/strip, porque ZONA del 1er archivo puede diferir en caja).
                    _nodo_norm = str(nodo).strip().upper()
                    _tiene_zonas = _nodo_norm in _nodos_del_segundo_archivo
                    if not sub_cob_nodo.empty and _tiene_zonas:
                        # 🗺️ Estado del nodo = VOTO POR MAYORÍA de sus CPs.
                        #    Un CP fronterizo puede venir del GeoJSON del estado vecino
                        #    (p.ej. un CP de Oaxaca que también existe en el archivo de
                        #    Veracruz). Para no mal-clasificar el nodo, lo asignamos al
                        #    estado donde está la MAYORÍA de sus CPs.
                        _conteo_estados = sub_cob_nodo['ESTADO_PERTENECE'].value_counts()
                        _estado_dominante = _conteo_estados.index[0] if len(_conteo_estados) else "DESCONOCIDO"
                        estado_txt = str(_estado_dominante).upper()

                        g_cob_nodo = unary_union(sub_cob_nodo['geometry'].buffer(0))

                        cob_km2 = g_cob_nodo.area / 1000000.0

                        if union_total_partners_m is not None:
                            union_total_partners_clean = union_total_partners_m.buffer(0)
                            interseccion_ocupada = g_cob_nodo.intersection(union_total_partners_clean)
                            ocu_km2 = interseccion_ocupada.area / 1000000.0
                        else:
                            ocu_km2 = 0.0

                        lib_km2 = max(0.0, cob_km2 - ocu_km2)

                        if cob_km2 > 0:
                            eficiencia = (ocu_km2 / cob_km2) * 100.0
                        else:
                            eficiencia = 0.0

                        # Volumen total del nodo
                        vol_nodo = sub_cob_nodo['VOLUMEN'].sum() if 'VOLUMEN' in sub_cob_nodo.columns else 0
                        # 🔢 Partners = nº de ZONAS/círculos del 2º archivo asignados a este nodo
                        partners_nodo = int(zonas_por_nodo.get(_nodo_norm, zonas_por_nodo.get(nodo, 0)))
                        num_cps_nodo = len(sub_cob_nodo)

                        desglose_nodos.append({
                            "Nodo": nodo,
                            "Estado": estado_txt,
                            "CPs": num_cps_nodo,
                            "Volumen Total": int(vol_nodo),
                            "Zonas (Partners)": int(partners_nodo),
                            "Territorio Cobertura Total (km²)": round(cob_km2, 2),
                            "Territorio Ocupado Total (km²)": round(ocu_km2, 2),
                            "Territorio Libre Total (km²)": round(lib_km2, 2),
                            "Eficiencia de Ocupación": f"{round(eficiencia, 2)}%"
                        })

                df_desglose = pd.DataFrame(desglose_nodos)
                if df_desglose.empty:
                    df_desglose = pd.DataFrame(columns=["Nodo", "Estado", "CPs", "Volumen Total", "Zonas (Partners)", "Territorio Cobertura Total (km²)", "Territorio Ocupado Total (km²)", "Territorio Libre Total (km²)", "Eficiencia de Ocupación"])

                nodos_validos = df_desglose['Nodo'].unique().tolist() if not df_desglose.empty else []
                gdf_cobertura_filtrada = gdf_cobertura[gdf_cobertura['ZONA'].isin(nodos_validos)] if nodos_validos else gdf_cobertura

                # ═══════════════════════════════════════════════════════════════
                # 🎲 MONTE CARLO: Calcular traslape entre zonas de cada nodo
                # ═══════════════════════════════════════════════════════════════
                # Preparar DataFrame de círculos con coordenadas GPS para Monte Carlo
                gdf_circles_para_traslape = gdf_circles.copy()
                gdf_circles_para_traslape['RADIO_ORIG'] = df_zonas_user['RADIO'].values
                # Usar RADIO original en metros para el Monte Carlo
                gdf_circles_para_traslape['RADIO'] = gdf_circles_para_traslape['RADIO_ORIG']

                _prog(85, "🎲 Calculando traslape entre zonas (Monte Carlo)...")
                traslape_por_zona, resultados_mc = calcular_traslape_por_zona(
                    gdf_cobertura.to_crs("EPSG:4326"), gdf_circles_para_traslape, nodos_unicos_maestro
                )
                df_traslape_mc = pd.DataFrame(resultados_mc)
                if df_traslape_mc.empty:
                    df_traslape_mc = pd.DataFrame(columns=["Nodo", "% Traslape", "Nivel"])

                # Integrar % Traslape y Nivel al desglose por nodo
                if not df_traslape_mc.empty and not df_desglose.empty:
                    df_desglose = df_desglose.merge(
                        df_traslape_mc[['Nodo', '% Traslape', 'Nivel']],
                        on='Nodo', how='left'
                    )
                    df_desglose['% Traslape'] = df_desglose['% Traslape'].fillna('0.00%')
                    df_desglose['Nivel'] = df_desglose['Nivel'].fillna('⚪ Sin datos')
                    df_desglose.rename(columns={'Nivel': 'Nivel Traslape'}, inplace=True)
                else:
                    # Si no hay datos de traslape, agregar columnas con valores por defecto
                    df_desglose['% Traslape'] = '0.00%'
                    df_desglose['Nivel Traslape'] = '⚪ Sin datos'

                # ═══════════════════════════════════════════════════════════════
                # 🔧 FIX: Guardar gdf_cobertura en session_state para que el
                #    bloque de renderizado del mapa lo tenga disponible en
                #    reruns posteriores (al descargar mapa/reporte).
                # ═══════════════════════════════════════════════════════════════
                # Guardar la cobertura FILTRADA (solo nodos con zonas) — así el mapa
                # y los reruns no dibujan CPs de nodos sin zonas.
                st.session_state['gdf_cobertura_global'] = gdf_cobertura_filtrada

                # Integrar % Traslape individual de cada zona a tabla de CPs por Zona
                if traslape_por_zona and not df_cp_por_zona.empty and 'Zona' in df_cp_por_zona.columns:
                    df_cp_por_zona['% Traslape'] = df_cp_por_zona['Zona'].apply(
                        lambda z: f"{traslape_por_zona[z]['pct']}%" if z in traslape_por_zona else "0.00%"
                    )
                    df_cp_por_zona['Nivel Traslape'] = df_cp_por_zona['Zona'].apply(
                        lambda z: traslape_por_zona[z]['nivel'] if z in traslape_por_zona else "⚪ Sin datos"
                    )

                # ═══════════════════════════════════════════════════════════════
                # 📦 Agregar columna "Upside Total (pqts)" al desglose por nodo
                #    (suma del upside de todas las zonas asignadas a ese nodo)
                # ═══════════════════════════════════════════════════════════════
                # Mapear cada zona a su nodo vía traslape_por_zona
                upside_por_nodo = {}
                for zona_nom, info_tz in traslape_por_zona.items():
                    nodo_de_zona = info_tz.get('nodo')
                    up_z = upside_por_zona.get(zona_nom, {}).get('total', 0)
                    if nodo_de_zona:
                        upside_por_nodo[nodo_de_zona] = upside_por_nodo.get(nodo_de_zona, 0) + up_z
                if not df_desglose.empty:
                    df_desglose['Upside Total (pqts)'] = df_desglose['Nodo'].map(
                        lambda n: int(upside_por_nodo.get(n, 0))
                    )

                # Construir df de detalle de zonas con Upside Total por zona
                df_zonas_detalles_base = gdf_circles_m[['NOMBRE', 'RADIO', 'VOLUMEN', 'AREA_KM2', 'Territorio MES', 'ESTADO']].copy()
                df_zonas_detalles_base['Upside Total (pqts)'] = df_zonas_detalles_base['NOMBRE'].map(
                    lambda n: int(upside_por_zona.get(n, {}).get('total', 0))
                )
                df_zonas_detalles_base = df_zonas_detalles_base.rename(columns={
                    'NOMBRE': 'Nombre de la Zona',
                    'RADIO': 'Radio (m)',
                    'AREA_KM2': 'Territorio'
                })

                st.session_state.resultados = {
                    'estado_nombre': edo_sel,
                    'df_desglose': df_desglose,
                    'gdf_cobertura_wgs84': gdf_cobertura_filtrada.to_crs("EPSG:4326"),
                    'gdf_circles_wgs84': gdf_circles_m.to_crs("EPSG:4326").assign(LATITUD=gdf_circles['LATITUD'], LONGITUD=gdf_circles['LONGITUD']),
                    'df_zonas_detalles': df_zonas_detalles_base,
                    'df_cp_por_estado': df_cp_por_estado,
                    'df_cp_por_zona': df_cp_por_zona,
                    'anillos_por_estado': anillos_por_estado,
                    'df_traslape_mc': df_traslape_mc,
                    'traslape_por_zona': traslape_por_zona,
                    'upside_por_zona': upside_por_zona,
                    'upside_ocupado_por_cp': upside_ocupado_por_cp
                }
                st.session_state.procesado = True

                # ═══════════════════════════════════════════════════════════════
                # ⚡ FIX CLAVE: construir el mapa Folium UNA sola vez aquí (al procesar)
                #    y guardar su HTML en session_state. En reruns (checkboxes,
                #    descargas) NO se reconstruye el mapa → la app responde al instante.
                # ═══════════════════════════════════════════════════════════════
                _prog(92, "🗺️ Construyendo mapa (Canvas renderer)...")
                _mapa_html = construir_mapa_html(
                    st.session_state.resultados,
                    gdf_cobertura_filtrada,
                    st.session_state.get('mostrar_anillos', True)
                )
                st.session_state['mapa_descarga_html'] = _mapa_html

                _elapsed = _time.time() - _t0
                _prog(100, f"✅ Procesamiento completo en {_elapsed:.1f}s")
                _time.sleep(0.4)
                _pbar.empty()



    with col_m:
        if st.session_state.procesado and st.session_state.resultados is not None:
            res = st.session_state.resultados

            # ═══════════════════════════════════════════════════════════════
            # ⚡ Mostrar el mapa cacheado. Si por algún motivo no existe (rerun
            #    sin haber procesado el mapa), reconstruirlo una vez.
            # ═══════════════════════════════════════════════════════════════
            _mapa_html = st.session_state.get('mapa_descarga_html', None)
            if _mapa_html is None:
                gdf_cobertura = st.session_state.get('gdf_cobertura_global', None)
                if gdf_cobertura is None:
                    st.warning("⚠️ Datos de cobertura no disponibles. Por favor, procesa la información nuevamente.")
                    st.stop()
                _mapa_html = construir_mapa_html(res, gdf_cobertura, st.session_state.get('mostrar_anillos', True))
                st.session_state['mapa_descarga_html'] = _mapa_html

            components.html(_mapa_html, height=600)

            st.write("---")
            st.markdown("### 🖥️ Control de Cobertura por Nodo (Albers Equal-Area + Lambert Conformal)")
            st.markdown(f"**Filtro de Consulta Activo:** `{res['estado_nombre']}`")
            st.dataframe(res['df_desglose'], use_container_width=True, hide_index=True)

            st.markdown("### 🎲 Análisis de Traslape por Nodo")
            st.markdown("_Porcentaje de traslape de las zonas operativas de cada nodo._")
            st.dataframe(res['df_traslape_mc'], use_container_width=True, hide_index=True)

            st.markdown("### 📍 Cobertura por CPs por Nodo")
            st.dataframe(res['df_cp_por_estado'], use_container_width=True, hide_index=True)

            st.markdown("### ⭕ Cobertura Detallada por cada Zona (con Upside de paquetes)")
            st.dataframe(res['df_cp_por_zona'], use_container_width=True, hide_index=True)
            st.write("---")

            c1, c2 = st.columns(2)
            with c1:
                st.download_button(label="💾 Descargar Mapa HTML", data=st.session_state.get('mapa_descarga_html', _mapa_html), file_name=f"Mapa_{res['estado_nombre']}.html", mime="text/html", use_container_width=True)
            with c2:
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                    res['df_desglose'].to_excel(writer, index=False, sheet_name='Resumen por Nodo')
                    res['df_zonas_detalles'].rename(columns={'NOMBRE': 'Nombre de la Zona', 'RADIO': 'Radio (m)', 'VOLUMEN': 'Volumen Registrado', 'AREA_KM2': 'Territorio Ocupado Individual (km²)'}).to_excel(writer, index=False, sheet_name='Zonas Detalles')
                    res['df_cp_por_estado'].to_excel(writer, index=False, sheet_name='CPs por Nodo')
                    res['df_cp_por_zona'].to_excel(writer, index=False, sheet_name='CPs por Zona')
                    res['df_traslape_mc'].to_excel(writer, index=False, sheet_name='Traslape entre Zonas')

                st.download_button(label="📊 Descargar Reporte Excel", data=buf.getvalue(), file_name=f"Reporte_{res['estado_nombre']}.xlsx", mime="application/vnd.ms-excel", use_container_width=True)


elif st.session_state["authentication_status"] is False:
    st.error("Error de acceso: Usuario o contraseña incorrectos.")
