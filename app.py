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
def calcular_traslape_real(p1, otros_pts):
    if not otros_pts: return 0.0
    n = 5000 
    ang = np.random.uniform(0, 2*np.pi, n); rad = np.sqrt(np.random.uniform(0, 1, n)) * p1['RADIO']
    m_grado = 111139; cos_lat = np.cos(np.radians(p1['LATITUD']))
    p_lat = p1['LATITUD'] + ((rad * np.sin(ang)) / m_grado)
    p_lon = p1['LONGITUD'] + ((rad * np.cos(ang)) / (m_grado * cos_lat))
    cubiertos = np.zeros(n, dtype=bool)
    for p2 in otros_pts:
        d2 = ((p_lat - p2['LATITUD'])**2 + ((p_lon - p2['LONGITUD']) * cos_lat)**2) * (m_grado**2)
        cubiertos |= (d2 <= p2['RADIO']**2)
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
        
        archs_geo = sorted([f for f in os.listdir('mapas') if f.endswith('.geojson')])
        edo_sel = st.selectbox("📍 Seleccionar Estado:", [f.replace('.geojson','') for f in archs_geo])
        
        st.subheader("📥 Plantillas")
        for t, n in [("Coordenadas", "Template_Coords.xlsx"), ("Salesforce", "Template_Salesforce.xlsx")]:
            buf = io.BytesIO()
            cols = ["ZONA", "LATITUD", "LONGITUD", "RADIO", "VOLUMEN"] if t == "Coordenadas" else ["Nombre", "CP", "Email", "Telefono"]
            pd.DataFrame(columns=cols).to_excel(buf, index=False)
            st.download_button(f"Descargar {t}", data=buf.getvalue(), file_name=n, key=n, use_container_width=True)

        f_coords = st.file_uploader("📂 Archivo Coordenadas", type=["xlsx"])
        f_sales = st.file_uploader("📂 Archivo Salesforce", type=["xlsx"])
        
        ver_n = st.toggle("🏷️ Ver Nombres de Personas", value=True)
        filtros = st.multiselect("Mostrar en mapa:", ["Tienditas", "QQ", "Descartar"], default=["Tienditas", "QQ", "Descartar"])

    with col_m:
        if f_coords and f_sales:
            # Procesar Salesforce
            df_s = pd.read_excel(f_sales).fillna(0)
            df_s['CP'] = df_s['CP'].apply(normalizar_cp)
            df_s_grp = df_s.groupby('CP').agg({
                'Nombre': lambda x: " / ".join(map(str, x)), # Separador para visualización en etiqueta
                'Email': 'first', 'Telefono': 'first', 'CP': 'count'
            }).rename(columns={'CP': 'Cantidad'}).reset_index()

            # Leer Listas de CPs desde archivos TXT
            def get_cp_txt(folder):
                path = f"{folder}/{edo_sel}.txt"
                if os.path.exists(path):
                    with open(path, 'r') as file:
                        return set(line.strip().zfill(5) for line in file if line.strip())
                return set()
            
            cp_tienditas = get_cp_txt("CP_tienditas")
            cp_qq = get_cp_txt("CP_QQ")

            gdf = gpd.read_file(f"mapas/{edo_sel}.geojson").to_crs("EPSG:4326")
            cp_col = next((c for c in ['d_cp','CP','CODIGOPOSTAL'] if c in gdf.columns), gdf.columns[0])
            gdf[cp_col] = gdf[cp_col].apply(normalizar_cp)

            df_c = pd.read_excel(f_coords)
            df_c.columns = df_c.columns.str.upper()
            pts = df_c.to_dict('records')
            circles_geom = [Point(p['LONGITUD'], p['LATITUD']).buffer(p['RADIO']/111139) for p in pts]
            
            m = folium.Map(location=[df_c['LATITUD'].mean(), df_c['LONGITUD'].mean()], zoom_start=11, tiles="CartoDB Voyager")
            reporte_final = []

            for p in pts:
                folium.Circle([p['LATITUD'], p['LONGITUD']], radius=p['RADIO'], color='blue', fill=True, fill_opacity=0.2, tooltip=f"Zona: {p['ZONA']}").add_to(m)

            for _, poly in gdf.iterrows():
                cp_act = poly[cp_col]
                match_s = df_s_grp[df_s_grp['CP'] == cp_act]
                
                if not match_s.empty:
                    data_s = match_s.iloc[0]
                    color, tipo, accion = "gray", "Descartar", "Descartar"
                    
                    toca = any(shape(poly['geometry']).intersects(c) for c in circles_geom)
                    
                    if toca: 
                        color, tipo = "gray", "Descartar"
                    elif cp_act in cp_tienditas:
                        color, tipo, accion = "green", "Tienditas", "Dar Seguimiento"
                    elif cp_act in cp_qq:
                        color, tipo, accion = "red", "QQ", "Dar Seguimiento"
                    
                    if tipo in filtros:
                        # Reemplazar ' / ' por '<br>' para el tooltip HTML
                        tooltip_names = data_s['Nombre'].replace(" / ", "<br>")
                        folium.GeoJson(poly['geometry'], style_function=lambda x, c=color: {
                            'fillColor': c, 'color': 'black', 'weight': 1, 'fillOpacity': 0.6
                        }, tooltip=f"<b>CP: {cp_act}</b><br>Personas: {data_s['Cantidad']}<br>{tooltip_names}").add_to(m)
                        
                        if ver_n:
                            c_point = poly['geometry'].centroid
                            # Muestra los nombres directamente en el mapa (ajustado para que no sea gigante)
                            display_names = data_s['Nombre'] if len(data_s['Nombre']) < 30 else data_s['Nombre'][:27]+"..."
                            folium.Marker([c_point.y, c_point.x], icon=folium.DivIcon(html=f'<div style="font-size:7pt; color:black; font-weight:bold; width:150px;">{display_names}</div>')).add_to(m)
                        
                        for nom_indiv in data_s['Nombre'].split(" / "):
                            reporte_final.append({
                                "Nombre": nom_indiv, "CP": cp_act, "Email": data_s['Email'],
                                "Telefono": data_s['Telefono'], "Accion": accion, "Tipo": tipo
                            })

            components.html(m._repr_html_(), height=600)
            
            st.write("---")
            c1, c2 = st.columns(2)
            c1.download_button("💾 Descargar Mapa HTML", m._repr_html_(), f"Mapa_{edo_sel}.html", "text/html", use_container_width=True)
            df_rep = pd.DataFrame(reporte_final)
            buf_rep = io.BytesIO()
            df_rep.to_excel(buf_rep, index=False)
            c2.download_button("📊 Descargar Reporte Excel", buf_rep.getvalue(), f"Reporte_{edo_sel}.xlsx", use_container_width=True)
            st.dataframe(df_rep, use_container_width=True)

elif st.session_state["authentication_status"] is False:
    st.error("Usuario/Contraseña incorrectos")
