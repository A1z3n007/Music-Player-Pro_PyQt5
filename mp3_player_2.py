import sys
import os
import random
import requests
import musicbrainzngs
import numpy as np

from mutagen import File as MutagenFile

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QPushButton, QHBoxLayout, QVBoxLayout,
    QFileDialog, QLabel, QTableWidget, QTableWidgetItem, QAction, QHeaderView,
    QSplitter, QLineEdit, QSystemTrayIcon, QMenu, QActionGroup, QTabWidget,
    QTextEdit, QListWidget, QListWidgetItem, QAbstractItemView, QSlider
)
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent, QAudioProbe
from PyQt5.QtGui import (
    QPixmap, QIcon, QPainter, QColor, QLinearGradient, QRadialGradient, QFont, QDesktopServices
)
from PyQt5.QtCore import (
    QUrl, Qt, QByteArray, QObject, pyqtSignal, QThread, QPointF, QTimer, QSettings, QPoint
)


class Theme:
    def __init__(self, name, **c):
        self.name = name
        for k, v in c.items():
            setattr(self, k, v)


DARK = Theme(
    "Тёмная",
    bg="#0e141b", panel="#151d26", text="#e9eef5", sub="#9fb0c2",
    accent="#1db954", accent2="#1ed760", alt="#101821", row="#151d26",
    handle="#1db954", groove="rgba(255,255,255,0.15)"
)
LIGHT = Theme(
    "Светлая",
    bg="#f5f7fb", panel="#ffffff", text="#0f1721", sub="#4b5a73",
    accent="#0066ff", accent2="#7a3cff", alt="#f3f6fb", row="#ffffff",
    handle="#0066ff", groove="rgba(0,0,0,0.18)"
)
NEON = Theme(
    "Неон",
    bg="#080a10", panel="#0b1118", text="#eaffff", sub="#a7c1c7",
    accent="#00ffd5", accent2="#7c4dff", alt="#0a0f16", row="#0b1118",
    handle="#00ffd5", groove="rgba(255,255,255,0.22)"
)


def make_tray_icon(accent: str) -> QIcon:
    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QPointF(size / 2, size / 2)
    g = QRadialGradient(c, size * 0.55)
    g.setColorAt(0.0, QColor(accent))
    g.setColorAt(1.0, QColor(24, 28, 36))
    p.setBrush(g); p.setPen(Qt.NoPen)
    p.drawEllipse(4, 4, size - 8, size - 8)
    p.setBrush(QColor(255, 255, 255)); p.setPen(Qt.NoPen)
    tri = [QPointF(size * 0.42, size * 0.33), QPointF(size * 0.42, size * 0.67), QPointF(size * 0.70, size * 0.50)]
    p.drawPolygon(*tri)
    p.end()
    icon = QIcon(pm)
    icon.addPixmap(pm.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    icon.addPixmap(pm.scaled(16, 16, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    return icon


class DynamicBackground(QWidget):
    def __init__(self, theme: Theme):
        super().__init__()
        self.theme = theme
        self.hue = 0.6
        self.pulse = 0.0
        self.brightness = 0.12
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(36)

    def set_theme(self, theme: Theme):
        self.theme = theme
        self.update()

    def _tick(self):
        self.hue += 0.0009
        self.update()

    def update_audio(self, bass_norm: float, vol_norm: float, bright_norm: float):
        self.pulse += (min(bass_norm * 1.6 + vol_norm * 0.2, 1.0) - self.pulse) * 0.12
        self.brightness += ((0.10 + bright_norm * 0.25) - self.brightness) * 0.10
        self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        center = QPointF(w / 2, h / 2)
        radius = min(w, h) * (0.65 + self.pulse * 0.45)
        g = QRadialGradient(center, radius)
        g.setColorAt(0.0, QColor.fromHsvF(self.hue % 1.0, 0.85, min(self.brightness + 0.2, 1.0), 1.0))
        g.setColorAt(0.8, QColor(self.theme.accent2))
        g.setColorAt(1.0, QColor(self.theme.bg))
        p.fillRect(self.rect(), g)


class Visualizer(QWidget):
    def __init__(self, theme: Theme, num_bars: int = 48):
        super().__init__()
        self.theme = theme
        self.num_bars = num_bars
        self.magnitudes = np.zeros(self.num_bars)
        self.setMinimumHeight(88)

    def set_theme(self, theme: Theme):
        self.theme = theme
        self.update()

    def update_magnitudes(self, mags: np.ndarray):
        if len(mags) == self.num_bars:
            self.magnitudes = 0.68 * self.magnitudes + 0.32 * mags
            self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        bw = max(w / self.num_bars, 2)
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor(self.theme.accent))
        grad.setColorAt(1.0, QColor(self.theme.accent2))
        p.setBrush(grad); p.setPen(Qt.NoPen)
        for i, m in enumerate(self.magnitudes):
            bh = int(min(m * h * 1.1, h))
            x = int(i * bw) + 1
            y = h - bh
            p.drawRoundedRect(x, y, int(bw - 3), bh, 3, 3)


class ArtWorker(QObject):
    art_found = pyqtSignal(QPixmap)
    finished = pyqtSignal()

    def __init__(self, artist: str, title: str):
        super().__init__()
        self.artist = artist
        self.title = title

    def run(self):
        try:
            musicbrainzngs.set_useragent("MusicPlayerPro", "3.0", "https://example.com")
            res = musicbrainzngs.search_releases(artist=self.artist, release=self.title, limit=1)
            if not res.get("release-list"):
                self.finished.emit(); return
            rgid = res["release-list"][0]["release-group"]["id"]
            url = f"http://coverartarchive.org/release-group/{rgid}/front-250"
            r = requests.get(url, timeout=10); r.raise_for_status()
            pix = QPixmap(); pix.loadFromData(QByteArray(r.content))
            self.art_found.emit(pix)
        except Exception:
            pass
        finally:
            self.finished.emit()


class LyricsWorker(QObject):
    text_ready = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, artist: str, title: str):
        super().__init__()
        self.artist = artist
        self.title = title

    def run(self):
        ua = {"User-Agent": "MusicPlayerPro/3.0"}
        lyrics = ""
        try:
            r = requests.get(f"https://lyrist.vercel.app/api/{self.artist}/{self.title}", timeout=8, headers=ua)
            if r.ok: lyrics = (r.json().get("lyrics") or "").strip()
        except Exception: pass
        if not lyrics:
            try:
                r = requests.get(f"https://api.lyrics.ovh/v1/{self.artist}/{self.title}", timeout=8, headers=ua)
                if r.ok: lyrics = (r.json().get("lyrics") or "").strip()
            except Exception: pass
        if not lyrics: lyrics = "Текст не найден."
        self.text_ready.emit(lyrics); self.finished.emit()


class SeekSlider(QSlider):
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.orientation() == Qt.Horizontal:
            x = e.pos().x() / max(1, self.width())
            self.setValue(int(self.minimum() + x * (self.maximum() - self.minimum())))
            e.accept()
        super().mousePressEvent(e)


class MiniPlayer(QWidget):
    def __init__(self, main: "SmartPlayer"):
        super().__init__()
        self.main = main
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setFixedSize(320, 124)
        self.art = QLabel(); self.art.setFixedSize(80, 80)
        self.title = QLabel("Нет трека"); self.title.setWordWrap(True)
        row = QHBoxLayout(); row.addWidget(self.art); row.addWidget(self.title, 1)
        self.prev_btn = QPushButton("⏮"); self.play_btn = QPushButton("▶"); self.next_btn = QPushButton("⏭")
        btns = QHBoxLayout(); btns.addStretch(1)
        for b in (self.prev_btn, self.play_btn, self.next_btn): btns.addWidget(b)
        btns.addStretch(1)
        root = QVBoxLayout(self); root.addLayout(row); root.addLayout(btns)
        self.prev_btn.clicked.connect(self.main._prev_track)
        self.play_btn.clicked.connect(self.main.toggle_play_pause)
        self.next_btn.clicked.connect(self.main._next_track)

    def update_track(self, t: dict):
        self.title.setText(f"{t['artist']} — {t['title']}")
        if t.get("art"):
            pix = QPixmap(); pix.loadFromData(QByteArray(t["art"]))
            self.art.setPixmap(pix.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))


class SmartPlayer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.theme = DARK
        self.setWindowTitle("Music Player Pro")
        self.resize(1180, 720)
        self.tray_icon = make_tray_icon(self.theme.accent); self.setWindowIcon(self.tray_icon)

        self.bg = DynamicBackground(self.theme); self.setCentralWidget(self.bg)
        self.player = QMediaPlayer(self); self.probe = None

        self.playlist = []
        self.index = -1
        self.repeat_mode = 0
        self.shuffle = False
        self.shuffle_history = []

        self.queue = []  # list of file paths
        self.fade_ms = 600
        self.fade_timer = None
        self.fade_step = 0
        self.fade_target = 0
        self.fade_base_volume = 80

        self.art_thread = None; self.art_worker = None
        self.lyr_thread = None; self.lyr_worker = None
        self.mini = None
        self.settings = QSettings("MusicPlayerPro", "SmartPlayer")

        self._build_ui()
        self._build_menus()
        self._apply_theme()
        self._create_tray()
        self._load_settings()

        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.mediaStatusChanged.connect(self._on_status)

        self.setAcceptDrops(True)

    def _build_ui(self):
        self.search = QLineEdit(); self.search.setPlaceholderText("Поиск по артисту или названию…")
        self.search.textChanged.connect(self._filter)

        self.tabs = QTabWidget()
        self.tab_playlist = QWidget()
        self.tab_lyrics = QWidget()
        self.tab_queue = QWidget()
        self.tabs.addTab(self.tab_playlist, "Плейлист")
        self.tabs.addTab(self.tab_lyrics, "Текст")
        self.tabs.addTab(self.tab_queue, "Очередь")

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "Артист", "Название", "Время"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(lambda i: self.play_index(i.row()))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)

        pl = QVBoxLayout(self.tab_playlist); pl.setContentsMargins(0, 0, 0, 0); pl.addWidget(self.table)

        self.lyrics = QTextEdit(); self.lyrics.setReadOnly(True)
        ll = QVBoxLayout(self.tab_lyrics); ll.setContentsMargins(0, 0, 0, 0); ll.addWidget(self.lyrics)

        self.queue_list = QListWidget()
        self.queue_list.setAlternatingRowColors(True)
        self.queue_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.queue_list.setDefaultDropAction(Qt.MoveAction)
        self.queue_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.queue_list.itemDoubleClicked.connect(self._queue_play_item)
        self.queue_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue_list.customContextMenuRequested.connect(self._queue_menu)
        ql = QVBoxLayout(self.tab_queue); ql.setContentsMargins(0, 0, 0, 0); ql.addWidget(self.queue_list)

        self.album_art = QLabel("No Art"); self.album_art.setAlignment(Qt.AlignCenter)
        self.album_art.setMinimumSize(280, 280); self.album_art.setMaximumSize(280, 280)

        self.visualizer = Visualizer(self.theme)
        self.now_playing = QLabel("Выберите трек"); self.now_playing.setAlignment(Qt.AlignCenter); self.now_playing.setWordWrap(True)

        self.slider = SeekSlider(Qt.Horizontal)
        self.current_time = QLabel("00:00")
        self.total_time = QLabel("00:00")

        self.volume = QSlider(Qt.Horizontal); self.volume.setRange(0, 100); self.volume.setValue(80)
        self.player.setVolume(80)
        self.volume.valueChanged.connect(self.player.setVolume)

        self.btn_prev = QPushButton("⏮")
        self.btn_rew  = QPushButton("⏪")
        self.btn_play = QPushButton("▶")
        self.btn_fwd  = QPushButton("⏩")
        self.btn_stop = QPushButton("⏹")
        self.btn_next = QPushButton("⏭")
        self.btn_rep  = QPushButton("🔁")
        self.btn_shuf = QPushButton("🔀"); self.btn_shuf.setObjectName("Shuffle")

        for b in (self.btn_prev, self.btn_rew, self.btn_play, self.btn_fwd, self.btn_stop, self.btn_next, self.btn_rep, self.btn_shuf):
            b.setFixedSize(52, 52)

        self.btn_play.clicked.connect(self.toggle_play_pause)
        self.btn_stop.clicked.connect(self.player.stop)
        self.btn_prev.clicked.connect(self._prev_track)
        self.btn_next.clicked.connect(self._next_track)
        self.btn_rew.clicked.connect(lambda: self.player.setPosition(max(0, self.player.position() - 10_000)))
        self.btn_fwd.clicked.connect(lambda: self.player.setPosition(min(self.player.duration(), self.player.position() + 10_000)))
        self.btn_rep.clicked.connect(self._toggle_repeat)
        self.btn_shuf.setCheckable(True); self.btn_shuf.clicked.connect(self._toggle_shuffle)

        self.slider.sliderMoved.connect(self.player.setPosition)

        left = QVBoxLayout()
        left.addWidget(self.album_art)
        left.addWidget(self.visualizer)
        left.addWidget(self.now_playing)

        right = QVBoxLayout()
        right.addWidget(self.search)
        right.addWidget(self.tabs, 1)

        progress = QHBoxLayout()
        progress.addWidget(self.current_time)
        progress.addWidget(self.slider, 1)
        progress.addWidget(self.total_time)

        ctrls = QHBoxLayout()
        ctrls.addStretch(1)
        for b in (self.btn_prev, self.btn_rew, self.btn_play, self.btn_fwd, self.btn_stop, self.btn_next, self.btn_rep, self.btn_shuf):
            ctrls.addWidget(b)
        ctrls.addStretch(1)

        right.addLayout(progress)
        right.addLayout(ctrls)
        right.addWidget(self.volume)

        splitter = QSplitter(Qt.Horizontal)
        lw = QWidget(); lw.setLayout(left)
        rw = QWidget(); rw.setLayout(right)
        splitter.addWidget(lw); splitter.addWidget(rw); splitter.setSizes([340, 840])

        root = QWidget()
        root_l = QHBoxLayout(root); root_l.setContentsMargins(16, 16, 16, 16)
        root_l.addWidget(splitter)

        self.bg_layout = QVBoxLayout(self.bg); self.bg_layout.setContentsMargins(0, 0, 0, 0)
        self.bg_layout.addWidget(root)

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("Файл")
        a_add_files  = QAction("Добавить файлы…", self); a_add_files.triggered.connect(self._add_files)
        a_add_folder = QAction("Добавить папку…", self); a_add_folder.triggered.connect(self._add_folder)
        a_load = QAction("Загрузить плейлист…", self); a_load.triggered.connect(self._load_playlist)
        a_save = QAction("Сохранить плейлист…", self); a_save.triggered.connect(self._save_playlist)
        a_clear= QAction("Очистить плейлист", self); a_clear.triggered.connect(self._clear_playlist)
        for a in (a_add_files, a_add_folder, None, a_load, a_save, None, a_clear):
            file_menu.addAction(a) if a else file_menu.addSeparator()

        theme_menu = self.menuBar().addMenu("Тема")
        group = QActionGroup(self); group.setExclusive(True)
        for th in (DARK, LIGHT, NEON):
            act = QAction(th.name, self, checkable=True)
            act.triggered.connect(lambda _, t=th: self._set_theme(t))
            theme_menu.addAction(act); group.addAction(act)
            if th is self.theme: act.setChecked(True)

        view_menu = self.menuBar().addMenu("Вид")
        a_mini = QAction("Открыть мини-плеер", self); a_mini.setShortcut("Ctrl+M"); a_mini.triggered.connect(self._show_mini)
        view_menu.addAction(a_mini)

    def _create_tray(self):
        self.tray = QSystemTrayIcon(self.tray_icon, self)
        m = QMenu()
        a_show = QAction("Показать", self); a_show.triggered.connect(self.show)
        a_play = QAction("Play/Pause", self); a_play.triggered.connect(self.toggle_play_pause)
        a_prev = QAction("Предыдущий", self); a_prev.triggered.connect(self._prev_track)
        a_next = QAction("Следующий", self); a_next.triggered.connect(self._next_track)
        a_mini = QAction("Мини-плеер", self); a_mini.triggered.connect(self._show_mini)
        a_quit = QAction("Выход", self); a_quit.triggered.connect(QApplication.instance().quit)
        for a in (a_show, a_play, a_prev, a_next, a_mini, a_quit): m.addAction(a)
        self.tray.setContextMenu(m); self.tray.show()

    def closeEvent(self, e):
        self._save_settings()
        if self.tray.isVisible():
            e.ignore()
            self.hide()
            self.tray.showMessage("Music Player", "Свернуто в трей", self.windowIcon(), 1500)

    def _set_theme(self, t: Theme):
        self.theme = t
        self.bg.set_theme(t); self.visualizer.set_theme(t)
        icon = make_tray_icon(self.theme.accent); self.tray.setIcon(icon); self.setWindowIcon(icon)
        self._apply_theme()

    def _apply_theme(self):
        th = self.theme
        self.setStyleSheet(f"""
            QMainWindow {{ background: {th.bg}; }}
            QWidget {{ color: {th.text}; background: transparent; font-size: 14px; }}
            QMenuBar {{ background: {th.panel}; padding: 6px; }}
            QMenu {{ background: {th.panel}; border: 1px solid {th.groove}; }}
            QMenu::item:selected {{ background: {th.handle}; color: black; }}
            QLineEdit {{ background: {th.panel}; border: 1px solid {th.groove}; border-radius: 10px; padding: 8px 10px; }}
            QTabWidget::pane {{ border: 1px solid {th.groove}; border-radius: 12px; background: {th.panel}; }}
            QTabBar::tab {{ padding: 6px 12px; background: {th.panel}; border-radius: 10px; margin: 2px; }}
            QTabBar::tab:selected {{ background: {th.handle}; color: black; }}
            QTableWidget {{ background: {th.row}; alternate-background-color: {th.alt}; border-radius: 10px; }}
            QHeaderView::section {{ background: {th.panel}; border: none; padding: 8px; }}
            QSlider::groove:horizontal {{ height: 8px; background: {th.groove}; border-radius: 4px; }}
            QSlider::handle:horizontal {{ background: {th.handle}; border: 1px solid {th.handle}; width: 18px; margin:-5px 0; border-radius: 9px; }}
            QPushButton {{ background: {th.panel}; border: none; border-radius: 26px; padding: 8px 10px; color: {th.text}; }}
            QPushButton:hover {{ background: {th.handle}; color: black; }}
            QPushButton#Shuffle:checked {{ background: {th.handle}; color: black; }}
            QListWidget {{ background: {th.row}; border: 1px solid {th.groove}; border-radius: 10px; }}
        """)

    def _ensure_probe(self):
        if not self.probe:
            self.probe = QAudioProbe(self)
            self.probe.audioBufferProbed.connect(self._process_audio)
        self.probe.setSource(self.player)

    def _process_audio(self, buffer):
        data = buffer.constData(); data.setsize(buffer.byteCount())
        arr = np.frombuffer(data, dtype=np.int16)
        if arr.size == 0: return
        rms = float(np.sqrt(np.mean(arr.astype(np.float64) ** 2)) / 32768.0)
        mag = np.abs(np.fft.rfft(arr))
        bass = float(np.mean(mag[:max(1, len(mag) // 24)]))
        bass_norm = min(bass / 220000.0, 1.0)
        n = self.visualizer.num_bars; bar = np.zeros(n); chunk = max(1, len(mag) // n)
        for i in range(n):
            s, e = i * chunk, min(len(mag), (i + 1) * chunk)
            bar[i] = np.mean(mag[s:e])
        logm = np.log10(bar + 1.0); mx = np.max(logm) if np.max(logm) > 0 else 1.0
        self.visualizer.update_magnitudes(logm / mx)
        self.bg.update_audio(bass_norm, rms, rms)

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Выбрать аудио", "", "Аудиофайлы (*.mp3 *.flac *.wav *.m4a)")
        if not files: return
        for f in files: self._add_track(f)
        if self.index == -1 and self.playlist: self.play_index(0)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выбрать папку с музыкой")
        if not folder: return
        for root, _, files in os.walk(folder):
            for fn in files:
                if fn.lower().endswith((".mp3", ".flac", ".wav", ".m4a")):
                    self._add_track(os.path.join(root, fn))
        if self.index == -1 and self.playlist: self.play_index(0)

    def _save_playlist(self):
        if not self.playlist: return
        fp, _ = QFileDialog.getSaveFileName(self, "Сохранить плейлист", "", "M3U Playlist (*.m3u)")
        if not fp: return
        with open(fp, "w", encoding="utf-8") as f:
            for t in self.playlist: f.write(t["path"] + "\n")
        self.settings.setValue("last_playlist", fp)

    def _load_playlist(self):
        fp, _ = QFileDialog.getOpenFileName(self, "Загрузить плейлист", "", "M3U Playlist (*.m3u)")
        if not fp: return
        self._clear_playlist()
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                p = line.strip()
                if p and os.path.exists(p): self._add_track(p)
        if self.index == -1 and self.playlist: self.play_index(0)
        self.settings.setValue("last_playlist", fp)

    def _clear_playlist(self):
        self._stop_art_thread()
        if self.probe: self.probe.setSource(None)
        self.player.setMedia(QMediaContent())
        self.playlist.clear(); self.table.setRowCount(0)
        self.index = -1; self.shuffle_history.clear()
        self.queue.clear(); self.queue_list.clear()
        self.visualizer.update_magnitudes(np.zeros(self.visualizer.num_bars))
        self.album_art.setText("No Art"); self.album_art.setPixmap(QPixmap())
        self.now_playing.setText("Плейлист очищен")

    def _fmt_dur(self, seconds: float) -> str:
        if not seconds or seconds <= 0: return "--:--"
        s = int(round(seconds)); m, s = divmod(s, 60); return f"{m:02d}:{s:02d}"

    def _add_track(self, path: str):
        artist = "Неизвестный исполнитель"
        title = os.path.basename(path)
        art_bytes = None; dur = 0.0
        try:
            easy = MutagenFile(path, easy=True)
            if easy: artist = easy.get("artist", [artist])[0]; title = easy.get("title", [title])[0]
            raw = MutagenFile(path)
            if raw:
                if getattr(raw, "info", None) and hasattr(raw.info, "length"): dur = float(raw.info.length or 0.0)
                if hasattr(raw, "tags") and raw.tags:
                    if "APIC:" in raw.tags: art_bytes = raw.tags.get("APIC:").data
                    elif "covr" in raw.tags:
                        cv = raw.tags.get("covr"); 
                        if cv: art_bytes = cv[0]
                if hasattr(raw, "pictures") and raw.pictures: art_bytes = raw.pictures[0].data
        except Exception: pass
        self.playlist.append({"path": path, "artist": artist, "title": title, "art": art_bytes, "dur": dur})
        r = self.table.rowCount(); self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(str(r + 1)))
        self.table.setItem(r, 1, QTableWidgetItem(artist))
        self.table.setItem(r, 2, QTableWidgetItem(title))
        self.table.setItem(r, 3, QTableWidgetItem(self._fmt_dur(dur)))

    def play_index(self, i: int, *, fade=True):
        if not (0 <= i < len(self.playlist)): return
        self._ensure_probe(); self._stop_art_thread()
        if fade: self._fade_out_then(lambda: self._start_track(i))
        else: self._start_track(i)

    def _start_track(self, i: int):
        self.index = i; self.table.selectRow(i)
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setBackground(QColor(0, 0, 0, 0))
        self.table.item(i, 0).setBackground(QColor(self.theme.handle))
        t = self.playlist[i]
        self.player.setMedia(QMediaContent(QUrl.fromLocalFile(t["path"])))
        self.player.play(); self.btn_play.setText("⏸")
        self.now_playing.setText(f"{t['artist']} — {t['title']}")
        self.tray.showMessage("Сейчас играет", f"{t['artist']} — {t['title']}", self.windowIcon(), 1800)
        if t.get("art"):
            pix = QPixmap(); pix.loadFromData(QByteArray(t["art"]))
            self.album_art.setPixmap(pix.scaled(280, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.album_art.setText("Ищем обложку…")
            self.art_thread = QThread(); self.art_worker = ArtWorker(t["artist"], t["title"])
            self.art_worker.moveToThread(self.art_thread)
            self.art_thread.started.connect(self.art_worker.run)
            self.art_worker.art_found.connect(lambda p: self.album_art.setPixmap(p.scaled(280, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            self.art_worker.finished.connect(self.art_thread.quit)
            self.art_thread.start()
        if self.mini: self.mini.update_track(t)
        self._fetch_lyrics(t["artist"], t["title"])
        self._fade_in_to(self.volume.value())

    def toggle_play_pause(self):
        if self.player.state() == QMediaPlayer.PlayingState:
            self.player.pause(); self.btn_play.setText("▶")
        else:
            if self.index == -1 and self.playlist: self.play_index(0, fade=False)
            else: self.player.play()
            self.btn_play.setText("⏸")

    def _toggle_repeat(self):
        self.repeat_mode = (self.repeat_mode + 1) % 3
        self.btn_rep.setText(["🚫", "🔁", "🔂"][self.repeat_mode])

    def _toggle_shuffle(self):
        self.shuffle = not self.shuffle
        self.btn_shuf.setChecked(self.shuffle)
        if not self.shuffle: self.shuffle_history.clear()

    def _next_source(self) -> int:
        if self.queue:
            path = self.queue.pop(0)
            self._queue_refresh()
            for idx, t in enumerate(self.playlist):
                if t["path"] == path: return idx
        if self.shuffle:
            if len(self.playlist) <= 1: return self.index
            choices = [idx for idx in range(len(self.playlist)) if idx != self.index]
            pick = random.choice(choices); self.shuffle_history.append(self.index)
            return pick
        nxt = (self.index + 1)
        if nxt >= len(self.playlist):
            if self.repeat_mode == 1: return 0
            return -1
        return nxt

    def _prev_source(self) -> int:
        if self.shuffle and self.shuffle_history:
            return self.shuffle_history.pop()
        prv = (self.index - 1)
        if prv < 0:
            if self.repeat_mode == 1: return len(self.playlist) - 1
            return -1
        return prv

    def _next_track(self):
        idx = self._next_source()
        if idx >= 0: self.play_index(idx)
    def _prev_track(self):
        idx = self._prev_source()
        if idx >= 0: self.play_index(idx)

    def _on_status(self, status):
        if status == QMediaPlayer.EndOfMedia and self.index != -1:
            idx = self._next_source()
            if idx >= 0: self.play_index(idx)
    def _on_position(self, pos: int):
        self.slider.setValue(pos); self.current_time.setText(self._fmt_time(pos))
    def _on_duration(self, dur: int):
        self.slider.setRange(0, dur); self.total_time.setText(self._fmt_time(dur))
    @staticmethod
    def _fmt_time(ms: int) -> str:
        s = int(round(ms / 1000)); m, s = divmod(s, 60); return f"{m:02d}:{s:02d}"

    def _fetch_lyrics(self, artist: str, title: str):
        if self.lyr_thread and self.lyr_thread.isRunning(): return
        self.lyrics.setPlainText("Ищем текст…")
        self.lyr_thread = QThread(); self.lyr_worker = LyricsWorker(artist, title)
        self.lyr_worker.moveToThread(self.lyr_thread)
        self.lyr_thread.started.connect(self.lyr_worker.run)
        self.lyr_worker.text_ready.connect(self.lyrics.setPlainText)
        self.lyr_worker.finished.connect(self.lyr_thread.quit)
        self.lyr_thread.start()

    def _filter(self):
        st = self.search.text().lower()
        for r in range(self.table.rowCount()):
            a = self.table.item(r, 1).text().lower()
            t = self.table.item(r, 2).text().lower()
            self.table.setRowHidden(r, st not in a and st not in t)

    def _stop_art_thread(self):
        if self.art_thread and self.art_thread.isRunning():
            self.art_thread.quit(); self.art_thread.wait()

    def _show_mini(self):
        if not self.mini: self.mini = MiniPlayer(self)
        self.mini.show()

    def _table_menu(self, pos: QPoint):
        row = self.table.indexAt(pos).row()
        if row < 0: return
        menu = QMenu(self)
        act_playnext = QAction("Играть далее (Play Next)", self)
        act_enqueue = QAction("Добавить в очередь", self)
        act_open = QAction("Открыть файл", self)
        act_folder = QAction("Открыть папку", self)
        act_del = QAction("Удалить выбранные", self)
        for a in (act_playnext, act_enqueue, None, act_open, act_folder, None, act_del):
            menu.addAction(a) if a else menu.addSeparator()
        act_playnext.triggered.connect(lambda: self._enqueue_rows([row], front=True))
        act_enqueue.triggered.connect(lambda: self._enqueue_rows([row], front=False))
        act_open.triggered.connect(lambda: self._open_file(row))
        act_folder.triggered.connect(lambda: self._open_folder(row))
        act_del.triggered.connect(self._remove_selected)
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _enqueue_rows(self, rows, front=False):
        paths = [self.playlist[r]["path"] for r in rows if 0 <= r < len(self.playlist)]
        if front: self.queue = paths + self.queue
        else: self.queue.extend(paths)
        self._queue_refresh()

    def _queue_refresh(self):
        self.queue_list.clear()
        for path in self.queue:
            t = next((x for x in self.playlist if x["path"] == path), None)
            txt = f"{t['artist']} — {t['title']}" if t else os.path.basename(path)
            self.queue_list.addItem(QListWidgetItem(txt))

    def _queue_menu(self, pos: QPoint):
        menu = QMenu(self)
        act_remove = QAction("Удалить из очереди", self)
        act_clear = QAction("Очистить очередь", self)
        menu.addAction(act_remove); menu.addAction(act_clear)
        act_remove.triggered.connect(self._queue_remove_selected)
        act_clear.triggered.connect(self._queue_clear)
        menu.exec_(self.queue_list.viewport().mapToGlobal(pos))

    def _queue_remove_selected(self):
        rows = sorted({i.row() for i in self.queue_list.selectedIndexes()}, reverse=True)
        for r in rows:
            if 0 <= r < len(self.queue): del self.queue[r]
        self._queue_refresh()

    def _queue_clear(self):
        self.queue.clear(); self._queue_refresh()

    def _queue_play_item(self, item: QListWidgetItem):
        idx = self.queue_list.row(item)
        if 0 <= idx < len(self.queue):
            path = self.queue.pop(idx); self._queue_refresh()
            for i, t in enumerate(self.playlist):
                if t["path"] == path:
                    self.play_index(i); break

    def _open_file(self, row: int):
        path = self.playlist[row]["path"]; QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_folder(self, row: int):
        path = self.playlist[row]["path"]; folder = os.path.dirname(path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _remove_selected(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not rows: return
        for r in rows:
            if r < len(self.playlist):
                path = self.playlist[r]["path"]
                self.queue = [p for p in self.queue if p != path]
                del self.playlist[r]
                self.table.removeRow(r)
                if r == self.index:
                    self.player.stop(); self.index = -1
        for i in range(self.table.rowCount()):
            self.table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
        self._queue_refresh()

    def _save_settings(self):
        self.settings.setValue("theme", self.theme.name)
        self.settings.setValue("volume", self.volume.value())
        self.settings.setValue("repeat_mode", self.repeat_mode)
        self.settings.setValue("shuffle", self.shuffle)
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self.settings.setValue("last_playlist", self.settings.value("last_playlist", ""))

    def _load_settings(self):
        name = self.settings.value("theme", "Тёмная")
        theme_map = {"Тёмная": DARK, "Светлая": LIGHT, "Неон": NEON}
        self._set_theme(theme_map.get(name, DARK))
        vol = int(self.settings.value("volume", 80)); self.volume.setValue(vol); self.player.setVolume(vol)
        self.repeat_mode = int(self.settings.value("repeat_mode", 0)); self.btn_rep.setText(["🚫", "🔁", "🔂"][self.repeat_mode])
        self.shuffle = self.settings.value("shuffle", "false") in ("true", True); self.btn_shuf.setChecked(self.shuffle)
        geom = self.settings.value("geometry"); state = self.settings.value("windowState")
        if geom is not None: self.restoreGeometry(geom)
        if state is not None: self.restoreState(state)
        last = self.settings.value("last_playlist", "")
        if last and os.path.exists(last):
            try:
                with open(last, "r", encoding="utf-8") as f:
                    for line in f:
                        p = line.strip()
                        if p and os.path.exists(p): self._add_track(p)
                if self.index == -1 and self.playlist: self.play_index(0, fade=False)
            except Exception: pass

    def _fade_out_then(self, after):
        self.fade_base_volume = self.volume.value()
        if self.fade_ms <= 0:
            after(); return
        steps = 10; interval = max(15, self.fade_ms // steps)
        self.fade_step = steps; self.fade_target = 0
        if self.fade_timer: self.fade_timer.stop()
        self.fade_timer = QTimer(self); self.fade_timer.timeout.connect(lambda: self._fade_tick(after))
        self.fade_timer.start(interval)

    def _fade_tick(self, after):
        if self.fade_step <= 0:
            self.fade_timer.stop()
            self.player.setVolume(0)
            after()
            return
        cur = max(0, int(self.fade_base_volume * (self.fade_step / 10.0)))
        self.player.setVolume(cur)
        self.fade_step -= 1

    def _fade_in_to(self, target):
        if self.fade_ms <= 0:
            self.player.setVolume(target); return
        steps = 10; interval = max(15, self.fade_ms // steps)
        vals = [int(target * (i / steps)) for i in range(1, steps + 1)]
        t = QTimer(self); idx = 0
        def tick():
            nonlocal idx
            self.player.setVolume(vals[idx]); idx += 1
            if idx >= len(vals): t.stop()
        t.timeout.connect(tick); t.start(interval)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    font = QFont(); font.setPointSize(10); app.setFont(font)
    win = SmartPlayer(); win.show()
    sys.exit(app.exec_())
