import streamlit as st
import pandas as pd
import folium
import geopandas as gpd
import os, io, yaml, numpy as np
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
from shapely.geometry import Point, shape
import streamlit.components.v1 as components

# --- 1. CONFIGURACIÓN Y LÓGICA DE CÁLCULO ---
st.set_page_config(page_title="Sistema Pro AMZL", layout="wide")

@st.cache_data
def area_interseccion(r1, r2, d):
    if d >= r1 + r2: return 0.0
    if d <= abs(r1 - r2): return np.pi * min(r1, r2)**2
    p1 = r1**2 * np.arccos(np.clip((d**2 + r1**2 - r2**2) / (2 * d * r1), -1, 1))
    p2 = r2**2 * np.arccos(np.clip((d**2 + r2**2 - r1**2) / (2 * d * r2), -1, 1))
    p3 = 0.5 * np.sqrt(max(0, (-d + r1 + r2) * (d + r1 - r2) * (d - r1 + r2) * (d + r1 + r2)))
    return p1 + p2 - p3

@st.cache_data
def calcular_traslape_real(p1, otros_pts):
    if not otros_pts: return 0.0
    n = 10000 
    ang = np.random.uniform(0, 2*np.pi, n); rad = np.sqrt(np.random.uniform(0, 1, n)) * p1['RAD']
    m_grado = 111139; cos_lat = np.cos(np.radians(p1['LAT']))
    p_lat = p1['LAT'] + ((rad * np.sin(ang)) / m_grado)
    p_lon = p1['LON'] + ((rad * np.cos(ang)) / (m_grado * cos_lat))
    cubiertos = np.zeros(n, dtype=bool)
    for p2 in otros_pts:
        d2 = ((p_lat - p2['LAT'])**2 + ((p_lon - p2['LON']) * cos_lat)**2) * (m_grado**2)
        cubiertos |= (d2 <= p2['RAD']**2)
        if np.all(cubiertos): break 
    return (np.sum(cubiertos) / n) * 100

def normalizar_cp(val):
    return str(val).split('.')[0].strip().zfill(5)

# --- 2. AUTENTICACIÓN ---
with open('config.yaml') as f:
    config = yaml.load(f, SafeLoader)

auth = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
auth.login(location='main')

if st.session_state["authentication_status"]:
    col_m, col_p = st.columns([3, 1.2])

    with col_p:
        st.title("🛡️ Panel AMZL")
        auth.logout('Cerrar Sesión', 'sidebar')
        
        # Filtro de Estado
        archs_geo = sorted([f for f in os.listdir('mapas') if f.endswith('.geojson')])
        edo_sel = st.selectbox("📍 Seleccionar Estado:", [f.replace('.geojson','') for f in archs_geo])
        
        # Plantillas
        st.subheader("📥 Plantillas")
        c1, c2 = st.columns(2)
        for t, n in [("Coordenadas", "Template_Coords.xlsx"), ("Salesforce", "Template_Salesforce.xlsx")]:
            buf = io.BytesIO()
            cols = ["ZONA", "LATITUD", "LONGITUD", "RADIO", "VOLUMEN"] if t == "Coordenadas" else ["Nombre", "CP", "Email", "Telefono"]
            pd.DataFrame(columns=cols).to_excel(buf, index=False)
            st.download_button(f"Descargar {t}", data=buf.getvalue(), file_name=n, use_container_width=True)

        # Carga de archivos
        f_coords = st.file_uploader("📂 Archivo Coordenadas", type=["xlsx"])
        f_sales = st.file_uploader("📂 Archivo Salesforce", type=["xlsx"])
        
        # Filtros de Mapa
        st.write("---")
        ver_n = st.toggle("🏷️ Ver Nombres/CP", value=True)
        filtros = st.multiselect("Mostrar en mapa:", ["Tienditas", "QQ", "Descartar"], default=["Tienditas", "QQ", "Descartar"])

    with col_m:
        if f_coords and f_sales:
            # Procesar Salesforce: Sumar y listar nombres
            df_s = pd.read_excel(f_sales).fillna(0)
            df_s['CP'] = df_s['CP'].apply(normalizar_cp)
            df_s_grp = df_s.groupby('CP').agg({
                'Nombre': lambda x: ", ".join(map(str, x)),
                'Email': 'first', 'Telefono': 'first', 'CP': 'count'
            }).rename(columns={'CP': 'Cantidad'}).reset_index()

            # Leer Listas de CPs
            def get_cp_list(folder):
                path = f"{folder}/{edo_sel}.csv"
                return set(pd.read_csv(path, dtype=str).iloc[:,0].str.zfill(5)) if os.path.exists(path) else set()
            
            cp_tienditas = get_cp_list("CP_tienditas")
            cp_qq = get_cp_list("CP_QQ")

            # Cargar GeoJSON
            gdf = gpd.read_file(f"mapas/{edo_sel}.geojson").to_crs("EPSG:4326")
            cp_col = next((c for c in ['d_cp','CP','CODIGOPOSTAL'] if c in gdf.columns), gdf.columns[0])
            gdf[cp_col] = gdf[cp_col].apply(normalizar_cp)

            # Cargar Coordenadas y calcular Monte Carlo
            df_c = pd.read_excel(f_coords)
            df_c.columns = df_c.columns.str.upper()
            pts = df_c.to_dict('records')
            circles_geom = []
            
            m = folium.Map(location=[df_c['LATITUD'].mean(), df_c['LONGITUD'].mean()], zoom_start=11, tiles="CartoDB Voyager")
            reporte_final = []

            for p in pts:
                c_geom = Point(p['LONGITUD'], p['LATITUD']).buffer(p['RADIO']/111139)
                circles_geom.append(c_geom)
                folium.Circle([p['LATITUD'], p['LONGITUD']], radius=p['RADIO'], color='blue', fill=True, fill_opacity=0.2).add_to(m)

            # Pintar Polígonos
            for _, poly in gdf.iterrows():
                cp_act = poly[cp_col]
                if cp_act in df_s_grp['CP'].values:
                    data_s = df_s_grp[df_s_grp['CP'] == cp_act].iloc[0]
                    color, tipo, accion = "gray", "Descartar", "Descartar"
                    
                    toca = any(shape(poly['geometry']).intersects(c) for c in circles_geom)
                    
                    if toca: 
                        color = "gray"; tipo = "Capa 1 Contacto"
                    elif cp_act in cp_tienditas:
                        color = "green"; tipo = "Tienditas"; accion = "Dar Seguimiento"
                    elif cp_act in cp_qq:
                        color = "red"; tipo = "QQ"; accion = "Dar Seguimiento"
                    
                    if tipo.replace("Capa 1 Contacto", "Descartar") in filtros:
                        folium.GeoJson(poly['geometry'], style_function=lambda x, c=color: {
                            'fillColor': c, 'color': 'black', 'weight': 1, 'fillOpacity': 0.6
                        }, tooltip=f"CP: {cp_act}<br>Personas: {data_s['Cantidad']}<br>Nombres: {data_s['Nombre']}").add_to(m)
                        
                        if ver_n:
                            c_point = poly['geometry'].centroid
                            folium.Marker([c_point.y, c_point.x], icon=folium.DivIcon(html=f'<div style="font-size:8pt; font-weight:bold;">{cp_act}</div>')).add_to(m)
                        
                        reporte_final.append({
                            "Nombre": data_s['Nombre'], "CP": cp_act, "Email": data_s['Email'],
                            "Telefono": data_s['Telefono'], "Accion": accion, "Tipo": tipo
                        })

            components.html(m._repr_html_(), height=600)
            
            # Botones de descarga debajo del mapa
            st.write("---")
            c1, c2 = st.columns(2)
            c1.download_button("🗺️ Descargar Mapa HTML", m._repr_html_(), f"Mapa_{edo_sel}.html", "text/html")
            
            df_rep = pd.DataFrame(reporte_final)
            buf_rep = io.BytesIO()
            df_rep.to_excel(buf_rep, index=False)
            c2.download_button("📊 Descargar Reporte Excel", buf_rep.getvalue(), f"Reporte_{edo_sel}.xlsx")
            
            st.subheader("📋 Resumen de Datos")
            st.dataframe(df_rep, use_container_width=True)

elif st.session_state["authentication_status"] is False:
    st.error("Usuario/Contraseña incorrectos")
