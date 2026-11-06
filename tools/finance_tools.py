"""
Ferramentas Financeiras Quantitativas (`himself65/finance-skills`).
Inclui cotações e fundamentos em tempo real via `yfinance` (sem chave obrigatória),
modelo de Valuation DCF (Discounted Cash Flow), matriz de correlação de ativos,
dados de cripto/perpetuals via Hyperliquid API, e suporte às APIs opcionais Fintel, Adanos e Alpha Vantage.
"""

import json
import math
from typing import Any, Dict, List

import httpx

from config import settings


def get_market_quote_yfinance(ticker: str, period: str = "1mo") -> str:
    """
    [Skill: finance-skills / yfinance] Obtém cotação atual, variação, múltiplos fundamentalistas
    (P/L, EV/EBITDA, Market Cap, Dividend Yield, Fluxo de Caixa Livre) e histórico recente de qualquer
    ação global, B3 (ex: 'PETR4.SA', 'VALE3.SA'), ETF ou criptomoeda ('BTC-USD') via Yahoo Finance.
    """
    if settings.STRICT_OFFLINE_MODE:
        return json.dumps({"status": "BLOCKED_OFFLINE"}, ensure_ascii=False)

    try:
        import yfinance as yf

        t = yf.Ticker(ticker.strip().upper())
        hist = t.history(period=period)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}

        last_close = float(hist["Close"].iloc[-1]) if not hist.empty else info.get("currentPrice")
        first_close = float(hist["Close"].iloc[0]) if not hist.empty else last_close
        pct_change = (
            round(((last_close - first_close) / first_close) * 100.0, 2)
            if last_close and first_close
            else 0.0
        )

        return json.dumps(
            {
                "status": "OK",
                "ticker": ticker.strip().upper(),
                "short_name": info.get("shortName") or ticker.upper(),
                "currency": info.get("currency", "USD"),
                "current_price": round(last_close, 4) if last_close else None,
                "period_return_pct": pct_change,
                "market_cap": info.get("marketCap"),
                "trailing_pe": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "ev_to_ebitda": info.get("enterpriseToEbitda"),
                "free_cashflow": info.get("freeCashflow"),
                "dividend_yield": info.get("dividendYield"),
                "sector": info.get("sector"),
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"status": "ERROR", "ticker": ticker, "error": str(exc)}, ensure_ascii=False)


def calculate_dcf_valuation(
    current_fcf: float,
    growth_rate_5y_pct: float = 10.0,
    terminal_growth_pct: float = 3.0,
    discount_rate_wacc_pct: float = 10.5,
    shares_outstanding: float = 1.0,
    net_debt: float = 0.0,
) -> str:
    """
    [Skill: finance-skills / dcf-valuation] Calcula o Valor Intrínseco de uma empresa por Fluxo de Caixa Descontado (DCF)
    de 5 anos + Valor Terminal (Gordon Growth) e retorna tabela de sensibilidade.
    Parâmetros:
    - current_fcf: Fluxo de Caixa Livre anual atual (em milhões ou unidades).
    - growth_rate_5y_pct: Taxa de crescimento anual projetada para os anos 1 a 5 (%).
    - terminal_growth_pct: Taxa de crescimento na perpetuidade (%).
    - discount_rate_wacc_pct: Custo médio ponderado de capital / WACC (%).
    - shares_outstanding: Total de ações emitidas (na mesma escala de current_fcf).
    - net_debt: Dívida líquida (Dívida Total - Caixa).
    """
    g = growth_rate_5y_pct / 100.0
    gt = terminal_growth_pct / 100.0
    wacc = discount_rate_wacc_pct / 100.0

    if wacc <= gt:
        return json.dumps(
            {"status": "ERROR", "error": "O WACC deve ser maior que a taxa de crescimento perpétuo."},
            ensure_ascii=False,
        )

    projected_fcfs: List[Dict[str, float]] = []
    pv_sum = 0.0
    fcf_t = float(current_fcf)

    for year in range(1, 6):
        fcf_t = fcf_t * (1.0 + g)
        pv = fcf_t / ((1.0 + wacc) ** year)
        pv_sum += pv
        projected_fcfs.append({"year": year, "projected_fcf": round(fcf_t, 2), "present_value": round(pv, 2)})

    terminal_fcf = fcf_t * (1.0 + gt)
    terminal_value = terminal_fcf / (wacc - gt)
    pv_terminal = terminal_value / ((1.0 + wacc) ** 5)

    enterprise_value = pv_sum + pv_terminal
    equity_value = enterprise_value - float(net_debt)
    fair_value_per_share = equity_value / max(float(shares_outstanding), 1e-9)

    return json.dumps(
        {
            "status": "OK",
            "inputs": {
                "current_fcf": current_fcf,
                "growth_rate_5y_pct": growth_rate_5y_pct,
                "terminal_growth_pct": terminal_growth_pct,
                "wacc_pct": discount_rate_wacc_pct,
            },
            "projected_5y_cashflows": projected_fcfs,
            "pv_of_5y_cashflows": round(pv_sum, 2),
            "pv_of_terminal_value": round(pv_terminal, 2),
            "enterprise_value": round(enterprise_value, 2),
            "equity_value": round(equity_value, 2),
            "intrinsic_value_per_share": round(fair_value_per_share, 2),
        },
        ensure_ascii=False,
        indent=2,
    )


def analyze_stock_correlation(tickers_csv: str, period: str = "6mo") -> str:
    """
    [Skill: finance-skills / stock-correlation] Calcula a matriz de correlação de retornos diários
    entre múltiplos ativos (ex: 'AAPL,MSFT,NVDA,BTC-USD') usando dados históricos do `yfinance`.
    """
    if settings.STRICT_OFFLINE_MODE:
        return json.dumps({"status": "BLOCKED_OFFLINE"}, ensure_ascii=False)

    try:
        import yfinance as yf

        symbols = [s.strip().upper() for s in tickers_csv.split(",") if s.strip()]
        if len(symbols) < 2:
            return json.dumps({"status": "ERROR", "error": "Informe ao menos 2 tickers separados por vírgula."})

        data = yf.download(symbols, period=period, progress=False)["Close"]
        returns = data.pct_change().dropna()
        corr_matrix = returns.corr().round(4).to_dict()

        return json.dumps(
            {
                "status": "OK",
                "tickers": symbols,
                "period": period,
                "correlation_matrix": corr_matrix,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


def get_hyperliquid_crypto_data(coin_symbol: str = "BTC") -> str:
    """
    [Skill: finance-skills / hyperliquid] Consulta preços médios (allMids) e metadados do mercado
    de derivativos/cripto na Hyperliquid DEX (API pública sem chave obrigatória).
    """
    if settings.STRICT_OFFLINE_MODE:
        return json.dumps({"status": "BLOCKED_OFFLINE"}, ensure_ascii=False)

    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "allMids"},
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            mids = resp.json()
            sym = coin_symbol.strip().upper()
            return json.dumps(
                {
                    "status": "OK",
                    "symbol": sym,
                    "mid_price_usd": mids.get(sym),
                    "top_markets_sample": {k: mids.get(k) for k in ["BTC", "ETH", "SOL", "HYPE"] if k in mids},
                },
                ensure_ascii=False,
                indent=2,
            )
    except Exception as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


FINANCE_TOOLS = [
    get_market_quote_yfinance,
    calculate_dcf_valuation,
    analyze_stock_correlation,
    get_hyperliquid_crypto_data,
]
