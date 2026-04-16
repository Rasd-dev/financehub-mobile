"""
FinanceHub Mobile — Servidor para Render.com
Roda na nuvem 24h, sem precisar de PC ligado
"""

import os
import sys
import time
import logging
import threading
from datetime import datetime
from pathlib import Path

import yfinance as yf
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

# ── CONFIG ────────────────────────────────────────────────────────
PORT      = int(os.environ.get("PORT", 5001))   # Render define PORT automaticamente
HOST      = "0.0.0.0"
CACHE_TTL = 60  # segundos entre atualizações

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder=str(Path(__file__).parent))
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ── TICKERS ───────────────────────────────────────────────────────
TICKERS = {
    "YM=F":       "Dow Jones Futuro (EUA)",
    "ES=F":       "S&P 500 Futuro (EUA)",
    "NQ=F":       "Nasdaq Futuro (EUA)",
    "^BVSP":      "Ibovespa",
    "BRL=X":      "Dólar (USD/BRL)",
    "EURBRL=X":   "Euro (EUR/BRL)",
    "000001.SS":  "Shanghai SE (China)",
    "^N225":      "Nikkei (Japão)",
    "^HSI":       "Hang Seng Index (Hong Kong)",
    "^KS11":      "Kospi (Coreia do Sul)",
    "^FTSE":      "FTSE 100 (Reino Unido)",
    "^GDAXI":     "DAX (Alemanha)",
    "^FCHI":      "CAC 40 (França)",
    "FTSEMIB.MI": "FTSE MIB (Itália)",
    "CL=F":       "Petróleo WTI",
    "BZ=F":       "Petróleo Brent",
    "GC=F":       "Ouro",
    "SI=F":       "Prata",
    "BTC-USD":    "Bitcoin",
    "ETH-USD":    "Ethereum",
}

# ── CACHE ─────────────────────────────────────────────────────────
_cache        = {"data": {}, "updated_at": None, "success": 0, "total": len(TICKERS)}
_lock         = threading.Lock()
_startup_done = threading.Event()


def buscar_cotacao(ticker: str) -> dict | None:
    try:
        info     = yf.Ticker(ticker).fast_info
        preco    = info.last_price
        anterior = info.previous_close
        if not preco or not anterior or preco <= 0:
            return None
        variacao = ((preco - anterior) / anterior) * 100
        return {"price": round(float(preco), 6), "chg": round(float(variacao), 4)}
    except Exception as e:
        log.warning("  ⚠️  %s: %s", ticker, e)
        return None


def atualizar_cache() -> None:
    log.info("Atualizando cotações — %s", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    resultado, sucesso = {}, 0
    for ticker in TICKERS:
        dado = buscar_cotacao(ticker)
        if dado:
            resultado[ticker] = dado
            sucesso += 1
    with _lock:
        _cache["data"]       = resultado
        _cache["updated_at"] = datetime.now().isoformat()
        _cache["success"]    = sucesso
        _cache["total"]      = len(TICKERS)
    log.info("Cache: %d/%d símbolos OK", sucesso, len(TICKERS))


def loop_atualizacao() -> None:
    atualizar_cache()
    _startup_done.set()
    while True:
        time.sleep(CACHE_TTL)
        atualizar_cache()


# ── ROTAS ─────────────────────────────────────────────────────────

@app.route("/api/quotes")
def api_quotes():
    _startup_done.wait(timeout=90)
    with _lock:
        return jsonify({
            "data":       _cache["data"],
            "updated_at": _cache["updated_at"],
            "success":    _cache["success"],
            "total":      _cache["total"],
        })


@app.route("/api/health")
def api_health():
    with _lock:
        return jsonify({
            "status":     "ok",
            "ready":      _startup_done.is_set(),
            "updated_at": _cache["updated_at"],
            "symbols":    f"{_cache['success']}/{_cache['total']}",
        })


@app.route("/")
@app.route("/app")
@app.route("/index.html")
def index():
    base = Path(__file__).parent
    return send_from_directory(str(base), "app.html")


@app.route("/manifest.json")
def manifest():
    return send_from_directory(str(Path(__file__).parent), "manifest.json")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(str(Path(__file__).parent), filename)


# ── MAIN ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Iniciando FinanceHub Mobile...")
    t = threading.Thread(target=loop_atualizacao, daemon=True)
    t.start()
    _startup_done.wait(timeout=90)
    log.info("Pronto! Porta %d", PORT)
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
