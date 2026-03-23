import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import Draw
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth

# 1. CONFIGURACIÓN Y ESTADO PERSISTENTE
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

# Inicializar estados si no existen
if 'dibujos_persistentes' not in st.session_state:
    st.session_state.dibujos_persistentes = []
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]

def obtener_color(v):
    try:
        v = float(v)
        if v == 0: return "#FFFFFF"
        if v <= 15: return "#FFFF00"
        if v <= 20: return "#FFA500"
        if v <= 30: return "#FF7777"
        if v <= 40: return "#FF0000"
        return "#800000"
    except: return "#888888"

# 2. AUTENTICACIÓN (Asegúrate de tener tu config.yaml)
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
    authenticator = stauth.Authenticate(
        config['credentials'], config['cookie']['name'], 
        config['cookie']['key'], config['cookie']['expiry_days']
    )
except: st.error("Error en config.yaml"); st.stop()

name, auth_status, username = authenticator.login(location='main')

if auth_status:
    col_mapa, col_controles = st.columns([4, 1.2]) 

    with col_controles:
        st.title(f"Usuario: {name}")
        authenticator.logout('Cerrar Sesión', 'main')
        archivo = st.file_uploader("Cargar Excel", type=["xlsx"])
        puntos_excel = pd.read_excel(archivo).dropna(subset=['Latitud', 'Longitud']) if archivo else pd.DataFrame()
        
        st.divider()
        mostrar_nombres = st.toggle("🏷️ Nombres Excel", True)
        
        # Filtros de Volumen
        c1, c2 = st.columns(2)
        f_v = [c1.checkbox("⚪ R0", True), c1.checkbox("🟡 R1-15", True), c1.checkbox("🟠 R16-20", True),
               c2.checkbox("🔴 R21-30", True), c2.checkbox("🏮 R31-40", True), c2.checkbox("🍷 R40+", True)]
        activos = [i for i, v in enumerate(f_v) if v]

        if st.button("🗑️ Borrar Dibujos Manuales"):
            st.session_state.dibujos_persistentes = []
            st.rerun()

    with col_mapa:
        # Crear objeto Mapa
        m = folium.Map(location=st.session_state.map_center, zoom_start=12)
        
        # --- CAPA 1: DATOS EXCEL (Dinámica por filtros) ---
        if not puntos_excel.empty:
            for _, fila in puntos_excel.iterrows():
                v = fila.get('Volumen', 0)
                rango = 0 if v==0 else 1 if v<=15 else 2 if v<=20 else 3 if v<=30 else 4 if v<=40 else 5
                if rango in activos:
                    folium.Circle(
                        location=[fila['Latitud'], fila['Longitud']], radius=float(fila.get('Radio', 800)),
                        color='black', weight=1, fill=True, fill_color=obtener_color(v), fill_opacity=0.4
                    ).add_to(m)
                    if mostrar_nombres:
                        folium.Marker([fila['Latitud'], fila['Longitud']], 
                            icon=DivIcon(html=f'<div style="font-size: 8pt; font-weight: bold; color: black;">{fila["Nombre"]}</div>')).add_to(m)

        # --- CAPA 2: DIBUJOS MANUALES (Persistentes) ---
        # Volvemos a dibujar los círculos que ya estaban guardados en el estado
        for d in st.session_state.dibujos_persistentes:
            folium.Circle(
                location=[d['lat'], d['lon']], radius=d['radius'],
                color='blue', weight=2, fill=True, fill_color='blue', fill_opacity=0.2
            ).add_to(m)

        # Herramienta de dibujo
        Draw(export=False, position='topleft', draw_options={
            'circle': {'showRadius': True, 'metric': True, 'shapeOptions': {'color': '#0000FF'}},
            'polyline': False, 'rectangle': False, 'polygon': False, 'marker': False
        }).add_to(m)

        # --- LEYENDA FLOTANTE (DENTRO DEL MAPA) ---
        filas_html = ""
        for i, d in enumerate(st.session_state.dibujos_persistentes):
            filas_html += f"<tr><td>M_{i+1}</td><td>{d['lat']:.4f}</td><td>{d['lon']:.4f}</td><td>{d['radius']:.1f}m</td></tr>"

        if filas_html:
            legend_html = f"""
            <div style="position: fixed; top: 20px; right: 70px; width: 260px; height: auto; 
                        z-index:9999; background: white; padding: 10px; border: 2px solid #333; 
                        border-radius: 8px; font-family: sans-serif; font-size: 11px; box-shadow: 2px 2px 5px rgba(0,0,0,0.2);">
                <b style="font-size: 13px;">📍 Nuevos Radios</b><br>
                <div style="max-height: 150px; overflow-y: auto; margin-top: 5px;">
                    <table style="width:100%; border-collapse: collapse; text-align: left;">
                        <tr style="border-bottom: 1px solid #ddd; background: #f9f9f9;"><th>ID</th><th>Lat</th><th>Lon</th><th>Radio</th></tr>
                        {filas_html}
                    </table>
                </div>
            </div>
            """
            m.get_root().html.add_child(folium.Element(legend_html))

        # Renderizado
        map_output = st_folium(m, width=1400, height=750, key="mapa_final")

    # --- 3. LÓGICA DE PERSISTENCIA (CAPTURA) ---
    if map_output and map_output.get("all_drawings"):
        nuevos_datos = []
        for dibujo in map_output["all_drawings"]:
            if 'radius' in dibujo['properties']:
                lng, lat = dibujo['geometry']['coordinates']
                nuevos_datos.append({
                    'lat': lat, 'lon': lng, 'radius': dibujo['properties']['radius']
                })
        
        # Si el número de dibujos cambió, actualizamos el estado y reiniciamos para mostrar la tabla
        if nuevos_datos != st.session_state.dibujos_persistentes:
            st.session_state.dibujos_persistentes = nuevos_datos
            st.rerun()

    # Descarga
    if st.session_state.dibujos_persistentes:
        df_save = pd.DataFrame(st.session_state.dibujos_persistentes)
        st.download_button("📥 Exportar Radios Manuales (CSV)", df_save.to_csv(index=False), "mis_radios.csv")

elif auth_status is False: st.error('Error de login')
