import pandas as pd
import folium
from folium.features import DivIcon
import random

# 1. Leer el Excel
try:
    df = pd.read_excel('datos.xlsx')
    df.columns = df.columns.str.strip() 
    print("Columnas detectadas en tu Excel:", list(df.columns))
except Exception as e:
    print(f"Error al abrir el archivo: {e}")
    exit()

# 2. Mapa base
mapa = folium.Map(location=[df['Latitud'].mean(), df['Longitud'].mean()], zoom_start=12)

# 3. Dibujar círculos
for i, fila in df.iterrows():
    color = "#{:06x}".format(random.randint(0, 0xFFFFFF))
    
    nombre = str(fila.get('Nombre', 'Sin Nombre'))
    lat = fila.get('Latitud')
    lon = fila.get('Longitud')
    radio = fila.get('Radio', 100)

    folium.Circle(
        location=[lat, lon],
        radius=radio,
        color=color,
        weight=1,
        fill=True,
        fill_color=color,
        fill_opacity=0.3,
        tooltip=f"Persona: {nombre}"
    ).add_to(mapa)

    folium.Marker(
        location=[lat, lon],
        icon=DivIcon(
            icon_size=(150,36),
            icon_anchor=(75,18),
            html=f'<div style="font-size: 9pt; color: black; font-weight: bold; text-shadow: 0 0 3px white; text-align: center;">{nombre}</div>'
        )
    ).add_to(mapa)

mapa.save('mapa_final.html')
print("\n¡ÉXITO! Mapa generado como 'mapa_final.html'.")
