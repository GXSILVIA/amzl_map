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
with open('config.yaml') as f: config = yaml.load(f, SafeLoader)
auth = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
auth.login(location='main')

if st.session_state["authentication_status"]:
    # INICIALIZAR ESTADOS DE SESIÓN
    if 'procesado' not in st.session_state: st.session_state.procesado = False
    if 'gdf_final' not in st.session_state: st.session_state.gdf_final = None
    if 'pts_coords' not in st.session_state: st.session_state.pts_coords = None

    col_m, col_p = st.columns([3, 1.2])

    with col_p:
        st.title("🛡️ Panel AMZL")
        auth.logout('Cerrar Sesión', 'sidebar')
        archs_geo = sorted([f for f in os.listdir('mapas') if f.endswith('.geojson')])
        edo_sel = st.selectbox("📍 Estado:", [f.replace('.geojson','') for f in archs_geo])
        
        f_coords = st.file_uploader("Archivo Coordenadas", type=["xlsx"])
        f_sales = st.file_uploader("Archivo Salesforce", type=["xlsx"])
        
        if st.button("🚀 Procesar Información", use_container_width=True, type="primary"):
            if f_coords and f_sales:
                with st.spinner("Procesando geometrías..."):
                    # Salesforce
                    df_s = pd.read_excel(f_sales).fillna(0)
                    df_s['CP'] = df_s['CP'].apply(normalizar_cp)
                    df_s_grp = df_s.groupby('CP').agg({'Nombre': lambda x: " / ".join(map(str, x)), 'Email': 'first', 'Telefono': 'first', 'CP': 'count'}).rename(columns={'CP': 'Cantidad'}).reset_index()

                    # Listas TXT
                    def get_cp_txt(folder):
                        path = f"{folder}/{edo_sel}.txt"
                        return set(line.strip().zfill(5) for line in open(path, 'r') if line.strip()) if os.path.exists(path) else set()
                    tienditas, qq = get_cp_txt("CP_tienditas"), get_cp_txt("CP_QQ")

                    # GeoJSON - DETECCIÓN SEGURA DE COLUMNA CP
                    gdf = gpd.read_file(f"mapas/{edo_sel}.geojson").to_crs("EPSG:4326")
                    posibles = ['d_cp', 'CP', 'CODIGOPOSTAL', 'cp', 'codigo_pos']
                    cp_col = next((c for c in posibles if c in gdf.columns), None)
                    
                    if cp_col is None: # Si no encuentra nombre conocido, usa la primera columna
                        cp_col = gdf.columns[0]
                    
                    gdf[cp_col] = gdf[cp_col].astype(str).apply(normalizar_cp)

                    # Coordenadas
                    df_c = pd.read_excel(f_coords); df_c.columns = df_c.columns.str.upper()
                    st.session_state.pts_coords = df_c.to_dict('records')
                    u_circles = unary_union([Point(p['LONGITUD'], p['LATITUD']).buffer(p['RADIO']/111139) for p in st.session_state.pts_coords])

                    # Unión y Lógica Espacial (Merge corregido)
                    gdf = gdf.merge(df_s_grp, left_on=cp_col, right_on='CP', how='inner')
                    
                    def clip_logic(row):
                        g = row['geometry'].buffer(0)
                        lib = g.difference(u_circles)
                        pct = round((lib.area / g.area) * 100, 1) if g.area > 0 else 0
                        tipo = "Descartar"
                        if pct > 0:
                            if row['CP'] in tienditas: tipo = "Tienditas"
                            elif row['CP'] in qq: tipo = "QQ"
                        return pd.Series([tipo, pct, lib, g.intersection(u_circles)])

                    gdf[['Tipo', '% Libre', 'geom_lib', 'geom_ocu']] = gdf.apply(clip_logic, axis=1)
                    st.session_state.gdf_final = gdf
                    st.session_state.procesado = True

        st.write("---")
        ver_n = st.toggle("🏷️ Ver Nombres en Mapa", value=True)
        filtros = st.multiselect("Filtrar:", ["Tienditas", "QQ", "Descartar"], default=["Tienditas", "QQ", "Descartar"])

    with col_m:
        if st.session_state.procesado and st.session_state.gdf_final is not None:
            df_v = st.session_state.gdf_final[st.session_state.gdf_final['Tipo'].isin(filtros)]
            
            # Centro del mapa basado en el primer punto de coordenadas
            c_lat = st.session_state.pts_coords[0]['LATITUD']
            c_lon = st.session_state.pts_coords[0]['LONGITUD']
            m = folium.Map(location=[c_lat, c_lon], zoom_start=11, tiles="CartoDB Voyager")
            
            clrs = {"Tienditas": "green", "QQ": "red", "Descartar": "gray"}

            for _, row in df_v.iterrows():
                tt = f"<b>CP: {row['CP']}</b><br>Personas: {row['Cantidad']}<br>{str(row['Nombre']).replace(' / ', '<br>')}"
                
                folium.GeoJson(row['geom_lib'], style_function=lambda x, c=clrs[row['Tipo']]: {'fillColor': c, 'color': 'black', 'weight': 1, 'fillOpacity': 0.6}, tooltip=tt).add_to(m)
                folium.GeoJson(row['geom_ocu'], style_function=lambda x: {'fillColor': 'gray', 'color': 'gray', 'weight': 0.5, 'fillOpacity': 0.3}).add_to(m)

                if ver_n:
                    c = row['geometry'].centroid
                    folium.Marker([c.y, c.x], icon=folium.DivIcon(html=f'<div style="font-size:7pt; font-weight:bold; width:150px; color:black;">{str(row["Nombre"])[:30]}</div>')).add_to(m)

            for p in st.session_state.pts_coords:
                folium.Circle([p['LATITUD'], p['LONGITUD']], radius=p['RADIO'], color='#3186cc', fill=True, fill_opacity=0.2).add_to(m)

            m_html = m._repr_html_()
            components.html(m_html, height=600)
            
            st.download_button("💾 Descargar Mapa HTML", m_html, f"Mapa_{edo_sel}.html", "text/html")
            st.dataframe(df_v[['Nombre', 'CP', 'Cantidad', 'Tipo', '% Libre']], use_container_width=True, hide_index=True)
