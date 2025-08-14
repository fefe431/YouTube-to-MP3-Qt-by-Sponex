import os
import sys
import shutil
import queue
import webbrowser
import urllib.request
from dataclasses import dataclass
from typing import List, Dict, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from yt_dlp import YoutubeDL


# App metadata
APP_NAME = "YouTube to MP3"
APP_VERSION = "1.0.0"
APP_AUTHOR = "Sponex"
PAYPAL_EMAIL = "viorelstanculet1234@outlook.com"
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
APP_ICON_PATH = os.path.join(ASSETS_DIR, "app_icon.png")


def find_local_ffmpeg_dir() -> str:
    base_dir = os.path.join(os.path.dirname(__file__), "tools", "ffmpeg")
    if not os.path.isdir(base_dir):
        return ""
    target_exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for root, _dirs, files in os.walk(base_dir):
        if target_exe in files:
            return root
    return ""


def normalize_bitrate_to_yt_dlp_quality(bitrate: str) -> str:
    cleaned = bitrate.strip().lower()
    if cleaned.endswith("k"):
        cleaned = cleaned[:-1]
    if not cleaned.isdigit():
        return "192"
    return cleaned


def build_yt_dlp_options(
    output_dir: str,
    audio_format: str,
    bitrate: str,
    embed_thumbnail: bool,
    write_metadata: bool,
    cookies_file: str,
    progress_hook,
    uploader_filter_str: str = "",
    download_archive_path: str = "",
) -> Dict:
    postprocessors: List[Dict] = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": audio_format,
            "preferredquality": normalize_bitrate_to_yt_dlp_quality(bitrate),
        }
    ]
    if write_metadata:
        postprocessors.append({"key": "FFmpegMetadata"})

    ydl_opts: Dict = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(output_dir, "%(title)s.%(ext)s"),
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "overwrites": False,
        "default_search": "ytsearch",
        "postprocessors": postprocessors,
        "progress_hooks": [progress_hook],
    }

    local_ffmpeg = find_local_ffmpeg_dir()
    if local_ffmpeg:
        ydl_opts["ffmpeg_location"] = local_ffmpeg
    if embed_thumbnail:
        ydl_opts["writethumbnail"] = True
        postprocessors.append({"key": "FFmpegThumbnailsEmbed"})
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file
    if download_archive_path:
        ydl_opts["download_archive"] = download_archive_path
    if uploader_filter_str:
        needle = uploader_filter_str.strip().lower()

        def _match_filter(info_dict):
            uploader = (info_dict.get("uploader") or "").lower()
            channel = (info_dict.get("channel") or "").lower()
            artist = (info_dict.get("artist") or "").lower()
            if needle and (needle in uploader or needle in channel or needle in artist):
                return None
            return f"skip: uploader/channel does not include '{uploader_filter_str}'"

        ydl_opts["match_filter"] = _match_filter

    return ydl_opts


@dataclass
class QueueItem:
    url: str
    title: str = "Pending..."
    row: int = -1
    video_id: str = ""


class DownloadWorker(QtCore.QObject):
    # row, status, progress(0..1), speed_str, eta_str, downloaded_bytes, total_bytes
    progress = QtCore.Signal(int, str, float, str, str, int, int)
    finished_item = QtCore.Signal(int, str)  # row, out_dir
    errored_item = QtCore.Signal(int, str)  # row, message

    def __init__(self, get_settings, parent=None):
        super().__init__(parent)
        self._queue: "queue.Queue[QueueItem]" = queue.Queue()
        self._get_settings = get_settings
        self._stop = False

    @QtCore.Slot()
    def stop(self):
        self._stop = True

    def enqueue(self, item: QueueItem):
        self._queue.put(item)

    @QtCore.Slot()
    def run(self):
        while not self._stop:
            try:
                item = self._queue.get(timeout=0.25)
            except queue.Empty:
                QtCore.QCoreApplication.processEvents()
                continue

            s = self._get_settings()
            archive = os.path.join(s["output_dir"], "downloaded.txt")

            def hook(d: Dict):
                status = d.get("status")
                if status == "downloading":
                    pct_str = (d.get("_percent_str") or "?").strip()
                    speed = (d.get("_speed_str") or "").strip()
                    eta = (d.get("_eta_str") or "").strip()
                    downloaded = int(d.get("downloaded_bytes") or 0)
                    total = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
                    try:
                        pct = float(pct_str.replace("%", "")) / 100.0
                    except Exception:
                        pct = 0.0
                    self.progress.emit(
                        item.row, "Downloading", max(0.0, min(1.0, pct)), speed, eta, downloaded, total
                    )
                elif status == "finished":
                    self.progress.emit(item.row, "Converting", 1.0, "", "", 0, 0)

            try:
                ydl_opts = build_yt_dlp_options(
                    output_dir=s["output_dir"],
                    audio_format=s["audio_format"],
                    bitrate=s["bitrate"],
                    embed_thumbnail=s["embed_thumbnail"],
                    write_metadata=s["write_metadata"],
                    cookies_file=s["cookies_file"],
                    progress_hook=hook,
                    uploader_filter_str=s.get("artist_filter", ""),
                    download_archive_path=archive,
                )
                with YoutubeDL(ydl_opts) as ydl:
                    ydl.download([item.url])
                # Find produced file path (best effort):
                out_dir = s["output_dir"]
                self.finished_item.emit(item.row, out_dir)
            except Exception as exc:
                self.errored_item.emit(item.row, str(exc))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} - Qt v{APP_VERSION}")
        self.resize(1000, 700)

        # Window icon
        self._apply_app_icon()

        # State
        self.session_ids: set[str] = set()

        # Central widget
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        # Menubar
        menubar = self.menuBar()
        help_menu = menubar.addMenu("&Help")
        about_action = QtGui.QAction("About", self)
        donate_action = QtGui.QAction("Donate", self)
        help_menu.addAction(about_action)
        help_menu.addAction(donate_action)

        about_action.triggered.connect(self.show_about)
        donate_action.triggered.connect(self.show_donate_dialog)

        # Controls row
        self.input_edit = QtWidgets.QPlainTextEdit()
        self.input_edit.setPlaceholderText("Paste YouTube URLs or type searches (multi-line or comma-separated). URLs download the exact video; searches use top result.")
        self.input_edit.setFixedHeight(64)

        self.add_btn = QtWidgets.QPushButton("Add")
        self.start_btn = QtWidgets.QPushButton("Start")
        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.donate_btn = QtWidgets.QPushButton("Donate ✨")

        top_row = QtWidgets.QHBoxLayout()
        top_row.addWidget(self.input_edit, 1)
        btns = QtWidgets.QHBoxLayout()
        btns.addWidget(self.add_btn)
        btns.addWidget(self.start_btn)
        btns.addWidget(self.clear_btn)
        btns.addWidget(self.donate_btn)
        top_row.addLayout(btns)

        layout.addLayout(top_row)

        # Settings grid
        grid = QtWidgets.QGridLayout()
        row = 0

        self.output_dir = QtWidgets.QLineEdit(os.path.join(os.getcwd(), "downloads"))
        os.makedirs(self.output_dir.text(), exist_ok=True)
        browse_btn = QtWidgets.QPushButton("Browse…")
        grid.addWidget(QtWidgets.QLabel("Output folder"), row, 0)
        grid.addWidget(self.output_dir, row, 1)
        grid.addWidget(browse_btn, row, 2)
        row += 1

        self.artist_filter = QtWidgets.QLineEdit()
        grid.addWidget(QtWidgets.QLabel("Only this artist/channel (optional)"), row, 0)
        grid.addWidget(self.artist_filter, row, 1, 1, 2)
        row += 1

        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems(["mp3", "m4a", "flac", "wav", "opus", "aac", "vorbis"])
        self.bitrate_combo = QtWidgets.QComboBox()
        self.bitrate_combo.addItems(["128", "160", "192", "256", "320"])
        self.embed_thumb = QtWidgets.QCheckBox("Embed thumbnail")
        self.write_meta = QtWidgets.QCheckBox("Write metadata")
        self.write_meta.setChecked(True)

        grid.addWidget(QtWidgets.QLabel("Format"), row, 0)
        grid.addWidget(self.format_combo, row, 1)
        grid.addWidget(QtWidgets.QLabel("Bitrate"), row, 2)
        grid.addWidget(self.bitrate_combo, row, 3)
        row += 1
        grid.addWidget(self.embed_thumb, row, 0)
        grid.addWidget(self.write_meta, row, 1)

        layout.addLayout(grid)

        # Tabs: Queue and Library
        tabs = QtWidgets.QTabWidget()
        layout.addWidget(tabs, 1)

        # Queue tab contents
        queue_page = QtWidgets.QWidget()
        q_layout = QtWidgets.QVBoxLayout(queue_page)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Title", "Status", "Progress", "Speed", "ETA", "Size"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for col in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeToContents)
        q_layout.addWidget(self.table)
        tabs.addTab(queue_page, "Queue")

        # Library tab contents
        lib_page = QtWidgets.QWidget()
        l_layout = QtWidgets.QVBoxLayout(lib_page)
        refresh_btn = QtWidgets.QPushButton("Refresh")
        self.library_list = QtWidgets.QListWidget()
        l_layout.addWidget(refresh_btn, 0)
        l_layout.addWidget(self.library_list, 1)
        tabs.addTab(lib_page, "Library")

        # Wire up
        self.add_btn.clicked.connect(self.on_add)
        self.start_btn.clicked.connect(self.on_start)
        self.clear_btn.clicked.connect(self.on_clear)
        refresh_btn.clicked.connect(self.refresh_library)
        browse_btn.clicked.connect(self.on_browse)
        self.donate_btn.clicked.connect(self.show_donate_dialog)

        # Worker thread
        self._thread = QtCore.QThread(self)
        self._worker = DownloadWorker(self.get_settings)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.on_progress)
        self._worker.finished_item.connect(self.on_finished_item)
        self._worker.errored_item.connect(self.on_errored_item)

        self.refresh_library()
        # Status bar metadata
        self.statusBar().showMessage(f"v{APP_VERSION} • Made by {APP_AUTHOR} • PayPal: {PAYPAL_EMAIL}")
        # Try load PayPal icon for the donate button (best-effort)
        self._set_donate_icon()

    def paypal_url(self) -> str:
        # Use standard donate URL with business email
        from urllib.parse import quote_plus
        business = quote_plus(PAYPAL_EMAIL)
        return f"https://www.paypal.com/donate?business={business}&no_recurring=0&currency_code=USD"

    def _load_pixmap_from_url(self, url: str) -> Optional[QtGui.QPixmap]:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = resp.read()
            pm = QtGui.QPixmap()
            if pm.loadFromData(data):
                return pm
        except Exception:
            return None
        return None

    def _set_donate_icon(self):
        # Public PayPal logo asset
        logo_url = "https://www.paypalobjects.com/webstatic/icon/pp258.png"
        pm = self._load_pixmap_from_url(logo_url)
        if pm:
            icon = QtGui.QIcon(pm)
            self.donate_btn.setIcon(icon)
            self.donate_btn.setIconSize(QtCore.QSize(18, 18))

    def open_donate(self):
        try:
            webbrowser.open(self.paypal_url())
        except Exception:
            pass

    def show_donate_dialog(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Donate")
        v = QtWidgets.QVBoxLayout(dlg)
        lbl = QtWidgets.QLabel()
        lbl.setTextFormat(QtCore.Qt.RichText)
        # Use hosted PayPal button image
        img_url = "https://www.paypalobjects.com/en_US/i/btn/btn_donate_LG.gif"
        lbl.setText(
            f"<div style='text-align:center'>"
            f"<p>If you'd like to support development:</p>"
            f"<p><a href='{self.paypal_url()}'>"
            f"<img src='{img_url}' alt='Donate with PayPal'/>"
            f"</a></p>"
            f"<p>PayPal: <b>{PAYPAL_EMAIL}</b></p>"
            f"</div>"
        )
        lbl.setOpenExternalLinks(True)
        v.addWidget(lbl)
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        v.addWidget(btns)
        btns.rejected.connect(dlg.close)
        dlg.exec()

    def _apply_app_icon(self):
        # Prefer local assets/app_icon.png if present, else draw a vector icon
        icon: Optional[QtGui.QIcon] = None
        try:
            if os.path.isfile(APP_ICON_PATH):
                pm = QtGui.QPixmap(APP_ICON_PATH)
                if not pm.isNull():
                    icon = QtGui.QIcon(pm)
        except Exception:
            icon = None
        if icon is None:
            icon = self._draw_vector_app_icon()
        if icon is not None:
            self.setWindowIcon(icon)
            QtWidgets.QApplication.setWindowIcon(icon)

    def _draw_vector_app_icon(self) -> QtGui.QIcon:
        size = 256
        pm = QtGui.QPixmap(size, size)
        pm.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pm)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # Background gradient circle
        rect = QtCore.QRectF(0, 0, size, size)
        grad = QtGui.QLinearGradient(0, 0, size, size)
        grad.setColorAt(0.0, QtGui.QColor(98, 0, 234))  # purple
        grad.setColorAt(1.0, QtGui.QColor(48, 63, 159))  # indigo
        brush = QtGui.QBrush(grad)
        painter.setBrush(brush)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(rect.adjusted(8, 8, -8, -8))
        # Music note
        painter.setPen(QtGui.QPen(QtGui.QColor("white"), 8))
        path = QtGui.QPainterPath()
        path.moveTo(size * 0.38, size * 0.35)
        path.lineTo(size * 0.70, size * 0.28)
        path.lineTo(size * 0.70, size * 0.58)
        path.addEllipse(QtCore.QPointF(size * 0.48, size * 0.62), size * 0.08, size * 0.06)
        path.addEllipse(QtCore.QPointF(size * 0.70, size * 0.70), size * 0.08, size * 0.06)
        painter.drawPath(path)
        painter.end()
        return QtGui.QIcon(pm)

    def show_about(self):
        text = (
            f"<h3>{APP_NAME} - Qt</h3>"
            f"<p>Version: <b>{APP_VERSION}</b><br/>"
            f"Made by: <b>{APP_AUTHOR}</b></p>"
            f"<p>Support the project via PayPal:<br/>"
            f"<a href='{self.paypal_url()}'>{PAYPAL_EMAIL}</a></p>"
        )
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("About")
        msg.setTextFormat(QtCore.Qt.RichText)
        msg.setText(text)
        msg.exec()

    # Settings
    def get_settings(self) -> Dict:
        return {
            "output_dir": self.output_dir.text().strip(),
            "audio_format": self.format_combo.currentText(),
            "bitrate": self.bitrate_combo.currentText(),
            "embed_thumbnail": self.embed_thumb.isChecked(),
            "write_metadata": self.write_meta.isChecked(),
            "cookies_file": "",
            "artist_filter": self.artist_filter.text().strip(),
        }

    # UI actions
    def on_browse(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output folder", self.output_dir.text())
        if d:
            self.output_dir.setText(d)
            os.makedirs(d, exist_ok=True)
            self.refresh_library()

    def on_add(self):
        raw = self.input_edit.toPlainText().strip()
        if not raw:
            return
        tokens: List[str] = []
        for p in raw.replace("\r", "").replace(",", "\n").split("\n"):
            t = p.strip()
            if not t:
                continue
            if not (t.startswith("http://") or t.startswith("https://") or t.startswith("ytsearch")):
                t = f"ytsearch1:{t}"
            tokens.append(t)
        for t in tokens:
            row = self.table.rowCount()
            self.table.insertRow(row)
            title_item = QtWidgets.QTableWidgetItem("Resolving…")
            status_item = QtWidgets.QTableWidgetItem("Queued")
            progress_bar = QtWidgets.QProgressBar()
            progress_bar.setMinimum(0)
            progress_bar.setMaximum(100)
            progress_bar.setValue(0)
            self.table.setItem(row, 0, title_item)
            self.table.setItem(row, 1, status_item)
            self.table.setCellWidget(row, 2, progress_bar)
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(""))
            self.table.setItem(row, 4, QtWidgets.QTableWidgetItem(""))
            self.table.setItem(row, 5, QtWidgets.QTableWidgetItem(""))

            qi = QueueItem(url=t, title="Resolving…", row=row)
            # Try to resolve id/title without downloading
            try:
                s = self.get_settings()
                opts = build_yt_dlp_options(
                    output_dir=s["output_dir"],
                    audio_format=s["audio_format"],
                    bitrate=s["bitrate"],
                    embed_thumbnail=s["embed_thumbnail"],
                    write_metadata=s["write_metadata"],
                    cookies_file=s["cookies_file"],
                    progress_hook=lambda _d: None,
                    uploader_filter_str=s.get("artist_filter", ""),
                    download_archive_path=os.path.join(s["output_dir"], "downloaded.txt"),
                )
                opts["skip_download"] = True
                opts["noplaylist"] = True
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(t, download=False)
                if info:
                    if "entries" in info and info["entries"]:
                        info = info["entries"][0]
                    qi.video_id = info.get("id") or ""
                    qi.title = info.get("title") or qi.title
                    true_url = info.get("webpage_url") or info.get("url") or t
                    qi.url = true_url
            except Exception:
                pass

            # Session dedup
            if qi.video_id and qi.video_id in self.session_ids:
                self.table.setItem(row, 1, QtWidgets.QTableWidgetItem("Skipped (duplicate)"))
                continue
            if qi.video_id:
                self.session_ids.add(qi.video_id)

            self.table.item(row, 0).setText(qi.title)
            self.table.item(row, 1).setText("Queued")
            # Enqueue for worker
            self._worker.enqueue(qi)

    def on_start(self):
        if not self._thread.isRunning():
            self._thread.start()

    def on_clear(self):
        self.table.setRowCount(0)
        self.session_ids.clear()

    # Worker signals
    @QtCore.Slot(int, str, float, str, str, int, int)
    def on_progress(self, row: int, status: str, progress: float, speed: str, eta: str, downloaded: int, total: int):
        if 0 <= row < self.table.rowCount():
            self.table.item(row, 1).setText(status)
            w = self.table.cellWidget(row, 2)
            if isinstance(w, QtWidgets.QProgressBar):
                w.setValue(int(progress * 100))
            self.table.item(row, 3).setText(speed)
            self.table.item(row, 4).setText(eta)
            if total:
                self.table.item(row, 5).setText(f"{downloaded/1048576:.2f}/{total/1048576:.2f} MB")
            else:
                self.table.item(row, 5).setText("")

    @QtCore.Slot(int, str)
    def on_finished_item(self, row: int, _out_dir: str):
        if 0 <= row < self.table.rowCount():
            self.table.item(row, 1).setText("Done")
            w = self.table.cellWidget(row, 2)
            if isinstance(w, QtWidgets.QProgressBar):
                w.setValue(100)
        self.refresh_library()

    @QtCore.Slot(int, str)
    def on_errored_item(self, row: int, message: str):
        if 0 <= row < self.table.rowCount():
            self.table.item(row, 1).setText(f"Error: {message[:80]}")

    # Library
    def refresh_library(self):
        self.library_list.clear()
        root = self.output_dir.text().strip()
        if not os.path.isdir(root):
            return
        for name in sorted(os.listdir(root), reverse=True):
            full = os.path.join(root, name)
            if not os.path.isfile(full):
                continue
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in {"mp3", "m4a", "flac", "wav", "opus", "aac", "ogg", "oga"}:
                continue
            item = QtWidgets.QListWidgetItem(name)
            item.setData(QtCore.Qt.UserRole, full)
            self.library_list.addItem(item)
        self.library_list.itemDoubleClicked.connect(self.open_file)

    def open_file(self, item: QtWidgets.QListWidgetItem):
        path = item.data(QtCore.Qt.UserRole)
        try:
            if os.name == "nt":
                os.startfile(path)
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')
        except Exception:
            pass


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()


