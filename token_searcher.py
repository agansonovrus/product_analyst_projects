import asyncio
import websockets
import json
import msgpack
import time
import re
import webbrowser
import os
import requests
import tkinter as tk
from tkinter import simpledialog, messagebox
from threading import Thread
from playwright.async_api import async_playwright
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

# --- НАСТРОЙКИ ---
REFRESH_TOKEN = ""
URL = "wss://pulse.axiom.trade/ws"
BLACKLIST_FILE = "blacklist.txt"
COMMUNITY_BLACKLIST_FILE = "community_blacklist.txt"
COMMUNITY_CREATORS_FILE = "community_creators.json"
WALLET_NOTES_FILE = "wallet_notes.json"
TWITTER_NOTES_FILE = "twitter_notes.json"

# --- TOKEN MANAGER ---
class TokenManager:
    def __init__(self, refresh_token):
        self.refresh_token = refresh_token
        self.auth_token = None
        self.token_expiry = None
        self.refresh_url = "https://api8.axiom.trade/refresh-access-token"
    
    def get_valid_token(self):
        if not self.auth_token or datetime.now() >= self.token_expiry - timedelta(minutes=5):
            self._refresh_auth_token()
        return self.auth_token
    
    def _refresh_auth_token(self):
        try:
            response = requests.post(
                self.refresh_url,
                cookies={"auth-refresh-token": self.refresh_token},
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    self.auth_token = data.get("access_token") or data.get("accessToken")
                    expires_in = data.get("expires_in") or data.get("expiresIn") or 900
                except:
                    cookies = response.cookies
                    self.auth_token = cookies.get("auth-access-token")
                    expires_in = 900
                
                if self.auth_token:
                    self.token_expiry = datetime.now() + timedelta(seconds=expires_in)
                    print(f"✅ Токен обновлён. Истекает в {self.token_expiry.strftime('%H:%M:%S')}")
                else:
                    raise Exception("Токен не найден в ответе")
            else:
                raise Exception(f"Ошибка {response.status_code}: {response.text}")
        except Exception as e:
            print(f"Ошибка обновления токена: {e}")
            if not self.auth_token:
                raise Exception("Не удалось получить токен авторизации!")

# --- COMMUNITY CREATORS MANAGER (только mint-адреса) ---
class CommunityCreatorsManager:
    def __init__(self, file_path):
        self.file_path = file_path
        self.creators = self.load_creators()
    
    def load_creators(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save_creators(self):
        with open(self.file_path, 'w') as f:
            json.dump(self.creators, f, indent=2)
    
    def add_token(self, wallet_address, mint_address):
        """Добавляет только mint-адрес токена (без ATH)"""
        if wallet_address not in self.creators:
            self.creators[wallet_address] = []
        
        # Проверяем, не добавлен ли уже этот токен
        if mint_address not in self.creators[wallet_address]:
            self.creators[wallet_address].insert(0, mint_address)
            self.creators[wallet_address] = self.creators[wallet_address][:3]
            self.save_creators()
    
    def get_tokens(self, wallet_address):
        """Возвращает список mint-адресов последних 3 токенов"""
        return self.creators.get(wallet_address, [])
    
    def delete_creator(self, wallet_address):
        if wallet_address in self.creators:
            del self.creators[wallet_address]
            self.save_creators()

# --- UNIVERSAL NOTES MANAGER ---
class NotesManager:
    def __init__(self, file_path):
        self.file_path = file_path
        self.notes = self.load_notes()
    
    def load_notes(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save_notes(self):
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(self.notes, f, indent=2, ensure_ascii=False)
    
    def add_note(self, identifier, note_text):
        self.notes[identifier] = {
            "note": note_text,
            "timestamp": datetime.now().isoformat()
        }
        self.save_notes()
    
    def get_note(self, identifier):
        data = self.notes.get(identifier)
        return data['note'] if data else None
    
    def delete_note(self, identifier):
        if identifier in self.notes:
            del self.notes[identifier]
            self.save_notes()

# --- ГЛОБАЛЬНЫЕ МЕНЕДЖЕРЫ ---
token_manager = TokenManager(REFRESH_TOKEN)
community_creators = CommunityCreatorsManager(COMMUNITY_CREATORS_FILE)
wallet_notes = NotesManager(WALLET_NOTES_FILE)
twitter_notes = NotesManager(TWITTER_NOTES_FILE)

seen_mints = set()
executor = ThreadPoolExecutor(max_workers=10)

def parse_ath_value(ath_str):
    try:
        match = re.search(r'\$(\d+\.?\d*)([MK]?)', ath_str.replace(',', ''))
        if not match: return 0
        val = float(match.group(1))
        suffix = match.group(2).upper()
        if suffix == 'M': return val * 1_000_000
        if suffix == 'K': return val * 1_000
        return val
    except: return 0

def format_ath_value(ath_val):
    """Форматирует числовое значение ATH в читаемую строку"""
    if ath_val >= 1_000_000:
        return f"${ath_val/1_000_000:.1f}M"
    elif ath_val >= 1_000:
        return f"${ath_val/1_000:.0f}K"
    else:
        return f"${ath_val:.0f}"

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Axiom Pulse [Smart Filters + Auto Token Refresh + Creator History + Dual Notes]")
        self.geometry("1100x850") 
        self.configure(bg="#1a1a1a")
        self.blacklist, self.comm_blacklist = set(), set()
        self.load_blacklists()
        
        self.community_monitoring_enabled = True

        header_frame = tk.Frame(self, bg="#333")
        header_frame.pack(fill="x")
        
        self.header = tk.Label(header_frame, text="MONITORING ACTIVE", bg="#333", fg="#00ff00", font=("Arial", 10, "bold"), pady=10)
        self.header.pack(side="left", padx=20)
        
        self.toggle_comm_btn = tk.Button(
            header_frame, 
            text="Community: ON", 
            bg="#00aa00", 
            fg="white", 
            font=("Arial", 9, "bold"),
            width=15,
            command=self.toggle_community_monitoring
        )
        self.toggle_comm_btn.pack(side="right", padx=20, pady=5)
        
        self.container = tk.Frame(self, bg="#1a1a1a")
        self.container.pack(fill="both", expand=True)
        
        self.canvas = tk.Canvas(self.container, bg="#1a1a1a", highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self.container, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg="#1a1a1a")
        
        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width))
        self.scrollable_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

    def load_blacklists(self):
        for f_name in [BLACKLIST_FILE, COMMUNITY_BLACKLIST_FILE]:
            if not os.path.exists(f_name): open(f_name, "a").close()
        with open(BLACKLIST_FILE, "r") as f: self.blacklist = {l.strip() for l in f if l.strip()}
        with open(COMMUNITY_BLACKLIST_FILE, "r") as f: self.comm_blacklist = {l.strip().lower() for l in f if l.strip()}
    
    def toggle_community_monitoring(self):
        self.community_monitoring_enabled = not self.community_monitoring_enabled
        
        if self.community_monitoring_enabled:
            self.toggle_comm_btn.config(text="Community: ON", bg="#00aa00")
            print("✅ Community monitoring: ENABLED")
        else:
            self.toggle_comm_btn.config(text="Community: OFF", bg="#aa0000")
            print("❌ Community monitoring: DISABLED")

    def add_token_card(self, name, dev, ath_info, axiom_url, x_info, history_info=None, is_new_dev=False, note_identifier=None, note_type="wallet"):
        bg_color = "#1e2a3a" if is_new_dev else "#2d2d2d"
        border_color = "#3d5afe" if is_new_dev else "#444"

        card = tk.Frame(self.scrollable_frame, bg=bg_color, highlightbackground=border_color, highlightthickness=2)
        card.pack(fill="x", padx=10, pady=5)

        info_frame = tk.Frame(card, bg=bg_color)
        info_frame.pack(side="left", fill="x", expand=True, padx=15, pady=10)

        tk.Label(info_frame, text=name, bg=bg_color, fg="#00ff00", font=("Arial", 12, "bold")).pack(anchor="w")
        tk.Label(info_frame, text=ath_info, bg=bg_color, fg="#ffffff", font=("Consolas", 10)).pack(anchor="w")
        
        if history_info:
            tk.Label(info_frame, text=history_info, bg=bg_color, fg="#ffaa00", font=("Consolas", 9, "italic")).pack(anchor="w")
        
        notes_mgr = twitter_notes if note_type == "twitter" else wallet_notes
        existing_note = notes_mgr.get_note(note_identifier) if note_identifier else None
        
        if existing_note:
            note_prefix = "🐦" if note_type == "twitter" else "💼"
            note_label = tk.Label(info_frame, text=f"{note_prefix} Note: {existing_note}", bg=bg_color, fg="#00ffaa", font=("Consolas", 9, "bold"))
            note_label.pack(anchor="w")
        
        tw_label = tk.Label(info_frame, text=x_info['text'], bg=bg_color, fg="#1da1f2", font=("Consolas", 10), cursor="hand2")
        tw_label.pack(anchor="w")
        if x_info['url']: tw_label.bind("<Button-1>", lambda e: webbrowser.open(x_info['url']))

        btn_frame = tk.Frame(card, bg=bg_color)
        btn_frame.pack(side="right", padx=15)
        
        tk.Button(btn_frame, text="OPEN", bg="#0066cc", fg="white", width=10, font=("Arial", 8, "bold"),
                  command=lambda: webbrowser.open(axiom_url)).pack(side="left", padx=5)
        
        if note_identifier:
            tk.Button(btn_frame, text="NOTE", bg="#00aa44", fg="white", width=8, font=("Arial", 8, "bold"),
                      command=lambda: self.add_note_dialog(note_identifier, note_type)).pack(side="left", padx=5)
        
        tk.Button(btn_frame, text="SKIP", bg="#555", fg="white", width=8, font=("Arial", 8),
                  command=lambda: card.destroy()).pack(side="left", padx=5)
        
        tk.Button(btn_frame, text="BAN", bg="#b30000", fg="white", width=10, font=("Arial", 8, "bold"),
                  command=lambda: self.universal_ban(dev, x_info.get('handle'), note_identifier, note_type, card)).pack(side="left", padx=5)

    def add_note_dialog(self, identifier, note_type):
        notes_mgr = twitter_notes if note_type == "twitter" else wallet_notes
        existing_note = notes_mgr.get_note(identifier)
        
        note_label = f"Twitter @{identifier}" if note_type == "twitter" else f"Wallet {identifier[:8]}...{identifier[-6:]}"
        
        note_text = simpledialog.askstring(
            "Add Note",
            f"Note for {note_label}\n\nEnter your note:",
            initialvalue=existing_note or ""
        )
        
        if note_text is not None:
            if note_text.strip():
                notes_mgr.add_note(identifier, note_text.strip())
                messagebox.showinfo("Success", f"Note saved ({note_type})!")
            else:
                notes_mgr.delete_note(identifier)
                messagebox.showinfo("Success", "Note deleted!")

    def universal_ban(self, dev_addr, x_handle, note_identifier, note_type, card):
        with open(BLACKLIST_FILE, "a") as f: f.write(f"{dev_addr}\n")
        self.blacklist.add(dev_addr)
        
        if x_handle:
            with open(COMMUNITY_BLACKLIST_FILE, "a") as f: f.write(f"{x_handle}\n")
            self.comm_blacklist.add(x_handle.lower())
        
        community_creators.delete_creator(dev_addr)
        wallet_notes.delete_note(dev_addr)
        if note_identifier:
            notes_mgr = twitter_notes if note_type == "twitter" else wallet_notes
            notes_mgr.delete_note(note_identifier)
        
        card.destroy()
        print(f"🚫 Banned & cleaned: wallet={dev_addr}, identifier={note_identifier}")

# --- NETWORK ---

def get_axiom_api(url, params):
    current_token = token_manager.get_valid_token()
    cookie_string = f"auth-refresh-token={REFRESH_TOKEN}; auth-access-token={current_token}"
    
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Cookie": cookie_string}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        return r.json() if r.status_code == 200 else None
    except: return None

async def handle_community_check(pair_addr, dev_wallet, app):
    p_data = await asyncio.get_event_loop().run_in_executor(executor, get_axiom_api, "https://api8.axiom.trade/pair-info", {"pairAddress": pair_addr, "v": "2"})
    if not p_data: return None
    
    target = p_data.get('data', p_data)
    tw_url = target.get('twitter')
    
    if tw_url and "communities/" in tw_url:
        match = re.search(r'communities/(\d+)', tw_url)
        if match:
            c_data = await asyncio.get_event_loop().run_in_executor(executor, get_axiom_api, "https://api10.axiom.trade/twitter-community-info", {"communityId": match.group(1), "v": "2"})
            
            if c_data:
                c_target = c_data.get('data', c_data)
                
                created_at_str = c_target.get('createdAt')
                if created_at_str:
                    try:
                        fmt = "%a %b %d %H:%M:%S %z %Y"
                        created_time = datetime.strptime(created_at_str, fmt)
                        
                        now = datetime.now(timezone.utc)
                        diff = now - created_time
                        
                        if diff.total_seconds() > 3600:
                            return None
                    except Exception as e:
                        print(f"Ошибка парсинга даты: {e}")

                creator = c_target.get('creator', {})
                handle = creator.get('screenName', 'Unknown')
                subs = creator.get('followersCount', 0)
                
                if handle.lower() in app.comm_blacklist: return "BANNED"
                
                return {
                    'text': f"X Comm: @{handle} | Subs: {subs:,}", 
                    'url': tw_url, 
                    'handle': handle,
                    'wallet': dev_wallet
                }
    return None

async def get_gmgn_ath(page, dev_address):
    try:
        await page.goto(f"https://gmgn.ai/sol/address/{dev_address}", wait_until="commit", timeout=10000)
        sel = "div:has-text('ATH MC:')"
        await page.wait_for_selector(sel, timeout=7000)
        raw = await page.locator(sel).last.inner_text()
        m = re.search(r'ATH MC:\s*([A-Za-z0-9]+\(\$.*?\))', " ".join(raw.split()))
        return m.group(0) if m else None
    except: return None

async def get_token_ath_by_mint(page, mint_address):
    """Получает ATH конкретного токена по его mint-адресу через GMGN"""
    try:
        await page.goto(f"https://gmgn.ai/sol/token/{mint_address}", wait_until="commit", timeout=10000)
        sel = "div:has-text('ATH')"
        await page.wait_for_selector(sel, timeout=7000)
        
        # Ищем конкретно значение ATH
        ath_elements = await page.locator(sel).all()
        for elem in ath_elements:
            text = await elem.inner_text()
            # Ищем паттерн типа "ATH: $123.45K" или "ATH MC: $1.2M"
            m = re.search(r'ATH[:\s]*\$?([\d.]+)([MKmk]?)', text)
            if m:
                val = float(m.group(1))
                suffix = m.group(2).upper() if m.group(2) else ''
                if suffix == 'M':
                    return val * 1_000_000
                elif suffix == 'K':
                    return val * 1_000
                return val
        return 0
    except:
        return 0

async def get_creator_history_ath(page, wallet_address):
    """Получает ATH предыдущих токенов создателя коммьюнити"""
    prev_tokens = community_creators.get_tokens(wallet_address)
    if not prev_tokens:
        return None
    
    ath_values = []
    for mint in prev_tokens:
        ath_val = await get_token_ath_by_mint(page, mint)
        if ath_val > 0:
            ath_values.append(ath_val)
    
    if not ath_values:
        return None
    
    # Форматируем список ATH
    formatted = [format_ath_value(val) for val in ath_values]
    return f"Previous ATH: {', '.join(formatted)}"

async def run_monitor(page, app):
    current_token = token_manager.get_valid_token()
    headers = {"Authorization": f"Bearer {current_token}"}
    
    async with websockets.connect(URL, extra_headers=headers) as ws:
        print("🟢 CONNECTED")
        await ws.send(json.dumps({"type": "userState", "state": {"tables": {"newPairs": True}}}))
        while True:
            raw = await ws.recv()
            if raw == "2": await ws.send("3"); continue
            try:
                msg = msgpack.unpackb(raw, raw=False) if isinstance(raw, bytes) else json.loads(raw[2:])
                if isinstance(msg, list) and msg[1][0] == "newPairs":
                    f = msg[1][1]
                    p_addr = f[0] if (isinstance(f[0], str) and len(f[0]) > 5) else f[38]
                    mint, dev, name = f[1], f[2], f[3]
                    migrated_c, total_c = (f[33] or 0), (f[41] or 1)
                    
                    if mint in seen_mints or dev in app.blacklist: continue

                    if total_c == 1:
                        if not app.community_monitoring_enabled:
                            continue
                        
                        x_info = await handle_community_check(p_addr, dev, app)
                        if x_info and x_info != "BANNED":
                            seen_mints.add(mint)
                            url = f"https://axiom.trade/meme/{p_addr}?chain=sol"
                            
                            # Получаем историю ATH предыдущих токенов через GMGN
                            history_text = await get_creator_history_ath(page, dev)
                            
                            twitter_handle = x_info.get('handle')
                            
                            app.add_token_card(
                                name, dev, 
                                "NEW DEV (1st Token) - Community Found", 
                                url, x_info, 
                                history_info=history_text,
                                is_new_dev=True,
                                note_identifier=twitter_handle,
                                note_type="twitter"
                            )
                            
                            # Сохраняем только mint-адрес (без ATH)
                            community_creators.add_token(dev, mint)
                            
                            webbrowser.open(url)
                        
                    elif migrated_c > 1 and (migrated_c / total_c >= 0.04):
                        ath = await get_gmgn_ath(page, dev)
                        if ath and parse_ath_value(ath) > 100000:
                            seen_mints.add(mint)
                            url = f"https://axiom.trade/meme/{p_addr}?chain=sol"
                            
                            app.add_token_card(
                                name, dev, 
                                f"EXP DEV | {ath}", 
                                url, 
                                {'text': 'X: Skip check for Exp Dev', 'url': None},
                                history_info=None,
                                is_new_dev=False,
                                note_identifier=dev,
                                note_type="wallet"
                            )
                            
                            webbrowser.open(url)
            except: continue

def start_async_thread(app):
    async def start_async():
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context('user_data', headless=False, args=["--mute-audio"])
            page = await context.new_page()
            await page.route("**/*.{png,jpg,jpeg,svg,css,woff2}", lambda r: r.abort())
            while True:
                try: await run_monitor(page, app)
                except: await asyncio.sleep(5)
    asyncio.run(start_async())

if __name__ == "__main__":
    app = App()
    Thread(target=start_async_thread, args=(app,), daemon=True).start()
    app.mainloop()
