"""
FinanceHub Mobile — Servidor para Render.com
Usa APIs abertas que funcionam em cloud (sem yfinance/Yahoo bloqueado por IP).

Fontes:
  - Índices globais  → Yahoo Finance com headers de browser real
  - Câmbio BRL       → AwesomeAPI
  - Cripto           → CoinGecko
"""

import os
import sys
import time
import logging
import threading
import requests
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

# ── CONFIG ────────────────────────────────────────────────────────
PORT      = int(os.environ.get("PORT", 5001))
HOST      = "0.0.0.0"
CACHE_TTL = 60
TIMEOUT   = 20

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder=str(Path(__file__).parent))
CORS(app, resources={r"/api/*": {"origins": "*"}})

_cache        = {"data": {}, "updated_at": None, "success": 0, "total": 20}
_lock         = threading.Lock()
_startup_done = threading.Event()

# Session com headers de browser real (bypassa bloqueio de cloud IP do Yahoo)
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://finance.yahoo.com/",
    "Origin": "https://finance.yahoo.com",
})

# Mapeamento: símbolo URL-encoded → símbolo interno
TICKERS_MAP = {
    "YM=F":       "YM=F",
    "ES=F":       "ES=F",
    "NQ=F":       "NQ=F",
    "^BVSP":      "^BVSP",
    "000001.SS":  "000001.SS",
    "^N225":      "^N225",
    "^HSI":       "^HSI",
    "^KS11":      "^KS11",
    "^FTSE":      "^FTSE",
    "^GDAXI":     "^GDAXI",
    "^FCHI":      "^FCHI",
    "FTSEMIB.MI": "FTSEMIB.MI",
    "CL=F":       "CL=F",
    "BZ=F":       "BZ=F",
    "GC=F":       "GC=F",
    "SI=F":       "SI=F",
}


def buscar_indices() -> dict:
    resultado = {}
    syms = ",".join(TICKERS_MAP.keys())
    for base_url in [
        "https://query1.finance.yahoo.com/v7/finance/quote",
        "https://query2.finance.yahoo.com/v7/finance/quote",
    ]:
        try:
            url = f"{base_url}?symbols={syms}&fields=regularMarketPrice,regularMarketChangePercent"
            r = SESSION.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            quotes = r.json().get("quoteResponse", {}).get("result", [])
            for q in quotes:
                sym   = q.get("symbol", "")
                price = q.get("regularMarketPrice")
                chg   = q.get("regularMarketChangePercent", 0)
                if price and price > 0:
                    resultado[TICKERS_MAP.get(sym, sym)] = {
                        "price": round(float(price), 6),
                        "chg":   round(float(chg), 4),
                    }
            if resultado:
                log.info("Yahoo (%s): %d índices OK", base_url.split("/")[2], len(resultado))
                break
        except Exception as e:
            log.warning("Yahoo %s falhou: %s", base_url, e)
    return resultado


def buscar_cambio() -> dict:
    resultado = {}
    try:
        r = SESSION.get(
            "https://economia.awesomeapi.com.br/json/last/USD-BRL,EUR-BRL",
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        resultado["BRL=X"]    = {"price": round(float(data["USDBRL"]["bid"]), 4), "chg": round(float(data["USDBRL"].get("pctChange", 0)), 4)}
        resultado["EURBRL=X"] = {"price": round(float(data["EURBRL"]["bid"]), 4), "chg": round(float(data["EURBRL"].get("pctChange", 0)), 4)}
        log.info("AwesomeAPI câmbio: OK")
    except Exception as e:
        log.warning("AwesomeAPI falhou: %s", e)
    return resultado


def buscar_cripto() -> dict:
    resultado = {}
    try:
        r = SESSION.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true",
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("bitcoin", {}).get("usd"):
            resultado["BTC-USD"] = {"price": round(float(data["bitcoin"]["usd"]), 2), "chg": round(float(data["bitcoin"].get("usd_24h_change", 0)), 4)}
        if data.get("ethereum", {}).get("usd"):
            resultado["ETH-USD"] = {"price": round(float(data["ethereum"]["usd"]), 2), "chg": round(float(data["ethereum"].get("usd_24h_change", 0)), 4)}
        log.info("CoinGecko: %d ativos OK", len(resultado))
    except Exception as e:
        log.warning("CoinGecko falhou: %s", e)
    return resultado


def atualizar_cache() -> None:
    log.info("Atualizando — %s", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    resultado = {**buscar_indices(), **buscar_cambio(), **buscar_cripto()}
    with _lock:
        _cache["data"]       = resultado
        _cache["updated_at"] = datetime.now().isoformat()
        _cache["success"]    = len(resultado)
        _cache["total"]      = 20
    log.info("Cache final: %d/20 símbolos", len(resultado))


def loop_atualizacao() -> None:
    for tentativa in range(5):
        atualizar_cache()
        if _cache["success"] >= 4:
            break
        log.warning("Tentativa %d falhou (%d símbolos), retentando...", tentativa + 1, _cache["success"])
        time.sleep(10)
    _startup_done.set()
    log.info("Pronto! %d/20 símbolos carregados", _cache["success"])
    while True:
        time.sleep(CACHE_TTL)
        atualizar_cache()


# ── ROTAS ─────────────────────────────────────────────────────────

@app.route("/api/quotes")
def api_quotes():
    with _lock:
        return jsonify({
            "data":       _cache["data"],
            "updated_at": _cache["updated_at"],
            "success":    _cache["success"],
            "total":      _cache["total"],
            "ready":      _startup_done.is_set(),
        })


@app.route("/api/ready")
def api_ready():
    with _lock:
        return jsonify({"ready": _startup_done.is_set(), "success": _cache["success"]})


@app.route("/api/health")
def api_health():
    with _lock:
        return jsonify({"status": "ok", "ready": _startup_done.is_set(), "updated_at": _cache["updated_at"], "symbols": f"{_cache['success']}/20"})


@app.route("/")
@app.route("/app")
@app.route("/index.html")
def index():
    return send_from_directory(str(Path(__file__).parent), "app.html")


@app.route("/manifest.json")
def manifest():
    return send_from_directory(str(Path(__file__).parent), "manifest.json")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(str(Path(__file__).parent), filename)


if __name__ == "__main__":
    log.info("Iniciando FinanceHub Mobile...")
    threading.Thread(target=loop_atualizacao, daemon=True).start()
    _startup_done.wait(timeout=120)
    log.info("Pronto! Porta %d", PORT)
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


