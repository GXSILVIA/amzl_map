# ... (dentro del bloque col_mapa)

# Usar el centro guardado en session_state para evitar saltos de cámara
m = folium.Map(
    location=st.session_state.map_center, 
    zoom_start=12,
    control_scale=True
)

if not puntos_excel.empty:
    for _, fila in puntos_excel.iterrows():
        # Verificación de seguridad para las columnas
        lat = fila['Latitud']
        lon = fila['Longitud']
        radio_m = fila.get('Radio', 800) # Default 800m si no existe la columna
        
        # Lógica de filtrado por volumen (tu código actual está bien)
        v = fila.get('Volumen', 0)
        # ... (tu lógica de rangos y activos)
        
        folium.Circle(
            location=[lat, lon],
            radius=float(radio_m),
            color='black',
            weight=1,
            fill=True,
            fill_color=obtener_color(v),
            fill_opacity=0.4,
            tooltip=f"Persona: {fila.get('Nombre', 'S/N')}"
        ).add_to(m)

# Renderizar y capturar cambios de vista
map_output = st_folium(
    m, 
    width="100%", # Ajuste responsivo
    height=800, 
    key="mapa_principal",
    returned_objects=["all_drawings", "last_object_clicked", "center", "zoom"]
)

# Actualizar centro en el estado para la siguiente interacción
if map_output.get("center"):
    st.session_state.map_center = [map_output["center"]["lat"], map_output["center"]["lng"]]
