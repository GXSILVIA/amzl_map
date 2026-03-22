import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import Draw
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
import io

# 1. CONFIGURACIÓN
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

if 'puntos_datos' not in st.session_state:
    st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen'])
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 12

def asignar_color(v):
    try:
        v = float(v)
        if v == 0: return "#FFFFFF"
        if v <= 15: return "#FFFF00"
        if v <= 20: return "#FFA500"
        if v <= 30: return "#FF7777"
        if v <= 40: return "#FF0000"
        return "#800000"
    except: return "#888888"

# 2. AUTENTICACIÓN
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
    authenticator = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
    authenticator.login(location='main')
except Exception as e:
    st.error(f"Error de config: {e}"); st.stop()

if st.session_state.get("authentication_status"):
    # Cambio de orden: Mapa a la izquierda (ancho), Panel a la derecha (estrecho)
    col_mapa, col_controles = st.columns([3.5, 1.2]) 

    with col_controles:
        st.title("📍 Control")
        authenticator.logout('Cerrar Sesión', 'main')
        
        archivo = st.file_uploader("Sube Excel", type=["xlsx"])
        if archivo and st.button("🚀 Cargar"):
            df_raw = pd.read_excel(archivo)
            df_raw.columns = df_raw.columns.str.strip()
            renombrar = {'lat':'Latitud','latitud':'Latitud','lon':'Longitud','longitud':'Longitud','radio':'Radio','volumen':'Volumen'}
            df_new = df_raw.rename(columns=renombrar).dropna(subset=['Latitud', 'Longitud'])
            st.session_state.puntos_datos = df_new
            
            # FOCO AUTOMÁTICO EN COORDENADAS DEL EXCEL
            if not df_new.empty:
                st.session_state.map_center = [df_new['Latitud'].mean(), df_new['Longitud'].mean()]
                st.session_state.map_zoom = 13
            st.rerun()

        st.subheader("🔍 Filtros y Nombres")
        c1, c2 = st.columns(2)
        f_activos = [i for i, v in enumerate([c1.checkbox("⚪ R0", True), c1.checkbox("🟡 R1-15", True), c1.checkbox("🟠 R16-20", True), 
                                              c2.checkbox("🔴 R21-30", True), c2.checkbox("🏮 R31-40", True), c2.checkbox("🍷 R40+", True)]) if v]
        mostrar_nombres = st.toggle("🏷️ Nombres", True)

        st.subheader("📝 Lista de Puntos")
        edited_df = st.data_editor(st.session_state.puntos_datos, num_rows="dynamic", key="editor_v16")
        if not edited_df.equals(st.session_state.puntos_datos):
            st.session_state.puntos_datos = edited_df
            st.rerun()

    with col_mapa:
        m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom)
        
        # --- HERRAMIENTA DE DIBUJO Y ARRASTRE ---
        # Permite crear círculos azules, moverlos y editarlos
        Draw(
            export=True,
            position='topleft',
            draw_options={
                'polyline': False, 'rectangle': False, 'polygon': False, 'circlemarker': False, 'marker': False,
                'circle': {'shapeOptions': {'color': '#3186cc', 'fillOpacity': 0.5}}
            },
            edit_options={'edit': True}
        ).add_to(m)

        if not st.session_state.puntos_datos.empty:
            df_m = st.session_state.puntos_datos.copy()
            df_m['Radio'] = pd.to_numeric(df_m['Radio'], errors='coerce').fillna(800)
            
            for _, fila in df_m.iterrows():
                rango = 0 if fila['Volumen']==0 else 1 if fila['Volumen']<=15 else 2 if fila['Volumen']<=20 else 3 if fila['Volumen']<=30 else 4 if fila['Volumen']<=40 else 5
                if rango not in f_activos: continue

                color_v = asignar_color(fila['Volumen'])
                folium.Circle(
                    [fila['Latitud'], fila['Longitud']], radius=float(fila['Radio']), 
                    color="black", weight=1, fill=True, fill_color=color_v, fill_opacity=0.6,
                    popup=f"{fila['Nombre']}"
                ).add_to(m)

                if mostrar_nombres:
                    folium.Marker(
                        [fila['Latitud'], fila['Longitud']], 
                        icon=DivIcon(html=f'<div style="font-size: 9pt; color: black; font-weight: normal; width:120px;">{fila["Nombre"]}</div>')
                    ).add_to(m)

        # Captura de datos del mapa
        map_output = st_folium(m, width="100%", height=800, key="mapa_v16")

        # LÓGICA PARA ACTUALIZAR LA LISTA DESDE EL MAPA
        if map_output and map_output.get("all_drawings"):
            dibujos = map_output["all_drawings"]
            # Si se detecta un nuevo círculo dibujado o movido
            for d in dibujos:
                if d['geometry']['type'] == 'Point' and 'radius' in d['properties']:
                    # Extraer coordenadas y radio del círculo dibujado a mano
                    lat_n = d['geometry']['coordinates'][1]
                    lng_n = d['geometry']['coordinates'][0]
                    rad_n = d['properties']['radius']
                    
                    # Agregar a la lista si no existe
                    if not ((st.session_state.puntos_datos['Latitud'] == lat_n) & (st.session_state.puntos_datos['Longitud'] == lng_n)).any():
                        nuevo = pd.DataFrame([{'Nombre': f'Manual_{len(st.session_state.puntos_datos)+1}', 'Latitud': lat_n, 'Longitud': lng_n, 'Radio': rad_n, 'Volumen': 0}])
                        st.session_state.puntos_datos = pd.concat([st.session_state.puntos_datos, nuevo], ignore_index=True)
                        st.rerun()

elif st.session_state.get("authentication_status") is False:
    st.error('Acceso denegado.')
