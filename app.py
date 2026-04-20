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
    try: return str(int(float(val))).strip().zfill(5)
    except: return str(val).strip().zfill(5)

# --- 2. AUTENTICACIÓN ---
with open('config.yaml') as f: config = yaml.load(f, SafeLoader)
auth = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
auth.login(location='main')

if st.session_state["authentication_status"]:
    col_m, col_p = st.columns([3, 1.2])

    with col_p:
        st.title("🛡️ Panel Racional")
        auth.logout('Cerrar Sesión', 'sidebar')
        archs_geo = sorted([f for f in os.listdir('mapas') if f.endswith('.geojson')])
        edo_sel = st.selectbox("📍 Estado:", [f.replace('.geojson','') for f in archs_geo])
        
        f_coords = st.file_uploader("📂 Coordenadas", type=["xlsx"])
        f_sales = st.file_uploader("📂 Salesforce", type=["xlsx"])
        procesar = st.button("🚀 Procesar con Lógica Espacial", use_container_width=True, type="primary")
        
        st.write("---")
        ver_n = st.toggle("🏷️ Ver Nombres", value=True)
        filtros = st.multiselect("Filtros Mapa:", ["Tienditas", "QQ", "Descartar"], default=["Tienditas", "QQ", "Descartar"])

    with col_m:
        if f_coords and f_sales and procesar:
            with st.spinner("Realizando recortes geométricos..."):
                # Cargas Base
                df_s = pd.read_excel(f_sales).fillna(0)
                df_s['CP'] = df_s['CP'].apply(normalizar_cp)
                df_s_grp = df_s.groupby('CP').agg({'Nombre': lambda x: " / ".join(map(str, x)), 'Email': 'first', 'Telefono': 'first', 'CP': 'count'}).rename(columns={'CP': 'Cantidad'}).reset_index()

                def get_cp_txt(folder):
                    path = f"{folder}/{edo_sel}.txt"; return set(line.strip().zfill(5) for line in open(path, 'r') if line.strip()) if os.path.exists(path) else set()
                
                cp_tienditas, cp_qq = get_cp_txt("CP_tienditas"), get_cp_txt("CP_QQ")
                gdf = gpd.read_file(f"mapas/{edo_sel}.geojson").to_crs("EPSG:4326")
                cp_col = next((c for c in ['d_cp','CP','CODIGOPOSTAL','cp'] if c in gdf.columns), gdf.columns[0])
                gdf[cp_col] = gdf[cp_col].apply(normalizar_cp)

                df_c = pd.read_excel(f_coords); df_c.columns = df_c.columns.str.upper()
                pts = df_c.to_dict('records')
                # Unificar todos los círculos en una sola geometría para recorte rápido
                union_circles = unary_union([Point(p['LONGITUD'], p['LATITUD']).buffer(p['RADIO']/111139) for p in pts])
                
                m = folium.Map(location=[df_c['LATITUD'].mean(), df_c['LONGITUD'].mean()], zoom_start=11, tiles="CartoDB Voyager")
                reporte_final = []

                for _, poly in gdf.iterrows():
                    cp_act = poly[cp_col]
                    match_s = df_s_grp[df_s_grp['CP'] == cp_act]
                    
                    if not match_s.empty:
                        data_s = match_s.iloc[0]
                        poly_geom = shape(poly['geometry'])
                        area_total = poly_geom.area
                        
                        # LÓGICA DE RECORTE: Restar círculos al polígono
                        area_libre_geom = poly_geom.difference(union_circles)
                        pct_libre = round((area_libre_geom.area / area_total) * 100, 1) if area_total > 0 else 0
                        
                        color, accion, tipo = "gray", "Descartar", "Descartar"

                        if pct_libre > 0: # Si queda espacio libre
                            if cp_act in cp_tienditas:
                                color, accion, tipo = "green", "Dar Seguimiento", "Tienditas"
                            elif cp_act in cp_qq:
                                color, accion, tipo = "red", "Dar Seguimiento", "QQ"
                            
                            # Pintar solo la parte libre
                            folium.GeoJson(area_libre_geom, style_function=lambda x, c=color: {'fillColor': c, 'color': 'black', 'weight': 1, 'fillOpacity': 0.6},
                                           tooltip=f"<b>CP: {cp_act}</b><br>Libre: {pct_libre}%").add_to(m)
                            # Pintar la parte ocupada en gris claro
                            folium.GeoJson(poly_geom.intersection(union_circles), style_function=lambda x: {'fillColor': 'gray', 'color': 'gray', 'weight': 0.5, 'fillOpacity': 0.2}).add_to(m)
                        else:
                            # 100% cubierto por círculos
                            folium.GeoJson(poly_geom, style_function=lambda x: {'fillColor': 'gray', 'color': 'black', 'weight': 1, 'fillOpacity': 0.4}).add_to(m)

                        if ver_n:
                            c_pt = poly_geom.centroid
                            folium.Marker([c_pt.y, c_pt.x], icon=folium.DivIcon(html=f'<div style="font-size:7pt; font-weight:bold; width:150px;">{data_s["Nombre"][:25]}</div>')).add_to(m)

                        for n_i in str(data_s['Nombre']).split(" / "):
                            reporte_final.append({"Nombre": n_i, "CP": cp_act, "Accion": accion, "Tipo": tipo, "% Libre": pct_libre, "Email": data_s['Email'], "Tel": data_s['Telefono']})

                # Visualización final
                df_rep = pd.DataFrame(reporte_final)
                if not df_rep.empty:
                    c1, c2, c3 = st.columns(3); c1.metric("👥 Total", len(df_rep)); c2.metric("🟢 Libres", len(df_rep[df_rep['% Libre'] > 0])); c3.metric("🔘 Cubiertos", len(df_rep[df_rep['% Libre'] == 0]))
                components.html(m._repr_html_(), height=600)
                st.dataframe(df_rep, use_container_width=True, hide_index=True)
                
                buf = io.BytesIO()
                df_rep.to_excel(buf, index=False)
                st.download_button("📊 Descargar Reporte con % Libre", buf.getvalue(), f"Reporte_Racional_{edo_sel}.xlsx", use_container_width=True)
