"""Weather lookup through the free Open-Meteo API."""

import requests

WMO_CODES = {
    0: "Ciel dégagé",
    1: "Principalement dégagé",
    2: "Partiellement nuageux",
    3: "Couvert",
    45: "Brouillard",
    48: "Brouillard givrant",
    51: "Bruine légère",
    53: "Bruine modérée",
    55: "Bruine dense",
    61: "Pluie faible",
    63: "Pluie modérée",
    65: "Pluie forte",
    71: "Chute de neige faible",
    73: "Chute de neige modérée",
    75: "Chute de neige forte",
    80: "Averses de pluie faibles",
    81: "Averses de pluie modérées",
    82: "Averses de pluie violentes",
    95: "Orage faible ou modéré",
    96: "Orage avec grêle légère",
    99: "Orage avec grêle forte",
}


def get_weather(city: str) -> str:
    """Fetch the current weather for a given city through Open-Meteo."""
    try:
        geo_url = (
            f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=fr&format=json"
        )
        geo_response = requests.get(geo_url, timeout=5)
        geo_data = geo_response.json()

        if not geo_data.get("results"):
            return f"Impossible de trouver la ville : {city}"

        location = geo_data["results"][0]
        lat, lon = location["latitude"], location["longitude"]
        city_name = location.get("name", city)

        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,weather_code"
        )
        weather_response = requests.get(weather_url, timeout=5)
        weather_data = weather_response.json()

        current = weather_data.get("current", {})
        temp = current.get("temperature_2m")
        humidity = current.get("relative_humidity_2m")
        weather_code = current.get("weather_code")

        desc = WMO_CODES.get(weather_code, "Conditions inconnues")

        return f"Météo à {city_name} : {temp}°C, {desc.lower()}, humidité {humidity}%."

    except Exception as e:
        return f"Erreur lors de la récupération de la météo pour {city} : {str(e)}"
