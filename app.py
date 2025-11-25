#!/usr/bin/env python3
"""
Seguimiento de partidos de fútbol (Football-Data.org)
Optimizado para partidos jugados (no programados)
"""
import os
import io
import time
from datetime import datetime
from functools import lru_cache
import requests

import requests
from flask import Flask, render_template, request, jsonify, url_for, send_file, redirect, flash

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# ThingSpeak Configura
THINGSPEAK_API_KEY = os.getenv('THINGSPEAK_API_KEY', 'EZW1T3EFD0ISPQHY')
THINGSPEAK_CHANNEL_ID = os.getenv('THINGSPEAK_CHANNEL_ID', '3179450')
THINGSPEAK_BASE_URL = 'https://api.thingspeak.com'

APP = Flask(__name__)
APP.secret_key = os.getenv('FLASK_SECRET', 'clave_segura_aqui')

# Football-Data.org config
FOOTBALL_DATA_KEY = os.getenv('FOOTBALL_DATA_KEY', 'b91aa1544ee549429984e85a0f23a190')
HEADERS = {'X-Auth-Token': FOOTBALL_DATA_KEY}

# Control de rate limiting
LAST_REQUEST_TIME = 0
REQUEST_DELAY = 6  # 6 segundos entre requests (10/minuto)

# Mapeo de ligas (códigos de Football-Data.org)
LEAGUES = {
    "PL": {"name": "Premier League", "display": "Premier League (ENG)", "id": "PL"},
    "PD": {"name": "La Liga", "display": "LaLiga (ESP)", "id": "PD"},
    "SA": {"name": "Serie A", "display": "Serie A (ITA)", "id": "SA"},
    "BL1": {"name": "Bundesliga", "display": "Bundesliga (GER)", "id": "BL1"},
    "FL1": {"name": "Ligue 1", "display": "Ligue 1 (FRA)", "id": "FL1"},
}

# Temporadas disponibles
SEASONS = {
    "2023": "2023-2024",
    "2022": "2022-2023", 
    "2021": "2021-2022",
    "2020": "2020-2021"
}

# --------------------
# Helpers optimizados
# --------------------
def rate_limited_request():
    """Controla el rate limiting"""
    global LAST_REQUEST_TIME
    current_time = time.time()
    elapsed = current_time - LAST_REQUEST_TIME
    
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)
    
    LAST_REQUEST_TIME = time.time()

def football_data_get(endpoint, timeout=10):
    """Llamada genérica a Football-Data.org con rate limiting"""
    url = f'https://api.football-data.org/v4/{endpoint}'
    
    # Rate limiting
    rate_limited_request()
    
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        
        if r.status_code == 429:
            flash('Límite de requests excedido. Espera un minuto.', 'warning')
            return None
        elif r.status_code == 403:
            flash('API Key inválida o no configurada.', 'danger')
            return None
        elif r.status_code == 404:
            return None
            
        r.raise_for_status()
        return r.json()
            
    except requests.exceptions.RequestException as e:
        flash(f'Error de conexión: {e}', 'danger')
        return None

# Cache por más tiempo (30 minutos)
@lru_cache(maxsize=128)
def get_teams_in_league(league_code, season=None):
    """Devuelve lista de equipos para una liga"""
    if not season:
        season = "2023"
        
    print(f"DEBUG: Obteniendo equipos para liga {league_code} temporada {season}")
    data = football_data_get(f'competitions/{league_code}/teams?season={season}')
    
    if not data or 'teams' not in data:
        return []
    
    return data['teams']

@lru_cache(maxsize=256)
def get_team_by_id(team_id):
    """Devuelve info del equipo por id"""
    print(f"DEBUG: Obteniendo info del equipo {team_id}")
    data = football_data_get(f'teams/{team_id}')
    return data if data else {}

@lru_cache(maxsize=256)
def get_last_matches_for_team(team_id, season=None, limit=15):
    """Devuelve últimos partidos JUGADOS del equipo"""
    if not season:
        season = "2023"
        
    print(f"DEBUG: Obteniendo {limit} partidos JUGADOS para equipo {team_id} temporada {season}")
    
    # Obtener partidos de la temporada específica y filtrar solo los FINALIZADOS
    data = football_data_get(f'teams/{team_id}/matches?season={season}&limit={limit * 2}')  # Pedir más para filtrar
    
    if not data or 'matches' not in data:
        return []
    
    # Filtrar solo partidos FINALIZADOS
    finished_matches = [match for match in data['matches'] if match.get('status') == 'FINISHED']
    
    # Tomar solo los últimos 'limit' partidos finalizados
    finished_matches = finished_matches[:limit]
    
    print(f"DEBUG: Encontrados {len(finished_matches)} partidos FINALIZADOS de {len(data['matches'])} totales")
    
    return finished_matches

@lru_cache(maxsize=128)
def get_league_standings(league_code, season=None):
    """Devuelve tabla de posiciones"""
    if not season:
        season = "2023"
        
    print(f"DEBUG: Obteniendo tabla para liga {league_code} temporada {season}")
    data = football_data_get(f'competitions/{league_code}/standings?season={season}')
    
    if not data or 'standings' not in data:
        return []
    
    for standing in data['standings']:
        if standing['type'] == 'TOTAL':
            return standing['table']
    
    return []

# --------------------
# Transformación de datos
# --------------------
def parse_date(date_str):
    """Parsea fecha de Football-Data.org"""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except Exception:
        return None

def build_match_list_from_matches(matches, team_id):
    """Construye lista de matches con campos uniformes"""
    match_list = []
    
    print(f"DEBUG: Procesando {len(matches)} partidos FINALIZADOS para equipo {team_id}")
    
    for i, match in enumerate(matches):
        date = parse_date(match.get('utcDate'))
        home_team = match.get('homeTeam', {})
        away_team = match.get('awayTeam', {})
        score = match.get('score', {}).get('fullTime', {})
        
        home_id = home_team.get('id')
        away_id = away_team.get('id')
        home_name = home_team.get('name', '')
        away_name = away_team.get('name', '')
        
        home_score = score.get('home')
        away_score = score.get('away')
        
        # DEBUG: Mostrar información del partido
        print(f"DEBUG Partido {i+1}: {home_name} {home_score}-{away_score} {away_name}")
        
        # Determinar si nuestro equipo es local o visitante
        if str(team_id) == str(home_id):
            goals_for = home_score
            goals_against = away_score
            opponent = away_name
            is_home = True
        else:
            goals_for = away_score
            goals_against = home_score
            opponent = home_name
            is_home = False
        
        # Calcular resultado (siempre debería haber datos en partidos FINISHED)
        if goals_for is not None and goals_against is not None:
            if goals_for > goals_against:
                result = 'W'
            elif goals_for == goals_against:
                result = 'D'
            else:
                result = 'L'
        else:
            result = None
            print(f"DEBUG: ⚠️ Partido sin datos de goles: {home_name} vs {away_name}")
        
        match_list.append({
            'date': date,
            'opponent': opponent,
            'goals_for': goals_for,
            'goals_against': goals_against,
            'result': result,
            'is_home': is_home,
            'status': 'Finalizado',
            'competition': match.get('competition', {}).get('name', ''),
            'raw': match
        })
    
    # Contar partidos con datos de goles
    matches_with_goals = sum(1 for m in match_list if m['goals_for'] is not None)
    print(f"DEBUG: {matches_with_goals}/{len(match_list)} partidos tienen datos de goles")
    
    return match_list

# --------------------
# Rutas web
# --------------------
@APP.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        league_id = request.form.get('league_id') or ''
        team_id = request.form.get('team_id') or ''
        season = request.form.get('season') or '2023'
        limit = request.form.get('limit') or '15'
        return redirect(url_for('index', league_id=league_id, team_id=team_id, season=season, limit=limit))

    league_id = request.args.get('league_id', '')
    team_id = request.args.get('team_id', '')
    season = request.args.get('season', '2023')
    try:
        limit = int(request.args.get('limit', '15'))
    except Exception:
        limit = 15

    leagues = LEAGUES
    seasons_list = SEASONS
    teams = []
    team_info = {}
    matches = []
    standings = []

    if league_id:
        teams = get_teams_in_league(league_id, season)

    if team_id:
        team_info = get_team_by_id(team_id)
        matches_data = get_last_matches_for_team(team_id, season, limit)
        matches = build_match_list_from_matches(matches_data, team_id)

    if league_id and season:
        standings = get_league_standings(league_id, season)

    return render_template(
        'index.html',
        leagues=leagues,
        seasons=seasons_list,
        teams=teams,
        selected_league=league_id,
        selected_team=team_id,
        selected_season=season,
        team_info=team_info,
        matches=matches,
        standings=standings,
        limit=limit
    )

@APP.route('/api/teams', methods=['GET'])
def api_teams():
    """GET /api/teams?league_id=PL&season=2023"""
    league_id = request.args.get('league_id')
    season = request.args.get('season', '2023')
    
    if not league_id:
        return jsonify({'error': 'league_id requerido'}), 400
    
    try:
        teams = get_teams_in_league(league_id, season)
        simple_teams = [{'id': t.get('id'), 'name': t.get('name')} for t in teams]
        return jsonify({'teams': simple_teams})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --------------------
# Endpoints de gráficos (PNG) - MEJORADOS
# --------------------
def plot_bytesio(fig):
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format='png', dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf

@APP.route('/plot/goals.png')
def plot_goals():
    """Línea de goles por partido"""
    team_id = request.args.get('team_id')
    season = request.args.get('season', '2023')
    try:
        limit = int(request.args.get('limit', '15'))
    except Exception:
        limit = 15
        
    if not team_id:
        fig, ax = plt.subplots(figsize=(6,3))
        ax.text(0.5, 0.5, 'team_id requerido', ha='center', va='center', fontsize=14)
        buf = plot_bytesio(fig)
        return send_file(buf, mimetype='image/png')

    matches_data = get_last_matches_for_team(team_id, season, limit)
    matches = build_match_list_from_matches(matches_data, team_id)
    
    if not matches:
        fig, ax = plt.subplots(figsize=(8,4))
        ax.text(0.5, 0.5, 'No hay partidos FINALIZADOS\nen esta temporada', 
                ha='center', va='center', fontsize=12, wrap=True)
        ax.set_title('Goles por partido - Sin datos')
        buf = plot_bytesio(fig)
        return send_file(buf, mimetype='image/png')
    
    # Ordenar cronológicamente (más antiguo primero)
    matches_sorted = sorted(matches, key=lambda x: (x['date'] or datetime.min))
    
    dates = [m['date'] for m in matches_sorted if m['date'] is not None]
    goals = [m['goals_for'] for m in matches_sorted]

    fig, ax = plt.subplots(figsize=(10,4))
    if dates and any(goals):
        ax.plot(dates, goals, marker='o', linestyle='-', color='#2a9d8f', linewidth=2, markersize=6)
        ax.set_xlabel('Fecha')
        ax.set_ylabel('Goles a favor')
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d-%m-%Y'))
        fig.autofmt_xdate(rotation=45)
        
        # Añadir valores en los puntos
        for i, (date, goal) in enumerate(zip(dates, goals)):
            ax.annotate(f'{goal}', (date, goal), textcoords="offset points", 
                       xytext=(0,10), ha='center', fontsize=9)
        
        # Mejorar el grid
        ax.grid(True, alpha=0.3)
        ax.set_ylim(bottom=0)  # Empezar desde 0 en el eje Y
        
    else:
        ax.text(0.5, 0.5, 'No hay datos suficientes para graficar', ha='center', va='center')
    
    ax.set_title(f'Goles por partido - Temporada {SEASONS.get(season, season)}')
    buf = plot_bytesio(fig)
    return send_file(buf, mimetype='image/png')

@APP.route('/plot/stacked.png')
def plot_stacked():
    """Stacked bar: goles marcados vs encajados"""
    team_id = request.args.get('team_id')
    season = request.args.get('season', '2023')
    try:
        limit = int(request.args.get('limit', '15'))
    except Exception:
        limit = 15
        
    if not team_id:
        fig, ax = plt.subplots(figsize=(6,3))
        ax.text(0.5,0.5,'team_id requerido', ha='center', va='center')
        buf = plot_bytesio(fig)
        return send_file(buf, mimetype='image/png')

    matches_data = get_last_matches_for_team(team_id, season, limit)
    matches = build_match_list_from_matches(matches_data, team_id)
    
    if not matches:
        fig, ax = plt.subplots(figsize=(8,4))
        ax.text(0.5, 0.5, 'No hay partidos FINALIZADOS\nen esta temporada', 
                ha='center', va='center', fontsize=12, wrap=True)
        ax.set_title('Goles marcados vs encajados - Sin datos')
        buf = plot_bytesio(fig)
        return send_file(buf, mimetype='image/png')
    
    matches_sorted = sorted(matches, key=lambda x: (x['date'] or datetime.min))
    
    labels = [m['date'].strftime('%d/%m') if m['date'] else f"vs {m['opponent'][:8]}..." for m in matches_sorted]
    goals_for = [m['goals_for'] for m in matches_sorted]
    goals_against = [m['goals_against'] for m in matches_sorted]

    fig, ax = plt.subplots(figsize=(12,5))
    x = range(len(labels))
    
    bars1 = ax.bar(x, goals_for, label='Marcados', color='#1f77b4', alpha=0.8)
    bars2 = ax.bar(x, goals_against, bottom=goals_for, label='Encajados', color='#ff7f0e', alpha=0.8)
    
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right')
    ax.set_title(f'Goles marcados vs encajados - Temporada {SEASONS.get(season, season)}')
    ax.set_ylabel('Goles')
    ax.legend()
    
    # Añadir valores en las barras
    for i, (gf, ga) in enumerate(zip(goals_for, goals_against)):
        ax.text(i, gf/2, f'{gf}', ha='center', va='center', fontweight='bold', color='white')
        ax.text(i, gf + ga/2, f'{ga}', ha='center', va='center', fontweight='bold', color='white')
    
    ax.grid(True, alpha=0.3, axis='y')
    
    buf = plot_bytesio(fig)
    return send_file(buf, mimetype='image/png')

@APP.route('/plot/heatmap.png')
def plot_heatmap():
    """Heatmap simple"""
    team_id = request.args.get('team_id')
    season = request.args.get('season', '2023')
    try:
        limit = int(request.args.get('limit', '15'))
    except Exception:
        limit = 15
        
    if not team_id:
        fig, ax = plt.subplots(figsize=(6,3))
        ax.text(0.5,0.5,'team_id requerido', ha='center', va='center')
        buf = plot_bytesio(fig)
        return send_file(buf, mimetype='image/png')

    matches_data = get_last_matches_for_team(team_id, season, limit)
    matches = build_match_list_from_matches(matches_data, team_id)
    
    if not matches:
        fig, ax = plt.subplots(figsize=(8,4))
        ax.text(0.5, 0.5, 'No hay partidos FINALIZADOS\nen esta temporada', 
                ha='center', va='center', fontsize=12, wrap=True)
        ax.set_title('Heatmap - Sin datos')
        buf = plot_bytesio(fig)
        return send_file(buf, mimetype='image/png')
    
    matches_sorted = sorted(matches, key=lambda x: (x['date'] or datetime.min))
    
    goals_for = [m['goals_for'] for m in matches_sorted]
    goals_against = [m['goals_against'] for m in matches_sorted]
    data = [goals_for, goals_against]  # 2 x N

    fig, ax = plt.subplots(figsize=(10,3))
    im = ax.imshow(data, aspect='auto', cmap='YlOrRd')
    ax.set_yticks([0,1])
    ax.set_yticklabels(['Marcados','Encajados'])
    ax.set_xticks(range(len(matches_sorted)))
    ax.set_xticklabels([m['date'].strftime('%d/%m') if m['date'] else '' for m in matches_sorted], rotation=45, ha='right')
    ax.set_title(f'Heatmap: goles por partido - Temporada {SEASONS.get(season, season)}')
    
    # Añadir valores en las celdas
    for i in range(len(goals_for)):
        ax.text(i, 0, f'{goals_for[i]}', ha='center', va='center', fontweight='bold', color='black')
        ax.text(i, 1, f'{goals_against[i]}', ha='center', va='center', fontweight='bold', color='black')
    
    plt.colorbar(im, ax=ax, orientation='vertical', fraction=0.02, pad=0.04)
    buf = plot_bytesio(fig)
    return send_file(buf, mimetype='image/png')

if __name__ == '__main__':
    APP.run(host='0.0.0.0', port=5000, debug=True)
