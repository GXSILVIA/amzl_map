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
    st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen', 'Tipo'])
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 12

def obtener_color(fila):
    if fila.get('Tipo') == 'Manual': return "#3186cc"
    try:
        v = float(fila.get('Volumen', 0))
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
            df_raw = pd.read_excel(archivo)
            df_raw.columns = df_raw.columns.str.strip()
            renombrar = {'lat':'Latitud','latitud':'Latitud','lon':'Longitud','longitud':'Longitud','radio':'Radio','volumen':'Volumen'}
            df_new = df_raw.rename(columns=renombrar).dropna(subset=['Latitud', 'Longitud'])
            df_new['Tipo'] = 'Excel'
            st.session_state.puntos_datos = df_new
            if not df_new.empty:
                st.session_state.map_center = [df_new['Latitud'].mean(), df_new['Longitud'].mean()]
            st.rerun()

        st.subheader("🔍 Filtros y Nombres")
        c1, c2 = st.columns(2)
        f_activos = [i for i, v in enumerate([c1.checkbox("⚪ R0", True), c1.checkbox("🟡 R1-15", True), c1.checkbox("🟠 R16-20", True), 
                                              c2.checkbox("🔴 R21-30", True), c2.checkbox("🏮 R31-40", True), c2.checkbox("🍷 R40+", True)]) if v]
        mostrar_nombres = st.toggle("🏷️ Nombres", True)

        st.subheader("📝 Lista de Puntos")
        edited_df = st.data_editor(st.session_state.puntos_datos, num_rows="fixed", key="editor_v22", use_container_width=True)
        if not edited_df.equals(st.session_state.puntos_datos):
            st.session_state.puntos_datos = edited_df
            st.rerun()
            
        if not st.session_state.puntos_datos.empty:
            buf = io.BytesIO()
            st.session_state.puntos_datos.to_excel(buf, index=False)
            st.download_button("📥 Descargar Excel", buf, "mapa_actualizado.xlsx")

    with col_mapa:
        m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom)
        
        # --- HERRAMIENTA DE DIBUJO AVANZADA ---
        # Permite crear (Azul), EDITAR (Mover/Redimensionar) y ELIMINAR.
        Draw(
            export=False,
            position='topleft',
            draw_options={
                'polyline': False, 'rectangle': False, 'polygon': False, 'circlemarker': False, 'marker': False,
                'circle': {'shapeOptions': {'color': '#3186cc', 'fillOpacity': 0.5, 'weight': 3}}
            },
            edit_options={'edit': True, 'remove': True}
        ).add_to(m)

        if not st.session_state.puntos_datos.empty:
            for i, fila in st.session_state.puntos_datos.iterrows():
                # Filtros para Excel (Los manuales siempre se ven)
                if fila['Tipo'] == 'Excel':
                    rango = 0 if fila['Volumen']==0 else 1 if fila['Volumen']<=15 else 2 if fila['Volumen']<=20 else 3 if fila['Volumen']<=30 else 4 if fila['Volumen']<=40 else 5
                    if rango not in f_activos: continue

                color = obtener_color(fila)
                folium.Circle(
                    [fila['Latitud'], fila['Longitud']], 
                    radius=float(fila['Radio']), 
                    color="#3186cc" if fila['Tipo']=='Manual' else "black", 
                    weight=3 if fila['Tipo']=='Manual' else 1,
                    fill=True, fill_color=color, fill_opacity=0.6,
                ).add_to(m)

                if mostrar_nombres:
                    folium.Marker(
                        [fila['Latitud'], fila['Longitud']], 
                        icon=DivIcon(html=f'<div style="font-size: 9pt; color: black; width:120px;">{fila["Nombre"]}</div>')
                    ).add_to(m)

        # CAPTURA DE DATOS DEL MAPA (DIBUJOS Y EDICIONES)
        map_output = st_folium(m, width="100%", height=850, key="mapa_v22")

        if map_output and map_output.get("all_drawings"):
            # Analizar el estado actual de los dibujos en el mapa
            dibujos_mapa = map_output["all_drawings"]
            puntos_manuales_nuevos = []
            
            # Limpiar manuales antiguos para reemplazarlos con la versión editada del mapa
            df_actual = st.session_state.puntos_datos[st.session_state.puntos_datos['Tipo'] == 'Excel'].copy()
            
            for d in dibujos_mapa:
                if d['geometry']['type'] == 'Point' and 'radius' in d['properties']:
                    # GeoJSON: [lng, lat]
                    lng, lat = d['geometry']['coordinates']
                    rad = d['properties']['radius']
                    
                    puntos_manuales_nuevos.append({
                        'Nombre': f'Manual_{len(puntos_manuales_nuevos)+1}',
                        'Latitud': round(lat, 6), 'Longitud': round(lng, 6),
                        'Radio': round(rad, 2), 'Volumen': 0, 'Tipo': 'Manual'
                    })
            
            # Unificar Excel con los nuevos Manuales (editados o movidos)
            nueva_tabla = pd.concat([df_actual, pd.DataFrame(puntos_manuales_nuevos)], ignore_index=True)
            
            if not nueva_tabla.equals(st.session_state.puntos_datos):
                st.session_state.puntos_datos = nueva_tabla
                st.rerun()

elif st.session_state.get("authentication_status") is False:
    st.error('Acceso denegado.')
