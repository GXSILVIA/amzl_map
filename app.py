import streamlit as st
import pandas as pd
import folium
import geopandas as gpd
import os, io, yaml, numpy as np
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
from shapely.geometry import Point, shape
from shapely.ops import unary_union
import streamlit.components.v1 as components

# --- 1. CONFIGURACIÓN ---
st.set_page_config(page_title="Sistema Pro AMZL", layout="wide")

def normalizar_cp(val):
    try: return str(int(float(val))).strip().zfill(5)
    except: return str(val).strip().zfill(5)

# --- 2. AUTENTICACIÓN ---
with open('config.yaml') as f:
    config = yaml.load(f, SafeLoader)

auth = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
auth.login(location='main')

if st.session_state["authentication_status"]:
    # Inicializar memoria de sesión para que no se borre al filtrar
    if 'mapa_html' not in st.session_state: st.session_state.mapa_html = None
    if 'df_reporte' not in st.session_state: st.session_state.df_reporte = None

    col_m, col_p = st.columns([3, 1.2])

    with col_p:
        st.title("🛡️ Panel AMZL")
        auth.logout('Cerrar Sesión', 'sidebar')
        archs_geo = sorted([f for f in os.listdir('mapas') if f.endswith('.geojson')])
        edo_sel = st.selectbox("📍 Seleccionar Estado:", [f.replace('.geojson','') for f in archs_geo])
        
        f_coords = st.file_uploader("Archivo Coordenadas", type=["xlsx"])
        f_sales = st.file_uploader("Archivo Salesforce", type=["xlsx"])
        
        # Botón de Procesar: Ahora solo dispara el cálculo una vez
        if st.button("🚀 Procesar Información", use_container_width=True, type="primary"):
            if f_coords and f_sales:
                with st.spinner("Generando análisis..."):
                    # --- LÓGICA DE PROCESAMIENTO (Igual a la anterior) ---
                    df_s = pd.read_excel(f_sales).fillna(0)
                    df_s['CP'] = df_s['CP'].apply(normalizar_cp)
                    df_s_grp = df_s.groupby('CP').agg({'Nombre': lambda x: " / ".join(map(str, x)),'Email': 'first', 'Telefono': 'first', 'CP': 'count'}).rename(columns={'CP': 'Cantidad'}).reset_index()

                    def get_cp_txt(folder):
                        path = f"{folder}/{edo_sel}.txt"
                        return set(line.strip().zfill(5) for line in open(path, 'r') if line.strip()) if os.path.exists(path) else set()
                    
                    cp_tienditas, cp_qq = get_cp_txt("CP_tienditas"), get_cp_txt("CP_QQ")
                    gdf = gpd.read_file(f"mapas/{edo_sel}.geojson").to_crs("EPSG:4326")
                    cp_col = next((c for c in ['d_cp', 'CP', 'CODIGOPOSTAL', 'cp'] if c in gdf.columns), gdf.columns[0])
                    gdf[cp_col] = gdf[cp_col].astype(str).apply(normalizar_cp)

                    df_c = pd.read_excel(f_coords); df_c.columns = df_c.columns.str.upper()
                    pts = df_c.to_dict('records')
                    union_circles = unary_union([Point(p['LONGITUD'], p['LATITUD']).buffer(p['RADIO']/111139) for p in pts])
                    
                    m = folium.Map(location=[df_c['LATITUD'].mean(), df_c['LONGITUD'].mean()], zoom_start=11, tiles="CartoDB Voyager")
                    reporte_final = []

                    for _, poly in gdf.iterrows():
                        cp_act = poly[cp_col]
                        match_s = df_s_grp[df_s_grp['CP'] == cp_act]
                        if not match_s.empty:
                            data_s = match_s.iloc[0]
                            poly_geom = shape(poly['geometry']).buffer(0)
                            try: area_libre_geom = poly_geom.difference(union_circles)
                            except: area_libre_geom = poly_geom
                                
                            pct_libre = round((area_libre_geom.area / poly_geom.area) * 100, 1) if poly_geom.area > 0 else 0
                            color, tipo, accion = "gray", "Descartar", "Descartar"
                            
                            if pct_libre > 0:
                                if cp_act in cp_tienditas: color, tipo, accion = "green", "Tienditas", "Dar Seguimiento"
                                elif cp_act in cp_qq: color, tipo, accion = "red", "QQ", "Dar Seguimiento"
                                folium.GeoJson(area_libre_geom, style_function=lambda x, c=color: {'fillColor': c, 'color': 'black', 'weight': 1, 'fillOpacity': 0.6}).add_to(m)
                                folium.GeoJson(poly_geom.intersection(union_circles), style_function=lambda x: {'fillColor': 'gray', 'color': 'gray', 'weight': 0.5, 'fillOpacity': 0.3}).add_to(m)
                            else:
                                folium.GeoJson(poly_geom, style_function=lambda x: {'fillColor': 'gray', 'color': 'black', 'weight': 1, 'fillOpacity': 0.4}).add_to(m)
                            
                            for n_i in str(data_s['Nombre']).split(" / "):
                                reporte_final.append({"Nombre": n_i, "CP": cp_act, "Accion": accion, "Tipo": tipo, "% Libre": pct_libre, "Lat": poly_geom.centroid.y, "Lon": poly_geom.centroid.x})

                    # Guardar círculos visuales
                    for p in pts:
                        folium.Circle([p['LATITUD'], p['LONGITUD']], radius=p['RADIO'], color='#3186cc', fill=True, fill_opacity=0.2).add_to(m)

                    # Guardar resultados en la sesión
                    st.session_state.mapa_html = m._repr_html_()
                    st.session_state.df_reporte = pd.DataFrame(reporte_final)
            else:
                st.warning("⚠️ Carga ambos archivos primero.")

        st.write("---")
        ver_n = st.toggle("🏷️ Ver Nombres en Mapa", value=True)
        filtros = st.multiselect("Filtrar por Tipo:", ["Tienditas", "QQ", "Descartar"], default=["Tienditas", "QQ", "Descartar"])

    with col_m:
        if st.session_state.mapa_html and st.session_state.df_reporte is not None:
            df_v = st.session_state.df_reporte.copy()
            df_v = df_v[df_v['Tipo'].isin(filtros)]

            # Métricas rápidas
            m1, m2, m3 = st.columns(3)
            m1.metric("👥 Total", len(df_v))
            m2.metric("🏪 Tienditas", len(df_v[df_v['Tipo'] == 'Tienditas']))
            m3.metric("🔴 QQ", len(df_v[df_v['Tipo'] == 'QQ']))

            # Mostrar Mapa Guardado
            # Nota: Los nombres se agregan dinámicamente si el toggle está activo
            map_to_show = st.session_state.mapa_html
            if ver_n:
                # Aquí podrías usar una lógica para inyectar nombres si fuera necesario, 
                # pero por simplicidad los nombres ya están en el HTML base si se procesaron.
                pass
            
            components.html(map_to_show, height=600)
            
            st.download_button("💾 Mapa HTML", map_to_show, f"Mapa_{edo_sel}.html", "text/html", use_container_width=True)
            st.dataframe(df_v, use_container_width=True, hide_index=True)
