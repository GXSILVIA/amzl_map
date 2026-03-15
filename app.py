import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
from geopy.geocoders import ArcGIS, Nominatim
import io

# 1. CONFIGURACIÓN DE PÁGINA
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

# Inicializar estados de sesión para evitar que el mapa se borre al interactuar
if 'puntos_capturados' not in st.session_state:
    st.session_state.puntos_capturados = []
if 'df_final' not in st.session_state:
    st.session_state.df_final = None

searcher_main = ArcGIS(timeout=15)
searcher_backup = Nominatim(user_agent="amzl_hub_mx_v16")

def geolocalizar_con_precision(df_input):
    df = df_input.copy()
    if 'CP' in df.columns:
        df['CP'] = df['CP'].astype(str).str.strip().str.replace('.0', '', regex=False).str.zfill(5)
    
    lats, lons = [], []
    progreso = st.progress(0)
    total = len(df)
    for i, fila in df.iterrows():
        cp, nombre = fila.get('CP', ''), str(fila.get('Nombre', ''))
        try:
            loc = searcher_main.geocode(f"{cp}, {nombre}, Mexico")
            if not loc: loc = searcher_backup.geocode(query={"postalcode": cp, "country": "Mexico"})
            lats.append(loc.latitude if loc else None)
            lons.append(loc.longitude if loc else None)
        except: lats.append(None); lons.append(None)
        progreso.progress((i + 1) / total)
    df['Latitud'], df['Longitud'] = lats, lons
    progreso.empty()
    return df

# 2. AUTENTICACIÓN
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
    authenticator = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
    authenticator.login(location='main')
except Exception as e:
    st.error(f"Error al cargar configuración: {e}")
    st.stop()

if st.session_state.get("authentication_status"):
    col_mapa, col_controles = st.columns([3, 1])

    with col_controles:
        st.title("Panel de Control")
        authenticator.logout('Cerrar Sesión', 'main')
        st.markdown("---")
        modo = st.radio("Modo de entrada:", ["Coordenadas", "Código Postal"])
        archivo = st.file_uploader("Sube tu archivo Excel", type=["xlsx"])
        
        # Limpieza manual si se quita el archivo
        if archivo is None:
            st.session_state.df_final = None
        
        st.subheader("📍 Puntos Capturados")
        if st.button("Limpiar Lista"):
            st.session_state.puntos_capturados = []
            st.rerun()

        if st.session_state.puntos_capturados:
            df_cap = pd.DataFrame(st.session_state.puntos_capturados)
            st.dataframe(df_cap, height=150)
            csv = df_cap.to_csv(index=False).encode('utf-8')
            st.download_button("📥 Descargar CSV", csv, "puntos.csv")

        st.subheader("🔍 Filtros")
        labels = ["⚪ R0", "🟡 R1-15", "🟠 R16-20", "🔴 R21-30", "🏮 R31-40", "🍷 R40+"]
        # Usamos st.session_state para que los checkboxes no se reseteen solos
        f_checks = [st.checkbox(labels[i], value=True, key=f"f_{i}") for i in range(6)]
        mostrar_nombres = st.checkbox("🏷️ Mostrar Nombres", value=True)

    with col_mapa:
        if archivo:
            if st.session_state.df_final is None:
                df_raw = pd.read_excel(archivo)
                df_raw.columns = df_raw.columns.str.strip()
                if modo == "Código Postal":
                    st.session_state.df_final = geolocalizar_con_precision(df_raw)
                else:
                    renombrar = {'lat':'Latitud','latitud':'Latitud','lon':'Longitud','longitud':'Longitud','lng':'Longitud'}
                    st.session_state.df_final = df_raw.rename(columns=renombrar)

        if st.session_state.df_final is not None:
            df = st.session_state.df_final.copy().dropna(subset=['Latitud', 'Longitud'])
            
            def asignar_rango(x):
                try: 
                    v = float(x)
                    return 0 if v==0 else 1 if v<=15 else 2 if v<=20 else 3 if v<=30 else 4 if v<=40 else 5
                except: return 0
            
            df['rango_id'] = df['Volumen'].apply(asignar_rango)
            filtros_activos = [i for i, v in enumerate(f_checks) if v]
            df_filtrado = df[df['rango_id'].isin(filtros_activos)]

            if not df_filtrado.empty:
                m = folium.Map(location=[df_filtrado['Latitud'].mean(), df_filtrado['Longitud'].mean()], zoom_start=12)
                folium.LatLngPopup().add_to(m)

                for _, fila in df_filtrado.iterrows():
                    color = {0:"#FFF", 1:"#FF0", 2:"#FFA500", 3:"#F77", 4:"#F00", 5:"#800"}.get(fila['rango_id'], "#888")
                    rad = fila.get('Radio', 800)
                    vol = fila.get('Volumen', 0)
                    
                    # Círculo con Popup que muestra el VOLUMEN
                    folium.Circle(
                        [fila['Latitud'], fila['Longitud']], 
                        radius=float(rad), color="black", weight=1, fill=True, fill_color=color, fill_opacity=0.6,
                        popup=f"<b>Nombre:</b> {fila.get('Nombre','')}<br><b>Volumen:</b> {vol}<br><b>Radio:</b> {rad}m"
                    ).add_to(m)
                    
                    if mostrar_nombres:
                        folium.Marker(
                            [fila['Latitud'], fila['Longitud']], 
                            icon=DivIcon(html=f'<div style="font-size: 8pt; font-weight: bold; color: black; text-shadow: 1px 1px white; width: 150px;">{fila.get("Nombre","")}</div>')
                        ).add_to(m)

                # Marcas azules de puntos capturados
                for p in st.session_state.puntos_capturados:
                    folium.Marker([p['Latitud'], p['Longitud']], icon=folium.Icon(color='blue', icon='info-sign')).add_to(m)
                
                # Renderizado y captura de clics
                map_output = st_folium(m, width="100%", height=750, key="mapa_v1")
                
                if map_output and map_output.get("last_clicked"):
                    click_p = {"Latitud": round(map_output["last_clicked"]["lat"], 6), "Longitud": round(map_output["last_clicked"]["lng"], 6)}
                    if click_p not in st.session_state.puntos_capturados:
                        st.session_state.puntos_capturados.append(click_p)
                        st.rerun()
            else:
                st.warning("No hay datos para mostrar con los filtros seleccionados.")

elif st.session_state.get("authentication_status") is False:
    st.error('Acceso denegado')
