import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import Draw
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth

# 1. CONFIGURACIÓN
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

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

# 2. AUTENTICACIÓN (CORREGIDO: cookie_expiry_days)
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
    
    authenticator = stauth.Authenticate(
        config['credentials'],
        config['cookie']['name'],
        config['cookie']['key'],
        cookie_expiry_days=config['cookie']['expiry_days'] # CORRECCIÓN AQUÍ
    )
    authenticator.login(location='main')
except Exception as e:
    st.error(f"Error de Autenticación: {e}"); st.stop()

if st.session_state.get("authentication_status"):
    col_mapa, col_controles = st.columns([3.5, 1.2]) 

    with col_controles:
        st.title("📍 Control")
        authenticator.logout('Cerrar Sesión', 'main')
        
        archivo = st.file_uploader("Sube Excel", type=["xlsx"])
        puntos_excel = pd.read_excel(archivo).dropna(subset=['Latitud', 'Longitud']) if archivo else pd.DataFrame()

        st.subheader("🔍 Filtros")
        mostrar_nombres = st.toggle("🏷️ Nombres", True)
        
        c1, c2 = st.columns(2)
        f_v = [c1.checkbox("⚪ R0", True), c1.checkbox("🟡 R1-15", True), c1.checkbox("🟠 R16-20", True),
               c2.checkbox("🔴 R21-30", True), c2.checkbox("🏮 R31-40", True), c2.checkbox("🍷 R40+", True)]
        activos = [i for i, v in enumerate(f_v) if v]

    with col_mapa:
        centro = [puntos_excel['Latitud'].mean(), puntos_excel['Longitud'].mean()] if not puntos_excel.empty else [19.4326, -99.1332]
        m = folium.Map(location=centro, zoom_start=12)

        # 3. DIBUJAR PUNTOS EXCEL (CON RADIO VISIBLE)
        if not puntos_excel.empty:
            for _, fila in puntos_excel.iterrows():
                v = fila.get('Volumen', 0)
                rango = 0 if v==0 else 1 if v<=15 else 2 if v<=20 else 3 if v<=30 else 4 if v<=40 else 5
                
                if rango in activos:
                    r_val = float(fila.get('Radio', 800))
                    # Usamos Circle directamente para que el plugin Draw lo capture como editable
                    folium.Circle(
                        location=[fila['Latitud'], fila['Longitud']],
                        radius=r_val,
                        color='black', weight=1,
                        fill=True, fill_color=obtener_color(v), fill_opacity=0.6,
                        tooltip=f"Nombre: {fila.get('Nombre')}"
                    ).add_to(m)

                    if mostrar_nombres:
                        folium.Marker(
                            [fila['Latitud'], fila['Longitud']], 
                            icon=DivIcon(html=f'<div style="font-size: 8pt; color: black; font-weight: bold; width:100px;">{fila["Nombre"]}</div>')
                        ).add_to(m)

        # 4. PLUGIN DRAW (Edición habilitada)
        draw = Draw(
            export=False,
            position='topleft',
            draw_options={
                'circle': {'showRadius': True, 'metric': True, 'shapeOptions': {'color': '#0000FF'}},
                'polyline': False, 'rectangle': False, 'polygon': False, 'marker': False, 'circlemarker': False
            },
            edit_options={'edit': True, 'remove': True}
        )
        draw.add_to(m)

        # Renderizar mapa
        map_output = st_folium(m, width="100%", height=800, key="mapa_v_corregida")

    # 5. EXPORTACIÓN (Recuperar nombres del tooltip)
    if map_output and map_output.get("all_drawings"):
        datos_actualizados = []
        for i, dibujo in enumerate(map_output["all_drawings"]):
            props = dibujo.get('properties', {})
            geom = dibujo.get('geometry', {})
            if 'radius' in props:
                lng, lat = geom['coordinates']
                nombre_raw = props.get('tooltip', f"Nuevo_{i+1}")
                nombre_limpio = nombre_raw.replace("Nombre: ", "")
                
                datos_actualizados.append({
                    "Nombre": nombre_limpio,
                    "Latitud": round(lat, 6), "Longitud": round(lng, 6),
                    "Radio_m": round(props['radius'], 1)
                })

        if datos_actualizados:
            with col_controles:
                st.subheader("💾 Exportar")
                df_export = pd.DataFrame(datos_actualizados)
                st.download_button("📥 Bajar CSV", df_export.to_csv(index=False).encode('utf-8'), "mapa_actualizado.csv", "text/csv")
                st.dataframe(df_export, height=200)

elif st.session_state.get("authentication_status") is False:
    st.error('Usuario/Contraseña incorrectos')
