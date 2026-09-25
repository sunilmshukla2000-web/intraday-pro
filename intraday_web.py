import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import os
import json
import time
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from scanner_web import run_stock_scanner, init_mstock, place_mstock_order, MSTOCK_CACHE

# =====================================================================
# 1. APP CONFIGURATION & STYLING
# =====================================================================
st.set_page_config(page_title="Intraday Pro - Web", layout="wide", page_icon="⚡")

st.markdown("""
    <style>
    .big-font { font-size:30px !important; font-weight: bold; color: #3498db; }
    .sub-font { font-size:14px !important; color: gray; }
    div[data-testid="stMetricValue"] { font-size: 22px; }
    </style>
""", unsafe_allow_html=True)

st.markdown('<p class="big-font">⚡ My Intraday Setup: Web Edition V5.0 (Pro Execution)</p>', unsafe_allow_html=True)
import pytz
ist = pytz.timezone('Asia/Kolkata')
st.markdown(f'<p class="sub-font">Last Refreshed: {datetime.now(ist).strftime("%H:%M:%S")}</p>', unsafe_allow_html=True)
st.markdown("---")

# =====================================================================
# 2. SMART TEXT FILE READER (Watchlists)
# =====================================================================
@st.cache_data
def load_list_from_file(filename, default_list):
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                content = f.read().replace('\n', ',')
                stocks = [s.strip().upper() for s in content.split(',') if s.strip()]
                if stocks: return sorted(list(set(stocks)))
        except: pass
    return default_list

backup_50 = ['RELIANCE', 'TCS', 'HDFCBANK', 'ICICIBANK', 'INFY', 'SBIN'] 
backup_100 = backup_50 + ['ABB', 'HAL', 'BEL']
backup_fno = backup_100 + ['DIXON', 'BSE', 'MCX']

list_50 = load_list_from_file("nifty50.txt", backup_50)
list_100 = load_list_from_file("nifty100.txt", backup_100)
list_fno = load_list_from_file("fno.txt", backup_fno)

# --- DEFAULT SLIDER SETTINGS (Ultra Strict) ---
DEFAULT_SLIDERS = {
    "rsi_bull": [60, 80], "rsi_bear": [20, 40],
    "orb_wide_limit": 1.0, "orb_dist_limit": 0.5,
    "vol_min": 2.5, "vol_max": 5.0,
    "wick_limit": 0.8, "vwap_dist_limit": 0.7,
    "min_price": 100.0, "max_price": 5000.0
}

# --- LOAD SAVED SETTINGS ---
def load_sliders():
    user_file = f"slider_{st.session_state.get('m_user', 'guest')}.json"
    if os.path.exists(user_file):
        try:
            with open(user_file, "r") as f:
                return json.load(f)
        except: pass
    return DEFAULT_SLIDERS.copy()

if 'sl_state' not in st.session_state:
    st.session_state.sl_state = load_sliders()

# =====================================================================
# 3. SIDEBAR (Strict Control Panel)
# =====================================================================
st.sidebar.header("⚙️ Advanced Control Panel")

col_r1, col_r2 = st.sidebar.columns([1.8, 1])
auto_refresh = col_r1.checkbox("🔄 Auto-Refresh", value=False)
refresh_dict = {"30 Sec": 30, "1 Min": 60, "2 Min": 120, "3 Min": 180, "5 Min": 300}
selected_label = col_r2.selectbox("Interval", options=list(refresh_dict.keys()), index=1, label_visibility="collapsed")
refresh_time = refresh_dict[selected_label]

watchlist_choice = st.sidebar.radio("1. Watchlist:", ["Nifty 50", "Nifty 100", "All F&O"], index=0)
st.sidebar.markdown("---")

current_risk_mode = st.session_state.get("risk_mode_key", "ATR")
risk_space = " " * 5 

with st.sidebar.expander(f"🎯 Risk & Reward{risk_space}[{current_risk_mode}]", expanded=False):
    col_s1, col_s2 = st.columns(2)
    target_pct = col_s1.number_input(f"Target ({current_risk_mode})", value=3.0, step=0.5)
    sl_pct = col_s2.number_input(f"SL ({current_risk_mode})", value=1.5, step=0.5)
    default_idx = 0 if current_risk_mode == "%" else 1
    risk_mode = st.selectbox("Risk Mode:", ["%", "ATR"], index=default_idx, key="risk_mode_key")

is_tsl_active = st.session_state.get("tsl_active_key", False)
tsl_dot = "🟢 ON" if is_tsl_active else "🔴 OFF"

with st.sidebar.expander(f"⚙️ Trailing SL [{tsl_dot}]", expanded=False):
    tsl_on = st.checkbox("Enable Trail SL", value=is_tsl_active, key="tsl_active_key")
    tsl_trigger = st.number_input("Trigger (ATR/Pts)", value=1.0, step=0.1)
    tsl_step = st.number_input("Trail Step (ATR/Pts)", value=0.5, step=0.1)

with st.sidebar.expander("⏱️ Time Zones", expanded=False):
    zone1 = st.checkbox("🔴 Zone 1 (09:15 - 09:45)", value=False)
    zone2 = st.checkbox("🟢 Zone 2 (09:45 - 11:30)", value=True)
    zone3 = st.checkbox("🟡 Zone 3 (11:30 - 13:30)", value=False)
    zone4 = st.checkbox("🟢 Zone 4 (13:30 - 14:45)", value=True)
    zone5 = st.checkbox("🔴 Zone 5 (14:45 - 15:30)", value=False)
        
with st.sidebar.expander("🎛️ Sliders (RSI, Vol, ORB)", expanded=False):
    st.markdown("**(Set Values & Save for Next Time)**")
    
    # Sliders linked to Session State
    rsi_bull = st.slider("RSI Bull Range", 0, 100, tuple(st.session_state.sl_state["rsi_bull"])) 
    rsi_bear = st.slider("RSI Bear Range", 0, 100, tuple(st.session_state.sl_state["rsi_bear"])) 
    orb_wide_limit = st.slider("ORB Wide % Max", 0.5, 3.0, float(st.session_state.sl_state["orb_wide_limit"]), step=0.1) 
    orb_dist_limit = st.slider("ORB Dist % Max", 0.1, 2.5, float(st.session_state.sl_state["orb_dist_limit"]), step=0.1) 
    vol_min = st.slider("Vol Min (x)", 1.0, 5.0, float(st.session_state.sl_state["vol_min"]), step=0.1) 
    vol_max = st.slider("Vol Max (x)", 1.5, 10.0, float(st.session_state.sl_state["vol_max"]), step=0.1)
    wick_limit = st.slider("Wick Max (x)", 0.2, 3.0, float(st.session_state.sl_state["wick_limit"]), step=0.1) 
    vwap_dist_limit = st.slider("VWAP Dist %", 0.1, 3.0, float(st.session_state.sl_state["vwap_dist_limit"]), step=0.1) 
    
    col_p1, col_p2 = st.columns(2)
    min_price = col_p1.number_input("Min ₹", value=float(st.session_state.sl_state["min_price"]), step=50.0)
    max_price = col_p2.number_input("Max ₹", value=float(st.session_state.sl_state["max_price"]), step=100.0)

    st.write("")
    btn_save, btn_reset = st.columns(2)
    
    # 💾 SAVE BUTTON
    if btn_save.button("💾 Save ", width="stretch"):
        new_sl = {
            "rsi_bull": list(rsi_bull), "rsi_bear": list(rsi_bear),
            "orb_wide_limit": orb_wide_limit, "orb_dist_limit": orb_dist_limit,
            "vol_min": vol_min, "vol_max": vol_max,
            "wick_limit": wick_limit, "vwap_dist_limit": vwap_dist_limit,
            "min_price": min_price, "max_price": max_price
        }
        # with open("slider_settings.json", "w") as f: json.dump(new_sl, f)
        with open(f"slider_{st.session_state.get('m_user', 'guest')}.json", "w") as f: json.dump(new_sl, f)
        st.session_state.sl_state = new_sl
        st.success("Saved!")
        time.sleep(0.5)
        st.rerun()
        
    # 🔄 DEFAULT BUTTON
    if btn_reset.button("🔄 Default", width="stretch"):
        # with open("slider_settings.json", "w") as f: json.dump(DEFAULT_SLIDERS, f)
        with open(f"slider_{st.session_state.get('m_user', 'guest')}.json", "w") as f: json.dump(DEFAULT_SLIDERS, f)
        st.session_state.sl_state = DEFAULT_SLIDERS.copy()
        st.success("Reset to Strict!")
        time.sleep(0.5)
        st.rerun()

with st.sidebar.expander("🎁 GiftNifty Manual Data", expanded=False):
    manual_prev = st.number_input("Prev Close", value=0.0)
    manual_curr = st.number_input("Live Price", value=0.0)

# =====================================================================
# 4. TOP DASHBOARD (Live Index & VIX)
# =====================================================================
@st.cache_data(ttl=60)
def get_live_indices():
    data = {}
    for idx in ["^NSEI", "^NSEBANK", "^INDIAVIX"]:
        try:
            df = yf.Ticker(idx).history(period="5d", interval="1d")
            df = df.dropna(subset=['Close'])
            if len(df) >= 2:
                cp = float(df['Close'].iloc[-1])
                prev_p = float(df['Close'].iloc[-2])
                data[idx] = {"cp": cp, "diff": cp - prev_p, "pct": ((cp - prev_p) / prev_p) * 100}
        except Exception: pass
    return data if data else None

idx_data = get_live_indices()
m1, m2, m3, m4 = st.columns(4)

if idx_data and "^NSEI" in idx_data: m1.metric("NIFTY 50", f"{idx_data['^NSEI']['cp']:,.2f}", f"{idx_data['^NSEI']['diff']:+.2f} ({idx_data['^NSEI']['pct']:+.2f}%)")
else: m1.metric("NIFTY 50", "Loading...", "0.0")

if idx_data and "^NSEBANK" in idx_data: m2.metric("BANK NIFTY", f"{idx_data['^NSEBANK']['cp']:,.2f}", f"{idx_data['^NSEBANK']['diff']:+.2f} ({idx_data['^NSEBANK']['pct']:+.2f}%)")
else: m2.metric("BANK NIFTY", "Loading...", "0.0")

if idx_data and "^INDIAVIX" in idx_data: m3.metric("INDIA VIX (Fear Gauge)", f"{idx_data['^INDIAVIX']['cp']:.2f}", f"{idx_data['^INDIAVIX']['pct']:+.2f}%", delta_color="inverse" if idx_data['^INDIAVIX']['pct'] > 0 else "normal")
else: m3.metric("INDIA VIX", "Loading...", "0.0")

gift_diff = manual_curr - manual_prev
gift_str = f"{'BULLISH 🚀' if gift_diff > 0 else 'BEARISH 🔻'}" if manual_prev > 0 else "Pending..."
m4.metric("GIFT NIFTY (Sentiment)", gift_str, f"{gift_diff:+.2f}" if manual_prev > 0 else None, delta_color="normal")
st.markdown("---")

# =====================================================================
# 5. SEGMENT SELECTOR & LIVE ALGO ENGINE
# =====================================================================
st.markdown("### 🔍 1. Select Segments to Scan (Screen View)")
c1, c2, c3, c4 = st.columns(4)
auto_n = c1.checkbox("NIFTY", value=True)
auto_bn = c2.checkbox("BANKNIFTY", value=False)
auto_eq = c3.checkbox("STOCKS (Cash Eq)", value=False)
auto_opt = c4.checkbox("STOCK-OPT (Option)", value=False)

st.write("")

# =====================================================================
# 🔐 SECURE PROFILE + PIN SYSTEM
# =====================================================================
m_user, m_pwd, m_totp, m_api_key = "", "", "", ""

if not st.session_state.get("is_logged_in", False):
    try:
        profiles = list(st.secrets["profiles"].keys())
    except:
        profiles = []
        
    if profiles:
        st.markdown("#### 🔐 Select Profile & Enter PIN")
        pc1, pc2, pc3 = st.columns([1.5, 1.5, 3])
        selected_profile = pc1.selectbox("Profile", options=[p.upper() for p in profiles], label_visibility="collapsed")
        entered_pin = pc2.text_input("PIN", type="password", max_chars=4, label_visibility="collapsed", placeholder="Enter PIN")
        
        if entered_pin:
            true_profile = selected_profile.lower()
            correct_pin = str(st.secrets["profiles"][true_profile]["pin"])
            
            if entered_pin == correct_pin:
                st.session_state.m_user = st.secrets["profiles"][true_profile]["user_id"]
                st.session_state.m_pwd = st.secrets["profiles"][true_profile]["password"]
                st.session_state.m_totp = st.secrets["profiles"][true_profile]["totp_secret"]
                st.session_state.m_api_key = st.secrets["profiles"][true_profile]["api_key"]
                pc3.success("✅ PIN Verified! Click 'MSTOCK LOGIN' below.")
            else:
                pc3.error("❌ Wrong PIN!")
    else:
        st.warning("⚠️ Secrets file missing or empty.")

# Session state se safe credentials nikalna engine ke liye
m_user = st.session_state.get("m_user", "")
m_pwd = st.session_state.get("m_pwd", "")
m_totp = st.session_state.get("m_totp", "")
m_api_key = st.session_state.get("m_api_key", "")

is_live_algo_on = st.session_state.get("master_switch_key", False)

st.markdown("### 🔴 2. LIVE MSTOCK EXECUTION ENGINE")

if is_live_algo_on:
    st.markdown('''
        <div style="background-color: rgba(231, 76, 60, 0.15); border: 2px solid #e74c3c; padding: 6px; border-radius: 6px; text-align: center; margin-bottom: 5px;">
            <span style="color: #e74c3c; font-weight: bold; font-size: 16px;">⚠️ DANGER: REAL MONEY ALGO IS ACTIVE ⚠️</span>
        </div>
    ''', unsafe_allow_html=True)
else:
    st.info("ℹ️ NOTE: Master Switch is OFF. Engine will Paper Trade (1 Qty/Lot). Turn ON to fire real orders with Capital settings.")

# --- YAHAN SE EXPANDER HATA DIYA AUR SAB EK LINE ME DAAL DIYA ---
ec1, ec2, ec3, ec4 = st.columns([1.5, 1, 1, 1])

with ec1:
    st.write("") # Checkbox ko align karne ke liye thoda space
    auto_on = st.checkbox("🔴 MASTER SWITCH (Real Money)", value=is_live_algo_on, key="master_switch_key")
with ec2:
    stk_cap = st.number_input("Capital ₹ (Stocks)", value=10000.0, step=1000.0)
with ec3:
    idx_lots = st.number_input("Index Lots", value=1, step=1)
with ec4:
    opt_lots = st.number_input("Stock Opt Lots", value=1, step=1)
    
st.markdown("---")

# =====================================================================
# 6. SCANNER CONNECTOR & TABLE VIEW
# =====================================================================
if 'signal_tracker' not in st.session_state:
    st.session_state.signal_tracker = {}
if 'score_text' not in st.session_state:
    st.session_state.score_text = "Targets: 0 | C2C: 0 | SL: 0 | Net P/L: ₹0.00"

st.write("") 
c1, c2, c3, c4 = st.columns(4)
ema_filter = c1.checkbox("📉 9-EMA Filter", value=True)
hide_wide = c2.checkbox("🚫 Hide Wide ORB", value=True)
triple_conf = c3.checkbox("📈 Nifty Trend Sync", value=True)
time_master = c4.checkbox("⏱️ Time Master (Strict)", value=True)

import pytz
ist = pytz.timezone('Asia/Kolkata')
curr_time = datetime.now(ist).time()
from datetime import time as dtime
current_zone_name = "Market Closed"
is_active_zone = False

if dtime(9, 15) <= curr_time < dtime(9, 45): current_zone_name, is_active_zone = "Zone 1 (09:15-09:45)", zone1
elif dtime(9, 45) <= curr_time < dtime(11, 30): current_zone_name, is_active_zone = "Zone 2 (09:45-11:30)", zone2
elif dtime(11, 30) <= curr_time < dtime(13, 30): current_zone_name, is_active_zone = "Zone 3 (11:30-13:30)", zone3
elif dtime(13, 30) <= curr_time < dtime(14, 45): current_zone_name, is_active_zone = "Zone 4 (13:30-14:45)", zone4
elif dtime(14, 45) <= curr_time <= dtime(15, 30): current_zone_name, is_active_zone = "Zone 5 (14:45-15:30)", zone5

if not time_master: 
    is_active_zone = True
    current_zone_name = "(All Zones Active)"

zone_status_color = "🟢 ACTIVE" if is_active_zone else "🔴 NO TRADE"

# --- ROW 1: Buttons & Time Zone ---
col_btn1, col_btn2, col_btn3, col_zone = st.columns([2.5, 2, 2, 3.5])

with col_btn1:
    if st.session_state.get('is_logged_in', False):
        st.markdown('''<div style="background-color: #2ecc71; color: white; padding: 7px 10px; border-radius: 8px; text-align: center; font-weight: bold; white-space: nowrap; font-size: 15px; margin-top: 2px;">✅ MSTOCK CONNECTED</div>''', unsafe_allow_html=True)
        login_btn = False 
    else:
        login_btn = st.button("🔐 MSTOCK LOGIN", width="stretch")

with col_btn2:
    scan_btn = st.button("🚀 SCAN MARKET", width="stretch", key="main_scan_btn")

with col_btn3:
    eod_btn = st.button("⏹️ SQUARE-OFF", width="stretch")
    
with col_zone:
    # Time zone ko ek alag neat information box me daal diya
    st.info(f"⏱️ **{current_zone_name}** [{zone_status_color}]")

# --- ROW 2: Full Width Dedicated Scoreboard ---
st.markdown(f'''
    <div style="background-color: #e8f4f8; border-left: 5px solid #3498db; padding: 10px 15px; border-radius: 5px; margin-top: 5px; margin-bottom: 15px;">
        <span style="color: #2c3e50; font-size: 18px; font-weight: bold;">🏆 SCOREBOARD: </span>
        <span style="color: #34495e; font-size: 18px;">{st.session_state.score_text}</span>
    </div>
''', unsafe_allow_html=True)

if login_btn:
    if m_api_key and m_user:
        with st.spinner("Logging into mStock API..."):
            try:
                init_mstock(m_user, m_pwd, m_totp, m_api_key)
                st.session_state.is_logged_in = True  
                st.success("✅ Login Successful!")
                time.sleep(1)
                st.rerun() 
            except Exception as e:
                st.error(f"❌ Login Failed: {e}")

if eod_btn:
    live_trades_exist = False
    with st.spinner("Firing Bulk Square-Off Orders on mStock..."):
        for k, t_data in st.session_state.signal_tracker.items():
            if "LIVE" in t_data["status"]:
                live_trades_exist = True
                is_real_money = t_data.get("is_mstock", False)
                qty = t_data.get("trade_qty", 1)
                action = t_data.get("action", "BUY")
                reverse_action = "SELL" if action == "BUY" else "BUY"
                
                is_opt = t_data.get("opt_sym") is not None
                last_res = t_data.get("last_res", {})
                current_price = float(last_res.get("opt_p", 0.0)) if is_opt else float(last_res.get("p", 0.0))

                # --- 🔴 ACTUAL BROKER ORDER LOGIC ---
                if is_real_money and m_api_key:
                    session = MSTOCK_CACHE.get(f"session_{m_api_key}")
                    acc_tok = MSTOCK_CACHE.get(f"access_token_{m_api_key}")
                    if session and acc_tok:
                        exc = "NFO" if is_opt else "NSE"
                        trade_sym = t_data.get("opt_sym") if is_opt else f"{k}-EQ"
                        try:
                            place_mstock_order(session, m_api_key, acc_tok, exc, trade_sym, qty, reverse_action, current_price)
                        except: pass
                
                # --- ✅ MEMORY AUR DISPLAY TABLE UPDATE LOGIC ---
                st.session_state.signal_tracker[k]["status"] = "⏹️ SQUARED OFF"
                st.session_state.signal_tracker[k]["locked"] = True
                # Table UI ko refresh karne ke liye last_res bhi update karna zaroori hai
                if "last_res" in st.session_state.signal_tracker[k]:
                    st.session_state.signal_tracker[k]["last_res"]["status"] = "⏹️ SQUARED OFF"

    if live_trades_exist:
        st.success("✅ All Live positions Squared-Off & synced with broker!")
        time.sleep(1.5)
        st.rerun()
    else:
        st.warning("⚠️ No LIVE positions to square off.")

st.write("") 
table_filter = st.radio("Filter Trades:", ["All", "🟢 Active", "🔴 Closed", "🏦 mStock Orders"], horizontal=True, label_visibility="collapsed")
st.markdown("---")

# =====================================================================
# ⏰ 3:00 PM AUTO SQUARE-OFF (SAVE RMS PENALTY)
# =====================================================================
if st.session_state.signal_tracker:
    curr_time_eod = datetime.now(ist).time()
    from datetime import time as dtime
    
    # Agar 3:00 PM ya uske baad ka time hai aur aaj ka auto-exit nahi hua hai
    if curr_time_eod >= dtime(15, 0) and not st.session_state.get("auto_eod_done", False):
        live_trades = {k: v for k, v in st.session_state.signal_tracker.items() if "LIVE" in v["status"]}
        
        if live_trades:
            st.warning("⏰ 3:00 PM Auto Square-Off Triggered! Closing all open positions to avoid RMS penalty...")
            
            for sym, t_data in live_trades.items():
                is_real_money = t_data.get("is_mstock", False)
                qty = t_data.get("trade_qty", 1)
                action = t_data.get("action", "BUY")
                reverse_action = "SELL" if action == "BUY" else "BUY"
                
                is_opt = t_data.get("opt_sym") is not None
                last_res = t_data.get("last_res", {})
                current_price = float(last_res.get("opt_p", 0.0)) if is_opt else float(last_res.get("p", 0.0))
                
                # Agar real trade tha, toh mStock par Exit Order fire karo
                if is_real_money and m_api_key:
                    session = MSTOCK_CACHE.get(f"session_{m_api_key}")
                    acc_tok = MSTOCK_CACHE.get(f"access_token_{m_api_key}")
                    
                    if session and acc_tok:
                        exc = "NFO" if is_opt else "NSE"
                        trade_sym = t_data.get("opt_sym") if is_opt else f"{sym}-EQ"
                        try:
                            place_mstock_order(session, m_api_key, acc_tok, exc, trade_sym, qty, reverse_action, current_price)
                        except: pass
                
                # System me status update karo
                st.session_state.signal_tracker[sym]["status"] = "⏰ 3PM AUTO EXIT"
                st.session_state.signal_tracker[sym]["locked"] = True
                if "last_res" in st.session_state.signal_tracker[sym]:
                    st.session_state.signal_tracker[sym]["last_res"]["status"] = "⏰ 3PM AUTO EXIT"
                
        # Lock kar do taaki din me baar-baar run na ho
        st.session_state.auto_eod_done = True
        st.success("✅ All positions safely squared off at 3:00 PM!")
        time.sleep(2)
        st.rerun()

# ==================== YAHAN SE REPLACE KAREIN ====================
status_placeholder = st.empty() # NAYA: Screen saaf karne wala Wiper 1

# 🌟 NAYA LOGIC: Check karo ki kya koi trade LIVE chal raha hai?
live_trades_active = any("LIVE" in v["status"] for v in st.session_state.signal_tracker.values())

# Scan tab chalega jab: Button dabaya ho YA (Auto-refresh ON ho aur (Active Zone ho YA Live trade bacha ho))
should_run_scan = scan_btn or (auto_refresh and (is_active_zone or live_trades_active))

if auto_refresh and not is_active_zone and not scan_btn and not live_trades_active:
    status_placeholder.warning(f"⏸️ **Auto-Scan Paused:** System is in {current_zone_name} (No Trade). Waiting for next active zone...")
elif auto_refresh and not is_active_zone and live_trades_active:
    status_placeholder.info(f"👀 **Tracking Active Trades:** {current_zone_name} (No New Trades). Updating live P/L & Trailing SL...")
else:
    status_placeholder.empty() # Purana message turant mita do!

if should_run_scan:
    base_list = list_50 if watchlist_choice == "Nifty 50" else (list_100 if watchlist_choice == "Nifty 100" else list_fno)
    
    # --- 🔥 NAYA LOGIC: JO SCREEN PAR HAI WAHI SCAN HOGA ---
    scan_list = []
    
    if is_active_zone:
        # Active Zone me normal scan karo (Jo dabbe tick hain)
        if auto_eq or auto_opt: 
            scan_list.extend(base_list)
        if auto_n: 
            scan_list.append("^NSEI")
        if auto_bn: 
            scan_list.append("^NSEBANK")
    else:
        # No-Trade Zone me SIRF LIVE trades ko scan karo (Super Fast Mode)
        live_syms = [k for k, v in st.session_state.signal_tracker.items() if "LIVE" in v["status"]]
        scan_list.extend(live_syms)
        
    # Duplicate hatane ke liye
    scan_list = list(set(scan_list))
        
    if len(scan_list) == 0:
        if not is_active_zone and not live_trades_active:
            st.info(f"⏳ System is currently in {current_zone_name} (No Trade Zone). Waiting for the active zone to begin scanning...")
            st.stop()
        else:
            st.warning("⚠️ Please select at least one segment (NIFTY, BANKNIFTY, STOCKS, or STOCK-OPT) to scan!")
            st.stop()
    # -------------------------------------------------------
    
    params = {
        "nifty_list": scan_list, "vol_sense": vol_min, "target": target_pct, "sl": sl_pct, "risk_mode": risk_mode,
        "ema_on": ema_filter, "hide_wide": hide_wide, "triple_on": triple_conf, "tracker": st.session_state.signal_tracker,
        "min_price": min_price, "max_price": max_price, "rsi_bull_min": rsi_bull[0], "rsi_bull_max": rsi_bull[1],
        "rsi_bear_min": rsi_bear[0], "rsi_bear_max": rsi_bear[1], "orb_wide": orb_wide_limit, "vwap_dist": vwap_dist_limit,
        "wick_limit": wick_limit, "max_vol": vol_max, "orb_dist": orb_dist_limit, "time_filter": time_master,
        "z1": zone1, "z2": zone2, "z3": zone3, "z4": zone4, "z5": zone5,
        "tsl_on": tsl_on, "tsl_trigger": tsl_trigger, "tsl_step": tsl_step,
        "auto_on": auto_on, "auto_opt": auto_opt, 
        "idx_lots": idx_lots, "opt_lots": opt_lots, "capital": stk_cap, "gift_diff": gift_diff,
        "m_user": m_user, "m_pwd": m_pwd, "m_totp": m_totp, "m_api_key": m_api_key, "ui_queue": None, "master_data": {}
    }
    
    params["log_func"] = st.write if not auto_refresh else lambda msg: None
    
    try:
        if not auto_refresh: st_context = st.status("🔥 Executing Quant Engine...", expanded=True)
        else: st_context = st.spinner("⏳")
            
        with st_context:
            results, updated_tracker = run_stock_scanner(params)
            st.session_state.signal_tracker = updated_tracker
            
            if results:
                t_hits = sum(1 for r in results if "TARGET" in r.get("status", ""))
                s_hits = sum(1 for r in results if "SL" in r.get("status", ""))
                c_hits = sum(1 for r in results if "C2C" in r.get("status", ""))
                auto_exits = sum(1 for r in results if "3PM" in r.get("status", "") or "MANUAL" in r.get("status", ""))
                
                # NAYA LOGIC: Total Rupees aur Total Investment sum karna
                net_pl = sum(float(r.get("pl_rs_disp", 0.0)) for r in results)
                total_inv = sum(float(r.get("fund_req_disp", 0.0)) for r in results)
                roi_pct = (net_pl / total_inv * 100) if total_inv > 0 else 0.0
                
                st.session_state.score_text = f"Targets: {t_hits} | SL: {s_hits} | C2C: {c_hits + auto_exits} | Inv: ₹{total_inv:,.0f} | Net P/L: ₹{net_pl:,.2f} ({roi_pct:+.2f}%)"
                
        scan_msg = st.empty() # NAYA: Screen saaf karne wala Wiper 2
        if not auto_refresh:
            if results: st.rerun()
            else: scan_msg.warning("⚠️ Koi naya Setup/Breakout nahi mila.")
        else:
            scan_msg.empty() # Purana message turant mita do
                
    except Exception as e:
        st.error(f"Engine Crash: {e}")

# =====================================================================
# 7. DISPLAY RESULTS (TABLE VIEW)
# =====================================================================
# 🛑 PREPARE DATA FOR TABLE (Fix: Scanner ke baad fresh data read hoga)
if st.session_state.signal_tracker:
    results = [v["last_res"] for k, v in st.session_state.signal_tracker.items() if "last_res" in v]
    if results:
        df_res = pd.DataFrame(results)
        
        if table_filter == "🟢 Active": 
            df_res = df_res[~df_res['status'].str.contains("TARGET|SL|C2C|SQUARED|MANUAL", regex=True, na=False)]
        elif table_filter == "🔴 Closed": 
            df_res = df_res[df_res['status'].str.contains("TARGET|SL|C2C|SQUARED|MANUAL", regex=True, na=False)]
        elif table_filter == "🏦 mStock Orders":
            if 'is_mstock' in df_res.columns:
                df_res = df_res[df_res['is_mstock'] == True]
            else:
                df_res = df_res.iloc[0:0]

if st.session_state.signal_tracker:
    if 'df_res' in locals() and not df_res.empty:
        # NAYE COLUMNS LIST ME ADD KIYE
        cols_to_show = ["time", "s", "sig", "ent", "p", "opt_ent", "opt_p", "tgt_sl", "spot_pl_pts", "opt_pl_pts", "pl_rs_disp", "trade_qty", "fund_req_disp", "strike_lot", "status"]
        show_cols = [c for c in cols_to_show if c in df_res.columns]
        
        # NAYE COLUMNS KE NAAM SET KIYE
        rename_map = {
            "time": "Time", "s": "Symbol", "sig": "Signal", 
            "ent": "Spot Ent", "p": "Spot LTP", 
            "opt_ent": "Opt Ent", "opt_p": "Opt LTP", 
            "tgt_sl": "Tgt|SL", 
            "spot_pl_pts": "Spot P/L", "opt_pl_pts": "Opt P/L", 
            "pl_rs_disp": "Net P/L", "trade_qty": "Qty", 
            "fund_req_disp": "Fund (₹)", "strike_lot": "Strike/Type", "status": "Status"
        }
        df_view = df_res[show_cols].rename(columns=rename_map)
        
        def color_status(val):
            c = 'white'
            if 'TARGET' in str(val): c = '#2ecc71'
            elif 'SL' in str(val): c = '#e74c3c'
            elif 'C2C' in str(val): c = '#f1c40f'
            elif 'LIVE' in str(val): c = '#3498db'
            return f'color: {c}; font-weight: bold;'
            
        def color_pl(val):
            try:
                v = float(val)
                if v > 0: return 'background-color: rgba(46, 204, 113, 0.2); color: #2ecc71; font-weight: bold;'
                elif v < 0: return 'background-color: rgba(231, 76, 60, 0.2); color: #e74c3c; font-weight: bold;'
            except: pass
            return ''
            
        def color_tgt_sl(val):
            if '🔄' in str(val) or '🔒' in str(val):
                return 'background-color: rgba(241, 196, 15, 0.25); color: #f39c12; font-weight: bold;'
            return ''
            
        # Naye columns par color logic lagaya (Crash-Proof)
        pl_cols = [c for c in ['Spot P/L', 'Opt P/L', 'Net P/L'] if c in df_view.columns]
        styled_df = df_view.style.map(color_status, subset=['Status'] if 'Status' in df_view.columns else [])
        if pl_cols: styled_df = styled_df.map(color_pl, subset=pl_cols)
        if 'Tgt|SL' in df_view.columns: styled_df = styled_df.map(color_tgt_sl, subset=['Tgt|SL'])
            
        st.dataframe(
            styled_df, 
            width="stretch", 
            hide_index=True,
            column_config={
                # SARE NUMBERS KO CLEAN AUR RIGHT-ALIGN KIYA
                "Spot Ent": st.column_config.NumberColumn("Spot Ent", format="%.2f"),
                "Spot LTP": st.column_config.NumberColumn("Spot LTP", format="%.2f"),
                "Opt Ent": st.column_config.NumberColumn("Opt Ent", format="%.2f"),
                "Opt LTP": st.column_config.NumberColumn("Opt LTP", format="%.2f"),
                "Spot P/L": st.column_config.NumberColumn("Spot P/L", format="%.2f"),
                "Opt P/L": st.column_config.NumberColumn("Opt P/L", format="%.2f"),
                "Net P/L": st.column_config.NumberColumn("Net P/L", format="%.2f"),
                "Fund (₹)": st.column_config.NumberColumn("Fund (₹)", format="%.2f")
            }
        )
        
        # =====================================================================
        # 📥 NAYA FEATURE: ONE-CLICK EXCEL DOWNLOAD (WITH DEEP SUMMARY)
        # =====================================================================
        st.write("") # Thoda space
        col_dl1, col_dl2 = st.columns([1, 4])
        with col_dl1:
            # 1. Main Table Data
            base_csv = df_view.to_csv(index=False)
            
            # 2. Advanced Summary Calculation (Cash vs Options)
            cash_df = df_view[df_view['Strike/Type'] == 'CASH (Eq)']
            opt_df = df_view[df_view['Strike/Type'] != 'CASH (Eq)']
            
            cash_inv = pd.to_numeric(cash_df['Fund (₹)'], errors='coerce').sum()
            opt_inv = pd.to_numeric(opt_df['Fund (₹)'], errors='coerce').sum()
            total_inv = cash_inv + opt_inv
            
            cash_pl = pd.to_numeric(cash_df['Net P/L'], errors='coerce').sum()
            opt_pl = pd.to_numeric(opt_df['Net P/L'], errors='coerce').sum()
            total_pl = cash_pl + opt_pl
            
            cash_roi = (cash_pl / cash_inv * 100) if cash_inv > 0 else 0.0
            opt_roi = (opt_pl / opt_inv * 100) if opt_inv > 0 else 0.0
            total_roi = (total_pl / total_inv * 100) if total_inv > 0 else 0.0
            
            # 3. Format CSV Summary (Khali cells diye hain taaki design theek aaye)
            summary_csv = f"""\n\n,,,--- ADVANCED DAILY SUMMARY ---
,,,Category,Total Investment (Rs),Net P/L (Rs),ROI (%)
,,,CASH (Equity),{cash_inv:.2f},{cash_pl:.2f},{cash_roi:.2f}%
,,,OPTIONS (F&O),{opt_inv:.2f},{opt_pl:.2f},{opt_roi:.2f}%
,,,TOTAL PORTFOLIO,{total_inv:.2f},{total_pl:.2f},{total_roi:.2f}%
"""
            
            final_csv_data = (base_csv + summary_csv).encode('utf-8')
            current_date = datetime.now(ist).strftime('%d_%b_%Y')
            
            # 4. Download Button
            st.download_button(
                label="📥 Download EOD Excel (With Summary)",
                data=final_csv_data,
                file_name=f"Intraday_Report_{current_date}.csv",
                mime="text/csv",
                type="primary",
                use_container_width=True
            )
            
    else: st.info(f"No {table_filter} trades right now.")

# =====================================================================
# 🛑 INDIVIDUAL MANUAL EXIT PANEL (MOVED BELOW TABLE)
# =====================================================================
if st.session_state.signal_tracker:
    # Sirf LIVE trades ko filter out karo Dropdown ke liye
    live_trades = {k: v for k, v in st.session_state.signal_tracker.items() if "LIVE" in v["status"]}
    live_symbols = list(live_trades.keys())
    
    if live_symbols:
        st.markdown("#### 🛑 Individual Manual Exit (Pro Control)")
        exit_msg = st.empty() 
        
        ex_c1, ex_c2, ex_c3 = st.columns([2.5, 2.5, 5]) 
        
        with ex_c1:
            selected_exit_sym = st.selectbox("Select Trade to Close:", live_symbols, label_visibility="collapsed", key="manual_exit_dropdown")
            
        with ex_c2:
            if st.button(f"🛑 Exit {selected_exit_sym}", width="stretch", key="exit_btn_main"):
                trade_data = st.session_state.signal_tracker[selected_exit_sym]
                is_real_money = trade_data.get("is_mstock", False)
                qty = trade_data.get("trade_qty", 1)
                action = trade_data.get("action", "BUY")
                
                # Agar buy tha toh sell karo, sell tha toh buy karo
                reverse_action = "SELL" if action == "BUY" else "BUY"
                
                last_res = trade_data.get("last_res", {})
                is_opt = trade_data.get("opt_sym") is not None
                current_price = float(last_res.get("opt_p", 0.0)) if is_opt else float(last_res.get("p", 0.0))
                
                if is_real_money and m_api_key:
                    session = MSTOCK_CACHE.get(f"session_{m_api_key}")
                    acc_tok = MSTOCK_CACHE.get(f"access_token_{m_api_key}")
                    
                    if session and acc_tok:
                        exc = "NFO" if is_opt else "NSE"
                        trade_sym = trade_data.get("opt_sym") if is_opt else f"{selected_exit_sym}-EQ"
                        
                        with st.spinner(f"Firing Exit Order for {selected_exit_sym} on mStock..."):
                            res = place_mstock_order(session, m_api_key, acc_tok, exc, trade_sym, qty, reverse_action, current_price)
                            
                            if isinstance(res, dict) and res.get("status") == "error":
                                exit_msg.error(f"❌ Broker Error: {res.get('message')}")
                                time.sleep(4)
                                st.rerun()
                            else:
                                st.session_state.signal_tracker[selected_exit_sym]["status"] = "🔴 MANUAL EXIT"
                                st.session_state.signal_tracker[selected_exit_sym]["locked"] = True
                                if "last_res" in st.session_state.signal_tracker[selected_exit_sym]:
                                    st.session_state.signal_tracker[selected_exit_sym]["last_res"]["status"] = "🔴 MANUAL EXIT"
                                exit_msg.success(f"✅ {selected_exit_sym} manually closed on mStock!")
                                time.sleep(1)
                                st.rerun()
                    else:
                        exit_msg.error("⚠️ mStock Session Expired! Please Login again.")
                        time.sleep(3)
                        st.rerun()
                else:
                    st.session_state.signal_tracker[selected_exit_sym]["status"] = "🔴 MANUAL EXIT"
                    st.session_state.signal_tracker[selected_exit_sym]["locked"] = True
                    if "last_res" in st.session_state.signal_tracker[selected_exit_sym]:
                        st.session_state.signal_tracker[selected_exit_sym]["last_res"]["status"] = "🔴 MANUAL EXIT"
                    exit_msg.success(f"✅ {selected_exit_sym} Paper Trade closed!")
                    time.sleep(1)
                    st.rerun()
        
        with ex_c3:
            if st.button("🗑️ Force Clear from App (Ignore Broker)", width="stretch", key="clear_btn_main"):
                st.session_state.signal_tracker[selected_exit_sym]["status"] = "🔴 MANUAL EXIT"
                st.session_state.signal_tracker[selected_exit_sym]["locked"] = True
                if "last_res" in st.session_state.signal_tracker[selected_exit_sym]:
                    st.session_state.signal_tracker[selected_exit_sym]["last_res"]["status"] = "🔴 MANUAL EXIT"
                exit_msg.warning(f"✅ {selected_exit_sym} cleared from app memory!")
                time.sleep(1)
                st.rerun()
                
        st.write("")

# =====================================================================
# 8. KUNDALI / LIVE CHART
# =====================================================================
if st.session_state.signal_tracker:
    if 'df_res' in locals() and not df_res.empty:
        st.markdown("---")
        st.subheader("📜 Deep Kundali & Live Chart Monitor")
        
        kc1, kc_tf, kc2 = st.columns([2, 1, 1.5])
        
        with kc1: 
            k_sym = st.selectbox("Select a Stock to View Log/Chart:", df_res['s'].tolist() if 's' in df_res else [])
            
        with kc_tf: 
            chart_tf = st.selectbox("Timeframe:", ["1m", "5m", "15m", "30m", "1h"], index=1)
            
        if k_sym:
            clean_sym = k_sym.replace("🔼 ", "").replace("🔽 ", "").strip()
            tracker_data = st.session_state.signal_tracker.get(clean_sym, {})
            k_data = tracker_data.get("kundali", "No log available.")
            st.code(k_data, language="text")
            
            with kc2:
                # Button ko theek alignment me laane ke liye spacing
                st.write("") 
                st.write("")
                show_chart_btn = st.button(f"📈 Show Live Chart for {clean_sym}", width="stretch")
                
            if show_chart_btn:
                with st.spinner(f"Loading Interactive Chart for {clean_sym}..."):
                    try:
                        yf_sym = clean_sym + ".NS"
                        if clean_sym == "NIFTY_IDX": yf_sym = "^NSEI"
                        elif clean_sym == "BANKNIFTY_IDX": yf_sym = "^NSEBANK"
                        elif "^" in clean_sym: yf_sym = clean_sym
                        
                        fetch_period = "5d" if chart_tf in ["15m", "30m", "1h"] else "2d"
                        df_chart = yf.download(yf_sym, period=fetch_period, interval=chart_tf, progress=False)
                        if isinstance(df_chart.columns, pd.MultiIndex): df_chart.columns = df_chart.columns.droplevel(1)
                        df_chart = df_chart.dropna()

                        if not df_chart.empty:
                            if df_chart.index.tz is None: df_chart.index = df_chart.index.tz_localize('UTC').tz_convert('Asia/Kolkata')
                            else: df_chart.index = df_chart.index.tz_convert('Asia/Kolkata')
                                
                            df_chart['9EMA'] = ta.ema(df_chart['Close'], length=9)
                            
                            if 'Volume' in df_chart.columns and df_chart['Volume'].sum() > 0: vol = df_chart['Volume']
                            else: vol = pd.Series(1, index=df_chart.index)
                                
                            tp = (df_chart['High'] + df_chart['Low'] + df_chart['Close']) / 3
                            df_chart['VWAP'] = (tp * vol).groupby(df_chart.index.date).cumsum() / vol.groupby(df_chart.index.date).cumsum()
                            
                            fig = make_subplots(rows=1, cols=1, shared_xaxes=True)
                            fig.add_trace(go.Candlestick(x=df_chart.index, open=df_chart['Open'], high=df_chart['High'], low=df_chart['Low'], close=df_chart['Close'], name="Price"))
                            fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['9EMA'], mode='lines', line=dict(color='#f39c12', width=1.5), name='9-EMA'))
                            fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['VWAP'], mode='lines', line=dict(color='#9b59b6', width=1.5), name='VWAP'))
                            
                            entry = float(tracker_data.get("entry", 0))
                            t_dist = float(tracker_data.get("t_dist", 0))
                            s_dist = float(tracker_data.get("s_dist", 0))
                            is_sell = tracker_data.get("action") == "SELL"
                            
                            tgt = (entry - t_dist) if is_sell else (entry + t_dist)
                            sl = (entry + s_dist) if is_sell else (entry - s_dist)
                            
                            if entry > 0: fig.add_hline(y=entry, line_dash="dash", line_color="white", annotation_text=f"Entry: {entry:.2f}")
                            if tgt > 0 and tgt != entry: fig.add_hline(y=tgt, line_dash="dash", line_color="#2ecc71", annotation_text=f"Target: {tgt:.2f}")
                            if sl > 0 and sl != entry: fig.add_hline(y=sl, line_dash="dash", line_color="#e74c3c", annotation_text=f"SL: {sl:.2f}")
                            
                            try:
                                today_date = df_chart.index[-1].date()
                                today_start_time = df_chart[df_chart.index.date == today_date].index[0]
                                fig.add_vline(x=today_start_time, line_dash="dot", line_color="#3498db", annotation_text="🌞 TODAY OPEN", annotation_position="top right")
                            except: pass

                            fig.update_layout(
                                title=f"Live Interactive Chart: {clean_sym}", 
                                yaxis_title="Price (₹)", 
                                template="plotly_dark", 
                                height=600, 
                                xaxis_rangeslider_visible=False, 
                                margin=dict(l=20, r=20, t=50, b=20)
                            )
                            
                            fig.update_xaxes(
                                rangebreaks=[
                                    dict(bounds=[15.5, 9.25], pattern="hour"), # 15.5 = 3:30 PM aur 9.25 = 9:15 AM
                                    dict(bounds=["sat", "mon"]) # Saturday-Sunday hide karo
                                ]
                            )
                            
                            st.plotly_chart(fig, width="stretch")
                            
                            if st.button("❌ Close Chart", width="stretch"):
                                st.rerun()
                                
                        else: st.warning("Live chart data not available right now.")
                    except Exception as e: st.error(f"Error drawing chart: {e}")

# =====================================================================
# 9. AUTO-REFRESH TRIGGER LOOP
# =====================================================================
if auto_refresh:
    time.sleep(refresh_time) 
    st.rerun()