from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from . import __version__
from .operations import compress_pdf, images_to_pdf, merge_pdfs, pdf_to_images, split_pdf
from .scanner import list_scanners, scan_one_page

APP_NAME = "DocuDesk"


class ToolPanel(QWidget):
    def __init__(self, title: str, description: str = ""):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 22)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("panelTitle")
        layout.addWidget(heading)
        if description:
            subtitle = QLabel(description)
            subtitle.setObjectName("muted")
            subtitle.setWordWrap(True)
            layout.addWidget(subtitle)
        self.content = QVBoxLayout()
        self.content.setSpacing(10)
        layout.addLayout(self.content)
        layout.addStretch(1)


def action_button(text: str, primary: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        button.setObjectName("primaryButton")
    return button


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — документы и сканирование")
        self.resize(1000, 700)
        self.setMinimumSize(800, 560)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f5f7fb; color: #202938; font-size: 14px; }
            QTabWidget::pane { border: 1px solid #e0e5ee; border-radius: 12px; background: white; }
            QTabBar::tab { background: #e9edf5; padding: 11px 18px; margin-right: 4px; border-top-left-radius: 8px; border-top-right-radius: 8px; }
            QTabBar::tab:selected { background: white; color: #c62845; font-weight: 600; }
            QLabel#panelTitle { font-size: 23px; font-weight: 700; color: #182230; }
            QLabel#muted { color: #667085; }
            QPushButton { background: white; border: 1px solid #d0d7e2; border-radius: 8px; padding: 10px 14px; text-align: left; }
            QPushButton:hover { border-color: #d92d4a; background: #fff8f9; }
            QPushButton#primaryButton { background: #d92d4a; color: white; border: 0; font-weight: 600; text-align: center; }
            QPushButton#primaryButton:hover { background: #b9233c; }
            QListWidget, QComboBox, QSpinBox { background: white; border: 1px solid #d0d7e2; border-radius: 7px; padding: 7px; }
            QFrame#hero { background: white; border: 1px solid #e0e5ee; border-radius: 14px; }
        """)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(22, 18, 22, 20)
        root_layout.setSpacing(14)
        header = QHBoxLayout()
        logo = QLabel("▣")
        logo.setStyleSheet("background:#d92d4a;color:white;border-radius:9px;font-size:25px;font-weight:bold;padding:8px 12px;")
        header.addWidget(logo)
        name_box = QVBoxLayout()
        name = QLabel("DocuDesk")
        name.setStyleSheet("font-size:25px;font-weight:800;color:#17202e;")
        tagline = QLabel("Документы, конвертация и сканирование — локально на компьютере")
        tagline.setObjectName("muted")
        name_box.addWidget(name)
        name_box.addWidget(tagline)
        header.addLayout(name_box)
        header.addStretch(1)
        version = QLabel(f"Версия {__version__}")
        version.setObjectName("muted")
        header.addWidget(version, alignment=Qt.AlignmentFlag.AlignTop)
        root_layout.addLayout(header)

        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(18, 15, 18, 15)
        hero_title = QLabel("Ваши документы под контролем")
        hero_title.setStyleSheet("font-size:18px;font-weight:700;")
        hero_text = QLabel("Объединяйте и разделяйте PDF, конвертируйте изображения и сканируйте бумажные документы. Файлы обрабатываются на этом компьютере.")
        hero_text.setWordWrap(True)
        hero_text.setObjectName("muted")
        hero_layout.addWidget(hero_title)
        hero_layout.addWidget(hero_text)
        root_layout.addWidget(hero)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._pdf_tab(), "PDF-инструменты")
        self.tabs.addTab(self._convert_tab(), "Конвертация")
        self.tabs.addTab(self._scanner_tab(), "Сканирование")
        self.tabs.addTab(self._about_tab(), "О программе")
        root_layout.addWidget(self.tabs, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage("Готово к работе")

    def _pdf_tab(self) -> QWidget:
        panel = ToolPanel("Инструменты PDF", "Выберите операцию. Оригинальные файлы не изменяются.")
        merge_label = QLabel("Объединение PDF")
        merge_label.setStyleSheet("font-weight:700;font-size:16px;")
        panel.content.addWidget(merge_label)
        self.merge_list = QListWidget()
        self.merge_list.setMinimumHeight(100)
        panel.content.addWidget(self.merge_list)
        merge_row = QHBoxLayout()
        add = action_button("＋ Добавить PDF")
        remove = action_button("Убрать выбранный")
        run_merge = action_button("Объединить и сохранить", True)
        add.clicked.connect(self._add_pdfs)
        remove.clicked.connect(lambda: self.merge_list.takeItem(self.merge_list.currentRow()) if self.merge_list.currentRow() >= 0 else None)
        run_merge.clicked.connect(self._merge)
        merge_row.addWidget(add)
        merge_row.addWidget(remove)
        merge_row.addStretch(1)
        merge_row.addWidget(run_merge)
        panel.content.addLayout(merge_row)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#e0e5ee;")
        panel.content.addWidget(line)
        split_label = QLabel("Разделение PDF — каждая страница в отдельный файл")
        split_label.setStyleSheet("font-weight:700;font-size:16px;")
        panel.content.addWidget(split_label)
        split_row = QHBoxLayout()
        split_button = action_button("Выбрать PDF и разделить", True)
        compress_button = action_button("Сжать PDF")
        split_button.clicked.connect(self._split)
        compress_button.clicked.connect(self._compress)
        split_row.addWidget(split_button)
        split_row.addWidget(compress_button)
        split_row.addStretch(1)
        panel.content.addLayout(split_row)
        return panel

    def _convert_tab(self) -> QWidget:
        panel = ToolPanel("Конвертация форматов", "В первой версии доступны PDF → PNG/JPG и изображения → PDF. Преобразование Word/Excel добавим следующим этапом через офисный движок.")
        to_images_title = QLabel("PDF → изображения")
        to_images_title.setStyleSheet("font-weight:700;font-size:16px;")
        panel.content.addWidget(to_images_title)
        form = QFormLayout()
        self.image_format = QComboBox()
        self.image_format.addItems(["PNG", "JPG"])
        self.image_dpi = QComboBox()
        self.image_dpi.addItems(["96", "150", "200", "300", "600"])
        self.image_dpi.setCurrentText("150")
        form.addRow("Формат изображения:", self.image_format)
        form.addRow("Разрешение (DPI):", self.image_dpi)
        panel.content.addLayout(form)
        export_images = action_button("Выбрать PDF и экспортировать страницы", True)
        export_images.clicked.connect(self._pdf_to_images)
        panel.content.addWidget(export_images)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        panel.content.addWidget(line)
        images_title = QLabel("Изображения → один PDF")
        images_title.setStyleSheet("font-weight:700;font-size:16px;")
        panel.content.addWidget(images_title)
        images_help = QLabel("Выберите несколько JPG, PNG, TIFF или BMP. Каждое изображение станет отдельной страницей PDF.")
        images_help.setWordWrap(True)
        images_help.setObjectName("muted")
        panel.content.addWidget(images_help)
        image_pdf_button = action_button("Выбрать изображения и сохранить PDF", True)
        image_pdf_button.clicked.connect(self._images_to_pdf)
        panel.content.addWidget(image_pdf_button)
        panel.content.addStretch(1)
        return panel

    def _scanner_tab(self) -> QWidget:
        panel = ToolPanel("Сканирование документов", "Поддержка зависит от драйвера устройства. В этой сборке реализовано обнаружение WIA-сканеров Windows и сканирование одной страницы; пакетный автоподатчик и дуплекс будут расширены в следующем этапе.")
        row = QHBoxLayout()
        self.scanner_combo = QComboBox()
        self.scanner_combo.setMinimumWidth(350)
        refresh = action_button("Обновить список")
        refresh.clicked.connect(self._refresh_scanners)
        row.addWidget(self.scanner_combo, 1)
        row.addWidget(refresh)
        panel.content.addLayout(row)
        form = QFormLayout()
        self.scan_color = QComboBox()
        self.scan_color.addItem("Цветное", True)
        self.scan_color.addItem("Чёрно-белое", False)
        self.scan_dpi = QComboBox()
        self.scan_dpi.addItems(["150", "200", "300", "600"])
        self.scan_dpi.setCurrentText("300")
        form.addRow("Режим:", self.scan_color)
        form.addRow("Разрешение:", self.scan_dpi)
        panel.content.addLayout(form)
        scan_button = action_button("Сканировать страницу", True)
        scan_button.clicked.connect(self._scan)
        panel.content.addWidget(scan_button)
        note = QLabel("Если устройство не отображается, установите официальный драйвер сканера и проверьте, что сканирование работает в приложении «Сканирование Windows». Сетевые устройства должны предоставлять совместимый WIA-драйвер.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        panel.content.addWidget(note)
        self._refresh_scanners(silent=True)
        return panel

    def _about_tab(self) -> QWidget:
        panel = ToolPanel("О DocuDesk", "Настольная программа для локальной обработки документов.")
        details = QLabel(
            "Текущая версия: 0.1.0\n\n"
            "Уже реализовано:\n"
            "• Объединение PDF\n• Разделение PDF по страницам\n• Базовое сжатие PDF без повторного кодирования изображений\n"
            "• Экспорт страниц PDF в PNG/JPG\n• Объединение изображений в PDF\n• Обнаружение WIA-сканеров и сканирование одной страницы в Windows\n\n"
            "Следующие этапы: пакетное сканирование через автоподатчик, двустороннее сканирование, OCR, конвертация DOCX/XLSX/PPTX, настройки и локализация."
        )
        details.setWordWrap(True)
        details.setStyleSheet("font-size:15px;line-height:1.5;")
        panel.content.addWidget(details)
        return panel

    def _error(self, message: str):
        QMessageBox.critical(self, "Не удалось выполнить операцию", message)
        self.statusBar().showMessage("Операция завершилась с ошибкой", 5000)

    def _success(self, message: str):
        QMessageBox.information(self, "DocuDesk", message)
        self.statusBar().showMessage("Операция выполнена", 5000)

    def _add_pdfs(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите PDF", "", "PDF-файлы (*.pdf)")
        existing = {self.merge_list.item(i).text() for i in range(self.merge_list.count())}
        for file in files:
            if file not in existing:
                self.merge_list.addItem(file)

    def _merge(self):
        files = [self.merge_list.item(i).text() for i in range(self.merge_list.count())]
        if len(files) < 2:
            return self._error("Для объединения выберите минимум два PDF-файла.")
        output, _ = QFileDialog.getSaveFileName(self, "Сохранить объединённый PDF", "merged.pdf", "PDF-файл (*.pdf)")
        if not output:
            return
        if not output.lower().endswith(".pdf"):
            output += ".pdf"
        try:
            result = merge_pdfs(files, output)
            self._success(f"Файлы объединены:\n{result}")
        except Exception as exc:
            self._error(str(exc))

    def _split(self):
        source, _ = QFileDialog.getOpenFileName(self, "Выберите PDF", "", "PDF-файлы (*.pdf)")
        if not source:
            return
        folder = QFileDialog.getExistingDirectory(self, "Папка для отдельных страниц")
        if not folder:
            return
        try:
            outputs = split_pdf(source, folder)
            self._success(f"Создано файлов: {len(outputs)}\nПапка: {folder}")
        except Exception as exc:
            self._error(str(exc))

    def _compress(self):
        source, _ = QFileDialog.getOpenFileName(self, "Выберите PDF", "", "PDF-файлы (*.pdf)")
        if not source:
            return
        default = str(Path(source).with_name(Path(source).stem + "_compressed.pdf"))
        output, _ = QFileDialog.getSaveFileName(self, "Сохранить сжатый PDF", default, "PDF-файл (*.pdf)")
        if not output:
            return
        if not output.lower().endswith(".pdf"):
            output += ".pdf"
        try:
            result = compress_pdf(source, output)
            before, after = Path(source).stat().st_size, result.stat().st_size
            self._success(f"Готово:\n{result}\nРазмер: {before // 1024} КБ → {after // 1024} КБ")
        except Exception as exc:
            self._error(str(exc))

    def _pdf_to_images(self):
        source, _ = QFileDialog.getOpenFileName(self, "Выберите PDF", "", "PDF-файлы (*.pdf)")
        if not source:
            return
        folder = QFileDialog.getExistingDirectory(self, "Папка для изображений")
        if not folder:
            return
        try:
            outputs = pdf_to_images(source, folder, self.image_format.currentText(), int(self.image_dpi.currentText()))
            self._success(f"Экспортировано страниц: {len(outputs)}\nПапка: {folder}")
        except Exception as exc:
            self._error(str(exc))

    def _images_to_pdf(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите изображения", "", "Изображения (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)")
        if not files:
            return
        output, _ = QFileDialog.getSaveFileName(self, "Сохранить PDF", "images.pdf", "PDF-файл (*.pdf)")
        if not output:
            return
        if not output.lower().endswith(".pdf"):
            output += ".pdf"
        try:
            result = images_to_pdf(files, output)
            self._success(f"PDF создан:\n{result}")
        except Exception as exc:
            self._error(str(exc))

    def _refresh_scanners(self, silent: bool = False):
        self.scanner_combo.clear()
        try:
            devices = list_scanners()
            for device in devices:
                self.scanner_combo.addItem(device["name"], device["id"])
            if not devices:
                self.scanner_combo.addItem("Совместимые WIA-сканеры не найдены", "")
                if not silent:
                    QMessageBox.information(self, "Сканеры", "Windows не обнаружила WIA-сканеры. Проверьте подключение и драйвер производителя.")
        except Exception as exc:
            self.scanner_combo.addItem("Список сканеров недоступен", "")
            if not silent:
                self._error(str(exc))

    def _scan(self):
        device_id = self.scanner_combo.currentData()
        if not device_id:
            return self._error("Сначала выберите доступный сканер.")
        output, selected_filter = QFileDialog.getSaveFileName(self, "Сохранить скан", "scan.jpg", "JPEG (*.jpg *.jpeg);;PNG (*.png);;TIFF (*.tif)")
        if not output:
            return
        suffix = Path(output).suffix.lower()
        if not suffix:
            suffix = ".png" if "PNG" in selected_filter else ".tif" if "TIFF" in selected_filter else ".jpg"
            output += suffix
        try:
            result = scan_one_page(device_id, output, bool(self.scan_color.currentData()), int(self.scan_dpi.currentText()))
            self._success(f"Скан сохранён:\n{result}")
        except Exception as exc:
            self._error(str(exc))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("DocuDesk")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
