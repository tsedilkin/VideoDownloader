# Video Downloader

Веб-интерфейс для скачивания видео с поддержкой m3u8, HLS и других форматов.

## Установка

1. Установите зависимости Python:
```bash
pip install -r requirements.txt
```

2. Установите `yt-dlp` (для скачивания видео):
```bash
pip install yt-dlp
```

3. Установите `ffmpeg` (для обработки видео):
   - macOS: `brew install ffmpeg`
   - Linux: `sudo apt-get install ffmpeg` или `sudo yum install ffmpeg`
   - Windows: Скачайте с [ffmpeg.org](https://ffmpeg.org/download.html)

## Запуск

```bash
python app.py
```

Или с uvicorn напрямую:
```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

Откройте браузер и перейдите на `http://localhost:8000`

## Использование

1. Вставьте URL страницы с видео в поле ввода
2. Нажмите кнопку "Скачать"
3. Дождитесь завершения загрузки
4. Видео будет сохранено в папку Downloads в формате MP4

## Особенности

- Поддержка m3u8, HLS, MP4 и других форматов
- Автоматическая конвертация в MP4
- Современный UI с отображением прогресса
- Сохранение в папку Downloads
- Автоматический выбор лучшего качества

