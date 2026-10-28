# DocuDesk

Локальное настольное приложение для Windows 10/11: базовые инструменты PDF, конвертация PDF/изображений и сканирование через WIA.

## Что работает в версии 0.1.0

- Объединение нескольких PDF.
- Разделение PDF на отдельные страницы.
- Базовая оптимизация/сжатие PDF.
- Экспорт страниц PDF в PNG/JPG с выбором DPI.
- Объединение изображений в многостраничный PDF.
- Обнаружение сканеров WIA в Windows и сканирование одной страницы в файл.
- Обработка файлов локально, без облачной загрузки.

## Ограничения первой версии

- Сканирование использует WIA. Устройство должно иметь совместимый драйвер Windows; не все сетевые сканеры видны как WIA.
- Пока реализовано сканирование одной страницы. Пакетный ADF, дуплекс и TWAIN-адаптер требуют отдельной реализации и проверки на реальном оборудовании.
- Word/Excel/PowerPoint-конвертация, OCR, защита/разблокировка PDF и редактор страниц пока не реализованы.
- «Сжатие» оптимизирует структуру PDF, но не гарантирует уменьшение размера, если в документе уже сжаты изображения.

## Запуск из исходников (Windows 10/11)

Установите Python 3.10–3.12 с python.org, отметив **Add Python to PATH**. Откройте PowerShell в папке проекта:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
python -m docudesk.main
```

Если PowerShell запрещает активацию окружения, можно использовать напрямую:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m docudesk.main
```

## Сборка портативного EXE

```powershell
pip install pyinstaller
pyinstaller --noconfirm --clean --windowed --name DocuDesk --collect-all fitz --collect-all PySide6 --paths src run_docudesk.py
```

Исполняемый файл будет в `dist\DocuDesk\DocuDesk.exe` (onedir-сборка). Это переносимая папка: передавайте папку целиком, а не один EXE.

## Сборка установщика

1. Сначала выполните сборку портативной папки по инструкции выше.
2. Установите Inno Setup 6.
3. Откройте `scripts\DocuDesk.iss` в Inno Setup Compiler и нажмите Compile.
4. Установщик появится в `installer\DocuDesk-Setup.exe`.

## Примечание о сборке

Сборку EXE нужно выполнять на Windows для целевой Windows-платформы. В этой среде исходный проект подготовлен, но Windows-сборка и сканирование физического устройства не могли быть проверены на реальном оборудовании.

## Самая простая сборка через GitHub

Не нужно собирать приложение локально: загрузите проект в новый репозиторий GitHub вместе с `.github/workflows/windows-release.yml`. Откройте вкладку **Actions**, выберите **Build Windows releases** и нажмите **Run workflow**. По завершении запуска скачайте артефакт `DocuDesk-Windows-builds`: внутри будут установщик и portable ZIP.

Пошаговая инструкция: [`GITHUB_SETUP.md`](GITHUB_SETUP.md).
