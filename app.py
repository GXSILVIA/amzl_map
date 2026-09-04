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
            with st.spinner("Calculando cobertura: Albers (áreas) + Lambert (distancias)..."):
                # Limpiar caché para garantizar datos frescos en cada procesamiento
                st.cache_data.clear()

                df_poly_user = pd.read_excel(f_poligonos)
                df_poly_user.columns = df_poly_user.columns.str.upper().str.strip()
                df_poly_user['CP'] = df_poly_user['CP'].apply(normalizar_cp)
                # Consolidar duplicados: sumamos volúmenes y mantenemos la primera ZONA
                if 'VOLUMEN' in df_poly_user.columns:
                    df_poly_user['VOLUMEN'] = pd.to_numeric(df_poly_user['VOLUMEN'], errors='coerce').fillna(0)
                    agg_dict = {'VOLUMEN': 'sum'}
                    if 'ZONA' in df_poly_user.columns:
                        agg_dict['ZONA'] = 'first'
                    # Preservar todas las demás columnas con 'first'
                    for col in df_poly_user.columns:
                        if col not in ['CP', 'VOLUMEN', 'ZONA']:
                            agg_dict[col] = 'first'
                    df_poly_user = df_poly_user.groupby('CP', as_index=False).agg(agg_dict)
                else:
                    df_poly_user = df_poly_user.drop_duplicates(subset=['CP'])

                df_zonas_user = pd.read_excel(f_zonas)
                df_zonas_user.columns = df_zonas_user.columns.str.strip()
                mapa_cols = {c: c.upper() for c in df_zonas_user.columns if c.upper() in ['NOMBRE', 'LATITUD', 'LONGITUD', 'RADIO', 'VOLUMEN']}
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
                    
                    partners_del_nodo = gdf_circles_m_lambert[gdf_circles_m_lambert['geometry'].intersects(g_cob_nodo_global)]
                    
                    if partners_del_nodo.empty:
                        centroide_temp_cob = g_cob_nodo_global.centroid
                        distancias_a_partners = gdf_circles_m_lambert['geometry'].distance(centroide_temp_cob)
                        partners_del_nodo = gdf_circles_m_lambert.loc[[distancias_a_partners.idxmin()]]
                    
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

                for est in estados_con_cobertura_real:
                    sub_cob = gdf_cobertura_m[gdf_cobertura_m['ESTADO_PERTENECE'] == est]
                    # Proyección Lambert del mismo subconjunto para cálculos de distancia
                    sub_cob_lambert = gdf_cobertura_lambert[gdf_cobertura_lambert['ESTADO_PERTENECE'] == est]
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
                                
                            geom_cp = cp_row['geometry'].buffer(0)  # Albers para intersección de áreas
                            cp_str = cp_row['CP']
                            zona_lbl = cp_row.get('ZONA', 'S/N')
                            
                            if union_total_partners_m is not None and union_total_partners_m.intersects(geom_cp):
                                try:
                                    area_interseccion = geom_cp.intersection(union_total_partners_m).area
                                    porcentaje_cobertura = (area_interseccion / area_real_cp_fija) * 100
                                except Exception:
                                    porcentaje_cobertura = 50.0
                                
                                porcentaje_cobertura = min(100.0, porcentaje_cobertura)
                                
                                if porcentaje_cobertura >= 95:
                                    cps_cubiertos_100.add(f"{zona_lbl}: {cp_str}")
                                else:
                                    cps_cubiertos_parcial.add(f"{zona_lbl}: {cp_str} ({round(porcentaje_cobertura, 0)}%)")
                                    porcentaje_faltante = 100 - porcentaje_cobertura
                                    cps_parciales_faltantes_porc.add(f"{zona_lbl}: {cp_str} ({round(porcentaje_faltante, 0)}%)")
                                
                                if porcentaje_cobertura < 0.01:
                                    cp_str = f"LIBRE - {cp_str}"
                            else:
                                cp_str = f"LIBRE - {cp_str}"

                            # 📏 DISTANCIA RADIAL: Usamos Lambert para medir distancias precisas
                            centroide_cp_lambert = sub_cob_lambert.loc[cp_row.name, 'geometry'].buffer(0).centroid

                            if centroides_nodos_globales:
                                distancia_al_centroide = min([centroide.distance(centroide_cp_lambert) for centroide in centroides_nodos_globales])
                                
                                if distancia_al_centroide <= 5000:
                                    cps_perimetro_5km.add(f"{zona_lbl}: {cp_str}")
                                elif distancia_al_centroide <= 10000:
                                    cps_perimetro_5_10km.add(f"{zona_lbl}: {cp_str}")
                                else:
                                    cps_perimetro_gt10km.add(f"{zona_lbl}: {cp_str}")
                            else:
                                cps_perimetro_gt10km.add(f"{zona_lbl}: {cp_str}")

                        cps_reales_primer_archivo = set(sub_cob['CP'].astype(str).tolist())
                        
                        cps_solo_libres = [cp for cp in (list(cps_perimetro_5km) + list(cps_perimetro_5_10km) + list(cps_perimetro_gt10km)) if "LIBRE" in cp]
                        cps_solo_libres_clean = [cp.replace("LIBRE - ", "") for cp in cps_solo_libres]
                        
                        cps_libres_filtrados = [cp for cp in cps_solo_libres_clean if cp.split(": ")[-1] in cps_reales_primer_archivo]
                        
                        cps_p5_limpios = [cp for cp in cps_perimetro_5km if "LIBRE" not in cp]
                        cps_p10_limpios = [cp for cp in cps_perimetro_5_10km if "LIBRE" not in cp]
                        cps_p15_limpios = [cp for cp in cps_perimetro_gt10km if "LIBRE" not in cp]
                        
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "Cubierto Total (100%)", "CP": ", ".join(sorted(list(cps_cubiertos_100))) if cps_cubiertos_100 else "Ninguno"})
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "Cubierto Parcial (~50%)", "CP": ", ".join(sorted(list(cps_cubiertos_parcial))) if cps_cubiertos_parcial else "Ninguno"})
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "libre", "CP": ", ".join(sorted(cps_libres_filtrados)) if cps_libres_filtrados else "Ninguno"})
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "perimetro 5km", "CP": ", ".join(sorted(cps_p5_limpios)) if cps_p5_limpios else "Ninguno"})
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "perimetro 5-10km", "CP": ", ".join(sorted(cps_p10_limpios)) if cps_p10_limpios else "Ninguno"})
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "perimetro 10-15km", "CP": ", ".join(sorted(cps_p15_limpios)) if cps_p15_limpios else "Ninguno"})

                        for _, zona_row in gdf_circles_m_corr.iterrows():
                            if union_total_partners_m is not None and zona_row['geometry'].intersects(union_total_partners_m):
                                cps_actuales_zona_con_pct = []
                                for _, cp_row in sub_cob.iterrows():
                                    geom_cp = cp_row['geometry'].buffer(0)
                                    if zona_row['geometry'].intersects(geom_cp) or zona_row['geometry'].contains(geom_cp.centroid):
                                        # Calcular % de cobertura del CP dentro de esta zona
                                        area_cp = geom_cp.area
                                        if area_cp > 0:
                                            try:
                                                area_inter = geom_cp.intersection(zona_row['geometry']).area
                                                pct = min(100.0, (area_inter / area_cp) * 100)
                                            except Exception:
                                                pct = 0.0
                                        else:
                                            pct = 0.0
                                        # Solo incluir CPs con cobertura real (>= 1%)
                                        if round(pct) >= 1:
                                            cps_actuales_zona_con_pct.append(f"{cp_row['CP']} ({round(pct)}%)")
                                
                                if cps_actuales_zona_con_pct:
                                    reporte_cp_por_zona.append({
                                        "Zona": zona_row['NOMBRE'],
                                        "Estado": est.upper(),
                                        "CPs Cubiertos": ", ".join(sorted(list(set(cps_actuales_zona_con_pct))))
                                    })

                df_cp_por_estado = pd.DataFrame(reporte_cp_por_estado)
                df_cp_por_zona = pd.DataFrame(reporte_cp_por_zona)
                if not df_cp_por_estado.empty:
                    df_cp_por_estado = df_cp_por_estado[["Estado", "Estatus", "CP"]]

                desglose_estados = []
                for est in estados_con_cobertura_real:
                    sub_cob = gdf_cobertura_m[gdf_cobertura_m['ESTADO_PERTENECE'] == est]
                    if not sub_cob.empty:
                        g_cob_est = unary_union(sub_cob['geometry'].buffer(0))

                        cob_km2 = g_cob_est.area / 1000000.0
                        
                        if union_total_partners_m is not None:
                            union_total_partners_clean = union_total_partners_m.buffer(0)
                            interseccion_ocupada = g_cob_est.intersection(union_total_partners_clean)
                            ocu_km2 = interseccion_ocupada.area / 1000000.0
                        else:
                            ocu_km2 = 0.0
                            
                        lib_km2 = max(0.0, cob_km2 - ocu_km2)
                        
                        if cob_km2 > 0:
                            eficiencia = (ocu_km2 / cob_km2) * 100.0
                        else:
                            eficiencia = 0.0

                        desglose_estados.append({
                            "Estado": est.upper(),
                            "Territorio Cobertura Total (km²)": round(cob_km2, 2),
                            "Territorio Ocupado Total (km²)": round(ocu_km2, 2),
                            "Territorio Libre Total (km²)": round(lib_km2, 2),
                            "Eficiencia de Ocupación": f"{round(eficiencia, 2)}%"
                        })

                df_desglose = pd.DataFrame(desglose_estados)
                if df_desglose.empty:
                    df_desglose = pd.DataFrame(columns=["Estado", "Territorio Cobertura Total (km²)", "Territorio Ocupado Total (km²)", "Territorio Libre Total (km²)", "Eficiencia de Ocupación"])

                estados_validos = df_desglose['Estado'].unique().tolist()
                gdf_cobertura_filtrada = gdf_cobertura[gdf_cobertura['ESTADO_PERTENECE'].isin(estados_validos)]

                # ═══════════════════════════════════════════════════════════════
                # 🔧 FIX: Guardar gdf_cobertura en session_state para que el
                #    bloque de renderizado del mapa lo tenga disponible en
                #    reruns posteriores (al descargar mapa/reporte).
                # ═══════════════════════════════════════════════════════════════
                st.session_state['gdf_cobertura_global'] = gdf_cobertura

                st.session_state.resultados = {
                    'estado_nombre': edo_sel,
                    'df_desglose': df_desglose,
                    'gdf_cobertura_wgs84': gdf_cobertura_filtrada.to_crs("EPSG:4326"),
                    'gdf_circles_wgs84': gdf_circles_m.to_crs("EPSG:4326").assign(LATITUD=gdf_circles['LATITUD'], LONGITUD=gdf_circles['LONGITUD']),
                    'df_zonas_detalles': gdf_circles_m[['NOMBRE', 'RADIO', 'VOLUMEN', 'AREA_KM2', 'Territorio MES', 'ESTADO']].copy().rename(columns={
                        'NOMBRE': 'Nombre de la Zona',
                        'RADIO': 'Radio (m)',
                        'AREA_KM2': 'Territorio'
                    }),
                    'df_cp_por_estado': df_cp_por_estado,
                    'df_cp_por_zona': df_cp_por_zona,
                    'anillos_por_estado': anillos_por_estado
                }
                st.session_state.procesado = True

        # ═══════════════════════════════════════════════════════════════
        # 🎛️ FILTROS POR RANGO (aparecen después de procesar)
        # Están FUERA del if st.button() pero DENTRO de col_p
        # ═══════════════════════════════════════════════════════════════
        if st.session_state.procesado:
            st.markdown("---")
            st.markdown("**🎨 Zonas (Círculos)**")
            rangos_z = ["⚪ R0", "🟡 R1-15", "🟠 R16-20", "🔴 R21-30", "🟣 R31-40", "🟤 R41+"]
            filtro_zonas = []
            for fila in range(0, len(rangos_z), 3):
                cols_z = st.columns(3)
                for j, col in enumerate(cols_z):
                    idx = fila + j
                    if idx < len(rangos_z):
                        with col:
                            if st.checkbox(rangos_z[idx], value=True, key=f"fz_{idx}"):
                                filtro_zonas.append(rangos_z[idx])
            st.session_state['filtro_zonas_activo'] = filtro_zonas

            st.markdown("**🗺️ CPs (Polígonos)**")
            rangos_c = ["⚪ R0", "🟡 R1-100", "🟠 R101-200", "🔴 R201-300", "🟣 R301-400", "🟤 R401+"]
            filtro_cps = []
            for fila in range(0, len(rangos_c), 3):
                cols_c = st.columns(3)
                for j, col in enumerate(cols_c):
                    idx = fila + j
                    if idx < len(rangos_c):
                        with col:
                            if st.checkbox(rangos_c[idx], value=True, key=f"fc_{idx}"):
                                filtro_cps.append(rangos_c[idx])
            st.session_state['filtro_cps_activo'] = filtro_cps

    with col_m:
        if st.session_state.procesado and st.session_state.resultados is not None:
            res = st.session_state.resultados

            # ═══════════════════════════════════════════════════════════════
            # 🔧 FIX: Recuperar gdf_cobertura desde session_state
            #    para que esté disponible en reruns (descarga, etc.)
            # ═══════════════════════════════════════════════════════════════
            gdf_cobertura = st.session_state.get('gdf_cobertura_global', None)
            if gdf_cobertura is None:
                st.warning("⚠️ Datos de cobertura no disponibles. Por favor, procesa la información nuevamente.")
                st.stop()
            
            if not res['gdf_circles_wgs84'].empty:
                c_lat = res['gdf_circles_wgs84']['LATITUD'].mean()
                c_lon = res['gdf_circles_wgs84']['LONGITUD'].mean()
            else:
                c_lat = 23.6345
                c_lon = -102.5528
                
            m = folium.Map(
                location=[c_lat, c_lon],
                zoom_start=6 if res['estado_nombre'] == "Todos" else 10,
                tiles="https://tile.openstreetmap.de/{z}/{x}/{y}.png",
                attr='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            )
            
            # ═══════════════════════════════════════════════════════════════
            # RENDERIZADO DE CAPAS (orden: CPs fondo → Zonas intermedio → Anillos encima)
            # ═══════════════════════════════════════════════════════════════

            # 1. POLÍGONOS DE CPs (fondo)
            gdf_mapa_cp = gdf_cobertura.copy()
            gdf_mapa_cp_wgs84 = gdf_mapa_cp.to_crs("EPSG:4326") if gdf_mapa_cp.crs != "EPSG:4326" else gdf_mapa_cp
            if 'VOLUMEN' not in gdf_mapa_cp_wgs84.columns:
                gdf_mapa_cp_wgs84['VOLUMEN'] = 0
            gdf_mapa_cp_wgs84['VOLUMEN'] = pd.to_numeric(gdf_mapa_cp_wgs84['VOLUMEN'], errors='coerce').fillna(0)
            gdf_mapa_cp_wgs84['_color_hex'] = gdf_mapa_cp_wgs84['VOLUMEN'].apply(lambda v: obtener_color_rango_cp(v)[0])
            gdf_mapa_cp_wgs84['_rango_txt'] = gdf_mapa_cp_wgs84['VOLUMEN'].apply(lambda v: obtener_color_rango_cp(v)[1])

            filtro_cps_activo = st.session_state.get('filtro_cps_activo', [])
            if filtro_cps_activo:
                gdf_mapa_cp_filtrado = gdf_mapa_cp_wgs84[gdf_mapa_cp_wgs84['_rango_txt'].isin(filtro_cps_activo)]
            else:
                gdf_mapa_cp_filtrado = gdf_mapa_cp_wgs84

            if not gdf_mapa_cp_filtrado.empty:
                folium.GeoJson(
                    gdf_mapa_cp_filtrado.to_json(),
                    style_function=lambda feature: {
                        'fillColor': feature['properties'].get('_color_hex', '#9e9e9e'),
                        'color': '#ffffff',
                        'weight': 1.5,
                        'fillOpacity': 0.45
                    },
                    tooltip=folium.GeoJsonTooltip(
                        fields=['CP', 'ESTADO_PERTENECE', 'VOLUMEN', '_rango_txt'],
                        aliases=['Código Postal:', 'Estado:', 'Volumen:', 'Rango:'],
                        localize=True
                    )
                ).add_to(m)

            # 2. CÍRCULOS DE ZONAS (capa intermedia)
            filtro_zonas_activo = st.session_state.get('filtro_zonas_activo', [])
            for _, r in res['gdf_circles_wgs84'].iterrows():
                color_hex, r_text = obtener_color_rango_circulo(r['VOLUMEN'])
                if filtro_zonas_activo and r_text not in filtro_zonas_activo:
                    continue
                geom_circulo = r['geometry']
                cps_bajo_circulo = []
                for _, cp_row in gdf_cobertura.iterrows():
                    if geom_circulo.intersects(cp_row['geometry']):
                        cps_bajo_circulo.append(str(cp_row['CP']))
                txt_cps_atrapados = ", ".join(sorted(list(set(cps_bajo_circulo)))) if cps_bajo_circulo else "Ninguno"
                tt_c = (
                    f"<b>Zona Operativa: {r['NOMBRE']}</b><br>"
                    f"Rango: {r_text}<br>"
                    f"Volumen: {r['VOLUMEN']}<br>"
                    f"Radio Ope: {r['RADIO']}m<br>"
                    f"-------------------------<br>"
                    f"<b>CPs Ocupados Abajo:</b> {txt_cps_atrapados}"
                )
                folium.GeoJson(
                    geom_circulo,
                    style_function=lambda x, col=color_hex: {'fillColor': col, 'color': 'black', 'weight': 1, 'fillOpacity': 0.45},
                    tooltip=tt_c
                ).add_to(m)

            # 3. ANILLOS DE FACTIBILIDAD (encima de todo)
            if st.session_state.get('mostrar_anillos', True) and 'anillos_por_estado' in res:
                for nodo_key, anillos in res['anillos_por_estado'].items():
                    folium.Marker(
                        location=[anillos['centro_lat'], anillos['centro_lon']],
                        icon=folium.Icon(color='purple', icon='crosshairs', prefix='fa'),
                        tooltip=f"Centroide Nodo: {str(nodo_key).upper()}"
                    ).add_to(m)
                    folium.GeoJson(
                        anillos['r15'], 
                        style_function=lambda x: {'fillColor': 'transparent', 'color': '#e74c3c', 'weight': 2, 'dashArray': '5, 5', 'pointerEvents': 'none'}, 
                        tooltip=f"15 km ({nodo_key})"
                    ).add_to(m)
                    folium.GeoJson(
                        anillos['r10'], 
                        style_function=lambda x: {'fillColor': 'transparent', 'color': '#f1c40f', 'weight': 2, 'dashArray': '5, 5', 'pointerEvents': 'none'}, 
                        tooltip=f"10 km ({nodo_key})"
                    ).add_to(m)
                    folium.GeoJson(
                        anillos['r5'], 
                        style_function=lambda x: {'fillColor': 'transparent', 'color': '#2ecc71', 'weight': 2, 'dashArray': '5, 5', 'pointerEvents': 'none'}, 
                        tooltip=f"5 km ({nodo_key})"
                    ).add_to(m)

            m_html = m._repr_html_()
            components.html(m_html, height=600)

            st.write("---")
            st.markdown("### 🖥️ Control de Cobertura (Albers Equal-Area + Lambert Conformal)")
            st.markdown(f"**Filtro de Consulta Activo:** `{res['estado_nombre']}`")
            st.dataframe(res['df_desglose'], use_container_width=True, hide_index=True)

            st.markdown("### 📍 Cobertura y Proximidad de Códigos Postales por Estado")
            st.dataframe(res['df_cp_por_estado'], use_container_width=True, hide_index=True)

            st.markdown("### ⭕ Cobertura Detallada por cada Zona")
            st.dataframe(res['df_cp_por_zona'], use_container_width=True, hide_index=True)
            st.write("---")

            c1, c2 = st.columns(2)
            with c1:
                st.download_button(label="💾 Descargar Mapa HTML", data=m_html, file_name=f"Mapa_{res['estado_nombre']}.html", mime="text/html", use_container_width=True)
            with c2:
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                    res['df_desglose'].to_excel(writer, index=False, sheet_name='Resumen por Estado')
                    res['df_zonas_detalles'].rename(columns={'NOMBRE': 'Nombre de la Zona', 'RADIO': 'Radio (m)', 'VOLUMEN': 'Volumen Registrado', 'AREA_KM2': 'Territorio Ocupado Individual (km²)'}).to_excel(writer, index=False, sheet_name='Zonas Detalles')
                    res['df_cp_por_estado'].to_excel(writer, index=False, sheet_name='CPs por Estado')
                    res['df_cp_por_zona'].to_excel(writer, index=False, sheet_name='CPs por Zona')

                st.download_button(label="📊 Descargar Reporte Excel", data=buf.getvalue(), file_name=f"Reporte_{res['estado_nombre']}.xlsx", mime="application/vnd.ms-excel", use_container_width=True)


elif st.session_state["authentication_status"] is False:
    st.error("Error de acceso: Usuario o contraseña incorrectos.")
