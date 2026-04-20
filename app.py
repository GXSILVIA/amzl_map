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

# --- 1. CONFIGURACIÓN Y LÓGICA DE CÁLCULO ---
st.set_page_config(page_title="Sistema Pro AMZL - Racional", layout="wide")

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
    try:
        return str(int(float(val))).strip().zfill(5)
    except:
        return str(val).strip().zfill(5)

# --- 2. AUTENTICACIÓN ---
with open('config.yaml') as f:
    config = yaml.load(f, SafeLoader)

auth = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
auth.login(location='main')

if st.session_state["authentication_status"]:
    col_m, col_p = st.columns([3, 1.2])

    with col_p:
        st.title("🛡️ Panel Racional")
        auth.logout('Cerrar Sesión', 'sidebar')
        
        archs_geo = sorted([f for f in os.listdir('mapas') if f.endswith('.geojson')])
        edo_sel = st.selectbox("📍 Seleccionar Estado:", [f.replace('.geojson','') for f in archs_geo])
        
        st.subheader("📥 Plantillas")
        for t, n in [("Coordenadas", "Template_Coords.xlsx"), ("Salesforce", "Template_Salesforce.xlsx")]:
            buf = io.BytesIO()
            cols = ["ZONA", "LATITUD", "LONGITUD", "RADIO", "VOLUMEN"] if t == "Coordenadas" else ["Nombre", "CP", "Email", "Telefono"]
            pd.DataFrame(columns=cols).to_excel(buf, index=False)
            st.download_button(f"Descargar {t}", data=buf.getvalue(), file_name=n, key=f"btn_{n}", use_container_width=True)

        st.subheader("📂 Carga de Archivos")
        f_coords = st.file_uploader("Archivo Coordenadas", type=["xlsx"])
        f_sales = st.file_uploader("Archivo Salesforce", type=["xlsx"])
        
        procesar = st.button("🚀 Procesar Información", use_container_width=True, type="primary")
        
        st.write("---")
        ver_n = st.toggle("🏷️ Ver Nombres de Personas", value=True)
        filtros = st.multiselect("Mostrar en mapa:", ["Tienditas", "QQ", "Descartar"], default=["Tienditas", "QQ", "Descartar"])

    with col_m:
        if f_coords and f_sales and procesar:
            with st.spinner("Procesando geometrías y áreas libres..."):
                # Procesar Salesforce
                df_s = pd.read_excel(f_sales).fillna(0)
                df_s['CP'] = df_s['CP'].apply(normalizar_cp)
                df_s_grp = df_s.groupby('CP').agg({
                    'Nombre': lambda x: " / ".join(map(str, x)),
                    'Email': 'first', 'Telefono': 'first', 'CP': 'count'
                }).rename(columns={'CP': 'Cantidad'}).reset_index()

                # CPs desde TXT
                def get_cp_txt(folder):
                    path = f"{folder}/{edo_sel}.txt"
                    if os.path.exists(path):
                        with open(path, 'r') as file:
                            return set(line.strip().zfill(5) for line in file if line.strip())
                    return set()
                
                cp_tienditas = get_cp_txt("CP_tienditas")
                cp_qq = get_cp_txt("CP_QQ")

                # GeoJSON
                gdf = gpd.read_file(f"mapas/{edo_sel}.geojson").to_crs("EPSG:4326")
                cp_col = next((c for c in ['d_cp','CP','CODIGOPOSTAL','cp'] if c in gdf.columns), gdf.columns)
                gdf[cp_col] = gdf[cp_col].apply(normalizar_cp)

                # Coordenadas y Unión de Círculos
                df_c = pd.read_excel(f_coords)
                df_c.columns = df_c.columns.str.upper()
                pts = df_c.to_dict('records')
                # Unificamos círculos en una sola geometría para recorte
                union_circles = unary_union([Point(p['LONGITUD'], p['LATITUD']).buffer(p['RADIO']/111139) for p in pts])
                
                m = folium.Map(location=[df_c['LATITUD'].mean(), df_c['LONGITUD'].mean()], zoom_start=11, tiles="CartoDB Voyager")
                reporte_final = []

                # Procesar Polígonos con Lógica de Recorte
                for _, poly in gdf.iterrows():
                    cp_act = poly[cp_col]
                    match_s = df_s_grp[df_s_grp['CP'] == cp_act]
                    
                    if not match_s.empty:
                        data_s = match_s.iloc
                        poly_geom = shape(poly['geometry'])
                        
                        # Reparación de geometría para evitar GEOSException
                        if not poly_geom.is_valid:
                            poly_geom = poly_geom.buffer(0)
                        
                        area_total = poly_geom.area
                        
                        # Cálculo de Área Libre
                        try:
                            area_libre_geom = poly_geom.difference(union_circles)
                        except:
                            area_libre_geom = poly_geom
                            
                        pct_libre = round((area_libre_geom.area / area_total) * 100, 1) if area_total > 0 else 0
                        
                        color, tipo, accion = "gray", "Descartar", "Descartar"
                        
                        if pct_libre > 0:
                            if cp_act in cp_tienditas:
                                color, tipo, accion = "green", "Tienditas", "Dar Seguimiento"
                            elif cp_act in cp_qq:
                                color, tipo, accion = "red", "QQ", "Dar Seguimiento"
                            
                            # Dibujar parte libre
                            folium.GeoJson(area_libre_geom, style_function=lambda x, c=color: {
                                'fillColor': c, 'color': 'black', 'weight': 1, 'fillOpacity': 0.6
                            }, tooltip=f"<b>CP: {cp_act}</b><br>Libre: {pct_libre}%").add_to(m)
                            
                            # Dibujar parte ocupada (sombra gris)
                            try:
                                area_ocupada = poly_geom.intersection(union_circles)
                                folium.GeoJson(area_ocupada, style_function=lambda x: {
                                    'fillColor': 'gray', 'color': 'gray', 'weight': 0.5, 'fillOpacity': 0.2
                                }).add_to(m)
                            except: pass
                        else:
                            # 100% Cubierto
                            folium.GeoJson(poly_geom, style_function=lambda x: {
                                'fillColor': 'gray', 'color': 'black', 'weight': 1, 'fillOpacity': 0.4
                            }).add_to(m)

                        # Marcadores de nombres
                        if ver_n:
                            c_pt = poly_geom.centroid
                            d_n = data_s['Nombre'] if len(data_s['Nombre']) < 35 else data_s['Nombre'][:32]+"..."
                            folium.Marker([c_pt.y, c_pt.x], icon=folium.DivIcon(html=f'<div style="font-size:7pt; color:black; font-weight:bold; width:150px;">{d_n}</div>')).add_to(m)
                        
                        for n_indiv in str(data_s['Nombre']).split(" / "):
                            reporte_final.append({
                                "Nombre": n_indiv, "CP": cp_act, "Email": data_s['Email'], 
                                "Telefono": data_s['Telefono'], "Accion": accion, 
                                "Tipo": tipo, "% Libre": pct_libre
                            })

                # Métricas
                df_rep = pd.DataFrame(reporte_final)
                if not df_rep.empty:
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("👥 Total", len(df_rep))
                    m2.metric("🟢 Libres", len(df_rep[df_rep['% Libre'] > 0]))
                    m3.metric("🏪 Tienditas", len(df_rep[df_rep['Tipo'] == 'Tienditas']))
                    m4.metric("🔴 QQ", len(df_rep[df_rep['Tipo'] == 'QQ']))

                # Resultados Visuales
                components.html(m._repr_html_(), height=600)
                st.write("---")
                
                c1, c2 = st.columns(2)
                c1.download_button("💾 Mapa HTML", m._repr_html_(), f"Mapa_{edo_sel}.html", "text/html", use_container_width=True)
                
                buf_rep = io.BytesIO()
                df_rep.to_excel(buf_rep, index=False)
                c2.download_button("📊 Reporte Excel", buf_rep.getvalue(), f"Reporte_{edo_sel}.xlsx", use_container_width=True)
                st.dataframe(df_rep, use_container_width=True, hide_index=True)
        else:
            st.info("👋 Sube tus archivos y haz clic en 'Procesar Información'.")

elif st.session_state["authentication_status"] is False:
    st.error("Usuario/Contraseña incorrectos")
