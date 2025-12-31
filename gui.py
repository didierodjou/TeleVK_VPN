# --- START OF FILE gui.py ---
import sys
import asyncio
import logging
import time
import threading
from collections import deque

import requests
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QTextEdit, QLabel,
                             QFrame, QStackedWidget, QLineEdit, QGridLayout,
                             QCheckBox, QDialog, QProgressBar, QComboBox, QScrollArea, QDialogButtonBox, QInputDialog)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QTextCursor, QPixmap

import pyqtgraph as pg

# --- ПОПЫТКА ИМПОРТА ЯДРА ---
try:
    from main import VPNApplication
    from config import config

    CORE_AVAILABLE = True
except ImportError:
    print("⚠️ ЯДРО НЕ НАЙДЕНО. ЗАПУСК В РЕЖИМЕ ДЕМОНСТРАЦИИ ИНТЕРФЕЙСА.")
    CORE_AVAILABLE = False
    config = None  # Заглушка

# --- ЦВЕТОВАЯ ПАЛИТРА ---
C_BG = "#121212"
C_PANEL = "#1E1E1E"
C_ACCENT = "#00E5FF"
C_SERVER = "#7C4DFF"
C_GREEN = "#00E676"
C_RED = "#FF5252"
C_TEXT = "#FFFFFF"
C_TEXT_DIM = "#B0BEC5"

STYLESHEET = f"""
QMainWindow {{ background-color: {C_BG}; }}
QWidget {{ font-family: 'Segoe UI', sans-serif; color: {C_TEXT}; font-size: 14px; }}
QFrame#Panel {{ background-color: {C_PANEL}; border-radius: 12px; border: 1px solid #333; }}
QFrame#Sidebar {{ background-color: #181818; border-right: 1px solid #333; }}
QPushButton#MenuBtn {{ background-color: transparent; color: {C_TEXT_DIM}; text-align: left; padding: 15px 25px; border: none; font-size: 15px; }}
QPushButton#MenuBtn:hover {{ background-color: #2C2C2C; color: {C_TEXT}; }}
QPushButton#MenuBtn:checked {{ color: {C_ACCENT}; background-color: #252525; border-left: 4px solid {C_ACCENT}; }}
QPushButton#ActionBtn {{ background-color: {C_ACCENT}; color: #000; border-radius: 8px; font-weight: bold; font-size: 16px; padding: 12px; }}
QPushButton#ActionBtn:hover {{ background-color: #4DD0E1; }}
QPushButton#ActionBtn[state="stop"] {{ background-color: {C_RED}; color: white; }}
QLineEdit {{ background-color: #252525; border: 1px solid #444; padding: 8px; border-radius: 6px; color: {C_ACCENT}; }}
QLineEdit:focus {{ border: 1px solid {C_ACCENT}; }}
QTextEdit {{ background-color: #000000; border: 1px solid #333; border-radius: 6px; font-family: 'Consolas', monospace; font-size: 12px; }}
QComboBox {{ background-color: #252525; color: {C_ACCENT}; padding: 8px; border: 1px solid #444; border-radius: 6px; }}
QComboBox::drop-down {{ border: none; }}
QLabel#Header {{ font-size: 24px; font-weight: bold; color: {C_TEXT}; }}
QLabel#StatValue {{ font-size: 28px; font-weight: bold; color: {C_ACCENT}; }}
"""


class LogBridge(logging.Handler):
    def __init__(self, signal):
        super().__init__()
        self.signal = signal

    def emit(self, record):
        self.signal.emit(self.format(record), record.levelno)


class VPNWorker(QThread):
    log_signal = pyqtSignal(str, int)
    status_signal = pyqtSignal(bool)
    traffic_signal = pyqtSignal()
    auth_request = pyqtSignal(str, object, str)

    def __init__(self, mode):
        super().__init__()
        self.mode = mode
        self.app = None
        self.loop = None
        self.auth_result = None

    def _gui_auth_wrapper(self, r_type, payload=None):
        event = threading.Event()
        self.auth_request.emit(r_type, event, payload)
        event.wait()
        return self.auth_result

    def run(self):
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.app = VPNApplication()
            self.app.set_callbacks(
                on_traffic=lambda: self.traffic_signal.emit(),
                auth_phone=lambda: self._gui_auth_wrapper('phone'),
                auth_code=lambda payload=None: self._gui_auth_wrapper('code', payload),
                auth_pass=lambda: self._gui_auth_wrapper('pass')
            )
            logger = logging.getLogger("VPN_Core")
            handler = LogBridge(self.log_signal)
            handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', '%H:%M:%S'))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            self.status_signal.emit(True)
            self.log_signal.emit(f"Инициализация ядра: {self.mode.upper()}", logging.INFO)
            self.loop.run_until_complete(self.app.run_async(self.mode))
        except Exception as e:
            self.log_signal.emit(f"Критическая ошибка: {e}", logging.ERROR)
        finally:
            self.status_signal.emit(False)
            self.loop.close()

    def stop(self):
        if self.app:
            self.app.is_running = False
            if self.loop and self.loop.is_running():
                asyncio.run_coroutine_threadsafe(self.app.shutdown(), self.loop)

    def get_stats(self):
        if self.app and hasattr(self.app, 'handler'):
            tap = getattr(self.app.handler, 'tap_interface', None)
            if tap: return tap.packet_count
        return 0


class StatCard(QFrame):
    def __init__(self, title, icon):
        super().__init__()
        self.setObjectName("Panel")
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        t = QLabel(title)
        t.setStyleSheet("color: #B0BEC5; font-size: 11px; text-transform: uppercase;")
        header.addWidget(t)
        header.addStretch()
        ic = QLabel(icon)
        ic.setStyleSheet("font-size: 18px;")
        header.addWidget(ic)
        layout.addLayout(header)
        self.value = QLabel("0")
        self.value.setObjectName("StatValue")
        layout.addWidget(self.value)
        self.sub = QLabel("WAITING...")
        self.sub.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.sub)

    def update_data(self, main_text, sub_text=None):
        self.value.setText(str(main_text))
        if sub_text: self.sub.setText(sub_text)


class Dashboard(QWidget):
    def __init__(self, parent_win):
        super().__init__()
        self.parent_win = parent_win
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)

        h_layout = QHBoxLayout()
        self.lbl_mode = QLabel("CLIENT MODE")
        self.lbl_mode.setObjectName("Header")
        h_layout.addWidget(self.lbl_mode)
        h_layout.addStretch()
        self.ip_badge = QLabel(f"IP: {config.client_ip}")
        h_layout.addWidget(self.ip_badge)
        layout.addLayout(h_layout)

        ctrl_layout = QHBoxLayout()
        self.btn_toggle = QPushButton("ПОДКЛЮЧИТЬСЯ")
        self.btn_toggle.setObjectName("ActionBtn")
        self.btn_toggle.setFixedWidth(200)
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle.clicked.connect(self.parent_win.toggle_vpn)
        self.lbl_status = QLabel("● ГОТОВ К РАБОТЕ")
        self.lbl_status.setStyleSheet("color: gray; font-weight: bold; margin-left: 15px;")
        ctrl_layout.addWidget(self.btn_toggle)
        ctrl_layout.addWidget(self.lbl_status)
        ctrl_layout.addStretch()
        layout.addLayout(ctrl_layout)

        grid = QGridLayout()
        self.card_speed = StatCard("СКОРОСТЬ", "⚡")
        self.card_total = StatCard("ОБЪЕМ", "📦")
        self.card_uptime = StatCard("ВРЕМЯ", "⏱️")
        grid.addWidget(self.card_speed, 0, 0)
        grid.addWidget(self.card_total, 0, 1)
        grid.addWidget(self.card_uptime, 0, 2)
        layout.addLayout(grid)

        g_panel = QFrame()
        g_panel.setObjectName("Panel")
        gl = QVBoxLayout(g_panel)
        gl.addWidget(QLabel("СЕТЕВАЯ АКТИВНОСТЬ"))
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('k')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
        self.curve = self.plot_widget.plot(pen=pg.mkPen(color=C_ACCENT, width=2))
        gl.addWidget(self.plot_widget)
        layout.addWidget(g_panel, stretch=2)

        l_panel = QFrame()
        l_panel.setObjectName("Panel")
        ll = QVBoxLayout(l_panel)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        ll.addWidget(self.log_view)
        layout.addWidget(l_panel, stretch=1)


class SettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        l = QVBoxLayout(self)
        l.setContentsMargins(40, 40, 40, 40)
        l.addWidget(QLabel("НАСТРОЙКИ КОНФИГУРАЦИИ", objectName="Header"))
        l.addSpacing(20)

        self.form = QFrame()
        self.form.setObjectName("Panel")
        self.grid = QGridLayout(self.form)
        self.grid.setSpacing(15)
        self.grid.setColumnStretch(1, 1)
        row = 0

        lbl_trans = QLabel("Протокол транспорта")
        lbl_trans.setStyleSheet(f"color: {C_TEXT_DIM}; font-weight: bold;")
        self.combo_trans = QComboBox()
        self.combo_trans.addItems(["telegram", "vk"])
        self.combo_trans.setCurrentText(getattr(config, 'transport_type', 'telegram'))
        self.combo_trans.currentTextChanged.connect(self.toggle_fields)
        self.grid.addWidget(lbl_trans, row, 0)
        self.grid.addWidget(self.combo_trans, row, 1, 1, 2)
        row += 1

        self.tg_widgets = []
        self.vk_widgets = []

        # TG Fields
        self.inp_api_id = self.add_field(row, "TG API ID", config.api_id, self.tg_widgets)
        row += 1
        self.inp_api_hash = self.add_secret_field(row, "TG API Hash", config.api_hash, self.tg_widgets)
        row += 1
        self.inp_bot_token = self.add_secret_field(row, "TG Bot Token", config.bot_token, self.tg_widgets)
        row += 1
        self.inp_chat_id = self.add_field(row, "TG Chat ID", config.chat_id, self.tg_widgets)
        row += 1

        # VK Fields
        vk_token_val = getattr(config, 'vk_token', '')

        lbl_token_info = QLabel("👇 ИЛИ Токен (Рекомендуется, обходит 2FA/Блок) 👇")
        lbl_token_info.setStyleSheet("color: #00E676; font-size: 11px;")
        self.grid.addWidget(lbl_token_info, row, 1, 1, 2)
        self.vk_widgets.append(lbl_token_info)
        row += 1

        self.inp_vk_token = self.add_secret_field(row, "VK Access Token", vk_token_val, self.vk_widgets)
        row += 1

        lbl_or = QLabel("--- ИЛИ Логин/Пароль ---")
        lbl_or.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.grid.addWidget(lbl_or, row, 1, 1, 2)
        self.vk_widgets.append(lbl_or)
        row += 1

        self.inp_vk_login = self.add_field(row, "VK Логин", config.vk_login, self.vk_widgets)
        row += 1
        self.inp_vk_pass = self.add_secret_field(row, "VK Пароль", config.vk_password, self.vk_widgets)
        row += 1
        self.inp_vk_peer = self.add_field(row, "VK Peer ID", config.vk_peer_id, self.vk_widgets)
        row += 1
        self.inp_vk_app = self.add_field(row, "VK App ID", config.vk_app_id, self.vk_widgets)
        row += 1

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #333;")
        self.grid.addWidget(sep, row, 0, 1, 3)
        row += 1

        self.inp_tap = self.add_field(row, "TAP Интерфейс", config.tap_interface_name)
        row += 1
        self.inp_key = self.add_secret_field(row, "Ключ (32 байт)", config.encryption_key)
        row += 1

        self.chk_comp = QCheckBox("Включить сжатие GZIP")
        self.chk_comp.setChecked(config.compression_enabled)
        self.chk_comp.setStyleSheet(f"color: {C_TEXT}; font-size: 14px; margin-top: 10px;")
        self.grid.addWidget(self.chk_comp, row, 0, 1, 3)
        row += 1

        btn_save = QPushButton("💾 Сохранить и Применить")
        btn_save.setObjectName("ActionBtn")
        btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.clicked.connect(self.save)

        l.addWidget(self.form)
        l.addSpacing(10)
        l.addWidget(btn_save)
        l.addStretch()
        self.toggle_fields(self.combo_trans.currentText())

    def add_field(self, row, label_text, value, group_list=None):
        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font-weight: bold;")
        inp = QLineEdit(str(value))
        self.grid.addWidget(lbl, row, 0)
        self.grid.addWidget(inp, row, 1, 1, 2)
        if group_list is not None:
            group_list.append(lbl)
            group_list.append(inp)
        return inp

    def add_secret_field(self, row, label_text, value, group_list=None):
        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font-weight: bold;")
        inp = QLineEdit(str(value))
        inp.setEchoMode(QLineEdit.EchoMode.Password)
        btn_eye = QPushButton("👁")
        btn_eye.setCheckable(True)
        btn_eye.setFixedSize(40, 36)
        btn_eye.setStyleSheet(
            "QPushButton { background-color: #2C2C2C; border: 1px solid #444; } QPushButton:checked { background-color: #00E5FF; color: black; }")

        def toggle(c): inp.setEchoMode(QLineEdit.EchoMode.Normal if c else QLineEdit.EchoMode.Password)

        btn_eye.toggled.connect(toggle)
        self.grid.addWidget(lbl, row, 0)
        self.grid.addWidget(inp, row, 1)
        self.grid.addWidget(btn_eye, row, 2)
        if group_list is not None:
            group_list.append(lbl)
            group_list.append(inp)
            group_list.append(btn_eye)
        return inp

    def toggle_fields(self, text):
        is_vk = (text == 'vk')
        for w in self.tg_widgets: w.setVisible(not is_vk)
        for w in self.vk_widgets: w.setVisible(is_vk)

    def save(self):
        try:
            config.transport_type = self.combo_trans.currentText()
            config.tap_interface_name = self.inp_tap.text()
            config.encryption_key = self.inp_key.text()
            config.compression_enabled = self.chk_comp.isChecked()

            if config.transport_type == 'telegram':
                config.api_id = int(self.inp_api_id.text())
                config.api_hash = self.inp_api_hash.text()
                config.bot_token = self.inp_bot_token.text()
                config.chat_id = self.inp_chat_id.text()
            else:
                config.vk_token = self.inp_vk_token.text()  # Сохраняем токен
                config.vk_login = self.inp_vk_login.text()
                config.vk_password = self.inp_vk_pass.text()
                config.vk_peer_id = self.inp_vk_peer.text()
                try:
                    config.vk_app_id = int(self.inp_vk_app.text())
                except:
                    config.vk_app_id = 6121396

            if len(config.encryption_key.encode()) != 32:
                print("⚠️ Ключ должен быть 32 байта!")
                return

            config.save_to_file()

            btn = self.sender()
            if btn:
                btn.setText("✅ Сохранено!")
                QTimer.singleShot(1500, lambda: btn.setText("💾 Сохранить и Применить"))

            print(f"✅ Настройки сохранены. Режим: {config.transport_type}")

        except Exception as e:
            print(f"❌ Ошибка сохранения: {e}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Telegram/VK VPN")
        self.resize(1100, 750)
        self.setStyleSheet(STYLESHEET)
        central = QWidget()
        self.setCentralWidget(central)
        main_l = QHBoxLayout(central)
        main_l.setContentsMargins(0, 0, 0, 0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(240)
        sl = QVBoxLayout(sidebar)
        logo = QLabel("VPN Tunnel")
        logo.setStyleSheet(f"font-size: 22px; font-weight: 900; color: {C_TEXT}; padding: 20px;")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sl.addWidget(logo)

        self.btn_dash = QPushButton("📊  МОНИТОРИНГ")
        self.btn_dash.setObjectName("MenuBtn")
        self.btn_dash.setCheckable(True)
        self.btn_dash.setChecked(True)
        self.btn_sett = QPushButton("⚙️  НАСТРОЙКИ")
        self.btn_sett.setObjectName("MenuBtn")
        self.btn_sett.setCheckable(True)
        self.btn_mode = QPushButton("🔄  СМЕНИТЬ РЕЖИМ")
        self.btn_mode.setObjectName("MenuBtn")
        self.btn_mode.setStyleSheet("color: #FFB74D;")

        sl.addWidget(self.btn_dash)
        sl.addWidget(self.btn_sett)
        sl.addStretch()
        sl.addWidget(self.btn_mode)
        sl.addSpacing(20)
        main_l.addWidget(sidebar)

        self.stack = QStackedWidget()
        self.dash = Dashboard(self)
        self.sett = SettingsPage()
        self.stack.addWidget(self.dash)
        self.stack.addWidget(self.sett)
        main_l.addWidget(self.stack)

        self.btn_dash.clicked.connect(lambda: self.switch_page(0))
        self.btn_sett.clicked.connect(lambda: self.switch_page(1))
        self.btn_mode.clicked.connect(self.switch_mode)

        self.current_mode = "client"
        self.worker = None
        self.is_running = False
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_stats)
        self.last_pkts = 0
        self.start_time = 0

        # --- ИСПРАВЛЕНИЕ: Добавлена история данных ---
        self.data_history = deque([0] * 60, maxlen=60)
        # ---------------------------------------------

        self.apply_theme()

    def switch_page(self, idx):
        self.stack.setCurrentIndex(idx)
        self.btn_dash.setChecked(idx == 0)
        self.btn_sett.setChecked(idx == 1)

    def switch_mode(self):
        if self.is_running: return
        self.current_mode = "server" if self.current_mode == "client" else "client"
        self.apply_theme()

    def apply_theme(self):
        is_client = self.current_mode == "client"
        color = C_ACCENT if is_client else C_SERVER
        ip = config.client_ip if is_client else config.server_ip
        self.dash.lbl_mode.setText(f"{self.current_mode.upper()} MODE")
        self.dash.ip_badge.setText(f"IP: {ip}")
        self.dash.ip_badge.setStyleSheet(
            f"background: {C_PANEL}; padding: 5px 10px; border-radius: 4px; color: {color}; border: 1px solid {color};")
        self.dash.curve.setPen(pg.mkPen(color=color, width=2))

        # Обновляем цвет заливки графика
        fill_color = QColor(color)
        fill_color.setAlpha(30)
        self.dash.plot_widget.clear()
        self.dash.plot_widget.addItem(
            pg.FillBetweenItem(self.dash.curve, self.dash.plot_widget.plot(), brush=pg.mkBrush(fill_color)))
        self.dash.plot_widget.addItem(self.dash.curve)

    def toggle_vpn(self):
        if not self.is_running:
            self.start_vpn()
        else:
            self.stop_vpn()

    def start_vpn(self):
        self.dash.log_view.clear()
        self.data_history.clear()
        self.data_history.extend([0] * 60)

        self.worker = VPNWorker(self.current_mode)
        self.worker.log_signal.connect(self.append_log)
        self.worker.status_signal.connect(self.on_status)
        self.worker.traffic_signal.connect(self.on_traffic)
        self.worker.auth_request.connect(self.handle_auth)
        self.worker.start()
        self.dash.btn_toggle.setText("ОСТАНОВИТЬ")
        self.dash.btn_toggle.setProperty("state", "stop")
        self.dash.btn_toggle.style().polish(self.dash.btn_toggle)
        self.dash.lbl_status.setText("● ЗАПУСК...")
        self.btn_mode.setEnabled(False)
        self.is_running = True

    def stop_vpn(self):
        if self.worker: self.worker.stop()
        self.timer.stop()
        self.dash.btn_toggle.setText("ПОДКЛЮЧИТЬСЯ")
        self.dash.btn_toggle.setProperty("state", "normal")
        self.dash.btn_toggle.style().polish(self.dash.btn_toggle)
        self.dash.lbl_status.setText("● ОТКЛЮЧЕНО")
        self.btn_mode.setEnabled(True)
        self.is_running = False

    def on_status(self, running):
        if not running and self.is_running: self.stop_vpn()

    def on_traffic(self):
        if not self.timer.isActive():
            self.start_time = time.time()
            self.timer.start(1000)
            self.dash.lbl_status.setText("● АКТИВНО")
            self.dash.lbl_status.setStyleSheet(f"color: {C_GREEN}; font-weight: bold; margin-left: 15px;")

    def handle_auth(self, r_type, event, payload):
        title = "Авторизация"
        text, ok = None, False

        if payload and payload.startswith('http'):
            # VK Captcha
            dlg = QDialog(self)
            dlg.setWindowTitle("VK Captcha")
            vbox = QVBoxLayout(dlg)

            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                }
                response = requests.get(payload, headers=headers)
                data = response.content

                pix = QPixmap()
                pix.loadFromData(data)

                if not pix.isNull():
                    pix = pix.scaled(300, 150, Qt.AspectRatioMode.KeepAspectRatio)

                lbl_img = QLabel()
                lbl_img.setPixmap(pix)
                lbl_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
                vbox.addWidget(lbl_img)
            except Exception as e:
                print(f"Captcha Load Error: {e}")
                vbox.addWidget(QLabel(f"Ошибка загрузки: {e}"))
                link_lbl = QLabel(f"<a href='{payload}'>Открыть в браузере</a>")
                link_lbl.setOpenExternalLinks(True)
                vbox.addWidget(link_lbl)

            inp = QLineEdit()
            inp.setPlaceholderText("Введите код с картинки")
            inp.setStyleSheet("font-size: 16px; padding: 5px;")
            vbox.addWidget(inp)

            bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            bb.accepted.connect(dlg.accept)
            bb.rejected.connect(dlg.reject)
            vbox.addWidget(bb)

            if dlg.exec():
                text, ok = inp.text(), True
        else:
            if r_type == 'phone':
                text, ok = QInputDialog.getText(self, title, "Номер телефона:")
            elif r_type == 'code':
                text, ok = QInputDialog.getText(self, title, "Код подтверждения:")
            elif r_type == 'pass':
                text, ok = QInputDialog.getText(self, title, "Облачный пароль:", QLineEdit.EchoMode.Password)

        self.worker.auth_result = text if ok else None
        event.set()

    def update_stats(self):
        if not self.worker: return
        el = int(time.time() - self.start_time)
        m, s = divmod(el, 60)
        self.dash.card_uptime.update_data(f"{m:02}:{s:02}")
        pkts = self.worker.get_stats()
        diff = pkts - self.last_pkts
        self.last_pkts = pkts
        self.dash.card_speed.update_data(f"{diff * 1.2:.1f} KB/s")
        self.dash.card_total.update_data(f"{(pkts * 1.2 / 1024):.2f} MB")

        # --- ИСПРАВЛЕНИЕ: Добавление в историю и отрисовка ---
        self.data_history.append(diff)
        self.dash.curve.setData(list(self.data_history))
        # -----------------------------------------------------

    def append_log(self, text, level):
        c = C_RED if level == logging.ERROR else "#FFD700" if level == logging.WARNING else "#FFF"
        self.dash.log_view.append(
            f'<span style="color:#666">[{time.strftime("%H:%M:%S")}]</span> <span style="color:{c}">{text}</span>')


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())