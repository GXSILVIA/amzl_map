import streamlit as st
import pandas as pd
import folium
import geopandas as gpd
import os, io, numpy as np
from shapely.geometry import Point, shape
from datetime import datetime

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Sistema Pro AMZL - Capa 1 & Salesforce", layout="wide")

# --- LÓGICA MONTE CARLO Y PRORRATEO (PARTE 1) ---
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
    n = 5000 # Reducido ligeramente para optimizar velocidad en web
    ang = np.random.uniform(0, 2*np.pi, n)
    rad = np.sqrt(np.random.uniform(0, 1, n)) * p1['RAD']
    m_grado = 111139
    cos_lat = np.cos(np.radians(p1['LAT']))
    p_lat = p1['LAT'] + ((rad * np.sin(ang)) / m_grado)
    p_lon = p1['LON'] + ((rad * np.cos(ang)) / (m_grado * cos_lat))
    cubiertos = np.zeros(n, dtype=bool)
    for p2 in otros_pts:
        d2 = ((p_lat - p2['LAT'])**2 + ((p_lon - p2['LON']) * cos_lat)**2) * (m_grado**2)
        cubiertos |= (d2 <= p2['RAD']**2)
        if np.all(cubiertos): break 
    return (np.sum(cubiertos) / n) * 100

# --- FUNCIONES DE APOYO ---
def normalizar_cp(val):
    return str(val).split('.')[0].zfill(5)

def generar_plantilla(tipo):
    if tipo == "Coordenadas":
        df = pd.DataFrame(columns=["ZONA", "LATITUD", "LONGITUD", "RADIO", "VOLUMEN"])
    else:
        df = pd.DataFrame(columns=["Nombre", "CP", "Email", "Telefono"])
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False)
    return output.getvalue()

# --- INTERFAZ ---
col_m, col_p = st.columns([3, 1])

with col_p:
    st.title("🛡️ Panel Control")
    
    # 1. Filtro de Estado
    archs_geo = sorted([f for f in os.listdir('mapas') if f.endswith('.geojson')])
    edo_sel = st.selectbox("📍 Estado:", [f.replace('.geojson','') for f in archs_geo])
    
    # 2. Descarga de Plantillas
    st.subheader("📥 Plantillas")
    st.download_button("Plantilla Coordenadas", generar_plantilla("Coordenadas"), "plantilla_coords.xlsx")
    st.download_button("Plantilla Salesforce", generar_plantilla("Salesforce"), "plantilla_salesforce.xlsx")
    
    # 3. Carga de Archivos
    file_coords = st.file_uploader("📂 Coordenadas (Excel)", type=["xlsx"])
    file_sales = st.file_uploader("📂 Salesforce (Excel)", type=["xlsx"])
    
    # Filtros de Mapa
    st.subheader("👁️ Visualización")
    ver_n = st.toggle("Mostrar Nombres", value=True)
    f_tienditas = st.checkbox("Tienditas (Verde)", True)
    f_qq = st.checkbox("QQ (Rojo)", True)
    f_descarta = st.checkbox("Descartar (Gris)", True)

# --- PROCESAMIENTO ---
if file_coords and file_sales:
    df_c = pd.read_excel(file_coords)
    df_s = pd.read_excel(file_sales).fillna(0)
    
    # Agrupar Salesforce
    df_s['CP'] = df_s['CP'].apply(normalizar_cp)
    df_s_grp = df_s.groupby('CP').agg({
        'Nombre': lambda x: list(x),
        'Email': 'first',
        'Telefono': 'first',
        'CP': 'count' # Usamos el conteo como cantidad de personas
    }).rename(columns={'CP': 'Cantidad'}).reset_index()

    # Cargar Listas de Cobertura
    try:
        cp_tienditas = pd.read_csv(f"CP_tienditas/{edo_sel}.csv")['CP'].apply(normalizar_cp).tolist()
        cp_qq = pd.read_csv(f"CP_QQ/{edo_sel}.csv")['CP'].apply(normalizar_cp).tolist()
    except:
        cp_tienditas, cp_qq = [], []

    # Cargar GeoJSON
    gdf = gpd.read_file(f"mapas/{edo_sel}.geojson")
    col_cp_geo = next((c for c in ['d_cp','CP','CODIGOPOSTAL'] if c in gdf.columns), gdf.columns[0])
    gdf[col_cp_geo] = gdf[col_cp_geo].apply(normalizar_cp)

    # Mapa Folium
    m = folium.Map(location=[19.4, -99.1], zoom_start=10, tiles="CartoDB Voyager")
    reporte = []

    # Procesar Coordenadas (Capa 1)
    pts = df_c.to_dict('records')
    circulos_shapes = []
    for p in pts:
        c_shape = Point(p['LONGITUD'], p['LATITUD']).buffer(p['RADIO']/111139)
        circulos_shapes.append(c_shape)
        folium.Circle([p['LATITUD'], p['LONGITUD']], radius=p['RADIO'], color='blue', fill=True, opacity=0.3).add_to(m)

    # Procesar Polígonos
    for _, poly in gdf.iterrows():
        cp_actual = poly[col_cp_geo]
        match_s = df_s_grp[df_s_grp['CP'] == cp_actual]
        
        if not match_s.empty:
            row_s = match_s.iloc[0]
            color = "gray"
            accion = "Descartar"
            tipo = "N/A"
            
            # Lógica de contacto con círculos
            toca_circulo = any(shape(poly['geometry']).intersects(c) for c in circulos_shapes)
            
            if toca_circulo:
                color = "gray"
            elif cp_actual in cp_tienditas:
                color = "green"; accion = "Dar Seguimiento"; tipo = "Tienditas"
            elif cp_actual in cp_qq:
                color = "red"; accion = "Dar Seguimiento"; tipo = "QQ"
            
            # Filtro visual
            if (color == "green" and f_tienditas) or (color == "red" and f_qq) or (color == "gray" and f_descarta):
                names_list = "<br>".join([str(n) for n in row_s['Nombre']])
                folium.GeoJson(
                    poly['geometry'],
                    style_function=lambda x, c=color: {'fillColor': c, 'color': 'black', 'weight': 1, 'fillOpacity': 0.5},
                    tooltip=f"CP: {cp_actual}<br>Personas: {row_s['Cantidad']}<br>Nombres:<br>{names_list}"
                ).add_to(m)
                
                if ver_n:
                    centro = poly['geometry'].centroid
                    folium.Marker([centro.y, centro.x], icon=folium.features.DivIcon(html=f'<div style="font-size:7pt;">{cp_actual}</div>')).add_to(m)
                
                # Agregar al reporte
                for n in row_s['Nombre']:
                    reporte.append({
                        "Nombre": n, "CP": cp_actual, "Email": row_s['Email'], 
                        "Telefono": row_s['Telefono'], "Accion": accion, "Tipo": tipo
                    })

    with col_m:
        mapa_html = m.get_root().render()
        components.html(mapa_html, height=600)
        
        st.subheader("📋 Reporte de Cobertura")
        df_rep = pd.DataFrame(reporte)
        st.dataframe(df_rep, use_container_width=True)
        
        # Botones descarga
        c1, c2 = st.columns(2)
        c1.download_button("💾 Descargar Mapa HTML", data=mapa_html, file_name=f"Mapa_{edo_sel}.html")
        
        buf_ex = io.BytesIO()
        df_rep.to_excel(buf_ex, index=False)
        c2.download_button("📊 Descargar Reporte Excel", data=buf_ex.getvalue(), file_name=f"Reporte_{edo_sel}.xlsx")

else:
    st.info("Por favor carga ambos archivos para generar el análisis.")
