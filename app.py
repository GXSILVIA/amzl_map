import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
from geopy.geocoders import ArcGIS
import io

# 1. CONFIGURACIÓN DE PÁGINA
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

# Inicializar estados de sesión para persistencia y estabilidad
if 'puntos_datos' not in st.session_state:
    st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen'])
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 12

searcher_main = ArcGIS(timeout=15)

def geolocalizar(df_input):
    df = df_input.copy()
    lats, lons = [], []
    progreso = st.progress(0)
    for i, fila in df.iterrows():
        try:
            loc = searcher_main.geocode(f"{fila.get('CP', '')}, {fila.get('Nombre', '')}, Mexico")
            lats.append(loc.latitude if loc else None)
            lons.append(loc.longitude if loc else None)
        except: lats.append(None); lons.append(None)
        progreso.progress((i + 1) / len(df))
    df['Latitud'], df['Longitud'] = lats, lons
    progreso.empty()
    return df

def asignar_rango(v):
    try:
        v = float(v)
        return 0 if v==0 else 1 if v<=15 else 2 if v<=20 else 3 if v<=30 else 4 if v<=40 else 5
    except: return 0

# 2. AUTENTICACIÓN
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
    authenticator = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
    authenticator.login(location='main')
except Exception as e:
    st.error(f"Error de configuración: {e}")
    st.stop()

if st.session_state.get("authentication_status"):
    col_mapa, col_controles = st.columns([3, 1])

    with col_controles:
        st.title("Panel de Control")
        authenticator.logout('Cerrar Sesión', 'main')
        
        # --- CARGA CON LIMPIEZA AUTOMÁTICA ---
        st.subheader("📁 Cargar Datos")
        modo = st.radio("Entrada por:", ["Coordenadas", "Código Postal"])
        archivo = st.file_uploader("Sube tu Excel (.xlsx)", type=["xlsx"])
        
        if archivo and st.button("🚀 Cargar y Dibujar"):
            df_raw = pd.read_excel(archivo)
            df_raw.columns = df_raw.columns.str.strip()
            
            if modo == "Código Postal":
                df_new = geolocalizar(df_raw)
            else:
                renombrar = {'lat':'Latitud','latitud':'Latitud','lon':'Longitud','longitud':'Longitud','radio':'Radio','volumen':'Volumen'}
                df_new = df_raw.rename(columns=renombrar)
            
            # Limpiamos lo anterior y enfocamos mapa
            df_final = df_new.dropna(subset=['Latitud', 'Longitud'])
            st.session_state.puntos_datos = df_final
            
            if not df_final.empty:
                st.session_state.map_center = [df_final['Latitud'].mean(), df_final['Longitud'].mean()]
                st.session_state.map_zoom = 13
            st.rerun()

        # --- FILTROS POR VOLUMEN ---
        st.subheader("🔍 Filtros de Visualización")
        f_cols = st.columns(2)
        f0 = f_cols[0].checkbox("⚪ R0 (Cero)", value=True)
        f1 = f_cols[0].checkbox("🟡 R1-15", value=True)
        f2 = f_cols[0].checkbox("🟠 R16-20", value=True)
        f3 = f_cols[1].checkbox("🔴 R21-30", value=True)
        f4 = f_cols[1].checkbox("🏮 R31-40", value=True)
        f5 = f_cols[1].checkbox("🍷 R40+", value=True)
        
        filtros_activos = [i for i, check in enumerate([f0, f1, f2, f3, f4, f5]) if check]

        # --- GESTIÓN DE PUNTOS ---
        st.subheader("📍 Editor de Puntos")
        if st.button("🗑️ Borrar Todo"):
            st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen'])
            st.rerun()

        edited_df = st.data_editor(st.session_state.puntos_datos, num_rows="dynamic", key="editor_v5")
        if not edited_df.equals(st.session_state.puntos_datos):
            st.session_state.puntos_datos = edited_df
            st.rerun()

        if not st.session_state.puntos_datos.empty:
            buf = io.BytesIO()
            st.session_state.puntos_datos.to_excel(buf, index=False)
            st.download_button("📥 Descargar Excel Final", buf, "mapa_exportado.xlsx")

    with col_mapa:
        # EL MAPA SOLO APARECE SI HAY DATOS
        if not st.session_state.puntos_datos.empty:
            df_temp = st.session_state.puntos_datos.copy()
            df_temp['rango_id'] = df_temp['Volumen'].apply(asignar_rango)
            df_filtrado = df_temp[df_temp['rango_id'].isin(filtros_activos)]

            m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom)
            folium.LatLngPopup().add_to(m)

            for _, fila in df_filtrado.iterrows():
                colores_map = {0:"#FFFFFF", 1:"#FFFF00", 2:"#FFA500", 3:"#FF7777", 4:"#FF0000", 5:"#800000"}
                color = colores_map.get(fila['rango_id'], "#888")
                
                folium.Circle(
                    [fila['Latitud'], fila['Longitud']], 
                    radius=float(fila.get('Radio', 800)), 
                    color="black", weight=1, fill=True, fill_color=color, fill_opacity=0.6,
                    popup=f"Nombre: {fila['Nombre']}<br>Vol: {fila['Volumen']}<br>Radio: {fila['Radio']}m"
                ).add_to(m)
                
                folium.Marker(
                    [fila['Latitud'], fila['Longitud']], 
                    icon=DivIcon(html=f'<div style="font-size: 8.5pt; font-weight: bold; width:150px">{fila["Nombre"]}</div>')
                ).add_to(m)

            # Mapa interactivo estable
            output = st_folium(m, width="100%", height=850, key="mapa_final", returned_objects=["last_clicked", "center", "zoom"])

            if output:
                # Mantener la vista donde el usuario la dejó
                if output.get("center"):
                    st.session_state.map_center = [output["center"]["lat"], output["center"]["lng"]]
                if output.get("zoom"):
                    st.session_state.map_zoom = output["zoom"]

                # Lógica para agregar punto nuevo al hacer clic
                clic = output.get("last_clicked")
                if clic:
                    clic_id = f"{clic['lat']}_{clic['lng']}"
                    if st.session_state.get('last_clic_id') != clic_id:
                        st.session_state.last_clic_id = clic_id
                        nuevo = {'Nombre': f'Punto_{len(st.session_state.puntos_datos)+1}', 'Latitud': clic['lat'], 'Longitud': clic['lng'], 'Radio': 800, 'Volumen': 0}
                        st.session_state.puntos_datos = pd.concat([st.session_state.puntos_datos, pd.DataFrame([nuevo])], ignore_index=True)
                        st.rerun()
        else:
            st.info("👋 Sube un archivo Excel para comenzar. El mapa se centrará automáticamente en tus datos.")
            st.warning("Asegúrate de que tu Excel tenga las columnas: Nombre, Latitud, Longitud (o CP), Radio y Volumen.")

elif st.session_state.get("authentication_status") is False:
    st.error('Usuario o contraseña incorrectos.')
