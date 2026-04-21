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
                with st.spinner("Calculando áreas..."):
                    df_s = pd.read_excel(f_sales).fillna(0)
                    df_s['CP'] = df_s['CP'].apply(normalizar_cp)
                    # Agrupamos para el mapa (tooltips), pero el reporte será individual
                    df_s_grp = df_s.groupby('CP').agg({'Nombre': lambda x: " / ".join(map(str, x)), 'CP': 'count'}).rename(columns={'CP': 'Cantidad'}).reset_index()

                    def get_cp_txt(folder):
                        path = f"{folder}/{edo_sel}.txt"
                        return set(line.strip().zfill(5) for line in open(path, 'r') if line.strip()) if os.path.exists(path) else set()
                    
                    tienditas, qq = get_cp_txt("CP_tienditas"), get_cp_txt("CP_QQ")
                    gdf = gpd.read_file(f"mapas/{edo_sel}.geojson").to_crs("EPSG:4326")
                    posibles = ['d_cp', 'CP', 'CODIGOPOSTAL', 'cp']
                    cp_col = next((c for c in posibles if c in gdf.columns), gdf.columns[0])
                    gdf[cp_col] = gdf[cp_col].astype(str).apply(normalizar_cp)

                    df_c = pd.read_excel(f_coords); df_c.columns = df_c.columns.str.upper()
                    st.session_state.pts_coords = df_c.to_dict('records')
                    u_circles = unary_union([Point(p['LONGITUD'], p['LATITUD']).buffer(p['RADIO']/111139) for p in st.session_state.pts_coords])

                    # Unión de datos (Individual para reporte detallado)
                    df_full = df_s.merge(gdf[[cp_col, 'geometry']], left_on='CP', right_on=cp_col, how='inner')
                    
                    def spatial_logic(row):
                        g = row['geometry'].buffer(0)
                        lib = g.difference(u_circles)
                        pct = round((lib.area / g.area) * 100, 1) if g.area > 0 else 0
                        tipo, accion = "Descartar", "Descartar"
                        if pct > 0:
                            if row['CP'] in tienditas: tipo, accion = "Tienditas", "Dar Seguimiento"
                            elif row['CP'] in qq: tipo, accion = "QQ", "Dar Seguimiento"
                        return pd.Series([tipo, accion, pct, lib, g.intersection(u_circles)])

                    df_full[['Tipo', 'Accion', '% Libre', 'geom_lib', 'geom_ocu']] = df_full.apply(spatial_logic, axis=1)
                    st.session_state.gdf_final = df_full
                    st.session_state.procesado = True

        st.write("---")
        ver_n = st.toggle("🏷️ Ver Nombres en Mapa", value=True)
        filtros = st.multiselect("Filtrar:", ["Tienditas", "QQ", "Descartar"], default=["Tienditas", "QQ", "Descartar"])

    with col_m:
        if st.session_state.procesado and st.session_state.gdf_final is not None:
            df_v = st.session_state.gdf_final[st.session_state.gdf_final['Tipo'].isin(filtros)]
            
            # Centro del mapa
            m = folium.Map(location=[st.session_state.pts_coords[0]['LATITUD'], st.session_state.pts_coords[0]['LONGITUD']], zoom_start=11, tiles="CartoDB Voyager")
            clrs = {"Tienditas": "green", "QQ": "red", "Descartar": "gray"}

            # Dibujar polígonos agrupados para evitar duplicidad visual en el mapa
            df_mapa = df_v.groupby('CP').first().reset_index()
            for _, row in df_mapa.iterrows():
                # Buscamos todos los nombres para ese CP en el dataframe filtrado
                nombres_cp = "<br>".join(df_v[df_v['CP'] == row['CP']]['Nombre'].astype(str))
                tt = f"<b>CP: {row['CP']}</b><br>{nombres_cp}"
                
                folium.GeoJson(row['geom_lib'], style_function=lambda x, c=clrs[row['Tipo']]: {'fillColor': c, 'color': 'black', 'weight': 1, 'fillOpacity': 0.6}, tooltip=tt).add_to(m)
                folium.GeoJson(row['geom_ocu'], style_function=lambda x: {'fillColor': 'gray', 'color': 'gray', 'weight': 0.5, 'fillOpacity': 0.3}).add_to(m)

                if ver_n:
                    c = row['geometry'].centroid
                    folium.Marker([c.y, c.x], icon=folium.DivIcon(html=f'<div style="font-size:7pt; font-weight:bold; color:black; width:150px;">{str(row["Nombre"])[:25]}</div>')).add_to(m)

            for p in st.session_state.pts_coords:
                folium.Circle([p['LATITUD'], p['LONGITUD']], radius=p['RADIO'], color='#3186cc', fill=True, fill_opacity=0.2).add_to(m)

            m_html = m._repr_html_()
            components.html(m_html, height=600)
            
            # --- SECCIÓN DE REPORTES Y DESCARGAS ---
            st.write("---")
            c1, c2 = st.columns(2)
            c1.download_button("💾 Descargar Mapa HTML", m_html, f"Mapa_{edo_sel}.html", "text/html", use_container_width=True)
            
            # Informe detallado como estaba originalmente
            reporte_xls = df_v[['Nombre', 'Telefono', 'Email', 'CP', 'Accion', 'Tipo']]
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                reporte_xls.to_excel(writer, index=False, sheet_name='Reporte')
            
            c2.download_button("📊 Descargar Reporte Excel", buf.getvalue(), f"Reporte_{edo_sel}.xlsx", "application/vnd.ms-excel", use_container_width=True)
            
            st.subheader("📋 Informe Detallado")
            st.dataframe(reporte_xls, use_container_width=True, hide_index=True)

elif st.session_state["authentication_status"] is False:
    st.error("Error de acceso")
