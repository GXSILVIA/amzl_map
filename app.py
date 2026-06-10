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

# --- 1. CONFIGURACIÓN ---
st.set_page_config(page_title="Sistema Pro AMZL - Cobertura", layout="wide")

def normalizar_cp(val):
    try: return str(int(float(val))).strip().zfill(5)
    except: return str(val).strip().zfill(5)

def obtener_color_rango(volumen):
    try:
        vol = float(volumen)
        if vol <= 15: return "yellow", "🟡 R1-15"
        elif vol <= 20: return "orange", "🟠 R16-20"
        elif vol <= 30: return "red", "🔴 R21-30"
        elif vol <= 40: return "purple", "🟣 R31-40"
        else: return "brown", "🟤 R41+"
    except:
        return "gray", "⚪ Desconocido"

# --- 2. AUTENTICACIÓN ---
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
        
        if not os.path.exists('mapas'):
            st.error("Error: La carpeta 'mapas' no se encuentra en el directorio.")
            st.stop()
            
        archs_geo = sorted([f for f in os.listdir('mapas') if f.endswith('.geojson')])
        estados_disponibles = [f.replace('.geojson','') for f in archs_geo]
        
        # Selección de estado con opción de "Todos"
        opciones_estado = ["Todos"] + estados_disponibles
        edo_sel = st.selectbox("📍 Seleccionar Estado:", opciones_estado)
        
        f_poligonos = st.file_uploader("Archivo Cobertura (ZONA/CP/VOLUMEN)", type=["xlsx"])
        f_zonas = st.file_uploader("Archivo Zonas Círculos (Nombre/Latitud/Longitud/Radio/Volumen)", type=["xlsx"])
        
        if st.button("🚀 Procesar Información", use_container_width=True, type="primary"):
            if f_poligonos and f_zonas:
                with st.spinner("Procesando geometrías y calculando áreas métricas..."):
                    # 1. Cargar archivos del usuario
                    df_poly_user = pd.read_excel(f_poligonos)
                    df_poly_user.columns = df_poly_user.columns.str.upper().str.strip()
                    df_poly_user['CP'] = df_poly_user['CP'].apply(normalizar_cp)
                    
                    df_zonas_user = pd.read_excel(f_zonas)
                    df_zonas_user.columns = df_zonas_user.columns.str.upper().str.strip()
                    
                    # 2. Cargar mapas GeoJSON requeridos
                    estados_a_cargar = estados_disponibles if edo_sel == "Todos" else [edo_sel]
                    gdfs_estados = []
                    for edo in estados_a_cargar:
                        path_geo = f"mapas/{edo}.geojson"
                        if os.path.exists(path_geo):
                            gdf_e = gpd.read_file(path_geo)
                            gdf_e['ESTADO_ORIGEN'] = edo
                            gdfs_estados.append(gdf_e)
                    
                    if not gdfs_estados:
                        st.error("No se pudieron cargar mapas base GeoJSON.")
                        st.stop()
                        
                    gdf_base_completo = pd.concat(gdfs_estados, ignore_index=True)
                    
                    # Identificar la columna CP en el GeoJSON base
                    posibles_cp = ['d_cp', 'CP', 'CODIGOPOSTAL', 'cp']
                    cp_col_geojson = next((c for c in posibles_cp if c in gdf_base_completo.columns), gdf_base_completo.columns)
                    gdf_base_completo[cp_col_geojson] = gdf_base_completo[cp_col_geojson].astype(str).apply(normalizar_cp)
                    
                    # Filtrar GeoJSON conservando solo los CP solicitados en el Excel de Polígonos
                    gdf_cobertura = gdf_base_completo.merge(df_poly_user, left_on=cp_col_geojson, right_on='CP', how='inner')
                    
                    if gdf_cobertura.empty:
                        st.warning("⚠️ No se encontraron coincidencias entre los CPs del Excel y los mapas GeoJSON.")
                        st.stop()
                    
                    # Asegurar SRC geográfico inicial
                    if gdf_cobertura.crs is None:
                        gdf_cobertura.set_crs("EPSG:4326", inplace=True)
                    else:
                        gdf_cobertura = gdf_cobertura.to_crs("EPSG:4326")
                        
                    # 3. Crear Zonas circulares (A partir de coordenadas WGS84)
                    puntos_geometria = []
                    for _, r in df_zonas_user.iterrows():
                        puntos_geometria.append(Point(r['LONGITUD'], r['LATITUD']))
                    
                    gdf_circles = gpd.GeoDataFrame(df_zonas_user, geometry=puntos_geometria, crs="EPSG:4326")
                    
                    # 4. Cambiar a proyección métrica local (EPSG:6362 - UTM 14N para México) para cálculo de áreas m²
                    gdf_cobertura_m = gdf_cobertura.to_crs("EPSG:6362")
                    gdf_circles_m = gdf_circles.to_crs("EPSG:6362")
                    
                    # Buffer en metros usando la columna RADIO del archivo
                    gdf_circles_m['geometry'] = gdf_circles_m.apply(lambda row: row['geometry'].buffer(row['RADIO']), axis=1)
                    
                    # Áreas individuales de cada círculo en m²
                    gdf_circles_m['AREA_M2'] = gdf_circles_m['geometry'].area
                    
                    # Geometría total de Cobertura (Unión de polígonos) y Círculos Ocupados
                    geom_cobertura_total = unary_union(gdf_cobertura_m['geometry'].buffer(0))
                    geom_circulos_total = unary_union(gdf_circles_m['geometry'].buffer(0))
                    
                    # Intersección real: territorio ocupado que está DENTRO de la cobertura
                    geom_ocupada_real = geom_cobertura_total.intersection(geom_circulos_total)
                    geom_libre_real = geom_cobertura_total.difference(geom_circulos_total)
                    
                    # Cálculos finales de áreas globales en metros cuadrados
                    area_cobertura_total_m2 = geom_cobertura_total.area
                    area_ocupada_total_m2 = geom_ocupada_real.area
                    area_libre_total_m2 = geom_libre_real.area
                    
                    # Guardar resultados en el estado de la sesión
                    st.session_state.resultados = {
                        'estado_nombre': edo_sel,
                        'area_cobertura': area_cobertura_total_m2,
                        'area_ocupada': area_ocupada_total_m2,
                        'area_libre': area_libre_total_m2,
                        'gdf_cobertura_wgs84': gdf_cobertura.to_crs("EPSG:4326"),
                        'gdf_circles_wgs84': gdf_circles_m.to_crs("EPSG:4326"),
                        'df_zonas_detalles': gdf_circles_m[['NOMBRE', 'RADIO', 'VOLUMEN', 'AREA_M2']].copy()
                    }
                    st.session_state.procesado = True
            else:
                st.warning("⚠️ Asegúrate de cargar ambos archivos de Excel antes de procesar.")

    with col_m:
        if st.session_state.procesado and st.session_state.resultados is not None:
            res = st.session_state.resultados
            
            # Obtener punto central para enfocar el mapa folium
            centro_lat = res['gdf_circles_wgs84']['LATITUD'].mean() if not res['gdf_circles_wgs84'].empty else 23.6345
            centro_lon = res['gdf_circles_wgs84']['LONGITUD'].mean() if not res['gdf_circles_wgs84'].empty else -102.5528
            
            m = folium.Map(location=[centro_lat, centro_lon], zoom_start=10, tiles="CartoDB Voyager")
            
            # Pintar Polígonos de Cobertura en Azul Transparente
            for _, row in res['gdf_cobertura_wgs84'].iterrows():
                tt = f"<b>ZONA: {row.get('ZONA','S/N')}</b><br>CP: {row['CP']}<br>Volumen: {row.get('VOLUMEN', 0)}"
                folium.GeoJson(
                    row['geometry'],
                    style_function=lambda x: {'fillColor': '#3186cc', 'color': '#1d4f78', 'weight': 1.5, 'fillOpacity': 0.35},
                    tooltip=tt
                ).add_to(m)
                
            # Pintar Círculos de Zonas Ocupadas basados en su Rango de Volumen
            for _, row in res['gdf_circles_wgs84'].iterrows():
                color_hex, rango_txt = obtener_color_rango(row['VOLUMEN'])
                tt_c = f"<b>Zona: {row['NOMBRE']}</b><br>Rango: {rango_txt}<br>Volumen: {row['VOLUMEN']}<br>Radio: {row['RADIO']}m"
                
                folium.GeoJson(
                    row['geometry'],
                    style_function=lambda x, c=color_hex: {'fillColor': c, 'color': 'black', 'weight': 1, 'fillOpacity': 0.55},
                    tooltip=tt_c
                ).add_to(m)
            
            # Desplegar Mapa HTML en la interfaz
            m_html = m._repr_html_()
            components.html(m_html, height=600)
            
            # --- SECCIÓN CONSOLA (Debajo del mapa) ---
            st.write("---")
            st.markdown("### 🖥️ Consola de Control de Territorios")
            st.markdown(f"**Estado Correspondiente:** `{res['estado_nombre']}`")
            st.markdown(f"**Cantidad de Territorio Ocupado:** `{res['area_ocupada']:.2f} m²`")

            st.markdown(f"**Cantidad de Territorio Libre:** `{res['area_libre']:.2f} m²`")
            st.write("---")
            
            # --- EXPORTACIONES Y REPORTES ---
            c1, c2 = st.columns(2)
            c1.download_button(
                label="💾 Descargar Mapa HTML",
                data=m_html,
                file_name=f"Mapa_Cobertura_{res['estado_nombre']}.html",
                mime="text/html",
                use_container_width=True
            )
            
            # Generación de la bitácora consolidada en Excel
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                # Pestaña 1: Resumen General de Áreas
                df_resumen = pd.DataFrame([{
                    "Estado": res['estado_nombre'],
                    "Territorio Cobertura Total (m²)": res['area_cobertura'],
                    "Territorio Ocupado Total (m²)": res['area_ocupada'],
                    "Territorio Libre Total (m²)": res['area_libre']
                }])
                df_resumen.to_excel(writer, index=False, sheet_name='Resumen General')
                
                # Pestaña 2: Desglose por Zonas de círculos individuales
                df_detalles_zonas = res['df_zonas_detalles'].rename(columns={
                    'NOMBRE': 'Nombre de la Zona',
                    'RADIO': 'Radio (m)',
                    'VOLUMEN': 'Volumen Registrado',
                    'AREA_M2': 'Territorio Ocupado Individual (m²)'
                })
                df_detalles_zonas.to_excel(writer, index=False, sheet_name='Ocupación por Zona')
                
            c2.download_button(
                label="📊 Descargar Reporte de Cobertura Excel",
                data=buf.getvalue(),
                file_name=f"Reporte_Areas_{res['estado_nombre']}.xlsx",
                mime="application/vnd.ms-excel",
                use_container_width=True
            )

elif st.session_state["authentication_status"] is False:
    st.error("Error de acceso: Usuario o contraseña incorrectos.")
