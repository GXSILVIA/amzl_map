import streamlit as st
import pandas as pd
import folium
import geopandas as gpd
import os, io, yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
from shapely.geometry import Point
from shapely.ops import unary_union
import streamlit.components.v1 as components

st.set_page_config(page_title="Sistema Pro AMZL - Cobertura", layout="wide")

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
        
        if st.button("🚀 Procesar Información", use_container_width=True, type="primary") and f_poligonos and f_zonas:
            with st.spinner("Calculando áreas y eficiencias..."):
                df_poly_user = pd.read_excel(f_poligonos)
                df_poly_user.columns = df_poly_user.columns.str.upper().str.strip()
                df_poly_user['CP'] = df_poly_user['CP'].apply(normalizar_cp)
                
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
                
                cp_col = next((c for c in ['d_cp', 'CP', 'CODIGOPOSTAL', 'cp'] if c in gdf_base.columns), gdf_base.columns)
                gdf_base[cp_col] = gdf_base[cp_col].astype(str).apply(normalizar_cp)
                
                gdf_cobertura = gdf_base.merge(df_poly_user, left_on=cp_col, right_on='CP', how='inner').set_crs("EPSG:4326", allow_override=True)
                
                if gdf_cobertura.empty:
                    st.warning("⚠️ No se encontraron coincidencias entre los CPs del Excel y los mapas GeoJSON.")
                    st.stop()
                
                # Identificar únicamente los estados que SÍ tienen cobertura en el Excel
                estados_con_cobertura real = gdf_cobertura['ESTADO_PERTENECE'].unique().tolist()
                
                for c in ['LATITUD', 'LONGITUD', 'RADIO', 'VOLUMEN']: df_zonas_user[c] = pd.to_numeric(df_zonas_user[c], errors='coerce')
                df_zonas_user = df_zonas_user.dropna(subset=['LATITUD', 'LONGITUD', 'RADIO'])
                pts = [Point(xy) for xy in zip(df_zonas_user['LONGITUD'], df_zonas_user['LATITUD'])]
                gdf_circles = gpd.GeoDataFrame(df_zonas_user, geometry=pts, crs="EPSG:4326")
                
                gdf_cobertura_m = gdf_cobertura.to_crs("EPSG:6362")
                gdf_circles_m = gdf_circles.to_crs("EPSG:6362")
                gdf_circles_m['geometry'] = gdf_circles_m.apply(lambda r: r['geometry'].buffer(r['RADIO']), axis=1)
                
                gdf_circles_m['AREA_KM2'] = gdf_circles_m['geometry'].area / 1000000.0
                geom_cir_total = unary_union(gdf_circles_m['geometry'].buffer(0))
                
                desglose_estados = []
                for est in estados_con_cobertura_real:
                    sub_cob = gdf_cobertura_m[gdf_cobertura_m['ESTADO_PERTENECE'] == est]
                    if not sub_cob.empty:
                        g_cob_est = unary_union(sub_cob['geometry'].buffer(0))
                        g_ocu_est = g_cob_est.intersection(geom_cir_total)
                        g_lib_est = g_cob_est.difference(geom_cir_total)
                        
                        cob_km2 = g_cob_est.area / 1000000.0
                        ocu_km2 = g_ocu_est.area / 1000000.0
                        lib_km2 = g_lib_est.area / 1000000.0
                        
                        eficiencia = (ocu_km2 / cob_km2 * 100) if cob_km2 > 0 else 0.0
                        
                        desglose_estados.append({
                            "Estado": est,
                            "Territorio Cobertura Total (km²)": round(cob_km2, 4),
                            "Territorio Ocupado Total (km²)": round(ocu_km2, 4),
                            "Territorio Libre Total (km²)": round(lib_km2, 4),
                            "Eficiencia de Ocupación": f"{round(eficiencia, 2)}%"
                        })
                
                df_desglose = pd.DataFrame(desglose_estados)
                
                st.session_state.resultados = {
                    'estado_nombre': edo_sel,
                    'df_desglose': df_desglose,
                    'gdf_cobertura_wgs84': gdf_cobertura.to_crs("EPSG:4326"),
                    'gdf_circles_wgs84': gdf_circles_m.to_crs("EPSG:4326"),
                    'df_zonas_detalles': gdf_circles_m[['NOMBRE', 'RADIO', 'VOLUMEN', 'AREA_KM2']].copy()
                }
                st.session_state.procesado = True

    with col_m:
        if st.session_state.procesado and st.session_state.resultados is not None:
            res = st.session_state.resultados
            c_lat = res['gdf_circles_wgs84']['LATITUD'].mean() if not res['gdf_circles_wgs84'].empty else 23.6345
            c_lon = res['gdf_circles_wgs84']['LONGITUD'].mean() if not res['gdf_circles_wgs84'].empty else -102.5528
            m = folium.Map(location=[c_lat, c_lon], zoom_start=6 if res['estado_nombre'] == "Todos" else 10, tiles="CartoDB Voyager")
            
            for _, r in res['gdf_cobertura_wgs84'].iterrows():
                tt = f"<b>Estado: {r.get('ESTADO_PERTENECE','S/N')}</b><br>ZONA: {r.get('ZONA','S/N')}<br>CP: {r['CP']}<br>Volumen: {r.get('VOLUMEN', 0)}"
                folium.GeoJson(r['geometry'], style_function=lambda x: {'fillColor': '#3186cc', 'color': '#1d4f78', 'weight': 1.5, 'fillOpacity': 0.35}, tooltip=tt).add_to(m)
                
            for _, r in res['gdf_circles_wgs84'].iterrows():
                c_hex, r_txt = obtener_color_rango(r['VOLUMEN'])
                tt_c = f"<b>Zona: {r['NOMBRE']}</b><br>Rango: {r_txt}<br>Volumen: {r['VOLUMEN']}<br>Radio: {r['RADIO']}m"
                folium.GeoJson(r['geometry'], style_function=lambda x, col=c_hex: {'fillColor': col, 'color': 'black', 'weight': 1, 'fillOpacity': 0.55}, tooltip=tt_c).add_to(m)
            
            m_html = m._repr_html_()
            components.html(m_html, height=600)
            
            st.write("---")
            st.markdown("### 🖥️ Consola de Control de Territorios")
            st.markdown(f"**Filtro de Consulta Activo:** `{res['estado_nombre']}`")
            
            st.dataframe(res['df_desglose'], use_container_width=True, hide_index=True)
            st.write("---")
            
            c1, c2 = st.columns(2)
            c1.download_button("💾 Descargar Mapa HTML", m_html, f"Mapa_{res['estado_nombre']}.html", "text/html", use_container_width=True)
            
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                res['df_desglose'].to_excel(writer, index=False, sheet_name='Resumen por Estado')
                res['df_zonas_detalles'].rename(columns={'NOMBRE': 'Nombre de la Zona', 'RADIO': 'Radio (m)', 'VOLUMEN': 'Volumen Registrado', 'AREA_KM2': 'Territorio Ocupado Individual (km²)'}).to_excel(writer, index=False, sheet_name='Ocupación por Zona')
            c2.download_button("📊 Descargar Reporte Excel", buf.getvalue(), f"Reporte_{res['estado_nombre']}.xlsx", "application/vnd.ms-excel", use_container_width=True)

elif st.session_state["authentication_status"] is False:
    st.error("Error de acceso: Usuario o contraseña incorrectos.")
