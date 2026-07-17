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

st.set_page_config(page_title="Sistema Pro AMZL - Cobertura Montecarlo", layout="wide")

def normalizar_cp(v):
    try: return str(int(float(v))).strip().zfill(5)
    except: return str(v).strip().zfill(5)

def obtener_color_rango(v):
    try:
        vol = float(v)
        if vol <= 15: return "yellow", "🟡 R1-15"
        elif vol <= 20: return "orange", "🟠 R16-20"
        elif vol <= 30: return "red", "🔴 R21-30"
        elif vol <= 40: return "purple", "🟣 R31-40"
        else: return "brown", "🟤 R41+"
    except: return "gray", "⚪ Desconocido"

with open('config.yaml') as f: config = yaml.load(f, SafeLoader)
auth = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
auth.login(location='main')

if st.session_state["authentication_status"]:
    if 'procesado' not in st.session_state: st.session_state.procesado = False
    if 'resultados' not in st.session_state: st.session_state.resultados = None

    col_m, col_p = st.columns([3, 1.2])

    with col_p:
        st.title("🛡️ Panel AMZL")
        auth.logout('Cerrar Sesión', 'sidebar')
        if not os.path.exists('mapas'): st.error("Falta carpeta mapas"); st.stop()
        archs_geo = sorted([f for f in os.listdir('mapas') if f.endswith('.geojson')])
        estados_disponibles = [f.replace('.geojson','') for f in archs_geo]
        edo_sel = st.selectbox("📍 Seleccionar Estado:", ["Todos"] + estados_disponibles)
        f_poligonos = st.file_uploader("Archivo Cobertura (ZONA/CP/VOLUMEN)", type=["xlsx"])
        f_zonas = st.file_uploader("Archivo Zonas Círculos (Nombre/Latitud/Longitud/Radio/Volumen)", type=["xlsx"])
        
        # Parámetro deslizable para controlar la densidad del muestreo estadístico
        n_simulaciones = st.slider("🎯 Puntos de Muestreo Montecarlo:", 2000, 20000, 10000, 2000)
        
        if st.button("🚀 Procesar Información", use_container_width=True, type="primary") and f_poligonos and f_zonas:
            with st.spinner("Ejecutando simulación estadística sobre la cobertura..."):
                df_poly_user = pd.read_excel(f_poligonos)
                df_poly_user.columns = df_poly_user.columns.str.upper().str.strip()
                df_poly_user['CP'] = df_poly_user['CP'].apply(normalizar_cp)
                df_poly_user = df_poly_user.drop_duplicates(subset=['CP'])
                
                df_zonas_user = pd.read_excel(f_zonas)
                df_zonas_user.columns = df_zonas_user.columns.str.strip()
                mapa_cols = {c: c.upper() for c in df_zonas_user.columns if c.upper() in ['NOMBRE', 'LATITUD', 'LONGITUD', 'RADIO', 'VOLUMEN']}
                df_zonas_user = df_zonas_user.rename(columns=mapa_cols)
                
                estados_a_cargar = estados_disponibles if edo_sel == "Todos" else [edo_sel]
                gdfs = []
                for e in estados_a_cargar:
                    p = os.path.join("mapas", f"{e}.geojson")
                    if os.path.exists(p):
                        g = gpd.read_file(p)
                        g['ESTADO_PERTENECE'] = e
                        gdfs.append(g)
                        
                gdf_base = pd.concat(gdfs, ignore_index=True)
                cp_col = next((c for c in ['d_codigo', 'd_cp', 'CP', 'CODIGOPOSTAL', 'cp'] if c in gdf_base.columns), gdf_base.columns)
                gdf_base[cp_col] = gdf_base[cp_col].astype(str).apply(normalizar_cp)
                
                gdf_cobertura = gdf_base.merge(df_poly_user, left_on=cp_col, right_on='CP', how='inner').set_crs("EPSG:4326", allow_override=True)
                
                if gdf_cobertura.empty:
                    st.warning("⚠️ No se encontraron coincidencias entre los CPs del Excel y los mapas GeoJSON.")
                    st.stop()
                
                gdf_cobertura = gdf_cobertura.drop_duplicates(subset=['CP'])
                estados_con_cobertura_real = gdf_cobertura['ESTADO_PERTENECE'].unique().tolist()
                
                for c in ['LATITUD', 'LONGITUD', 'RADIO', 'VOLUMEN']: df_zonas_user[c] = pd.to_numeric(df_zonas_user[c], errors='coerce')
                df_zonas_user = df_zonas_user.dropna(subset=['LATITUD', 'LONGITUD', 'RADIO'])
                pts = [Point(xy) for xy in zip(df_zonas_user['LONGITUD'], df_zonas_user['LATITUD'])]
                gdf_circles = gpd.GeoDataFrame(df_zonas_user, geometry=pts, crs="EPSG:4326")
                
                gdf_cobertura_m = gdf_cobertura.to_crs("EPSG:6362")
                gdf_circles_m = gdf_circles.to_crs("EPSG:6362")
                gdf_circles_m['geometry'] = gdf_circles_m.apply(lambda r: r['geometry'].buffer(r['RADIO']), axis=1)
                
                gdf_circles_m['AREA_KM2'] = gdf_circles_m['geometry'].area / 1000000.0
                geom_cir_total = unary_union(gdf_circles_m['geometry'].buffer(0))
                
                # =========================================================================
                # 📊 AUDITORÍA DE CPS CUBIERTOS Y FACTIBILIDAD AMPLIADA POR DISTANCIA
                # =========================================================================
                reporte_cp_por_zona = []
                reporte_cp_por_estado = []
                
                # Clonamos para el análisis espacial
                gdf_circles_m_corr = gdf_circles_m.copy()
                
                for idx, row in gdf_circles_m_corr.iterrows():
                    if row['RADIO'] < 100:
                        base_geom = gdf_circles[gdf_circles['NOMBRE'] == row['NOMBRE']]['geometry'].to_crs("EPSG:6362").iloc
                        gdf_circles_m_corr.at[idx, 'geometry'] = base_geom.buffer(row['RADIO'] * 1000)
                
                for est in estados_con_cobertura_real:
                    sub_cob = gdf_cobertura_m[gdf_cobertura_m['ESTADO_PERTENECE'] == est]
                    if not sub_cob.empty:
                        g_cob_est = unary_union(sub_cob['geometry'].buffer(0))
                        zonas_del_estado = gdf_circles_m_corr[gdf_circles_m_corr['geometry'].intersects(g_cob_est)]
                        
                        cps_cubiertos_estado = set()
                        cps_factibles_inmediatos = set()
                        cps_factibles_moderados = set()
                        cps_complejos = set()
                        cps_totalmente_faltantes = set()
                        
                        if not zonas_del_estado.empty:
                            union_zonas_est = unary_union(zonas_del_estado['geometry'])
                            
                            # Creamos los anillos de expansión/distancia a la redonda (en metros)
                            buffer_inmediato = union_zonas_est.buffer(5000)   # +5 KM
                            buffer_moderado = union_zonas_est.buffer(15000)  # +15 KM
                            buffer_complejo = union_zonas_est.buffer(30000)  # +30 KM
                            
                            for _, cp_row in sub_cob.iterrows():
                                geom_cp = cp_row['geometry']
                                centroide_cp = geom_cp.centroid
                                
                                # Evaluar nivel de cobertura/proximidad
                                if union_zonas_est.intersects(geom_cp) or union_zonas_est.contains(centroide_cp):
                                    cps_cubiertos_estado.add(cp_row['CP'])
                                elif buffer_inmediato.contains(centroide_cp):
                                    cps_factibles_inmediatos.add(cp_row['CP'])
                                elif buffer_moderado.contains(centroide_cp):
                                    cps_factibles_moderados.add(cp_row['CP'])
                                elif buffer_complejo.contains(centroide_cp):
                                    cps_complejos.add(cp_row['CP'])
                                else:
                                    cps_totalmente_faltantes.add(cp_row['CP'])
                        else:
                            # Si no hay ninguna zona en este estado, todos los CPs están totalmente fuera
                            cps_totalmente_faltantes = set(sub_cob['CP'].tolist())
                        
                        # 1. Análisis General por Estado (4 Renglones con orden estricto de columnas)
                        reporte_cp_por_estado.append({
                            "Estado": est, "Estatus": "Cubierto",
                            "CP": ", ".join(sorted(list(cps_cubiertos_estado))) if cps_cubiertos_estado else "Ninguno"
                        })
                        reporte_cp_por_estado.append({
                            "Estado": est, "Estatus": "Factible Inmediato (<5km)",
                            "CP": ", ".join(sorted(list(cps_factibles_inmediatos))) if cps_factibles_inmediatos else "Ninguno"
                        })
                        reporte_cp_por_estado.append({
                            "Estado": est, "Estatus": "Factible Moderado (5-15km)",
                            "CP": ", ".join(sorted(list(cps_factibles_moderados))) if cps_factibles_moderados else "Ninguno"
                        })
                        reporte_cp_por_estado.append({
                            "Estado": est, "Estatus": "Falta por Cubrir (>15km)",
                            "CP": ", ".join(sorted(list(cps_complejos.union(cps_totalmente_faltantes)))) if (cps_complejos or cps_totalmente_faltantes) else "Ninguno"
                        })
                        
                        # 2. Análisis Específico por cada Zona (Muestra los CPs actuales + los factibles a la redonda < 15km)
                        for _, zona_row in gdf_circles_m_corr.iterrows():
                            if zona_row['geometry'].intersects(g_cob_est):
                                cps_actuales_zona = []
                                cps_potenciales_zona = []
                                
                                # Buffer individual de expansión de factibilidad (15 KM a la redonda de la zona)
                                area_factible_zona = zona_row['geometry'].buffer(15000)
                                
                                for _, cp_row in sub_cob.iterrows():
                                    geom_cp = cp_row['geometry']
                                    centroide_cp = geom_cp.centroid
                                    
                                    if zona_row['geometry'].intersects(geom_cp) or zona_row['geometry'].contains(centroide_cp):
                                        cps_actuales_zona.append(cp_row['CP'])
                                    elif area_factible_zona.contains(centroide_cp):
                                        cps_potenciales_zona.append(cp_row['CP'])
                                
                                txt_actuales = ", ".join(sorted(list(set(cps_actuales_zona)))) if cps_actuales_zona else "Ninguno"
                                txt_potenciales = ", ".join(sorted(list(set(cps_potenciales_zona)))) if cps_potenciales_zona else "Ninguno"
                                
                                reporte_cp_por_zona.append({
                                    "Zona": zona_row['NOMBRE'],
                                    "Estado": est,
                                    "CPs Cubiertos": txt_actuales,
                                    "CPs Factibles Ext. (<15km)": txt_potenciales
                                })
                
                df_cp_por_estado = pd.DataFrame(reporte_cp_por_estado)
                df_cp_por_zona = pd.DataFrame(reporte_cp_por_zona)
                
                if not df_cp_por_estado.empty:
                    df_cp_por_estado = df_cp_por_estado[["Estado", "Estatus", "CP"]]
                # =========================================================================

                
                desglose_estados = []
                for est in estados_con_cobertura_real:
                    sub_cob = gdf_cobertura_m[gdf_cobertura_m['ESTADO_PERTENECE'] == est]
                    if not sub_cob.empty:
                        g_cob_est = unary_union(sub_cob['geometry'].buffer(0))
                        
                        minx, miny, maxx, maxy = g_cob_est.bounds
                        area_caja_cobertura = (maxx - minx) * (maxy - miny)
                        
                        x_rand = np.random.uniform(minx, maxx, n_simulaciones)
                        y_rand = np.random.uniform(miny, maxy, n_simulaciones)
                        puntos_simulados = [Point(x, y) for x, y in zip(x_rand, y_rand)]
                        
                        puntos_en_cobertura = sum(1 for p in puntos_simulados if g_cob_est.contains(p))
                        puntos_en_ocupacion = sum(1 for p in puntos_simulados if g_cob_est.contains(p) and geom_cir_total.contains(p))
                        
                        cob_km2 = (puntos_en_cobertura / n_simulaciones) * (area_caja_cobertura / 1000000.0)
                        ocu_km2 = (puntos_en_ocupacion / n_simulaciones) * (area_caja_cobertura / 1000000.0)
                        lib_km2 = max(0.0, cob_km2 - ocu_km2)
                        
                        if ocu_km2 > 0:
                            eficiencia = (ocu_km2 / cob_km2 * 100) if cob_km2 > 0 else 0.0
                            desglose_estados.append({
                                "Estado": est,
                                "Territorio Cobertura Total (km²)": round(cob_km2, 4),
                                "Territorio Ocupado Total (km²)": round(ocu_km2, 4),
                                "Territorio Libre Total (km²)": round(lib_km2, 4),
                                "Eficiencia de Ocupación": f"{round(eficiencia, 2)}%"
                            })
                
                df_desglose = pd.DataFrame(desglose_estados)
                if df_desglose.empty:
                    df_desglose = pd.DataFrame(columns=["Estado", "Territorio Cobertura Total (km²)", "Territorio Ocupado Total (km²)", "Territorio Libre Total (km²)", "Eficiencia de Ocupación"])
                
                estados_validos = df_desglose['Estado'].unique().tolist()
                gdf_cobertura_filtrada = gdf_cobertura[gdf_cobertura['ESTADO_PERTENECE'].isin(estados_validos)]
                
                st.session_state.resultados = {
                    'estado_nombre': edo_sel,
                    'df_desglose': df_desglose,
                    'gdf_cobertura_wgs84': gdf_cobertura_filtrada.to_crs("EPSG:4326"),
                    'gdf_circles_wgs84': gdf_circles_m.to_crs("EPSG:4326"),
                    'df_zonas_detalles': gdf_circles_m[['NOMBRE', 'RADIO', 'VOLUMEN', 'AREA_KM2']].copy(),
                    'df_cp_por_estado': df_cp_por_estado,  # Integrado sin romper estructura
                    'df_cp_por_zona': df_cp_por_zona      # Integrado sin romper estructura
                }
                st.session_state.procesado = True

        with col_m:
        if st.session_state.procesado and st.session_state.resultados is not None:
            res = st.session_state.resultados
            c_lat = res['gdf_circles_wgs84']['LATITUD'].mean() if not res['gdf_circles_wgs84'].empty else 23.6345
            c_lon = res['gdf_circles_wgs84']['LONGITUD'].mean() if not res['gdf_circles_wgs84'].empty else -102.5528
            m = folium.Map(location=[c_lat, c_lon], zoom_start=6 if res['estado_nombre'] == "Todos" else 10, tiles="CartoDB Voyager")
            
            # 1. Dibujamos los polígonos de los Códigos Postales
            for _, r in res['gdf_cobertura_wgs84'].iterrows():
                tt = f"<b>Estado: {r.get('ESTADO_PERTENECE','S/N')}</b><br>ZONA: {r.get('ZONA','S/N')}<br>CP: {r['CP']}<br>Volumen: {r.get('VOLUMEN', 0)}"
                folium.GeoJson(r['geometry'], style_function=lambda x: {'fillColor': '#3186cc', 'color': '#1d4f78', 'weight': 1.5, 'fillOpacity': 0.35}, tooltip=tt).add_to(m)
                
            # 2. Dibujamos los círculos operativos actuales
            for _, r in res['gdf_circles_wgs84'].iterrows():
                color_hex, r_txt = obtener_color_rango(r['VOLUMEN'])
                tt_c = f"<b>Zona Operativa: {r['NOMBRE']}</b><br>Rango: {r_txt}<br>Volumen: {r['VOLUMEN']}<br>Radio Ope: {r['RADIO']}m"
                folium.GeoJson(
                    r['geometry'], 
                    style_function=lambda x, col=color_hex: {'fillColor': col, 'color': 'black', 'weight': 1, 'fillOpacity': 0.55}, 
                    tooltip=tt_c
                ).add_to(m)

                # 🗺️ NUEVO EN EL MAPA: Dibujamos visualmente el radio de Factibilidad Extendida (15 km) a la redonda de cada zona
                # Pasamos la geometría métrica + 15km a WGS84 para Folium
                geom_factible_w84 = gpd.GeoSeries([r['geometry']], crs="EPSG:4326").to_crs("EPSG:6362").buffer(15000).to_crs("EPSG:4326").iloc[0]
                folium.GeoJson(
                    geom_factible_w84,
                    style_function=lambda x: {'fillColor': 'transparent', 'color': '#2ecc71', 'weight': 1.5, 'dashArray': '6, 6'},
                    tooltip=f"<b>Límite Factible Extendida (15km)</b><br>Zona: {r['NOMBRE']}"
                ).add_to(m)
            
            m_html = m._repr_html_()
            components.html(m_html, height=600)
            
            st.write("---")
            st.markdown("### 🖥️ Consola de Control de Territorios (Muestreo de Cobertura Montecarlo)")
            st.markdown(f"**Filtro de Consulta Activo:** `{res['estado_nombre']}`")
            st.dataframe(res['df_desglose'], use_container_width=True, hide_index=True)
            
            st.markdown("### 📍 Cobertura y Proximidad de Códigos Postales por Estado")
            st.dataframe(res['df_cp_por_estado'], use_container_width=True, hide_index=True)
            
            st.markdown("### ⭕ Cobertura y Factibilidad Detallada por cada Zona")
            st.dataframe(res['df_cp_por_zona'], use_container_width=True, hide_index=True)
            st.write("---")
            
            c1, c2 = st.columns(2)
            with c1:
                st.download_button(
                    label="💾 Descargar Mapa HTML",
                    data=m_html,
                    file_name=f"Mapa_{res['estado_nombre']}.html",
                    mime="text/html",
                    use_container_width=True
                )
            with c2:
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                    res['df_desglose'].to_excel(writer, index=False, sheet_name='Resumen por Estado')
                    res['df_zonas_detalles'].rename(columns={'NOMBRE': 'Nombre de la Zona', 'RADIO': 'Radio (m)', 'VOLUMEN': 'Volumen Registrado', 'AREA_KM2': 'Territorio Ocupado Individual (km²)'}).to_excel(writer, index=False, sheet_name='Ocupación por Zona')
                    
                    # 📊 VISIBLE EN EL EXCEL: Hojas actualizadas con las nuevas columnas y clasificaciones por distancia
                    res['df_cp_por_estado'].to_excel(writer, index=False, sheet_name='CPs por Estado')
                    res['df_cp_por_zona'].to_excel(writer, index=False, sheet_name='CPs por Zona')
                    
                st.download_button(
                    label="📊 Descargar Reporte Excel",
                    data=buf.getvalue(),
                    file_name=f"Reporte_{res['estado_nombre']}.xlsx",
                    mime="application/vnd.ms-excel",
                    use_container_width=True
                )

elif st.session_state["authentication_status"] is False:
    st.error("Error de acceso: Usuario o contraseña incorrectos.")
