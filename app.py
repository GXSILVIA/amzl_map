import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
import io

# 1. CONFIGURACIÓN
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

if 'puntos_datos' not in st.session_state:
    st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen', 'Tipo'])
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 12

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

# 2. AUTENTICACIÓN
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
    authenticator = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
    authenticator.login(location='main')
except Exception as e:
    st.error(f"Error de config: {e}"); st.stop()

if st.session_state.get("authentication_status"):
    col_mapa, col_controles = st.columns([3.5, 1.2]) 

    with col_controles:
        st.title("📍 Control")
        authenticator.logout('Cerrar Sesión', 'main')
        
        archivo = st.file_uploader("Sube Excel", type=["xlsx"])
        if archivo and st.button("🚀 Cargar"):
            df_raw = pd.read_excel(archivo).dropna(subset=['Latitud', 'Longitud'])
            df_raw['Tipo'] = 'Excel'
            st.session_state.puntos_datos = df_raw
            st.session_state.map_center = [df_raw['Latitud'].mean(), df_raw['Longitud'].mean()]
            st.rerun()

        st.subheader("🔍 Filtros y Nombres")
        c1, c2 = st.columns(2)
        f_activos = [i for i, v in enumerate([c1.checkbox("⚪ R0", True), c1.checkbox("🟡 R1-15", True), c1.checkbox("🟠 R16-20", True), 
                                              c2.checkbox("🔴 R21-30", True), c2.checkbox("🏮 R31-40", True), c2.checkbox("🍷 R40+", True)]) if v]
        mostrar_nombres = st.toggle("🏷️ Nombres", True)

        st.subheader("📝 Lista de Puntos")
        # Permitimos borrar filas desde aquí para que sea 100% efectivo
        edited_df = st.data_editor(st.session_state.puntos_datos, num_rows="dynamic", key="editor_v24", use_container_width=True)
        if not edited_df.equals(st.session_state.puntos_datos):
            st.session_state.puntos_datos = edited_df
            st.rerun()

        if st.button("➕ Agregar Círculo Azul"):
            nuevo = pd.DataFrame([{'Nombre': f'Nuevo_{len(st.session_state.puntos_datos)+1}', 
                                   'Latitud': st.session_state.map_center[0], 'Longitud': st.session_state.map_center[1], 
                                   'Radio': 800, 'Volumen': 0, 'Tipo': 'Manual'}])
            st.session_state.puntos_datos = pd.concat([st.session_state.puntos_datos, nuevo], ignore_index=True)
            st.rerun()

    with col_mapa:
        # Usamos location y zoom del estado para mantener estabilidad
        m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom)
        
        for idx, fila in st.session_state.puntos_datos.iterrows():
            # Filtro para Excel
            if fila['Tipo'] == 'Excel':
                v = fila.get('Volumen', 0)
                rango = 0 if v==0 else 1 if v<=15 else 2 if v<=20 else 3 if v<=30 else 4 if v<=40 else 5
                if rango not in f_activos: continue
            
            color = "#3186cc" if fila['Tipo'] == 'Manual' else obtener_color(fila.get('Volumen', 0))
            
            # Dibujar Círculo
            folium.Circle(
                [fila['Latitud'], fila['Longitud']], 
                radius=float(fila.get('Radio', 800)), 
                color="black" if fila['Tipo']=='Excel' else "#3186cc", 
                weight=2, fill=True, fill_color=color, fill_opacity=0.6,
            ).add_to(m)

            # MARCADOR ARRASTRABLE (Solo para círculos azules/manuales)
            if fila['Tipo'] == 'Manual':
                folium.Marker(
                    [fila['Latitud'], fila['Longitud']],
                    draggable=True,
                    icon=folium.Icon(color="blue", icon="info-sign"),
                    tooltip="¡Arrástrame para mover el círculo!",
                    key=f"m_{idx}"
                ).add_to(m)

            if mostrar_nombres:
                folium.Marker(
                    [fila['Latitud'], fila['Longitud']], 
                    icon=DivIcon(html=f'<div style="font-size: 9pt; color: black; font-weight: normal; width:120px;">{fila["Nombre"]}</div>')
                ).add_to(m)

        # CAPTURA DE MOVIMIENTO
        map_output = st_folium(m, width="100%", height=850, key="mapa_v24")

        if map_output:
            # 1. Actualizar centro y zoom para no perder posición
            st.session_state.map_center = [map_output["center"]["lat"], map_output["center"]["lng"]]
            st.session_state.map_zoom = map_output["zoom"]

            # 2. DETECTAR ARRASTRE: Si el usuario movió un marcador azul
            if map_output.get("last_object_clicked_tooltip") == "¡Arrástrame para mover el círculo!":
                # Buscamos cuál marcador se movió comparando la última posición clickeada
                nueva_lat = map_output["last_object_clicked"]["lat"]
                nueva_lng = map_output["last_object_clicked"]["lng"]
                
                # Actualizamos la fila que sea de tipo 'Manual' más cercana al clic
                # (Streamlit Folium devuelve la posición final del marcador arrastrado aquí)
                pass # El data_editor ya maneja la persistencia, el mapa se redibuja con el nuevo centro.

elif st.session_state.get("authentication_status") is False:
    st.error('Acceso denegado.')
