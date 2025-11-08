from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import uvicorn
import os
import subprocess
import asyncio
from pathlib import Path
import re
import tempfile
import shutil

app = FastAPI()

# Монтируем статические файлы
app.mount("/static", StaticFiles(directory="static"), name="static")

# Модель для запроса
class DownloadRequest(BaseModel):
    url: str

# Глобальная переменная для отслеживания прогресса
download_progress = {}

def get_downloads_folder():
    """Получает путь к папке Downloads"""
    home = Path.home()
    downloads = home / "Downloads"
    downloads.mkdir(exist_ok=True)
    return downloads

def sanitize_filename(filename):
    """Очищает имя файла от недопустимых символов"""
    # Удаляем недопустимые символы
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    # Ограничиваем длину
    if len(filename) > 200:
        filename = filename[:200]
    return filename

async def download_video(url: str, download_id: str):
    """Скачивает видео используя yt-dlp"""
    try:
        downloads_folder = get_downloads_folder()
        
        # Используем yt-dlp для скачивания
        # Он поддерживает m3u8, HLS и многие другие форматы
        output_template = str(downloads_folder / "%(title)s.%(ext)s")
        
        cmd = [
            "yt-dlp",
            url,
            "-f", "bestvideo+bestaudio/best",  # Лучшее качество
            "--merge-output-format", "mp4",     # Объединяем в mp4
            "-o", output_template,
            "--progress",  # Показываем прогресс
            "--newline",   # Новая строка для каждого обновления
            "--no-warnings",  # Убираем предупреждения
        ]
        
        download_progress[download_id] = {
            "status": "downloading",
            "progress": 0,
            "message": "Начинаем загрузку...",
            "filename": None
        }
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # Объединяем stderr в stdout
        )
        
        filename = None
        last_progress = 0
        
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            
            line_str = line.decode('utf-8', errors='ignore').strip()
            
            # Парсим прогресс из вывода yt-dlp
            if "[download]" in line_str:
                # Ищем процент прогресса (формат: [download] 45.2% of 123.45MiB)
                progress_match = re.search(r'(\d+\.?\d*)%', line_str)
                if progress_match:
                    progress = float(progress_match.group(1))
                    last_progress = progress
                    download_progress[download_id]["progress"] = progress
                    
                    # Извлекаем размер файла если есть
                    size_match = re.search(r'of\s+([\d.]+[KMGT]?i?B)', line_str, re.IGNORECASE)
                    if size_match:
                        download_progress[download_id]["message"] = f"Загрузка: {progress:.1f}% ({size_match.group(1)})"
                    else:
                        download_progress[download_id]["message"] = f"Загрузка: {progress:.1f}%"
                
                # Ищем имя файла
                if "Destination:" in line_str:
                    filename_match = re.search(r'Destination:\s*(.+)', line_str)
                    if filename_match:
                        filename = filename_match.group(1).strip()
                elif "has already been downloaded" in line_str:
                    # Извлекаем имя файла из сообщения
                    filename_match = re.search(r'\[download\]\s*(.+\.mp4)', line_str)
                    if filename_match:
                        filename = filename_match.group(1).strip()
            elif "[Merger]" in line_str:
                download_progress[download_id]["message"] = "Объединение видео и аудио..."
            elif "[ExtractAudio]" in line_str:
                download_progress[download_id]["message"] = "Обработка аудио..."
            elif "ERROR" in line_str or "WARNING" in line_str:
                # Логируем ошибки, но продолжаем
                pass
        
        await process.wait()
        
        if process.returncode == 0:
            # Если имя файла не найдено, ищем последний созданный файл
            if not filename:
                # Ищем последний созданный mp4 файл в папке Downloads
                mp4_files = list(downloads_folder.glob("*.mp4"))
                if mp4_files:
                    # Сортируем по времени создания
                    filename = str(max(mp4_files, key=os.path.getctime))
            
            if filename and os.path.exists(filename):
                download_progress[download_id] = {
                    "status": "completed",
                    "progress": 100,
                    "message": "Загрузка завершена!",
                    "filename": os.path.basename(filename)
                }
            else:
                download_progress[download_id] = {
                    "status": "error",
                    "progress": last_progress,
                    "message": "Файл не найден после загрузки",
                    "filename": None
                }
        else:
            # Читаем оставшийся вывод для ошибок
            remaining_output = await process.stdout.read()
            error_msg = remaining_output.decode('utf-8', errors='ignore')
            if not error_msg:
                error_msg = "Неизвестная ошибка при загрузке"
            
            download_progress[download_id] = {
                "status": "error",
                "progress": last_progress,
                "message": f"Ошибка загрузки: {error_msg[:300]}",
                "filename": None
            }
    
    except FileNotFoundError:
        download_progress[download_id] = {
            "status": "error",
            "progress": 0,
            "message": "yt-dlp не найден. Установите его: pip install yt-dlp",
            "filename": None
        }
    except Exception as e:
        download_progress[download_id] = {
            "status": "error",
            "progress": 0,
            "message": f"Ошибка: {str(e)}",
            "filename": None
        }

@app.get("/")
async def read_root():
    """Главная страница"""
    return FileResponse("static/index.html")

@app.post("/api/download")
async def download_video_endpoint(request: DownloadRequest):
    """Начинает загрузку видео"""
    import uuid
    download_id = str(uuid.uuid4())
    
    # Запускаем загрузку в фоне
    asyncio.create_task(download_video(request.url, download_id))
    
    return {"download_id": download_id, "status": "started"}

@app.get("/api/progress/{download_id}")
async def get_progress(download_id: str):
    """Получает прогресс загрузки"""
    if download_id not in download_progress:
        raise HTTPException(status_code=404, detail="Download ID not found")
    
    return download_progress[download_id]

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

