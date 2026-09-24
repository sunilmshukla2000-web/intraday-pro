import yfinance as yf
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
import os
import time
import math
import requests
import pyotp
import io
import urllib.parse
import json
import struct
import threading
import websocket

# --- 🛡️ MSTOCK CACHE & WEBSOCKET ENGINE ---
MSTOCK_CACHE = {
    "session": None,
    "access_token": None,
    "token_map": {},
    "df_options": pd.DataFrame() 
}

WS_CACHE = {
    "ws": None,
    "connected": False,
    "data": {} 
}

# --- 📡 WEBSOCKET BACKGROUND HELPER ---
def start_mstock_ws(api_key, access_token, log_func=print):
    if WS_CACHE["connected"]: return
    
    log_func("🚀 Connecting WebSocket to mStock Server...")

    def on_open(ws):
        ws.send(f"LOGIN:{access_token}")
        time.sleep(0.5) 
        ws.send(json.dumps({"a": "mode", "v": ["full"]}))
        WS_CACHE["connected"] = True
        log_func("🟢 Live WebSocket Engine is READY & LISTENING!")

        def heartbeat():
            while WS_CACHE["connected"]:
                try:
                    ws.send(json.dumps({"a": "h", "v": []}))
                    time.sleep(10)
                except:
                    break
        threading.Thread(target=heartbeat, daemon=True).start()

    def on_message(ws, message):
        try:
            payload = message[4:]
            token = struct.unpack('>I', payload[0:4])[0]
            ltp = struct.unpack('>I', payload[4:8])[0] / 100.0
            vol = struct.unpack('>I', payload[16:20])[0]
            tot_buy = struct.unpack('>I', payload[20:24])[0]
            tot_sell = struct.unpack('>I', payload[24:28])[0]
            oi = struct.unpack('>I', payload[48:52])[0]
            
            WS_CACHE["data"][token] = {"ltp": ltp, "vol": vol, "oi": oi, "buy": tot_buy, "sell": tot_sell}
        except: pass

    def on_error(ws, error): 
        WS_CACHE["connected"] = False

    def on_close(ws, close_status_code, close_msg):
        WS_CACHE["connected"] = False
        WS_CACHE["data"].clear() # 🔥 MAIN FIX: Connection tootne par purana atka hua data saaf karo
        time.sleep(3)
        start_mstock_ws(api_key, access_token, log_func) 

    def run_ws():
        ws_url = f"wss://ws.mstock.trade?API_KEY={api_key}&ACCESS_TOKEN={access_token}"
        WS_CACHE["ws"] = websocket.WebSocketApp(ws_url, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
        WS_CACHE["ws"].run_forever()

    threading.Thread(target=run_ws, daemon=True).start()

def init_mstock(user, pwd, totp_sec, api_key):
    global MSTOCK_CACHE
    if MSTOCK_CACHE.get(f"access_token_{api_key}") and len(MSTOCK_CACHE.get("token_map", {})) > 0:
        return MSTOCK_CACHE[f"access_token_{api_key}"], MSTOCK_CACHE["token_map"]

    session = requests.Session()
    MSTOCK_CACHE[f"session_{api_key}"] = session

    res1 = session.post('https://api.mstock.trade/openapi/typea/connect/login',
                        headers={'X-Mirae-Version': '1', 'Content-Type': 'application/x-www-form-urlencoded'},
                        data={'username': user, 'password': pwd})

    if res1.json().get("status") == "success":
        totp = pyotp.TOTP(totp_sec)
        res2 = session.post('https://api.mstock.trade/openapi/typea/session/verifytotp',
                            headers={'X-Mirae-Version': '1', 'Content-Type': 'application/x-www-form-urlencoded'},
                            data={'api_key': api_key, 'totp': totp.now()})

        resp_data = res2.json()
        if resp_data.get("status") == "success":
            temp_token = resp_data['data']['access_token']
            start_mstock_ws(api_key, temp_token)

            master_url = 'https://api.mstock.trade/openapi/typea/instruments/scriptmaster'
            master_res = session.get(master_url, headers={'X-Mirae-Version': '1', 'Authorization': f'token {api_key}:{temp_token}'})

            if master_res.status_code == 200:
                df_master = pd.read_csv(io.StringIO(master_res.text))
                cols = df_master.columns.astype(str).str.strip().str.lower().tolist()
                df_master.columns = cols
                
                sym_col = next((c for c in cols if 'symbol' in c), cols[2] if len(cols)>2 else None)
                tok_col = next((c for c in cols if 'token' in c), cols[0] if len(cols)>0 else None)
                exc_col = next((c for c in cols if 'exchange' in c and 'token' not in c), None)

                if sym_col and tok_col and exc_col:
                    df_eq = df_master[df_master[exc_col].astype(str).str.strip().str.upper().str.contains('NSE', na=False)]
                    temp_map = dict(zip(df_eq[sym_col].astype(str).str.strip(), df_eq[tok_col]))
                    df_nfo = df_master[df_master[exc_col].astype(str).str.strip().str.upper() == 'NFO']
                    
                    if len(temp_map) > 0:
                        MSTOCK_CACHE[f"access_token_{api_key}"] = temp_token
                        MSTOCK_CACHE["token_map"] = temp_map
                        MSTOCK_CACHE["df_options"] = df_nfo
                        return temp_token, temp_map
    raise Exception("MStock Login ya Script Master Fetch fail ho gaya!")

def calculate_vwap(df):
    if df.empty: return pd.Series([0.0])
    tp = (df['High'] + df['Low'] + df['Close']) / 3
    if 'Volume' not in df.columns or df['Volume'].sum() == 0: return tp  
    return (tp * df['Volume']).cumsum() / df['Volume'].cumsum()

def get_smart_option(stock, price, signal_type):
    df_opt = MSTOCK_CACHE.get("df_options")
    if df_opt is None or df_opt.empty: return None, "No Data in Cache", 1  
        
    opt_type = "CE" if ("BREAKOUT" in signal_type or "BUY" in signal_type) else "PE"
    
    if stock in ["NIFTY", "NIFTY_IDX", "^NSEI"]:
        stock_rows = df_opt[df_opt['name'].astype(str).str.strip().str.upper() == "NIFTY"].copy()
    elif stock in ["BANKNIFTY", "BANKNIFTY_IDX", "^NSEBANK"]:
        stock_rows = df_opt[df_opt['name'].astype(str).str.strip().str.upper() == "BANKNIFTY"].copy()
    else:
        super_clean = str(stock).strip().upper().replace('-', '').replace('&', '').replace('_', '')
        df_opt['safe_name'] = df_opt['name'].astype(str).str.strip().str.upper().str.replace('-', '').str.replace('&', '').str.replace('_', '')
        df_opt['safe_tsym'] = df_opt['tradingsymbol'].astype(str).str.strip().str.upper().str.replace('-', '').str.replace('&', '').str.replace('_', '')
        stock_rows = df_opt[(df_opt['safe_name'] == super_clean) | (df_opt['safe_tsym'].str.startswith(super_clean))].copy()
    
    if stock_rows.empty: return None, f"Cash Stock", 1
        
    match = stock_rows[stock_rows['instrument_type'].astype(str).str.strip().str.upper() == opt_type].copy()
    if match.empty: return None, f"No {opt_type} Found", 1
        
    match['expiry_dt'] = pd.to_datetime(match['expiry'], errors='coerce')
    future_match = match[match['expiry_dt'] >= pd.Timestamp.now().normalize()]
    if future_match.empty: return None, "All Expiries Old", 1
        
    match = future_match
    nearest_exp = match['expiry_dt'].min()
    match_curr = match[match['expiry_dt'] == nearest_exp].copy()
    
    match_curr['diff'] = abs(pd.to_numeric(match_curr['strike'], errors='coerce') - price)
    top_5 = match_curr.sort_values('diff').head(5)
    
    tokens_to_sub = top_5['instrument_token'].astype(int).tolist()
    if WS_CACHE["connected"] and WS_CACHE["ws"] and tokens_to_sub:
        try:
            WS_CACHE["ws"].send(json.dumps({"a": "subscribe", "v": tokens_to_sub}))
            time.sleep(0.5) 
        except: pass
        
    best_opt = top_5.iloc[0]
    best_score = -1
    for idx, row in top_5.iterrows():
        tk = int(row['instrument_token'])
        live_data = WS_CACHE["data"].get(tk, {})
        vol = live_data.get("vol", 0)
        buy_qty = live_data.get("buy", 0)
        sell_qty = live_data.get("sell", 0)
        score = vol
        if buy_qty > (sell_qty * 1.2): score *= 1.5 
        if score > best_score:
            best_score = score
            best_opt = row
            
    exact_symbol = str(best_opt['tradingsymbol']).strip()
    lot_size = int(best_opt['lot_size'])
    strike = int(pd.to_numeric(best_opt['strike'], errors='coerce'))
    return exact_symbol, f"{strike} {opt_type}", lot_size

def get_live_option_premium(opt_symbol, api_key):
    try:
        def fetch_price():
            df_opt = MSTOCK_CACHE.get("df_options")
            if df_opt is not None and not df_opt.empty:
                clean_opt = str(opt_symbol).strip().upper()
                tk_row = df_opt[df_opt['tradingsymbol'].astype(str).str.strip().str.upper() == clean_opt]
                if not tk_row.empty:
                    tk = int(tk_row.iloc[0]['instrument_token'])
                    # Sirf tabhi memory se padho jab connection ZINDA ho
                    if WS_CACHE["connected"] and WS_CACHE["ws"]:
                        WS_CACHE["ws"].send(json.dumps({"a": "subscribe", "v": [tk]}))
                        if tk in WS_CACHE["data"] and WS_CACHE["data"][tk]["ltp"] > 0:
                            return float(WS_CACHE["data"][tk]["ltp"])
                        
            token = MSTOCK_CACHE.get(f"access_token_{api_key}")
            session = MSTOCK_CACHE.get(f"session_{api_key}")
            if not token or not session or not opt_symbol: return 0.0
                
            headers = {'X-Mirae-Version': '1', 'Authorization': f'token {api_key}:{token}'}
            params = {'i': [f'NFO:{opt_symbol}']}
            res = session.get('https://api.mstock.trade/openapi/typea/instruments/quote/ltp', params=params, headers=headers, timeout=3)
            if res.status_code == 200:
                data = res.json().get('data', [])
                if data and isinstance(data, list):
                    return float(data[0].get('last_price', 0.0))
            return 0.0
            
        p = fetch_price()
        if p <= 0.0:
            time.sleep(0.5)
            p = fetch_price()
        return p
    except: return 0.0

def get_live_spot_price(stock_sym, api_key):
    try:
        def fetch_price():
            tk_map = MSTOCK_CACHE.get("token_map", {})
            clean_sym = str(stock_sym).strip().upper()
            
            # Nifty aur BankNifty ke names mStock format me map karna
            if clean_sym == "^NSEI": clean_sym = "NIFTY 50"
            elif clean_sym == "^NSEBANK": clean_sym = "NIFTY BANK"
            elif clean_sym.startswith("^"): clean_sym = clean_sym.replace("^", "")
            
            tk = tk_map.get(clean_sym)
            if tk:
                tk = int(tk)
                # Seedha WebSocket pipe se current LTP nikalna (Zero API limit load)
                # Seedha WebSocket pipe se current LTP nikalna
                if WS_CACHE["connected"] and WS_CACHE["ws"]:
                    WS_CACHE["ws"].send(json.dumps({"a": "subscribe", "v": [tk]}))
                    if tk in WS_CACHE["data"] and WS_CACHE["data"][tk]["ltp"] > 0:
                        return float(WS_CACHE["data"][tk]["ltp"])
                return 0.0 # Agar WS dead hai toh Yahoo par fallback karo
            
        p = fetch_price()
        if p <= 0.0:
            time.sleep(0.4)
            p = fetch_price()
        return p
    except: return 0.0

# --- 🔴 MSTOCK REAL MONEY ORDER FUNCTION ---
def place_mstock_order(session, api_key, token, exc, tsym, qty, action, current_price=0.0, product="MIS"):
    try:
        import json
        if not session or not token: return {"status": "error", "message": "No active session"}
        
        # 🔥 SMART ORDER TYPE LOGIC (SLIPPAGE FIX)
        order_type = "LIMIT"  
        if exc == "NFO":
            if current_price <= 0.0:
                return {"status": "error", "message": "REJECTED: Live Premium not available for Option Limit Order"}
            safe_price = round(current_price * 1.02, 1) if action == "BUY" else round(current_price * 0.98, 1)
        else:
            safe_price = round(current_price * 1.002, 2) if action == "BUY" else round(current_price * 0.998, 2)
            
        url = 'https://api.mstock.trade/openapi/typea/orders/regular'
        headers = {
            'X-Mirae-Version': '1',
            'Authorization': f'token {api_key}:{token}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        payload = {
            'exchange': exc,
            'tradingsymbol': tsym,
            'transaction_type': action,
            'order_type': order_type,
            'quantity': str(int(qty)),
            'product': product,
            'validity': 'DAY',
            'price': str(safe_price),
            'trigger_price': '0',
            'disclosed_quantity': '0'
        }
        
        res = session.post(url, headers=headers, data=payload, timeout=5)
        
        if res.text.strip() == "" or "<html" in res.text.lower():
            return {"status": "error", "message": f"Broker returned invalid blank page (HTTP {res.status_code})"}
            
        resp_json = res.json()
        if isinstance(resp_json, list) and len(resp_json) > 0:
            return resp_json[0]
            
        return resp_json
        
    except json.JSONDecodeError:
        return {"status": "error", "message": f"Broker format error: {res.text[:100]}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- 🚀 THE MAIN ENGINE (STREAMLIT READY) ---
def run_stock_scanner(params):
    nifty50_list = params['nifty_list']
    log_func = params.get('log_func', print) 
    
    th = params['vol_sense']
    t_pct = params['target']
    s_pct = params['sl']
    risk_mode = params['risk_mode'] 
    ema_filter_on = params['ema_on']
    hide_wide_orb = params['hide_wide']
    triple_on = params.get('triple_on', False)
    signal_tracker = params['tracker']
    
    orb_wide_limit = params.get('orb_wide', 1.2)
    vwap_dist_limit = params.get('vwap_dist', 1.0)
    wick_limit = params.get('wick_limit', 1.5)
    max_vol_limit = params.get('max_vol', 2.5)
    orb_dist_limit = params.get('orb_dist', 0.8) 
    
    m_user = params.get('m_user', '')
    m_pwd = params.get('m_pwd', '')
    m_totp = params.get('m_totp', '')
    m_api_key = params.get('m_api_key', '')
    
    mstock_token, mstock_token_map = None, {}
    if m_api_key:
        try:
            mstock_token, mstock_token_map = init_mstock(m_user, m_pwd, m_totp, m_api_key)
        except Exception as e:
            log_func(f"⚠️ mStock Login Wait/Error: {e}")

    # Nifty Base Trend
    try:
        n_data = yf.Ticker("^NSEI").history(period="5d", interval="1d").ffill().dropna()
        n_prev_close = n_data['Close'].iloc[-2]
        n_curr_close = n_data['Close'].iloc[-1]
        n_chg = ((n_curr_close - n_prev_close) / n_prev_close) * 100
    except:
        n_chg = 0.0

    # --- BULK DOWNLOAD ENGINE ---
    log_func(f"🚀 Fetching {len(nifty50_list)} symbols in ONE GO (Anti-Block)...")
    
    yf_syms = [sym + ".NS" if not sym.startswith("^") else sym for sym in nifty50_list]
    bulk_data = yf.download(yf_syms, period="2d", interval="5m", progress=False)

    for s in nifty50_list:
        try:
            yf_sym = s + ".NS" if not s.startswith("^") else s
            
            if len(nifty50_list) == 1:
                df = bulk_data.copy()
            else:
                try:
                    df = bulk_data.xs(yf_sym, level=1, axis=1).copy()
                except KeyError:
                    continue 
                    
            df = df.dropna(how='all')
            if df.empty: continue

            df.index = pd.to_datetime(df.index) 
            if df.index.tz is not None: df.index = df.index.tz_convert('Asia/Kolkata')
            
            latest_date = df.index[-1].date()
            td_df = df[df.index.date == latest_date].between_time('09:15', '15:30').ffill()
            
            if len(td_df) < 3: continue 
            
            # --- TIME ZONE LOGIC ---
            time_master = params.get('time_filter', True)
            time_allowed = True
            
            if time_master:
                time_allowed = False
                curr_time = td_df.index[-1].time()
                from datetime import time as dtime
                if params.get('z1', False) and (dtime(9, 15) <= curr_time < dtime(9, 45)): time_allowed = True
                elif params.get('z2', True) and (dtime(9, 45) <= curr_time < dtime(11, 30)): time_allowed = True
                elif params.get('z3', False) and (dtime(11, 30) <= curr_time < dtime(13, 30)): time_allowed = True
                elif params.get('z4', True) and (dtime(13, 30) <= curr_time < dtime(14, 45)): time_allowed = True
                elif params.get('z5', False) and (dtime(14, 45) <= curr_time <= dtime(15, 30)): time_allowed = True
            
            cp = float(td_df['Close'].iloc[-1])
            
            # --- 🔥 SUPER FAST MSTOCK SPOT SYNC FOR LIVE TRADES ---
            if s in signal_tracker and signal_tracker[s]["status"] == "LIVE":
                try:
                    fast_cp = get_live_spot_price(s, m_api_key)
                    if fast_cp > 0:
                        cp = fast_cp # Yahoo ka delayed price mStock ke live price se replace ho gaya
                except: pass
            
            # --- Update Existing Trades ---
            if s in signal_tracker:
                entry = signal_tracker[s]["entry"]
                locked = signal_tracker[s]["locked"]
                action_type = signal_tracker[s]["action"]
                t_dist, s_dist = signal_tracker[s]["t_dist"], signal_tracker[s]["s_dist"]
                
                opt_sym = signal_tracker[s].get("opt_sym")
                opt_entry = signal_tracker[s].get("opt_entry", 0.0)
                trade_qty = signal_tracker[s].get("trade_qty", 1) # Jo trigger ke time par quantity thi
                
                if not locked:
                    pts_diff = (cp - entry) if action_type == "BUY" else (entry - cp)
                    
                    tsl_on = params.get('tsl_on', False)
                    base_atr = s_dist if s_dist > 0 else (entry * 0.01)
                    
                    if tsl_on:
                        trigger_pts = params.get('tsl_trigger', 1.0) * base_atr
                        step_pts = params.get('tsl_step', 0.5) * base_atr
                        
                        max_fav = signal_tracker[s].get("max_fav", 0.0)
                        if pts_diff > max_fav:
                            signal_tracker[s]["max_fav"] = pts_diff
                            max_fav = pts_diff
                            
                        if max_fav >= trigger_pts:
                            steps = int((max_fav - trigger_pts) / step_pts) if step_pts > 0 else 0
                            new_locked_pts = 0.0 + (steps * step_pts)
                            
                            curr_locked = signal_tracker[s].get("locked_pts", -s_dist)
                            if new_locked_pts > curr_locked:
                                signal_tracker[s]["locked_pts"] = new_locked_pts
                                signal_tracker[s]["kundali"] += f"\n🛡️ TRAIL UP: +{new_locked_pts:.2f} pts Locked!"
                                
                    locked_pts = signal_tracker[s].get("locked_pts", -s_dist)
                    
                    if pts_diff >= t_dist:
                        signal_tracker[s]["status"] = "🎯 TARGET HIT"
                        signal_tracker[s]["locked"] = True
                    elif pts_diff <= locked_pts:
                        signal_tracker[s]["status"] = "🛡️ TRAIL HIT" if locked_pts > 0 else "🛡️ C2C HIT" if locked_pts == 0 else "🛑 SL HIT"
                        signal_tracker[s]["locked"] = True
                        
                final_pts_diff = (cp - entry) if action_type == "BUY" else (entry - cp)
                
                # --- NAYA LOGIC: Cash aur Option ke P/L alag calculate karna ---
                opt_pts_diff = 0.0
                pl_rs = 0.0
                opt_live = 0.0
                
                if opt_sym and opt_entry > 0:
                    live_opt_price = get_live_option_premium(opt_sym, m_api_key)
                    if live_opt_price > 0:
                        opt_live = live_opt_price
                        opt_pts_diff = (live_opt_price - opt_entry) # Option Premium Points
                        pl_rs = opt_pts_diff * trade_qty
                        signal_tracker[s]["opt"] = f"{opt_sym.split('-')[-1]} ({opt_entry:.1f} ➔ {live_opt_price:.1f})"
                else:
                    pl_rs = final_pts_diff * trade_qty # Cash Profit
                
                if opt_sym:
                    strike_lot_str = f"{opt_sym.split('-')[-1]}"
                else:
                    strike_lot_str = "CASH (Eq)"

                tgt_price = (entry + t_dist) if action_type == 'BUY' else (entry - t_dist)
                curr_locked = signal_tracker[s].get("locked_pts", -s_dist)
                sl_price = (entry + curr_locked) if action_type == 'BUY' else (entry - curr_locked)
                
                if curr_locked > -s_dist:
                    tgt_sl_str = f"{tgt_price:.2f} | {sl_price:.2f} 🔄"
                else:
                    tgt_sl_str = f"{tgt_price:.2f} | {sl_price:.2f}"
                    
                fund_req = (opt_entry * trade_qty) if (opt_sym and opt_entry > 0) else (entry * trade_qty)

                # --- NAYA RESULT FORMAT (Dono data ek sath) ---
                res = {
                    "s": f"{'🔼' if action_type == 'BUY' else '🔽'} {s.replace('^NSEI', 'NIFTY').replace('^NSEBANK', 'BANKNIFTY')}", 
                    "time": signal_tracker[s]["time"], 
                    "ent": entry,               # Spot (Cash) Entry
                    "p": cp,                    # Spot (Cash) LTP
                    "opt_ent": opt_entry,       # Option Entry Premium
                    "opt_p": opt_live,          # Option Live Premium
                    "spot_pl_pts": final_pts_diff, # Spot me kitne points mile
                    "opt_pl_pts": opt_pts_diff,    # Premium me kitne points mile
                    "tgt_sl": tgt_sl_str,  
                    "pl_rs_disp": pl_rs, 
                    "trade_qty": trade_qty,
                    "fund_req_disp": fund_req,
                    "strike_lot": strike_lot_str,
                    "status": signal_tracker[s]["status"], 
                    "sig": signal_tracker[s]["sig"],
                    "is_mstock": signal_tracker[s].get("is_mstock", False)
                }
                
                signal_tracker[s]["last_res"] = res
            
            # --- PRICE RANGE FILTER ---
            min_p = params.get('min_price', 0.0)
            max_p = params.get('max_price', 100000.0)
            
            if not (min_p <= cp <= max_p) and not s.startswith("^"):
                continue  
                
            h, l = float(td_df['High'].iloc[:3].max()), float(td_df['Low'].iloc[:3].min())
            vw = calculate_vwap(td_df).iloc[-1]
            
            avg_vol = df['Volume'].rolling(10).mean().iloc[-1]
            curr_vol = td_df['Volume'].iloc[-1]
            if curr_vol == 0 and len(td_df) > 1: curr_vol = td_df['Volume'].iloc[-2]
            vol_ratio = curr_vol / avg_vol if pd.notna(avg_vol) and avg_vol > 0 else 0
            
            e9_s = ta.ema(df['Close'].ffill(), length=9).iloc[-1]
            rsi = ta.rsi(df['Close'].ffill(), length=14).iloc[-1]
            
            vwap_dist = abs((cp - vw) / vw) * 100
            orb_high_dist = ((cp - h) / h) * 100
            orb_low_dist = ((l - cp) / l) * 100
            wide = (((h-l) / cp) * 100 > orb_wide_limit)
            
            c_o, c_c, c_h, c_l = float(td_df['Open'].iloc[-1]), cp, float(td_df['High'].iloc[-1]), float(td_df['Low'].iloc[-1])
            body = abs(c_c - c_o)
            safe_body = body if body > 0 else 0.05
            upper_wick, lower_wick = c_h - max(c_c, c_o), min(c_c, c_o) - c_l
            wick_trap_buy, wick_trap_sell = upper_wick > (wick_limit * safe_body), lower_wick > (wick_limit * safe_body)
            
            vol_ok = (vol_ratio >= th) and (vol_ratio <= max_vol_limit)
            if s.startswith("^"): vol_ok = True  # Ignore volume constraint for Index itself
            
            rsi_bull_min, rsi_bull_max = params.get('rsi_bull_min', 60), params.get('rsi_bull_max', 80)
            rsi_bear_min, rsi_bear_max = params.get('rsi_bear_min', 20), params.get('rsi_bear_max', 40)
            
            strict_buy = (rsi_bull_min <= rsi <= rsi_bull_max) and (vwap_dist <= vwap_dist_limit)
            strict_sell = (rsi_bear_min <= rsi <= rsi_bear_max) and (vwap_dist <= vwap_dist_limit)
            
            now_t = td_df.index[-1].time()
            c_m = now_t.hour * 60 + now_t.minute 
            if time_master and c_m < 570: 
                time_allowed = False 

            trend_align_buy = (e9_s > vw)
            trend_align_sell = (e9_s < vw)
            
            safe_h = h + (h * 0.001)
            safe_l = l - (l * 0.001)

            buy_ok = (cp > e9_s if ema_filter_on else True) and (not triple_on or n_chg > 0) and not wick_trap_buy and vol_ok and (orb_high_dist <= orb_dist_limit) and time_allowed and trend_align_buy
            sell_ok = (cp < e9_s if ema_filter_on else True) and (not triple_on or n_chg < 0) and not wick_trap_sell and vol_ok and (orb_low_dist <= orb_dist_limit) and time_allowed and trend_align_sell

            sig = ""
            if cp >= safe_h and cp > vw and buy_ok and strict_buy: sig = "BREAKOUT" if not wide else "WIDE BREAKOUT"
            elif cp <= safe_l and cp < vw and sell_ok and strict_sell: sig = "BREAKDOWN" if not wide else "WIDE BREAKDOWN"
            
            if hide_wide_orb and wide and "CONFIRMED" not in sig: continue
                
            if sig and s not in signal_tracker:
                is_index_sym = True if s.startswith("^") else False
                
                # Jo check box (Option ya Eq) ticker ke hisab se hai wahi assign karo
                is_opt_trade = is_index_sym or params.get("auto_opt", False)
                
                if is_opt_trade:
                    exact_opt_sym, opt_str, lot_val = get_smart_option(s, cp, sig)
                    opt_entry_price = get_live_option_premium(exact_opt_sym, m_api_key) if exact_opt_sym else 0.0
                    if exact_opt_sym: opt_str = f"{opt_str} ({opt_entry_price:.1f})"
                else:
                    exact_opt_sym, opt_str, lot_val = None, "CASH (Eq)", 1
                    opt_entry_price = 0.0
                
                if risk_mode == "ATR":
                    atr_val = (h - l) 
                    s_dist = max(s_pct * atr_val, cp * 0.004)
                    t_dist = s_dist * 2.0     
                else:
                    t_dist, s_dist = cp * (t_pct / 100), cp * (s_pct / 100)
                
                action_type = "BUY" if "BREAKOUT" in sig else "SELL"

                # --- 🔴 LIVE ORDER & QUANTITY CALCULATION ---
                is_master_on = params.get("auto_on", False)
                order_response = "PAPER TRADE (Master Switch OFF)"
                is_mstock_success = False
                
                # Quantity calculation according to Master Switch
                trade_qty = 0
                if is_opt_trade:
                    if is_master_on:
                        lots = params.get("idx_lots", 1) if is_index_sym else params.get("opt_lots", 1)
                        trade_qty = lot_val * lots
                    else:
                        trade_qty = lot_val * 1 # Paper Trade: 1 Lot
                else:
                    if is_master_on:
                        cash_cap = params.get("capital", 10000.0)
                        trade_qty = int(cash_cap / cp) 
                        if trade_qty < 1: trade_qty = 1
                    else:
                        trade_qty = 1 # Paper Trade: 1 Qty

                # Fire order ONLY if master is ON
                if is_master_on and trade_qty >= 1:
                    exc = "NFO" if is_opt_trade else "NSE"
                    trade_sym = exact_opt_sym if is_opt_trade else f"{s}-EQ"
                    broker_action = "BUY" if is_opt_trade else action_type
                        
                    session = MSTOCK_CACHE.get(f"session_{m_api_key}")
                    acc_tok = MSTOCK_CACHE.get(f"access_token_{m_api_key}")
                    ord_price = opt_entry_price if is_opt_trade else cp
                    
                    raw_res = place_mstock_order(session, m_api_key, acc_tok, exc, trade_sym, trade_qty, broker_action, current_price=ord_price)
                    
                    if isinstance(raw_res, dict) and raw_res.get("status") == "error":
                        order_response = f"BROKER RESPONSE: {raw_res}"
                    else:
                        order_response = f"BROKER RESPONSE: SUCCESS {raw_res}"
                        is_mstock_success = True  
                        
                        # --- 🔥 REAL PRICE SYNC (Fix for Trailing SL) ---
                        time.sleep(1.0) 
                        try:
                            if is_opt_trade:
                                fresh_p = get_live_option_premium(exact_opt_sym, m_api_key)
                                if fresh_p > 0: opt_entry_price = fresh_p 
                            else:
                                fresh_df = yf.download(yf_sym, period="1d", interval="1m", progress=False)
                                if not fresh_df.empty: cp = float(fresh_df['Close'].iloc[-1])
                        except: pass
                        # ------------------------------------------------
                        
                    params['log_func'](f"⚡ LIVE ORDER SENT: {trade_sym} | Qty: {trade_qty} | Action: {broker_action}")

                kundali_text = f"=== DEEP KUNDALI FOR {s} ===\n"
                kundali_text += f"Time: {datetime.now().strftime('%H:%M:%S')}\n"
                kundali_text += f"Signal: {sig} (Action: {action_type})\n"
                kundali_text += f"Live Price: ₹{cp:.2f} | VWAP: ₹{vw:.2f}\n"
                kundali_text += f"Volume Spike: {vol_ratio:.2f}x (Allowed: {th}x to {max_vol_limit}x)\n"
                kundali_text += f"VWAP Distance: {vwap_dist:.2f}%\n"
                kundali_text += f"ORB Range: High {h:.2f} | Low {l:.2f}\n"
                kundali_text += f"Trade Qty/Lots: {trade_qty}\n"
                kundali_text += f"Option Selected: {opt_str if exact_opt_sym else 'CASH EQUITIES'}\n"
                kundali_text += f"Engine Status: TRADE CAPTURED 🚀\n"
                kundali_text += f"🔴 {order_response}"

                signal_tracker[s] = {
                    "time": datetime.now().strftime('%H:%M'), 
                    "entry": cp, "locked": False, "sig": sig, "opt": opt_str,
                    "t_dist": t_dist, "s_dist": s_dist,
                    "action": action_type, "opt_sym": exact_opt_sym, "opt_entry": opt_entry_price,
                    "trade_qty": trade_qty, # Ye qty save kar li aage calculation ke liye
                    "status": "LIVE",
                    "kundali": kundali_text,
                    "is_mstock": is_mstock_success 
                }
                log_func(f"🎯 NEW SIGNAL: {s} -> {sig} @ {cp:.2f}")

            

        except Exception as e:
            pass 
            
    final_results = [v["last_res"] for k, v in signal_tracker.items() if "last_res" in v]
    log_func("✅ Scan Complete!")
    return final_results, signal_tracker